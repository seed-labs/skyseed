#!/usr/bin/env python3
# encoding: utf-8

"""Run the first non-legacy Iteration 2 satellite experiment path."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from _path_setup import bootstrap_app_import_path

SATELLITE_DIR = bootstrap_app_import_path()

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
DEFAULT_CONFIG_DIR = SATELLITE_DIR / "configs" / "iteration2_small_scale"


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
            "path for one safe slice while the full multi-slice sequence remains previewable."
        ),
    )
    parser.add_argument(
        "--loop-count",
        type=int,
        default=1,
        help="Repeat the deterministic slice sequence this many times for preview or opt-in real runs.",
    )
    parser.add_argument(
        "--target-slice-id",
        default="small-scale-slice-0003",
        help="Slice id to target for real execution when --execute-real is enabled.",
    )
    parser.add_argument(
        "--max-node-workers",
        type=int,
        default=2,
        help="Bounded worker count for per-node real runtime batch execution.",
    )
    parser.add_argument(
        "--max-host-workers",
        type=int,
        default=1,
        help=(
            "Bounded worker count requested for host-side NetworkMutationPlan "
            "execution. The current controller-backed transport may still force "
            "effective serial execution for safety."
        ),
    )
    parser.add_argument(
        "--continuous-real",
        action="store_true",
        help=(
            "Execute the full slice sequence continuously with real apply/verify "
            "transitions instead of the single target-slice regression mode."
        ),
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.0,
        help=(
            "Optional demo pacing sleep, in seconds, inserted between continuous "
            "slice transitions without being counted as runtime transition cost."
        ),
    )
    parser.add_argument(
        "--runtime-mode",
        choices=("correctness", "fast-demo"),
        default="correctness",
        help=(
            "Use full correctness checks or a faster demo-oriented transition mode "
            "that reuses installed node-local state between slices."
        ),
    )
    parser.add_argument(
        "--observe-flow",
        action="append",
        default=[],
        help=(
            "Packet-path observation selector in the form ut_id:target_host_node_id "
            "or ut_id:target_ip, for example ut1:as160-host1."
        ),
    )
    parser.add_argument(
        "--stream-progress",
        action="store_true",
        help=(
            "Emit per-transition progress markers to stderr during continuous "
            "real execution so manual ping/tcpdump observation can be correlated "
            "with the active slice."
        ),
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


def _ut_gateway_behavior(runtime_update_plan: Any) -> Dict[str, Dict[str, Any]]:
    behavior: Dict[str, Dict[str, Any]] = {}
    for subplan in runtime_update_plan.node_subplans.values():
        if str(subplan.get("target_node_type")) != "user_terminal":
            continue
        for action in subplan.get("actions", []):
            if str(action.get("action_type")) != "forwarding_entry":
                continue
            gateway_selection = dict(
                action.get("intent", {}).get("outcome", {}).get("gateway_selection", {})
            )
            ut_id = str(gateway_selection.get("ut_id", subplan.get("target_node_id")))
            behavior[ut_id] = {
                "default_gateway_class_id": gateway_selection.get("default_gateway_class_id"),
                "special_prefix_overrides": list(
                    gateway_selection.get("special_prefix_overrides", [])
                ),
                "gateway_class_bindings": list(
                    gateway_selection.get("gateway_class_bindings", [])
                )
                or [dict(gateway_selection.get("gateway_class_binding", {}))],
            }
    return behavior


def _gateway_choice_summary(
    ut_gateway_behavior: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    summary: Dict[str, Dict[str, Any]] = {}
    for ut_id, behavior in sorted(ut_gateway_behavior.items()):
        bindings = {
            str(binding.get("gateway_class_id")): dict(binding)
            for binding in behavior.get("gateway_class_bindings", [])
            if binding
        }
        default_gateway_class_id = str(behavior.get("default_gateway_class_id"))
        default_binding = bindings.get(default_gateway_class_id, {})
        summary[ut_id] = {
            "default_gateway_class_id": behavior.get("default_gateway_class_id"),
            "default_gateway_id": default_binding.get("selected_gateway_id"),
            "serving_satellite_id": default_binding.get("serving_satellite_id"),
            "special_prefix_overrides": [
                {
                    "destination_prefix": override.get("destination_prefix"),
                    "gateway_class_id": override.get("gateway_class_id"),
                    "selected_gateway_id": bindings.get(
                        str(override.get("gateway_class_id")),
                        {},
                    ).get("selected_gateway_id"),
                    "resolution_status": override.get(
                        "resolution_status",
                        override.get("resolution_state", "resolved"),
                    ),
                }
                for override in behavior.get("special_prefix_overrides", [])
            ],
        }
    return summary


def _targets_reuse_seed_inventory(snapshot: Any) -> bool:
    integration = dict(snapshot.planner_hints.get("mini_internet_integration", {}))
    discovered_targets = {
        str(target.get("node_id"))
        for target in snapshot.planner_hints.get("internet_validation_targets", [])
    }
    external_targets = {
        str(target_id)
        for target_id in integration.get("external_test_target_node_ids", [])
    }
    return bool(discovered_targets) and discovered_targets.issubset(external_targets)


def _gateway_nat_semantics(snapshot: Any) -> Dict[str, Any]:
    catalog = dict(snapshot.planner_hints.get("gateway_ix_catalog", {}))
    semantics = {
        "gateway_nat_required": True,
        "return_path_model": "gateway_conntrack_and_snat_return_mapping",
        "satellite_domain_separate_from_mini_internet_routing": True,
        "gateways": {},
    }
    for gateway_id, gateway_info in sorted(catalog.items()):
        uplink = dict(gateway_info.get("internet_uplink", {}))
        ix_peering = dict(gateway_info.get("ix_peering", {}))
        semantics["gateways"][gateway_id] = {
            "ix_id": ix_peering.get("ix_id"),
            "uplink_network_name": uplink.get("network_name"),
            "uplink_ip_address": uplink.get("ip_address"),
            "gateway_region": gateway_info.get("gateway_region"),
            "nat_egress_interface": uplink.get("network_name"),
        }
    return semantics


def _flow_path_summaries(runtime_update_plan: Any) -> List[Dict[str, Any]]:
    flow_summaries: Dict[str, Dict[str, Any]] = {}
    for subplan in runtime_update_plan.node_subplans.values():
        for action in subplan.get("actions", []):
            if str(action.get("action_type")) != "forwarding_entry":
                continue
            outcome = dict(action.get("intent", {}).get("outcome", {}))
            flow_context = dict(outcome.get("flow_context", {}))
            if not flow_context:
                continue
            flow_id = str(flow_context.get("flow_id", ""))
            if not flow_id:
                continue
            satellite_path = [str(node_id) for node_id in flow_context.get("satellite_path", [])]
            target_host_node_id = str(flow_context.get("target_host_node_id", ""))
            summary = {
                "flow_id": flow_id,
                "ut_id": str(flow_context.get("ut_id", "")),
                "traffic_scope": str(flow_context.get("traffic_scope", "")),
                "destination_prefix": str(flow_context.get("destination_prefix", "")),
                "selected_gateway_id": str(flow_context.get("selected_gateway_id", "")),
                "selected_gateway_satellite_id": str(
                    flow_context.get("selected_gateway_satellite_id", "")
                ),
                "target_host_node_id": target_host_node_id,
                "satellite_path": satellite_path,
                "path_hop_count": len(satellite_path),
                "contains_multi_hop": len(satellite_path) > 1,
                "path_string": " -> ".join(
                    [
                        str(flow_context.get("ut_id", "")),
                        *satellite_path,
                        str(flow_context.get("selected_gateway_id", "")),
                        target_host_node_id,
                    ]
                ),
            }
            flow_summaries.setdefault(flow_id, summary)
    return [flow_summaries[flow_id] for flow_id in sorted(flow_summaries)]


def _flow_path_change_summary(
    previous_paths: Sequence[Dict[str, Any]],
    current_paths: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    previous_by_id = {
        str(entry.get("flow_id")): {
            "selected_gateway_id": entry.get("selected_gateway_id"),
            "satellite_path": list(entry.get("satellite_path", [])),
            "path_string": entry.get("path_string"),
        }
        for entry in previous_paths
    }
    current_by_id = {
        str(entry.get("flow_id")): {
            "selected_gateway_id": entry.get("selected_gateway_id"),
            "satellite_path": list(entry.get("satellite_path", [])),
            "path_string": entry.get("path_string"),
        }
        for entry in current_paths
    }
    changed_flows: List[Dict[str, Any]] = []
    for flow_id in sorted(set(previous_by_id) | set(current_by_id)):
        if previous_by_id.get(flow_id) == current_by_id.get(flow_id):
            continue
        changed_flows.append(
            {
                "flow_id": flow_id,
                "previous": previous_by_id.get(flow_id),
                "current": current_by_id.get(flow_id),
            }
        )
    return {
        "changed_flow_count": len(changed_flows),
        "changed_flows": changed_flows,
    }


def _connectivity_summary(flow_results: Sequence[Dict[str, Any]] | None) -> Dict[str, Any]:
    normalized_results = list(flow_results or [])
    return {
        "result_count": len(normalized_results),
        "ping_success": bool(normalized_results)
        and all(bool(result.get("success")) for result in normalized_results),
        "results": [dict(result) for result in normalized_results],
    }


def _await_initial_real_runtime_readiness(
    *,
    gateway_ids: Sequence[str],
    validation_targets: Sequence[Dict[str, Any]],
    timeout_seconds: float = 20.0,
    probe_interval_seconds: float = 2.0,
) -> Dict[str, Any]:
    target_ips = [
        str(target.get("ip_address"))
        for target in validation_targets
        if target.get("ip_address")
    ]
    started_at = time.perf_counter()
    attempts: List[Dict[str, Any]] = []
    if not gateway_ids or not target_ips:
        return {
            "attempt_count": 0,
            "gateway_ids": list(gateway_ids),
            "target_ips": target_ips,
            "ready": True,
            "wall_seconds": 0.0,
        }
    while True:
        attempt_started_at = time.perf_counter()
        probe_results: List[Dict[str, Any]] = []
        all_ready = True
        for gateway_id in gateway_ids:
            for target_ip in target_ips:
                completed = subprocess.run(
                    [
                        "docker",
                        "exec",
                        gateway_id,
                        "ip",
                        "route",
                        "get",
                        target_ip,
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                success = (
                    completed.returncode == 0
                    and "unreachable" not in completed.stdout.lower()
                    and "unreachable" not in completed.stderr.lower()
                )
                probe_results.append(
                    {
                        "gateway_id": gateway_id,
                        "target_ip": target_ip,
                        "success": success,
                    }
                )
                if not success:
                    all_ready = False
        attempt_record = {
            "attempt_index": len(attempts) + 1,
            "wall_seconds": time.perf_counter() - attempt_started_at,
            "probe_results": probe_results,
            "all_ready": all_ready,
        }
        attempts.append(attempt_record)
        if all_ready:
            return {
                "attempt_count": len(attempts),
                "gateway_ids": list(gateway_ids),
                "target_ips": target_ips,
                "ready": True,
                "wall_seconds": time.perf_counter() - started_at,
                "attempts": attempts,
            }
        elapsed = time.perf_counter() - started_at
        if elapsed >= float(timeout_seconds):
            return {
                "attempt_count": len(attempts),
                "gateway_ids": list(gateway_ids),
                "target_ips": target_ips,
                "ready": False,
                "wall_seconds": elapsed,
                "attempts": attempts,
            }
        time.sleep(float(probe_interval_seconds))


def _continuous_timing_summary(iterations: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    if not iterations:
        return {
            "transition_count": 0,
            "average_transition_wall_seconds": 0.0,
            "max_transition_wall_seconds": 0.0,
            "phase_averages": {},
            "dominant_phase_by_average": None,
            "host_operation_wall_seconds": {},
            "host_delta_totals": {},
            "host_manager_phase_averages": {},
            "average_unaccounted_transition_wall_seconds": 0.0,
        }
    phase_fields = {
        "network_mutation_plan_wall_seconds": "network_mutation_plan",
        "pre_transition_connectivity_probe_wall_seconds": "pre_transition_connectivity_probe",
        "node_local_preflight_wall_seconds": "node_local_preflight",
        "node_local_apply_wall_seconds": "node_local_apply",
        "node_local_verify_wall_seconds": "node_local_verify",
        "node_local_access_link_warmup_wall_seconds": "access_link_warmup",
        "node_local_ping_wall_seconds": "connectivity_probe",
        "packet_observation_wall_seconds": "packet_observation",
        "node_local_execution_public_wall_seconds": "report_serialization",
        "transition_report_wall_seconds": "transition_reporting",
        "node_local_rollback_before_transition_wall_seconds": "node_local_rollback",
    }
    phase_totals = {phase_name: 0.0 for phase_name in phase_fields.values()}
    transition_totals = 0.0
    max_transition_wall_seconds = 0.0
    host_operation_totals: Dict[str, float] = {}
    host_delta_totals: Dict[str, int] = {}
    host_manager_phase_totals: Dict[str, float] = {}
    unaccounted_transition_totals = 0.0
    for iteration in iterations:
        timing = dict(iteration.get("timing", {}))
        transition_wall_seconds = float(timing.get("transition_wall_seconds", 0.0))
        transition_totals += transition_wall_seconds
        max_transition_wall_seconds = max(max_transition_wall_seconds, transition_wall_seconds)
        for field_name, phase_name in phase_fields.items():
            phase_totals[phase_name] += float(timing.get(field_name, 0.0))
        host_summary = dict(
            iteration.get("host_mutation_execution", {})
            .get("outcome", {})
            .get("operation_timing_summary", {})
        )
        for operation_name, seconds in dict(
            host_summary.get("per_operation_wall_seconds", {})
        ).items():
            host_operation_totals[str(operation_name)] = (
                host_operation_totals.get(str(operation_name), 0.0) + float(seconds)
            )
        delta_summary = dict(
            iteration.get("host_mutation_execution", {})
            .get("outcome", {})
            .get("delta_summary", {})
        )
        for field_name in (
            "skipped_slot_count",
            "reused_slot_count",
            "changed_slot_count",
            "created_slot_count",
            "deleted_slot_count",
        ):
            host_delta_totals[field_name] = (
                host_delta_totals.get(field_name, 0) + int(delta_summary.get(field_name, 0))
            )
        manager_timing = dict(
            iteration.get("host_mutation_execution", {})
            .get("outcome", {})
            .get("manager_timing_breakdown", {})
        )
        for phase_name, seconds in manager_timing.items():
            host_manager_phase_totals[str(phase_name)] = (
                host_manager_phase_totals.get(str(phase_name), 0.0) + float(seconds)
            )
        accounted_transition_seconds = sum(
            float(timing.get(field_name, 0.0))
            for field_name in phase_fields
        )
        unaccounted_transition_totals += max(
            0.0,
            transition_wall_seconds - accounted_transition_seconds,
        )
    phase_averages = {
        phase_name: round(total / len(iterations), 3)
        for phase_name, total in sorted(phase_totals.items())
    }
    dominant_phase_by_average = max(
        phase_averages.items(),
        key=lambda item: item[1],
    )[0]
    return {
        "transition_count": len(iterations),
        "average_transition_wall_seconds": round(transition_totals / len(iterations), 3),
        "max_transition_wall_seconds": round(max_transition_wall_seconds, 3),
        "phase_averages": phase_averages,
        "dominant_phase_by_average": dominant_phase_by_average,
        "host_operation_wall_seconds": {
            operation_name: round(seconds, 3)
            for operation_name, seconds in sorted(host_operation_totals.items())
        },
        "host_delta_totals": dict(sorted(host_delta_totals.items())),
        "host_manager_phase_averages": {
            phase_name: round(total / len(iterations), 6)
            for phase_name, total in sorted(host_manager_phase_totals.items())
        },
        "average_unaccounted_transition_wall_seconds": round(
            unaccounted_transition_totals / len(iterations),
            6,
        ),
    }


def _emit_progress(message: str, *, enabled: bool) -> None:
    if not enabled:
        return
    print(message, file=sys.stderr, flush=True)


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
    previous_flow_paths: Sequence[Dict[str, Any]],
    current_flow_paths: Sequence[Dict[str, Any]],
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
        "flow_path_changes": _flow_path_change_summary(
            previous_flow_paths,
            current_flow_paths,
        ),
    }


def _build_real_transition_sequence(
    *,
    slice_contexts: Sequence[Dict[str, Any]],
    loop_count: int,
) -> List[Dict[str, Any]]:
    if not slice_contexts:
        return []
    first_time = float(slice_contexts[0]["snapshot"].time_slice.get("relative_time_seconds", 0.0))
    last_snapshot = slice_contexts[-1]["snapshot"]
    cycle_span = (
        float(last_snapshot.time_slice.get("relative_time_seconds", 0.0))
        + float(last_snapshot.time_slice.get("duration_seconds", 0.0))
        - first_time
    )
    sequence: List[Dict[str, Any]] = []
    previous_context: Optional[Dict[str, Any]] = None
    previous_simulated_time: Optional[float] = None
    for loop_index in range(1, max(1, loop_count) + 1):
        cycle_offset = (loop_index - 1) * cycle_span
        for slice_index, context in enumerate(slice_contexts, start=1):
            snapshot = context["snapshot"]
            slice_id = str(snapshot.time_slice.get("slice_id"))
            simulated_time = float(snapshot.time_slice.get("relative_time_seconds", 0.0)) + cycle_offset
            if previous_context is None:
                host_plan = context["baseline_network_mutation_plan"]
                from_slice_id = "baseline"
            else:
                previous_snapshot = previous_context["snapshot"]
                from_slice_id = str(previous_snapshot.time_slice.get("slice_id"))
                if slice_index == 1:
                    host_plan = build_network_mutation_plan(
                        source_state=dynamic_slot_state_from_snapshot(previous_snapshot),
                        source_state_id=previous_snapshot.snapshot_id,
                        target_snapshot=snapshot,
                        transition_name=f"{from_slice_id}_to_{slice_id}_loop",
                    )
                else:
                    host_plan = context["network_mutation_plan"]
            sequence.append(
                {
                    "sequence_index": len(sequence) + 1,
                    "loop_iteration": loop_index,
                    "slice_index": slice_index,
                    "from_slice_id": from_slice_id,
                    "to_slice_id": slice_id,
                    "context": context,
                    "host_plan": host_plan,
                    "simulated_time_seconds": simulated_time,
                    "simulated_delta_seconds": (
                        None
                        if previous_simulated_time is None
                        else simulated_time - previous_simulated_time
                    ),
                }
            )
            previous_context = context
            previous_simulated_time = simulated_time
    return sequence


def _warmup_ut_ids_for_transition(transition: Dict[str, Any]) -> Optional[List[str]]:
    host_plan = transition.get("host_plan")
    operations = list(getattr(host_plan, "operations", []) or [])
    if not operations:
        return None
    warmup_ut_ids: set[str] = set()
    for operation in operations:
        if str(operation.get("slot_type")) != "ut_access":
            continue
        if str(operation.get("operation")) not in {
            "activate_slot_attachment",
            "rebind_slot_attachment",
        }:
            continue
        for binding_key in ("source_binding", "target_binding"):
            binding = dict(operation.get(binding_key, {}) or {})
            if binding.get("ut_id"):
                warmup_ut_ids.add(str(binding["ut_id"]))
    if not warmup_ut_ids:
        return []
    return sorted(warmup_ut_ids)


def _run_continuous_real_execution(
    *,
    topology_path: Path,
    link_state_path: Path,
    slice_contexts: Sequence[Dict[str, Any]],
    loop_count: int,
    pacing_interval_seconds: float,
    max_node_workers: int,
    max_host_workers: int,
    runtime_mode: str,
    observe_selectors: Sequence[str],
    stream_progress: bool,
) -> Dict[str, Any]:
    real_installer = MinimalRealLinuxMplsInstaller()
    network_executor = MinimalRealNetworkMutationExecutor(
        max_host_workers=max_host_workers
    )
    transition_sequence = _build_real_transition_sequence(
        slice_contexts=slice_contexts,
        loop_count=loop_count,
    )
    initial_runtime_readiness = _await_initial_real_runtime_readiness(
        gateway_ids=sorted(
            str(gateway_id)
            for gateway_id in dict(
                slice_contexts[0]["snapshot"].planner_hints.get("gateway_ix_catalog", {})
            )
        )
        if slice_contexts
        else [],
        validation_targets=list(
            slice_contexts[0]["snapshot"].planner_hints.get("internet_validation_targets", [])
        )
        if slice_contexts
        else [],
    )
    if initial_runtime_readiness["ready"] is not True:
        return {
            "mode": "continuous_multi_slice_real_execution",
            "loop_count": max(1, loop_count),
            "pacing_interval_seconds": float(pacing_interval_seconds),
            "runtime_mode": runtime_mode,
            "initial_runtime_readiness": initial_runtime_readiness,
            "iterations": [],
            "timing_summary": _continuous_timing_summary([]),
            "final_cleanup": {
                "node_local": None,
                "host_rollbacks": [],
                "all_cleanup_success": True,
            },
            "outcome": {
                "real_apply_success": False,
                "ping_success": False,
                "rollback_completed": True,
                "failure_reason": "initial_runtime_readiness_timeout",
            },
        }
    transition_iterations: List[Dict[str, Any]] = []
    host_history: List[Dict[str, Any]] = []
    active_session = None
    applied_sessions: List[Any] = []
    failure_reason: Optional[str] = None
    final_cleanup: Dict[str, Any] = {
        "node_local": None,
        "host_rollbacks": [],
        "all_cleanup_success": False,
    }

    try:
        for transition in transition_sequence:
            transition_started_at = time.perf_counter()
            transition_started_epoch_seconds = time.time()
            _emit_progress(
                (
                    "[iter2] transition "
                    f"{transition['sequence_index']} "
                    f"{transition['from_slice_id']} -> {transition['to_slice_id']} "
                    f"(loop={transition['loop_iteration']} "
                    f"sim_t={transition['simulated_time_seconds']}s)"
                ),
                enabled=stream_progress,
            )
            pre_transition_connectivity = None
            pre_transition_paths: List[Dict[str, Any]] = []
            node_local_rollback = None
            if active_session is not None:
                pre_transition_probe_started_at = time.perf_counter()
                pre_transition_connectivity = real_installer.probe_connectivity(active_session)
                pre_transition_probe_wall_seconds = (
                    time.perf_counter() - pre_transition_probe_started_at
                )
                pre_transition_probe_completed_epoch_seconds = time.time()
                pre_transition_paths = list(active_session.timing.get("flow_path_observations", []))
                if runtime_mode == "correctness":
                    node_local_rollback = real_installer.rollback_session(
                        active_session,
                        max_node_workers=max_node_workers,
                    )
                    if node_local_rollback["rollback_verification"]["cleanup_success"] is not True:
                        raise RuntimeError(
                            "node-local rollback failed before the next continuous transition"
                        )
                    active_session = None
            else:
                pre_transition_probe_wall_seconds = 0.0
                pre_transition_probe_completed_epoch_seconds = transition_started_epoch_seconds

            host_apply_started_at = time.perf_counter()
            host_apply_result = network_executor.apply(
                topology_path=topology_path,
                link_state_path=link_state_path,
                network_mutation_plan=transition["host_plan"],
            )
            host_apply = host_apply_result.to_dict()
            host_apply_wall_seconds = time.perf_counter() - host_apply_started_at
            host_apply_completed_epoch_seconds = time.time()
            if host_apply["outcome"]["apply_success"] is not True:
                raise RuntimeError(
                    f"host-side network mutation failed for {transition['to_slice_id']}"
                )
            host_history.append(
                {
                    "transition": transition,
                    "host_plan": transition["host_plan"],
                    "applied_operations": list(
                        host_apply["outcome"].get("applied_operations", [])
                    ),
                }
            )

            active_session = real_installer.apply_and_validate(
                topology_path=topology_path,
                topology_snapshot=transition["context"]["snapshot"],
                runtime_update_plan=transition["context"]["runtime_update_plan"],
                distribution_batch=transition["context"]["distribution_batch"],
                require_preview_validation=True,
                host_runtime_summary=host_apply["boundary"],
                max_node_workers=max_node_workers,
                runtime_mode=runtime_mode,
                warmup_ut_ids=_warmup_ut_ids_for_transition(transition),
            )
            applied_sessions.append(active_session)
            post_transition_connectivity = _connectivity_summary(active_session.ping_results)
            packet_observation_started_at = time.perf_counter()
            packet_observation = real_installer.observe_packet_path(
                active_session,
                observe_selectors=observe_selectors,
            )
            packet_observation_wall_seconds = time.perf_counter() - packet_observation_started_at
            packet_observation_completed_epoch_seconds = time.time()
            session_public_dict_started_at = time.perf_counter()
            node_local_execution_public = active_session.to_public_dict()
            node_local_execution_public_wall_seconds = (
                time.perf_counter() - session_public_dict_started_at
            )
            transition_record_started_at = time.perf_counter()
            transition_wall_seconds = time.perf_counter() - transition_started_at
            transition_completed_epoch_seconds = time.time()
            _emit_progress(
                (
                    "[iter2] completed "
                    f"{transition['to_slice_id']} "
                    f"host={host_apply_wall_seconds:.3f}s "
                    f"node_apply={float(active_session.timing.get('apply_wall_seconds', 0.0)):.3f}s "
                    f"ping={float(active_session.timing.get('ping_wall_seconds', 0.0)):.3f}s "
                    f"ok={post_transition_connectivity['ping_success']} "
                    "paths="
                    + "; ".join(
                        path.get("path_string", "")
                        for path in active_session.timing.get("flow_path_observations", [])
                    )
                ),
                enabled=stream_progress,
            )

            pacing_applied = 0.0
            if float(pacing_interval_seconds) > 0.0 and transition != transition_sequence[-1]:
                _emit_progress(
                    f"[iter2] pacing sleep {float(pacing_interval_seconds):.3f}s",
                    enabled=stream_progress,
                )
                sleep_started_at = time.perf_counter()
                time.sleep(float(pacing_interval_seconds))
                pacing_applied = time.perf_counter() - sleep_started_at

            transition_iterations.append(
                {
                    "sequence_index": transition["sequence_index"],
                    "loop_iteration": transition["loop_iteration"],
                    "slice_index": transition["slice_index"],
                    "from_slice_id": transition["from_slice_id"],
                    "to_slice_id": transition["to_slice_id"],
                    "simulated_time_seconds": transition["simulated_time_seconds"],
                    "simulated_delta_seconds": transition["simulated_delta_seconds"],
                    "target_gateway_choices": transition["context"]["slice_report"][
                        "gateway_choice_summary"
                    ],
                    "target_flow_paths": transition["context"]["slice_report"][
                        "flow_path_summaries"
                    ],
                    "target_multi_hop_status": transition["context"]["slice_report"][
                        "multi_hop_status"
                    ],
                    "pre_transition_connectivity": pre_transition_connectivity,
                    "post_transition_connectivity": post_transition_connectivity,
                    "pre_transition_paths": pre_transition_paths,
                    "post_transition_paths": list(
                        active_session.timing.get("flow_path_observations", [])
                    ),
                    "packet_observation": packet_observation,
                    "host_mutation_execution": host_apply,
                    "host_operation_timing_summary": host_apply["outcome"].get(
                        "operation_timing_summary",
                        {},
                    ),
                    "host_typed_operation_metrics": host_apply["outcome"].get(
                        "typed_operation_metrics",
                        {},
                    ),
                    "node_local_rollback_before_transition": node_local_rollback,
                    "node_local_execution": node_local_execution_public,
                    "epoch_timestamps": {
                        "transition_started_epoch_seconds": transition_started_epoch_seconds,
                        "pre_transition_connectivity_probe_completed_epoch_seconds": (
                            pre_transition_probe_completed_epoch_seconds
                        ),
                        "host_mutation_completed_epoch_seconds": (
                            host_apply_completed_epoch_seconds
                        ),
                        "node_local_session_started_epoch_seconds": float(
                            active_session.timing.get("session_started_epoch_seconds", 0.0)
                        ),
                        "node_local_apply_completed_epoch_seconds": float(
                            active_session.timing.get("apply_completed_epoch_seconds", 0.0)
                        ),
                        "node_local_access_link_warmup_completed_epoch_seconds": float(
                            active_session.timing.get(
                                "access_link_warmup_completed_epoch_seconds",
                                0.0,
                            )
                        ),
                        "node_local_ping_completed_epoch_seconds": float(
                            active_session.timing.get("ping_completed_epoch_seconds", 0.0)
                        ),
                        "packet_observation_completed_epoch_seconds": (
                            packet_observation_completed_epoch_seconds
                        ),
                        "transition_completed_epoch_seconds": (
                            transition_completed_epoch_seconds
                        ),
                    },
                    "timing": {
                        "transition_wall_seconds": transition_wall_seconds,
                        "network_mutation_plan_wall_seconds": host_apply_wall_seconds,
                        "host_mutation_apply_wall_seconds": host_apply_wall_seconds,
                        "pre_transition_connectivity_probe_wall_seconds": (
                            pre_transition_probe_wall_seconds
                        ),
                        "node_local_apply_wall_seconds": float(
                            active_session.timing.get("apply_wall_seconds", 0.0)
                        ),
                        "node_local_verify_wall_seconds": float(
                            active_session.timing.get("verify_wall_seconds", 0.0)
                        ),
                        "node_local_access_link_warmup_wall_seconds": float(
                            active_session.timing.get("access_link_warmup_wall_seconds", 0.0)
                        ),
                        "node_local_ping_wall_seconds": float(
                            active_session.timing.get("ping_wall_seconds", 0.0)
                        ),
                        "node_local_preflight_wall_seconds": float(
                            active_session.timing.get("preflight_wall_seconds", 0.0)
                        ),
                        "packet_observation_wall_seconds": packet_observation_wall_seconds,
                        "node_local_execution_public_wall_seconds": (
                            node_local_execution_public_wall_seconds
                        ),
                        "node_local_rollback_before_transition_wall_seconds": (
                            float(node_local_rollback["timing"]["rollback_wall_seconds"])
                            if node_local_rollback is not None
                            else 0.0
                        ),
                        "pacing_sleep_wall_seconds": pacing_applied,
                    },
                    "outcome": {
                        "transition_success": post_transition_connectivity["ping_success"],
                        "failure_reason": None,
                    },
                }
            )
            transition_report_wall_seconds = time.perf_counter() - transition_record_started_at
            transition_iterations[-1]["timing"]["transition_report_wall_seconds"] = (
                transition_report_wall_seconds
            )
            transition_iterations[-1]["timing"]["transition_wall_seconds"] = (
                time.perf_counter() - transition_started_at
            )
    except Exception as exc:  # noqa: BLE001
        failure_reason = str(exc)
    finally:
        node_cleanup = None
        if runtime_mode == "correctness":
            if active_session is not None:
                node_cleanup = real_installer.rollback_session(
                    active_session,
                    max_node_workers=max_node_workers,
                )
        else:
            cleanup_reports: List[Dict[str, Any]] = []
            for session in reversed(applied_sessions):
                cleanup_reports.append(
                    real_installer.rollback_session(
                        session,
                        max_node_workers=max_node_workers,
                    )
                )
            if cleanup_reports:
                node_cleanup = {
                    "rollback_results": [
                        result
                        for report in cleanup_reports
                        for result in report["rollback_results"]
                    ],
                    "rollback_verification": {
                        "contexts": [
                            context
                            for report in cleanup_reports
                            for context in report["rollback_verification"]["contexts"]
                        ],
                        "cleanup_success": all(
                            report["rollback_verification"]["cleanup_success"]
                            for report in cleanup_reports
                        ),
                    },
                    "timing": {
                        "rollback_wall_seconds": sum(
                            float(report["timing"]["rollback_wall_seconds"])
                            for report in cleanup_reports
                        ),
                        "rollback_batch_timing": {
                            "batch_count": sum(
                                int(report["timing"]["rollback_batch_timing"]["batch_count"])
                                for report in cleanup_reports
                            ),
                            "total_wall_seconds": sum(
                                float(
                                    report["timing"]["rollback_batch_timing"][
                                        "total_wall_seconds"
                                    ]
                                )
                                for report in cleanup_reports
                            ),
                        },
                    },
                    "session_cleanup_count": len(cleanup_reports),
                }
        host_rollbacks: List[Dict[str, Any]] = []
        host_cleanup_success = True
        for host_entry in reversed(host_history):
            rollback_started_at = time.perf_counter()
            rollback_result = network_executor.rollback(
                topology_path=topology_path,
                link_state_path=link_state_path,
                network_mutation_plan=host_entry["host_plan"],
                applied_operations=host_entry["applied_operations"],
            ).to_dict()
            rollback_result["timing"] = {
                "rollback_wall_seconds": time.perf_counter() - rollback_started_at,
            }
            host_rollbacks.append(rollback_result)
            if rollback_result["outcome"]["rollback_completed"] is not True:
                host_cleanup_success = False
        final_cleanup = {
            "node_local": node_cleanup,
            "host_rollbacks": host_rollbacks,
            "all_cleanup_success": (
                (node_cleanup is None or node_cleanup["rollback_verification"]["cleanup_success"])
                and host_cleanup_success
            ),
        }

    return {
        "mode": "continuous_multi_slice_real_execution",
        "loop_count": max(1, loop_count),
        "pacing_interval_seconds": float(pacing_interval_seconds),
        "runtime_mode": runtime_mode,
        "initial_runtime_readiness": initial_runtime_readiness,
        "iterations": transition_iterations,
        "timing_summary": _continuous_timing_summary(transition_iterations),
        "final_cleanup": final_cleanup,
        "outcome": {
            "real_apply_success": bool(transition_iterations)
            and all(
                iteration["outcome"]["transition_success"]
                for iteration in transition_iterations
            ),
            "ping_success": bool(transition_iterations)
            and all(
                iteration["post_transition_connectivity"]["ping_success"]
                for iteration in transition_iterations
            ),
            "rollback_completed": bool(final_cleanup.get("all_cleanup_success")),
            "failure_reason": failure_reason,
        },
    }


def build_experiment_report(
    *,
    topology_path: Path,
    link_state_path: Path,
    input_profile: str,
    scenario_name: str,
    installer_name: str,
    execute_real: bool,
    continuous_real: bool,
    target_slice_id: str,
    loop_count: int,
    pacing_interval_seconds: float,
    max_node_workers: int,
    max_host_workers: int,
    runtime_mode: str,
    observe_selectors: Sequence[str],
    stream_progress: bool,
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
    real_installer = MinimalRealLinuxMplsInstaller()

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
        ut_gateway_behavior = _ut_gateway_behavior(runtime_update_plan)
        gateway_choice_summary = _gateway_choice_summary(ut_gateway_behavior)
        flow_path_summaries = _flow_path_summaries(runtime_update_plan)
        realization_support = real_installer.assess_realization(
            runtime_update_plan=runtime_update_plan,
        )
        targets_reuse_seed_inventory = _targets_reuse_seed_inventory(snapshot)
        all_valid = (
            topology_validation.is_valid
            and plan_validation.is_valid
            and mutation_validation.is_valid
            and planner_flow_validation.is_valid
            and node_reports_valid
            and planner_output_generic
            and projection_backend_neutral
            and ut_side_state_lightweight
            and targets_reuse_seed_inventory
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
            "mini_internet_targets": list(
                snapshot.planner_hints.get("internet_validation_targets", [])
            ),
            "mini_internet_integration": dict(
                snapshot.planner_hints.get("mini_internet_integration", {})
            ),
            "gateway_ix_catalog": dict(snapshot.planner_hints.get("gateway_ix_catalog", {})),
            "gateway_nat_semantics": _gateway_nat_semantics(snapshot),
            "ut_gateway_behavior": ut_gateway_behavior,
            "gateway_choice_summary": gateway_choice_summary,
            "flow_path_summaries": flow_path_summaries,
            "multi_hop_status": {
                "multi_hop_flow_count": realization_support["multi_hop_flow_count"],
                "direct_flow_count": realization_support["direct_flow_count"],
                "multi_hop_flow_ids": list(realization_support["multi_hop_flow_ids"]),
                "present": realization_support["multi_hop_flow_count"] > 0,
            },
            "real_execution_support": realization_support,
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
                "targets_reuse_seed_inventory": targets_reuse_seed_inventory,
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
                    previous_flow_paths=slice_contexts[-1]["slice_report"]["flow_path_summaries"],
                    current_flow_paths=flow_path_summaries,
                )
            )

        previous_snapshot = snapshot
        current_slot_state = dynamic_slot_state_from_snapshot(snapshot)
        current_state_id = snapshot.snapshot_id

    overall_valid = all(
        slice_report["validation"]["all_valid"] for slice_report in slice_reports
    )
    loop_iterations = [
        {
            "iteration": iteration_index,
            "slice_sequence": [
                {
                    "slice_id": slice_report["time_slice_id"],
                    "real_execution_supported": slice_report["real_execution_support"][
                        "supported"
                    ],
                    "multi_hop_present": slice_report["multi_hop_status"]["present"],
                    "mutation_count": slice_report["network_mutation_summary"][
                        "operation_count"
                    ],
                    "gateway_choices": slice_report["gateway_choice_summary"],
                }
                for slice_report in slice_reports
            ],
        }
        for iteration_index in range(1, max(1, loop_count) + 1)
    ]
    real_execution = None
    if execute_real:
        if continuous_real:
            if not all(
                context["slice_report"]["validation"]["all_valid"] is True
                for context in slice_contexts
            ):
                raise RuntimeError("continuous real execution requires all slices to be preview-valid")
            if not all(
                context["slice_report"]["real_execution_support"]["supported"] is True
                for context in slice_contexts
            ):
                unsupported = [
                    context["snapshot"].time_slice.get("slice_id")
                    for context in slice_contexts
                    if context["slice_report"]["real_execution_support"]["supported"] is not True
                ]
                raise RuntimeError(
                    "continuous real execution requires every slice to support real execution: "
                    + ", ".join(str(slice_id) for slice_id in unsupported)
                )
            real_execution = _run_continuous_real_execution(
                topology_path=topology_path,
                link_state_path=link_state_path,
                slice_contexts=slice_contexts,
                loop_count=loop_count,
                pacing_interval_seconds=pacing_interval_seconds,
                max_node_workers=max_node_workers,
                max_host_workers=max_host_workers,
                runtime_mode=runtime_mode,
                observe_selectors=observe_selectors,
                stream_progress=stream_progress,
            )
        else:
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
            if target_context["slice_report"]["real_execution_support"]["supported"] is not True:
                raise RuntimeError(
                    "target slice does not support minimal real execution: "
                    + ", ".join(
                        target_context["slice_report"]["real_execution_support"]["blockers"]
                    )
                )
            network_executor = MinimalRealNetworkMutationExecutor(
                max_host_workers=max_host_workers
            )
            real_iterations: List[Dict[str, Any]] = []
            for iteration_index in range(1, max(1, loop_count) + 1):
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
                    node_local_execution = real_installer.execute(
                        topology_path=topology_path,
                        topology_snapshot=target_context["snapshot"],
                        runtime_update_plan=target_context["runtime_update_plan"],
                        distribution_batch=target_context["distribution_batch"],
                        require_preview_validation=True,
                        host_runtime_summary=host_mutation_apply["boundary"],
                        max_node_workers=max_node_workers,
                        runtime_mode=runtime_mode,
                    ).to_dict()
                finally:
                    host_mutation_rollback = network_executor.rollback(
                        topology_path=topology_path,
                        link_state_path=link_state_path,
                        network_mutation_plan=target_context["baseline_network_mutation_plan"],
                        applied_operations=host_mutation_apply["outcome"].get(
                            "applied_operations",
                            [],
                        ),
                    ).to_dict()
                if host_mutation_rollback["outcome"]["rollback_completed"] is not True:
                    raise RuntimeError(
                        f"host-side network mutation rollback failed for target slice {target_slice_id}"
                    )
                real_iterations.append(
                    {
                        "iteration": iteration_index,
                        "target_slice_id": target_slice_id,
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
                )
            real_execution = {
                "mode": "loopable_target_slice_real_execution",
                "loop_count": max(1, loop_count),
                "iterations": real_iterations,
                "outcome": {
                    "real_apply_success": all(
                        iteration["outcome"]["real_apply_success"]
                        for iteration in real_iterations
                    ),
                    "ping_success": all(
                        iteration["outcome"]["ping_success"] for iteration in real_iterations
                    ),
                    "rollback_completed": all(
                        iteration["outcome"]["rollback_completed"]
                        for iteration in real_iterations
                    ),
                    "failure_reason": next(
                        (
                            iteration["outcome"]["failure_reason"]
                            for iteration in real_iterations
                            if iteration["outcome"]["failure_reason"]
                        ),
                        None,
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
            "runtime_architecture_direction": "distribution_to_node_local_batched_installer_transition",
            "loop_mode": {
                "loop_count": max(1, loop_count),
                "default_is_preview_only": not execute_real,
                "real_execution_is_opt_in": True,
                "continuous_real_available": True,
                "continuous_real_enabled": bool(execute_real and continuous_real),
                "target_slice_id": target_slice_id if execute_real else None,
                "max_node_workers": max(1, max_node_workers),
                "max_host_workers": max(1, max_host_workers),
                "pacing_interval_seconds": float(pacing_interval_seconds),
                "runtime_mode": runtime_mode,
                "observe_selectors": list(observe_selectors),
            },
            "mini_internet_targets_discovered": list(
                snapshots[0].planner_hints.get("internet_validation_targets", [])
            )
            if snapshots
            else [],
            "mini_internet_integration": dict(
                snapshots[0].planner_hints.get("mini_internet_integration", {})
            )
            if snapshots
            else {},
            "gateway_ix_catalog": dict(snapshots[0].planner_hints.get("gateway_ix_catalog", {}))
            if snapshots
            else {},
        },
        "pipeline": {
            **pipeline.metadata(),
            "planner": planner.metadata(),
            "projector": projector.metadata(),
            "envelope_builder": envelope_builder.metadata(),
            "distributor": distributor.metadata(),
            "installer": installer.metadata(),
            "network_mutation_boundary": (
                "host_runtime_envelope -> in_process_host_runtime_manager -> "
                "direct_netns_veth_slot_link_namespace_qdisc_installer"
            ),
        },
        "slices": slice_reports,
        "transitions": transition_reports,
        "loop_iterations": loop_iterations,
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
    loop_mode = report["experiment"]["loop_mode"]
    lines.append(
        "Loop mode: "
        f"iterations={loop_mode['loop_count']} "
        f"preview_default={loop_mode['default_is_preview_only']} "
        f"real_opt_in={loop_mode['real_execution_is_opt_in']} "
        f"continuous_real={loop_mode['continuous_real_enabled']} "
        f"max_node_workers={loop_mode['max_node_workers']} "
        f"max_host_workers={loop_mode['max_host_workers']} "
        f"interval={loop_mode['pacing_interval_seconds']}s "
        f"runtime_mode={loop_mode['runtime_mode']}"
    )
    if loop_mode.get("observe_selectors"):
        lines.append(
            "Observation selectors: " + ", ".join(loop_mode["observe_selectors"])
        )
    lines.append(f"All slices valid: {report['validation']['all_valid']}")
    if report["experiment"]["mini_internet_targets_discovered"]:
        lines.append(
            "Mini Internet targets: "
            + ", ".join(
                f"{target['node_id']}={target['ip_address']}"
                for target in report["experiment"]["mini_internet_targets_discovered"]
            )
        )
    if report["experiment"]["gateway_ix_catalog"]:
        lines.append(
            "Gateway/IX diversity: "
            + ", ".join(
                f"{gateway_id}->{gateway_info.get('ix_peering', {}).get('ix_id')}"
                for gateway_id, gateway_info in sorted(
                    report["experiment"]["gateway_ix_catalog"].items()
                )
            )
        )
    if report["experiment"]["mini_internet_integration"]:
        integration = report["experiment"]["mini_internet_integration"]
        lines.append(
            "Mini Internet integration: "
            f"source={integration.get('inventory_source')} "
            f"transit_as={integration.get('transit_as_count')} "
            f"stub_as={integration.get('stub_as_count')}"
        )
        lines.append(
            "Visualization note: "
            + str(integration.get("visualization_expectation"))
        )
    if report["real_execution"] is None:
        lines.append("Real connectivity: not attempted; validated execution preview only")
    else:
        outcome = report["real_execution"]["outcome"]
        lines.append(
            "Real execution: "
            f"apply_success={outcome['real_apply_success']} "
            f"rollback_completed={outcome['rollback_completed']} "
            f"ping_success={outcome['ping_success']}"
        )

    for slice_report in report["slices"]:
        lines.append(
            f"Slice {slice_report['time_slice_id']} "
            f"(t={slice_report['relative_time_seconds']}s)"
        )
        lines.append(
            "  Gateway choices: "
            + json.dumps(slice_report["gateway_choice_summary"], sort_keys=True)
        )
        lines.append(
            "  Multi-hop: "
            + json.dumps(slice_report["multi_hop_status"], sort_keys=True)
        )
        lines.append(
            "  Real support: "
            + json.dumps(slice_report["real_execution_support"], sort_keys=True)
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
        lines.append(
            "  NAT/egress semantics: "
            + json.dumps(slice_report["gateway_nat_semantics"], sort_keys=True)
        )
        lines.append(
            "  UT gateway behavior: "
            + json.dumps(slice_report["ut_gateway_behavior"], sort_keys=True)
        )
        lines.append(
            "  Flow paths: "
            + json.dumps(slice_report["flow_path_summaries"], sort_keys=True)
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
        if transition["flow_path_changes"]["changed_flow_count"] > 0:
            lines.append(
                "  Flow path changes: "
                + json.dumps(transition["flow_path_changes"], sort_keys=True)
            )
    for iteration in report.get("loop_iterations", []):
        lines.append(
            f"Loop iteration {iteration['iteration']}: "
            + " -> ".join(
                (
                    f"{slice_state['slice_id']}"
                    f"[multi_hop={slice_state['multi_hop_present']},"
                    f"real={slice_state['real_execution_supported']}]"
                )
                for slice_state in iteration["slice_sequence"]
            )
        )
    if report["real_execution"] is not None:
        if report["real_execution"]["mode"] == "continuous_multi_slice_real_execution":
            timing_summary = report["real_execution"].get("timing_summary", {})
            if timing_summary:
                lines.append(
                    "Continuous timing summary: "
                    f"avg_transition={timing_summary.get('average_transition_wall_seconds', 0.0):.3f}s "
                    f"max_transition={timing_summary.get('max_transition_wall_seconds', 0.0):.3f}s "
                    f"dominant_phase={timing_summary.get('dominant_phase_by_average')}"
                )
                lines.append(
                    "  Phase averages: "
                    + json.dumps(timing_summary.get("phase_averages", {}), sort_keys=True)
                )
                if timing_summary.get("host_operation_wall_seconds"):
                    lines.append(
                        "  Host op totals: "
                        + json.dumps(
                            timing_summary.get("host_operation_wall_seconds", {}),
                            sort_keys=True,
                        )
                    )
                if timing_summary.get("host_delta_totals"):
                    lines.append(
                        "  Host delta totals: "
                        + json.dumps(
                            timing_summary.get("host_delta_totals", {}),
                            sort_keys=True,
                        )
                    )
            for iteration in report["real_execution"].get("iterations", []):
                node_exec = iteration["node_local_execution"]
                backend = node_exec["backend"]
                timing = iteration["timing"]
                lines.append(
                    f"Continuous transition {iteration['sequence_index']} "
                    f"{iteration['from_slice_id']} -> {iteration['to_slice_id']} "
                    f"(loop={iteration['loop_iteration']} sim_t={iteration['simulated_time_seconds']}s "
                    f"delta={iteration['simulated_delta_seconds']}s)"
                )
                lines.append(
                    "  Runtime timing: "
                    f"transition={timing['transition_wall_seconds']:.3f}s "
                    f"netmut={timing['network_mutation_plan_wall_seconds']:.3f}s "
                    f"host={timing['host_mutation_apply_wall_seconds']:.3f}s "
                    f"node_apply={timing['node_local_apply_wall_seconds']:.3f}s "
                    f"node_verify={timing['node_local_verify_wall_seconds']:.3f}s "
                    f"ping={timing['node_local_ping_wall_seconds']:.3f}s "
                    f"node_rollback={timing['node_local_rollback_before_transition_wall_seconds']:.3f}s "
                    f"sleep={timing['pacing_sleep_wall_seconds']:.3f}s"
                )
                if iteration.get("host_operation_timing_summary"):
                    lines.append(
                        "  Host ops: "
                        + json.dumps(
                            iteration["host_operation_timing_summary"],
                            sort_keys=True,
                        )
                    )
                host_delta_summary = (
                    iteration.get("host_mutation_execution", {})
                    .get("outcome", {})
                    .get("delta_summary", {})
                )
                if host_delta_summary:
                    lines.append(
                        "  Host delta: "
                        + json.dumps(
                            {
                                key: host_delta_summary.get(key)
                                for key in (
                                    "skipped_slot_count",
                                    "reused_slot_count",
                                    "changed_slot_count",
                                    "created_slot_count",
                                    "deleted_slot_count",
                                )
                            },
                            sort_keys=True,
                        )
                    )
                lines.append(
                    "  Connectivity: "
                    f"before={None if iteration['pre_transition_connectivity'] is None else iteration['pre_transition_connectivity']['ping_success']} "
                    f"after={iteration['post_transition_connectivity']['ping_success']}"
                )
                lines.append(
                    "  Paths: "
                    + "; ".join(
                        path.get("path_string", "")
                        for path in iteration["post_transition_paths"]
                    )
                )
                if iteration["packet_observation"]["enabled"]:
                    lines.append(
                        "  Packet observation: "
                        + json.dumps(iteration["packet_observation"], sort_keys=True)
                    )
                lines.append(
                    "  Transport/concurrency: "
                    f"{backend['execution_transport']} / {backend['concurrency_model']} "
                    f"workers={backend.get('max_node_workers')}"
                )
            cleanup = report["real_execution"]["final_cleanup"]
            node_cleanup = cleanup.get("node_local") or {}
            lines.append(
                "Continuous cleanup: "
                f"host_rollbacks={len(cleanup.get('host_rollbacks', []))} "
                f"node_cleanup={node_cleanup.get('rollback_verification', {}).get('cleanup_success')} "
                f"all_cleanup_success={cleanup.get('all_cleanup_success')}"
            )
        else:
            for iteration in report["real_execution"].get("iterations", []):
                ping_result = iteration["node_local_execution"]["ping_result"]
                flow_results = iteration["node_local_execution"]["outcome"].get(
                    "flow_results",
                    [],
                )
                backend = iteration["node_local_execution"]["backend"]
                lines.append(
                    f"Real iteration {iteration['iteration']} "
                    f"(target={iteration['target_slice_id']}): "
                    f"transport={backend['execution_transport']} "
                    f"concurrency={backend['concurrency_model']} "
                    f"workers={backend.get('max_node_workers')}"
                )
                lines.append(
                    "  Ping result: "
                    f"{ping_result['ut_id']} -> {ping_result['ping_target_ip']} "
                    f"({ping_result['target_host_node_id']}) success={ping_result['success']}"
                )
                if flow_results:
                    lines.append(
                        "  All validation pings: "
                        + ", ".join(
                            (
                                f"{flow_result['ut_id']}:{flow_result['target_host_node_id']}"
                                f"={flow_result['success']}"
                            )
                            for flow_result in flow_results
                        )
                    )
                lines.append(
                    "  Timing: "
                    + json.dumps(
                        iteration["node_local_execution"].get("timing", {}),
                        sort_keys=True,
                    )
                )
                lines.append(
                    "  Rollback verification: "
                    + json.dumps(
                        {
                            "host_mutation": iteration["host_mutation_execution"]["rollback"][
                                "rollback_verification"
                            ],
                            "node_local": iteration["node_local_execution"][
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
        continuous_real=bool(args.continuous_real),
        target_slice_id=str(args.target_slice_id),
        loop_count=max(1, int(args.loop_count)),
        pacing_interval_seconds=max(0.0, float(args.interval)),
        max_node_workers=max(1, int(args.max_node_workers)),
        max_host_workers=max(1, int(args.max_host_workers)),
        runtime_mode=str(args.runtime_mode),
        observe_selectors=list(args.observe_flow),
        stream_progress=bool(args.stream_progress),
    )
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(_render_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
