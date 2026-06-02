#!/usr/bin/env python3
# encoding: utf-8

"""Run the first end-to-end dry-run runtime projection demo without Docker."""

from __future__ import annotations

import argparse
import json
from typing import Any, Dict, List

from contracts import validate_runtime_update_plan, validate_topology_snapshot
from planner import DefaultPlannerSkeleton, Planner
from planner_validation import PlannerValidationResult
from runtime_projection import (
    DryRunInstallerPlan,
    GenericDryRunProjector,
    ProjectionValidationResult,
    RuntimeProjector,
)
from topology_pipeline import build_demo_topology_snapshot_pipeline


def _build_planner(planner_name: str) -> Planner:
    if planner_name == "default":
        return DefaultPlannerSkeleton()
    raise ValueError(f"unsupported planner: {planner_name}")


def _build_projector(projector_name: str) -> RuntimeProjector:
    if projector_name == "generic-dry-run":
        return GenericDryRunProjector()
    raise ValueError(f"unsupported projector: {projector_name}")


def _action_ids_by_node(node_subplans: Dict[str, Dict[str, Any]]) -> Dict[str, List[str]]:
    return {
        node_id: [str(action.get("action_id")) for action in subplan.get("actions", [])]
        for node_id, subplan in sorted(node_subplans.items())
    }


def _instruction_ids_by_node(
    node_installer_subplans: Dict[str, Dict[str, Any]]
) -> Dict[str, List[str]]:
    return {
        node_id: [
            str(intent.get("instruction_id"))
            for intent in subplan.get("installer_intents", [])
        ]
        for node_id, subplan in sorted(node_installer_subplans.items())
    }


def _slice_summary(
    *,
    planner: Planner,
    projector: RuntimeProjector,
    snapshot: Any,
    runtime_update_plan: Any,
    installer_plan: DryRunInstallerPlan,
) -> Dict[str, Any]:
    topology_validation = validate_topology_snapshot(snapshot)
    plan_validation = validate_runtime_update_plan(runtime_update_plan)
    planner_flow_validation: PlannerValidationResult = planner.validate_flow(
        snapshot,
        runtime_update_plan,
    )
    projection_input_validation: ProjectionValidationResult = projector.validate_input(
        runtime_update_plan
    )
    projection_output_validation: ProjectionValidationResult = projector.validate_output(
        runtime_update_plan,
        installer_plan,
    )
    return {
        "time_slice_id": snapshot.time_slice.get("slice_id"),
        "snapshot_id": snapshot.snapshot_id,
        "runtime_update_plan": runtime_update_plan.to_dict(),
        "installer_projection": installer_plan.to_dict(),
        "action_ids_by_node": _action_ids_by_node(runtime_update_plan.node_subplans),
        "instruction_ids_by_node": _instruction_ids_by_node(
            installer_plan.node_installer_subplans
        ),
        "validation": {
            "topology_snapshot": topology_validation.to_dict(),
            "runtime_update_plan": plan_validation.to_dict(),
            "planner_flow": planner_flow_validation.to_dict(),
            "projection_input": projection_input_validation.to_dict(),
            "projection_output": projection_output_validation.to_dict(),
            "all_valid": (
                topology_validation.is_valid
                and plan_validation.is_valid
                and planner_flow_validation.is_valid
                and projection_input_validation.is_valid
                and projection_output_validation.is_valid
            ),
        },
    }


def _transition_summary(previous: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Any]:
    node_projection_changes: Dict[str, Dict[str, List[str]]] = {}
    for node_id in sorted(
        set(previous["instruction_ids_by_node"]) | set(current["instruction_ids_by_node"])
    ):
        previous_intents = set(previous["instruction_ids_by_node"].get(node_id, []))
        current_intents = set(current["instruction_ids_by_node"].get(node_id, []))
        added = sorted(current_intents - previous_intents)
        removed = sorted(previous_intents - current_intents)
        if added or removed:
            node_projection_changes[node_id] = {
                "added": added,
                "removed": removed,
            }
    return {
        "from_slice_id": previous["time_slice_id"],
        "to_slice_id": current["time_slice_id"],
        "node_projection_changes": node_projection_changes,
    }


