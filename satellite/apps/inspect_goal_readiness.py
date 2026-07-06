#!/usr/bin/env python3
# encoding: utf-8

"""Summarize the current 1-shell G/Z optimization readiness gates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Sequence

from _path_setup import bootstrap_app_import_path

SATELLITE_DIR = bootstrap_app_import_path()
DEFAULT_RUN_ROOT = SATELLITE_DIR / "output" / "starlink_shell_experiments"
DEFAULT_NEAR_RUN = DEFAULT_RUN_ROOT / "goal-1shell-172-preview-distance-near6-catalog"
DEFAULT_MIXED_RUN = DEFAULT_RUN_ROOT / "goal-1shell-172-preview-distance-mixed6-catalog"
DEFAULT_FAR_RUN = DEFAULT_RUN_ROOT / "goal-1shell-172-preview-distance-far12-catalog"
DEFAULT_RETURN_AUDIT_RUN = (
    DEFAULT_RUN_ROOT / "goal-1shell-172-preview-return-audit-selected-smoke"
)
DEFAULT_BASELINE_REAL_RUN = DEFAULT_RUN_ROOT / "goal-1shell-172-z6valid6-0400-real1"
EXPECTED_RUNTIME_VALIDATION_SCOPE = "reapplied_gateway_class_sample"

from inspect_starlink_distance_matrix import summarize_distance_matrix_run  # noqa: E402
from linux_mpls_runtime import DEFAULT_RUNTIME_EPOCH_VALIDATION_SCOPE  # noqa: E402
from return_path_audit import audit_planner_preview_path  # noqa: E402
from return_path_install_audit import summarize_return_path_install_points  # noqa: E402
from skyseed_preflight import run_skyseed_preflight  # noqa: E402
from temporal_run_inspector import inspect_temporal_run  # noqa: E402


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.expanduser().read_text(encoding="utf-8"))


def _bool_or_none(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _compact_distance_run(run: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "run_id": run.get("run_id"),
        "artifact_ready": run.get("artifact_ready", True),
        "artifact_error": run.get("artifact_error"),
        "artifact_path": run.get("artifact_path"),
        "runtime_snapshot_count": run.get("runtime_snapshot_count"),
        "selected_ut_count": run.get("selected_ut_count"),
        "covered_runtime_ut_count": run.get("covered_runtime_ut_count"),
        "isl_hop_count_avg": run.get("isl_hop_count_avg"),
        "isl_hop_count_max": run.get("isl_hop_count_max"),
        "runtime_update_action_count_avg": run.get(
            "runtime_update_action_count_avg"
        ),
        "runtime_update_action_count_max": run.get(
            "runtime_update_action_count_max"
        ),
        "node_subplan_count_avg": run.get("node_subplan_count_avg"),
        "node_subplan_count_max": run.get("node_subplan_count_max"),
        "selected_gateway_counts": run.get("selected_gateway_counts"),
        "gateway_selection_modes": run.get("gateway_selection_modes"),
    }


def _summarize_distance_matrix_run_fail_closed(run_path: Path) -> Dict[str, Any]:
    try:
        run = summarize_distance_matrix_run(run_path)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        return {
            "run_id": Path(run_path).name,
            "artifact_ready": False,
            "artifact_error": f"{exc.__class__.__name__}:{exc}",
            "artifact_path": str(Path(run_path).expanduser()),
            "runtime_snapshot_count": 0,
            "covered_runtime_ut_count": 0,
            "isl_hop_count_avg": 0.0,
            "isl_hop_count_max": 0,
            "runtime_update_action_count_avg": 0.0,
            "node_subplan_count_avg": 0.0,
        }
    return {**dict(run), "artifact_ready": True, "artifact_error": ""}


def _distance_diversity_summary(
    *,
    near_run: Path,
    varied_runs: Sequence[Path],
) -> Dict[str, Any]:
    near = _summarize_distance_matrix_run_fail_closed(near_run)
    varied = [
        _summarize_distance_matrix_run_fail_closed(path)
        for path in list(varied_runs)
    ]
    all_runs = [near] + varied
    artifact_ready = all(bool(run.get("artifact_ready", True)) for run in all_runs)
    coverage_ready = all(
        bool(run.get("artifact_ready", True))
        and
        int(run.get("runtime_snapshot_count", 0) or 0) > 0
        and int(run.get("covered_runtime_ut_count", 0) or 0) > 0
        for run in all_runs
    )
    near_one_hop = (
        coverage_ready
        and float(near.get("isl_hop_count_avg", 0.0) or 0.0) == 0.0
        and int(near.get("isl_hop_count_max", 0) or 0) == 0
    )
    varied_multihop_count = sum(
        1
        for run in varied
        if float(run.get("isl_hop_count_avg", 0.0) or 0.0) > 0.0
        and int(run.get("isl_hop_count_max", 0) or 0) > 0
    )
    update_size_metrics_present = all(
        float(run.get("runtime_update_action_count_avg", 0.0) or 0.0) > 0.0
        and float(run.get("node_subplan_count_avg", 0.0) or 0.0) > 0.0
        for run in all_runs
    )
    ready = (
        artifact_ready
        and
        coverage_ready
        and near_one_hop
        and varied_multihop_count >= 1
        and update_size_metrics_present
    )
    blockers = [
        "distance_diversity_artifact_missing_or_unreadable"
        for run in all_runs
        if not bool(run.get("artifact_ready", True))
    ]
    return {
        "ready": ready,
        "artifact_ready": artifact_ready,
        "blockers": sorted(set(blockers)),
        "coverage_ready": coverage_ready,
        "near_one_hop": near_one_hop,
        "varied_multihop_run_count": varied_multihop_count,
        "update_size_metrics_present": update_size_metrics_present,
        "near_run": _compact_distance_run(near),
        "varied_runs": [_compact_distance_run(run) for run in varied],
    }


def _return_path_summary(return_audit_run: Path) -> Dict[str, Any]:
    inspection = inspect_temporal_run(return_audit_run)
    audit = dict(inspection.get("return_path_audit_summary", {}))
    run_path = Path(return_audit_run).expanduser().resolve()
    run_dir = run_path if run_path.is_dir() else run_path.parent
    reachability = _return_path_reachability_from_preview(run_dir)
    install_audit = summarize_return_path_install_points()
    selected_flow_count = int(audit.get("selected_runtime_total_flow_count", 0) or 0)
    selected_blocker_items = int(
        audit.get("selected_runtime_backbone_blocker_item_count", 0) or 0
    )
    full_blocker_items = int(
        audit.get("return_path_backbone_blocker_item_count", 0) or 0
    )
    selected_migration_required = _bool_or_none(
        audit.get("selected_runtime_backbone_migration_required")
    )
    if selected_flow_count <= 0:
        selected_migration_required = None
    audit_ready = (
        bool(audit.get("present"))
        and int(audit.get("total_flow_count", 0) or 0) > 0
        and (
            selected_blocker_items > 0
            or full_blocker_items > 0
            or bool(audit.get("return_path_backbone_migration_required"))
        )
    )
    install_point_audit_ready = (
        install_audit.get("current_linux_realization")
        == "per_ut_flow_compatibility"
        and install_audit.get("target_backbone_realization")
        == "shared_gateway_zone_or_zone_return_reachability"
        and int(install_audit.get("backbone_blocker_count", 0) or 0) >= 3
        and int(install_audit.get("install_point_count", 0) or 0) > 0
    )
    return {
        "audit_ready": audit_ready,
        "install_point_audit_ready": install_point_audit_ready,
        "run_id": Path(return_audit_run).expanduser().resolve().name,
        "present": bool(audit.get("present")),
        "shared_backbone_ready": bool(
            audit.get("selected_runtime_shared_backbone_ready")
            if selected_flow_count > 0
            else audit.get("return_path_shared_backbone_ready")
        ),
        "backbone_migration_required": (
            selected_migration_required
            if selected_migration_required is not None
            else _bool_or_none(audit.get("return_path_backbone_migration_required"))
        ),
        "current_realization": audit.get("return_path_current_realization"),
        "target_realization": audit.get("return_path_target_realization"),
        "selected_runtime_flow_count": selected_flow_count,
        "selected_runtime_backbone_blocker_item_count": selected_blocker_items,
        "selected_runtime_current_to_target_backbone_state_ratio": audit.get(
            "selected_runtime_current_to_target_backbone_state_ratio"
        ),
        "full_scope_flow_count": audit.get("total_flow_count"),
        "full_scope_backbone_blocker_item_count": full_blocker_items,
        "top_backbone_migration_blocker": audit.get(
            "return_path_top_backbone_migration_blocker"
        ),
        "top_global_migration_blocker": audit.get("return_path_top_migration_blocker"),
        "projection_10000_current_backbone_items": audit.get(
            "selected_runtime_projection_10000_current_backbone_items"
        )
        or audit.get("projection_10000_current_backbone_items"),
        "projection_10000_target_constant_backbone_items": audit.get(
            "selected_runtime_projection_10000_target_constant_backbone_items"
        )
        or audit.get("projection_10000_target_constant_backbone_items"),
        "projection_10000_ratio_constant": audit.get(
            "selected_runtime_projection_10000_ratio_constant"
        )
        or audit.get("projection_10000_ratio_constant"),
        "selected_runtime_current_reverse_label_count": reachability.get(
            "selected_current_reverse_label_count"
        ),
        "selected_runtime_candidate_shared_label_stack_count": reachability.get(
            "selected_candidate_shared_label_stack_count"
        ),
        "selected_runtime_shared_label_ratio": reachability.get(
            "selected_current_to_candidate_label_stack_ratio"
        ),
        "selected_runtime_shared_label_reduction_available": reachability.get(
            "selected_reverse_label_reduction_available"
        ),
        "selected_runtime_candidate_shared_zone_route_count": reachability.get(
            "selected_candidate_shared_zone_route_count"
        ),
        "selected_runtime_candidate_zone_route_coverage_ratio": reachability.get(
            "selected_candidate_zone_route_coverage_ratio"
        ),
        "selected_runtime_candidate_zone_route_complete": reachability.get(
            "selected_candidate_zone_route_complete"
        ),
        "selected_runtime_candidate_metadata_coverage_ratio": reachability.get(
            "selected_candidate_metadata_coverage_ratio"
        ),
        "selected_runtime_candidate_metadata_complete": reachability.get(
            "selected_candidate_metadata_complete"
        ),
        "selected_runtime_explicit_candidate_metadata_flow_count": reachability.get(
            "selected_explicit_candidate_metadata_flow_count"
        ),
        "selected_runtime_fallback_reverse_path_signature_flow_count": reachability.get(
            "selected_fallback_reverse_path_signature_flow_count"
        ),
        "install_point_blocker_count": install_audit.get("blocker_count"),
        "install_point_backbone_blocker_count": install_audit.get(
            "backbone_blocker_count"
        ),
        "install_point_count": install_audit.get("install_point_count"),
    }


def _return_path_reachability_from_preview(run_dir: Path) -> Dict[str, Any]:
    planner_preview_path = run_dir / "planner_preview.json"
    if not planner_preview_path.exists():
        return {}
    selected_scope_ids: List[str] = []
    return_path_audit_path = run_dir / "return_path_audit.json"
    if return_path_audit_path.exists():
        existing_audit = _load_json(return_path_audit_path)
        selected_scope_ids = [
            str(scope_id)
            for scope_id in list(existing_audit.get("selected_scope_ids", []))
            if str(scope_id)
        ]
    recomputed = audit_planner_preview_path(
        planner_preview_path,
        selected_scope_ids=selected_scope_ids,
    )
    full = dict(recomputed.get("shared_backbone_return_reachability_preview", {}))
    selected = dict(
        dict(recomputed.get("selected_runtime_scope", {})).get(
            "shared_backbone_return_reachability_preview",
            {},
        )
    )
    selected_source = selected if selected else full
    return {
        "full_current_reverse_label_count": full.get("current_reverse_label_count"),
        "full_candidate_shared_zone_route_count": full.get(
            "candidate_shared_zone_route_count"
        ),
        "full_candidate_zone_route_coverage_ratio": full.get(
            "candidate_zone_route_coverage_ratio"
        ),
        "full_candidate_zone_route_complete": full.get(
            "candidate_zone_route_complete"
        ),
        "full_candidate_shared_label_stack_count": full.get(
            "candidate_shared_label_stack_count"
        ),
        "full_current_to_candidate_label_stack_ratio": full.get(
            "current_to_candidate_label_stack_ratio"
        ),
        "full_reverse_label_reduction_available": full.get(
            "reverse_label_reduction_available"
        ),
        "full_candidate_metadata_coverage_ratio": full.get(
            "candidate_metadata_coverage_ratio"
        ),
        "full_candidate_metadata_complete": full.get("candidate_metadata_complete"),
        "full_explicit_candidate_metadata_flow_count": full.get(
            "explicit_candidate_metadata_flow_count"
        ),
        "full_fallback_reverse_path_signature_flow_count": full.get(
            "fallback_reverse_path_signature_flow_count"
        ),
        "selected_current_reverse_label_count": selected_source.get(
            "current_reverse_label_count"
        ),
        "selected_candidate_shared_zone_route_count": selected_source.get(
            "candidate_shared_zone_route_count"
        ),
        "selected_candidate_zone_route_coverage_ratio": selected_source.get(
            "candidate_zone_route_coverage_ratio"
        ),
        "selected_candidate_zone_route_complete": selected_source.get(
            "candidate_zone_route_complete"
        ),
        "selected_candidate_shared_label_stack_count": selected_source.get(
            "candidate_shared_label_stack_count"
        ),
        "selected_current_to_candidate_label_stack_ratio": selected_source.get(
            "current_to_candidate_label_stack_ratio"
        ),
        "selected_reverse_label_reduction_available": selected_source.get(
            "reverse_label_reduction_available"
        ),
        "selected_candidate_metadata_coverage_ratio": selected_source.get(
            "candidate_metadata_coverage_ratio"
        ),
        "selected_candidate_metadata_complete": selected_source.get(
            "candidate_metadata_complete"
        ),
        "selected_explicit_candidate_metadata_flow_count": selected_source.get(
            "explicit_candidate_metadata_flow_count"
        ),
        "selected_fallback_reverse_path_signature_flow_count": selected_source.get(
            "fallback_reverse_path_signature_flow_count"
        ),
    }


def _runtime_protection_summary(protection_run: Path) -> Dict[str, Any]:
    inspection = inspect_temporal_run(protection_run)
    ping = dict(inspection.get("runtime_ping_validation_summary", {}))
    reuse = dict(inspection.get("runtime_incremental_reuse_summary", {}))
    sampling_default_ready = (
        DEFAULT_RUNTIME_EPOCH_VALIDATION_SCOPE == EXPECTED_RUNTIME_VALIDATION_SCOPE
    )
    test_files = {
        "runtime_validation_defaults": SATELLITE_DIR
        / "test_runtime_validation_defaults.py",
        "incremental_runtime_reuse": SATELLITE_DIR
        / "test_incremental_runtime_reuse.py",
    }
    test_coverage_present = all(path.exists() for path in test_files.values())
    return {
        "ready": sampling_default_ready and test_coverage_present,
        "sampling_default_ready": sampling_default_ready,
        "default_validation_scope": DEFAULT_RUNTIME_EPOCH_VALIDATION_SCOPE,
        "test_coverage_present": test_coverage_present,
        "test_files": {name: str(path) for name, path in test_files.items()},
        "artifact_run_id": Path(protection_run).expanduser().resolve().name,
        "artifact_ping_validation_present": bool(ping.get("present")),
        "artifact_validation_scopes": list(ping.get("validation_scopes", [])),
        "artifact_max_selected_to_source_ratio": ping.get(
            "max_selected_to_sample_source_flow_ratio"
        ),
        "artifact_incremental_reuse_present": bool(reuse.get("present")),
        "artifact_max_reapply_reduction_count": reuse.get(
            "max_reapply_reduction_count"
        ),
        "artifact_max_held_to_active_flow_ratio": reuse.get(
            "max_held_to_active_flow_ratio"
        ),
    }


def _timing_classification_summary(baseline_real_run: Path) -> Dict[str, Any]:
    inspection = inspect_temporal_run(baseline_real_run)
    timing_analysis = dict(inspection.get("timing_analysis", {}))
    initial = dict(timing_analysis.get("initial_activation", {}))
    steady = dict(timing_analysis.get("steady_state_runtime_epochs", {}))
    parameter = dict(timing_analysis.get("parameter_epochs", {}))
    teardown = dict(inspection.get("teardown_cleanup_summary", {}))
    steady_excludes_initial = bool(
        initial.get("steady_runtime_excludes_initial_activation")
    )
    ready = (
        initial.get("classification") == "bootstrap_plus_initial_runtime_epoch"
        and float(initial.get("initial_activation_wall_seconds", 0.0) or 0.0) > 0.0
        and steady_excludes_initial
        and int(steady.get("count", 0) or 0) > 0
        and int(steady.get("excluded_initial_runtime_epoch_count", 0) or 0) >= 1
        and bool(teardown.get("present"))
    )
    return {
        "ready": ready,
        "run_id": Path(baseline_real_run).expanduser().resolve().name,
        "initial_activation_classification": initial.get("classification"),
        "initial_activation_wall_seconds": initial.get(
            "initial_activation_wall_seconds"
        ),
        "initial_activation_cold_start_seconds": initial.get(
            "cold_start_before_first_epoch_execution_wall_seconds"
        ),
        "first_runtime_execution_seconds": initial.get(
            "first_runtime_epoch_execution_after_prepare_reconcile_wall_seconds"
        ),
        "steady_runtime_excludes_initial_activation": steady_excludes_initial,
        "steady_runtime_epoch_count": steady.get("count"),
        "steady_runtime_source_epoch_count": steady.get("source_runtime_epoch_count"),
        "steady_runtime_excluded_initial_count": steady.get(
            "excluded_initial_runtime_epoch_count"
        ),
        "steady_runtime_wall_avg_seconds": steady.get(
            "average_transition_wall_seconds"
        ),
        "steady_runtime_host_apply_avg_seconds": steady.get(
            "average_host_mutation_apply_wall_seconds"
        ),
        "steady_runtime_node_local_apply_avg_seconds": steady.get(
            "average_node_local_apply_wall_seconds"
        ),
        "parameter_epoch_count": parameter.get("count"),
        "parameter_epoch_wall_avg_seconds": parameter.get(
            "average_transition_wall_seconds"
        ),
        "teardown_present": bool(teardown.get("present")),
        "teardown_success": teardown.get("all_cleanup_success"),
        "teardown_known_wall_seconds": teardown.get(
            "known_teardown_cleanup_wall_seconds"
        ),
        "teardown_wall_seconds": teardown.get("teardown_cleanup_wall_seconds"),
        "teardown_wall_time_complete": teardown.get(
            "teardown_cleanup_wall_time_complete"
        ),
    }


def _preflight_summary(
    *,
    live_preflight: bool,
    preflight_json: Path | None,
) -> Dict[str, Any]:
    if live_preflight:
        report = run_skyseed_preflight()
        source = "live"
    elif preflight_json is not None:
        report = _load_json(preflight_json)
        source = str(preflight_json.expanduser().resolve())
    else:
        return {
            "present": False,
            "source": None,
            "scoped_hygiene_ready": None,
            "host_runtime_ready": None,
            "long_real_run_environment_ready": None,
            "warning_reasons": [],
            "blocking_reasons": [],
        }

    assessment = dict(report.get("assessment", {}))
    checks = dict(report.get("checks", {}))
    warnings = list(assessment.get("warning_reasons", []))
    scoped_warning_ids = {
        "seed_output_compose_resources_present",
        "seed_output_partial_container_state_present",
        "named_skyseed_containers_present",
        "named_skyseed_networks_present",
        "direct_backend_host_interfaces_present",
        "runtime_lock_files_present",
    }
    host_warning_ids = {
        "host_mpls_not_ready",
        "bridge_netfilter_passthrough_not_ready",
    }
    scoped_hygiene_ready = bool(assessment.get("ready")) and not any(
        warning in scoped_warning_ids for warning in warnings
    )
    host_runtime_ready = not any(warning in host_warning_ids for warning in warnings)
    return {
        "present": True,
        "source": source,
        "scoped_hygiene_ready": scoped_hygiene_ready,
        "host_runtime_ready": host_runtime_ready,
        "long_real_run_environment_ready": scoped_hygiene_ready and host_runtime_ready,
        "warning_reasons": warnings,
        "blocking_reasons": list(assessment.get("blocking_reasons", [])),
        "preferred_seed310_selected": dict(checks.get("python_runtime", {})).get(
            "preferred_seed310_selected"
        ),
    }


def summarize_goal_readiness(
    *,
    near_run: Path,
    varied_runs: Sequence[Path],
    return_audit_run: Path,
    baseline_real_run: Path,
    protection_run: Path | None = None,
    live_preflight: bool = False,
    preflight_json: Path | None = None,
) -> Dict[str, Any]:
    protection_run = protection_run or baseline_real_run
    distance = _distance_diversity_summary(
        near_run=near_run,
        varied_runs=varied_runs,
    )
    return_path = _return_path_summary(return_audit_run)
    runtime_protection = _runtime_protection_summary(protection_run)
    timing = _timing_classification_summary(baseline_real_run)
    preflight = _preflight_summary(
        live_preflight=live_preflight,
        preflight_json=preflight_json,
    )
    static_ready = (
        bool(distance.get("ready"))
        and bool(return_path.get("audit_ready"))
        and bool(return_path.get("install_point_audit_ready"))
        and bool(runtime_protection.get("ready"))
        and bool(timing.get("ready"))
    )
    preflight_ready = preflight.get("long_real_run_environment_ready")
    long_real_run_ready = static_ready and preflight_ready is True
    if not static_ready:
        next_action = "continue_static_artifact_or_unit_checks"
    elif preflight_ready is None:
        next_action = "run_seed310_preflight_before_spending_long_real_run"
    elif not preflight_ready:
        next_action = "run_host_prepare_then_recheck_preflight_before_real_run"
    else:
        next_action = "long_real_run_gate_ready_with_known_return_path_blockers"

    return {
        "goal": "1shell_172_gz_runtime_readiness",
        "static_evidence_ready": static_ready,
        "long_real_run_ready": long_real_run_ready,
        "next_action": next_action,
        "distance_diversity": distance,
        "return_path_migration": return_path,
        "runtime_update_protection": runtime_protection,
        "timing_classification": timing,
        "preflight": preflight,
    }


def render_goal_readiness_summary(report: Dict[str, Any]) -> str:
    distance = dict(report.get("distance_diversity", {}))
    return_path = dict(report.get("return_path_migration", {}))
    protection = dict(report.get("runtime_update_protection", {}))
    timing = dict(report.get("timing_classification", {}))
    preflight = dict(report.get("preflight", {}))
    return "\n".join(
        [
            "Skyseed 1-shell G/Z readiness",
            f"  static_evidence_ready={report.get('static_evidence_ready')}",
            f"  long_real_run_ready={report.get('long_real_run_ready')}",
            f"  next_action={report.get('next_action')}",
            (
                "  distance_diversity: "
                f"ready={distance.get('ready')} "
                f"near_one_hop={distance.get('near_one_hop')} "
                f"varied_multihop_runs={distance.get('varied_multihop_run_count')} "
                f"update_size_metrics={distance.get('update_size_metrics_present')}"
            ),
            (
                "  return_path_migration: "
                f"audit_ready={return_path.get('audit_ready')} "
                f"install_points_ready={return_path.get('install_point_audit_ready')} "
                f"shared_backbone_ready={return_path.get('shared_backbone_ready')} "
                f"migration_required={return_path.get('backbone_migration_required')} "
                f"selected_blocker_items={return_path.get('selected_runtime_backbone_blocker_item_count')} "
                f"shared_label_ratio={return_path.get('selected_runtime_shared_label_ratio')} "
                f"candidate_zone_routes={return_path.get('selected_runtime_candidate_shared_zone_route_count')} "
                f"candidate_zone_route_coverage={return_path.get('selected_runtime_candidate_zone_route_coverage_ratio')} "
                f"candidate_metadata_coverage={return_path.get('selected_runtime_candidate_metadata_coverage_ratio')} "
                f"candidate_metadata_complete={return_path.get('selected_runtime_candidate_metadata_complete')} "
                f"install_points={return_path.get('install_point_count')} "
                f"top_backbone_blocker={return_path.get('top_backbone_migration_blocker')}"
            ),
            (
                "  runtime_update_protection: "
                f"ready={protection.get('ready')} "
                f"default_scope={protection.get('default_validation_scope')} "
                f"test_coverage={protection.get('test_coverage_present')} "
                f"artifact_ping_validation={protection.get('artifact_ping_validation_present')} "
                f"artifact_incremental_reuse={protection.get('artifact_incremental_reuse_present')}"
            ),
            (
                "  timing_classification: "
                f"ready={timing.get('ready')} "
                f"initial_activation_wall={timing.get('initial_activation_wall_seconds')} "
                f"steady_runtime_count={timing.get('steady_runtime_epoch_count')} "
                f"steady_wall_avg={timing.get('steady_runtime_wall_avg_seconds')} "
                f"parameter_wall_avg={timing.get('parameter_epoch_wall_avg_seconds')}"
            ),
            (
                "  preflight: "
                f"present={preflight.get('present')} "
                f"scoped_hygiene_ready={preflight.get('scoped_hygiene_ready')} "
                f"host_runtime_ready={preflight.get('host_runtime_ready')} "
                f"warnings={','.join(preflight.get('warning_reasons', [])) or 'none'}"
            ),
        ]
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Combine preview distance diversity, return-path scaling audit, runtime "
            "validation protections, timing classification, and optional preflight "
            "into a single 1-shell G/Z readiness report."
        ),
    )
    parser.add_argument("--near-run", type=Path, default=DEFAULT_NEAR_RUN)
    parser.add_argument(
        "--varied-run",
        action="append",
        type=Path,
        default=None,
        help="Preview run with non-near UT/GW placement; repeatable.",
    )
    parser.add_argument(
        "--return-audit-run",
        type=Path,
        default=DEFAULT_RETURN_AUDIT_RUN,
    )
    parser.add_argument(
        "--baseline-real-run",
        type=Path,
        default=DEFAULT_BASELINE_REAL_RUN,
    )
    parser.add_argument(
        "--protection-run",
        type=Path,
        default=None,
        help="Real-runtime artifact to inspect for optional ping/reuse report evidence.",
    )
    parser.add_argument(
        "--live-preflight",
        action="store_true",
        help="Run live Skyseed preflight and include it in the gate.",
    )
    parser.add_argument(
        "--preflight-json",
        type=Path,
        default=None,
        help="Read an existing skyseed_preflight --format json report.",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
    )
    parser.add_argument("--write-report", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    varied_runs = (
        list(args.varied_run)
        if args.varied_run
        else [DEFAULT_MIXED_RUN, DEFAULT_FAR_RUN]
    )
    report = summarize_goal_readiness(
        near_run=args.near_run,
        varied_runs=varied_runs,
        return_audit_run=args.return_audit_run,
        baseline_real_run=args.baseline_real_run,
        protection_run=args.protection_run,
        live_preflight=bool(args.live_preflight),
        preflight_json=args.preflight_json,
    )
    rendered_json = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.write_report:
        args.write_report.expanduser().resolve().parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        args.write_report.expanduser().write_text(rendered_json, encoding="utf-8")
    if args.format == "json":
        print(rendered_json, end="")
    else:
        print(render_goal_readiness_summary(report))
    return 0 if report.get("static_evidence_ready") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
