#!/usr/bin/env python3
"""Generate second-pass figures for the 6-zone 2h G/Z metadata runtime run.

The script is deliberately offline and read-only with respect to runtime
artifacts. It clears prior generated figures in the target analysis directory,
then writes a focused figure set where timing, workload, resources, FEC diffs,
and orbit views share comparable axes and source references.
"""

from __future__ import annotations

import argparse
import base64
import csv
import glob
import html
import json
import math
import re
import textwrap
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
from matplotlib import gridspec
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401


EARTH_RADIUS_KM = 6371.0
SATELLITE_DIR = Path(__file__).resolve().parents[1]
THREE_VENDOR_DIR = SATELLITE_DIR / "assets" / "vendor" / "three-r128"
RUN_DIR_DEFAULT = Path(
    "satellite/output/starlink_shell_experiments/"
    "gz-z6metadata-2h-gated-20260611-0824"
)
OUT_DIR_DEFAULT = Path(
    "satellite/output/analysis/gz_6zone_2h_figures_20260611"
)


@dataclass
class FigureRecord:
    filename: str
    title: str
    description: str
    sources: list[str]


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def fnum(value: Any, default: float = 0.0) -> float:
    if value in ("", None, "None"):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def pct(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * percentile / 100.0
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (rank - lo)


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def stats(values: list[float]) -> dict[str, float]:
    return {
        "avg": round(mean(values), 6),
        "p50": round(pct(values, 50), 6),
        "p95": round(pct(values, 95), 6),
        "max": round(max(values), 6) if values else 0.0,
    }


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def savefig(fig: plt.Figure, out_dir: Path, filename: str) -> str:
    out_path = out_dir / filename
    fig.savefig(out_path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return str(out_path)


def data_uri(path: Path, mime_type: str) -> str:
    payload = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{payload}"


def inline_script(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    return text.replace("</script", "<\\/script")


def clean_out_dir(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for path in out_dir.iterdir():
        if path.suffix.lower() in {".png", ".html"} or path.name in {
            "figure_manifest.json",
            "figure_summary.json",
        }:
            path.unlink()


def latlon_to_ecef(lat_deg: float, lon_deg: float, alt_km: float = 0.0) -> tuple[float, float, float]:
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    radius = EARTH_RADIUS_KM + alt_km
    return (
        radius * math.cos(lat) * math.cos(lon),
        radius * math.cos(lat) * math.sin(lon),
        radius * math.sin(lat),
    )


def zone_circle_ecef_points(
    *,
    lat_deg: float,
    lon_deg: float,
    radius_km: float,
    samples: int = 96,
) -> list[tuple[float, float, float]]:
    """Return points on a small circle around a lat/lon center on Earth.

    The circle follows the spherical destination formula, so it remains on the
    Earth surface instead of being a flat Euclidean ring through the globe.
    """

    lat1 = math.radians(lat_deg)
    lon1 = math.radians(lon_deg)
    angular_distance = max(0.0, float(radius_km)) / EARTH_RADIUS_KM
    points: list[tuple[float, float, float]] = []
    for idx in range(samples + 1):
        bearing = 2.0 * math.pi * idx / samples
        lat2 = math.asin(
            math.sin(lat1) * math.cos(angular_distance)
            + math.cos(lat1) * math.sin(angular_distance) * math.cos(bearing)
        )
        lon2 = lon1 + math.atan2(
            math.sin(bearing) * math.sin(angular_distance) * math.cos(lat1),
            math.cos(angular_distance) - math.sin(lat1) * math.sin(lat2),
        )
        points.append(latlon_to_ecef(math.degrees(lat2), math.degrees(lon2), 0.0))
    return points


def collect_zone_schedule(run_dir: Path) -> list[dict[str, Any]]:
    paths = [run_dir / "zone_anchor_schedule.json"]
    paths.extend(sorted(run_dir.glob("replenished_segments/segment_*/config/zone_anchor_schedule.json")))
    rows_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for path in paths:
        if not path.exists():
            continue
        for row in read_json(path).get("rows", []):
            key = (row.get("absolute_time_utc", ""), row.get("zone_id", ""))
            enriched = dict(row)
            enriched["_source"] = str(path.relative_to(run_dir))
            rows_by_key.setdefault(key, enriched)
    return sorted(rows_by_key.values(), key=lambda r: (r.get("absolute_time_utc", ""), r.get("zone_id", "")))


def collect_ping_watcher_samples(run_dir: Path) -> dict[str, Any]:
    ping_results_path = run_dir / "ping_results.json"
    if not ping_results_path.exists():
        return {"watchers": []}
    ping_results = read_json(ping_results_path)
    watchers = []
    pattern = re.compile(
        r"^(?P<timestamp>\d+\.\d+)\s+\S+\s+"
        r"(?P<status>success|loss|unreachable)\b"
        r"(?:.*?=\s*(?P<min>[0-9.]+)/(?P<avg>[0-9.]+)/(?P<max>[0-9.]+)/(?P<mdev>[0-9.]+)\s*ms)?"
    )
    for watcher_id, watcher in sorted((ping_results.get("watchers") or {}).items()):
        log_path = Path(str(watcher.get("log_path", "")))
        if not log_path.exists():
            candidate = run_dir / log_path.name
            log_path = candidate if candidate.exists() else log_path
        samples = []
        if log_path.exists():
            with log_path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    match = pattern.search(line.strip())
                    if not match:
                        continue
                    samples.append(
                        {
                            "timestamp": float(match.group("timestamp")),
                            "status": str(match.group("status")),
                            "rtt_avg_ms": (
                                float(match.group("avg"))
                                if match.group("avg") is not None
                                else None
                            ),
                        }
                    )
        watchers.append(
            {
                "watcher_id": watcher_id,
                "ut_id": watcher.get("ut_id"),
                "target": watcher.get("target"),
                "target_ip": watcher.get("target_ip"),
                "log_path": str(log_path),
                "samples": samples,
                "summary": watcher.get("summary", {}),
            }
        )
    return {"watchers": watchers}


def collect_phase_markers(resource_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    markers = []
    previous = None
    for row in resource_rows:
        phase = row.get("current_phase")
        if phase == previous:
            continue
        previous = phase
        parsed = parse_time(row.get("timestamp_utc"))
        if parsed is None:
            continue
        markers.append({"phase": phase, "timestamp": parsed.timestamp()})
    return markers


def timing_by_sequence(timing_rows: list[dict[str, str]]) -> dict[int, dict[str, str]]:
    out: dict[int, dict[str, str]] = {}
    for row in timing_rows:
        try:
            out[int(row.get("sequence_index", ""))] = row
        except ValueError:
            continue
    return out


def semantic_value(epoch: dict[str, Any], timing_row: dict[str, str] | None, key: str) -> float:
    if timing_row is not None and timing_row.get(key) not in ("", None, "None"):
        return fnum(timing_row.get(key))
    sb = (epoch.get("timing") or {}).get("semantic_breakdown") or {}
    return fnum(sb.get(key.removeprefix("semantic_")))


def epoch_records(data: dict[str, Any]) -> list[dict[str, Any]]:
    timing_map = timing_by_sequence(data["timing"])
    records = []
    for epoch in data["epochs"]:
        seq = int(epoch.get("sequence_index", 0) or 0)
        timing_row = timing_map.get(seq)
        timing = epoch.get("timing") or {}
        sb = timing.get("semantic_breakdown") or {}
        epoch_type = epoch.get("epoch_type", "")
        records.append(
            {
                "sequence": seq,
                "minute": fnum(epoch.get("simulated_time_seconds")) / 60.0,
                "sim_seconds": fnum(epoch.get("simulated_time_seconds")),
                "epoch_type": epoch_type,
                "slice_id": epoch.get("slice_id"),
                "activation_time_utc": epoch.get("activation_time_utc"),
                "applied_ops": fnum(epoch.get("applied_operations")),
                "skipped_ops": fnum(epoch.get("skipped_operations")),
                "network_mutations": fnum(epoch.get("network_mutation_count")),
                "forwarding_mutations": fnum(epoch.get("forwarding_mutation_count")),
                "topology_mutations": fnum(epoch.get("topology_mutation_count")),
                "node_reapplied_flows": fnum(timing.get("node_local_reapplied_flow_count", len(epoch.get("node_local_reapplied_flow_ids") or []))),
                "node_held_flows": fnum(timing.get("node_local_held_flow_count", len(epoch.get("node_local_held_flow_ids") or []))),
                "host_reconcile_slots": fnum(timing.get("host_reconcile_requested_slot_count")),
                "total_wall": semantic_value(epoch, timing_row, "semantic_total_epoch_wall_seconds"),
                "data_plane": semantic_value(epoch, timing_row, "semantic_data_plane_mutation_apply_seconds"),
                "host_apply": semantic_value(epoch, timing_row, "semantic_runtime_host_apply_seconds"),
                "node_apply": semantic_value(epoch, timing_row, "semantic_runtime_node_local_apply_seconds"),
                "mpls_route_nat": semantic_value(epoch, timing_row, "semantic_runtime_mpls_route_nat_apply_seconds"),
                "parameter_actual": semantic_value(epoch, timing_row, "semantic_parameter_actual_mutation_seconds"),
                "parameter_qdisc": fnum(timing.get("parameter_qdisc_apply_seconds")),
                "parameter_metadata": fnum(timing.get("parameter_metadata_update_seconds")),
                "parameter_reconcile": fnum(timing.get("parameter_dirty_reconcile_seconds")),
                "parameter_validation": semantic_value(epoch, timing_row, "semantic_parameter_targeted_validation_seconds"),
                "pre_cleanup": semantic_value(epoch, timing_row, "semantic_pre_apply_cleanup_or_blocking_verification_seconds"),
                "post_validation": semantic_value(epoch, timing_row, "semantic_post_apply_validation_probe_seconds"),
                "cleanup_overhead": semantic_value(epoch, timing_row, "semantic_verification_cleanup_runner_overhead_seconds"),
                "scheduler_block": semantic_value(epoch, timing_row, "semantic_scheduler_blocking_after_data_plane_ready_seconds"),
                "required_gate": semantic_value(epoch, timing_row, "semantic_required_safety_gate_seconds"),
                "artifact_block": semantic_value(epoch, timing_row, "semantic_artifact_write_blocking_seconds"),
                "activation_lag": semantic_value(epoch, timing_row, "semantic_scheduled_activation_lag_seconds"),
                "ready_lag": semantic_value(epoch, timing_row, "semantic_data_plane_ready_lag_seconds"),
                "network_plan_wall": fnum(timing.get("network_mutation_plan_wall_seconds")),
                "trigger_reasons": epoch.get("trigger_reasons") or [],
            }
        )
    return sorted(records, key=lambda r: r["sequence"])


def find_path_change_example(epochs: list[dict[str, Any]]) -> dict[str, Any] | None:
    for epoch in epochs:
        if epoch.get("epoch_type") != "runtime_epoch":
            continue
        pre = {p.get("flow_id"): p for p in epoch.get("pre_transition_paths") or []}
        post = {p.get("flow_id"): p for p in epoch.get("post_transition_paths") or []}
        for flow_id, before in pre.items():
            after = post.get(flow_id)
            if after and before.get("path_hop_count") != after.get("path_hop_count"):
                return {
                    "sequence": epoch.get("sequence_index"),
                    "minute": fnum(epoch.get("simulated_time_seconds")) / 60.0,
                    "activation_time_utc": epoch.get("activation_time_utc"),
                    "slice_id": epoch.get("slice_id"),
                    "flow_id": flow_id,
                    "before": before,
                    "after": after,
                }
    return None


def collect_data(run_dir: Path) -> dict[str, Any]:
    data = {
        "timing": read_csv_rows(run_dir / "timing.csv"),
        "epochs": read_jsonl(run_dir / "epoch_execution_details.jsonl"),
        "resources": read_jsonl(run_dir / "resource_usage.jsonl"),
        "run_manifest": read_json(run_dir / "run_manifest.json"),
        "real_runtime_report": read_json(run_dir / "real_runtime_report.json"),
        "return_path_audit": read_json(run_dir / "return_path_audit.json"),
        "temporal_audit": read_json(run_dir / "temporal_audit.json"),
        "fec_diff": read_json(run_dir / "gz_update_diff_summary.json"),
        "fec_plan": read_json(run_dir / "gz_fec_plan.json"),
        "zone_model": read_json(run_dir / "zone_model.json"),
        "zone_anchor_schedule": collect_zone_schedule(run_dir),
        "topology": read_json(run_dir / "topology.json"),
        "ground_manifest": read_json(run_dir / "ground_manifest.json"),
        "positions": read_json(run_dir / "propagated_positions.json"),
        "runtime_summary": read_json(run_dir / "runtime_epoch_summary.json"),
        "parameter_summary": read_json(run_dir / "parameter_epoch_summary.json"),
        "replenishment_count": len(list(run_dir.glob("replenished_segments/segment_*/segment_summary.json"))),
    }
    data["epoch_records"] = epoch_records(data)
    data["path_change_example"] = find_path_change_example(data["epochs"])
    data["ping_watchers"] = collect_ping_watcher_samples(run_dir)
    data["phase_markers"] = collect_phase_markers(data["resources"])
    return data


def values(records: list[dict[str, Any]], key: str) -> list[float]:
    return [fnum(record.get(key)) for record in records]


def minutes(records: list[dict[str, Any]]) -> np.ndarray:
    return np.array(values(records, "minute"), dtype=float)


def add_interval_lines(ax: plt.Axes, include_sample: bool = True) -> None:
    ax.axhline(10, color="#333333", linestyle="--", linewidth=1.0, alpha=0.75, label="10s event interval")
    if include_sample:
        ax.axhline(60, color="#888888", linestyle=":", linewidth=1.0, alpha=0.8, label="60s sample interval")


def figure_timing_detail(data: dict[str, Any], out_dir: Path) -> FigureRecord:
    records = data["epoch_records"]
    runtime = [r for r in records if r["epoch_type"] == "runtime_epoch"]
    parameter = [r for r in records if r["epoch_type"] == "parameter_epoch"]

    fig = plt.figure(figsize=(16, 10))
    fig.suptitle("Detailed per-update timing, aligned by simulated runtime", fontsize=18, fontweight="bold")
    gs = gridspec.GridSpec(3, 1, figure=fig, height_ratios=[1.1, 1.0, 1.0])

    ax0 = fig.add_subplot(gs[0, 0])
    ax0.plot(minutes(runtime), values(runtime, "total_wall"), color="#1F77B4", linewidth=1.2, label="RuntimeEpoch total wall")
    ax0.plot(minutes(parameter), values(parameter, "total_wall"), color="#2CA02C", linewidth=1.0, label="ParameterEpoch total wall")
    ax0.plot(minutes(runtime), values(runtime, "ready_lag"), color="#9467BD", linewidth=1.0, alpha=0.85, label="Runtime data-plane ready lag")
    ax0.plot(minutes(parameter), values(parameter, "ready_lag"), color="#8C564B", linewidth=0.9, alpha=0.75, label="Parameter data-plane ready lag")
    add_interval_lines(ax0)
    ax0.set_ylabel("seconds")
    ax0.set_title("Wall time and lag")
    ax0.grid(True, alpha=0.25)
    ax0.legend(ncol=3, fontsize=8)

    ax1 = fig.add_subplot(gs[1, 0], sharex=ax0)
    ax1.plot(minutes(runtime), values(runtime, "host_apply"), color="#D62728", linewidth=1.1, label="runtime host apply")
    ax1.plot(minutes(runtime), values(runtime, "node_apply"), color="#FF7F0E", linewidth=1.1, label="runtime node-local apply")
    ax1.plot(minutes(runtime), values(runtime, "pre_cleanup"), color="#7F7F7F", linewidth=1.0, label="runtime pre cleanup/block")
    ax1.plot(minutes(runtime), values(runtime, "post_validation"), color="#17BECF", linewidth=1.0, label="runtime post validation/probe")
    ax1.plot(minutes(runtime), values(runtime, "scheduler_block"), color="#BCBD22", linewidth=0.9, label="runtime scheduler block")
    add_interval_lines(ax1, include_sample=False)
    ax1.set_ylabel("seconds")
    ax1.set_title("RuntimeEpoch data-plane and blocking subphases")
    ax1.grid(True, alpha=0.25)
    ax1.legend(ncol=3, fontsize=8)

    ax2 = fig.add_subplot(gs[2, 0], sharex=ax0)
    ax2.plot(minutes(parameter), values(parameter, "parameter_qdisc"), color="#E377C2", linewidth=1.1, label="parameter qdisc apply")
    ax2.plot(minutes(parameter), values(parameter, "parameter_metadata"), color="#7F7F7F", linewidth=0.9, label="parameter metadata update")
    ax2.plot(minutes(parameter), values(parameter, "parameter_reconcile"), color="#BCBD22", linewidth=0.9, label="parameter dirty reconcile")
    ax2.plot(minutes(parameter), values(parameter, "parameter_validation"), color="#17BECF", linewidth=0.9, label="parameter targeted validation")
    ax2.plot(minutes(parameter), values(parameter, "scheduler_block"), color="#8C564B", linewidth=0.8, label="parameter scheduler block")
    add_interval_lines(ax2, include_sample=False)
    ax2.set_xlabel("simulated runtime minute")
    ax2.set_ylabel("seconds")
    ax2.set_title("ParameterEpoch mutation subphases")
    ax2.grid(True, alpha=0.25)
    ax2.legend(ncol=3, fontsize=8)

    filename = "01_detailed_timing_runtime_parameter.png"
    savefig(fig, out_dir, filename)
    return FigureRecord(
        filename,
        "Detailed runtime and parameter timing",
        "Runtime and ParameterEpoch timing lines share the simulated-minute axis and show finer data-plane subphases.",
        ["timing.csv", "epoch_execution_details.jsonl"],
    )


def figure_workload_timeline(data: dict[str, Any], out_dir: Path) -> FigureRecord:
    records = data["epoch_records"]
    runtime = [r for r in records if r["epoch_type"] == "runtime_epoch"]
    parameter = [r for r in records if r["epoch_type"] == "parameter_epoch"]

    fig = plt.figure(figsize=(16, 9.5))
    fig.suptitle("Per-update work quantities, aligned with timing figures", fontsize=18, fontweight="bold")
    gs = gridspec.GridSpec(3, 1, figure=fig, height_ratios=[1.0, 1.0, 0.95])

    ax0 = fig.add_subplot(gs[0, 0])
    ax0.plot(minutes(runtime), values(runtime, "applied_ops"), color="#1F77B4", linewidth=1.1, label="runtime applied operations")
    ax0.plot(minutes(runtime), values(runtime, "skipped_ops"), color="#A0A0A0", linewidth=0.9, label="runtime skipped operations")
    ax0.plot(minutes(parameter), values(parameter, "applied_ops"), color="#2CA02C", linewidth=1.0, label="parameter applied operations")
    ax0.plot(minutes(parameter), values(parameter, "skipped_ops"), color="#C7C7C7", linewidth=0.9, label="parameter skipped operations")
    ax0.set_ylabel("operation count")
    ax0.set_title("Applied and skipped operations")
    ax0.grid(True, alpha=0.25)
    ax0.legend(ncol=4, fontsize=8)

    ax1 = fig.add_subplot(gs[1, 0], sharex=ax0)
    ax1.plot(minutes(runtime), values(runtime, "forwarding_mutations"), color="#D62728", linewidth=1.1, label="runtime forwarding mutations")
    ax1.plot(minutes(runtime), values(runtime, "topology_mutations"), color="#FF7F0E", linewidth=1.1, label="runtime topology mutations")
    ax1.plot(minutes(runtime), values(runtime, "node_reapplied_flows"), color="#9467BD", linewidth=1.0, label="runtime node reapplied flows")
    ax1.plot(minutes(runtime), values(runtime, "node_held_flows"), color="#8C564B", linewidth=1.0, label="runtime held flows")
    ax1.set_ylabel("count")
    ax1.set_title("Runtime mutation detail")
    ax1.grid(True, alpha=0.25)
    ax1.legend(ncol=4, fontsize=8)

    ax2 = fig.add_subplot(gs[2, 0], sharex=ax0)
    ax2.plot(minutes(parameter), values(parameter, "network_mutations"), color="#2CA02C", linewidth=1.0, label="parameter network mutations")
    ax2.plot(minutes(parameter), values(parameter, "host_reconcile_slots"), color="#17BECF", linewidth=0.9, label="host reconcile slots")
    ax2.scatter(minutes(parameter), values(parameter, "applied_ops"), s=9, color="#2CA02C", alpha=0.7)
    ax2.set_xlabel("simulated runtime minute")
    ax2.set_ylabel("count")
    ax2.set_title("Parameter mutation detail")
    ax2.grid(True, alpha=0.25)
    ax2.legend(ncol=3, fontsize=8)

    filename = "02_update_workload_timeline.png"
    savefig(fig, out_dir, filename)
    return FigureRecord(
        filename,
        "Per-update workload timeline",
        "Counts of applied/skipped operations, topology/forwarding mutations, and node-local flow work.",
        ["epoch_execution_details.jsonl"],
    )


def figure_timing_workload_overlay(data: dict[str, Any], out_dir: Path) -> FigureRecord:
    records = data["epoch_records"]
    runtime = [r for r in records if r["epoch_type"] == "runtime_epoch"]
    parameter = [r for r in records if r["epoch_type"] == "parameter_epoch"]

    fig = plt.figure(figsize=(16, 8.8))
    fig.suptitle("Timing vs work quantity: same updates, same x-axis", fontsize=18, fontweight="bold")
    gs = gridspec.GridSpec(2, 1, figure=fig)

    for ax, subset, title, color in [
        (fig.add_subplot(gs[0, 0]), runtime, "RuntimeEpoch wall vs applied operations", "#1F77B4"),
        (fig.add_subplot(gs[1, 0]), parameter, "ParameterEpoch wall vs applied operations", "#2CA02C"),
    ]:
        x = minutes(subset)
        wall = values(subset, "total_wall")
        applied = values(subset, "applied_ops")
        ax.plot(x, wall, color=color, linewidth=1.2, label="total wall seconds")
        ax.scatter(x, wall, c=applied, cmap="viridis", s=18, alpha=0.85, label="updates colored by applied ops")
        ax2 = ax.twinx()
        ax2.plot(x, applied, color="#555555", linewidth=0.8, alpha=0.65, label="applied operations")
        ax.axhline(10, color="#333333", linestyle="--", linewidth=1.0, alpha=0.6)
        ax.set_ylabel("seconds")
        ax2.set_ylabel("applied ops")
        ax.set_title(title)
        ax.grid(True, alpha=0.25)
        lines, labels = ax.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax.legend(lines + lines2, labels + labels2, loc="upper right", fontsize=8)
    fig.axes[-2].set_xlabel("simulated runtime minute")

    filename = "03_timing_workload_overlay.png"
    savefig(fig, out_dir, filename)
    return FigureRecord(
        filename,
        "Timing workload overlay",
        "Direct visual correspondence between per-update wall time and operation count.",
        ["timing.csv", "epoch_execution_details.jsonl"],
    )


def full_reinstall_estimate(plan: dict[str, Any]) -> int:
    groups = list(plan.get("forward_fecs", [])) + list(plan.get("return_fecs", []))
    node_items = sum(len(group.get("affected_nodes") or []) for group in groups)
    return node_items + len(groups)


def figure_fec_diff_timeline(data: dict[str, Any], out_dir: Path) -> FigureRecord:
    diffs = data["fec_diff"].get("diffs", [])
    plans = data["fec_plan"].get("plans", [])
    x = np.arange(len(diffs))
    changed = [fnum(d.get("changed_fec_group_count")) for d in diffs]
    skipped = [fnum(d.get("skipped_unchanged_fec_group_count")) for d in diffs]
    affected = [fnum(d.get("affected_node_count")) for d in diffs]
    ops = [fnum(d.get("estimated_lfib_route_operation_items")) for d in diffs]
    baseline = [full_reinstall_estimate(p) for p in plans[: len(diffs)]]
    labels = [
        (d.get("current_snapshot") or {}).get("slice_id", f"slice-{idx + 1}").replace("starlink-shell-", "")
        for idx, d in enumerate(diffs)
    ]

    fig = plt.figure(figsize=(16, 8.8))
    fig.suptitle("G/Z FEC diff timeline: changed work vs skipped unchanged groups", fontsize=18, fontweight="bold")
    gs = gridspec.GridSpec(2, 1, figure=fig, height_ratios=[1.15, 0.9])

    ax0 = fig.add_subplot(gs[0, 0])
    ax0.bar(x, changed, color="#4E79A7", label="changed FEC groups")
    ax0.bar(x, skipped, bottom=changed, color="#BDBDBD", label="skipped unchanged FEC groups")
    ax0.plot(x, baseline, color="#333333", linestyle="--", linewidth=1.2, label="full-plan reinstall estimate")
    ax0.set_ylabel("FEC group / estimate count")
    ax0.set_title("Skipped unchanged groups dominate most slices")
    ax0.grid(True, axis="y", alpha=0.25)
    ax0.legend(ncol=3, fontsize=8)

    ax1 = fig.add_subplot(gs[1, 0], sharex=ax0)
    ax1.plot(x, affected, color="#D95F02", marker="o", linewidth=1.2, label="affected nodes")
    ax1.plot(x, ops, color="#7B3294", marker="s", linewidth=1.2, label="operation estimate")
    for idx, diff in sorted(
        enumerate(diffs),
        key=lambda item: fnum(item[1].get("estimated_lfib_route_operation_items")),
        reverse=True,
    )[:5]:
        ax1.annotate(
            labels[idx],
            (idx, ops[idx]),
            textcoords="offset points",
            xytext=(0, 8),
            ha="center",
            fontsize=8,
        )
    ax1.set_ylabel("count")
    ax1.set_xlabel("preview slice")
    ax1.grid(True, alpha=0.25)
    ax1.legend(ncol=2, fontsize=8)
    ax1.set_xticks(x)
    ax1.set_xticklabels(labels, rotation=45, ha="right")

    filename = "04_fec_diff_timeline.png"
    savefig(fig, out_dir, filename)
    return FigureRecord(
        filename,
        "FEC diff timeline",
        "Changed/skipped FEC groups, affected nodes, operation estimates, and full-plan reinstall baseline.",
        ["gz_update_diff_summary.json", "gz_fec_plan.json"],
    )


def concise_group(group_id: str) -> str:
    parts = group_id.split(":")
    if len(parts) >= 3 and parts[0].startswith("gw"):
        return f"{parts[1]} @ {parts[2]} via {parts[0]}"
    if len(parts) >= 4 and parts[0].startswith("zone-"):
        return f"{parts[0]} return via {parts[2]} @ {parts[3]}"
    return group_id


def figure_large_update_explainer(data: dict[str, Any], out_dir: Path) -> FigureRecord:
    diffs = data["fec_diff"].get("diffs", [])
    ranked = sorted(
        enumerate(diffs),
        key=lambda item: (
            fnum(item[1].get("estimated_lfib_route_operation_items")),
            fnum(item[1].get("affected_node_count")),
        ),
        reverse=True,
    )
    initial_idx, initial = ranked[0]
    mid_idx, mid = next(((i, d) for i, d in ranked if i != initial_idx), ranked[0])

    fig = plt.figure(figsize=(16, 9.5))
    fig.suptitle("Why the largest G/Z update events are large", fontsize=18, fontweight="bold")
    gs = gridspec.GridSpec(2, 2, figure=fig, width_ratios=[0.95, 1.25], height_ratios=[1, 1])

    def panel(ax: plt.Axes, idx: int, diff: dict[str, Any], title: str) -> None:
        ax.axis("off")
        slice_id = (diff.get("current_snapshot") or {}).get("slice_id", f"slice-{idx + 1}").replace("starlink-shell-", "")
        zone_text = "\n  ".join(textwrap.wrap(", ".join(diff.get("changed_zones", [])), width=46))
        lines = [
            f"{title}: {slice_id}",
            f"changed FEC groups: {diff.get('changed_fec_group_count')}",
            f"skipped unchanged FEC groups: {diff.get('skipped_unchanged_fec_group_count')}",
            f"affected nodes: {diff.get('affected_node_count')}",
            f"operation estimate: {diff.get('estimated_lfib_route_operation_items')}",
            "changed zones:",
            f"  {zone_text}",
            f"scope: {diff.get('diff_scope')}",
        ]
        ax.text(0.0, 1.0, "\n".join(lines), va="top", fontsize=11, linespacing=1.45)

    panel(fig.add_subplot(gs[0, 0]), initial_idx, initial, "Initial/materialization event")
    panel(fig.add_subplot(gs[1, 0]), mid_idx, mid, "Largest mid-run diff")

    ax_top = fig.add_subplot(gs[0, 1])
    categories = ["added", "removed", "changed", "skipped"]
    initial_vals = [
        len(initial.get("added_fec_groups") or []),
        len(initial.get("removed_fec_groups") or []),
        len(initial.get("changed_fec_groups") or []),
        len(initial.get("skipped_unchanged_fec_groups") or []),
    ]
    mid_vals = [
        len(mid.get("added_fec_groups") or []),
        len(mid.get("removed_fec_groups") or []),
        len(mid.get("changed_fec_groups") or []),
        len(mid.get("skipped_unchanged_fec_groups") or []),
    ]
    x = np.arange(len(categories))
    width = 0.36
    ax_top.bar(x - width / 2, initial_vals, width, color="#4E79A7", label="initial")
    ax_top.bar(x + width / 2, mid_vals, width, color="#F28E2B", label="mid-run")
    ax_top.set_xticks(x)
    ax_top.set_xticklabels(categories)
    ax_top.set_title("FEC group category comparison")
    ax_top.set_ylabel("FEC groups")
    ax_top.grid(True, axis="y", alpha=0.25)
    ax_top.legend(fontsize=8)

    ax_mid = fig.add_subplot(gs[1, 1])
    ax_mid.axis("off")
    added = [concise_group(g) for g in (mid.get("added_fec_groups") or [])[:5]]
    removed = [concise_group(g) for g in (mid.get("removed_fec_groups") or [])[:5]]
    skipped = [concise_group(g) for g in (mid.get("skipped_unchanged_fec_groups") or [])[:6]]
    text = [
        "Mid-run diff explanation:",
        "",
        "Removed old anchor/path groups:",
        *[f"  - {line}" for line in removed],
        "",
        "Added replacement anchor/path groups:",
        *[f"  + {line}" for line in added],
        "",
        "Skipped unchanged groups:",
        *[f"  = {line}" for line in skipped],
        "",
        f"Affected nodes: {', '.join(mid.get('affected_nodes', []))}",
    ]
    ax_mid.text(0.0, 1.0, "\n".join(text), va="top", fontsize=9.5, family="monospace")

    filename = "05_large_update_event_explainer.png"
    savefig(fig, out_dir, filename)
    return FigureRecord(
        filename,
        "Large update explainer",
        "Readable explanation of the initial materialization and the largest mid-run G/Z FEC diff.",
        ["gz_update_diff_summary.json"],
    )


def path_nodes(path_string: str) -> list[str]:
    return [part.strip() for part in path_string.split("->") if part.strip()]


def short_label(node: str) -> str:
    if node.startswith("stl-"):
        return node.replace("stl-", "")
    if node.startswith("ut-internet-egress:"):
        return node.split(":", 1)[1]
    return node


def draw_path_flow(ax: plt.Axes, y: float, title: str, path_string: str, color: str) -> None:
    nodes = path_nodes(path_string)
    xs = np.linspace(0.17, 0.93, len(nodes))
    ax.text(0.02, y, title, ha="left", va="center", fontsize=12, fontweight="bold")
    for idx, (x, node) in enumerate(zip(xs, nodes)):
        ax.scatter([x], [y], s=950, color=color, edgecolor="#222222", linewidth=1.2, zorder=3)
        ax.text(x, y - 0.085, short_label(node), ha="center", va="top", fontsize=10, fontweight="bold")
        if idx < len(nodes) - 1:
            ax.annotate(
                "",
                xy=(xs[idx + 1] - 0.06, y),
                xytext=(x + 0.06, y),
                arrowprops={"arrowstyle": "->", "lw": 2.0, "color": "#333333"},
            )


def figure_path_transition(data: dict[str, Any], out_dir: Path) -> FigureRecord:
    example = data["path_change_example"]
    fig = plt.figure(figsize=(16, 8.5))
    fig.suptitle("Concrete slice transition: one flow changes from 1-hop to 2-hop", fontsize=18, fontweight="bold")
    ax = fig.add_subplot(111)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    if not example:
        ax.text(0.5, 0.5, "No hop-count path transition found", ha="center", va="center", fontsize=14)
    else:
        before = example["before"]
        after = example["after"]
        header = (
            f"Flow {example['flow_id']} | sequence {example['sequence']} | "
            f"minute {example['minute']:.2f} | {example['activation_time_utc']}"
        )
        ax.text(0.02, 0.91, header, ha="left", va="top", fontsize=12)
        draw_path_flow(ax, 0.66, f"before ({before.get('path_hop_count')} hop)", before.get("path_string", ""), "#6BAED6")
        draw_path_flow(ax, 0.34, f"after ({after.get('path_hop_count')} hops)", after.get("path_string", ""), "#FD8D3C")
        notes = [
            "Interpretation:",
            "The serving satellite stays on the UT side, but the gateway-facing satellite changes to a two-satellite path.",
            "The planner updates only the affected path/FEC state for this flow/zone group; unchanged groups are skipped.",
            f"Forward labels: {before.get('forward_labels')} -> {after.get('forward_labels')}",
            f"Reverse labels: {before.get('reverse_labels')} -> {after.get('reverse_labels')}",
            f"Zone return target: {before.get('zone_return_target_key')} -> {after.get('zone_return_target_key')}",
        ]
        ax.text(0.02, 0.12, "\n".join(textwrap.wrap(notes[1], 115)), fontsize=10.5, color="#444444")
        ax.text(0.02, 0.06, "\n".join(notes[3:]), fontsize=9.5, color="#333333", family="monospace")

    filename = "06_path_transition_example.png"
    savefig(fig, out_dir, filename)
    return FigureRecord(
        filename,
        "Path transition example",
        "A clearer before/after diagram for one real flow whose path changes from one satellite hop to two.",
        ["epoch_execution_details.jsonl"],
    )


def figure_resource_timeline(data: dict[str, Any], out_dir: Path) -> FigureRecord:
    rows = data["resources"]
    times = [parse_time(row.get("timestamp_utc")) for row in rows]
    base = next((t for t in times if t), None)
    x = [((t - base).total_seconds() / 60.0 if t and base else 0.0) for t in times]
    rss = [fnum((row.get("self_process") or {}).get("vmrss_kib")) / 1024.0 for row in rows]
    mem_avail = [
        fnum((((row.get("host_resources") or {}).get("memory") or {}).get("memavailable_kib"))) / (1024.0 * 1024.0)
        for row in rows
    ]
    swap_used = []
    run_dir_mib = []
    runtime_completed = []
    parameter_completed = []
    for row in rows:
        mem = ((row.get("host_resources") or {}).get("memory") or {})
        total = fnum(mem.get("swaptotal_kib"))
        free = fnum(mem.get("swapfree_kib"))
        swap_used.append((total - free) / 1024.0 if total else 0.0)
        run_dir = ((row.get("host_resources") or {}).get("run_dir") or {})
        run_dir_mib.append(fnum(run_dir.get("size_bytes")) / (1024.0 * 1024.0))
        runtime_completed.append(fnum(row.get("runtime_epochs_completed")))
        parameter_completed.append(fnum(row.get("parameter_epochs_completed")))

    fig = plt.figure(figsize=(16, 10))
    fig.suptitle("Resource usage and progress during the run", fontsize=18, fontweight="bold")
    gs = gridspec.GridSpec(4, 1, figure=fig, height_ratios=[1, 1, 1, 0.9])

    axes = [fig.add_subplot(gs[i, 0]) for i in range(4)]
    axes[0].plot(x, rss, color="#1F77B4", linewidth=1.1)
    axes[0].set_ylabel("RSS MiB")
    axes[0].set_title(f"Runner RSS, max {max(rss) if rss else 0.0:.1f} MiB")

    axes[1].plot(x, mem_avail, color="#2CA02C", linewidth=1.1, label="available memory GiB")
    axes[1].plot(x, swap_used, color="#D62728", linewidth=1.0, label="swap used MiB")
    axes[1].set_ylabel("GiB / MiB")
    axes[1].legend(fontsize=8)

    axes[2].plot(x, run_dir_mib, color="#7B3294", linewidth=1.1)
    axes[2].set_ylabel("run dir MiB")

    axes[3].plot(x, runtime_completed, color="#1F77B4", linewidth=1.0, label="RuntimeEpochs completed")
    axes[3].plot(x, parameter_completed, color="#2CA02C", linewidth=1.0, label="ParameterEpochs completed")
    axes[3].set_ylabel("completed")
    axes[3].set_xlabel("wall-clock minute from runner start")
    axes[3].legend(fontsize=8)

    phase_changes: list[tuple[float, str]] = []
    previous = None
    for xpos, row in zip(x, rows):
        phase = row.get("current_phase", "")
        if phase != previous:
            phase_changes.append((xpos, phase))
            previous = phase
    label_map = {
        "host_prepare": "prepare",
        "runtime_rollback": "live done\nrollback start",
        "runtime_rollback:host": "host\nrollback",
        "runtime_rollback:artifact_cleanup": "artifact\ncleanup",
        "completed": "done",
    }
    important = [
        item
        for item in phase_changes
        if item[1] in label_map
    ]
    for ax in axes:
        ax.grid(True, alpha=0.25)
        for xpos, _phase in important[:30]:
            ax.axvline(xpos, color="#999999", linewidth=0.5, alpha=0.32)
    for xpos, phase in important[:10]:
        axes[3].text(
            xpos,
            max(runtime_completed + parameter_completed) * 0.98,
            label_map.get(phase, phase),
            rotation=90,
            fontsize=7,
            va="top",
            ha="center",
        )

    filename = "07_resource_progress_timeline.png"
    savefig(fig, out_dir, filename)
    return FigureRecord(
        filename,
        "Resource and progress timeline",
        "RSS, memory, swap, artifact growth, and completed epoch counts against wall-clock runner time.",
        ["resource_usage.jsonl", "resource_budget.json"],
    )


def figure_ping_rtt_stability(data: dict[str, Any], out_dir: Path) -> FigureRecord:
    watchers = data.get("ping_watchers", {}).get("watchers", [])
    watcher = watchers[0] if watchers else {"samples": [], "watcher_id": "no-watcher"}
    samples = list(watcher.get("samples", []))
    fig = plt.figure(figsize=(16, 9.5))
    fig.suptitle("Ping RTT stability during real runtime", fontsize=18, fontweight="bold")
    gs = gridspec.GridSpec(2, 1, figure=fig, height_ratios=[1.35, 0.85])
    ax0 = fig.add_subplot(gs[0, 0])
    ax1 = fig.add_subplot(gs[1, 0], sharex=ax0)

    if not samples:
        ax0.text(0.5, 0.5, "No ping watcher samples found", ha="center", va="center", fontsize=14)
        ax1.axis("off")
    else:
        base = float(samples[0]["timestamp"])
        first_success = next(
            (float(sample["timestamp"]) for sample in samples if sample.get("rtt_avg_ms") is not None),
            base,
        )
        success_samples = [
            sample for sample in samples if sample.get("rtt_avg_ms") is not None
        ]
        rtts = [float(sample["rtt_avg_ms"]) for sample in success_samples]
        y_cap = max(80.0, min(250.0, pct(rtts, 99.0) * 1.15 if rtts else 80.0))
        success_x = np.array([(float(sample["timestamp"]) - base) / 60.0 for sample in success_samples])
        success_y = np.array([min(float(sample["rtt_avg_ms"]), y_cap) for sample in success_samples])
        outlier_mask = np.array([float(sample["rtt_avg_ms"]) > y_cap for sample in success_samples], dtype=bool)

        ax0.scatter(success_x[~outlier_mask], success_y[~outlier_mask], s=4, alpha=0.18, color="#1F77B4", label="success RTT avg")
        if outlier_mask.any():
            ax0.scatter(success_x[outlier_mask], success_y[outlier_mask], s=12, alpha=0.55, color="#D62728", marker="^", label=f"RTT clipped above {y_cap:.0f} ms")

        bin_seconds = 30.0
        bins: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for sample in samples:
            bin_index = int((float(sample["timestamp"]) - base) // bin_seconds)
            bins[bin_index].append(sample)
        bin_minutes = []
        median_rtt = []
        p95_rtt = []
        success_rates = []
        loss_rates = []
        unreachable_rates = []
        for bin_index in sorted(bins):
            rows = bins[bin_index]
            center = (bin_index * bin_seconds + bin_seconds / 2.0) / 60.0
            values = [float(row["rtt_avg_ms"]) for row in rows if row.get("rtt_avg_ms") is not None]
            if values:
                bin_minutes.append(center)
                median_rtt.append(pct(values, 50))
                p95_rtt.append(pct(values, 95))
            total = len(rows)
            if total:
                success_rates.append(sum(1 for row in rows if row.get("status") == "success") / total)
                loss_rates.append(sum(1 for row in rows if row.get("status") == "loss") / total)
                unreachable_rates.append(sum(1 for row in rows if row.get("status") == "unreachable") / total)

        ax0.plot(
            bin_minutes,
            [min(value, y_cap) for value in median_rtt],
            color="#0B4F9C",
            linewidth=1.6,
            label="30s rolling median",
        )
        ax0.plot(
            bin_minutes,
            [min(value, y_cap) for value in p95_rtt],
            color="#FF7F0E",
            linewidth=1.2,
            label="30s rolling p95",
        )
        ax0.axvline((first_success - base) / 60.0, color="#2CA02C", linestyle="--", linewidth=1.0, label="first success")

        rate_x = np.array([(idx * bin_seconds + bin_seconds / 2.0) / 60.0 for idx in sorted(bins)])
        success_arr = np.array(success_rates)
        loss_arr = np.array(loss_rates)
        unreach_arr = np.array(unreachable_rates)
        ax1.stackplot(
            rate_x,
            success_arr,
            loss_arr,
            unreach_arr,
            colors=["#2CA02C", "#FFBF00", "#D62728"],
            labels=["success", "loss", "unreachable"],
            alpha=0.82,
        )

        marker_labels = {
            "runtime_rollback": "live done / rollback",
            "runtime_rollback:host": "host rollback",
            "runtime_rollback:artifact_cleanup": "artifact cleanup",
            "completed": "done",
        }
        for marker in data.get("phase_markers", []):
            phase = str(marker.get("phase"))
            if phase not in marker_labels:
                continue
            xpos = (float(marker.get("timestamp")) - base) / 60.0
            for ax in (ax0, ax1):
                ax.axvline(xpos, color="#777777", linewidth=0.8, alpha=0.45)
            ax1.text(xpos, 0.98, marker_labels[phase], rotation=90, ha="center", va="top", fontsize=7)

        live_end = next(
            (
                float(marker.get("timestamp"))
                for marker in data.get("phase_markers", [])
                if marker.get("phase") == "runtime_rollback"
            ),
            float(samples[-1]["timestamp"]),
        )
        live_values = [
            float(sample["rtt_avg_ms"])
            for sample in success_samples
            if first_success <= float(sample["timestamp"]) <= live_end
        ]
        recent_values = [
            float(sample["rtt_avg_ms"])
            for sample in success_samples
            if live_end - 1800.0 <= float(sample["timestamp"]) <= live_end
        ]
        summary = (
            f"{watcher.get('watcher_id')} | live success RTT median/p95 "
            f"{pct(live_values, 50):.1f}/{pct(live_values, 95):.1f} ms | "
            f"last 30 min before rollback median/p95 {pct(recent_values, 50):.1f}/{pct(recent_values, 95):.1f} ms"
        )
        ax0.text(
            0.01,
            0.88,
            summary,
            transform=ax0.transAxes,
            fontsize=9,
            va="top",
            bbox={"facecolor": "white", "alpha": 0.82, "edgecolor": "#dddddd"},
        )

    ax0.set_ylabel("RTT avg ms")
    ax0.set_ylim(0, y_cap * 1.15 if samples else 1)
    ax0.set_title("Successful ping RTT samples and rolling distribution")
    ax0.grid(True, alpha=0.25)
    ax0.legend(ncol=4, fontsize=8, loc="upper left", bbox_to_anchor=(0.0, 1.02))
    ax1.set_ylabel("30s window share")
    ax1.set_xlabel("wall-clock minute from watcher start")
    ax1.set_ylim(0, 1)
    ax1.set_title("Watcher status mix")
    ax1.grid(True, alpha=0.25)
    ax1.legend(ncol=3, fontsize=8, loc="upper right")
    fig.text(
        0.02,
        0.01,
        "RTT timestamps are wall-clock watcher times from the real runtime ping monitor, not simulated orbit time.",
        fontsize=9,
        color="#444444",
    )

    filename = "10_ping_rtt_stability.png"
    savefig(fig, out_dir, filename)
    return FigureRecord(
        filename,
        "Ping RTT stability",
        "Real watcher ping RTT, rolling median/p95, and success/loss/unreachable mix over wall-clock time.",
        ["watcher_ut101_to_as154-host-new.log", "ping_results.json", "resource_usage.jsonl"],
    )


def orbit_snapshot_data(data: dict[str, Any]) -> dict[str, Any]:
    topology = data["topology"]
    positions = data["positions"]
    example = data["path_change_example"]
    slice_id = (example or {}).get("slice_id")
    slice_data = None
    for item in positions.get("slices", []):
        if slice_id and item.get("slice_id") == slice_id:
            slice_data = item
            break
    if slice_data is None:
        slice_data = positions.get("slices", [])[0]
    sat_states = slice_data.get("satellite_states", {})
    sat_to_plane: dict[str, int] = {}
    for plane in topology.get("orbital_planes", []):
        for sat_id in plane.get("satellite_ids", []):
            sat_to_plane[sat_id] = int(plane.get("plane_index", 0) or 0)
    return {"slice": slice_data, "sat_states": sat_states, "sat_to_plane": sat_to_plane}


def figure_orbit_static(data: dict[str, Any], out_dir: Path) -> FigureRecord:
    snap = orbit_snapshot_data(data)
    sat_states = snap["sat_states"]
    sat_to_plane = snap["sat_to_plane"]
    topology = data["topology"]
    ground = data["ground_manifest"].get("effective_ground_locations", {})
    zones = data["zone_model"].get("zones", [])
    example = data["path_change_example"]

    fig = plt.figure(figsize=(13.5, 11))
    ax = fig.add_subplot(111, projection="3d")
    fig.suptitle("Static 3D snapshot: orbit planes, ground catalog, and example path", fontsize=16, fontweight="bold")

    u = np.linspace(0, 2 * np.pi, 80)
    v = np.linspace(0, np.pi, 40)
    xs = EARTH_RADIUS_KM * np.outer(np.cos(u), np.sin(v))
    ys = EARTH_RADIUS_KM * np.outer(np.sin(u), np.sin(v))
    zs = EARTH_RADIUS_KM * np.outer(np.ones_like(u), np.cos(v))
    ax.plot_surface(xs, ys, zs, color="#8EC7E8", alpha=0.24, linewidth=0)
    ax.plot_wireframe(xs, ys, zs, color="#B8D8E8", linewidth=0.25, alpha=0.35)

    colors = {1: "#4E79A7", 2: "#F28E2B", 3: "#59A14F", 4: "#E15759"}
    for plane_idx in sorted(set(sat_to_plane.values())):
        sat_ids = [sid for sid, pidx in sat_to_plane.items() if pidx == plane_idx and sid in sat_states]
        coords = np.array([[sat_states[sid]["x_km"], sat_states[sid]["y_km"], sat_states[sid]["z_km"]] for sid in sat_ids])
        if len(coords):
            ax.scatter(coords[:, 0], coords[:, 1], coords[:, 2], s=14, color=colors.get(plane_idx, "#777777"), label=f"plane {plane_idx}")

    for node_id, loc in ground.items():
        if node_id.startswith("gw"):
            x, y, z = latlon_to_ecef(fnum(loc.get("latitude_deg")), fnum(loc.get("longitude_deg")), fnum(loc.get("altitude_km")))
            ax.scatter([x], [y], [z], marker="^", s=80, color="#111111", label="gateway" if node_id == "gw1" else None)
            ax.text(x, y, z, node_id, fontsize=8)

    for ut in topology.get("user_terminals", []):
        loc = ut.get("catalog_coordinates") or {}
        x, y, z = latlon_to_ecef(fnum(loc.get("latitude_deg")), fnum(loc.get("longitude_deg")), fnum(loc.get("altitude_km")))
        surface = np.array([x, y, z], dtype=float)
        lifted = surface * 1.035
        ax.plot(
            [surface[0] * 1.004, lifted[0]],
            [surface[1] * 1.004, lifted[1]],
            [surface[2] * 1.004, lifted[2]],
            color="#7B3294",
            linewidth=0.7,
            alpha=0.72,
        )
        ax.scatter(
            [lifted[0]],
            [lifted[1]],
            [lifted[2]],
            marker="o",
            s=48,
            color="#7B3294",
            edgecolors="#FFFFFF",
            linewidths=0.5,
            label="UT" if ut.get("ut_id") == "ut101" else None,
        )
        ax.text(lifted[0] * 1.01, lifted[1] * 1.01, lifted[2] * 1.01, ut.get("ut_id", ""), fontsize=6, color="#5B1B7A")

    for zone in zones:
        loc = zone.get("geometry") or {}
        actual_radius = fnum(zone.get("radius_km"), 0.0)
        display_radius = max(actual_radius, 180.0)
        ring = np.array(
            zone_circle_ecef_points(
                lat_deg=fnum(loc.get("latitude_deg")),
                lon_deg=fnum(loc.get("longitude_deg")),
                radius_km=display_radius,
            )
        )
        color = "#00C46A" if zone.get("required_for_runtime") else "#999999"
        ax.plot(ring[:, 0], ring[:, 1], ring[:, 2], color=color, linewidth=1.8, alpha=0.82)
        x, y, z = latlon_to_ecef(
            fnum(loc.get("latitude_deg")),
            fnum(loc.get("longitude_deg")),
            fnum(loc.get("altitude_km")),
        )
        ax.text(x, y, z, f"{zone.get('zone_id', '')}\n{actual_radius:.0f}km zone", fontsize=7)

    if example:
        coords = path_coords(example["after"].get("path_string", ""), data, snap)
        if len(coords) >= 2:
            arr = np.array(coords)
            ax.plot(arr[:, 0], arr[:, 1], arr[:, 2], color="#FFD92F", linewidth=3.0, label="example path")

    limit = 7800
    ax.set_xlim(-limit, limit)
    ax.set_ylim(-limit, limit)
    ax.set_zlim(-limit, limit)
    ax.set_xlabel("x km")
    ax.set_ylabel("y km")
    ax.set_zlabel("z km")
    ax.view_init(elev=25, azim=38)
    ax.legend(loc="upper left", fontsize=8)
    ax.text2D(
        0.02,
        0.02,
        f"Snapshot: {snap['slice'].get('slice_id')} at {snap['slice'].get('timestamp_utc')} | "
        "zone footprints shown as rings; 25km catalog radius is visually enlarged for global-scale readability",
        transform=ax.transAxes,
        fontsize=9,
    )

    filename = "08_orbit_3d_static_snapshot.png"
    savefig(fig, out_dir, filename)
    return FigureRecord(
        filename,
        "Static 3D orbit snapshot",
        "Static 3D view of orbit planes, gateways, UTs, zones, and the highlighted example path.",
        ["propagated_positions.json", "topology.json", "ground_manifest.json", "zone_model.json", "epoch_execution_details.jsonl"],
    )


def path_coords(path_string: str, data: dict[str, Any], snap: dict[str, Any]) -> list[tuple[float, float, float]]:
    sat_states = snap["sat_states"]
    ground = data["ground_manifest"].get("effective_ground_locations", {})
    topology = data["topology"]
    coords: list[tuple[float, float, float]] = []
    for node in path_nodes(path_string):
        if node in sat_states:
            state = sat_states[node]
            coords.append((state["x_km"], state["y_km"], state["z_km"]))
            continue
        if node in ground:
            loc = ground[node]
            coords.append(latlon_to_ecef(fnum(loc.get("latitude_deg")), fnum(loc.get("longitude_deg")), fnum(loc.get("altitude_km"))))
            continue
        ut = next((item for item in topology.get("user_terminals", []) if item.get("ut_id") == node), None)
        if ut:
            loc = ut.get("catalog_coordinates") or {}
            coords.append(latlon_to_ecef(fnum(loc.get("latitude_deg")), fnum(loc.get("longitude_deg")), fnum(loc.get("altitude_km"))))
    return coords


def figure_orbit_interactive_html(data: dict[str, Any], out_dir: Path) -> FigureRecord:
    three_js = THREE_VENDOR_DIR / "three.min.js"
    controls_js = THREE_VENDOR_DIR / "OrbitControls.js"
    earth_texture = THREE_VENDOR_DIR / "textures" / "earth_atmos_2048.jpg"
    missing_assets = [path for path in (three_js, controls_js, earth_texture) if not path.exists()]
    if missing_assets:
        raise FileNotFoundError(
            "missing Three.js orbit assets: "
            + ", ".join(str(path) for path in missing_assets)
        )

    snap = orbit_snapshot_data(data)
    sat_states = snap["sat_states"]
    sat_to_plane = snap["sat_to_plane"]
    topology = data["topology"]
    ground = data["ground_manifest"].get("effective_ground_locations", {})
    zones = data["zone_model"].get("zones", [])
    example = data["path_change_example"]
    plane_payload: dict[str, dict[str, list[Any]]] = {}
    for plane in topology.get("orbital_planes", []):
        plane_idx = str(plane.get("plane_index", 0))
        payload = {"positions": [], "text": []}
        for sat_id in plane.get("satellite_ids", []):
            state = sat_states.get(sat_id)
            if not state:
                continue
            payload["positions"].append([state["x_km"], state["y_km"], state["z_km"]])
            payload["text"].append(sat_id)
        if payload["positions"]:
            plane_payload[plane_idx] = payload

    ground_payload = {
        "gw": {"positions": [], "text": []},
        "ut": {"positions": [], "text": []},
        "zone": {
            "centers": [],
            "rings": [],
            "text": [],
            "required": [],
            "actual_radius_km": [],
            "display_radius_km": [],
        },
    }
    for node_id, loc in ground.items():
        if node_id.startswith("gw"):
            x, y, z = latlon_to_ecef(fnum(loc.get("latitude_deg")), fnum(loc.get("longitude_deg")), fnum(loc.get("altitude_km")))
            ground_payload["gw"]["positions"].append([x, y, z])
            ground_payload["gw"]["text"].append(node_id)
    for ut in topology.get("user_terminals", []):
        loc = ut.get("catalog_coordinates") or {}
        x, y, z = latlon_to_ecef(fnum(loc.get("latitude_deg")), fnum(loc.get("longitude_deg")), fnum(loc.get("altitude_km")))
        ground_payload["ut"]["positions"].append([x, y, z])
        ground_payload["ut"]["text"].append(ut.get("ut_id"))
    for zone in zones:
        loc = zone.get("geometry") or {}
        lat = fnum(loc.get("latitude_deg"))
        lon = fnum(loc.get("longitude_deg"))
        x, y, z = latlon_to_ecef(lat, lon, fnum(loc.get("altitude_km")))
        actual_radius = fnum(zone.get("radius_km"), 0.0)
        display_radius = max(actual_radius, 180.0)
        ring = zone_circle_ecef_points(
            lat_deg=lat,
            lon_deg=lon,
            radius_km=display_radius,
            samples=128,
        )
        ground_payload["zone"]["centers"].append([x, y, z])
        ground_payload["zone"]["rings"].append([list(point) for point in ring])
        role = "required" if zone.get("required_for_runtime") else "optional"
        scale_note = (
            f", shown {display_radius / actual_radius:.1f}x"
            if actual_radius > 0 and display_radius > actual_radius
            else ""
        )
        ground_payload["zone"]["text"].append(
            f"{zone.get('zone_id')} ({role}, radius {actual_radius:.0f} km{scale_note})"
        )
        ground_payload["zone"]["required"].append(bool(zone.get("required_for_runtime")))
        ground_payload["zone"]["actual_radius_km"].append(actual_radius)
        ground_payload["zone"]["display_radius_km"].append(display_radius)

    path_payload = {"positions": [], "text": []}
    if example:
        coords = path_coords(example["after"].get("path_string", ""), data, snap)
        for node, coord in zip(path_nodes(example["after"].get("path_string", "")), coords):
            path_payload["positions"].append(list(coord))
            path_payload["text"].append(node)

    payload = {
        "planes": plane_payload,
        "ground": ground_payload,
        "path": path_payload,
        "snapshot": {
            "slice_id": snap["slice"].get("slice_id"),
            "timestamp_utc": snap["slice"].get("timestamp_utc"),
        },
        "earth_radius_km": EARTH_RADIUS_KM,
    }
    three_inline = inline_script(three_js)
    controls_inline = inline_script(controls_js)
    earth_texture_uri = data_uri(earth_texture, "image/jpeg")

    html_text = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Interactive Earth orbit snapshot</title>
  <script>
{three_inline}
  </script>
  <script>
{controls_inline}
  </script>
  <style>
    body {{
      margin: 0;
      overflow: hidden;
      background: #02040a;
      color: #f3f6fb;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
    }}
    #scene {{ width: 100vw; height: 100vh; display: block; }}
    #hud {{
      position: fixed;
      left: 16px;
      top: 14px;
      max-width: 430px;
      padding: 12px 14px;
      border: 1px solid rgba(255,255,255,.18);
      border-radius: 8px;
      background: rgba(4, 10, 22, .78);
      backdrop-filter: blur(8px);
      line-height: 1.4;
      font-size: 13px;
    }}
    #hud h1 {{ margin: 0 0 6px 0; font-size: 16px; }}
    #legend {{ margin-top: 8px; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 4px 12px; }}
    .swatch {{ display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; vertical-align: -1px; }}
    #fallback {{ display: none; padding: 24px; }}
  </style>
</head>
<body>
  <canvas id="scene"></canvas>
  <div id="fallback">Three.js did not load. Check local vendor assets under satellite/assets/vendor/three-r128/.</div>
  <div id="hud">
    <h1>S5 1-shell orbit with Earth</h1>
    <div>Drag to rotate, scroll to zoom, right-drag to pan.</div>
    <div>Snapshot: {html.escape(str(payload["snapshot"]["slice_id"]))} at {html.escape(str(payload["snapshot"]["timestamp_utc"]))}</div>
    <div id="legend">
      <span><i class="swatch" style="background:#4E79A7"></i>plane 1</span>
      <span><i class="swatch" style="background:#F28E2B"></i>plane 2</span>
      <span><i class="swatch" style="background:#59A14F"></i>plane 3</span>
      <span><i class="swatch" style="background:#E15759"></i>plane 4</span>
      <span><i class="swatch" style="background:#111"></i>gateway</span>
      <span><i class="swatch" style="background:#7B3294"></i>UT</span>
      <span><i class="swatch" style="background:#00C46A"></i>zone footprint</span>
      <span><i class="swatch" style="background:#FFD92F"></i>example path</span>
    </div>
    <div style="margin-top:8px;color:#b9c7d9">Catalog zone radius is 25 km; rings are visually enlarged on the globe so the footprint is visible.</div>
  </div>
  <script>
  const payload = {json.dumps(payload)};
  const EARTH_TEXTURE = "{earth_texture_uri}";

  if (!window.THREE) {{
    document.getElementById("fallback").style.display = "block";
    throw new Error("Three.js not loaded");
  }}

  const canvas = document.getElementById("scene");
  const renderer = new THREE.WebGLRenderer({{ canvas, antialias: true, alpha: false }});
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setSize(window.innerWidth, window.innerHeight);
  renderer.outputEncoding = THREE.sRGBEncoding;

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x02040a);
  const camera = new THREE.PerspectiveCamera(45, window.innerWidth / window.innerHeight, 1, 50000);
  camera.position.set(14500, 9200, 15000);

  const controls = new THREE.OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.06;
  controls.minDistance = 7200;
  controls.maxDistance = 30000;

  scene.add(new THREE.AmbientLight(0xffffff, 0.72));
  const sunlight = new THREE.DirectionalLight(0xffffff, 1.05);
  sunlight.position.set(15000, 8000, 12000);
  scene.add(sunlight);

  function ecefToThree(p) {{
    return new THREE.Vector3(p[0], p[2], p[1]);
  }}

  function addTextSprite(label, position, color = "#ffffff", size = 380, lift = 1.018) {{
    const canvas = document.createElement("canvas");
    canvas.width = 512;
    canvas.height = 128;
    const ctx = canvas.getContext("2d");
    ctx.font = "bold 42px system-ui, sans-serif";
    ctx.fillStyle = "rgba(0,0,0,.55)";
    ctx.fillRect(0, 18, 512, 70);
    ctx.fillStyle = color;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(label, 256, 54);
    const texture = new THREE.CanvasTexture(canvas);
    texture.needsUpdate = true;
    const material = new THREE.SpriteMaterial({{ map: texture, transparent: true, depthWrite: false }});
    const sprite = new THREE.Sprite(material);
    sprite.position.copy(position.clone().multiplyScalar(lift));
    sprite.scale.set(size * 2.8, size * 0.7, 1);
    scene.add(sprite);
    return sprite;
  }}

  const earthTexture = new THREE.TextureLoader().load(EARTH_TEXTURE);
  earthTexture.encoding = THREE.sRGBEncoding;
  const earth = new THREE.Mesh(
    new THREE.SphereGeometry(payload.earth_radius_km, 128, 96),
    new THREE.MeshPhongMaterial({{ map: earthTexture, shininess: 8 }})
  );
  scene.add(earth);

  const atmosphere = new THREE.Mesh(
    new THREE.SphereGeometry(payload.earth_radius_km * 1.012, 128, 96),
    new THREE.MeshBasicMaterial({{ color: 0x7fc8ff, transparent: true, opacity: 0.13, depthWrite: false }})
  );
  scene.add(atmosphere);

  const starGeometry = new THREE.BufferGeometry();
  const starVertices = [];
  for (let i = 0; i < 1200; i++) {{
    const radius = 26000 + Math.random() * 12000;
    const theta = Math.random() * Math.PI * 2;
    const phi = Math.acos(2 * Math.random() - 1);
    starVertices.push(
      radius * Math.sin(phi) * Math.cos(theta),
      radius * Math.cos(phi),
      radius * Math.sin(phi) * Math.sin(theta)
    );
  }}
  starGeometry.setAttribute("position", new THREE.Float32BufferAttribute(starVertices, 3));
  scene.add(new THREE.Points(starGeometry, new THREE.PointsMaterial({{ color: 0xffffff, size: 45, opacity: 0.45, transparent: true }})));

  const planeColors = {{ "1": 0x4E79A7, "2": 0xF28E2B, "3": 0x59A14F, "4": 0xE15759, "0": 0x999999 }};
  for (const [plane, points] of Object.entries(payload.planes)) {{
    const vertices = [];
    const converted = points.positions.map(ecefToThree);
    converted.forEach(v => vertices.push(v.x, v.y, v.z));
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.Float32BufferAttribute(vertices, 3));
    scene.add(new THREE.Points(
      geometry,
      new THREE.PointsMaterial({{ color: planeColors[plane] || planeColors["0"], size: 70, sizeAttenuation: true }})
    ));
    if (converted.length > 1) {{
      const lineGeometry = new THREE.BufferGeometry().setFromPoints([...converted, converted[0]]);
      scene.add(new THREE.Line(
        lineGeometry,
        new THREE.LineBasicMaterial({{ color: planeColors[plane] || planeColors["0"], transparent: true, opacity: 0.34 }})
      ));
    }}
  }}

  function addMarker(position, color, radius, geometryKind = "sphere", lift = 1.012) {{
    const geometry = geometryKind === "box"
      ? new THREE.BoxGeometry(radius * 1.8, radius * 1.8, radius * 1.8)
      : new THREE.SphereGeometry(radius, 18, 18);
    const material = new THREE.MeshPhongMaterial({{ color }});
    const marker = new THREE.Mesh(geometry, material);
    marker.position.copy(position.clone().multiplyScalar(lift));
    scene.add(marker);
    return marker;
  }}

  function addRadialStem(position, fromLift, toLift, color, opacity = 0.72) {{
    const geometry = new THREE.BufferGeometry().setFromPoints([
      position.clone().multiplyScalar(fromLift),
      position.clone().multiplyScalar(toLift)
    ]);
    scene.add(new THREE.Line(
      geometry,
      new THREE.LineBasicMaterial({{ color, transparent: true, opacity }})
    ));
  }}

  payload.ground.gw.positions.forEach((p, idx) => {{
    const v = ecefToThree(p);
    addMarker(v, 0x111111, 105);
    addTextSprite(payload.ground.gw.text[idx], v, "#ffffff", 300);
  }});
  payload.ground.ut.positions.forEach((p, idx) => {{
    const v = ecefToThree(p);
    addRadialStem(v, 1.014, 1.055, 0x7B3294, 0.72);
    addMarker(v, 0x7B3294, 92, "sphere", 1.055);
    addTextSprite(payload.ground.ut.text[idx], v, "#ead7ff", 210, 1.078);
  }});
  payload.ground.zone.rings.forEach((ring, idx) => {{
    const converted = ring.map(ecefToThree);
    const zoneGeometry = new THREE.BufferGeometry().setFromPoints(converted);
    scene.add(new THREE.Line(
      zoneGeometry,
      new THREE.LineBasicMaterial({{
        color: payload.ground.zone.required[idx] ? 0x00C46A : 0x999999,
        transparent: true,
        opacity: 0.92
      }})
    ));
    const center = ecefToThree(payload.ground.zone.centers[idx]);
    addTextSprite(payload.ground.zone.text[idx], center, "#d6ffe8", 390);
  }});

  if (payload.path.positions.length > 1) {{
    const converted = payload.path.positions.map(ecefToThree);
    const pathGeometry = new THREE.BufferGeometry().setFromPoints(converted);
    scene.add(new THREE.Line(pathGeometry, new THREE.LineBasicMaterial({{ color: 0xFFD92F, linewidth: 4 }})));
    converted.forEach((v, idx) => {{
      addMarker(v, 0xFFD92F, 82);
      addTextSprite(payload.path.text[idx], v, "#fff1a8", 300);
    }});
  }}

  function animate() {{
    requestAnimationFrame(animate);
    controls.update();
    renderer.render(scene, camera);
  }}
  animate();

  window.addEventListener("resize", () => {{
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
  }});
  </script>
</body>
</html>
"""
    filename = "09_orbit_3d_interactive.html"
    (out_dir / filename).write_text(html_text, encoding="utf-8")
    return FigureRecord(
        filename,
        "Interactive Earth orbit snapshot",
        "Browser-rotatable Three.js view with textured Earth, orbit planes, gateways, UTs, zones, and an example path.",
        [
            "propagated_positions.json",
            "topology.json",
            "ground_manifest.json",
            "zone_model.json",
            "epoch_execution_details.jsonl",
            "satellite/assets/vendor/three-r128/three.min.js",
            "satellite/assets/vendor/three-r128/OrbitControls.js",
            "satellite/assets/vendor/three-r128/textures/earth_atmos_2048.jpg",
        ],
    )


def build_summary(data: dict[str, Any]) -> dict[str, Any]:
    records = data["epoch_records"]
    runtime = [r for r in records if r["epoch_type"] == "runtime_epoch"]
    parameter = [r for r in records if r["epoch_type"] == "parameter_epoch"]
    steady_runtime = runtime[1:] if len(runtime) > 1 else runtime
    diffs = data["fec_diff"].get("diffs", [])
    resources = data["resources"]
    rss = [fnum((row.get("self_process") or {}).get("vmrss_kib")) / 1024.0 for row in resources if row.get("self_process")]
    swap = []
    for row in resources:
        mem = ((row.get("host_resources") or {}).get("memory") or {})
        total = fnum(mem.get("swaptotal_kib"))
        free = fnum(mem.get("swapfree_kib"))
        if total:
            swap.append((total - free) / 1024.0)
    watcher_rtts: list[float] = []
    watcher_statuses: list[str] = []
    for watcher in data.get("ping_watchers", {}).get("watchers", []):
        for sample in watcher.get("samples", []):
            watcher_statuses.append(str(sample.get("status")))
            if sample.get("rtt_avg_ms") is not None:
                watcher_rtts.append(float(sample["rtt_avg_ms"]))
    return {
        "run_id": data["run_manifest"].get("run_id"),
        "runtime_epoch_count": len(runtime),
        "parameter_epoch_count": len(parameter),
        "replenishment_count": data.get("replenishment_count", 0),
        "zone_count": data["zone_model"].get("zone_count"),
        "steady_runtime_wall_seconds": stats(values(steady_runtime, "total_wall")),
        "steady_runtime_data_plane_seconds": stats(values(steady_runtime, "data_plane")),
        "runtime_host_apply_seconds": stats(values(steady_runtime, "host_apply")),
        "runtime_node_apply_seconds": stats(values(steady_runtime, "node_apply")),
        "runtime_pre_cleanup_seconds": stats(values(steady_runtime, "pre_cleanup")),
        "runtime_ready_lag_seconds": stats(values(steady_runtime, "ready_lag")),
        "parameter_wall_seconds": stats(values(parameter, "total_wall")),
        "parameter_qdisc_apply_seconds": stats(values(parameter, "parameter_qdisc")),
        "parameter_applied_operations": stats(values(parameter, "applied_ops")),
        "runtime_applied_operations": stats(values(runtime, "applied_ops")),
        "fec_diff": {
            "changed_fec_groups": stats([fnum(d.get("changed_fec_group_count")) for d in diffs]),
            "skipped_unchanged_fec_groups": stats([fnum(d.get("skipped_unchanged_fec_group_count")) for d in diffs]),
            "affected_nodes": stats([fnum(d.get("affected_node_count")) for d in diffs]),
            "operation_estimate": stats([fnum(d.get("estimated_lfib_route_operation_items")) for d in diffs]),
        },
        "resources": {
            "runner_rss_mib": stats(rss),
            "swap_used_mib": stats(swap),
        },
        "ping_rtt_ms": stats(watcher_rtts),
        "ping_watcher_status_counts": {
            "success": watcher_statuses.count("success"),
            "loss": watcher_statuses.count("loss"),
            "unreachable": watcher_statuses.count("unreachable"),
        },
    }


def write_manifest(run_dir: Path, out_dir: Path, figures: list[FigureRecord], data: dict[str, Any]) -> None:
    manifest = {
        "model": "gz_6zone_2h_figure_manifest_v2",
        "run_dir": str(run_dir),
        "out_dir": str(out_dir),
        "generated_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "figure_count": len(figures),
        "figures": [asdict(record) for record in figures],
        "summary": build_summary(data),
    }
    (out_dir / "figure_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out_dir / "figure_summary.json").write_text(json.dumps(manifest["summary"], indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=RUN_DIR_DEFAULT)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR_DEFAULT)
    args = parser.parse_args()
    if not args.run_dir.exists():
        raise SystemExit(f"run directory not found: {args.run_dir}")

    clean_out_dir(args.out_dir)
    plt.rcParams.update(
        {
            "font.size": 10,
            "axes.titlesize": 12,
            "axes.labelsize": 10,
            "legend.fontsize": 9,
            "figure.facecolor": "white",
            "axes.facecolor": "white",
        }
    )

    data = collect_data(args.run_dir)
    figures = [
        figure_timing_detail(data, args.out_dir),
        figure_workload_timeline(data, args.out_dir),
        figure_timing_workload_overlay(data, args.out_dir),
        figure_fec_diff_timeline(data, args.out_dir),
        figure_large_update_explainer(data, args.out_dir),
        figure_path_transition(data, args.out_dir),
        figure_resource_timeline(data, args.out_dir),
        figure_ping_rtt_stability(data, args.out_dir),
        figure_orbit_static(data, args.out_dir),
        figure_orbit_interactive_html(data, args.out_dir),
    ]
    write_manifest(args.run_dir, args.out_dir, figures, data)
    print(json.dumps({"out_dir": str(args.out_dir), "figure_count": len(figures)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
