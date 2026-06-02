#!/usr/bin/env python3
# encoding: utf-8

"""Run the first complete Iteration 2 runtime-realization demo without Docker."""

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
from distribution_store import (
    DistributedEnvelopeBatch,
    DistributionValidationResult,
    EnvelopeDistributor,
    GenericDryRunEnvelopeDistributor,
)
from installer_runtime import (
    GenericDryRunInstaller,
    LinuxForwardingDryRunInstaller,
    NodeLocalInstaller,
)
from node_local_agent import (
    AgentValidationResult,
    GenericDryRunNodeLocalAgent,
    NodeLocalAgent,
    NodeLocalAgentExecutionReport,
)
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


def _build_distributor(distributor_name: str) -> EnvelopeDistributor:
    if distributor_name == "generic-dry-run":
        return GenericDryRunEnvelopeDistributor()
    raise ValueError(f"unsupported distributor: {distributor_name}")


def _build_agent(agent_name: str) -> NodeLocalAgent:
    if agent_name == "generic-dry-run":
        return GenericDryRunNodeLocalAgent()
    raise ValueError(f"unsupported agent: {agent_name}")


def _build_installer(installer_name: str) -> NodeLocalInstaller:
    if installer_name == "generic-dry-run":
        return GenericDryRunInstaller()
    if installer_name == "linux-forwarding-dry-run":
        return LinuxForwardingDryRunInstaller()
    raise ValueError(f"unsupported installer: {installer_name}")


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


def _delivery_ids_by_node(node_delivery_views: Dict[str, Dict[str, Any]]) -> Dict[str, str]:
    return {
        node_id: str(view.get("envelope_ref", {}).get("envelope_id"))
        for node_id, view in sorted(node_delivery_views.items())
    }


def _execution_report_ids_by_node(
    execution_reports: Dict[str, NodeLocalAgentExecutionReport]
) -> Dict[str, str]:
    return {
        node_id: report.execution_report_id
        for node_id, report in sorted(execution_reports.items())
    }


def _installer_execution_ids_by_node(
    execution_reports: Dict[str, NodeLocalAgentExecutionReport]
) -> Dict[str, str]:
    return {
        node_id: str(report.installer_execution.get("execution_id"))
        for node_id, report in sorted(execution_reports.items())
    }


def _node_runtime_realization(
    *,
    distribution_batch: DistributedEnvelopeBatch,
    agent: NodeLocalAgent,
    installer: NodeLocalInstaller,
) -> Dict[str, Dict[str, Any]]:
    node_reports: Dict[str, Dict[str, Any]] = {}
    for node_id, delivery_view in sorted(distribution_batch.node_delivery_views.items()):
        node_envelope = dict(delivery_view.get("node_envelope", {}))
        installer_input = installer.validate_input(node_envelope)
        agent_input = agent.validate_input(distribution_batch, delivery_view, node_id)
        execution_report = agent.execute(
            distribution_batch=distribution_batch,
            target_node_id=node_id,
            installer=installer,
        )
        agent_output = agent.validate_output(
            distribution_batch,
            delivery_view,
            execution_report,
            node_id,
        )
        installer_output = execution_report.installer_execution["validation"]
        backend_isolated_to_installer = (
            "operation_preview" not in json.dumps(node_envelope, sort_keys=True)
            and "backend_adapter" not in json.dumps(node_envelope, sort_keys=True)
            and "realization_target" not in json.dumps(node_envelope, sort_keys=True)
            and "operation_preview" not in json.dumps(delivery_view, sort_keys=True)
            and "backend_adapter" not in json.dumps(delivery_view, sort_keys=True)
            and "realization_target" not in json.dumps(delivery_view, sort_keys=True)
        )
        installer_backend_specific = (
            execution_report.installer_execution["execution_summary"].get(
                "backend_target"
            )
            == "linux_forwarding_dry_run"
            or any(
                step_result.get("realization_target") == "linux_forwarding_dry_run"
                for step_result in execution_report.installer_execution.get(
                    "step_results", []
                )
            )
        )
        node_reports[node_id] = {
            "delivery_view": dict(delivery_view),
            "agent_execution_report": execution_report.to_dict(),
            "validation": {
                "installer_input": installer_input.to_dict(),
                "installer_output": installer_output,
                "agent_input": agent_input.to_dict(),
                "agent_output": agent_output.to_dict(),
                "backend_isolation": {
                    "backend_preview_isolated_to_installer": backend_isolated_to_installer,
                    "installer_backend_specific_preview_present": installer_backend_specific,
                },
                "all_valid": (
                    installer_input.is_valid
                    and installer_output["status"] == "valid"
                    and agent_input.is_valid
                    and agent_output.is_valid
                    and backend_isolated_to_installer
                ),
            },
        }
    return node_reports


