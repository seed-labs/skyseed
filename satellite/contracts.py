#!/usr/bin/env python3
# encoding: utf-8

"""Iteration 2 contract skeletons and legacy adapters for satellite/."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple


TOPOLOGY_SNAPSHOT_SCHEMA_VERSION = "satellite-iteration2-topology-snapshot-v1alpha1"
RUNTIME_UPDATE_PLAN_SCHEMA_VERSION = "satellite-iteration2-runtime-update-plan-v1alpha1"
NETWORK_MUTATION_PLAN_SCHEMA_VERSION = "satellite-iteration2-network-mutation-plan-v1alpha1"
TOPOLOGY_FORBIDDEN_FIELDS = (
    "mpls_labels",
    "lfib_entries",
    "ip_route_actions",
    "ip_rule_actions",
    "iptables_actions",
    "sysctl_actions",
    "docker_exec_commands",
    "docker_network_operations",
)
RUNTIME_UPDATE_PLAN_FORBIDDEN_FIELDS = (
    "orbit_details",
    "visibility_engine_state",
    "zone_generation_trace",
    "docker_network_operations",
    "bridge_operations",
    "veth_operations",
)
NETWORK_MUTATION_PLAN_FORBIDDEN_FIELDS = (
    "mpls_labels",
    "lfib_entries",
    "route_policy",
    "gateway_selection_reason",
    "zone_selection_reason",
    "orbit_details",
)


def _fingerprint(payload: Dict[str, Any]) -> str:
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()[:16]


def _active_slot_links(link_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    active_links: List[Dict[str, Any]] = []
    for slot in link_state.get("slots", []):
        slot_type = str(slot.get("slot_type"))
        occupied = bool(slot.get("occupied", False))
        endpoints = list(slot.get("preconnected_endpoints", [])) + list(
            slot.get("dynamic_endpoints", [])
        )
        if slot_type != "fixed_isl" and not occupied:
            continue
        if len(endpoints) < 2:
            continue
        active_links.append(
            {
                "link_id": str(slot.get("slot_name")),
                "slot_type": slot_type,
                "subnet": slot.get("subnet"),
                "network_name": slot.get("network_name"),
                "docker_network_name": slot.get("docker_network_name"),
                "occupied": occupied or slot_type == "fixed_isl",
                "binding": dict(slot.get("binding") or {}),
                "endpoints": [dict(endpoint) for endpoint in endpoints],
            }
        )
    return sorted(active_links, key=lambda entry: entry["link_id"])


def _ut_attachment(link_state: Dict[str, Any], ut_id: str) -> Dict[str, Any]:
    for slot in link_state.get("slots", []):
        if slot.get("slot_type") != "ut_access":
            continue
        if slot.get("owner_node_id") != ut_id:
            continue
        binding = dict(slot.get("binding") or {})
        if slot.get("occupied") and binding.get("satellite_id") is not None:
            return {
                "state": "attached",
                "slot_name": slot.get("slot_name"),
                "active_serving_satellite_id": str(binding.get("satellite_id")),
            }
    return {
        "state": "detached",
        "slot_name": None,
        "active_serving_satellite_id": None,
    }


def _slot_state_by_name(link_state: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        str(slot.get("slot_name")): dict(slot)
        for slot in link_state.get("slots", [])
    }


def _validation_payload(issues: Sequence[str]) -> Dict[str, Any]:
    return {
        "status": "valid" if not issues else "issues_detected",
        "issues": list(issues),
        "issue_count": len(issues),
    }


def _collect_forbidden_key_paths(
    value: Any,
    *,
    forbidden_keys: Sequence[str],
    path: str,
) -> List[str]:
    findings: List[str] = []
    if isinstance(value, dict):
        for key, nested_value in value.items():
            key_text = str(key)
            nested_path = f"{path}.{key_text}" if path else key_text
            if key_text in forbidden_keys:
                findings.append(nested_path)
            findings.extend(
                _collect_forbidden_key_paths(
                    nested_value,
                    forbidden_keys=forbidden_keys,
                    path=nested_path,
                )
            )
    elif isinstance(value, list):
        for index, nested_value in enumerate(value):
            findings.extend(
                _collect_forbidden_key_paths(
                    nested_value,
                    forbidden_keys=forbidden_keys,
                    path=f"{path}[{index}]",
                )
            )
    return findings


@dataclass(frozen=True)
class ContractValidationResult:
    contract_type: str
    contract_id: str
    schema_version: str
    issues: Tuple[str, ...]

    @property
    def is_valid(self) -> bool:
        return len(self.issues) == 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "contract_type": self.contract_type,
            "contract_id": self.contract_id,
            "schema_version": self.schema_version,
            **_validation_payload(self.issues),
        }


@dataclass(frozen=True)
class TopologySnapshot:
    schema_version: str
    snapshot_id: str
    time_slice: Dict[str, Any]
    provenance: Dict[str, Any]
    nodes: Dict[str, Dict[str, Any]]
    active_links: List[Dict[str, Any]]
    zones: Dict[str, Dict[str, Any]]
    ut_zone_membership: Dict[str, str]
    planner_hints: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> List[str]:
        issues: List[str] = []
        if self.schema_version != TOPOLOGY_SNAPSHOT_SCHEMA_VERSION:
            issues.append(f"unexpected_schema_version:{self.schema_version}")
        if not self.snapshot_id:
            issues.append("snapshot_id_missing")
        if not self.time_slice.get("slice_id"):
            issues.append("time_slice_missing_slice_id")
        if self.time_slice.get("state_model") != "full_state":
            issues.append(f"time_slice_unexpected_state_model:{self.time_slice.get('state_model')}")
        if not self.provenance.get("source"):
            issues.append("provenance_missing_source")

        forbidden_paths = _collect_forbidden_key_paths(
            asdict(self),
            forbidden_keys=TOPOLOGY_FORBIDDEN_FIELDS,
            path="topology_snapshot",
        )
        issues.extend(
            f"topology_snapshot_contains_forbidden_field:{path}"
            for path in forbidden_paths
        )

        seen_zone_ids: set[str] = set()
        seen_link_ids: set[str] = set()
        zone_members: Dict[str, set[str]] = {
            zone_id: set(str(ut_id) for ut_id in zone.get("ut_ids", []))
            for zone_id, zone in self.zones.items()
        }
        for node_id, node in sorted(self.nodes.items()):
            if node.get("node_id") != node_id:
                issues.append(f"node_id_mismatch:{node_id}")
            if not node.get("node_type"):
                issues.append(f"node_type_missing:{node_id}")
        for zone_id, zone in sorted(self.zones.items()):
            if zone_id in seen_zone_ids:
                issues.append(f"duplicate_zone_id:{zone_id}")
            seen_zone_ids.add(zone_id)
            ut_ids = list(zone.get("ut_ids", []))
            if len(ut_ids) == 0:
                issues.append(f"zone_without_ut_members:{zone_id}")
            active_serving_satellite_id = zone.get("active_serving_satellite_id")
            if active_serving_satellite_id is None:
                issues.append(f"zone_missing_active_serving_satellite:{zone_id}")
            elif str(active_serving_satellite_id) not in self.nodes:
                issues.append(
                    f"zone_active_serving_satellite_unknown:{zone_id}:{active_serving_satellite_id}"
                )
            elif self.nodes[str(active_serving_satellite_id)].get("node_type") != "satellite":
                issues.append(
                    f"zone_active_serving_satellite_not_satellite:{zone_id}:{active_serving_satellite_id}"
                )
        for ut_id, zone_id in sorted(self.ut_zone_membership.items()):
            if ut_id not in self.nodes:
                issues.append(f"ut_zone_membership_unknown_ut:{ut_id}")
            if zone_id not in self.zones:
                issues.append(f"ut_zone_membership_unknown_zone:{ut_id}:{zone_id}")
                continue
            if ut_id not in zone_members[zone_id]:
                issues.append(f"ut_zone_membership_not_listed_in_zone:{ut_id}:{zone_id}")
        for link in self.active_links:
            link_id = str(link.get("link_id"))
            if link_id in seen_link_ids:
                issues.append(f"duplicate_link_id:{link_id}")
            seen_link_ids.add(link_id)
            endpoints = list(link.get("endpoints", []))
            if len(endpoints) < 2:
                issues.append(f"link_without_two_endpoints:{link_id}")
            for endpoint in endpoints:
                node_id = str(endpoint.get("node_id"))
                if node_id not in self.nodes:
                    issues.append(f"link_endpoint_unknown_node:{link_id}:{node_id}")
        return issues

    def validation_result(self) -> ContractValidationResult:
        return ContractValidationResult(
            contract_type="TopologySnapshot",
            contract_id=self.snapshot_id,
            schema_version=self.schema_version,
            issues=tuple(self.validate()),
        )

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["validation"] = self.validation_result().to_dict()
        return data


@dataclass(frozen=True)
class RuntimeUpdatePlan:
    schema_version: str
    plan_id: str
    target_snapshot: Dict[str, Any]
    planner: Dict[str, Any]
    apply_model: Dict[str, Any]
    node_subplans: Dict[str, Dict[str, Any]]
    replacement_semantics: Dict[str, Any]
    validation_expectations: Dict[str, Any]

    def validate(self) -> List[str]:
        issues: List[str] = []
        if self.schema_version != RUNTIME_UPDATE_PLAN_SCHEMA_VERSION:
            issues.append(f"unexpected_schema_version:{self.schema_version}")
        if not self.plan_id:
            issues.append("plan_id_missing")
        if not self.target_snapshot.get("snapshot_id"):
            issues.append("target_snapshot_missing_snapshot_id")
        if not self.planner.get("planner_name"):
            issues.append("planner_name_missing")
        if self.apply_model.get("state_model") != "full_state_intent":
            issues.append(
                f"apply_model_unexpected_state_model:{self.apply_model.get('state_model')}"
            )
        forbidden_paths = _collect_forbidden_key_paths(
            asdict(self),
            forbidden_keys=RUNTIME_UPDATE_PLAN_FORBIDDEN_FIELDS,
            path="runtime_update_plan",
        )
        issues.extend(
            f"runtime_update_plan_contains_forbidden_field:{path}"
            for path in forbidden_paths
        )
        for node_id, subplan in sorted(self.node_subplans.items()):
            if subplan.get("target_node_id") != node_id:
                issues.append(f"node_subplan_target_mismatch:{node_id}")
            actions = list(subplan.get("actions", []))
            if len(actions) == 0:
                issues.append(f"node_subplan_without_actions:{node_id}")
            for index, action in enumerate(actions, start=1):
                if not action.get("command_family"):
                    issues.append(f"action_missing_command_family:{node_id}:{index}")
                if action.get("target_node_id") != node_id:
                    issues.append(f"action_target_mismatch:{node_id}:{index}")
        return issues

    def validation_result(self) -> ContractValidationResult:
        return ContractValidationResult(
            contract_type="RuntimeUpdatePlan",
            contract_id=self.plan_id,
            schema_version=self.schema_version,
            issues=tuple(self.validate()),
        )

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["validation"] = self.validation_result().to_dict()
        return data


@dataclass(frozen=True)
class NetworkMutationPlan:
    schema_version: str
    mutation_id: str
    source_snapshot: Dict[str, Any]
    target_scope: Dict[str, Any]
    execution_boundary: Dict[str, Any]
    operations: List[Dict[str, Any]]
    validation_expectations: Dict[str, Any]

    def validate(self) -> List[str]:
        issues: List[str] = []
        if self.schema_version != NETWORK_MUTATION_PLAN_SCHEMA_VERSION:
            issues.append(f"unexpected_schema_version:{self.schema_version}")
        if not self.mutation_id:
            issues.append("mutation_id_missing")
        if self.target_scope.get("state_model") != "delta_mutation":
            issues.append(
                f"target_scope_unexpected_state_model:{self.target_scope.get('state_model')}"
            )
        if not self.execution_boundary.get("consumer"):
            issues.append("execution_boundary_missing_consumer")
        forbidden_paths = _collect_forbidden_key_paths(
            asdict(self),
            forbidden_keys=NETWORK_MUTATION_PLAN_FORBIDDEN_FIELDS,
            path="network_mutation_plan",
        )
        issues.extend(
            f"network_mutation_plan_contains_forbidden_field:{path}"
            for path in forbidden_paths
        )
        for index, operation in enumerate(self.operations, start=1):
            if not operation.get("operation"):
                issues.append(f"operation_missing_kind:{index}")
            if not operation.get("slot_name"):
                issues.append(f"operation_missing_slot_name:{index}")
            if not operation.get("execution_domain"):
                issues.append(f"operation_missing_execution_domain:{index}")
        return issues

    def validation_result(self) -> ContractValidationResult:
        return ContractValidationResult(
            contract_type="NetworkMutationPlan",
            contract_id=self.mutation_id,
            schema_version=self.schema_version,
            issues=tuple(self.validate()),
        )

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["validation"] = self.validation_result().to_dict()
        return data


def validate_topology_snapshot(snapshot: TopologySnapshot) -> ContractValidationResult:
    return snapshot.validation_result()


def validate_runtime_update_plan(plan: RuntimeUpdatePlan) -> ContractValidationResult:
    return plan.validation_result()


def validate_network_mutation_plan(plan: NetworkMutationPlan) -> ContractValidationResult:
    return plan.validation_result()


def build_mock_topology_snapshot() -> TopologySnapshot:
    return TopologySnapshot(
        schema_version=TOPOLOGY_SNAPSHOT_SCHEMA_VERSION,
        snapshot_id="topsnap-mock-slice-0001",
        time_slice={
            "slice_id": "mock-slice-0001",
            "state_model": "full_state",
            "time_semantics": "mock_time_slice",
        },
        provenance={
            "source": "mock_contract_flow",
            "zone_membership_mode": "explicit_mock_zone",
            "visibility_model": "not-modeled",
        },
        nodes={
            "sat-demo-1": {
                "node_id": "sat-demo-1",
                "node_type": "satellite",
                "orbit_id": "mock-orbit-a",
                "plane_index": 1,
            },
            "gw-demo-1": {
                "node_id": "gw-demo-1",
                "node_type": "gateway",
                "gateway_region": "mock-east",
            },
            "ut-demo-1": {
                "node_id": "ut-demo-1",
                "node_type": "user_terminal",
                "region": "mock-east",
                "attachment": {
                    "state": "attached",
                    "slot_name": "ut-demo-1-slot",
                    "active_serving_satellite_id": "sat-demo-1",
                },
            },
        },
        active_links=[
            {
                "link_id": "sat-demo-1-gw-demo-1",
                "slot_type": "gw_access",
                "subnet": "10.99.1.0/30",
                "occupied": True,
                "binding": {
                    "gateway_id": "gw-demo-1",
                    "satellite_id": "sat-demo-1",
                },
                "endpoints": [
                    {"node_id": "sat-demo-1", "interface_name": "ga0"},
                    {"node_id": "gw-demo-1", "interface_name": "ga0"},
                ],
            },
            {
                "link_id": "ut-demo-1-access",
                "slot_type": "ut_access",
                "subnet": "10.99.2.0/30",
                "occupied": True,
                "binding": {
                    "ut_id": "ut-demo-1",
                    "satellite_id": "sat-demo-1",
                },
                "endpoints": [
                    {"node_id": "ut-demo-1", "interface_name": "ua0"},
                    {"node_id": "sat-demo-1", "interface_name": "ua0"},
                ],
            },
        ],
        zones={
            "zone-demo-east": {
                "zone_id": "zone-demo-east",
                "membership_mode": "explicit_mock_zone",
                "ut_ids": ["ut-demo-1"],
                "active_serving_satellite_id": "sat-demo-1",
                "candidate_serving_satellite_ids": ["sat-demo-1"],
            }
        },
        ut_zone_membership={"ut-demo-1": "zone-demo-east"},
        planner_hints={
            "gateway_ids": ["gw-demo-1"],
            "satellite_ids": ["sat-demo-1"],
            "planner_examples": ["default_gz_mpls"],
        },
    )


def build_mock_runtime_update_plan(
    topology_snapshot: TopologySnapshot,
) -> RuntimeUpdatePlan:
    return RuntimeUpdatePlan(
        schema_version=RUNTIME_UPDATE_PLAN_SCHEMA_VERSION,
        plan_id="rtplan-mock-slice-0001",
        target_snapshot={
            "snapshot_id": topology_snapshot.snapshot_id,
            "slice_id": topology_snapshot.time_slice.get("slice_id"),
        },
        planner={
            "planner_name": "mock-gz-planner",
            "planner_kind": "mpls",
            "planner_version": "0.1.0",
            "source": "mock_contract_flow",
        },
        apply_model={
            "state_model": "full_state_intent",
            "execution_projection": "node_local_subplans",
            "agent_policy_visibility": "none",
        },
        node_subplans={
            "sat-demo-1": {
                "target_node_id": "sat-demo-1",
                "target_node_type": "satellite",
                "apply_mode": "ordered_local_actions",
                "actions": [
                    {
                        "sequence": 10,
                        "target_node_id": "sat-demo-1",
                        "logical_kind": "lfib_programming",
                        "category": "mpls",
                        "command_family": "ip",
                        "argv": ["ip", "-f", "mpls", "route", "replace", "100", "via", "inet", "10.99.1.2"],
                        "expected_state": "label_100_points_to_gw_demo_1",
                    },
                    {
                        "sequence": 20,
                        "target_node_id": "sat-demo-1",
                        "logical_kind": "input_enable",
                        "category": "sysctl",
                        "command_family": "sysctl",
                        "argv": ["sysctl", "-w", "net.mpls.conf.ga0.input=1"],
                        "expected_state": "mpls_input_enabled_on_ga0",
                    },
                ],
                "validation_expectations": {
                    "post_apply_validation_expected": True,
                    "validation_source": "mock_contract_flow",
                },
            }
        },
        replacement_semantics={
            "replayable": True,
            "idempotency_scope": "plan_id_and_target_node",
            "rollback_on_failure": "best_effort",
        },
        validation_expectations={
            "planner_side_validation": "required_before_publish",
            "agent_side_validation": "required_before_local_apply",
            "installer_side_validation": "required_after_local_apply",
        },
    )


def build_mock_network_mutation_plan(
    topology_snapshot: TopologySnapshot,
) -> NetworkMutationPlan:
    return NetworkMutationPlan(
        schema_version=NETWORK_MUTATION_PLAN_SCHEMA_VERSION,
        mutation_id="netmut-mock-slice-0001",
        source_snapshot={
            "snapshot_id": topology_snapshot.snapshot_id,
            "slice_id": topology_snapshot.time_slice.get("slice_id"),
            "link_state_schema_version": "mock-link-state-v1",
        },
        target_scope={
            "scenario_name": "mock_handover_prepare",
            "state_model": "delta_mutation",
            "managed_slot_count": 1,
        },
        execution_boundary={
            "producer": "mock_topology_mutation_compiler",
            "consumer": "host_daemon",
            "planner_policy_visibility": "none",
        },
        operations=[
            {
                "operation": "attach_gw",
                "slot_name": "gw-demo-1-slot-1",
                "slot_type": "gw_access",
                "request": "gw-demo-1:sat-demo-1",
                "current_binding": None,
                "target_binding": {
                    "gateway_id": "gw-demo-1",
                    "satellite_id": "sat-demo-1",
                },
                "execution_domain": "host_daemon",
                "runtime_surfaces": [
                    "link_state",
                    "namespace_interface",
                    "docker_network",
                ],
                "runtime_critical": True,
            }
        ],
        validation_expectations={
            "pre_apply": [
                "slot_inventory_present",
                "target_binding_is_supported",
            ],
            "post_apply": [
                "slot_occupancy_matches_target",
                "link_state_persisted",
            ],
        },
    )


def build_mock_contract_flow() -> Dict[str, Any]:
    topology_snapshot = build_mock_topology_snapshot()
    runtime_update_plan = build_mock_runtime_update_plan(topology_snapshot)
    network_mutation_plan = build_mock_network_mutation_plan(topology_snapshot)
    validation_results = [
        validate_topology_snapshot(topology_snapshot),
        validate_runtime_update_plan(runtime_update_plan),
        validate_network_mutation_plan(network_mutation_plan),
    ]
    return {
        "topology_snapshot": topology_snapshot.to_dict(),
        "runtime_update_plan": runtime_update_plan.to_dict(),
        "network_mutation_plan": network_mutation_plan.to_dict(),
        "validation_summary": {
            "all_valid": all(result.is_valid for result in validation_results),
            "contracts": [result.to_dict() for result in validation_results],
        },
    }


def build_topology_snapshot_from_legacy_state(
    topology: Dict[str, Any],
    link_state: Dict[str, Any],
) -> TopologySnapshot:
    nodes: Dict[str, Dict[str, Any]] = {}
    for satellite in topology.get("satellites", []):
        satellite_id = str(satellite.get("satellite_id"))
        nodes[satellite_id] = {
            "node_id": satellite_id,
            "node_type": "satellite",
            "orbit_id": satellite.get("orbit_id"),
            "plane_index": satellite.get("plane_index"),
            "bootstrap_network": dict(satellite.get("bootstrap_network", {})),
        }
    for gateway in topology.get("gateways", []):
        gateway_id = str(gateway.get("gateway_id"))
        nodes[gateway_id] = {
            "node_id": gateway_id,
            "node_type": "gateway",
            "gateway_region": gateway.get("gateway_region"),
            "internet_uplink": dict(gateway.get("internet_uplink", {})),
            "bootstrap_network": dict(gateway.get("bootstrap_network", {})),
        }
    ut_zone_membership: Dict[str, str] = {}
    zones: Dict[str, Dict[str, Any]] = {}
    for ut in topology.get("user_terminals", []):
        ut_id = str(ut.get("ut_id"))
        zone_id = f"zone-for-{ut_id}"
        attachment = _ut_attachment(link_state, ut_id)
        nodes[ut_id] = {
            "node_id": ut_id,
            "node_type": "user_terminal",
            "region": ut.get("region"),
            "identity_interface": dict(ut.get("identity_interface", {})),
            "bootstrap_network": dict(ut.get("bootstrap_network", {})),
            "candidate_serving_satellite_ids": list(
                ut.get("candidate_serving_satellite_ids", [])
            ),
            "attachment": attachment,
        }
        ut_zone_membership[ut_id] = zone_id
        zones[zone_id] = {
            "zone_id": zone_id,
            "membership_mode": "skeleton_one_zone_per_ut",
            "ut_ids": [ut_id],
            "active_serving_satellite_id": attachment["active_serving_satellite_id"],
            "candidate_serving_satellite_ids": list(
                ut.get("candidate_serving_satellite_ids", [])
            ),
            "current_attachment_state": attachment["state"],
        }

    active_links = _active_slot_links(link_state)
    snapshot_seed = {
        "topology_schema_version": topology.get("schema_version"),
        "link_state_schema_version": link_state.get("schema_version"),
        "active_links": active_links,
        "zone_serving": {
            zone_id: zone.get("active_serving_satellite_id")
            for zone_id, zone in sorted(zones.items())
        },
    }
    snapshot_id = f"topsnap-{_fingerprint(snapshot_seed)}"
    return TopologySnapshot(
        schema_version=TOPOLOGY_SNAPSHOT_SCHEMA_VERSION,
        snapshot_id=snapshot_id,
        time_slice={
            "slice_id": "legacy-runtime-current",
            "state_model": "full_state",
            "time_semantics": "controller_observed_current_state",
            "future_time_slices_supported": True,
        },
        provenance={
            "source": "legacy_controller_projection",
            "topology_schema_version": topology.get("schema_version"),
            "link_state_schema_version": link_state.get("schema_version"),
            "runtime_initialized": bool(link_state.get("runtime_initialized", False)),
            # This is a safe placeholder until a replaceable ZoneGenerator exists.
            "zone_membership_mode": "per_ut_skeleton_zone",
        },
        nodes=nodes,
        active_links=active_links,
        zones=zones,
        ut_zone_membership=ut_zone_membership,
        planner_hints={
            "gateway_ids": sorted(
                str(gateway.get("gateway_id"))
                for gateway in topology.get("gateways", [])
                if gateway.get("gateway_id") is not None
            ),
            "satellite_ids": sorted(
                str(satellite.get("satellite_id"))
                for satellite in topology.get("satellites", [])
                if satellite.get("satellite_id") is not None
            ),
        },
    )


def build_runtime_update_plan_from_legacy_payload(
    *,
    payload: Dict[str, Any],
    topology_snapshot: TopologySnapshot,
    planner_name: str,
    planner_kind: str,
    planner_version: str,
) -> RuntimeUpdatePlan:
    node_subplans: Dict[str, Dict[str, Any]] = {}
    for node_plan in payload.get("node_plans", []):
        node_id = str(node_plan.get("node_id"))
        actions = []
        for command in node_plan.get("commands", []):
            actions.append(
                {
                    "sequence": command.get("sequence"),
                    "target_node_id": node_id,
                    "logical_kind": command.get("logical_kind"),
                    "category": command.get("category"),
                    "command_family": command.get("command_family"),
                    "argv": list(command.get("argv", [])),
                    "shared_key": command.get("shared_key"),
                    "allow_failure": bool(command.get("allow_failure", False)),
                    "expected_state": command.get("expected_state"),
                }
            )
        node_subplans[node_id] = {
            "target_node_id": node_id,
            "target_node_type": node_plan.get("target_node_type"),
            "apply_mode": "ordered_local_actions",
            "actions": actions,
            "validation_expectations": {
                "post_apply_validation_expected": True,
                "validation_source": "legacy_controller_projection",
            },
        }

    return RuntimeUpdatePlan(
        schema_version=RUNTIME_UPDATE_PLAN_SCHEMA_VERSION,
        plan_id=str(payload.get("plan_id")),
        target_snapshot={
            "snapshot_id": topology_snapshot.snapshot_id,
            "slice_id": topology_snapshot.time_slice.get("slice_id"),
        },
        planner={
            "planner_name": planner_name,
            "planner_kind": planner_kind,
            "planner_version": planner_version,
            "source": "legacy_controller_projection",
        },
        apply_model={
            "state_model": "full_state_intent",
            "execution_projection": "node_local_subplans",
            "agent_policy_visibility": "none",
        },
        node_subplans=node_subplans,
        replacement_semantics={
            "replayable": bool(payload.get("replayable", False)),
            "idempotency_scope": payload.get("idempotency_scope"),
            "rollback_on_failure": payload.get("transaction_boundary", {}).get(
                "rollback_on_failure"
            ),
        },
        validation_expectations={
            "planner_side_validation": "already_completed_before_payload_construction",
            "agent_side_validation": "required_before_local_apply",
            "installer_side_validation": "required_after_local_apply",
        },
    )


def build_network_mutation_plan_from_scenario(
    *,
    link_state: Dict[str, Any],
    scenario_name: str,
    managed_slots: Dict[str, Any],
    topology_snapshot: Optional[TopologySnapshot] = None,
) -> NetworkMutationPlan:
    state_by_name = _slot_state_by_name(link_state)
    operations: List[Dict[str, Any]] = []

    for slot_name in sorted(managed_slots):
        slot_state = state_by_name[slot_name]
        target = managed_slots[slot_name]
        current_binding = dict(slot_state.get("binding") or {})
        slot_type = str(slot_state.get("slot_type"))
        occupied = bool(slot_state.get("occupied", False))
        slot_surfaces = [
            "link_state",
            "namespace_interface",
            "docker_network",
        ]
        if target is None:
            if occupied:
                operations.append(
                    {
                        "operation": "release_slot",
                        "slot_name": slot_name,
                        "slot_type": slot_type,
                        "current_binding": current_binding,
                        "target_binding": None,
                        "execution_domain": "host_daemon",
                        "runtime_surfaces": slot_surfaces,
                        "runtime_critical": True,
                    }
                )
            continue

        target_binding = dict(target.get("binding") or {})
        if occupied and current_binding != target_binding:
            operations.append(
                {
                    "operation": "release_slot",
                    "slot_name": slot_name,
                    "slot_type": slot_type,
                    "current_binding": current_binding,
                    "target_binding": None,
                    "execution_domain": "host_daemon",
                    "runtime_surfaces": slot_surfaces,
                    "runtime_critical": True,
                }
            )
        if (not occupied) or current_binding != target_binding:
            operations.append(
                {
                    "operation": str(target.get("action")),
                    "slot_name": slot_name,
                    "slot_type": slot_type,
                    "request": target.get("request"),
                    "current_binding": current_binding if occupied else None,
                    "target_binding": target_binding,
                    "execution_domain": "host_daemon",
                    "runtime_surfaces": slot_surfaces,
                    "runtime_critical": True,
                }
            )

    mutation_seed = {
        "scenario": scenario_name,
        "operations": operations,
    }
    mutation_id = f"netmut-{_fingerprint(mutation_seed)}"
    return NetworkMutationPlan(
        schema_version=NETWORK_MUTATION_PLAN_SCHEMA_VERSION,
        mutation_id=mutation_id,
        source_snapshot={
            "snapshot_id": None if topology_snapshot is None else topology_snapshot.snapshot_id,
            "slice_id": None
            if topology_snapshot is None
            else topology_snapshot.time_slice.get("slice_id"),
            "link_state_schema_version": link_state.get("schema_version"),
        },
        target_scope={
            "scenario_name": scenario_name,
            "state_model": "delta_mutation",
            "managed_slot_count": len(managed_slots),
        },
        execution_boundary={
            "producer": "host_side_mutation_compiler",
            "consumer": "host_daemon",
            "planner_policy_visibility": "none",
        },
        operations=operations,
        validation_expectations={
            "pre_apply": [
                "slot_inventory_present",
                "current_binding_matches_source_state",
            ],
            "post_apply": [
                "slot_occupancy_matches_target",
                "link_state_persisted",
            ],
        },
    )
