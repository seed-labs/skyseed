#!/usr/bin/env python3
# encoding: utf-8

"""Run the first complete Iteration 2 architecture pipeline demo without Docker."""

from __future__ import annotations

import argparse
import json
from typing import Any, Dict, List

from agent_envelope import (
    AgentEnvelopeBuilder,
    AgentInstallerEnvelopeBatch,
    EnvelopeValidationResult,
    GenericDryRunAgentEnvelopeBuilder,
)
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


def _build_envelope_builder(envelope_builder_name: str) -> AgentEnvelopeBuilder:
    if envelope_builder_name == "generic-dry-run":
        return GenericDryRunAgentEnvelopeBuilder()
    raise ValueError(f"unsupported envelope builder: {envelope_builder_name}")


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


def _envelope_ids_by_node(node_envelopes: Dict[str, Dict[str, Any]]) -> Dict[str, str]:
    return {
        node_id: str(envelope.get("envelope_id"))
        for node_id, envelope in sorted(node_envelopes.items())
    }


def _envelope_instruction_ids_by_node(
    node_envelopes: Dict[str, Dict[str, Any]]
) -> Dict[str, List[str]]:
    return {
        node_id: [
            str(intent.get("instruction_id"))
            for intent in envelope.get("installer_intents", [])
        ]
        for node_id, envelope in sorted(node_envelopes.items())
    }


def _slice_summary(
    *,
    planner: Planner,
    projector: RuntimeProjector,
    envelope_builder: AgentEnvelopeBuilder,
    snapshot: Any,
    runtime_update_plan: Any,
    installer_plan: DryRunInstallerPlan,
    envelope_batch: AgentInstallerEnvelopeBatch,
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
    envelope_input_validation: EnvelopeValidationResult = envelope_builder.validate_input(
        installer_plan
    )
    envelope_output_validation: EnvelopeValidationResult = envelope_builder.validate_output(
        installer_plan,
        envelope_batch,
    )
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
        "runtime_update_plan": runtime_update_plan.to_dict(),
        "installer_projection": installer_plan.to_dict(),
        "agent_envelope_batch": envelope_batch.to_dict(),
        "action_ids_by_node": _action_ids_by_node(runtime_update_plan.node_subplans),
        "instruction_ids_by_node": _instruction_ids_by_node(
            installer_plan.node_installer_subplans
        ),
        "envelope_ids_by_node": _envelope_ids_by_node(envelope_batch.node_envelopes),
        "envelope_instruction_ids_by_node": _envelope_instruction_ids_by_node(
            envelope_batch.node_envelopes
        ),
        "validation": {
            "topology_snapshot": topology_validation.to_dict(),
            "runtime_update_plan": plan_validation.to_dict(),
            "planner_flow": planner_flow_validation.to_dict(),
            "projection_input": projection_input_validation.to_dict(),
            "projection_output": projection_output_validation.to_dict(),
            "envelope_input": envelope_input_validation.to_dict(),
            "envelope_output": envelope_output_validation.to_dict(),
            "all_valid": (
                topology_validation.is_valid
                and plan_validation.is_valid
                and planner_flow_validation.is_valid
                and projection_input_validation.is_valid
                and projection_output_validation.is_valid
                and envelope_input_validation.is_valid
                and envelope_output_validation.is_valid
            ),
        },
    }


def _transition_summary(previous: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Any]:
    previous_links = set(previous["active_link_ids"])
    current_links = set(current["active_link_ids"])
    previous_zone_serving = previous["zone_serving"]
    current_zone_serving = current["zone_serving"]

    def collect_delta(
        previous_map: Dict[str, List[str]],
        current_map: Dict[str, List[str]],
    ) -> Dict[str, Dict[str, List[str]]]:
        changes: Dict[str, Dict[str, List[str]]] = {}
        for node_id in sorted(set(previous_map) | set(current_map)):
            previous_values = set(previous_map.get(node_id, []))
            current_values = set(current_map.get(node_id, []))
            added = sorted(current_values - previous_values)
            removed = sorted(previous_values - current_values)
            if added or removed:
                changes[node_id] = {
                    "added": added,
                    "removed": removed,
                }
        return changes

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
        "node_action_changes": collect_delta(
            previous["action_ids_by_node"],
            current["action_ids_by_node"],
        ),
        "node_projection_changes": collect_delta(
            previous["instruction_ids_by_node"],
            current["instruction_ids_by_node"],
        ),
        "node_envelope_instruction_changes": collect_delta(
            previous["envelope_instruction_ids_by_node"],
            current["envelope_instruction_ids_by_node"],
        ),
    }


