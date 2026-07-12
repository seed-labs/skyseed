#!/usr/bin/env python3
# encoding: utf-8

"""Stable Satellite Internet 1.0 intermediate representations.

The types in this module preserve the validated v1 schema names,
serialization, fingerprints, and fail-closed validation behavior while their
implementation moves into the Skyseed package.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple


TOPOLOGY_SNAPSHOT_SCHEMA_VERSION = "satellite-iteration2-topology-snapshot-v1alpha1"
RUNTIME_UPDATE_PLAN_SCHEMA_VERSION = "satellite-iteration2-runtime-update-plan-v1alpha1"
NETWORK_MUTATION_PLAN_SCHEMA_VERSION = "satellite-iteration2-network-mutation-plan-v1alpha1"
RUNTIME_ACTION_MODEL_VERSION = "satellite-runtime-update-action-v1alpha1"
RUNTIME_ACTION_TYPES = (
    "service_binding",
    "traffic_classification",
    "forwarding_entry",
    "node_capability",
)
RUNTIME_ACTION_FORBIDDEN_FIELDS = (
    "command_family",
    "argv",
    "shell_command",
    "docker_exec_command",
    "docker_network_operation",
)
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


def build_runtime_plan_action(
    *,
    action_id: str,
    action_type: str,
    target_node_id: str,
    intent: Dict[str, Any],
    sequence: Optional[int] = None,
    lifecycle: Optional[Dict[str, Any]] = None,
    validation: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    action: Dict[str, Any] = {
        "action_id": action_id,
        "action_type": action_type,
        "target_node_id": target_node_id,
        "intent": dict(intent),
        "lifecycle": dict(lifecycle or {}),
        "validation": dict(validation or {}),
    }
    if sequence is not None:
        action["sequence"] = sequence
    if metadata:
        action["metadata"] = dict(metadata)
    return action


def _validate_runtime_action_intent_shape(
    *,
    action_type: str,
    action_id: str,
    intent: Any,
) -> List[str]:
    issues: List[str] = []
    if not isinstance(intent, dict) or len(intent) == 0:
        return [f"action_intent_missing_or_invalid:{action_id}"]

    def require_field(field_name: str) -> None:
        if field_name not in intent:
            issues.append(f"action_intent_missing_field:{action_id}:{field_name}")

    if action_type == "service_binding":
        require_field("binding_kind")
        require_field("subject")
        require_field("target")
    elif action_type == "traffic_classification":
        require_field("match")
        require_field("outcome")
    elif action_type == "forwarding_entry":
        require_field("forwarding_plane")
        require_field("match")
        require_field("outcome")
    elif action_type == "node_capability":
        require_field("capability")
        require_field("operation")

    for endpoint_field in ("subject", "target"):
        endpoint = intent.get(endpoint_field)
        if endpoint is None:
            continue
        if not isinstance(endpoint, dict):
            issues.append(f"action_intent_endpoint_not_object:{action_id}:{endpoint_field}")
            continue
        if not endpoint.get("entity_type"):
            issues.append(f"action_intent_endpoint_missing_entity_type:{action_id}:{endpoint_field}")
        if not endpoint.get("entity_id"):
            issues.append(f"action_intent_endpoint_missing_entity_id:{action_id}:{endpoint_field}")

    return issues


def validate_runtime_plan_action(action: Dict[str, Any]) -> List[str]:
    issues: List[str] = []
    action_id = str(action.get("action_id") or "")
    action_type = str(action.get("action_type") or "")
    target_node_id = str(action.get("target_node_id") or "")

    if not action_id:
        issues.append("action_id_missing")
    if action_type not in RUNTIME_ACTION_TYPES:
        issues.append(f"action_type_unsupported:{action_id or '<missing>'}:{action_type}")
    if not target_node_id:
        issues.append(f"action_target_node_missing:{action_id or '<missing>'}")

    forbidden_paths = _collect_forbidden_key_paths(
        action,
        forbidden_keys=RUNTIME_ACTION_FORBIDDEN_FIELDS,
        path=f"runtime_action[{action_id or '<missing>'}]",
    )
    issues.extend(
        f"runtime_action_contains_forbidden_field:{path}"
        for path in forbidden_paths
    )
    if action_type in RUNTIME_ACTION_TYPES:
        issues.extend(
            _validate_runtime_action_intent_shape(
                action_type=action_type,
                action_id=action_id or "<missing>",
                intent=action.get("intent"),
            )
        )
    return issues


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
            resolution_state = str(zone.get("resolution_state", "resolved"))
            active_serving_satellite_id = zone.get("active_serving_satellite_id")
            if active_serving_satellite_id is None and resolution_state == "resolved":
                issues.append(f"zone_missing_active_serving_satellite:{zone_id}")
            elif active_serving_satellite_id is not None and str(active_serving_satellite_id) not in self.nodes:
                issues.append(
                    f"zone_active_serving_satellite_unknown:{zone_id}:{active_serving_satellite_id}"
                )
            elif (
                active_serving_satellite_id is not None
                and self.nodes[str(active_serving_satellite_id)].get("node_type") != "satellite"
            ):
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
        if self.apply_model.get("action_model") != RUNTIME_ACTION_MODEL_VERSION:
            issues.append(
                f"apply_model_unexpected_action_model:{self.apply_model.get('action_model')}"
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
                if action.get("target_node_id") != node_id:
                    issues.append(f"action_target_mismatch:{node_id}:{index}")
                issues.extend(
                    f"{node_id}:{index}:{issue}"
                    for issue in validate_runtime_plan_action(action)
                )
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
            "action_model": RUNTIME_ACTION_MODEL_VERSION,
            "execution_projection": "node_local_subplans",
            "agent_policy_visibility": "none",
        },
        node_subplans={
            "sat-demo-1": {
                "target_node_id": "sat-demo-1",
                "target_node_type": "satellite",
                "apply_mode": "ordered_local_actions",
                "actions": [
                    build_runtime_plan_action(
                        action_id="forwarding:sat-demo-1:zone-demo-east",
                        action_type="forwarding_entry",
                        target_node_id="sat-demo-1",
                        sequence=10,
                        intent={
                            "forwarding_plane": "label_switching",
                            "match": {
                                "destination_scope": {
                                    "entity_type": "zone",
                                    "entity_id": "zone-demo-east",
                                }
                            },
                            "outcome": {
                                "delivery_target": {
                                    "entity_type": "user_terminal",
                                    "entity_id": "ut-demo-1",
                                },
                                "egress_path": "local_access_delivery",
                            },
                        },
                        validation={
                            "expected_state": "zone-demo-east traffic resolves toward ut-demo-1 access"
                        },
                        metadata={
                            "planner_semantics": "mock_runtime_update_plan",
                            "example_policy_family": "mpls_compatible_placeholder",
                        },
                    ),
                    build_runtime_plan_action(
                        action_id="capability:sat-demo-1:label-switching",
                        action_type="node_capability",
                        target_node_id="sat-demo-1",
                        sequence=20,
                        intent={
                            "capability": "forwarding_plane_readiness",
                            "operation": "ensure_present",
                            "capability_scope": {
                                "forwarding_plane": "label_switching",
                                "interface_scope": "node_local",
                            },
                        },
                        validation={
                            "expected_state": "sat-demo-1 can host label-switching forwarding state"
                        },
                        metadata={
                            "planner_semantics": "mock_runtime_update_plan",
                        },
                    ),
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
