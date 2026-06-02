#!/usr/bin/env python3
# encoding: utf-8

"""Planner validation helpers for Iteration 2 contract flow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from contracts import (
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