def build_architecture_pipeline_demo_report(
    *,
    planner_name: str = "default",
    projector_name: str = "generic-dry-run",
    envelope_builder_name: str = "generic-dry-run",
) -> Dict[str, Any]:
    pipeline = build_demo_topology_snapshot_pipeline()
    planner = _build_planner(planner_name)
    projector = _build_projector(projector_name)
    envelope_builder = _build_envelope_builder(envelope_builder_name)
    slices = []
    for snapshot in pipeline.produce_snapshots():
        runtime_update_plan = planner.plan(snapshot)
        installer_plan = projector.project(runtime_update_plan)
        envelope_batch = envelope_builder.build(installer_plan)
        slices.append(
            _slice_summary(
                planner=planner,
                projector=projector,
                envelope_builder=envelope_builder,
                snapshot=snapshot,
                runtime_update_plan=runtime_update_plan,
                installer_plan=installer_plan,
                envelope_batch=envelope_batch,
            )
        )
    transitions = [
        _transition_summary(previous, current)
        for previous, current in zip(slices, slices[1:])
    ]
    return {
        "demo": {
            "demo_name": "iteration2_architecture_pipeline_demo",
            "planner_option": planner_name,
            "projector_option": projector_name,
            "envelope_builder_option": envelope_builder_name,
            "slice_count": len(slices),
        },
        "pipeline": pipeline.metadata(),
        "planner": planner.metadata(),
        "projector": projector.metadata(),
        "envelope_builder": envelope_builder.metadata(),
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
        f"{report['projector']['projector_name']} -> "
        f"{report['envelope_builder']['envelope_builder_name']}"
    )
    lines.append(f"All slices valid: {report['all_valid']}")
    for slice_summary in report["time_slices"]:
        runtime_update_plan = slice_summary["runtime_update_plan"]
        installer_projection = slice_summary["installer_projection"]
        agent_envelope_batch = slice_summary["agent_envelope_batch"]
        lines.append("")
        lines.append(
            f"Slice {slice_summary['time_slice_id']} "
            f"(t={slice_summary['relative_time_seconds']}s, "
            f"duration={slice_summary['duration_seconds']}s)"
        )
        lines.append("  Active links: " + ", ".join(slice_summary["active_link_ids"]))
        zone_pairs = ", ".join(
            f"{zone_id}->{satellite_id}"
            for zone_id, satellite_id in sorted(slice_summary["zone_serving"].items())
        )
        lines.append("  Zone serving: " + zone_pairs)
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
            "  RuntimeProjection: "
            f"{installer_projection['projection_id']} "
            f"({installer_projection['projector']['projector_name']})"
        )
        for node_id, subplan in sorted(installer_projection["node_installer_subplans"].items()):
            intent_summaries = ", ".join(
                f"{intent.get('instruction_id')}[{intent.get('instruction_type')}]"
                for intent in subplan.get("installer_intents", [])
            )
            lines.append(f"  Projected intents {node_id}: {intent_summaries}")
        lines.append(
            "  AgentEnvelopeBatch: "
            f"{agent_envelope_batch['envelope_batch_id']} "
            f"({agent_envelope_batch['envelope_builder']['envelope_builder_name']})"
        )
        for node_id, envelope in sorted(agent_envelope_batch["node_envelopes"].items()):
            intent_summaries = ", ".join(
                f"{intent.get('instruction_id')}[{intent.get('instruction_type')}]"
                for intent in envelope.get("installer_intents", [])
            )
            lines.append(
                f"  Agent envelope {node_id}: "
                f"{envelope.get('envelope_id')} -> {intent_summaries}"
            )
        validation = slice_summary["validation"]
        lines.append(
            "  Validation: "
            f"snapshot={validation['topology_snapshot']['status']}, "
            f"plan={validation['runtime_update_plan']['status']}, "
            f"flow={validation['planner_flow']['status']}, "
            f"projection_in={validation['projection_input']['status']}, "
            f"projection_out={validation['projection_output']['status']}, "
            f"envelope_in={validation['envelope_input']['status']}, "
            f"envelope_out={validation['envelope_output']['status']}"
        )
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
        for label, changes_key in (
            ("Node action changes", "node_action_changes"),
            ("Node projection changes", "node_projection_changes"),
            ("Node envelope instruction changes", "node_envelope_instruction_changes"),
        ):
            if transition[changes_key]:
                for node_id, node_change in sorted(transition[changes_key].items()):
                    lines.append(
                        f"  {label} {node_id}: "
                        f"added={node_change['added'] or ['none']}, "
                        f"removed={node_change['removed'] or ['none']}"
                    )
            else:
                lines.append(f"  {label}: none")
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
        "--envelope-builder",
        default="generic-dry-run",
        help="envelope builder choice for the demo. Currently supported: generic-dry-run",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="output format",
    )
    args = parser.parse_args()

    report = build_architecture_pipeline_demo_report(
        planner_name=args.planner,
        projector_name=args.projector,
        envelope_builder_name=args.envelope_builder,
    )
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
        return
    print(_render_text(report))


if __name__ == "__main__":
    main()
