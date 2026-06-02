#!/usr/bin/env python3
# encoding: utf-8

"""Run the first end-to-end Iteration 2 control-plane demo without Docker."""

from __future__ import annotations

import argparse
import json
from typing import Any, Dict, List, Sequence

from contracts import validate_runtime_update_plan, validate_topology_snapshot
from planner import DefaultPlannerSkeleton, Planner
from topology_pipeline import build_demo_topology_snapshot_pipeline


def _build_planner(planner_name: str) -> Planner:
    if planner_name == "default":
        return DefaultPlannerSkeleton()
    raise ValueError(f"unsupported planner: {planner_name}")


def _node_action_ids(node_subplans: Dict[str, Dict[str, Any]]) -> Dict[str, List[str]]:
    return {
        node_id: [str(action.get("action_id")) for action in subplan.get("actions", [])]
        for node_id, subplan in sorted(node_subplans.items())
    }


def _slice_summary(
    planner: Planner,
    snapshot: Any,
    runtime_update_plan: Any,
) -> Dict[str, Any]:
    topology_validation = validate_topology_snapshot(snapshot)
    plan_validation = validate_runtime_update_plan(runtime_update_plan)
    flow_validation = planner.validate_flow(snapshot, runtime_update_plan)
    return {
        "time_slice_id": snapshot.time_slice.get("slice_id"),
        "snapshot_id": snapshot.snapshot_id,
        "relative_time_seconds": snapshot.time_slice.get("relative_time_seconds"),
        "duration_seconds": snapshot.time_slice.get("duration_seconds"),
        "active_link_ids": [
            str(link.get("link_id")) for link in snapshot.active_links
        ],
        "zone_serving": {
            zone_id: zone.get("active_serving_satellite_id")
            for zone_id, zone in sorted(snapshot.zones.items())
        },
        "planner_used": runtime_update_plan.planner,
        "node_actions": runtime_update_plan.node_subplans,
        "action_ids_by_node": _node_action_ids(runtime_update_plan.node_subplans),
        "validation": {
            "topology_snapshot": topology_validation.to_dict(),
            "runtime_update_plan": plan_validation.to_dict(),
            "planner_flow": flow_validation.to_dict(),
            "all_valid": (
                topology_validation.is_valid
                and plan_validation.is_valid
                and flow_validation.is_valid
            ),
        },
    }


def _transition_summary(previous: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Any]:
    previous_links = set(previous["active_link_ids"])
    current_links = set(current["active_link_ids"])
    previous_zone_serving = previous["zone_serving"]
    current_zone_serving = current["zone_serving"]
    node_action_changes: Dict[str, Dict[str, List[str]]] = {}
    for node_id in sorted(
        set(previous["action_ids_by_node"]) | set(current["action_ids_by_node"])
    ):
        previous_actions = set(previous["action_ids_by_node"].get(node_id, []))
        current_actions = set(current["action_ids_by_node"].get(node_id, []))
        added = sorted(current_actions - previous_actions)
        removed = sorted(previous_actions - current_actions)
        if added or removed:
            node_action_changes[node_id] = {
                "added": added,
                "removed": removed,
            }
    zone_serving_changes = []
    for zone_id in sorted(set(previous_zone_serving) | set(current_zone_serving)):
        old_value = previous_zone_serving.get(zone_id)
        new_value = current_zone_serving.get(zone_id)
        if old_value != new_value:
            zone_serving_changes.append(
                {
                    "zone_id": zone_id,
                    "from": old_value,
                    "to": new_value,
                }
            )
    return {
        "from_slice_id": previous["time_slice_id"],
        "to_slice_id": current["time_slice_id"],
        "link_changes": {
            "added": sorted(current_links - previous_links),
            "removed": sorted(previous_links - current_links),
        },
        "zone_serving_changes": zone_serving_changes,
        "node_action_changes": node_action_changes,
    }


def build_control_plane_demo_report(planner_name: str = "default") -> Dict[str, Any]:
    pipeline = build_demo_topology_snapshot_pipeline()
    planner = _build_planner(planner_name)
    slices = []
    for snapshot in pipeline.produce_snapshots():
        runtime_update_plan = planner.plan(snapshot)
        slices.append(_slice_summary(planner, snapshot, runtime_update_plan))
    transitions = [
        _transition_summary(previous, current)
        for previous, current in zip(slices, slices[1:])
    ]
    return {
        "demo": {
            "demo_name": "iteration2_control_plane_demo",
            "planner_option": planner_name,
            "slice_count": len(slices),
        },
        "pipeline": pipeline.metadata(),
        "planner": planner.metadata(),
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
        f"{report['planner']['planner_name']}"
    )
    lines.append(f"All slices valid: {report['all_valid']}")
    for slice_summary in report["time_slices"]:
        lines.append("")
        lines.append(
            f"Slice {slice_summary['time_slice_id']} "
            f"(t={slice_summary['relative_time_seconds']}s, "
            f"duration={slice_summary['duration_seconds']}s)"
        )
        lines.append(
            "  Active links: " + ", ".join(slice_summary["active_link_ids"])
        )
        zone_pairs = ", ".join(
            f"{zone_id}->{satellite_id}"
            for zone_id, satellite_id in sorted(slice_summary["zone_serving"].items())
        )
        lines.append("  Zone serving: " + zone_pairs)
        lines.append(
            "  Planner: "
            f"{slice_summary['planner_used']['planner_name']} "
            f"({slice_summary['planner_used']['planner_kind']})"
        )
        lines.append(
            "  Validation: "
            f"snapshot={slice_summary['validation']['topology_snapshot']['status']}, "
            f"plan={slice_summary['validation']['runtime_update_plan']['status']}, "
            f"flow={slice_summary['validation']['planner_flow']['status']}"
        )
        for node_id, subplan in sorted(slice_summary["node_actions"].items()):
            action_summaries = ", ".join(
                f"{action.get('action_id')}[{action.get('action_type')}]"
                for action in subplan.get("actions", [])
            )
            lines.append(f"  Node {node_id}: {action_summaries}")
    for transition in report["transitions"]:
        lines.append("")
        lines.append(
            f"Transition {transition['from_slice_id']} -> {transition['to_slice_id']}"
        )
        lines.append(
            "  Links added: "
            + (", ".join(transition["link_changes"]["added"]) or "none")
        )
        lines.append(
            "  Links removed: "
            + (", ".join(transition["link_changes"]["removed"]) or "none")
        )
        if transition["zone_serving_changes"]:
            zone_changes = ", ".join(
                f"{change['zone_id']}:{change['from']}->{change['to']}"
                for change in transition["zone_serving_changes"]
            )
            lines.append("  Zone serving changes: " + zone_changes)
        else:
            lines.append("  Zone serving changes: none")
        if transition["node_action_changes"]:
            for node_id, node_change in sorted(transition["node_action_changes"].items()):
                lines.append(
                    f"  Node {node_id} action delta: "
                    f"added={node_change['added'] or ['none']}, "
                    f"removed={node_change['removed'] or ['none']}"
                )
        else:
            lines.append("  Node action changes: none")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--planner",
        default="default",
        help="planner choice for the demo. Currently supported: default",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="output format",
    )
    args = parser.parse_args()

    report = build_control_plane_demo_report(planner_name=args.planner)
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
        return
    print(_render_text(report))


if __name__ == "__main__":
    main()
