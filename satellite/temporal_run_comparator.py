#!/usr/bin/env python3
# encoding: utf-8

"""Compare summarized temporal run reports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Sequence

from temporal_run_inspector import inspect_temporal_run


def _metric(report: Dict[str, Any], family: str, metric: str, field: str) -> Any:
    return (
        dict(report.get("event_summary", {}))
        .get(family, {})
        .get(metric, {})
        .get(field)
    )


def compare_temporal_runs(
    run_dirs: Sequence[Path],
    *,
    stall_threshold_seconds: float,
) -> Dict[str, Any]:
    reports = [
        inspect_temporal_run(Path(run_dir), stall_threshold_seconds=stall_threshold_seconds)
        for run_dir in run_dirs
    ]
    comparisons: List[Dict[str, Any]] = []
    for report in reports:
        progress = dict(report.get("progress", {}))
        timing_analysis = dict(report.get("timing_analysis", {}))
        startup = dict(timing_analysis.get("startup_preparation", {}))
        initial_activation = dict(timing_analysis.get("initial_activation", {}))
        first_runtime = dict(timing_analysis.get("first_runtime_epoch", {}))
        steady_runtime = dict(timing_analysis.get("steady_state_runtime_epochs", {}))
        return_path_audit = dict(report.get("return_path_audit_summary", {}))
        zone_return_boundary = dict(
            report.get("runtime_zone_return_boundary_summary", {})
        )
        ping_validation = dict(report.get("runtime_ping_validation_summary", {}))
        incremental_reuse = dict(report.get("runtime_incremental_reuse_summary", {}))
        teardown_cleanup = dict(report.get("teardown_cleanup_summary", {}))
        comparisons.append(
            {
                "run_dir": report.get("run_dir"),
                "phase": progress.get("phase"),
                "return_path_audit_present": return_path_audit.get("present"),
                "return_path_flow_count": return_path_audit.get("total_flow_count"),
                "return_path_per_flow_state": return_path_audit.get(
                    "current_return_state_scales_with_flow_count"
                ),
                "return_path_shared_reduction_available": return_path_audit.get(
                    "shared_target_reduction_available"
                ),
                "return_path_backbone_state_ratio": return_path_audit.get(
                    "current_to_target_backbone_state_ratio"
                ),
                "return_path_shared_backbone_ready": return_path_audit.get(
                    "return_path_shared_backbone_ready"
                ),
                "return_path_backbone_migration_required": return_path_audit.get(
                    "return_path_backbone_migration_required"
                ),
                "return_path_backbone_blocker_items": return_path_audit.get(
                    "return_path_backbone_blocker_item_count"
                ),
                "return_path_migration_blocker_count": return_path_audit.get(
                    "return_path_migration_blocker_count"
                ),
                "return_path_top_migration_blocker": return_path_audit.get(
                    "return_path_top_migration_blocker"
                ),
                "return_path_top_backbone_migration_blocker": return_path_audit.get(
                    "return_path_top_backbone_migration_blocker"
                ),
                "return_path_backbone_return_items": return_path_audit.get(
                    "return_path_backbone_return_item_count"
                ),
                "return_path_gateway_edge_policy_items": return_path_audit.get(
                    "return_path_gateway_edge_policy_item_count"
                ),
                "return_path_edge_local_delivery_items": return_path_audit.get(
                    "return_path_edge_local_delivery_item_count"
                ),
                "return_path_projection_10000_current": return_path_audit.get(
                    "projection_10000_current_backbone_items"
                ),
                "return_path_projection_10000_ratio_constant": return_path_audit.get(
                    "projection_10000_ratio_constant"
                ),
                "selected_runtime_return_path_flow_count": return_path_audit.get(
                    "selected_runtime_total_flow_count"
                ),
                "selected_runtime_return_path_per_flow_state": return_path_audit.get(
                    "selected_runtime_per_flow_state"
                ),
                "selected_runtime_return_path_backbone_state_ratio": return_path_audit.get(
                    "selected_runtime_current_to_target_backbone_state_ratio"
                ),
                "selected_runtime_return_path_shared_backbone_ready": return_path_audit.get(
                    "selected_runtime_shared_backbone_ready"
                ),
                "selected_runtime_return_path_backbone_migration_required": return_path_audit.get(
                    "selected_runtime_backbone_migration_required"
                ),
                "selected_runtime_return_path_backbone_blocker_items": return_path_audit.get(
                    "selected_runtime_backbone_blocker_item_count"
                ),
                "selected_runtime_return_path_projection_10000_current": return_path_audit.get(
                    "selected_runtime_projection_10000_current_backbone_items"
                ),
                "selected_runtime_return_path_projection_10000_ratio_constant": return_path_audit.get(
                    "selected_runtime_projection_10000_ratio_constant"
                ),
                "runtime_zone_return_boundary_present": zone_return_boundary.get(
                    "present"
                ),
                "runtime_zone_return_boundary_records": zone_return_boundary.get(
                    "boundary_record_count"
                ),
                "runtime_zone_return_max_all_intents": zone_return_boundary.get(
                    "max_all_intent_count"
                ),
                "runtime_zone_return_max_reapplied_intents": zone_return_boundary.get(
                    "max_reapplied_intent_count"
                ),
                "runtime_zone_return_unique_targets": zone_return_boundary.get(
                    "unique_all_target_key_count"
                ),
                "runtime_zone_return_all_backbone_ratio": zone_return_boundary.get(
                    "max_all_current_to_target_backbone_state_ratio"
                ),
                "runtime_zone_return_all_candidate_zone_routes": zone_return_boundary.get(
                    "max_all_candidate_shared_zone_route_count"
                ),
                "runtime_zone_return_all_candidate_label_stacks": zone_return_boundary.get(
                    "max_all_candidate_shared_label_stack_count"
                ),
                "runtime_zone_return_all_reverse_to_candidate_ratio": zone_return_boundary.get(
                    "max_all_reverse_labels_to_candidate_label_stack_ratio"
                ),
                "runtime_zone_return_reapplied_backbone_ratio": zone_return_boundary.get(
                    "max_reapplied_current_to_target_backbone_state_ratio"
                ),
                "runtime_zone_return_reapplied_candidate_zone_routes": zone_return_boundary.get(
                    "max_reapplied_candidate_shared_zone_route_count"
                ),
                "runtime_zone_return_reapplied_candidate_label_stacks": zone_return_boundary.get(
                    "max_reapplied_candidate_shared_label_stack_count"
                ),
                "runtime_zone_return_reapplied_reverse_to_candidate_ratio": zone_return_boundary.get(
                    "max_reapplied_reverse_labels_to_candidate_label_stack_ratio"
                ),
                "runtime_ping_validation_present": ping_validation.get("present"),
                "runtime_ping_validation_records": ping_validation.get(
                    "selection_record_count"
                ),
                "runtime_ping_validation_selected_max": ping_validation.get(
                    "max_selected_flow_count"
                ),
                "runtime_ping_validation_reduction_max": ping_validation.get(
                    "max_gateway_class_sample_reduction_count"
                ),
                "runtime_incremental_reuse_present": incremental_reuse.get("present"),
                "runtime_incremental_reuse_records": incremental_reuse.get(
                    "slice_record_count"
                ),
                "runtime_incremental_reuse_reapplied_max": incremental_reuse.get(
                    "max_reapplied_flow_count"
                ),
                "runtime_incremental_reuse_held_max": incremental_reuse.get(
                    "max_held_flow_count"
                ),
                "runtime_incremental_reuse_reduction_max": incremental_reuse.get(
                    "max_reapply_reduction_count"
                ),
                "runtime_incremental_reuse_reapplied_ratio_min": incremental_reuse.get(
                    "min_reapplied_to_active_flow_ratio"
                ),
                "teardown_cleanup_present": teardown_cleanup.get("present"),
                "teardown_cleanup_success": teardown_cleanup.get(
                    "all_cleanup_success"
                ),
                "teardown_cleanup_known_wall_seconds": teardown_cleanup.get(
                    "known_teardown_cleanup_wall_seconds"
                ),
                "teardown_cleanup_wall_seconds": teardown_cleanup.get(
                    "teardown_cleanup_wall_seconds"
                ),
                "teardown_cleanup_wall_time_complete": teardown_cleanup.get(
                    "teardown_cleanup_wall_time_complete"
                ),
                "teardown_cleanup_node_local_rollback_seconds": teardown_cleanup.get(
                    "node_local_rollback_wall_seconds"
                ),
                "teardown_cleanup_host_rollback_seconds": teardown_cleanup.get(
                    "host_rollback_wall_seconds"
                ),
                "teardown_cleanup_host_rollback_count": teardown_cleanup.get(
                    "host_rollback_count"
                ),
                "teardown_cleanup_host_artifact_cleanup_seconds": teardown_cleanup.get(
                    "host_artifact_cleanup_wall_seconds"
                ),
                "teardown_cleanup_post_run_preflight_seconds": teardown_cleanup.get(
                    "post_run_preflight_wall_seconds"
                ),
                "startup_cold_start_before_first_epoch_seconds": startup.get(
                    "cold_start_before_first_epoch_execution_wall_seconds"
                ),
                "startup_ready_to_execute_epochs_seconds": startup.get(
                    "ready_to_execute_epochs_wall_seconds"
                ),
                "initial_activation_wall_seconds": initial_activation.get(
                    "initial_activation_wall_seconds"
                ),
                "initial_activation_cold_start_seconds": initial_activation.get(
                    "cold_start_before_first_epoch_execution_wall_seconds"
                ),
                "initial_activation_first_runtime_execution_seconds": initial_activation.get(
                    "first_runtime_epoch_execution_after_prepare_reconcile_wall_seconds"
                ),
                "initial_activation_steady_excludes_initial": initial_activation.get(
                    "steady_runtime_excludes_initial_activation"
                ),
                "first_runtime_epoch_wall_seconds": first_runtime.get(
                    "transition_wall_seconds"
                ),
                "first_runtime_epoch_network_mutation_seconds": first_runtime.get(
                    "network_mutation_plan_wall_seconds"
                ),
                "first_runtime_epoch_node_local_apply_seconds": first_runtime.get(
                    "node_local_apply_wall_seconds"
                ),
                "runtime_epoch_count": _metric(
                    report,
                    "runtime_epoch_results",
                    "activation_lag_seconds",
                    "count",
                ),
                "runtime_epoch_lag_avg": _metric(
                    report,
                    "runtime_epoch_results",
                    "activation_lag_seconds",
                    "average",
                ),
                "runtime_epoch_wall_avg": _metric(
                    report,
                    "runtime_epoch_results",
                    "transition_wall_seconds",
                    "average",
                ),
                "runtime_epoch_host_apply_avg": _metric(
                    report,
                    "runtime_epoch_results",
                    "host_operation_apply_wall_seconds",
                    "average",
                ),
                "runtime_epoch_post_refresh_avg": _metric(
                    report,
                    "runtime_epoch_results",
                    "post_apply_state_refresh_wall_seconds",
                    "average",
                ),
                "steady_runtime_epoch_count": steady_runtime.get("count"),
                "steady_runtime_source_epoch_count": steady_runtime.get(
                    "source_runtime_epoch_count"
                ),
                "steady_runtime_excluded_initial_epoch_count": steady_runtime.get(
                    "excluded_initial_runtime_epoch_count"
                ),
                "steady_runtime_epoch_wall_avg": steady_runtime.get(
                    "average_transition_wall_seconds"
                ),
                "steady_runtime_epoch_host_apply_avg": steady_runtime.get(
                    "average_host_mutation_apply_wall_seconds"
                ),
                "parameter_epoch_count": _metric(
                    report,
                    "parameter_epoch_results",
                    "activation_lag_seconds",
                    "count",
                ),
                "parameter_epoch_lag_avg": _metric(
                    report,
                    "parameter_epoch_results",
                    "activation_lag_seconds",
                    "average",
                ),
                "parameter_epoch_wall_avg": _metric(
                    report,
                    "parameter_epoch_results",
                    "transition_wall_seconds",
                    "average",
                ),
                "parameter_epoch_host_apply_avg": _metric(
                    report,
                    "parameter_epoch_results",
                    "host_operation_apply_wall_seconds",
                    "average",
                ),
                "parameter_epoch_post_refresh_avg": _metric(
                    report,
                    "parameter_epoch_results",
                    "post_apply_state_refresh_wall_seconds",
                    "average",
                ),
                "parameter_epoch_noop_count": _metric(
                    report,
                    "parameter_epoch_noop_skipped",
                    "activation_lag_seconds",
                    "count",
                ),
                "parameter_epoch_noop_wall_avg": _metric(
                    report,
                    "parameter_epoch_noop_skipped",
                    "transition_wall_seconds",
                    "average",
                ),
                "parameter_epoch_noop_host_apply_avg": _metric(
                    report,
                    "parameter_epoch_noop_skipped",
                    "host_operation_apply_wall_seconds",
                    "average",
                ),
                "live_runtime_complete_inferred": dict(report.get("inference", {})).get(
                    "live_runtime_complete_inferred"
                ),
                "final_artifacts_complete": dict(report.get("inference", {})).get(
                    "final_artifacts_complete"
                ),
                "metric_source": dict(report.get("event_summary", {})).get("metric_source"),
            }
        )
    return {
        "run_count": len(comparisons),
        "runs": comparisons,
    }


def render_temporal_run_comparison_json(report: Dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True)


def render_temporal_run_comparison_summary(report: Dict[str, Any]) -> str:
    lines = []
    for run in list(report.get("runs", [])):
        lines.append(
            " | ".join(
                [
                    str(run.get("run_dir")),
                    f"phase={run.get('phase')}",
                    f"metric_source={run.get('metric_source')}",
                    f"return_path_audit_present={run.get('return_path_audit_present')}",
                    f"return_path_flows={run.get('return_path_flow_count')}",
                    f"return_path_per_flow_state={run.get('return_path_per_flow_state')}",
                    f"return_path_shared_reduction={run.get('return_path_shared_reduction_available')}",
                    f"return_path_backbone_ratio={run.get('return_path_backbone_state_ratio')}",
                    f"return_path_shared_backbone_ready={run.get('return_path_shared_backbone_ready')}",
                    f"return_path_backbone_migration_required={run.get('return_path_backbone_migration_required')}",
                    f"return_path_backbone_blocker_items={run.get('return_path_backbone_blocker_items')}",
                    f"return_path_migration_blockers={run.get('return_path_migration_blocker_count')}",
                    f"return_path_top_migration_blocker={run.get('return_path_top_migration_blocker')}",
                    f"return_path_top_backbone_migration_blocker={run.get('return_path_top_backbone_migration_blocker')}",
                    f"return_path_backbone_return_items={run.get('return_path_backbone_return_items')}",
                    f"return_path_gateway_edge_policy_items={run.get('return_path_gateway_edge_policy_items')}",
                    f"return_path_edge_local_delivery_items={run.get('return_path_edge_local_delivery_items')}",
                    f"return_path_projection_10000_current={run.get('return_path_projection_10000_current')}",
                    f"return_path_projection_10000_ratio_constant={run.get('return_path_projection_10000_ratio_constant')}",
                    f"selected_runtime_return_path_flows={run.get('selected_runtime_return_path_flow_count')}",
                    f"selected_runtime_return_path_per_flow_state={run.get('selected_runtime_return_path_per_flow_state')}",
                    f"selected_runtime_return_path_backbone_ratio={run.get('selected_runtime_return_path_backbone_state_ratio')}",
                    f"selected_runtime_return_path_shared_backbone_ready={run.get('selected_runtime_return_path_shared_backbone_ready')}",
                    f"selected_runtime_return_path_backbone_migration_required={run.get('selected_runtime_return_path_backbone_migration_required')}",
                    f"selected_runtime_return_path_backbone_blocker_items={run.get('selected_runtime_return_path_backbone_blocker_items')}",
                    f"selected_runtime_return_path_projection_10000_current={run.get('selected_runtime_return_path_projection_10000_current')}",
                    f"selected_runtime_return_path_projection_10000_ratio_constant={run.get('selected_runtime_return_path_projection_10000_ratio_constant')}",
                    f"runtime_zone_return_boundary_present={run.get('runtime_zone_return_boundary_present')}",
                    f"runtime_zone_return_records={run.get('runtime_zone_return_boundary_records')}",
                    f"runtime_zone_return_max_all_intents={run.get('runtime_zone_return_max_all_intents')}",
                    f"runtime_zone_return_max_reapplied_intents={run.get('runtime_zone_return_max_reapplied_intents')}",
                    f"runtime_zone_return_unique_targets={run.get('runtime_zone_return_unique_targets')}",
                    f"runtime_zone_return_all_backbone_ratio={run.get('runtime_zone_return_all_backbone_ratio')}",
                    f"runtime_zone_return_all_candidate_zone_routes={run.get('runtime_zone_return_all_candidate_zone_routes')}",
                    f"runtime_zone_return_all_candidate_label_stacks={run.get('runtime_zone_return_all_candidate_label_stacks')}",
                    f"runtime_zone_return_all_reverse_to_candidate_ratio={run.get('runtime_zone_return_all_reverse_to_candidate_ratio')}",
                    f"runtime_zone_return_reapplied_backbone_ratio={run.get('runtime_zone_return_reapplied_backbone_ratio')}",
                    f"runtime_zone_return_reapplied_candidate_zone_routes={run.get('runtime_zone_return_reapplied_candidate_zone_routes')}",
                    f"runtime_zone_return_reapplied_candidate_label_stacks={run.get('runtime_zone_return_reapplied_candidate_label_stacks')}",
                    f"runtime_zone_return_reapplied_reverse_to_candidate_ratio={run.get('runtime_zone_return_reapplied_reverse_to_candidate_ratio')}",
                    f"runtime_ping_validation_present={run.get('runtime_ping_validation_present')}",
                    f"runtime_ping_validation_records={run.get('runtime_ping_validation_records')}",
                    f"runtime_ping_validation_selected_max={run.get('runtime_ping_validation_selected_max')}",
                    f"runtime_ping_validation_reduction_max={run.get('runtime_ping_validation_reduction_max')}",
                    f"runtime_incremental_reuse_present={run.get('runtime_incremental_reuse_present')}",
                    f"runtime_incremental_reuse_records={run.get('runtime_incremental_reuse_records')}",
                    f"runtime_incremental_reuse_reapplied_max={run.get('runtime_incremental_reuse_reapplied_max')}",
                    f"runtime_incremental_reuse_held_max={run.get('runtime_incremental_reuse_held_max')}",
                    f"runtime_incremental_reuse_reduction_max={run.get('runtime_incremental_reuse_reduction_max')}",
                    f"runtime_incremental_reuse_reapplied_ratio_min={run.get('runtime_incremental_reuse_reapplied_ratio_min')}",
                    f"teardown_cleanup_present={run.get('teardown_cleanup_present')}",
                    f"teardown_cleanup_success={run.get('teardown_cleanup_success')}",
                    f"teardown_cleanup_known_wall={run.get('teardown_cleanup_known_wall_seconds')}",
                    f"teardown_cleanup_wall={run.get('teardown_cleanup_wall_seconds')}",
                    f"teardown_cleanup_wall_complete={run.get('teardown_cleanup_wall_time_complete')}",
                    f"teardown_cleanup_node_local_rollback={run.get('teardown_cleanup_node_local_rollback_seconds')}",
                    f"teardown_cleanup_host_rollback={run.get('teardown_cleanup_host_rollback_seconds')}",
                    f"teardown_cleanup_host_rollback_count={run.get('teardown_cleanup_host_rollback_count')}",
                    f"teardown_cleanup_host_artifact_cleanup={run.get('teardown_cleanup_host_artifact_cleanup_seconds')}",
                    f"teardown_cleanup_post_run_preflight={run.get('teardown_cleanup_post_run_preflight_seconds')}",
                    f"startup_cold_before_first_epoch={run.get('startup_cold_start_before_first_epoch_seconds')}",
                    f"startup_ready_to_execute={run.get('startup_ready_to_execute_epochs_seconds')}",
                    f"initial_activation_wall={run.get('initial_activation_wall_seconds')}",
                    f"initial_activation_cold_start={run.get('initial_activation_cold_start_seconds')}",
                    f"initial_activation_first_runtime_execution={run.get('initial_activation_first_runtime_execution_seconds')}",
                    f"initial_activation_steady_excludes_initial={run.get('initial_activation_steady_excludes_initial')}",
                    f"first_runtime_wall={run.get('first_runtime_epoch_wall_seconds')}",
                    f"first_runtime_network_mutation={run.get('first_runtime_epoch_network_mutation_seconds')}",
                    f"first_runtime_node_local_apply={run.get('first_runtime_epoch_node_local_apply_seconds')}",
                    f"runtime_count={run.get('runtime_epoch_count')}",
                    f"runtime_lag_avg={run.get('runtime_epoch_lag_avg')}",
                    f"runtime_wall_avg={run.get('runtime_epoch_wall_avg')}",
                    f"runtime_host_apply_avg={run.get('runtime_epoch_host_apply_avg')}",
                    f"runtime_post_refresh_avg={run.get('runtime_epoch_post_refresh_avg')}",
                    f"steady_runtime_source_count={run.get('steady_runtime_source_epoch_count')}",
                    f"steady_runtime_excluded_initial={run.get('steady_runtime_excluded_initial_epoch_count')}",
                    f"steady_runtime_count={run.get('steady_runtime_epoch_count')}",
                    f"steady_runtime_wall_avg={run.get('steady_runtime_epoch_wall_avg')}",
                    f"steady_runtime_host_apply_avg={run.get('steady_runtime_epoch_host_apply_avg')}",
                    f"parameter_count={run.get('parameter_epoch_count')}",
                    f"parameter_lag_avg={run.get('parameter_epoch_lag_avg')}",
                    f"parameter_wall_avg={run.get('parameter_epoch_wall_avg')}",
                    f"parameter_host_apply_avg={run.get('parameter_epoch_host_apply_avg')}",
                    f"parameter_post_refresh_avg={run.get('parameter_epoch_post_refresh_avg')}",
                    f"noop_count={run.get('parameter_epoch_noop_count')}",
                    f"noop_wall_avg={run.get('parameter_epoch_noop_wall_avg')}",
                    f"noop_host_apply_avg={run.get('parameter_epoch_noop_host_apply_avg')}",
                    f"live_complete={run.get('live_runtime_complete_inferred')}",
                    f"final_artifacts_complete={run.get('final_artifacts_complete')}",
                ]
            )
        )
    return "\n".join(lines)
