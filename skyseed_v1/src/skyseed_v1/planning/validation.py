#!/usr/bin/env python3
# encoding: utf-8

"""Planner validation helpers for Iteration 2 contract flow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from skyseed_v1.core.ir.contracts import (
    RuntimeUpdatePlan,
    TopologySnapshot,
    validate_runtime_update_plan,
    validate_topology_snapshot,
)


@dataclass(frozen=True)
class PlannerValidationResult:
    validation_scope: str
    planner_name: str
    planner_kind: str
    snapshot_id: str
    plan_id: Optional[str]
    issues: Tuple[str, ...]

    @property
    def is_valid(self) -> bool:
        return len(self.issues) == 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "validation_scope": self.validation_scope,
            "planner_name": self.planner_name,
            "planner_kind": self.planner_kind,
            "snapshot_id": self.snapshot_id,
            "plan_id": self.plan_id,
            "status": "valid" if self.is_valid else "issues_detected",
            "issues": list(self.issues),
            "issue_count": len(self.issues),
        }


def validate_planner_input(
    topology_snapshot: TopologySnapshot,
    *,
    planner_name: str,
    planner_kind: str,
) -> PlannerValidationResult:
    issues = list(validate_topology_snapshot(topology_snapshot).issues)
    if len(topology_snapshot.nodes) == 0:
        issues.append("planner_input_missing_nodes")
    if len(topology_snapshot.zones) == 0:
        issues.append("planner_input_missing_zones")
    if len(topology_snapshot.ut_zone_membership) == 0:
        issues.append("planner_input_missing_ut_zone_membership")
    for zone_id, zone in sorted(topology_snapshot.zones.items()):
        resolution_state = str(zone.get("resolution_state", "resolved"))
        if resolution_state == "resolved" and not zone.get("active_serving_satellite_id"):
            issues.append(f"planner_input_zone_missing_active_serving_satellite:{zone_id}")
    for ut_id, zone_id in sorted(topology_snapshot.ut_zone_membership.items()):
        zone = dict(topology_snapshot.zones.get(zone_id, {}))
        attachment = dict(topology_snapshot.nodes.get(ut_id, {}).get("attachment", {}))
        if zone and not bool(
            zone.get("zone_service_resolved", zone.get("resolution_state") == "resolved")
        ):
            continue
        attachment_satellite_id = attachment.get("active_serving_satellite_id")
        if attachment.get("state") != "attached":
            issues.append(f"planner_input_ut_not_attached:{ut_id}:{zone_id}")
        if not attachment_satellite_id:
            issues.append(f"planner_input_ut_missing_serving_satellite:{ut_id}:{zone_id}")
    return PlannerValidationResult(
        validation_scope="planner_input",
        planner_name=planner_name,
        planner_kind=planner_kind,
        snapshot_id=topology_snapshot.snapshot_id,
        plan_id=None,
        issues=tuple(issues),
    )


def validate_planner_output(
    topology_snapshot: TopologySnapshot,
    runtime_update_plan: RuntimeUpdatePlan,
    *,
    planner_name: str,
    planner_kind: str,
) -> PlannerValidationResult:
    issues = list(validate_runtime_update_plan(runtime_update_plan).issues)
    if runtime_update_plan.target_snapshot.get("snapshot_id") != topology_snapshot.snapshot_id:
        issues.append("planner_output_target_snapshot_mismatch")
    if runtime_update_plan.target_snapshot.get("slice_id") != topology_snapshot.time_slice.get(
        "slice_id"
    ):
        issues.append("planner_output_target_slice_mismatch")
    if runtime_update_plan.planner.get("planner_name") != planner_name:
        issues.append("planner_output_planner_name_mismatch")
    if runtime_update_plan.planner.get("planner_kind") != planner_kind:
        issues.append("planner_output_planner_kind_mismatch")
    if len(runtime_update_plan.node_subplans) == 0:
        issues.append("planner_output_missing_node_subplans")
    for node_id, subplan in sorted(runtime_update_plan.node_subplans.items()):
        if node_id not in topology_snapshot.nodes:
            issues.append(f"planner_output_unknown_target_node:{node_id}")
        if subplan.get("target_node_type") != topology_snapshot.nodes.get(node_id, {}).get(
            "node_type"
        ):
            issues.append(f"planner_output_target_node_type_mismatch:{node_id}")
        for index, action in enumerate(subplan.get("actions", []), start=1):
            if action.get("target_node_id") not in topology_snapshot.nodes:
                issues.append(
                    f"planner_output_action_unknown_target:{node_id}:{index}:{action.get('target_node_id')}"
                )
            if (
                topology_snapshot.nodes.get(node_id, {}).get("node_type") == "user_terminal"
                and action.get("action_type") == "forwarding_entry"
            ):
                outcome = dict(action.get("intent", {}).get("outcome", {}))
                gateway_selection = dict(outcome.get("gateway_selection", {}))
                gateway_bindings = [
                    dict(binding)
                    for binding in gateway_selection.get("gateway_class_bindings", [])
                ]
                if not gateway_bindings and gateway_selection.get("gateway_class_binding"):
                    gateway_bindings = [
                        dict(gateway_selection.get("gateway_class_binding", {}))
                    ]
                forbidden_ut_fields = (
                    "satellite_path",
                    "forward_link_ids",
                    "reverse_link_ids",
                    "forward_labels",
                    "reverse_labels",
                    "ping_target_ip",
                    "target_host_node_id",
                    "policy_table_id",
                    "policy_rule_priority",
                    "gateway_access_link_id",
                    "ut_access_link_id",
                )
                for field_name in forbidden_ut_fields:
                    if field_name in gateway_selection:
                        issues.append(
                            f"planner_output_ut_gateway_selection_too_heavy:{node_id}:{field_name}"
                        )
                for binding in gateway_bindings:
                    for field_name in forbidden_ut_fields:
                        if field_name in binding:
                            issues.append(
                                "planner_output_ut_gateway_binding_too_heavy:"
                                f"{node_id}:{field_name}"
                            )
                for override in gateway_selection.get("special_prefix_overrides", []):
                    for field_name in forbidden_ut_fields:
                        if field_name in dict(override):
                            issues.append(
                                "planner_output_ut_prefix_override_too_heavy:"
                                f"{node_id}:{field_name}"
                            )
    return PlannerValidationResult(
        validation_scope="planner_output",
        planner_name=planner_name,
        planner_kind=planner_kind,
        snapshot_id=topology_snapshot.snapshot_id,
        plan_id=runtime_update_plan.plan_id,
        issues=tuple(issues),
    )


def validate_planner_flow(
    topology_snapshot: TopologySnapshot,
    runtime_update_plan: RuntimeUpdatePlan,
    *,
    planner_name: str,
    planner_kind: str,
) -> PlannerValidationResult:
    input_result = validate_planner_input(
        topology_snapshot,
        planner_name=planner_name,
        planner_kind=planner_kind,
    )
    output_result = validate_planner_output(
        topology_snapshot,
        runtime_update_plan,
        planner_name=planner_name,
        planner_kind=planner_kind,
    )
    return PlannerValidationResult(
        validation_scope="planner_flow",
        planner_name=planner_name,
        planner_kind=planner_kind,
        snapshot_id=topology_snapshot.snapshot_id,
        plan_id=runtime_update_plan.plan_id,
        issues=tuple([*input_result.issues, *output_result.issues]),
    )
