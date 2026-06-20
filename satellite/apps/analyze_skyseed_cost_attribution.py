#!/usr/bin/env python3
# encoding: utf-8

"""Attribute costs in an existing Skyseed canonical runtime run."""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence


SCHEMA_VERSION = "skyseed-cost-attribution-v1"
BASELINE_ID = "skyseed_1shell_s5_12ut6gw_6zone_2h"
BASELINE_COMMITS = {
    "goal_1_regression_metrics_guard": "7efef0f389f417cb6d1f42a77ae602fd901470f3",
    "goal_2_platform_abstraction_boundary": "7e6ec793f0719a0126b53b8675eb43f05c1bdbfe",
}


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
    return rows


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _is_num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _num(value: Any, default: float = 0.0) -> float:
    if _is_num(value):
        return float(value)
    return float(default)


def _nearest_rank(values: Sequence[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(quantile * len(ordered)) - 1))
    return float(ordered[index])


def _stats(values: Iterable[Any]) -> Dict[str, Any]:
    rendered = [float(value) for value in values if _is_num(value)]
    if not rendered:
        return {
            "count": 0,
            "p50_seconds": 0.0,
            "p95_seconds": 0.0,
            "max_seconds": 0.0,
            "average_seconds": 0.0,
            "sum_seconds": 0.0,
        }
    return {
        "count": len(rendered),
        "p50_seconds": round(_nearest_rank(rendered, 0.50), 6),
        "p95_seconds": round(_nearest_rank(rendered, 0.95), 6),
        "max_seconds": round(max(rendered), 6),
        "average_seconds": round(sum(rendered) / len(rendered), 6),
        "sum_seconds": round(sum(rendered), 6),
    }


def _count_stats(values: Iterable[Any]) -> Dict[str, Any]:
    stats = _stats(values)
    return {
        "count": stats["count"],
        "p50": stats["p50_seconds"],
        "p95": stats["p95_seconds"],
        "max": stats["max_seconds"],
        "average": stats["average_seconds"],
        "sum": stats["sum_seconds"],
    }


def _corr(xs: Sequence[Any], ys: Sequence[Any]) -> float | None:
    pairs = [
        (float(x), float(y))
        for x, y in zip(xs, ys)
        if _is_num(x) and _is_num(y)
    ]
    if len(pairs) < 2:
        return None
    mean_x = sum(x for x, _ in pairs) / len(pairs)
    mean_y = sum(y for _, y in pairs) / len(pairs)
    var_x = sum((x - mean_x) ** 2 for x, _ in pairs)
    var_y = sum((y - mean_y) ** 2 for _, y in pairs)
    if var_x == 0.0 or var_y == 0.0:
        return 0.0
    cov = sum((x - mean_x) * (y - mean_y) for x, y in pairs)
    return round(cov / math.sqrt(var_x * var_y), 6)


def _row_values(rows: Sequence[Dict[str, Any]], epoch_type: str, key: str) -> List[float]:
    return [
        float(row[key])
        for row in rows
        if row.get("epoch_type") == epoch_type and _is_num(row.get(key))
    ]


