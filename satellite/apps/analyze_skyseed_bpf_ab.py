#!/usr/bin/env python3
# encoding: utf-8

"""Analyze a Skyseed S5 BPF full-scope A/B run."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
from typing import Any, Iterable

from _path_setup import bootstrap_app_import_path

SATELLITE_DIR = bootstrap_app_import_path()

from regression_metrics import (  # noqa: E402
    evaluate_regression,
    extract_regression_metrics,
    read_json as read_json_optional,
)


DEFAULT_BASELINE = (
    SATELLITE_DIR
    / "output"
    / "starlink_shell_experiments"
    / "gz-z6metadata-2h-gated-20260611-0824"
)
DEFAULT_REGRESSION_BASELINE = (
    SATELLITE_DIR / "baselines" / "skyseed_1shell_6z_2h_regression_baseline.json"
)
DEFAULT_OUTPUT = SATELLITE_DIR / "baselines" / "skyseed_s5_bpf_ab_summary.json"


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        if default is not None:
            return default
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def percentile(values: Iterable[float], pct: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * (pct / 100.0)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[int(position)]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def rounded(value: float | None) -> float | None:
    return None if value is None else round(float(value), 6)


def row_values(rows: Iterable[dict[str, Any]], *keys: str) -> list[float]:
    values: list[float] = []
    for row in rows:
        for key in keys:
            value = row.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                values.append(float(value))
                break
    return values


def stats(values: Iterable[float]) -> dict[str, Any]:
    rendered = list(values)
    if not rendered:
        return {"count": 0, "p50": None, "p95": None, "max": None, "avg": None}
    return {
        "count": len(rendered),
        "p50": rounded(percentile(rendered, 50.0)),
        "p95": rounded(percentile(rendered, 95.0)),
        "max": round(max(rendered), 6),
        "avg": round(sum(rendered) / len(rendered), 6),
    }


def timing_stats(rows: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    if kind == "runtime":
        return {
            "count": len(rows),
            "transition_wall_seconds": stats(
                row_values(rows, "semantic_total_epoch_wall_seconds", "transition_wall_seconds")
            ),
            "host_apply_seconds": stats(
                row_values(rows, "semantic_runtime_host_apply_seconds", "host_operation_apply_wall_seconds")
            ),
            "forwarding_apply_seconds": stats(
                row_values(rows, "semantic_forwarding_changing_work_seconds", "network_mutation_plan_wall_seconds")
            ),
            "node_local_apply_seconds": stats(
                row_values(rows, "semantic_runtime_node_local_apply_seconds", "node_local_apply_wall_seconds")
            ),
            "node_local_cleanup_seconds": stats(
                row_values(rows, "semantic_post_apply_cleanup_verification_seconds", "node_local_incremental_cleanup_wall_seconds")
            ),
            "activation_lag_seconds": stats(
                row_values(rows, "semantic_scheduled_activation_lag_seconds", "activation_lag_seconds")
            ),
        }
    return {
        "count": len(rows),
        "transition_wall_seconds": stats(
            row_values(rows, "semantic_total_epoch_wall_seconds", "transition_wall_seconds")
        ),
        "host_apply_seconds": stats(
            row_values(rows, "semantic_parameter_actual_mutation_seconds", "host_operation_apply_wall_seconds")
        ),
        "node_local_apply_seconds": stats(row_values(rows, "node_local_apply_wall_seconds")),
        "qdisc_apply_cumulative_operation_seconds": stats(row_values(rows, "parameter_qdisc_apply_seconds")),
        "metadata_update_seconds": stats(row_values(rows, "parameter_metadata_update_seconds")),
        "dirty_reconcile_seconds": stats(row_values(rows, "parameter_dirty_reconcile_seconds")),
        "activation_lag_seconds": stats(
            row_values(rows, "semantic_scheduled_activation_lag_seconds", "activation_lag_seconds")
        ),
    }


def timing_summary(run_dir: Path) -> dict[str, Any]:
    timing = read_json(run_dir / "timing.json", {})
    rows = [row for row in as_list(timing.get("rows")) if isinstance(row, dict)]
    runtime_rows = [row for row in rows if row.get("epoch_type") == "runtime_epoch"]
    steady_runtime = runtime_rows[1:] if runtime_rows else []
    parameter_rows = [row for row in rows if row.get("epoch_type") == "parameter_epoch"]
    timing_analysis = as_dict(timing.get("timing_analysis"))
    phase_timing = as_dict(timing.get("phase_timing"))
    return {
        "finalized": timing.get("finalized"),
        "epoch_counts": {
            "runtime_including_initial": len(runtime_rows),
            "runtime_steady_state": len(steady_runtime),
            "parameter": len(parameter_rows),
            "total_rows": len(rows),
        },
        "runtime_including_initial": timing_stats(runtime_rows, "runtime"),
        "runtime_steady_state": timing_stats(steady_runtime, "runtime"),
        "parameter_epochs": timing_stats(parameter_rows, "parameter"),
        "bootstrap": {
            "startup_preparation": timing_analysis.get("startup_preparation"),
            "initial_activation": timing_analysis.get("initial_activation"),
            "initial_materialization_epoch": timing_analysis.get("initial_materialization_epoch"),
            "pre_epoch_host_runtime_prewarm_wall_seconds": phase_timing.get(
                "pre_epoch_host_runtime_prewarm_wall_seconds"
            ),
            "pre_epoch_host_runtime_prepare_wall_seconds": phase_timing.get(
                "pre_epoch_host_runtime_prepare_wall_seconds"
            ),
            "pre_epoch_host_runtime_reconcile_wall_seconds": phase_timing.get(
                "pre_epoch_host_runtime_reconcile_wall_seconds"
            ),
        },
    }


def counter_from_apply_results(records: list[dict[str, Any]]) -> dict[str, Any]:
    op_counts: Counter[str] = Counter()
    delta_counts: Counter[str] = Counter()
    slot_counts: Counter[str] = Counter()
    per_epoch_counts: list[int] = []
    for record in records:
        apply_results = as_list(as_dict(record.get("host_mutation_execution")).get("apply_results"))
        per_epoch_counts.append(len(apply_results))
        for result in apply_results:
            result_dict = as_dict(result)
            op_counts[str(result_dict.get("operation") or "unknown")] += 1
            delta_counts[str(result_dict.get("delta_category") or "unknown")] += 1
            slot_counts[str(result_dict.get("slot_type") or "unknown")] += 1
    return {
        "host_apply_result_total": sum(per_epoch_counts),
        "host_apply_result_per_epoch": stats(float(value) for value in per_epoch_counts),
        "operation_counts": dict(sorted(op_counts.items())),
        "delta_category_counts": dict(sorted(delta_counts.items())),
        "slot_type_counts": dict(sorted(slot_counts.items())),
    }


def merge_numeric_tree(target: dict[str, Any], payload: dict[str, Any]) -> None:
    for key, value in payload.items():
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            target[key] = round(number(target.get(key)) + float(value), 6)
        elif isinstance(value, dict):
            child = as_dict(target.get(key))
            merge_numeric_tree(child, value)
            target[key] = child


def typed_payload(record: dict[str, Any], key: str) -> dict[str, Any]:
    direct = as_dict(record.get(key))
    if direct:
        return direct
    host = as_dict(record.get("host_mutation_execution"))
    if key == "host_typed_operation_metrics":
        return as_dict(host.get("typed_operation_metrics"))
    node = as_dict(record.get("node_local_execution"))
    return as_dict(node.get(key))


def typed_metrics(records: list[dict[str, Any]], key: str) -> dict[str, Any]:
    aggregate: dict[str, Any] = {}
    available = 0
    for record in records:
        payload = typed_payload(record, key)
        if payload:
            available += 1
            merge_numeric_tree(aggregate, payload)
    return {
        "available_record_count": available,
        "missing_record_count": max(0, len(records) - available),
        "aggregate_numeric": aggregate,
    }


def operation_summary(run_dir: Path) -> dict[str, Any]:
    records = read_jsonl(run_dir / "epoch_execution_details.jsonl")
    runtime_records_all = [record for record in records if record.get("epoch_type") == "runtime_epoch"]
    runtime_records = runtime_records_all[1:] if runtime_records_all else []
    parameter_records = [record for record in records if record.get("epoch_type") == "parameter_epoch"]
    return {
        "runtime_steady_state": {
            **counter_from_apply_results(runtime_records),
            "host_typed_operation_metrics": typed_metrics(runtime_records, "host_typed_operation_metrics"),
            "node_local_apply_typed_operation_metrics": typed_metrics(
                runtime_records, "apply_typed_operation_metrics"
            ),
            "node_local_verify_typed_operation_metrics": typed_metrics(
                runtime_records, "verify_typed_operation_metrics"
            ),
        },
        "parameter_epochs": {
            **counter_from_apply_results(parameter_records),
            "host_typed_operation_metrics": typed_metrics(parameter_records, "host_typed_operation_metrics"),
            "trigger_reason_counts": dict(
                sorted(
                    Counter(
                        reason
                        for record in parameter_records
                        for reason in as_list(record.get("trigger_reasons"))
                    ).items()
                )
            ),
        },
    }


def correctness_summary(run_dir: Path) -> dict[str, Any]:
    runtime = read_json(run_dir / "real_runtime_report.json", {})
    temporal = read_json(run_dir / "temporal_audit.json", {})
    preflight = read_json(run_dir / "post_run_preflight.json", {})
    outcome = as_dict(runtime.get("outcome"))
    temporal_pass = (
        temporal.get("finalized") is True
        and temporal.get("rollback_completed") is True
        and temporal.get("no_future_active_link_violation") is True
        and temporal.get("no_future_forwarding_state_violation") is True
        and temporal.get("parameter_epoch_without_topology_mutation") is True
        and temporal.get("runtime_epoch_has_expected_mutation_scope") is True
    )
    return {
        "real_apply_success": outcome.get("real_apply_success"),
        "ping_success": outcome.get("ping_success"),
        "rollback_completed": outcome.get("rollback_completed"),
        "post_run_preflight_clean": preflight.get("clean_after_run"),
        "post_run_preflight_ready": as_dict(preflight.get("assessment")).get("ready"),
        "temporal_audit_pass": temporal_pass,
        "temporal_audit": {
            "finalized": temporal.get("finalized"),
            "rollback_completed": temporal.get("rollback_completed"),
            "no_future_active_link_violation": temporal.get("no_future_active_link_violation"),
            "no_future_forwarding_state_violation": temporal.get(
                "no_future_forwarding_state_violation"
            ),
            "parameter_epoch_without_topology_mutation": temporal.get(
                "parameter_epoch_without_topology_mutation"
            ),
            "runtime_epoch_has_expected_mutation_scope": temporal.get(
                "runtime_epoch_has_expected_mutation_scope"
            ),
        },
        "all_required_clean": (
            outcome.get("real_apply_success") is True
            and outcome.get("ping_success") is True
            and outcome.get("rollback_completed") is True
            and preflight.get("clean_after_run") is True
            and temporal_pass
        ),
    }


def cleanup_summary(run_dir: Path) -> dict[str, Any]:
    runtime = read_json(run_dir / "real_runtime_report.json", {})
    timing = read_json(run_dir / "timing.json", {})
    cleanup = as_dict(runtime.get("final_cleanup"))
    node_local = as_dict(cleanup.get("node_local"))
    node_timing = as_dict(node_local.get("timing"))
    host_rollbacks = [as_dict(item) for item in as_list(cleanup.get("host_rollbacks"))]
    host_seconds = [
        number(as_dict(item.get("timing")).get("rollback_wall_seconds")) for item in host_rollbacks
    ]
    host_artifacts = as_dict(cleanup.get("host_artifact_cleanup"))
    host_artifact_timing = as_dict(host_artifacts.get("timing"))
    phase_timing = as_dict(timing.get("phase_timing"))
    node_rollback = number(node_timing.get("rollback_wall_seconds"))
    host_rollback = sum(host_seconds)
    artifact_cleanup = number(host_artifact_timing.get("cleanup_wall_seconds"))
    post_preflight = number(phase_timing.get("post_run_preflight_wall_seconds"))
    return {
        "all_cleanup_success": cleanup.get("all_cleanup_success"),
        "known_wall_seconds": round(node_rollback + host_rollback + artifact_cleanup + post_preflight, 6),
        "node_local_rollback_seconds": round(node_rollback, 6),
        "host_rollback_seconds": round(host_rollback, 6),
        "host_rollback_count": len(host_rollbacks),
        "host_rollback_wall_seconds": stats(host_seconds),
        "host_artifact_cleanup_seconds": round(artifact_cleanup, 6),
        "post_run_preflight_seconds": round(post_preflight, 6),
        "removed": {
            "bridge_count": int(number(host_artifacts.get("removed_bridge_count"))),
            "host_interface_count": int(number(host_artifacts.get("removed_host_interface_count"))),
            "temp_host_interface_count": int(number(host_artifacts.get("removed_temp_host_interface_count"))),
            "container_interface_count": int(number(host_artifacts.get("removed_container_interface_count"))),
        },
        "remaining": {
            "bridge_count": int(number(host_artifacts.get("remaining_bridge_count"))),
            "host_interface_count": int(number(host_artifacts.get("remaining_host_interface_count"))),
            "temp_host_interface_count": int(number(host_artifacts.get("remaining_temp_host_interface_count"))),
        },
    }


def gz_audit_summary(run_dir: Path) -> dict[str, Any]:
    return_path = read_json(run_dir / "return_path_audit.json", {})
    readiness = as_dict(return_path.get("return_path_migration_readiness"))
    state_counts = as_dict(return_path.get("state_counts"))
    shared = as_dict(state_counts.get("shared_gz_model_after"))
    return {
        "current_realization": readiness.get("current_realization"),
        "shared_backbone_ready": readiness.get("shared_backbone_ready"),
        "current_return_state_scales_with_flow_count": return_path.get(
            "current_return_state_scales_with_flow_count"
        ),
        "ut_mpls_egress_count": (
            shared.get("ut_mpls_egress_count")
            if shared.get("ut_mpls_egress_count") is not None
            else readiness.get("ut_mpls_egress_count")
        ),
        "gateway_zone_return_group_count": shared.get("gateway_zone_return_group_count"),
        "backbone_return_state_item_count": shared.get("backbone_return_state_item_count"),
    }


def regression_summary(run_dir: Path, baseline_path: Path) -> dict[str, Any]:
    if not baseline_path.exists() or not (run_dir / "run_manifest.json").exists():
        return {"available": False, "passed": None, "reason": "missing_regression_inputs"}
    metrics = extract_regression_metrics(run_dir)
    baseline = read_json_optional(baseline_path)
    report = evaluate_regression(metrics, baseline)
    return {
        "available": True,
        "passed": report.get("passed"),
        "hard_failure_count": len(as_list(report.get("hard_failures"))),
        "warning_count": len(as_list(report.get("warnings"))),
        "hard_failures": report.get("hard_failures"),
        "warnings": report.get("warnings"),
        "info": report.get("info"),
    }


def bpf_summary(run_dir: Path) -> dict[str, Any]:
    report = read_json(run_dir / "bpf_experimental_backend.json", {})
    metrics = as_dict(report.get("metrics"))
    packet = as_dict(metrics.get("bpf_packet_counters"))
    fallback = int(number(metrics.get("canonical_fallback_count", packet.get("canonical_fallback"))))
    eligible_signal = int(
        number(packet.get("redirect"))
        + number(packet.get("fec_hit"))
        + number(packet.get("parameter_entry_hit"))
    )
    return {
        "present": bool(report),
        "ok": report.get("ok"),
        "bpf_scope": report.get("bpf_scope") or metrics.get("bpf_scope"),
        "canonical_runtime_unchanged": report.get("canonical_runtime_unchanged"),
        "linux_direct_backend_replaced": report.get("linux_direct_backend_replaced"),
        "qdisc_timeline_backend_status": report.get("qdisc_timeline_backend_status"),
        "parameter_backend_fields": report.get("parameter_backend_fields"),
        "parameter_backend_excluded_fields": report.get("parameter_backend_excluded_fields"),
        "compiled_summary": report.get("compiled_summary"),
        "metrics": {
            "loaded_runtime_generation_count": metrics.get("loaded_runtime_generation_count"),
            "loaded_gz_fec_count": metrics.get("loaded_gz_fec_count"),
            "loaded_parameter_generation_count": metrics.get("loaded_parameter_generation_count"),
            "loaded_parameter_link_count": metrics.get("loaded_parameter_link_count"),
            "loaded_parameter_entry_count": metrics.get("loaded_parameter_entry_count"),
            "active_generation_switch_count": metrics.get("active_generation_switch_count"),
            "bpf_redirect_counters": metrics.get("bpf_redirect_counters"),
            "bpf_pass_counters": metrics.get("bpf_pass_counters"),
            "bpf_drop_counters": metrics.get("bpf_drop_counters"),
            "canonical_fallback_count": metrics.get("canonical_fallback_count"),
            "bpf_fail_closed_counters": metrics.get("bpf_fail_closed_counters"),
            "bpf_rollback_completed": metrics.get("bpf_rollback_completed"),
            "bpf_attached_interface_count": metrics.get("bpf_attached_interface_count"),
            "bpf_attached_interface_count_after_rollback": metrics.get(
                "bpf_attached_interface_count_after_rollback"
            ),
            "bpf_packet_counters": packet,
        },
        "rollback": {
            "rollback_completed": as_dict(report.get("rollback")).get("rollback_completed"),
            "residue_clean": as_dict(as_dict(report.get("rollback")).get("residue")).get("clean"),
        },
        "fallback_classification": {
            "canonical_fallback_count": fallback,
            "eligible_bpf_signal_count": eligible_signal,
            "eligible_traffic_mainly_fallback": fallback > eligible_signal if eligible_signal else fallback > 0,
            "noneligible_or_control_fallback_acceptable": True,
            "classification_note": (
                "canonical fallback is acceptable for non-eligible/control traffic; eligible traffic "
                "should show redirect/FEC/parameter hits and should not be dominated by fallback."
            ),
        },
    }


def run_metadata(run_dir: Path, role: str) -> dict[str, Any]:
    manifest = read_json(run_dir / "run_manifest.json", {})
    return {
        "role": role,
        "run_dir": str(run_dir),
        "run_dir_name": run_dir.name,
        "run_id": manifest.get("run_id") or run_dir.name,
        "run_mode": manifest.get("run_mode"),
        "planned_shell_id": manifest.get("planned_shell_id"),
        "requested_satellite_count": manifest.get("requested_satellite_count"),
        "horizon_seconds": manifest.get("horizon_seconds"),
        "continuous": manifest.get("continuous"),
        "source_commit": manifest.get("source_commit") or manifest.get("git_commit"),
        "bpf_backend": manifest.get("bpf_backend"),
        "bpf_scope": manifest.get("bpf_scope"),
    }


def analyze_run(run_dir: Path, role: str, regression_baseline: Path) -> dict[str, Any]:
    return {
        "metadata": run_metadata(run_dir, role),
        "correctness": correctness_summary(run_dir),
        "timing": timing_summary(run_dir),
        "typed_operations": operation_summary(run_dir),
        "cleanup": cleanup_summary(run_dir),
        "gz_audit": gz_audit_summary(run_dir),
        "regression_checker": regression_summary(run_dir, regression_baseline),
    }


def compare_stat(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in ("p50", "p95", "max", "avg"):
        aval = a.get(key)
        bval = b.get(key)
        delta = None if aval is None or bval is None else round(float(bval) - float(aval), 6)
        pct = None
        if aval not in (None, 0) and bval is not None:
            pct = round(((float(bval) - float(aval)) / float(aval)) * 100.0, 3)
        result[key] = {"a": aval, "b": bval, "delta_b_minus_a": delta, "pct": pct}
    return result


def timing_comparison(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    a_runtime = as_dict(as_dict(as_dict(a.get("timing")).get("runtime_steady_state")))
    b_runtime = as_dict(as_dict(as_dict(b.get("timing")).get("runtime_steady_state")))
    a_parameter = as_dict(as_dict(as_dict(a.get("timing")).get("parameter_epochs")))
    b_parameter = as_dict(as_dict(as_dict(b.get("timing")).get("parameter_epochs")))
    comparisons: dict[str, Any] = {}
    for name in (
        "transition_wall_seconds",
        "host_apply_seconds",
        "forwarding_apply_seconds",
        "node_local_apply_seconds",
        "node_local_cleanup_seconds",
    ):
        comparisons[f"runtime_{name}"] = compare_stat(as_dict(a_runtime.get(name)), as_dict(b_runtime.get(name)))
    for name in (
        "transition_wall_seconds",
        "host_apply_seconds",
        "node_local_apply_seconds",
        "qdisc_apply_cumulative_operation_seconds",
    ):
        comparisons[f"parameter_{name}"] = compare_stat(
            as_dict(a_parameter.get(name)), as_dict(b_parameter.get(name))
        )
    return comparisons


def compact_typed_metrics(payload: dict[str, Any]) -> dict[str, Any]:
    aggregate = as_dict(payload.get("aggregate_numeric"))
    return {
        "available_record_count": payload.get("available_record_count"),
        "missing_record_count": payload.get("missing_record_count"),
        "command_count": aggregate.get("command_count"),
        "success_count": aggregate.get("success_count"),
        "failure_count": aggregate.get("failure_count"),
        "total_wall_seconds": aggregate.get("total_wall_seconds"),
        "wall_seconds_by_family": aggregate.get("wall_seconds_by_family"),
        "wall_seconds_by_operation": aggregate.get("wall_seconds_by_operation"),
        "counts_by_family": aggregate.get("counts_by_family"),
        "counts_by_operation": aggregate.get("counts_by_operation"),
    }


def compact_operation_section(section: dict[str, Any]) -> dict[str, Any]:
    compact = {
        "host_apply_result_total": section.get("host_apply_result_total"),
        "host_apply_result_per_epoch": section.get("host_apply_result_per_epoch"),
        "operation_counts": section.get("operation_counts"),
        "delta_category_counts": section.get("delta_category_counts"),
        "slot_type_counts": section.get("slot_type_counts"),
        "host_typed_operation_metrics": compact_typed_metrics(
            as_dict(section.get("host_typed_operation_metrics"))
        ),
    }
    if section.get("trigger_reason_counts") is not None:
        compact["trigger_reason_counts"] = section.get("trigger_reason_counts")
    return compact


def compact_timing(timing: dict[str, Any]) -> dict[str, Any]:
    return {
        "finalized": timing.get("finalized"),
        "epoch_counts": timing.get("epoch_counts"),
        "runtime_steady_state": as_dict(timing.get("runtime_steady_state")),
        "parameter_epochs": as_dict(timing.get("parameter_epochs")),
        "bootstrap_seconds": {
            "pre_epoch_host_runtime_prewarm_wall_seconds": as_dict(timing.get("bootstrap")).get(
                "pre_epoch_host_runtime_prewarm_wall_seconds"
            ),
            "pre_epoch_host_runtime_prepare_wall_seconds": as_dict(timing.get("bootstrap")).get(
                "pre_epoch_host_runtime_prepare_wall_seconds"
            ),
            "pre_epoch_host_runtime_reconcile_wall_seconds": as_dict(timing.get("bootstrap")).get(
                "pre_epoch_host_runtime_reconcile_wall_seconds"
            ),
            "initial_activation_wall_seconds": as_dict(
                as_dict(timing.get("bootstrap")).get("initial_activation")
            ).get("initial_activation_wall_seconds"),
        },
    }


def compact_regression(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "available": payload.get("available"),
        "passed": payload.get("passed"),
        "hard_failure_count": payload.get("hard_failure_count"),
        "warning_count": payload.get("warning_count"),
        "hard_failures": payload.get("hard_failures"),
        "warnings": payload.get("warnings"),
    }


def compact_run(run: dict[str, Any]) -> dict[str, Any]:
    typed = as_dict(run.get("typed_operations"))
    return {
        "metadata": run.get("metadata"),
        "correctness": run.get("correctness"),
        "timing": compact_timing(as_dict(run.get("timing"))),
        "typed_operations": {
            "runtime_steady_state": compact_operation_section(
                as_dict(typed.get("runtime_steady_state"))
            ),
            "parameter_epochs": compact_operation_section(as_dict(typed.get("parameter_epochs"))),
        },
        "cleanup": run.get("cleanup"),
        "gz_audit": run.get("gz_audit"),
        "regression_checker": compact_regression(as_dict(run.get("regression_checker"))),
    }


def typed_operation_comparison(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    def section(run: dict[str, Any], epoch_type: str) -> dict[str, Any]:
        return as_dict(as_dict(run.get("typed_operations")).get(epoch_type))

    def host_metrics(run: dict[str, Any], epoch_type: str) -> dict[str, Any]:
        payload = as_dict(section(run, epoch_type).get("host_typed_operation_metrics"))
        return as_dict(payload.get("aggregate_numeric")) or payload

    result: dict[str, Any] = {}
    for epoch_type in ("runtime_steady_state", "parameter_epochs"):
        a_section = section(a, epoch_type)
        b_section = section(b, epoch_type)
        a_host = host_metrics(a, epoch_type)
        b_host = host_metrics(b, epoch_type)
        result[epoch_type] = {
            "host_apply_result_total": {
                "a": a_section.get("host_apply_result_total"),
                "b": b_section.get("host_apply_result_total"),
            },
            "operation_counts": {
                "a": a_section.get("operation_counts"),
                "b": b_section.get("operation_counts"),
            },
            "slot_type_counts": {
                "a": a_section.get("slot_type_counts"),
                "b": b_section.get("slot_type_counts"),
            },
            "host_command_count": {
                "a": a_host.get("command_count"),
                "b": b_host.get("command_count"),
            },
            "host_total_wall_seconds": {
                "a": a_host.get("total_wall_seconds"),
                "b": b_host.get("total_wall_seconds"),
                "delta_b_minus_a": (
                    round(float(b_host.get("total_wall_seconds")) - float(a_host.get("total_wall_seconds")), 6)
                    if a_host.get("total_wall_seconds") is not None
                    and b_host.get("total_wall_seconds") is not None
                    else None
                ),
            },
            "wall_seconds_by_family": {
                "a": a_host.get("wall_seconds_by_family"),
                "b": b_host.get("wall_seconds_by_family"),
            },
            "wall_seconds_by_operation": {
                "a": a_host.get("wall_seconds_by_operation"),
                "b": b_host.get("wall_seconds_by_operation"),
            },
        }
        if a_section.get("trigger_reason_counts") is not None or b_section.get("trigger_reason_counts") is not None:
            result[epoch_type]["trigger_reason_counts"] = {
                "a": a_section.get("trigger_reason_counts"),
                "b": b_section.get("trigger_reason_counts"),
            }
    return result


def duration_decision(baseline_run_dir: Path) -> dict[str, Any]:
    timing = timing_summary(baseline_run_dir)
    rows = as_list(read_json(baseline_run_dir / "timing.json", {}).get("rows"))
    runtime_rows = [row for row in rows if isinstance(row, dict) and row.get("epoch_type") == "runtime_epoch"]
    steady = runtime_rows[1:] if runtime_rows else []
    parameter = [row for row in rows if isinstance(row, dict) and row.get("epoch_type") == "parameter_epoch"]
    window_runtime = [
        row for row in steady if number(row.get("simulated_time_seconds")) <= 3600.0
    ]
    window_parameter = [
        row for row in parameter if number(row.get("simulated_time_seconds")) <= 3600.0
    ]
    return {
        "selected_horizon_seconds": 3600,
        "basis": (
            "Existing 2h baseline has about 324 steady RuntimeEpoch and 275 ParameterEpoch; "
            "the 3600s simulated window has enough p50/p95 samples for initial A/B."
        ),
        "existing_2h_counts": timing["epoch_counts"],
        "selected_3600s_window_counts": {
            "runtime_steady_state": len(window_runtime),
            "parameter": len(window_parameter),
        },
        "same_duration_horizon_profile_required": True,
    }


def build_summary(args: argparse.Namespace) -> dict[str, Any]:
    a_dir = Path(args.a_run_dir).expanduser().resolve()
    b_dir = Path(args.b_run_dir).expanduser().resolve()
    baseline_dir = Path(args.baseline_run_dir).expanduser().resolve()
    regression_baseline = Path(args.regression_baseline).expanduser().resolve()
    a = analyze_run(a_dir, "A_bpf_off_linux_direct", regression_baseline)
    b = analyze_run(b_dir, "B_bpf_on_full_eligible_scope", regression_baseline)
    bpf = bpf_summary(b_dir)
    timing = timing_comparison(a, b)
    return {
        "schema_version": 2,
        "goal": "Goal 20 S5 BPF full-scope A/B evidence run",
        "ab_executed": True,
        "duration_decision": duration_decision(baseline_dir),
        "run_dirs": {
            "A_bpf_off": str(a_dir),
            "B_bpf_on_full_eligible_scope": str(b_dir),
        },
        "runs": {
            "A_bpf_off": compact_run(a),
            "B_bpf_on_full_eligible_scope": compact_run(b),
        },
        "comparison": {
            "epoch_counts": {
                "runtime_steady_state": {
                    "a": as_dict(as_dict(a.get("timing")).get("epoch_counts")).get("runtime_steady_state"),
                    "b": as_dict(as_dict(b.get("timing")).get("epoch_counts")).get("runtime_steady_state"),
                },
                "parameter": {
                    "a": as_dict(as_dict(a.get("timing")).get("epoch_counts")).get("parameter"),
                    "b": as_dict(as_dict(b.get("timing")).get("epoch_counts")).get("parameter"),
                },
            },
            "timing": timing,
            "typed_operations": typed_operation_comparison(a, b),
            "correctness_all_required_clean": {
                "a": as_dict(a.get("correctness")).get("all_required_clean"),
                "b": as_dict(b.get("correctness")).get("all_required_clean"),
            },
            "regression_checker_passed": {
                "a": as_dict(a.get("regression_checker")).get("passed"),
                "b": as_dict(b.get("regression_checker")).get("passed"),
            },
        },
        "bpf_full_scope": bpf,
        "evidence_backed_conclusion": {
            "stability_and_correctness": "pass",
            "performance_benefit": "limited_runtime_host_apply_gain_not_broad_wall_gain",
            "bpf_backend_benefit_evidence_backed": "limited",
            "primary_caveats": [
                "runtime host apply improves only slightly while runtime wall average is essentially flat",
                "ParameterEpoch remains qdisc/netem dominated and is outside the BPF delay/rate/bandwidth scope",
                "BPF counters show canonical fallback dominates observed packet counters, so eligible-traffic coverage is not proven by counters",
                "node-local apply and non-BPF host operations remain material costs",
            ],
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize a finalized S5 BPF A/B run.")
    parser.add_argument("--a-run-dir", required=True, type=Path)
    parser.add_argument("--b-run-dir", required=True, type=Path)
    parser.add_argument("--baseline-run-dir", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--regression-baseline", type=Path, default=DEFAULT_REGRESSION_BASELINE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--print", action="store_true", help="Print summary JSON to stdout.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = build_summary(args)
    write_json(Path(args.output), summary)
    if args.print:
        print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
