#!/usr/bin/env python3
# encoding: utf-8

"""Run the first non-legacy Iteration 2 satellite experiment path."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from agent_envelope import GenericDryRunAgentEnvelopeBuilder
from contracts import (
    validate_network_mutation_plan,
    validate_runtime_update_plan,
    validate_topology_snapshot,
)
from distribution_store import GenericDryRunEnvelopeDistributor
from experiment_topology import build_experiment_topology_snapshot_pipeline
from installer_runtime import (
    GenericDryRunInstaller,
    LinuxForwardingDryRunInstaller,
    LinuxMplsDryRunInstaller,
    NodeLocalInstaller,
)
from linux_mpls_runtime import MinimalRealLinuxMplsInstaller
from network_runtime import MinimalRealNetworkMutationExecutor
from network_mutation import (
    build_network_mutation_plan,
    dynamic_slot_state_from_link_state,
    dynamic_slot_state_from_snapshot,
    load_link_state,
    summarize_network_mutation_plan,
)
from node_local_agent import GenericDryRunNodeLocalAgent
from planner import UtInternetExperimentPlanner
from planner_validation import PlannerValidationResult
from runtime_projection import GenericDryRunProjector


DEFAULT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_DIR = DEFAULT_DIR / "configs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the first non-legacy Iteration 2 satellite experiment from "
            "semi-real orbit input through validated runtime-realization preview."
        )
    )
    parser.add_argument(
        "--topology-config",
        default=str(DEFAULT_CONFIG_DIR / "topology.json"),
        help="Path to topology.json used for inventory details.",
    )
    parser.add_argument(
        "--link-state",
        default=str(DEFAULT_CONFIG_DIR / "link_state.json"),
        help="Path to link_state.json used as the current runtime baseline.",
    )
    parser.add_argument(
        "--input-profile",
        choices=("semi-real-orbital-elements", "synthetic-position"),
        default="semi-real-orbital-elements",
        help="Choose the experiment topology input family.",
    )
    parser.add_argument(
        "--scenario",
        choices=("single-ut-direct", "small-scale-semi-real-internet"),
        default="small-scale-semi-real-internet",
        help="Choose the Iteration 2 satellite experiment scenario.",
    )
    parser.add_argument(
        "--installer",
        choices=(
            "generic-dry-run",
            "linux-forwarding-dry-run",
            "linux-mpls-dry-run",
        ),
        default="linux-mpls-dry-run",
        help="Choose the node-local installer preview backend.",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Render the experiment report as text or JSON.",
    )
    parser.add_argument(
        "--execute-real",
        action="store_true",
        help=(
            "Opt in to the first minimal real non-legacy Linux/MPLS apply/verify/rollback "
            "path for one safe slice."
        ),
    )
    parser.add_argument(
        "--target-slice-id",
        default="small-scale-slice-0002",
        help="Slice id to target for real execution when --execute-real is enabled.",
    )
    return parser.parse_args()


def _build_installer(installer_name: str) -> NodeLocalInstaller:
    if installer_name == "generic-dry-run":
        return GenericDryRunInstaller()
    if installer_name == "linux-forwarding-dry-run":
        return LinuxForwardingDryRunInstaller()
    if installer_name == "linux-mpls-dry-run":
        return LinuxMplsDryRunInstaller()
    raise ValueError(f"unsupported installer: {installer_name}")


def _planner_output_remains_generic(runtime_update_plan: Any) -> bool:
    return all(
        action.get("action_type") in (
            "service_binding",
            "traffic_classification",
            "forwarding_entry",
            "node_capability",
        )
        for subplan in runtime_update_plan.node_subplans.values()
        for action in subplan.get("actions", [])
    )


def _projection_remains_backend_neutral(installer_plan: Any) -> bool:
    return all(
        str(intent.get("execution_preview", {}).get("backend_family", "")).startswith("generic_")
        for subplan in installer_plan.node_installer_subplans.values()
        for intent in subplan.get("installer_intents", [])
    )


def _ut_side_state_remains_lightweight(runtime_update_plan: Any) -> bool:
    forbidden_fields = {
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
    }
    for subplan in runtime_update_plan.node_subplans.values():
        if str(subplan.get("target_node_type")) != "user_terminal":
            continue
        for action in subplan.get("actions", []):
            if str(action.get("action_type")) != "forwarding_entry":
                continue
            gateway_selection = dict(
                action.get("intent", {}).get("outcome", {}).get("gateway_selection", {})
            )
            if any(field_name in gateway_selection for field_name in forbidden_fields):
                return False
    return True


def _node_runtime_realization(
    *,
    runtime_update_plan: Any,
    installer_plan: Any,
    envelope_batch: Any,
    distribution_batch: Any,
    installer: NodeLocalInstaller,
) -> Dict[str, Dict[str, Any]]:
    agent = GenericDryRunNodeLocalAgent()
    node_reports: Dict[str, Dict[str, Any]] = {}
    upstream_payload_text = json.dumps(
        {
            "runtime_update_plan": runtime_update_plan.to_dict(),
            "installer_projection": installer_plan.to_dict(),
            "agent_envelope_batch": envelope_batch.to_dict(),
            "distribution_batch": distribution_batch.to_dict(),
        },
        sort_keys=True,
    )
    backend_preview_isolated_to_installer = (
        "linux_mpls_dry_run" not in upstream_payload_text
        and "linux_forwarding_dry_run" not in upstream_payload_text
        and "operation_preview" not in upstream_payload_text
        and "backend_adapter" not in upstream_payload_text
    )

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
        backend_specific_preview_present = bool(
            execution_report.installer_execution["execution_summary"].get(
                "backend_specific_preview",
                False,
            )
            or any(
                step_result.get("realization_target") is not None
                for step_result in execution_report.installer_execution.get(
                    "step_results",
                    []
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
                    "backend_preview_isolated_to_installer": backend_preview_isolated_to_installer,
                    "backend_specific_preview_present": backend_specific_preview_present,
                },
                "all_valid": (
                    installer_input.is_valid
                    and installer_output["status"] == "valid"
                    and agent_input.is_valid
                    and agent_output.is_valid
                    and backend_preview_isolated_to_installer
                    and backend_specific_preview_present
                ),
            },
        }
    return node_reports


def _active_link_bindings(snapshot: Any) -> Dict[str, Dict[str, Any]]:
    return {
        str(link.get("link_id")): dict(link.get("binding", {}))
        for link in snapshot.active_links
    }


def _transition_summary(
    *,
    previous_snapshot: Any,
    current_snapshot: Any,
    network_mutation_plan: Any,
) -> Dict[str, Any]:
    previous_bindings = _active_link_bindings(previous_snapshot)
    current_bindings = _active_link_bindings(current_snapshot)
    changed_links = []
    for link_id in sorted(set(previous_bindings) | set(current_bindings)):
        if previous_bindings.get(link_id) == current_bindings.get(link_id):
            continue
        changed_links.append(
            {
                "link_id": link_id,
                "previous_binding": previous_bindings.get(link_id),
                "current_binding": current_bindings.get(link_id),
            }
        )
    return {
        "from_slice_id": previous_snapshot.time_slice.get("slice_id"),
        "to_slice_id": current_snapshot.time_slice.get("slice_id"),
        "changed_links": changed_links,
        "network_mutation_summary": summarize_network_mutation_plan(network_mutation_plan),
    }


def build_experiment_report(
    *,
    topology_path: Path,
    link_state_path: Path,
    input_profile: str,
    scenario_name: str,
    installer_name: str,
    execute_real: bool,
    target_slice_id: str,
) -> Dict[str, Any]:
    pipeline = build_experiment_topology_snapshot_pipeline(
        topology_path=topology_path,
        input_profile=input_profile,
        scenario_name=scenario_name,
    )
    planner = UtInternetExperimentPlanner()
    projector = GenericDryRunProjector()
    envelope_builder = GenericDryRunAgentEnvelopeBuilder()
    distributor = GenericDryRunEnvelopeDistributor()
    installer = _build_installer(installer_name)

    snapshots = pipeline.produce_snapshots()
    baseline_slot_state = dynamic_slot_state_from_link_state(load_link_state(link_state_path))
    current_slot_state = dict(baseline_slot_state)
    current_state_id = f"link_state:{link_state_path.name}"

    slice_reports: List[Dict[str, Any]] = []
    slice_contexts: List[Dict[str, Any]] = []
    transition_reports: List[Dict[str, Any]] = []
    previous_snapshot = None

    for index, snapshot in enumerate(snapshots, start=1):
        runtime_update_plan = planner.plan(snapshot)
        installer_plan = projector.project(runtime_update_plan)
        envelope_batch = envelope_builder.build(installer_plan)
        distribution_batch = distributor.publish(envelope_batch)
        node_reports = _node_runtime_realization(
            runtime_update_plan=runtime_update_plan,
            installer_plan=installer_plan,
            envelope_batch=envelope_batch,
            distribution_batch=distribution_batch,
            installer=installer,
        )
        network_mutation_plan = build_network_mutation_plan(
            source_state=current_slot_state,
            source_state_id=current_state_id,
            target_snapshot=snapshot,
            transition_name=(
                f"baseline_to_{snapshot.time_slice.get('slice_id')}"
                if previous_snapshot is None
                else (
                    f"{previous_snapshot.time_slice.get('slice_id')}"
                    f"_to_{snapshot.time_slice.get('slice_id')}"
                )
            ),
        )
        baseline_network_mutation_plan = build_network_mutation_plan(
            source_state=baseline_slot_state,
            source_state_id=f"link_state:{link_state_path.name}",
            target_snapshot=snapshot,
            transition_name=f"baseline_to_{snapshot.time_slice.get('slice_id')}",
        )

        topology_validation = validate_topology_snapshot(snapshot)
        plan_validation = validate_runtime_update_plan(runtime_update_plan)
        mutation_validation = validate_network_mutation_plan(network_mutation_plan)
        planner_flow_validation: PlannerValidationResult = planner.validate_flow(
            snapshot,
            runtime_update_plan,
        )
        node_reports_valid = all(
            report["validation"]["all_valid"] for report in node_reports.values()
        )
        planner_output_generic = _planner_output_remains_generic(runtime_update_plan)
        projection_backend_neutral = _projection_remains_backend_neutral(installer_plan)
        ut_side_state_lightweight = _ut_side_state_remains_lightweight(runtime_update_plan)
        all_valid = (
            topology_validation.is_valid
            and plan_validation.is_valid
            and mutation_validation.is_valid
            and planner_flow_validation.is_valid
            and node_reports_valid
            and planner_output_generic
            and projection_backend_neutral
            and ut_side_state_lightweight
        )

        slice_report = {
            "time_slice_id": snapshot.time_slice.get("slice_id"),
            "snapshot_id": snapshot.snapshot_id,
            "relative_time_seconds": snapshot.time_slice.get("relative_time_seconds"),
            "duration_seconds": snapshot.time_slice.get("duration_seconds"),
            "orbit_input_kind": snapshot.planner_hints.get("orbit_input_kind"),
            "active_links": [
                {
                    "link_id": str(link.get("link_id")),
                    "slot_type": str(link.get("slot_type")),
                    "binding": dict(link.get("binding", {})),
                }
                for link in snapshot.active_links
            ],
            "zone_serving": {
                zone_id: zone.get("active_serving_satellite_id")
                for zone_id, zone in sorted(snapshot.zones.items())
            },
            "zone_resolution_states": {
                zone_id: zone.get("resolution_state", "resolved")
                for zone_id, zone in sorted(snapshot.zones.items())
            },
            "network_mutation_plan": network_mutation_plan.to_dict(),
            "network_mutation_summary": summarize_network_mutation_plan(
                network_mutation_plan
            ),
            "runtime_update_plan": runtime_update_plan.to_dict(),
            "installer_projection": installer_plan.to_dict(),
            "agent_envelope_batch": envelope_batch.to_dict(),
            "distribution_batch": distribution_batch.to_dict(),
            "node_runtime_reports": node_reports,
            "validation": {
                "topology_snapshot": topology_validation.to_dict(),
                "runtime_update_plan": plan_validation.to_dict(),
                "network_mutation_plan": mutation_validation.to_dict(),
                "planner_flow": planner_flow_validation.to_dict(),
                "planner_output_generic": planner_output_generic,
                "projection_backend_neutral": projection_backend_neutral,
                "ut_side_state_lightweight": ut_side_state_lightweight,
                "node_reports_all_valid": node_reports_valid,
                "all_valid": all_valid,
            },
        }
        slice_reports.append(slice_report)
        slice_contexts.append(
            {
                "snapshot": snapshot,
                "runtime_update_plan": runtime_update_plan,
                "distribution_batch": distribution_batch,
                "network_mutation_plan": network_mutation_plan,
                "baseline_network_mutation_plan": baseline_network_mutation_plan,
                "slice_report": slice_report,
            }
        )

        if previous_snapshot is not None:
            transition_reports.append(
                _transition_summary(
                    previous_snapshot=previous_snapshot,
                    current_snapshot=snapshot,
                    network_mutation_plan=network_mutation_plan,
                )
            )

        previous_snapshot = snapshot
        current_slot_state = dynamic_slot_state_from_snapshot(snapshot)
        current_state_id = snapshot.snapshot_id

    overall_valid = all(
        slice_report["validation"]["all_valid"] for slice_report in slice_reports
    )
    real_execution = None
    if execute_real:
        target_context = next(
            (
                context
                for context in slice_contexts
                if context["snapshot"].time_slice.get("slice_id") == target_slice_id
            ),
            None,
        )
        if target_context is None:
            raise RuntimeError(f"unknown target slice for real execution: {target_slice_id}")
        if target_context["slice_report"]["validation"]["all_valid"] is not True:
            raise RuntimeError(
                f"target slice {target_slice_id} is not preview-valid; refusing real execution"
            )
        network_executor = MinimalRealNetworkMutationExecutor()
        host_mutation_apply = network_executor.apply(
            topology_path=topology_path,
            link_state_path=link_state_path,
            network_mutation_plan=target_context["baseline_network_mutation_plan"],
        ).to_dict()
        if host_mutation_apply["outcome"]["apply_success"] is not True:
            raise RuntimeError(
                f"host-side network mutation failed for target slice {target_slice_id}"
            )
        node_local_execution: Dict[str, Any] = {
            "outcome": {
                "real_apply_success": False,
                "ping_success": False,
                "rollback_completed": False,
                "failure_reason": "node_local_execution_not_started",
                "flow_results": [],
            },
            "ping_result": {
                "ut_id": None,
                "ping_target_ip": None,
                "target_host_node_id": None,
                "success": False,
            },
            "rollback_verification": {
                "cleanup_success": False,
                "contexts": [],
            },
        }
        try:
            node_local_execution = MinimalRealLinuxMplsInstaller().execute(
                topology_path=topology_path,
                topology_snapshot=target_context["snapshot"],
                runtime_update_plan=target_context["runtime_update_plan"],
                distribution_batch=target_context["distribution_batch"],
                require_preview_validation=True,
                host_runtime_summary=host_mutation_apply["boundary"],
            ).to_dict()
        finally:
            host_mutation_rollback = network_executor.rollback(
                topology_path=topology_path,
                link_state_path=link_state_path,
                network_mutation_plan=target_context["baseline_network_mutation_plan"],
                applied_operations=host_mutation_apply["outcome"].get("applied_operations", []),
            ).to_dict()
        if host_mutation_rollback["outcome"]["rollback_completed"] is not True:
            raise RuntimeError(
                f"host-side network mutation rollback failed for target slice {target_slice_id}"
            )
        real_execution = {
            "host_mutation_execution": {
                "apply": host_mutation_apply,
                "rollback": host_mutation_rollback,
            },
            "node_local_execution": node_local_execution,
            "outcome": {
                "real_apply_success": (
                    host_mutation_apply["outcome"]["apply_success"]
                    and node_local_execution["outcome"]["real_apply_success"]
                ),
                "ping_success": node_local_execution["outcome"]["ping_success"],
                "rollback_completed": (
                    host_mutation_rollback["outcome"]["rollback_completed"]
                    and node_local_execution["outcome"]["rollback_completed"]
                ),
                "failure_reason": (
                    host_mutation_apply["outcome"]["failure_reason"]
                    or host_mutation_rollback["outcome"]["failure_reason"]
                    or node_local_execution["outcome"]["failure_reason"]
                ),
            },
        }

    return {
        "experiment": {
            "experiment_name": "iteration2_small_scale_satellite_internet_example",
            "orbit_input_kind": input_profile,
            "scenario_name": scenario_name,
            "real_execution_performed": real_execution is not None,
            "validated_execution_preview": overall_valid,
            "legacy_bridge_used": False,
            "installer_backend": installer.metadata(),
            "all_valid": overall_valid,
            "target_slice_id": target_slice_id,
            "runtime_architecture_direction": "agent_daemon_runtime_manager_transition",
        },
        "pipeline": {
            **pipeline.metadata(),
            "planner": planner.metadata(),
            "projector": projector.metadata(),
            "envelope_builder": envelope_builder.metadata(),
            "distributor": distributor.metadata(),
            "installer": installer.metadata(),
            "network_mutation_boundary": "host_network_runtime_manager",
        },
        "slices": slice_reports,
        "transitions": transition_reports,
        "validation": {
            "slice_count": len(slice_reports),
            "all_valid": overall_valid,
        },
        "real_execution": real_execution,
    }


def _render_text(report: Dict[str, Any]) -> str:
    lines: List[str] = []
    pipeline = report["pipeline"]
    lines.append(
        "Pipeline: "
        f"{pipeline['orbit_producer']} -> {pipeline['visibility_model']} -> "
        f"{pipeline['zone_generator']} -> {pipeline['topology_slicer']} -> "
        f"{pipeline['planner']['planner_name']} -> RuntimeUpdatePlan"
    )
    lines.append(
        "Runtime path: "
        f"{pipeline['projector']['projector_name']} -> "
        f"{pipeline['envelope_builder']['envelope_builder_name']} -> "
        f"{pipeline['distributor']['distributor_name']} -> "
        f"{pipeline['installer']['installer_name']}"
    )
    lines.append(
        "Runtime posture: "
        + report["experiment"]["runtime_architecture_direction"]
    )
    lines.append(
        "Network mutation path: "
        f"NetworkMutationPlan -> {pipeline['network_mutation_boundary']}"
    )
    lines.append(f"All slices valid: {report['validation']['all_valid']}")
    if report["real_execution"] is None:
        lines.append("Real connectivity: not attempted; validated execution preview only")
    else:
        outcome = report["real_execution"]["outcome"]
        ping_result = report["real_execution"]["node_local_execution"]["ping_result"]
        lines.append(
            "Real execution: "
            f"apply_success={outcome['real_apply_success']} "
            f"rollback_completed={outcome['rollback_completed']} "
            f"ping_success={ping_result['success']}"
        )

    for slice_report in report["slices"]:
        lines.append(
            f"Slice {slice_report['time_slice_id']} "
            f"(t={slice_report['relative_time_seconds']}s)"
        )
        lines.append(
            "  Active links: "
            + ", ".join(
                f"{link['link_id']}={json.dumps(link['binding'], sort_keys=True)}"
                for link in slice_report["active_links"]
            )
        )
        lines.append(
            "  Zone serving: "
            + json.dumps(slice_report["zone_serving"], sort_keys=True)
        )
        lines.append(
            "  Zone states: "
            + json.dumps(slice_report["zone_resolution_states"], sort_keys=True)
        )
        mutation_summary = slice_report["network_mutation_summary"]
        lines.append(
            "  Mutation ops: "
            f"{mutation_summary['operation_count']} "
            + json.dumps(mutation_summary["operation_counts"], sort_keys=True)
        )
        for node_id, node_report in sorted(slice_report["node_runtime_reports"].items()):
            execution_summary = node_report["agent_execution_report"]["installer_execution"][
                "execution_summary"
            ]
            lines.append(
                f"  Node {node_id}: "
                f"backend={execution_summary.get('backend_target', 'generic_dry_run')} "
                f"valid={node_report['validation']['all_valid']}"
            )
    for transition in report["transitions"]:
        lines.append(
            f"Transition {transition['from_slice_id']} -> {transition['to_slice_id']}: "
            f"{len(transition['changed_links'])} changed links, "
            f"{transition['network_mutation_summary']['operation_count']} mutation ops"
        )
    if report["real_execution"] is not None:
        ping_result = report["real_execution"]["node_local_execution"]["ping_result"]
        flow_results = report["real_execution"]["node_local_execution"]["outcome"].get(
            "flow_results",
            [],
        )
        lines.append(
            "Ping result: "
            f"{ping_result['ut_id']} -> {ping_result['ping_target_ip']} "
            f"({ping_result['target_host_node_id']}) success={ping_result['success']}"
        )
        if flow_results:
            lines.append(
                "  All flow pings: "
                + ", ".join(
                    f"{flow_result['ut_id']}={flow_result['success']}"
                    for flow_result in flow_results
                )
            )
        lines.append(
            "Rollback verification: "
            + json.dumps(
                {
                    "host_mutation": report["real_execution"][
                        "host_mutation_execution"
                    ]["rollback"]["rollback_verification"],
                    "node_local": report["real_execution"]["node_local_execution"][
                        "rollback_verification"
                    ],
                },
                sort_keys=True,
            )
        )
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    report = build_experiment_report(
        topology_path=Path(args.topology_config).resolve(),
        link_state_path=Path(args.link_state).resolve(),
        input_profile=args.input_profile,
        scenario_name=args.scenario,
        installer_name=args.installer,
        execute_real=bool(args.execute_real),
        target_slice_id=str(args.target_slice_id),
    )
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(_render_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
