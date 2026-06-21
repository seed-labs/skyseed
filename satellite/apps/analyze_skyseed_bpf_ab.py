#!/usr/bin/env python3
# encoding: utf-8

"""Analyze Skyseed S5 BPF A/B readiness and compact run metrics."""

from __future__ import annotations

import argparse
import inspect
import json
import math
import re
from pathlib import Path
from typing import Any, Iterable

from _path_setup import bootstrap_app_import_path

SATELLITE_DIR = bootstrap_app_import_path()

from bpf_backend import compile_from_run_dir  # noqa: E402
import bpf_dataplane_canary as bpf_live  # noqa: E402


DEFAULT_BASELINE = (
    SATELLITE_DIR
    / "output"
    / "starlink_shell_experiments"
    / "gz-z6metadata-2h-gated-20260611-0824"
)
DEFAULT_OUTPUT = SATELLITE_DIR / "baselines" / "skyseed_s5_bpf_ab_summary.json"
DEFAULT_BPF_SOURCE = SATELLITE_DIR / "experimental" / "bpf" / "skyseed_tc_dataplane_canary.c"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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


def round_or_none(value: float | None) -> float | None:
    return None if value is None else round(float(value), 6)


def numeric_values(rows: list[dict[str, Any]], key: str) -> list[float]:
    values: list[float] = []
    for row in rows:
        value = row.get(key)
        if value is None:
            continue
        values.append(float(value))
    return values


def stats(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    values = numeric_values(rows, key)
    if not values:
        return {"count": 0, "p50": None, "p95": None, "max": None, "avg": None}
    return {
        "count": len(values),
        "p50": round_or_none(percentile(values, 50.0)),
        "p95": round_or_none(percentile(values, 95.0)),
        "max": round(max(values), 6),
        "avg": round(sum(values) / len(values), 6),
    }


def epoch_family_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "count": len(rows),
        "transition_wall_seconds": stats(rows, "transition_wall_seconds"),
        "host_apply_seconds": stats(rows, "host_operation_apply_wall_seconds"),
        "node_local_apply_seconds": stats(rows, "node_local_apply_wall_seconds"),
        "network_mutation_seconds": stats(rows, "network_mutation_plan_wall_seconds"),
        "parameter_qdisc_apply_seconds": stats(rows, "parameter_qdisc_apply_seconds"),
        "cleanup_verification_seconds": stats(rows, "node_local_incremental_cleanup_wall_seconds"),
        "activation_lag_seconds": stats(rows, "activation_lag_seconds"),
    }