def _steady_runtime_rows(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    runtime_rows = [row for row in rows if row.get("epoch_type") == "runtime_epoch"]
    return runtime_rows[1:] if runtime_rows else []


def _operation_counter(records: Sequence[Dict[str, Any]], epoch_type: str, key: str) -> Dict[str, int]:
    counter: Counter[str] = Counter()
    for record in records:
        if record.get("epoch_type") != epoch_type:
            continue
        summary = _as_dict(record.get("host_operation_timing_summary"))
        values = _as_dict(summary.get(key))
        counter.update({str(name): int(_num(count)) for name, count in values.items()})
    return dict(sorted(counter.items()))


def _parameter_trigger_counts(records: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    counter: Counter[str] = Counter()
    for record in records:
        if record.get("epoch_type") != "parameter_epoch":
            continue
        for reason in _as_list(record.get("trigger_reasons")):
            counter[str(reason)] += 1
    return dict(sorted(counter.items()))


def _parameter_guard_counts(records: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    hidden_full_apply = 0
    hidden_recovery = 0
    for record in records:
        if record.get("epoch_type") != "parameter_epoch":
            continue
        if (
            record.get("escalated_to_runtime_epoch") is True
            or record.get("scheduled_topology_mutation") is True
            or int(_num(record.get("topology_mutation_count"))) > 0
            or int(_num(record.get("forwarding_mutation_count"))) > 0
            or bool(record.get("node_local_execution"))
        ):
            hidden_full_apply += 1
        recovery = _as_dict(record.get("parameter_refresh_recovery"))
        if record.get("parameter_refresh_recovery_used") is True or recovery.get("used") is True:
            hidden_recovery += 1
    return {
        "hidden_full_apply_count": hidden_full_apply,
        "hidden_recovery_count": hidden_recovery,
    }


def _diff_by_slice(run_dir: Path) -> Dict[str, Dict[str, Any]]:
    payload = _read_json(run_dir / "gz_update_diff_summary.json", {})
    diffs = _as_list(_as_dict(payload).get("diffs"))
    result: Dict[str, Dict[str, Any]] = {}
    for diff in diffs:
        if not isinstance(diff, dict):
            continue
        slice_id = _as_dict(diff.get("current_snapshot")).get("slice_id")
        if slice_id:
            result[str(slice_id)] = diff
    return result


def _fec_totals(diffs: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    changed_total = sum(int(_num(diff.get("changed_fec_group_count"))) for diff in diffs)
    changed_gz = sum(int(_num(diff.get("changed_gz_return_group_count"))) for diff in diffs)
    skipped_total = sum(int(_num(diff.get("skipped_unchanged_fec_group_count"))) for diff in diffs)
    return {
        "diff_count": len(diffs),
        "affected_nodes": {
            "total": sum(int(_num(diff.get("affected_node_count"))) for diff in diffs),
            "max": max((int(_num(diff.get("affected_node_count"))) for diff in diffs), default=0),
        },
        "operation_estimate": {
            "field": "estimated_lfib_route_operation_items",
            "total": sum(int(_num(diff.get("estimated_lfib_route_operation_items"))) for diff in diffs),
            "max": max(
                (int(_num(diff.get("estimated_lfib_route_operation_items"))) for diff in diffs),
                default=0,
            ),
        },
        "fec_groups": {
            "changed_total": changed_total,
            "changed_g_total": max(0, changed_total - changed_gz),
            "changed_gz_total": changed_gz,
            "skipped_unchanged_total": skipped_total,
            "skipped_g_vs_gz_split_available": False,
        },
    }


def _host_apply_correlation(
    runtime_records: Sequence[Dict[str, Any]],
    diff_by_slice: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    steady = list(runtime_records[1:]) if runtime_records else []
    host_apply = [
        _as_dict(record.get("timing")).get("host_operation_apply_wall_seconds")
        for record in steady
    ]
    mutation_count = [record.get("network_mutation_count") for record in steady]
    applied_operations = [record.get("applied_operations") for record in steady]

    def diff_value(record: Dict[str, Any], key: str) -> Any:
        return _as_dict(diff_by_slice.get(str(record.get("slice_id")))).get(key)

    return {
        "sample_count": len(steady),
        "note": (
            "network_mutation_count/applied_operations are per live epoch; FEC diff "
            "fields are slice-level summaries joined by slice_id."
        ),
        "host_apply_vs_network_mutation_count": _corr(host_apply, mutation_count),
        "host_apply_vs_applied_operations": _corr(host_apply, applied_operations),
        "host_apply_vs_affected_nodes_slice_joined": _corr(
            host_apply,
            [diff_value(record, "affected_node_count") for record in steady],
        ),
        "host_apply_vs_operation_estimate_slice_joined": _corr(
            host_apply,
            [diff_value(record, "estimated_lfib_route_operation_items") for record in steady],
        ),
        "host_apply_vs_changed_fec_slice_joined": _corr(
            host_apply,
            [diff_value(record, "changed_fec_group_count") for record in steady],
        ),
        "host_apply_vs_changed_gz_fec_slice_joined": _corr(
            host_apply,
            [diff_value(record, "changed_gz_return_group_count") for record in steady],
        ),
        "host_apply_vs_skipped_fec_slice_joined": _corr(
            host_apply,
            [diff_value(record, "skipped_unchanged_fec_group_count") for record in steady],
        ),
    }


def _long_tail_runtime_examples(
    runtime_records: Sequence[Dict[str, Any]],
    diff_by_slice: Dict[str, Dict[str, Any]],
    limit: int = 8,
) -> List[Dict[str, Any]]:
    steady = list(runtime_records[1:]) if runtime_records else []
    ordered = sorted(
        steady,
        key=lambda record: _num(_as_dict(record.get("timing")).get("host_operation_apply_wall_seconds")),
        reverse=True,
    )
    examples: List[Dict[str, Any]] = []
    for record in ordered[:limit]:
        timing = _as_dict(record.get("timing"))
        diff = _as_dict(diff_by_slice.get(str(record.get("slice_id"))))
        examples.append(
            {
                "sequence_index": record.get("sequence_index"),
                "slice_id": record.get("slice_id"),
                "host_apply_seconds": round(_num(timing.get("host_operation_apply_wall_seconds")), 6),
                "transition_seconds": round(_num(timing.get("transition_wall_seconds")), 6),
                "network_mutation_count": int(_num(record.get("network_mutation_count"))),
                "node_local_apply_seconds": round(_num(timing.get("node_local_apply_wall_seconds")), 6),
                "affected_node_count_slice_joined": diff.get("affected_node_count"),
                "operation_estimate_slice_joined": diff.get("estimated_lfib_route_operation_items"),
                "changed_fec_group_count_slice_joined": diff.get("changed_fec_group_count"),
                "changed_gz_return_group_count_slice_joined": diff.get("changed_gz_return_group_count"),
            }
        )
    return examples


def _cleanup_summary(run_dir: Path, timing: Dict[str, Any]) -> Dict[str, Any]:
    report = _read_json(run_dir / "real_runtime_report.json", {})
    cleanup = _as_dict(_as_dict(report).get("final_cleanup"))
    node_local = _as_dict(cleanup.get("node_local"))
    node_timing = _as_dict(node_local.get("timing"))
    node_results = [item for item in _as_list(node_local.get("rollback_results")) if isinstance(item, dict)]
    host_rollbacks = [
        item for item in _as_list(cleanup.get("host_rollbacks")) if isinstance(item, dict)
    ]
    host_artifacts = _as_dict(cleanup.get("host_artifact_cleanup"))
    host_artifact_timing = _as_dict(host_artifacts.get("timing"))
    phase_timing = _as_dict(timing.get("phase_timing"))

    host_rollback_seconds = [
        _num(_as_dict(item.get("timing")).get("rollback_wall_seconds"))
        for item in host_rollbacks
    ]
    host_operation_counts = [
        _num(_as_dict(item.get("outcome")).get("operation_count"))
        for item in host_rollbacks
    ]
    host_operation_wall = [
        _num(_as_dict(_as_dict(item.get("outcome")).get("operation_timing_summary")).get("total_wall_seconds"))
        for item in host_rollbacks
    ]
    node_wall = [_num(item.get("wall_seconds")) for item in node_results]
    node_command_counts = [int(_num(item.get("command_count"))) for item in node_results]
    node_phase_counts: Counter[str] = Counter(str(item.get("phase")) for item in node_results)

    node_rollback = _num(node_timing.get("rollback_wall_seconds"))
    host_rollback = sum(host_rollback_seconds)
    host_artifact_cleanup = _num(host_artifact_timing.get("cleanup_wall_seconds"))
    post_run_preflight = _num(phase_timing.get("post_run_preflight_wall_seconds"))
    known_total = node_rollback + host_rollback + host_artifact_cleanup + post_run_preflight
    return {
        "success": cleanup.get("all_cleanup_success"),
        "known_wall_seconds": round(known_total, 6),
        "node_local_rollback_seconds": round(node_rollback, 6),
        "node_local_rollback_batch_count": len(node_results),
        "node_local_rollback_session_cleanup_count": node_local.get("session_cleanup_count"),
        "node_local_rollback_command_count": sum(node_command_counts),
        "node_local_rollback_batch_wall_seconds": _stats(node_wall),
        "node_local_rollback_phase_counts": dict(sorted(node_phase_counts.items())),
        "host_rollback_seconds": round(host_rollback, 6),
        "host_rollback_count": len(host_rollbacks),
        "host_rollback_wall_seconds": _stats(host_rollback_seconds),
        "host_rollback_operation_count": int(sum(host_operation_counts)),
        "host_rollback_operation_wall_seconds": _stats(host_operation_wall),
        "host_artifact_cleanup_seconds": round(host_artifact_cleanup, 6),
        "host_artifact_cleanup_removed": {
            "bridge_count": int(_num(host_artifacts.get("removed_bridge_count"))),
            "host_interface_count": int(_num(host_artifacts.get("removed_host_interface_count"))),
            "temp_host_interface_count": int(_num(host_artifacts.get("removed_temp_host_interface_count"))),
            "container_interface_count": int(_num(host_artifacts.get("removed_container_interface_count"))),
        },
        "host_artifact_cleanup_remaining": {
            "bridge_count": int(_num(host_artifacts.get("remaining_bridge_count"))),
            "host_interface_count": int(_num(host_artifacts.get("remaining_host_interface_count"))),
            "temp_host_interface_count": int(_num(host_artifacts.get("remaining_temp_host_interface_count"))),
        },
        "post_run_preflight_seconds": round(post_run_preflight, 6),
        "deeper_breakdown_available": {
            "node_local_batch_wall_seconds": True,
            "node_local_command_count": True,
            "host_rollback_per_epoch_wall_seconds": True,
            "host_artifact_cleanup_removed_counts": True,
            "route_mpls_nat_per_command_timing": False,
            "host_artifact_cleanup_per_interface_timing": False,
        },
    }


def _backend_visibility() -> Dict[str, Any]:
    return {
        "qdisc_add_change_delete_count": {
            "status": "gap",
            "available": "qdisc_apply_wall_seconds and host slot operation counts",
            "missing": "tc add/change/delete command counts and per-command errors",
        },
        "linux_route_mpls_add_change_delete_count": {
            "status": "partial",
            "available": "node-local batch command_count and descriptions",
            "missing": "typed route/MPLS add/change/delete counters and per-command timing",
        },
        "veth_netns_link_operation_count": {
            "status": "partial",
            "available": "host slot operation counts and artifact cleanup removed interface/bridge counts",
            "missing": "typed veth/netns/link create/delete/change counters per epoch",
        },
        "subprocess_command_count": {
            "status": "partial",
            "available": "node-local batch command_count",
            "missing": "host-side subprocess/netlink command counts and per-command timing",
        },
        "docker_exec_batch_count": {
            "status": "partial",
            "available": "node-local apply/rollback batch records with node_local_batch=true",
            "missing": "explicit Docker exec transport count separated from node-local logical batch count",
        },
        "artifact_write_blocking": {
            "status": "partial",
            "available": "epoch artifact_write_blocking_seconds/audit_write_seconds marks",
            "missing": "large JSON serialization/write timing split by artifact",
        },
        "rollback_node_host_artifact_split": {
            "status": "available",
            "available": "node-local rollback, host rollback, host artifact cleanup, post-run preflight",
            "missing": "node command-type timing and host cleanup per-resource timing",
        },
    }


def build_cost_attribution(run_dir: Path) -> Dict[str, Any]:
    run_dir = run_dir.expanduser()
    manifest = _read_json(run_dir / "run_manifest.json", {})
    timing = _read_json(run_dir / "timing.json", {})
    rows = [row for row in _as_list(_as_dict(timing).get("rows")) if isinstance(row, dict)]
    records = _read_jsonl(run_dir / "epoch_execution_details.jsonl")
    runtime_records = [record for record in records if record.get("epoch_type") == "runtime_epoch"]
    parameter_records = [record for record in records if record.get("epoch_type") == "parameter_epoch"]
    steady_runtime = _steady_runtime_rows(rows)
    diff_by_slice = _diff_by_slice(run_dir)
    diffs = list(diff_by_slice.values())
    continuous = _as_dict(_as_dict(manifest).get("continuous_operation_summary"))
    timing_analysis = _as_dict(_as_dict(timing).get("timing_analysis"))
    initial_activation = _as_dict(timing_analysis.get("initial_activation"))
    initial_materialization = _as_dict(timing_analysis.get("initial_materialization_epoch"))

    runtime_host_apply = _row_values(steady_runtime, "runtime_epoch", "host_operation_apply_wall_seconds")
    runtime_network_plan = _row_values(steady_runtime, "runtime_epoch", "network_mutation_plan_wall_seconds")
    runtime_node_apply = _row_values(steady_runtime, "runtime_epoch", "node_local_apply_wall_seconds")
    runtime_cleanup = _row_values(steady_runtime, "runtime_epoch", "node_local_incremental_cleanup_wall_seconds")
    runtime_transition = _row_values(steady_runtime, "runtime_epoch", "transition_wall_seconds")
    runtime_audit = _row_values(steady_runtime, "runtime_epoch", "audit_write_seconds")
    runtime_artifact = _row_values(steady_runtime, "runtime_epoch", "artifact_write_blocking_seconds")

    parameter_transition = _row_values(rows, "parameter_epoch", "transition_wall_seconds")
    parameter_host_apply = _row_values(rows, "parameter_epoch", "host_operation_apply_wall_seconds")
    parameter_qdisc = _row_values(rows, "parameter_epoch", "parameter_qdisc_apply_seconds")
    parameter_metadata = _row_values(rows, "parameter_epoch", "parameter_metadata_update_seconds")
    parameter_dirty = _row_values(rows, "parameter_epoch", "parameter_dirty_reconcile_seconds")

    runtime_operation_counts = [record.get("network_mutation_count") for record in runtime_records]
    parameter_operation_counts = [record.get("network_mutation_count") for record in parameter_records]

    return {
        "schema_version": SCHEMA_VERSION,
        "baseline_id": BASELINE_ID,
        "baseline_commits": BASELINE_COMMITS,
        "run_source": {
            "run_dir_input": str(run_dir),
            "run_id": _as_dict(manifest).get("run_id") or run_dir.name,
            "baseline_description": (
                "planned-shell S5 full 1-shell + 12UT/6GW valid6 + "
                "6-zone metadata catalog + event-time RuntimeEpoch/ParameterEpoch + "
                "G/Z FEC diff + direct backend dynamic_satellite_link=0"
            ),
        },
        "epoch_distribution": {
            "runtime_epoch_count": len(runtime_records),
            "parameter_epoch_count": len(parameter_records),
            "replenishment_count": continuous.get("replenishment_segment_count"),
            "initial_activation_and_materialization_seconds": {
                "bucket_note": (
                    "initial_activation_wall_seconds is cold start plus first runtime "
                    "execution; Goal 1 initial_materialization_seconds is the first "
                    "runtime epoch transition only."
                ),
                "initial_activation_wall_seconds": initial_activation.get("initial_activation_wall_seconds"),
                "cold_start_before_first_epoch_execution_wall_seconds": initial_activation.get(
                    "cold_start_before_first_epoch_execution_wall_seconds"
                ),
                "first_runtime_epoch_transition_wall_seconds": initial_activation.get(
                    "first_runtime_epoch_transition_wall_seconds"
                ),
                "first_runtime_epoch_network_mutation_plan_wall_seconds": initial_materialization.get(
                    "network_mutation_plan_wall_seconds"
                ),
                "first_runtime_epoch_node_local_apply_wall_seconds": initial_materialization.get(
                    "node_local_apply_wall_seconds"
                ),
                "goal_1_initial_materialization_seconds": initial_materialization.get(
                    "transition_wall_seconds"
                ),
            },
            "steady_runtime_transition_seconds": _stats(runtime_transition),
            "steady_parameter_transition_seconds": _stats(parameter_transition),
        },
        "runtime_epoch_cost": {
            "host_apply_seconds": _stats(runtime_host_apply),
            "node_local_apply_seconds": _stats(runtime_node_apply),
            "node_local_incremental_cleanup_seconds": _stats(runtime_cleanup),
            "network_mutation_plan_seconds": _stats(runtime_network_plan),
            "audit_write_seconds": _stats(runtime_audit),
            "artifact_write_blocking_seconds": _stats(runtime_artifact),
            "operation_count": _count_stats(runtime_operation_counts),
            "host_operation_counts": _operation_counter(records, "runtime_epoch", "operation_counts"),
            "host_delta_category_counts": _operation_counter(records, "runtime_epoch", "delta_category_counts"),
            "host_slot_type_counts": _operation_counter(records, "runtime_epoch", "slot_type_counts"),
            "fec_diff": _fec_totals(diffs),
            "host_apply_correlation": _host_apply_correlation(runtime_records, diff_by_slice),
            "host_apply_long_tail_examples": _long_tail_runtime_examples(runtime_records, diff_by_slice),
            "fec_diff_scope_note": (
                "G/Z FEC diff artifacts are slice-level summaries for 25 slices; live "
                "RuntimeEpoch records repeat slice_ids across replenishment."
            ),
        },
        "parameter_epoch_cost": {
            "transition_seconds": _stats(parameter_transition),
            "host_apply_seconds": _stats(parameter_host_apply),
            "qdisc_apply_cumulative_operation_seconds": _stats(parameter_qdisc),
            "metadata_update_seconds": _stats(parameter_metadata),
            "dirty_reconcile_seconds": _stats(parameter_dirty),
            "changed_parameter_link_count": {
                "total": int(sum(_num(value) for value in parameter_operation_counts)),
                "max_per_epoch": int(max((_num(value) for value in parameter_operation_counts), default=0.0)),
                "per_epoch": _count_stats(parameter_operation_counts),
            },
            "host_operation_counts": _operation_counter(records, "parameter_epoch", "operation_counts"),
            "host_delta_category_counts": _operation_counter(records, "parameter_epoch", "delta_category_counts"),
            "host_slot_type_counts": _operation_counter(records, "parameter_epoch", "slot_type_counts"),
            "trigger_reason_counts": _parameter_trigger_counts(records),
            "hidden_full_apply_or_recovery": _parameter_guard_counts(records),
            "timing_scope_note": (
                "transition_seconds is per ParameterEpoch wall time; "
                "qdisc_apply_cumulative_operation_seconds sums qdisc_apply_wall_seconds "
                "over update_slot_parameters operations in that epoch, so it is not an "
                "additive wall-clock sub-bucket and can exceed transition wall time."
            ),
            "parameter_change_category_note": (
                "Per-live-epoch trigger_reasons distinguish routing_cost and bandwidth "
                "threshold triggers; delay/loss category counters are only present in "
                "the compact initial-horizon parameter summary."
            ),
        },
        "rollback_cleanup": _cleanup_summary(run_dir, timing),
        "backend_operation_visibility": _backend_visibility(),
        "optimization_priority": {
            "netlink_batch": {
                "priority": "high",
                "evidence": (
                    "RuntimeEpoch steady host apply p95/max and moderate correlation with "
                    "per-epoch operation counts point at host mutation batching."
                ),
                "missing_metrics": "typed host netlink/veth/qdisc command counts and per-command timing",
            },
            "qdisc_parameter_epoch": {
                "priority": "medium_high",
                "evidence": (
                    "ParameterEpoch visible mutation cost is mostly cumulative qdisc "
                    "operation timing, but transition-wall p95 is much smaller than "
                    "RuntimeEpoch and rollback."
                ),
                "missing_metrics": "tc add/change/delete count and netlink-vs-subprocess split",
            },
            "mpls_gz_bpf_shadow": {
                "priority": "medium",
                "evidence": (
                    "Node-local apply and cleanup are meaningful but below host apply in "
                    "steady RuntimeEpoch; node-local rollback is very large."
                ),
                "missing_metrics": "route/MPLS/NAT typed apply counts, per-command timing, Docker exec count",
            },
            "rollback_cleanup": {
                "priority": "high",
                "evidence": (
                    "Final cleanup is thousands of seconds and is split enough to show "
                    "large node-local and host rollback components."
                ),
                "missing_metrics": "node command-type timing and host artifact cleanup per-resource timing",
            },
        },
    }


def render_text(report: Dict[str, Any]) -> str:
    epoch = report["epoch_distribution"]
    runtime = report["runtime_epoch_cost"]
    parameter = report["parameter_epoch_cost"]
    cleanup = report["rollback_cleanup"]
    corr = runtime["host_apply_correlation"]
    lines = [
        f"Skyseed cost attribution: {report['run_source']['run_id']}",
        f"schema={report['schema_version']} baseline={report['baseline_id']}",
        "",
        "Epoch distribution",
        f"  RuntimeEpoch count: {epoch['runtime_epoch_count']}",
        f"  ParameterEpoch count: {epoch['parameter_epoch_count']}",
        f"  Replenishment count: {epoch['replenishment_count']}",
        (
            "  Initial activation wall: "
            f"{epoch['initial_activation_and_materialization_seconds']['initial_activation_wall_seconds']}s "
            "(first runtime transition "
            f"{epoch['initial_activation_and_materialization_seconds']['first_runtime_epoch_transition_wall_seconds']}s)"
        ),
        (
            "  Steady RuntimeEpoch transition p50/p95/max: "
            f"{epoch['steady_runtime_transition_seconds']['p50_seconds']}/"
            f"{epoch['steady_runtime_transition_seconds']['p95_seconds']}/"
            f"{epoch['steady_runtime_transition_seconds']['max_seconds']}s"
        ),
        (
            "  ParameterEpoch transition p50/p95/max: "
            f"{epoch['steady_parameter_transition_seconds']['p50_seconds']}/"
            f"{epoch['steady_parameter_transition_seconds']['p95_seconds']}/"
            f"{epoch['steady_parameter_transition_seconds']['max_seconds']}s"
        ),
        f"  Final rollback/cleanup known wall: {cleanup['known_wall_seconds']}s",
        "",
        "RuntimeEpoch cost",
        (
            "  Host apply p50/p95/max: "
            f"{runtime['host_apply_seconds']['p50_seconds']}/"
            f"{runtime['host_apply_seconds']['p95_seconds']}/"
            f"{runtime['host_apply_seconds']['max_seconds']}s"
        ),
        (
            "  Node-local apply p50/p95/max: "
            f"{runtime['node_local_apply_seconds']['p50_seconds']}/"
            f"{runtime['node_local_apply_seconds']['p95_seconds']}/"
            f"{runtime['node_local_apply_seconds']['max_seconds']}s"
        ),
        (
            "  Node-local incremental cleanup p50/p95/max: "
            f"{runtime['node_local_incremental_cleanup_seconds']['p50_seconds']}/"
            f"{runtime['node_local_incremental_cleanup_seconds']['p95_seconds']}/"
            f"{runtime['node_local_incremental_cleanup_seconds']['max_seconds']}s"
        ),
        (
            "  Network mutation plan/apply p50/p95/max: "
            f"{runtime['network_mutation_plan_seconds']['p50_seconds']}/"
            f"{runtime['network_mutation_plan_seconds']['p95_seconds']}/"
            f"{runtime['network_mutation_plan_seconds']['max_seconds']}s"
        ),
        (
            "  Operation count total/max: "
            f"{runtime['operation_count']['sum']}/{runtime['operation_count']['max']}"
        ),
        (
            "  FEC diff changed G/GZ/skipped totals: "
            f"{runtime['fec_diff']['fec_groups']['changed_g_total']}/"
            f"{runtime['fec_diff']['fec_groups']['changed_gz_total']}/"
            f"{runtime['fec_diff']['fec_groups']['skipped_unchanged_total']}"
        ),
        (
            "  host_apply corr with ops/affected/estimate/changed_fec: "
            f"{corr['host_apply_vs_network_mutation_count']}/"
            f"{corr['host_apply_vs_affected_nodes_slice_joined']}/"
            f"{corr['host_apply_vs_operation_estimate_slice_joined']}/"
            f"{corr['host_apply_vs_changed_fec_slice_joined']}"
        ),
        "",
        "ParameterEpoch cost",
        (
            "  Apply p50/p95/max: "
            f"{parameter['transition_seconds']['p50_seconds']}/"
            f"{parameter['transition_seconds']['p95_seconds']}/"
            f"{parameter['transition_seconds']['max_seconds']}s"
        ),
        (
            "  Qdisc cumulative operation p50/p95/max: "
            f"{parameter['qdisc_apply_cumulative_operation_seconds']['p50_seconds']}/"
            f"{parameter['qdisc_apply_cumulative_operation_seconds']['p95_seconds']}/"
            f"{parameter['qdisc_apply_cumulative_operation_seconds']['max_seconds']}s"
        ),
        (
            "  Changed parameter links total/max: "
            f"{parameter['changed_parameter_link_count']['total']}/"
            f"{parameter['changed_parameter_link_count']['max_per_epoch']}"
        ),
        f"  Trigger reasons: {parameter['trigger_reason_counts']}",
        f"  Hidden full apply/recovery: {parameter['hidden_full_apply_or_recovery']}",
        "",
        "Rollback/cleanup",
        f"  Node-local rollback: {cleanup['node_local_rollback_seconds']}s",
        f"  Host rollback: {cleanup['host_rollback_seconds']}s across {cleanup['host_rollback_count']} records",
        f"  Host artifact cleanup: {cleanup['host_artifact_cleanup_seconds']}s",
        f"  Post-run preflight: {cleanup['post_run_preflight_seconds']}s",
        "",
        "Instrumentation gaps",
        "  Missing typed qdisc add/change/delete counts and per-command timing.",
        "  Missing typed route/MPLS/NAT add/change/delete counts and per-command timing.",
        "  Missing host-side subprocess/netlink command counts and artifact write split by file.",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Analyze cost attribution from an existing Skyseed 2h canonical run."
    )
    parser.add_argument("--run-dir", required=True, help="Path to the finalized run directory.")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--output", help="Optional path for compact JSON output.")
    args = parser.parse_args()

    report = build_cost_attribution(Path(args.run_dir))
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.format == "json":
        print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    else:
        print(render_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