def _slice_summary(
    *,
    planner: Planner,
    projector: RuntimeProjector,
    envelope_builder: AgentEnvelopeBuilder,
    distributor: EnvelopeDistributor,
    agent: NodeLocalAgent,
    installer: NodeLocalInstaller,
    snapshot: Any,
    runtime_update_plan: Any,
    installer_plan: DryRunInstallerPlan,
    envelope_batch: AgentInstallerEnvelopeBatch,
    distribution_batch: DistributedEnvelopeBatch,
    node_reports: Dict[str, Dict[str, Any]],
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
    distribution_input_validation: DistributionValidationResult = distributor.validate_input(
        envelope_batch
    )
    distribution_output_validation: DistributionValidationResult = distributor.validate_output(
        envelope_batch,
        distribution_batch,
    )
    execution_reports = {
        node_id: report["agent_execution_report"]
        for node_id, report in node_reports.items()
    }
    planner_output_remains_generic = all(
        action.get("action_type") in (
            "service_binding",
            "traffic_classification",
            "forwarding_entry",
            "node_capability",
        )
        for subplan in runtime_update_plan.node_subplans.values()
        for action in subplan.get("actions", [])
    )
    projection_remains_backend_neutral = all(
        str(intent.get("execution_preview", {}).get("backend_family", "")).startswith("generic_")
        for subplan in installer_plan.node_installer_subplans.values()
        for intent in subplan.get("installer_intents", [])
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
        "distribution_batch": distribution_batch.to_dict(),
        "node_runtime_reports": node_reports,
        "action_ids_by_node": _action_ids_by_node(runtime_update_plan.node_subplans),
        "instruction_ids_by_node": _instruction_ids_by_node(
            installer_plan.node_installer_subplans
        ),
        "envelope_ids_by_node": _envelope_ids_by_node(envelope_batch.node_envelopes),
        "delivery_ids_by_node": _delivery_ids_by_node(
            distribution_batch.node_delivery_views
        ),
        "agent_execution_ids_by_node": {
            node_id: report["agent_execution_report"]["execution_report_id"]
            for node_id, report in sorted(node_reports.items())
        },
        "installer_execution_ids_by_node": {
            node_id: report["agent_execution_report"]["installer_execution"][
                "execution_id"
            ]
            for node_id, report in sorted(node_reports.items())
        },
        "validation": {
            "topology_snapshot": topology_validation.to_dict(),
            "runtime_update_plan": plan_validation.to_dict(),
            "planner_flow": planner_flow_validation.to_dict(),
            "projection_input": projection_input_validation.to_dict(),
            "projection_output": projection_output_validation.to_dict(),
            "envelope_input": envelope_input_validation.to_dict(),
            "envelope_output": envelope_output_validation.to_dict(),
            "distribution_input": distribution_input_validation.to_dict(),
            "distribution_output": distribution_output_validation.to_dict(),
            "backend_architecture": {
                "planner_output_remains_generic": planner_output_remains_generic,
                "projection_remains_backend_neutral_before_adapter": (
                    projection_remains_backend_neutral
                ),
                "backend_specific_preview_isolated_to_installer": all(
                    report["validation"]["backend_isolation"][
                        "backend_preview_isolated_to_installer"
                    ]
                    for report in node_reports.values()
                ),
                "no_real_commands_executed": all(
                    report["agent_execution_report"]["observed_state"][
                        "runtime_mutation_performed"
                    ]
                    is False
                    for report in node_reports.values()
                ),
            },
            "node_runtime": {
                node_id: report["validation"]
                for node_id, report in sorted(node_reports.items())
            },
            "all_valid": (
                topology_validation.is_valid
                and plan_validation.is_valid
                and planner_flow_validation.is_valid
                and projection_input_validation.is_valid
                and projection_output_validation.is_valid
                and envelope_input_validation.is_valid
                and envelope_output_validation.is_valid
                and distribution_input_validation.is_valid
                and distribution_output_validation.is_valid
                and planner_output_remains_generic
                and projection_remains_backend_neutral
                and all(report["validation"]["all_valid"] for report in node_reports.values())
            ),
        },
    }


def _transition_summary(previous: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Any]:
    previous_links = set(previous["active_link_ids"])
    current_links = set(current["active_link_ids"])
    previous_zone_serving = previous["zone_serving"]
    current_zone_serving = current["zone_serving"]

    def collect_delta(
        previous_map: Dict[str, Any],
        current_map: Dict[str, Any],
    ) -> Dict[str, Dict[str, List[str]]]:
        changes: Dict[str, Dict[str, List[str]]] = {}
        for node_id in sorted(set(previous_map) | set(current_map)):
            previous_values = {str(previous_map.get(node_id))} if node_id in previous_map else set()
            current_values = {str(current_map.get(node_id))} if node_id in current_map else set()
            if isinstance(previous_map.get(node_id), list):
                previous_values = set(str(value) for value in previous_map.get(node_id, []))
            if isinstance(current_map.get(node_id), list):
                current_values = set(str(value) for value in current_map.get(node_id, []))
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
        "node_envelope_changes": collect_delta(
            previous["envelope_ids_by_node"],
            current["envelope_ids_by_node"],
        ),
        "node_delivery_changes": collect_delta(
            previous["delivery_ids_by_node"],
            current["delivery_ids_by_node"],
        ),
        "node_agent_execution_changes": collect_delta(
            previous["agent_execution_ids_by_node"],
            current["agent_execution_ids_by_node"],
        ),
        "node_installer_execution_changes": collect_delta(
            previous["installer_execution_ids_by_node"],
            current["installer_execution_ids_by_node"],
        ),
    }


