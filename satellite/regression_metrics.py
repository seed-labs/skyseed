#!/usr/bin/env python3
# encoding: utf-8

"""Regression metrics contract for the Skyseed 1-shell canonical runtime."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple


SCHEMA_VERSION = "skyseed-regression-metrics-v1"
BASELINE_ID = "skyseed_1shell_s5_12ut6gw_6zone_2h"
TIMING_MARK_SCHEMA_VERSION = "skyseed-epoch-timing-marks-v1"

REQUIRED_ARTIFACTS = (
    "run_manifest.json",
    "real_runtime_report.json",
    "timing.json",
    "temporal_audit.json",
    "return_path_audit.json",
    "gz_update_diff_summary.json",
)

TIMING_REQUIRED_ELAPSED_FIELDS = (
    "timing_mark_elapsed_actual_epoch_start_time_utc_seconds",
    "timing_mark_elapsed_data_plane_apply_start_utc_seconds",
    "timing_mark_elapsed_host_apply_start_seconds",
    "timing_mark_elapsed_host_apply_end_seconds",
    "timing_mark_elapsed_data_plane_ready_time_utc_seconds",
    "timing_mark_elapsed_artifact_reporting_start_seconds",
    "timing_mark_elapsed_artifact_reporting_end_seconds",
    "timing_mark_elapsed_epoch_end_time_utc_seconds",
)

TIMING_REQUIRED_WALL_FIELDS = (
    "transition_wall_seconds",
    "semantic_total_epoch_wall_seconds",
    "semantic_scheduled_activation_lag_seconds",
    "semantic_data_plane_ready_lag_seconds",
    "semantic_scheduler_blocking_after_data_plane_ready_seconds",
    "semantic_artifact_write_blocking_seconds",
)


def read_json(path: Path) -> Any:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _num(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return float(default)
    if isinstance(value, (int, float)):
        return float(value)
    try:
        if value is None or value == "":
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _bool(value: Any) -> bool:
    return bool(value)


def _nearest_rank(values: Sequence[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    index = max(0, min(len(ordered) - 1, math.ceil(quantile * len(ordered)) - 1))
    return ordered[index]


def _stats(values: Iterable[Any]) -> Dict[str, Any]:
    rendered = [
        float(value)
        for value in values
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    if not rendered:
        return {"count": 0, "p50": 0.0, "p95": 0.0, "max": 0.0, "sum": 0.0}
    return {
        "count": len(rendered),
        "p50": round(_nearest_rank(rendered, 0.50), 6),
        "p95": round(_nearest_rank(rendered, 0.95), 6),
        "max": round(max(rendered), 6),
        "sum": round(sum(rendered), 6),
    }


def _row_values(rows: Sequence[Dict[str, Any]], key: str) -> List[float]:
    return [
        float(row[key])
        for row in rows
        if isinstance(row.get(key), (int, float)) and not isinstance(row.get(key), bool)
    ]


def _artifact_totals(run_dir: Path) -> Dict[str, int]:
    total_size = 0
    total_count = 0
    for root, _, files in os.walk(run_dir):
        for name in files:
            path = Path(root) / name
            try:
                stat = path.stat()
            except OSError:
                continue
            total_size += stat.st_size
            total_count += 1
    return {"run_dir_size_bytes": total_size, "artifact_count": total_count}


def _cleanup_metrics(report: Dict[str, Any], timing: Dict[str, Any]) -> Dict[str, Any]:
    cleanup = _as_dict(report.get("final_cleanup"))
    node_local = _as_dict(cleanup.get("node_local"))
    node_timing = _as_dict(node_local.get("timing"))
    host_rollbacks = [
        _as_dict(item)
        for item in _as_list(cleanup.get("host_rollbacks"))
        if isinstance(item, dict)
    ]
    host_rollback_seconds = [
        _num(_as_dict(item.get("timing")).get("rollback_wall_seconds"))
        for item in host_rollbacks
    ]
    host_artifacts = _as_dict(cleanup.get("host_artifact_cleanup"))
    host_artifact_timing = _as_dict(host_artifacts.get("timing"))
    phase_timing = _as_dict(timing.get("phase_timing"))
    node_rollback = _num(node_timing.get("rollback_wall_seconds"))
    host_rollback = sum(host_rollback_seconds)
    host_artifact_cleanup = _num(host_artifact_timing.get("cleanup_wall_seconds"))
    post_run_preflight = _num(phase_timing.get("post_run_preflight_wall_seconds"))
    remaining_residue = sum(
        int(_num(host_artifacts.get(key)))
        for key in (
            "remaining_bridge_count",
            "remaining_host_interface_count",
            "remaining_temp_host_interface_count",
        )
    )
    return {
        "final_rollback_cleanup_seconds": round(
            node_rollback + host_rollback + host_artifact_cleanup + post_run_preflight,
            6,
        ),
        "node_local_rollback_seconds": round(node_rollback, 6),
        "host_rollback_seconds": round(host_rollback, 6),
        "host_rollback_max_seconds": round(max(host_rollback_seconds) if host_rollback_seconds else 0.0, 6),
        "host_artifact_cleanup_seconds": round(host_artifact_cleanup, 6),
        "post_run_preflight_seconds": round(post_run_preflight, 6),
        "cleanup_success": cleanup.get("all_cleanup_success"),
        "residue_count_after_rollback": remaining_residue,
    }


def _dynamic_satellite_link_count(manifest: Dict[str, Any]) -> int | None:
    compose_audit = _as_dict(manifest.get("compose_audit"))
    network_counts = _as_dict(compose_audit.get("network_counts"))
    value = network_counts.get("dynamic_satellite_link")
    if value is None:
        return None
    return int(_num(value))


def _temporal_audit_pass(temporal: Dict[str, Any]) -> bool:
    return (
        temporal.get("finalized") is True
        and temporal.get("no_future_active_link_violation") is True
        and temporal.get("no_future_forwarding_state_violation") is True
        and temporal.get("parameter_epoch_without_topology_mutation") is True
        and temporal.get("runtime_epoch_has_expected_mutation_scope") is True
        and temporal.get("rollback_completed") is True
    )


def _ut_mpls_egress_count(return_path: Dict[str, Any]) -> int | None:
    for container in (
        return_path,
        _as_dict(return_path.get("state_counts")),
        _as_dict(_as_dict(return_path.get("state_counts")).get("shared_gz_model_after")),
        _as_dict(return_path.get("return_path_migration_readiness")),
    ):
        value = container.get("ut_mpls_egress_count")
        if value is not None:
            return int(_num(value))
    readiness = _as_dict(return_path.get("return_path_migration_readiness"))
    if (
        readiness.get("current_realization") == "shared_gz_backbone"
        and return_path.get("current_return_state_scales_with_flow_count") is False
    ):
        return 0
    return None


def _parameter_epoch_guard_counts(report: Dict[str, Any], timing_rows: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    hidden_full_apply = 0
    hidden_recovery = 0
    parameter_iterations = [
        _as_dict(item)
        for item in _as_list(report.get("iterations"))
        if _as_dict(item).get("epoch_type") == "parameter_epoch"
    ]
    for item in parameter_iterations:
        if (
            item.get("escalated_to_runtime_epoch") is True
            or item.get("scheduled_topology_mutation") is True
            or int(_num(item.get("topology_mutation_count"))) > 0
            or int(_num(item.get("forwarding_mutation_count"))) > 0
            or _as_dict(item.get("node_local_execution"))
        ):
            hidden_full_apply += 1
        if item.get("parameter_refresh_recovery_used") is True:
            hidden_recovery += 1
    for row in timing_rows:
        if row.get("epoch_type") != "parameter_epoch":
            continue
        if row.get("semantic_topology_or_forwarding_mutation_free") is False:
            hidden_full_apply += 1
        if _num(row.get("semantic_parameter_recovery_fallback_seconds")) > 0 or _num(row.get("recovery_seconds")) > 0:
            hidden_recovery += 1
    return {
        "parameter_epoch_hidden_full_apply_count": hidden_full_apply,
        "parameter_epoch_hidden_recovery_count": hidden_recovery,
    }


def _timing_integrity(timing: Dict[str, Any]) -> Dict[str, Any]:
    rows = [_as_dict(row) for row in _as_list(timing.get("rows")) if isinstance(row, dict)]
    missing_mark_count = 0
    negative_duration_count = 0
    mixed_event_monotonic_count = 0
    missing_epoch_type_count = 0
    for row in rows:
        if row.get("epoch_type") not in {"runtime_epoch", "parameter_epoch"}:
            missing_epoch_type_count += 1
        if row.get("epoch_timing_marks_schema_version") != TIMING_MARK_SCHEMA_VERSION:
            missing_mark_count += 1
        for field in TIMING_REQUIRED_ELAPSED_FIELDS:
            value = row.get(field)
            if value is None:
                missing_mark_count += 1
                continue
            if _num(value) < -1e-9:
                negative_duration_count += 1
        for field in TIMING_REQUIRED_WALL_FIELDS:
            value = row.get(field)
            if value is None:
                missing_mark_count += 1
                continue
            if _num(value) < -1e-9:
                negative_duration_count += 1
        scheduled_elapsed = row.get("timing_mark_elapsed_scheduled_activation_time_utc_seconds")
        activation_lag = row.get("semantic_scheduled_activation_lag_seconds", row.get("activation_lag_seconds"))
        if scheduled_elapsed is None or activation_lag is None:
            missing_mark_count += 1
        elif abs(_num(scheduled_elapsed) + _num(activation_lag)) > 0.005:
            mixed_event_monotonic_count += 1
        start_end_pairs = (
            ("timing_mark_elapsed_host_apply_start_seconds", "timing_mark_elapsed_host_apply_end_seconds"),
            ("timing_mark_elapsed_data_plane_apply_start_utc_seconds", "timing_mark_elapsed_data_plane_ready_time_utc_seconds"),
            ("timing_mark_elapsed_artifact_reporting_start_seconds", "timing_mark_elapsed_artifact_reporting_end_seconds"),
        )
        for start, end in start_end_pairs:
            if row.get(start) is not None and row.get(end) is not None and _num(row[end]) + 1e-9 < _num(row[start]):
                mixed_event_monotonic_count += 1
    runtime_count = sum(1 for row in rows if row.get("epoch_type") == "runtime_epoch")
    parameter_count = sum(1 for row in rows if row.get("epoch_type") == "parameter_epoch")
    return {
        "row_count": len(rows),
        "runtime_row_count": runtime_count,
        "parameter_row_count": parameter_count,
        "missing_timing_mark_count": missing_mark_count,
        "negative_duration_count": negative_duration_count,
        "mixed_event_time_monotonic_count": mixed_event_monotonic_count,
        "missing_epoch_type_count": missing_epoch_type_count,
        "runtime_parameter_epoch_distinguishable": runtime_count > 0 and parameter_count > 0 and missing_epoch_type_count == 0,
    }


def _is_gz_group(group_id: Any) -> bool:
    text = str(group_id)
    return text.startswith("zone-") or ":default-gateway:" in text


def _fec_diff_metrics(diff_summary: Dict[str, Any], manifest: Dict[str, Any]) -> Dict[str, Any]:
    diffs = [_as_dict(item) for item in _as_list(diff_summary.get("diffs")) if isinstance(item, dict)]
    requested_satellites = int(_num(manifest.get("requested_satellite_count"), 0.0))
    changed_g = 0
    changed_gz = 0
    skipped_g = 0
    skipped_gz = 0
    full_core = 0
    operation_estimates: List[int] = []
    affected_counts: List[int] = []
    for row in diffs:
        changed_groups = (
            _as_list(row.get("added_fec_groups"))
            + _as_list(row.get("removed_fec_groups"))
            + _as_list(row.get("changed_fec_groups"))
        )
        skipped_groups = _as_list(row.get("skipped_unchanged_fec_groups"))
        changed_gz += sum(1 for group in changed_groups if _is_gz_group(group))
        changed_g += sum(1 for group in changed_groups if not _is_gz_group(group))
        skipped_gz += sum(1 for group in skipped_groups if _is_gz_group(group))
        skipped_g += sum(1 for group in skipped_groups if not _is_gz_group(group))
        affected = int(_num(row.get("affected_node_count")))
        affected_counts.append(affected)
        estimate = int(_num(row.get("estimated_lfib_route_operation_items")))
        operation_estimates.append(estimate)
        scope = str(row.get("diff_scope") or "")
        if "full" in scope.lower() or (requested_satellites > 0 and affected >= requested_satellites):
            full_core += 1
    return {
        "diff_count": len(diffs),
        "changed_g_fec_count": changed_g,
        "skipped_g_fec_count": skipped_g,
        "changed_gz_fec_count": changed_gz,
        "skipped_gz_fec_count": skipped_gz,
        "changed_fec_count_total": sum(int(_num(row.get("changed_fec_group_count"))) for row in diffs),
        "skipped_fec_count_total": sum(int(_num(row.get("skipped_unchanged_fec_group_count"))) for row in diffs),
        "changed_gz_return_group_count_total": sum(int(_num(row.get("changed_gz_return_group_count"))) for row in diffs),
        "affected_nodes_total": sum(affected_counts),
        "affected_nodes_max": max(affected_counts) if affected_counts else 0,
        "operation_estimate_total": sum(operation_estimates),
        "operation_estimate_max": max(operation_estimates) if operation_estimates else 0,
        "per_epoch_max_operation_estimate": max(operation_estimates) if operation_estimates else 0,
        "full_core_reinstall_count": full_core,
    }


def extract_regression_metrics(run_dir: Path) -> Dict[str, Any]:
    run_dir = run_dir.expanduser().resolve()
    manifest = _as_dict(read_json(run_dir / "run_manifest.json"))
    report = _as_dict(read_json(run_dir / "real_runtime_report.json"))
    timing = _as_dict(read_json(run_dir / "timing.json"))
    temporal = _as_dict(read_json(run_dir / "temporal_audit.json"))
    return_path = _as_dict(read_json(run_dir / "return_path_audit.json"))
    post_preflight = _as_dict(read_json(run_dir / "post_run_preflight.json"))
    diff_summary = _as_dict(read_json(run_dir / "gz_update_diff_summary.json"))
    timing_rows = [_as_dict(row) for row in _as_list(timing.get("rows")) if isinstance(row, dict)]
    runtime_rows = [row for row in timing_rows if row.get("epoch_type") == "runtime_epoch"]
    steady_runtime_rows = runtime_rows[1:] if runtime_rows else []
    parameter_rows = [row for row in timing_rows if row.get("epoch_type") == "parameter_epoch"]
    timing_analysis = _as_dict(timing.get("timing_analysis") or manifest.get("timing_analysis"))
    startup = _as_dict(timing_analysis.get("startup_preparation"))
    initial_materialization = _as_dict(timing_analysis.get("initial_materialization_epoch"))
    continuous = _as_dict(manifest.get("continuous_operation_summary"))
    outcome = _as_dict(report.get("outcome") or manifest.get("outcome"))
    readiness = _as_dict(return_path.get("return_path_migration_readiness"))
    cleanup = _cleanup_metrics(report, timing)
    artifact_totals = _artifact_totals(run_dir)
    parameter_guards = _parameter_epoch_guard_counts(report, timing_rows)
    timing_integrity = _timing_integrity(timing)
    fec_diff = _fec_diff_metrics(diff_summary, manifest)
    post_assessment = _as_dict(post_preflight.get("assessment"))
    artifacts_present = {name: (run_dir / name).exists() for name in REQUIRED_ARTIFACTS}
    hard_gates = {
        "real_apply_success": outcome.get("real_apply_success") is True or manifest.get("real_apply_success") is True,
        "ping_success": outcome.get("ping_success") is True or manifest.get("ping_success") is True,
        "rollback_completed": outcome.get("rollback_completed") is True or manifest.get("rollback_completed") is True,
        "temporal_audit_pass": _temporal_audit_pass(temporal),
        "gz_audit_mode": readiness.get("current_realization"),
        "ut_mpls_egress_count": _ut_mpls_egress_count(return_path),
        "current_return_state_scales_with_flow_count": return_path.get("current_return_state_scales_with_flow_count"),
        "dynamic_satellite_link": _dynamic_satellite_link_count(manifest),
        **parameter_guards,
        "full_core_reinstall_count": fec_diff["full_core_reinstall_count"],
        "timing_mark_missing_count": timing_integrity["missing_timing_mark_count"],
        "timing_negative_duration_count": timing_integrity["negative_duration_count"],
        "timing_event_time_monotonic_mix_count": timing_integrity["mixed_event_time_monotonic_count"],
        "runtime_parameter_epoch_distinguishable": timing_integrity["runtime_parameter_epoch_distinguishable"],
    }
    timing_metrics = {
        "cold_startup_seconds": round(
            _num(startup.get("cold_start_before_first_epoch_execution_wall_seconds")),
            6,
        ),
        "initial_materialization_seconds": round(
            _num(initial_materialization.get("transition_wall_seconds")),
            6,
        ),
        "steady_runtime_epoch_seconds": _stats(_row_values(steady_runtime_rows, "semantic_total_epoch_wall_seconds") or _row_values(steady_runtime_rows, "transition_wall_seconds")),
        "steady_parameter_epoch_seconds": _stats(_row_values(parameter_rows, "semantic_total_epoch_wall_seconds") or _row_values(parameter_rows, "transition_wall_seconds")),
        "host_apply_seconds": _stats(_row_values(steady_runtime_rows, "semantic_runtime_host_apply_seconds") or _row_values(steady_runtime_rows, "host_operation_apply_wall_seconds")),
        "node_local_apply_seconds": _stats(_row_values(steady_runtime_rows, "semantic_runtime_node_local_apply_seconds") or _row_values(steady_runtime_rows, "node_local_apply_wall_seconds")),
        "scheduled_activation_lag_seconds": _stats(_row_values(steady_runtime_rows, "semantic_scheduled_activation_lag_seconds") or _row_values(steady_runtime_rows, "activation_lag_seconds")),
        "data_plane_ready_lag_seconds": _stats(_row_values(steady_runtime_rows, "semantic_data_plane_ready_lag_seconds") or _row_values(steady_runtime_rows, "derived_data_plane_ready_lag_seconds")),
        "post_data_plane_scheduler_blocking_seconds": _stats(_row_values(timing_rows, "semantic_scheduler_blocking_after_data_plane_ready_seconds") or _row_values(timing_rows, "scheduler_blocking_after_data_plane_ready_seconds")),
        "artifact_write_blocking_seconds": _stats(_row_values(timing_rows, "semantic_artifact_write_blocking_seconds") or _row_values(timing_rows, "artifact_write_blocking_seconds")),
        "artifact_write_total_seconds": round(sum(_row_values(timing_rows, "semantic_reporting_artifact_write_seconds") or _row_values(timing_rows, "artifact_write_blocking_seconds")), 6),
        "final_rollback_cleanup_seconds": cleanup["final_rollback_cleanup_seconds"],
    }
    workload = {
        "runtime_epoch_count": int(_num(continuous.get("prepared_runtime_epoch_count"), len(runtime_rows))),
        "parameter_epoch_count": int(_num(continuous.get("prepared_parameter_epoch_count"), len(parameter_rows))),
        "replenishment_count": int(_num(continuous.get("replenishment_segment_count") or _as_dict(manifest.get("prepared_bundle_runtime")).get("replenishment_event_count"))),
        **fec_diff,
    }
    artifact_cleanup = {
        **artifact_totals,
        "artifact_write_total_seconds": timing_metrics["artifact_write_total_seconds"],
        "residue_count_after_rollback": cleanup["residue_count_after_rollback"],
        "post_run_preflight_ready": post_assessment.get("ready"),
        "post_run_preflight_blocking_count": len(_as_list(post_assessment.get("blocking_reasons"))),
        "post_run_preflight_warning_count": len(_as_list(post_assessment.get("warning_reasons"))),
        "cleanup": cleanup,
    }
    return {
        "artifact_kind": "skyseed_regression_metrics",
        "schema_version": SCHEMA_VERSION,
        "baseline_id": BASELINE_ID,
        "source_commit": "e4756573cfa17ed3b911aca6267a2efda84fd28a",
        "run_id": manifest.get("run_id") or run_dir.name,
        "run_dir_name": run_dir.name,
        "route": {
            "orbit_input_mode": "planned-shell",
            "planned_shell_id": "S5",
            "max_satellites": int(_num(manifest.get("requested_satellite_count"), 172)),
            "ut_catalog": manifest.get("requested_ut_catalog"),
            "gateway_catalog": manifest.get("requested_gateway_catalog"),
            "zone_catalog": manifest.get("requested_zone_catalog"),
            "dynamic_satellite_link": hard_gates["dynamic_satellite_link"],
        },
        "artifacts_present": artifacts_present,
        "hard_gates": hard_gates,
        "timing_integrity": timing_integrity,
        "timing": timing_metrics,
        "workload_fec_diff": workload,
        "artifact_cleanup": artifact_cleanup,
        "info": {
            "total_wall_clock_seconds": _num(_as_dict(manifest.get("phase_timing")).get("total_wall_seconds")),
            "max_rss_mib": _max_resource_rss_mib(run_dir / "resource_usage.jsonl"),
            "timing_row_count": len(timing_rows),
        },
    }


def _max_resource_rss_mib(path: Path) -> float:
    if not path.exists():
        return 0.0
    max_rss = 0.0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        for key in ("rss_mib", "process_rss_mib", "self_rss_mib"):
            if key in payload:
                max_rss = max(max_rss, _num(payload.get(key)))
        self_process = _as_dict(payload.get("self_process"))
        if "vmrss_kib" in self_process:
            max_rss = max(max_rss, _num(self_process.get("vmrss_kib")) / 1024.0)
    return round(max_rss, 6)


def evaluate_regression(
    metrics: Dict[str, Any],
    baseline: Dict[str, Any],
) -> Dict[str, Any]:
    hard_failures: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    info: List[Dict[str, Any]] = []

    def hard(name: str, passed: bool, detail: str) -> None:
        if not passed:
            hard_failures.append({"metric": name, "detail": detail})

    gates = _as_dict(metrics.get("hard_gates"))
    hard("real_apply_success", gates.get("real_apply_success") is True, str(gates.get("real_apply_success")))
    hard("ping_success", gates.get("ping_success") is True, str(gates.get("ping_success")))
    hard("rollback_completed", gates.get("rollback_completed") is True, str(gates.get("rollback_completed")))
    hard("temporal_audit_pass", gates.get("temporal_audit_pass") is True, str(gates.get("temporal_audit_pass")))
    hard("gz_audit_mode", gates.get("gz_audit_mode") == "shared_gz_backbone", str(gates.get("gz_audit_mode")))
    hard("ut_mpls_egress_count", gates.get("ut_mpls_egress_count") == 0, str(gates.get("ut_mpls_egress_count")))
    hard(
        "current_return_state_scales_with_flow_count",
        gates.get("current_return_state_scales_with_flow_count") is False,
        str(gates.get("current_return_state_scales_with_flow_count")),
    )
    hard("dynamic_satellite_link", gates.get("dynamic_satellite_link") == 0, str(gates.get("dynamic_satellite_link")))
    for name in (
        "parameter_epoch_hidden_full_apply_count",
        "parameter_epoch_hidden_recovery_count",
        "full_core_reinstall_count",
        "timing_mark_missing_count",
        "timing_negative_duration_count",
        "timing_event_time_monotonic_mix_count",
    ):
        hard(name, int(_num(gates.get(name), -1)) == 0, str(gates.get(name)))
    hard(
        "runtime_parameter_epoch_distinguishable",
        gates.get("runtime_parameter_epoch_distinguishable") is True,
        str(gates.get("runtime_parameter_epoch_distinguishable")),
    )

    for name, present in _as_dict(metrics.get("artifacts_present")).items():
        hard(f"artifact_present:{name}", present is True, str(present))

    metric_timing = _as_dict(metrics.get("timing"))
    baseline_timing = _as_dict(baseline.get("timing"))
    _warn_ratio(
        warnings,
        metric_timing,
        baseline_timing,
        "steady_runtime_epoch_seconds",
        "p95",
        1.25,
    )
    _warn_ratio(warnings, metric_timing, baseline_timing, "host_apply_seconds", "p95", 1.25)
    _warn_scalar_ratio(
        warnings,
        metric_timing,
        baseline_timing,
        "final_rollback_cleanup_seconds",
        1.50,
    )
    metric_artifacts = _as_dict(metrics.get("artifact_cleanup"))
    baseline_artifacts = _as_dict(baseline.get("artifact_cleanup"))
    _warn_scalar_ratio(warnings, metric_artifacts, baseline_artifacts, "run_dir_size_bytes", 1.25)
    _warn_scalar_ratio(warnings, metric_artifacts, baseline_artifacts, "artifact_count", 1.25)

    for key in (
        "cold_startup_seconds",
        "initial_materialization_seconds",
        "final_rollback_cleanup_seconds",
    ):
        info.append({"metric": key, "value": metric_timing.get(key)})
    info.append({"metric": "max_rss_mib", "value": _as_dict(metrics.get("info")).get("max_rss_mib")})
    info.append({"metric": "run_dir_size_bytes", "value": metric_artifacts.get("run_dir_size_bytes")})

    return {
        "schema_version": "skyseed-regression-check-v1",
        "passed": not hard_failures,
        "hard_failures": hard_failures,
        "warnings": warnings,
        "info": info,
        "run_id": metrics.get("run_id"),
        "baseline_id": baseline.get("baseline_id"),
    }


def _warn_ratio(
    warnings: List[Dict[str, Any]],
    metrics: Dict[str, Any],
    baseline: Dict[str, Any],
    metric_name: str,
    stat_name: str,
    limit_ratio: float,
) -> None:
    current = _num(_as_dict(metrics.get(metric_name)).get(stat_name))
    expected = _num(_as_dict(baseline.get(metric_name)).get(stat_name))
    if expected > 0 and current > expected * limit_ratio:
        warnings.append(
            {
                "metric": f"{metric_name}.{stat_name}",
                "baseline": expected,
                "current": current,
                "limit_ratio": limit_ratio,
            }
        )


def _warn_scalar_ratio(
    warnings: List[Dict[str, Any]],
    metrics: Dict[str, Any],
    baseline: Dict[str, Any],
    metric_name: str,
    limit_ratio: float,
) -> None:
    current = _num(metrics.get(metric_name))
    expected = _num(baseline.get(metric_name))
    if expected > 0 and current > expected * limit_ratio:
        warnings.append(
            {
                "metric": metric_name,
                "baseline": expected,
                "current": current,
                "limit_ratio": limit_ratio,
            }
        )


def render_metrics_text(metrics: Dict[str, Any]) -> str:
    timing = _as_dict(metrics.get("timing"))
    workload = _as_dict(metrics.get("workload_fec_diff"))
    gates = _as_dict(metrics.get("hard_gates"))
    artifacts = _as_dict(metrics.get("artifact_cleanup"))
    lines = [
        "Skyseed regression metrics",
        f"  run_id={metrics.get('run_id')}",
        f"  schema={metrics.get('schema_version')}",
        "  hard_gates:",
    ]
    for key in sorted(gates):
        lines.append(f"    {key}={gates[key]}")
    lines.extend(
        [
            "  timing:",
            f"    cold_startup_seconds={timing.get('cold_startup_seconds')}",
            f"    initial_materialization_seconds={timing.get('initial_materialization_seconds')}",
            f"    steady_runtime_epoch_p95={_as_dict(timing.get('steady_runtime_epoch_seconds')).get('p95')}",
            f"    steady_parameter_epoch_p95={_as_dict(timing.get('steady_parameter_epoch_seconds')).get('p95')}",
            f"    host_apply_p95={_as_dict(timing.get('host_apply_seconds')).get('p95')}",
            f"    node_local_apply_p95={_as_dict(timing.get('node_local_apply_seconds')).get('p95')}",
            f"    final_rollback_cleanup_seconds={timing.get('final_rollback_cleanup_seconds')}",
            "  workload_fec_diff:",
            f"    runtime_epoch_count={workload.get('runtime_epoch_count')}",
            f"    parameter_epoch_count={workload.get('parameter_epoch_count')}",
            f"    replenishment_count={workload.get('replenishment_count')}",
            f"    changed_g_fec_count={workload.get('changed_g_fec_count')}",
            f"    changed_gz_fec_count={workload.get('changed_gz_fec_count')}",
            f"    operation_estimate_max={workload.get('operation_estimate_max')}",
            "  artifacts:",
            f"    run_dir_size_bytes={artifacts.get('run_dir_size_bytes')}",
            f"    artifact_count={artifacts.get('artifact_count')}",
            f"    residue_count_after_rollback={artifacts.get('residue_count_after_rollback')}",
        ]
    )
    return "\n".join(lines)


def render_check_text(report: Dict[str, Any]) -> str:
    lines = [
        "Skyseed regression check",
        f"  run_id={report.get('run_id')}",
        f"  baseline_id={report.get('baseline_id')}",
        f"  passed={report.get('passed')}",
        "  hard_failures:",
    ]
    failures = _as_list(report.get("hard_failures"))
    if failures:
        for item in failures:
            item = _as_dict(item)
            lines.append(f"    FAIL {item.get('metric')}: {item.get('detail')}")
    else:
        lines.append("    none")
    lines.append("  warnings:")
    warnings = _as_list(report.get("warnings"))
    if warnings:
        for item in warnings:
            item = _as_dict(item)
            lines.append(
                f"    WARN {item.get('metric')}: current={item.get('current')} baseline={item.get('baseline')} limit_ratio={item.get('limit_ratio')}"
            )
    else:
        lines.append("    none")
    lines.append("  info:")
    for item in _as_list(report.get("info")):
        item = _as_dict(item)
        lines.append(f"    {item.get('metric')}={item.get('value')}")
    return "\n".join(lines)