def duration_windows(
    *,
    steady_runtime_rows: list[dict[str, Any]],
    parameter_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = []
    requested_seconds = [1800.0, 2700.0, 3600.0, 5400.0, 7140.0]
    for seconds in requested_seconds:
        runtime_rows = [
            row
            for row in steady_runtime_rows
            if float(row.get("simulated_time_seconds") or 0.0) <= seconds
        ]
        parameter_subset = [
            row
            for row in parameter_rows
            if float(row.get("simulated_time_seconds") or 0.0) <= seconds
        ]
        windows.append(
            {
                "simulated_seconds": int(seconds),
                "runtime_epoch_count": len(runtime_rows),
                "parameter_epoch_count": len(parameter_subset),
                "runtime_transition_wall_seconds": stats(runtime_rows, "transition_wall_seconds"),
                "runtime_host_apply_seconds": stats(runtime_rows, "host_operation_apply_wall_seconds"),
                "parameter_transition_wall_seconds": stats(parameter_subset, "transition_wall_seconds"),
                "parameter_host_apply_seconds": stats(parameter_subset, "host_operation_apply_wall_seconds"),
            }
        )
    return windows


def operation_metrics(run_dir: Path) -> dict[str, Any]:
    details_path = run_dir / "epoch_execution_details.jsonl"
    host_operation_counts: dict[str, int] = {}
    host_apply_result_counts: list[int] = []
    epoch_counts: dict[str, int] = {}
    if not details_path.exists():
        return {
            "epoch_counts": epoch_counts,
            "host_operation_counts": host_operation_counts,
            "host_apply_result_total": 0,
            "host_apply_result_max_per_epoch": 0,
        }
    with details_path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            epoch_type = str(record.get("epoch_type") or record.get("original_epoch_type") or "unknown")
            epoch_counts[epoch_type] = epoch_counts.get(epoch_type, 0) + 1
            apply_results = (
                (record.get("host_mutation_execution") or {}).get("apply_results") or []
            )
            host_apply_result_counts.append(len(apply_results))
            for result in apply_results:
                if isinstance(result, dict):
                    operation = str(result.get("operation") or "unknown")
                    host_operation_counts[operation] = host_operation_counts.get(operation, 0) + 1
    return {
        "epoch_counts": dict(sorted(epoch_counts.items())),
        "host_operation_counts": dict(sorted(host_operation_counts.items())),
        "host_apply_result_total": sum(host_apply_result_counts),
        "host_apply_result_max_per_epoch": max(host_apply_result_counts)
        if host_apply_result_counts
        else 0,
    }


def c_map_capacities(source_path: Path) -> dict[str, int]:
    source = source_path.read_text(encoding="utf-8")
    capacities: dict[str, int] = {}
    pattern = re.compile(
        r'struct bpf_elf_map SEC\("maps"\)\s+(?P<name>\w+)\s*=\s*\{(?P<body>.*?)\};',
        re.DOTALL,
    )
    for match in pattern.finditer(source):
        body = match.group("body")
        max_elem = re.search(r"\.max_elem\s*=\s*(?P<value>\d+)", body)
        if max_elem:
            capacities[match.group("name")] = int(max_elem.group("value"))
    return capacities


def source_line(function: Any) -> int:
    try:
        _, line_number = inspect.getsourcelines(function)
        return int(line_number)
    except OSError:
        return 0


def bpf_scope_readiness(run_dir: Path, bpf_source: Path) -> dict[str, Any]:
    compiled = compile_from_run_dir(run_dir, input_mode="s5_bpf_ab_readiness")
    compact = dict(compiled.get("compact_summary") or {})
    capacities = c_map_capacities(bpf_source)
    selector = bpf_live.normalize_selector({"max_scope_entries": 999999})
    required = {
        "runtime_generation_count": int(compact.get("runtime_generation_count") or 0),
        "gz_fec_entry_count": int(compact.get("gz_fec_entry_count") or 0),
        "parameter_generation_count": int(compact.get("parameter_generation_count") or 0),
        "parameter_link_count": int(compact.get("parameter_link_count") or 0),
        "parameter_entry_count": int(compact.get("parameter_entry_count") or 0),
    }
    live_limits = {
        "selector_max_scope_entries_after_normalize": int(selector["max_scope_entries"]),
        "generation_status_max_elem": int(capacities.get("generation_status", 0)),
        "fec_forwarding_max_elem": int(capacities.get("fec_forwarding", 0)),
        "param_status_max_elem": int(capacities.get("param_status", 0)),
        "parameter_values_max_elem": int(capacities.get("parameter_values", 0)),
    }
    blockers = []
    if required["gz_fec_entry_count"] > live_limits["fec_forwarding_max_elem"]:
        blockers.append("live_fec_forwarding_map_capacity_below_compiled_gz_fec_scope")
    if required["parameter_generation_count"] > live_limits["param_status_max_elem"]:
        blockers.append("live_parameter_generation_capacity_below_compiled_parameter_scope")
    if required["parameter_entry_count"] > live_limits["parameter_values_max_elem"]:
        blockers.append("live_parameter_values_map_capacity_below_compiled_parameter_scope")
    if required["parameter_link_count"] > live_limits["selector_max_scope_entries_after_normalize"]:
        blockers.append("live_selector_capacity_below_parameter_link_scope")
    if compact.get("bpf_scope") == "semantic_intent_canary_subset":
        blockers.append("live_backend_scope_is_canary_subset")
    return {
        "ready_for_full_eligible_scope": not blockers,
        "blockers": blockers,
        "allowed_non_bpf_scope": {
            "delay_rate_bandwidth": "qdisc_netem",
            "qdisc_timeline_backend_status": compact.get("qdisc_timeline_backend_status"),
        },
        "compiled_full_scope_required": required,
        "current_live_limits": live_limits,
        "compiled_compact_summary": compact,
        "source_evidence": {
            "python_backend": "satellite/bpf_dataplane_canary.py",
            "c_backend": "satellite/experimental/bpf/skyseed_tc_dataplane_canary.c",
            "normalize_selector_line": source_line(bpf_live.normalize_selector),
            "select_experimental_scope_line": source_line(bpf_live.select_experimental_scope),
            "load_canary_maps_line": source_line(bpf_live.load_canary_maps),
        },
    }


def correctness_summary(run_dir: Path) -> dict[str, Any]:
    runtime = read_json(run_dir / "real_runtime_report.json")
    temporal_audit = read_json(run_dir / "temporal_audit.json")
    preflight = read_json(run_dir / "post_run_preflight.json")
    outcome = dict(runtime.get("outcome") or {})
    return {
        "real_apply_success": outcome.get("real_apply_success"),
        "ping_success": outcome.get("ping_success"),
        "rollback_completed": outcome.get("rollback_completed"),
        "post_run_preflight_clean": preflight.get("clean_after_run"),
        "temporal_audit": {
            "rollback_completed": temporal_audit.get("rollback_completed"),
            "no_future_active_link_violation": temporal_audit.get("no_future_active_link_violation"),
            "no_future_forwarding_state_violation": temporal_audit.get(
                "no_future_forwarding_state_violation"
            ),
            "parameter_epoch_without_topology_mutation": temporal_audit.get(
                "parameter_epoch_without_topology_mutation"
            ),
            "runtime_epoch_has_expected_mutation_scope": temporal_audit.get(
                "runtime_epoch_has_expected_mutation_scope"
            ),
        },
    }


def baseline_analysis(run_dir: Path) -> dict[str, Any]:
    timing = read_json(run_dir / "timing.json")
    rows = list(timing.get("rows") or [])
    runtime_rows = [row for row in rows if row.get("epoch_type") == "runtime_epoch"]
    steady_runtime_rows = runtime_rows[1:] if runtime_rows else []
    parameter_rows = [row for row in rows if row.get("epoch_type") == "parameter_epoch"]
    timing_analysis = dict(timing.get("timing_analysis") or {})
    return {
        "run_dir": str(run_dir),
        "baseline_role": "existing_2h_duration_decision_source",
        "timing_analysis": {
            "startup_preparation": timing_analysis.get("startup_preparation"),
            "initial_activation": timing_analysis.get("initial_activation"),
            "first_runtime_epoch": timing_analysis.get("first_runtime_epoch"),
        },
        "epoch_counts": {
            "total_rows": len(rows),
            "runtime_including_initial": len(runtime_rows),
            "runtime_steady_state": len(steady_runtime_rows),
            "parameter": len(parameter_rows),
        },
        "runtime_including_initial": epoch_family_metrics(runtime_rows),
        "runtime_steady_state": epoch_family_metrics(steady_runtime_rows),
        "parameter_epochs": epoch_family_metrics(parameter_rows),
        "duration_windows": duration_windows(
            steady_runtime_rows=steady_runtime_rows,
            parameter_rows=parameter_rows,
        ),
        "operation_metrics": operation_metrics(run_dir),
        "correctness": correctness_summary(run_dir),
    }


def duration_decision(baseline: dict[str, Any]) -> dict[str, Any]:
    windows = list(baseline.get("duration_windows") or [])
    selected = next(
        (window for window in windows if int(window.get("simulated_seconds") or 0) == 3600),
        windows[-1] if windows else {},
    )
    return {
        "if_full_scope_backend_available": {
            "recommended_same_window_for_a_and_b": "3600s simulated horizon",
            "acceptable_shorter_smoke_floor": "2700s simulated horizon",
            "minimum_epoch_basis": ">=100 steady RuntimeEpoch and >=90 ParameterEpoch for p50/p95",
            "preferred_epoch_basis": "about 155 steady RuntimeEpoch and 145 ParameterEpoch at 3600s",
            "selected_window_metrics": selected,
        },
        "does_not_need_near_2h_for_initial_ab": True,
        "rationale": [
            "The first RuntimeEpoch is initial materialization and is excluded from steady-state.",
            "The existing 2h baseline has 324 steady RuntimeEpoch and 275 ParameterEpoch samples.",
            "At 2700-3600s the p50/p95 shape is already close enough for an initial A/B p50/p95 comparison.",
            "Use the same shortened horizon for A and B to keep cold start and workload exposure symmetric.",
        ],
        "caveats": [
            "A shorter window is enough for p50/p95 direction, but max tail claims remain caveated.",
            "The current live BPF backend is blocked for full eligible scope, so this is a run plan, not an executed A/B result.",
        ],
    }


def build_summary(args: argparse.Namespace) -> dict[str, Any]:
    baseline_run_dir = Path(args.baseline_run_dir)
    baseline = baseline_analysis(baseline_run_dir)
    bpf_readiness = bpf_scope_readiness(baseline_run_dir, Path(args.bpf_source))
    ab_status = "ready_to_run" if bpf_readiness["ready_for_full_eligible_scope"] else "blocked_before_ab"
    return {
        "schema_version": 1,
        "goal": "Goal 19 S5 full-scope BPF experimental backend A/B",
        "current_head_ab_status": ab_status,
        "ab_executed": False,
        "ab_run_dirs": {"A_bpf_off": None, "B_bpf_on": None},
        "blocker_policy_honored": ab_status == "blocked_before_ab",
        "baseline": baseline,
        "duration_decision": duration_decision(baseline),
        "bpf_full_eligible_scope_readiness": bpf_readiness,
        "evidence_backed_conclusion": {
            "bpf_backend_benefit_proven": False,
            "reason": (
                "No A/B run was executed because current HEAD cannot run BPF on the "
                "full eligible S5 scope without falling back to canary subset behavior."
            ),
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize S5 BPF A/B duration readiness and full-scope blockers."
    )
    parser.add_argument("--baseline-run-dir", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--bpf-source", type=Path, default=DEFAULT_BPF_SOURCE)
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