def build_runtime_realization_demo_report(
    *,
    planner_name: str = "default",
    projector_name: str = "generic-dry-run",
    envelope_builder_name: str = "generic-dry-run",
    distributor_name: str = "generic-dry-run",
    agent_name: str = "generic-dry-run",
    installer_name: str = "generic-dry-run",
) -> Dict[str, Any]:
    pipeline = build_demo_topology_snapshot_pipeline()
    planner = _build_planner(planner_name)
    projector = _build_projector(projector_name)
    envelope_builder = _build_envelope_builder(envelope_builder_name)
    distributor = _build_distributor(distributor_name)
    agent = _build_agent(agent_name)
    installer = _build_installer(installer_name)
    slices = []
    for snapshot in pipeline.produce_snapshots():
        runtime_update_plan = planner.plan(snapshot)
        installer_plan = projector.project(runtime_update_plan)
        envelope_batch = envelope_builder.build(installer_plan)
        distribution_batch = distributor.publish(envelope_batch)
        node_reports = _node_runtime_realization(
            distribution_batch=distribution_batch,
            agent=agent,
            installer=installer,
        )
        slices.append(
            _slice_summary(
                planner=planner,
                projector=projector,
                envelope_builder=envelope_builder,
                distributor=distributor,
                agent=agent,
                installer=installer,
                snapshot=snapshot,
                runtime_update_plan=runtime_update_plan,
                installer_plan=installer_plan,
                envelope_batch=envelope_batch,
                distribution_batch=distribution_batch,
                node_reports=node_reports,
            )
        )
    transitions = [
        _transition_summary(previous, current)
        for previous, current in zip(slices, slices[1:])
    ]
    return {
        "demo": {
            "demo_name": "iteration2_runtime_realization_demo",
            "planner_option": planner_name,
            "projector_option": projector_name,
            "envelope_builder_option": envelope_builder_name,
            "distributor_option": distributor_name,
            "agent_option": agent_name,
            "installer_option": installer_name,
            "slice_count": len(slices),
        },
        "pipeline": pipeline.metadata(),
        "planner": planner.metadata(),
        "projector": projector.metadata(),
        "envelope_builder": envelope_builder.metadata(),
        "distributor": distributor.metadata(),
        "agent": agent.metadata(),
        "installer": installer.metadata(),
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
        f"{report['envelope_builder']['envelope_builder_name']} -> "
        f"{report['distributor']['distributor_name']} -> "
        f"{report['agent']['agent_name']} -> "
        f"{report['installer']['installer_name']}"
    )
    lines.append(f"All slices valid: {report['all_valid']}")
    for slice_summary in report["time_slices"]:
        runtime_update_plan = slice_summary["runtime_update_plan"]
        installer_projection = slice_summary["installer_projection"]
        agent_envelope_batch = slice_summary["agent_envelope_batch"]
        distribution_batch = slice_summary["distribution_batch"]
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
        lines.append(
            "  RuntimeProjection: "
            f"{installer_projection['projection_id']} "
            f"({installer_projection['projector']['projector_name']})"
        )
        lines.append(
            "  AgentEnvelopeBatch: "
            f"{agent_envelope_batch['envelope_batch_id']} "
            f"({agent_envelope_batch['envelope_builder']['envelope_builder_name']})"
        )
        lines.append(
            "  DistributionBatch: "
            f"{distribution_batch['distribution_id']} "
            f"({distribution_batch['distributor']['distributor_name']})"
        )
        for node_id, node_report in sorted(slice_summary["node_runtime_reports"].items()):
            execution_report = node_report["agent_execution_report"]
            installer_execution = execution_report["installer_execution"]
            lines.append(
                f"  Node runtime {node_id}: "
                f"delivery={node_report['delivery_view']['envelope_ref']['envelope_id']}, "
                f"agent={execution_report['execution_report_id']}, "
                f"installer={installer_execution['execution_id']}, "
                f"status={execution_report['execution_summary']['status']}"
            )
        validation = slice_summary["validation"]
        lines.append(
            "  Validation: "
            f"snapshot={validation['topology_snapshot']['status']}, "
            f"plan={validation['runtime_update_plan']['status']}, "
            f"flow={validation['planner_flow']['status']}, "
            f"projection={validation['projection_output']['status']}, "
            f"envelope={validation['envelope_output']['status']}, "
            f"distribution={validation['distribution_output']['status']}"
        )
        lines.append(
            "  Backend proof: "
            f"generic_plan={validation['backend_architecture']['planner_output_remains_generic']}, "
            f"neutral_projection={validation['backend_architecture']['projection_remains_backend_neutral_before_adapter']}, "
            f"isolated_backend_preview={validation['backend_architecture']['backend_specific_preview_isolated_to_installer']}, "
            f"no_real_commands={validation['backend_architecture']['no_real_commands_executed']}"
        )
        node_runtime_bits = ", ".join(
            f"{node_id}={'valid' if report['validation']['all_valid'] else 'issues_detected'}"
            for node_id, report in sorted(slice_summary["node_runtime_reports"].items())
        )
        lines.append("  Node runtime validation: " + node_runtime_bits)
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
            ("Node envelope changes", "node_envelope_changes"),
            ("Node delivery changes", "node_delivery_changes"),
            ("Node agent execution changes", "node_agent_execution_changes"),
            ("Node installer execution changes", "node_installer_execution_changes"),
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
    parser.add_argument("--planner", default="default")
    parser.add_argument("--projector", default="generic-dry-run")
    parser.add_argument("--envelope-builder", default="generic-dry-run")
    parser.add_argument("--distributor", default="generic-dry-run")
    parser.add_argument("--agent", default="generic-dry-run")
    parser.add_argument("--installer", default="generic-dry-run")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args()

    report = build_runtime_realization_demo_report(
        planner_name=args.planner,
        projector_name=args.projector,
        envelope_builder_name=args.envelope_builder,
        distributor_name=args.distributor,
        agent_name=args.agent,
        installer_name=args.installer,
    )
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
        return
    print(_render_text(report))


if __name__ == "__main__":
    main()
