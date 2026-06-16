#!/usr/bin/env python3
# encoding: utf-8

"""Build a compact, analysis-ready summary for a Starlink shell long run."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from _path_setup import bootstrap_app_import_path

bootstrap_app_import_path()


DEFAULT_DEADLINE_MISS_THRESHOLD_SECONDS = 0.05


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _p95(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    index = max(0, min(len(sorted_values) - 1, math.ceil(0.95 * len(sorted_values)) - 1))
    return float(sorted_values[index])


def _stats(values: Iterable[Any]) -> Dict[str, Any]:
    rendered = [
        float(value)
        for value in values
        if isinstance(value, (int, float)) or str(value).replace(".", "", 1).isdigit()
    ]
    if not rendered:
        return {
            "count": 0,
            "avg": 0.0,
            "p95": 0.0,
            "worst": 0.0,
        }
    return {
        "count": len(rendered),
        "avg": round(sum(rendered) / float(len(rendered)), 6),
        "p95": round(_p95(rendered), 6),
        "worst": round(max(rendered), 6),
    }


def _timing_values(
    rows: Sequence[Dict[str, Any]],
    *,
    epoch_type: str,
    key: str,
) -> List[float]:
    values: List[float] = []
    for row in rows:
        if str(row.get("epoch_type")) != epoch_type:
            continue
        value = row.get(key)
        if isinstance(value, (int, float)):
            values.append(float(value))
    return values


def _event_values(
    events: Sequence[Dict[str, Any]],
    *,
    event_type: str,
    key: str,
) -> List[float]:
    values: List[float] = []
    for event in events:
        if str(event.get("event_type")) != event_type:
            continue
        value = event.get(key)
        if isinstance(value, (int, float)):
            values.append(float(value))
    return values


def _epoch_metrics(
    *,
    timing_rows: Sequence[Dict[str, Any]],
    events: Sequence[Dict[str, Any]],
    epoch_type: str,
    event_type: str,
) -> Dict[str, Any]:
    source = "timing_rows" if timing_rows else "events"
    if timing_rows:
        count = sum(1 for row in timing_rows if str(row.get("epoch_type")) == epoch_type)
        values = lambda key: _timing_values(timing_rows, epoch_type=epoch_type, key=key)
    else:
        count = sum(1 for event in events if str(event.get("event_type")) == event_type)
        values = lambda key: _event_values(events, event_type=event_type, key=key)
    return {
        "source": source,
        "count": count,
        "activation_lag_seconds": _stats(values("activation_lag_seconds")),
        "transition_wall_seconds": _stats(values("transition_wall_seconds")),
        "host_apply_seconds": _stats(values("host_operation_apply_wall_seconds")),
        "host_reconcile_seconds": _stats(values("host_runtime_reconcile_wall_seconds")),
        "qdisc_apply_seconds": _stats(values("parameter_qdisc_apply_seconds")),
        "node_local_apply_seconds": _stats(values("node_local_apply_wall_seconds")),
    }


def _path_hop_distribution(value: Any) -> Dict[str, int]:
    counts: Dict[int, int] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            hop_count = node.get("path_hop_count")
            if isinstance(hop_count, int):
                counts[hop_count] = counts.get(hop_count, 0) + 1
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return {str(key): counts[key] for key in sorted(counts)}


def _artifact_sizes(run_dir: Path) -> Dict[str, int]:
    return {
        path.name: path.stat().st_size
        for path in sorted(run_dir.iterdir())
        if path.is_file()
    }


def _preflight_summary(report: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "ready": report.get("assessment", {}).get("ready"),
        "blocking_reasons": report.get("assessment", {}).get("blocking_reasons", []),
        "warning_reasons": report.get("assessment", {}).get("warning_reasons", []),
        "clean_after_run": report.get("clean_after_run"),
        "compose_residue_summary": report.get("compose_residue_summary"),
    }


def build_summary(
    run_dir: Path,
    *,
    gz_semantics: Path | None = None,
    current_preflight: Path | None = None,
) -> Dict[str, Any]:
    run_dir = run_dir.expanduser().resolve()
    manifest = _read_json(run_dir / "run_manifest.json")
    report = _read_json(run_dir / "real_runtime_report.json")
    timing = _read_json(run_dir / "timing.json")
    temporal = _read_json(run_dir / "temporal_audit.json")
    post_preflight = _read_json(run_dir / "post_run_preflight.json")
    path_observations = _read_json(run_dir / "path_observation_results.json")
    epoch_queue = _read_json_or_list(run_dir / "epoch_execution_queue.json")
    events = _read_jsonl(run_dir / "events.jsonl")
    timing_rows = list(timing.get("rows", [])) if isinstance(timing.get("rows"), list) else []
    gz = _read_json(gz_semantics) if gz_semantics is not None else {}
    current = _read_json(current_preflight) if current_preflight is not None else {}
    timing_summary = dict(timing.get("timing_summary") or report.get("timing_summary") or {})
    continuous = dict(manifest.get("continuous_operation_summary") or {})
    phase_timing = dict(timing.get("phase_timing") or manifest.get("phase_timing") or {})
    artifacts = _artifact_sizes(run_dir)
    ping_fail_events = sum(1 for event in events if event.get("ping_success") is False)
    hop_distribution = _path_hop_distribution(path_observations)
    long_hop_count = sum(
        count for hop, count in hop_distribution.items() if int(hop) > 4
    )

    return {
        "artifact_kind": "starlink_long_run_baseline_summary",
        "artifact_version": "v1",
        "run_dir": str(run_dir),
        "run_id": manifest.get("run_id") or run_dir.name,
        "run_mode": manifest.get("run_mode") or report.get("mode"),
        "outcome": report.get("outcome") or manifest.get("outcome"),
        "event_time_duration_seconds": continuous.get("requested_horizon_seconds")
        or manifest.get("horizon_seconds")
        or _requested_horizon_seconds(events)
        or _infer_event_duration_seconds(epoch_queue)
        or _infer_event_duration_seconds(timing_rows),
        "epoch_counts": {
            "runtime_epoch_count": timing_summary.get("runtime_epoch_count")
            or continuous.get("prepared_runtime_epoch_count"),
            "parameter_epoch_count": timing_summary.get("parameter_epoch_count")
            or continuous.get("prepared_parameter_epoch_count"),
            "total_epoch_count": continuous.get("prepared_epoch_queue_count"),
            "replenishment_count": continuous.get("replenishment_segment_count")
            or len([event for event in events if event.get("event_type") == "horizon_replenishment_completed"]),
        },
        "timing_terms": {
            "activation_lag_seconds": (
                "wall-clock delay between the scheduled activation boundary and "
                "when the epoch starts applying"
            ),
            "p95": "nearest-rank 95th percentile over the selected epoch population",
            "transition_wall_seconds": (
                "wall time to apply an epoch and complete its configured validation"
            ),
            "runtime_epoch": "epoch that can mutate topology/link/forwarding runtime state",
            "parameter_epoch": "epoch that updates runtime parameters without topology mutation",
            "strict_deadline_miss": (
                "activation_lag_seconds exceeds the scheduler threshold; this does "
                "not by itself imply ping loss or temporal-audit failure"
            ),
            "rss_vs_artifact_growth": (
                "RSS is sampled process resident memory; artifact/log growth is "
                "durable bytes written under the run directory"
            ),
        },
        "deadline_miss": {
            "count": timing_summary.get("deadline_miss_count"),
            "threshold_seconds": timing_summary.get(
                "deadline_miss_threshold_seconds",
                DEFAULT_DEADLINE_MISS_THRESHOLD_SECONDS,
            ),
            "definition": timing_summary.get(
                "deadline_miss_definition",
                "strict scheduler-boundary miss: activation_lag_seconds exceeds threshold",
            ),
        },
        "runtime_epoch_timing": _epoch_metrics(
            timing_rows=timing_rows,
            events=events,
            epoch_type="runtime_epoch",
            event_type="runtime_epoch_result",
        ),
        "parameter_epoch_timing": _epoch_metrics(
            timing_rows=timing_rows,
            events=events,
            epoch_type="parameter_epoch",
            event_type="parameter_epoch_result",
        ),
        "phase_timing": {
            "compose_up_wall_seconds": phase_timing.get("compose_up_wall_seconds"),
            "pre_epoch_host_runtime_prepare_wall_seconds": phase_timing.get(
                "pre_epoch_host_runtime_prepare_wall_seconds"
            ),
            "pre_epoch_host_runtime_reconcile_wall_seconds": phase_timing.get(
                "pre_epoch_host_runtime_reconcile_wall_seconds"
            ),
            "pre_epoch_host_runtime_prewarm_wall_seconds": phase_timing.get(
                "pre_epoch_host_runtime_prewarm_wall_seconds"
            ),
            "first_runtime_epoch_transition_wall_seconds": phase_timing.get(
                "first_runtime_epoch_transition_wall_seconds"
            ),
            "post_run_preflight_wall_seconds": phase_timing.get(
                "post_run_preflight_wall_seconds"
            ),
        },
        "temporal_audit": temporal,
        "ping": {
            "run_ping_success": (report.get("outcome") or {}).get("ping_success"),
            "ping_fail_event_count": ping_fail_events,
            "final_runtime_connectivity_validation": report.get(
                "final_runtime_connectivity_validation"
            ),
        },
        "path_hop_count_distribution": hop_distribution,
        "long_hop_count_gt4": long_hop_count,
        "gz_semantics": {
            "artifact": str(gz_semantics) if gz_semantics is not None else None,
            "passed": gz.get("passed"),
            "state_counts": gz.get("state_counts"),
            "shared_return_groups": gz.get("shared_return_groups"),
            "zone_assignments": gz.get("zone_assignments"),
            "distinct_prefix_override_examples": gz.get(
                "distinct_prefix_override_examples"
            ),
        },
        "preflight": {
            "run_local_post_run": _preflight_summary(post_preflight),
            "current_environment": _preflight_summary(current) if current else None,
            "residue_vs_host_readiness_note": (
                "compose/direct-backend residue is reported separately from host "
                "MPLS/sysctl/bridge readiness"
            ),
        },
        "artifact_growth": {
            "artifact_total_bytes": sum(artifacts.values()),
            "artifact_sizes_bytes": artifacts,
        },
    }


def _infer_event_duration_seconds(rows: Sequence[Dict[str, Any]]) -> float | None:
    if not isinstance(rows, list):
        return None
    values = [
        float(row.get("activation_relative_time_seconds", row.get("simulated_time_seconds")))
        for row in rows
        if isinstance(
            row.get("activation_relative_time_seconds", row.get("simulated_time_seconds")),
            (int, float),
        )
    ]
    if not values:
        return None
    return round(max(values) - min(values), 6)


def _requested_horizon_seconds(events: Sequence[Dict[str, Any]]) -> float | None:
    for event in events:
        if event.get("event_type") != "continuous_initial_horizon_selected":
            continue
        value = event.get("requested_horizon_seconds")
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _read_json_or_list(path: Path) -> Any:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def render_text(summary: Dict[str, Any]) -> str:
    epoch_counts = dict(summary.get("epoch_counts") or {})
    runtime = dict(summary.get("runtime_epoch_timing") or {})
    parameter = dict(summary.get("parameter_epoch_timing") or {})
    gz = dict(summary.get("gz_semantics") or {})
    preflight = dict(summary.get("preflight") or {})
    current = preflight.get("current_environment") or {}
    lines = [
        f"Run: {summary.get('run_id')}",
        f"Outcome: {summary.get('outcome')}",
        (
            "Epochs: "
            f"runtime={epoch_counts.get('runtime_epoch_count')} "
            f"parameter={epoch_counts.get('parameter_epoch_count')} "
            f"replenishments={epoch_counts.get('replenishment_count')}"
        ),
        (
            "Runtime timing: "
            f"lag={runtime.get('activation_lag_seconds')} "
            f"transition={runtime.get('transition_wall_seconds')}"
        ),
        (
            "Parameter timing: "
            f"lag={parameter.get('activation_lag_seconds')} "
            f"transition={parameter.get('transition_wall_seconds')}"
        ),
        f"Deadline miss: {summary.get('deadline_miss')}",
        (
            "G/Z: "
            f"passed={gz.get('passed')} "
            f"groups={len(gz.get('shared_return_groups') or [])}"
        ),
        (
            "Ping/path: "
            f"fail_events={dict(summary.get('ping') or {}).get('ping_fail_event_count')} "
            f"hop_dist={summary.get('path_hop_count_distribution')} "
            f"long_hop_gt4={summary.get('long_hop_count_gt4')}"
        ),
        (
            "Preflight: "
            f"post_run={dict(preflight.get('run_local_post_run') or {}).get('clean_after_run')} "
            f"current_ready={dict(current).get('ready')} "
            f"current_warnings={dict(current).get('warning_reasons')}"
        ),
    ]
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize durable artifacts from a completed Starlink shell long run.",
    )
    parser.add_argument("run_dir", help="Path to the run directory.")
    parser.add_argument("--gz-semantics", default=None, help="Optional G/Z semantics JSON.")
    parser.add_argument(
        "--current-preflight",
        default=None,
        help="Optional current-environment preflight JSON.",
    )
    parser.add_argument("--output", default=None, help="Optional output JSON path.")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = build_summary(
        Path(args.run_dir),
        gz_semantics=Path(args.gz_semantics) if args.gz_semantics else None,
        current_preflight=Path(args.current_preflight) if args.current_preflight else None,
    )
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    if args.format == "json":
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(render_text(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