def build_runtime_projection_demo_report(
    *,
    planner_name: str = "default",
    projector_name: str = "generic-dry-run",
) -> Dict[str, Any]:
    pipeline = build_demo_topology_snapshot_pipeline()
    planner = _build_planner(planner_name)
    projector = _build_projector(projector_name)
    slices = []
    for snapshot in pipeline.produce_snapshots():
        runtime_update_plan = planner.plan(snapshot)
        installer_plan = projector.project(runtime_update_plan)
        slices.append(
            _slice_summary(
                planner=planner,
                projector=projector,
                snapshot=snapshot,
                runtime_update_plan=runtime_update_plan,
                installer_plan=installer_plan,
            )
        )
    transitions = [
        _transition_summary(previous, current)
        for previous, current in zip(slices, slices[1:])
    ]
    return {
        "demo": {
            "demo_name": "iteration2_runtime_projection_demo",
            "planner_option": planner_name,
            "projector_option": projector_name,
            "slice_count": len(slices),
        },
        "pipeline": pipeline.metadata(),
        "planner": planner.metadata(),
        "projector": projector.metadata(),
        "time_slices": slices,
        "transitions": transitions,
        "all_valid": all(slice_summary["validation"]["all_valid"] for slice_summary in slices),
    }


def _render_text(report: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append(
        "Pipeline: "
        f"{report['pipeline']['orbit_producer']} -> "
        f"{report['pipeline']['visibility_model']} -> "
        f"{report['pipeline']['zone_generator']} -> "
        f"{report['pipeline']['topology_slicer']} -> "
        f"{report['planner']['planner_name']} -> "
        f"{report['projector']['projector_name']}"
    )
    lines.append(f"All slices valid: {report['all_valid']}")
    for slice_summary in report["time_slices"]:
        runtime_update_plan = slice_summary["runtime_update_plan"]
        installer_projection = slice_summary["installer_projection"]
        lines.append("")
        lines.append(f"Slice {slice_summary['time_slice_id']}")
        lines.append(
            "  RuntimeUpdatePlan: "
            f"{runtime_update_plan['plan_id']} "
            f"({runtime_update_plan['planner']['planner_name']})"
        )
        for node_id, subplan in sorted(runtime_update_plan["node_subplans"].items()):
            action_summaries = ", ".join(
                f"{action.get('action_id')}[{action.get('action_type')}]"
                for action in subplan.get("actions", [])
            )
            lines.append(f"  Planner actions {node_id}: {action_summaries}")
        lines.append(
            "  InstallerProjection: "
            f"{installer_projection['projection_id']} "
            f"({installer_projection['projector']['projector_name']})"
        )
        for node_id, subplan in sorted(installer_projection["node_installer_subplans"].items()):
            intent_summaries = ", ".join(
                f"{intent.get('instruction_id')}[{intent.get('instruction_type')}]"
                for intent in subplan.get("installer_intents", [])
            )
            lines.append(f"  Projected intents {node_id}: {intent_summaries}")
        validation = slice_summary["validation"]
        lines.append(
            "  Validation: "
            f"snapshot={validation['topology_snapshot']['status']}, "
            f"plan={validation['runtime_update_plan']['status']}, "
            f"flow={validation['planner_flow']['status']}, "
            f"projection_in={validation['projection_input']['status']}, "
            f"projection_out={validation['projection_output']['status']}"
        )
    for transition in report["transitions"]:
        lines.append("")
        lines.append(
            f"Projection transition {transition['from_slice_id']} -> {transition['to_slice_id']}"
        )
        if transition["node_projection_changes"]:
            for node_id, node_change in sorted(transition["node_projection_changes"].items()):
                lines.append(
                    f"  Node {node_id}: "
                    f"added={node_change['added'] or ['none']}, "
                    f"removed={node_change['removed'] or ['none']}"
                )
        else:
            lines.append("  Node projection changes: none")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--planner",
        default="default",
        help="planner choice for the demo. Currently supported: default",
    )
    parser.add_argument(
        "--projector",
        default="generic-dry-run",
        help="projector choice for the demo. Currently supported: generic-dry-run",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="output format",
    )
    args = parser.parse_args()

    report = build_runtime_projection_demo_report(
        planner_name=args.planner,
        projector_name=args.projector,
    )
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
        return
    print(_render_text(report))


if __name__ == "__main__":
    main()
