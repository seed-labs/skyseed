#!/usr/bin/env python3
# encoding: utf-8

"""Legacy controller payload adapters for Iteration 2 contracts.

The canonical contract schemas live in contracts.py. These helpers are isolated
because they translate older controller/link-state shapes rather than define the
current research-platform boundary.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

from contracts import (
    NETWORK_MUTATION_PLAN_SCHEMA_VERSION,
    RUNTIME_ACTION_MODEL_VERSION,
    RUNTIME_UPDATE_PLAN_SCHEMA_VERSION,
    TOPOLOGY_SNAPSHOT_SCHEMA_VERSION,
    NetworkMutationPlan,
    RuntimeUpdatePlan,
    TopologySnapshot,
    build_runtime_plan_action,
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
            action_type = "node_capability"
            intent: Dict[str, Any] = {
                "capability": "legacy_runtime_requirement",
                "operation": "project_into_runtime_update_plan",
                "capability_scope": {
                    "legacy_category": command.get("category"),
                    "legacy_logical_kind": command.get("logical_kind"),
                },
            }
            command_family = command.get("command_family")
            if command_family in {"ip", "iptables"}:
                action_type = "forwarding_entry"
                intent = {
                    "forwarding_plane": (
                        "label_switching"
                        if command.get("category") == "mpls"
                        else "ip_forwarding"
                    ),
                    "match": {
                        "intent_role": command.get("logical_kind"),
                        "shared_key": command.get("shared_key"),
                    },
                    "outcome": {
                        "expected_state": command.get("expected_state"),
                    },
                }
            actions.append(
                build_runtime_plan_action(
                    action_id=(
                        f"legacy:{node_id}:{command.get('sequence')}:{command.get('shared_key')}"
                    ),
                    action_type=action_type,
                    target_node_id=node_id,
                    sequence=command.get("sequence"),
                    intent=intent,
                    validation={
                        "expected_state": command.get("expected_state"),
                    },
                    metadata={
                        "source": "legacy_controller_projection",
                        "legacy_logical_kind": command.get("logical_kind"),
                        "legacy_category": command.get("category"),
                    },
                )
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
            "action_model": RUNTIME_ACTION_MODEL_VERSION,
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
            "snapshot_id": None
            if topology_snapshot is None
            else topology_snapshot.snapshot_id,
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
