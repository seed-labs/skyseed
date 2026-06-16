#!/usr/bin/env python3
# encoding: utf-8

"""Summarize preview-only Starlink distance-matrix artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from _path_setup import bootstrap_app_import_path

SATELLITE_DIR = bootstrap_app_import_path()


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _avg(values: Iterable[float]) -> float:
    rendered = [float(value) for value in values]
    if not rendered:
        return 0.0
    return round(sum(rendered) / float(len(rendered)), 6)


def _runtime_slice_ids(run_dir: Path, planner_preview: Dict[str, Any]) -> List[str]:
    summary_path = run_dir / "runtime_update_plan_summary.json"
    if summary_path.exists():
        records = _load_json(summary_path)
        return [
            str(record.get("slice_id"))
            for record in list(records)
            if isinstance(record, dict) and record.get("slice_id")
        ]
    return [
        str(snapshot.get("slice_id"))
        for snapshot in list(planner_preview.get("snapshots", []))
        if isinstance(snapshot, dict) and snapshot.get("slice_id")
    ]


def _flow_contexts(snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    contexts_by_flow_id: Dict[str, Dict[str, Any]] = {}
    runtime_plan = dict(snapshot.get("runtime_update_plan", {}))
    for subplan in dict(runtime_plan.get("node_subplans", {})).values():
        for action in list(dict(subplan).get("actions", [])):
            flow_context = (
                dict(action)
                .get("intent", {})
                .get("outcome", {})
                .get("flow_context")
            )
            if not isinstance(flow_context, dict):
                continue
            flow_id = str(flow_context.get("flow_id") or "").strip()
            if not flow_id:
                continue
            contexts_by_flow_id.setdefault(flow_id, dict(flow_context))
    return [contexts_by_flow_id[flow_id] for flow_id in sorted(contexts_by_flow_id)]


def _ground_counts(run_dir: Path) -> Dict[str, int]:
    ground_path = run_dir / "ground_manifest.json"
    if not ground_path.exists():
        return {"selected_ut_count": 0, "selected_gateway_count": 0}
    ground = dict(_load_json(ground_path))
    provenance = dict(ground.get("data_provenance", {})).get("ground", {})
    return {
        "selected_ut_count": len(list(dict(provenance).get("ut_coordinates", []))),
        "selected_gateway_count": len(
            list(dict(provenance).get("gateway_coordinates", []))
        ),
    }


def summarize_distance_matrix_run(run_dir: Path) -> Dict[str, Any]:
    run_dir = run_dir.expanduser().resolve()
    planner_preview = dict(_load_json(run_dir / "planner_preview.json"))
    runtime_summary = (
        list(_load_json(run_dir / "runtime_update_plan_summary.json"))
        if (run_dir / "runtime_update_plan_summary.json").exists()
        else []
    )
    runtime_by_slice = {
        str(record.get("slice_id")): dict(record)
        for record in runtime_summary
        if isinstance(record, dict) and record.get("slice_id")
    }
    runtime_slice_ids = set(_runtime_slice_ids(run_dir, planner_preview))
    all_snapshot_count = len(list(planner_preview.get("snapshots", [])))
    runtime_snapshots = [
        dict(snapshot)
        for snapshot in list(planner_preview.get("snapshots", []))
        if str(snapshot.get("slice_id")) in runtime_slice_ids
    ]

    flow_counts: List[int] = []
    isl_hops: List[int] = []
    satellite_path_lengths: List[int] = []
    selected_gateway_counts: Dict[str, int] = {}
    gateway_selection_modes: Dict[str, int] = {}
    covered_uts: set[str] = set()
    runtime_action_counts: List[int] = []
    node_subplan_counts: List[int] = []

    for snapshot in runtime_snapshots:
        slice_id = str(snapshot.get("slice_id"))
        runtime_record = runtime_by_slice.get(slice_id, {})
        if runtime_record:
            runtime_action_counts.append(
                int(runtime_record.get("runtime_update_action_count", 0) or 0)
            )
            node_subplan_counts.append(
                int(runtime_record.get("node_subplan_count", 0) or 0)
            )
        contexts = _flow_contexts(snapshot)
        flow_counts.append(len(contexts))
        for context in contexts:
            path = list(context.get("satellite_path", []))
            satellite_path_lengths.append(len(path))
            isl_hops.append(max(0, len(path) - 1))
            ut_id = str(context.get("ut_id") or "").strip()
            if ut_id:
                covered_uts.add(ut_id)
            gateway_id = str(context.get("selected_gateway_id") or "").strip()
            if gateway_id:
                selected_gateway_counts[gateway_id] = (
                    selected_gateway_counts.get(gateway_id, 0) + 1
                )
            selection_mode = str(context.get("gateway_selection_mode") or "").strip()
            if selection_mode:
                gateway_selection_modes[selection_mode] = (
                    gateway_selection_modes.get(selection_mode, 0) + 1
                )

    ground_counts = _ground_counts(run_dir)
    return {
        "run_dir": str(run_dir),
        "run_id": run_dir.name,
        "all_snapshot_count": all_snapshot_count,
        "runtime_snapshot_count": len(runtime_snapshots),
        "selected_ut_count": ground_counts["selected_ut_count"],
        "covered_runtime_ut_count": len(covered_uts),
        "selected_gateway_count": ground_counts["selected_gateway_count"],
        "runtime_flow_count_min": min(flow_counts) if flow_counts else 0,
        "runtime_flow_count_max": max(flow_counts) if flow_counts else 0,
        "runtime_flow_count_avg": _avg(flow_counts),
        "isl_hop_count_avg": _avg(isl_hops),
        "isl_hop_count_max": max(isl_hops) if isl_hops else 0,
        "satellite_path_length_avg": _avg(satellite_path_lengths),
        "satellite_path_length_max": (
            max(satellite_path_lengths) if satellite_path_lengths else 0
        ),
        "runtime_update_action_count_avg": _avg(runtime_action_counts),
        "runtime_update_action_count_max": (
            max(runtime_action_counts) if runtime_action_counts else 0
        ),
        "node_subplan_count_avg": _avg(node_subplan_counts),
        "node_subplan_count_max": max(node_subplan_counts) if node_subplan_counts else 0,
        "selected_gateway_counts": {
            gateway_id: selected_gateway_counts[gateway_id]
            for gateway_id in sorted(selected_gateway_counts)
        },
        "gateway_selection_modes": {
            mode: gateway_selection_modes[mode]
            for mode in sorted(gateway_selection_modes)
        },
    }


def render_distance_matrix_summary(report: Dict[str, Any]) -> str:
    lines = []
    for run in list(report.get("runs", [])):
        lines.append(
            " | ".join(
                [
                    str(run.get("run_id")),
                    f"runtime_snapshots={run.get('runtime_snapshot_count')}",
                    f"covered_uts={run.get('covered_runtime_ut_count')}/{run.get('selected_ut_count')}",
                    f"isl_hops_avg={run.get('isl_hop_count_avg')}",
                    f"isl_hops_max={run.get('isl_hop_count_max')}",
                    f"path_len_avg={run.get('satellite_path_length_avg')}",
                    f"path_len_max={run.get('satellite_path_length_max')}",
                    f"runtime_actions_avg={run.get('runtime_update_action_count_avg')}",
                    f"runtime_actions_max={run.get('runtime_update_action_count_max')}",
                    f"node_subplans_avg={run.get('node_subplan_count_avg')}",
                    f"node_subplans_max={run.get('node_subplan_count_max')}",
                    "gateways="
                    + ",".join(
                        f"{gateway}:{count}"
                        for gateway, count in dict(
                            run.get("selected_gateway_counts", {})
                        ).items()
                    ),
                ]
            )
        )
    return "\n".join(lines)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect preview-only Starlink distance-matrix run artifacts."
    )
    parser.add_argument(
        "run_dirs",
        nargs="+",
        type=Path,
        help="Run directories containing planner_preview.json.",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Render text summary or JSON.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = {
        "run_count": len(list(args.run_dirs)),
        "runs": [
            summarize_distance_matrix_run(Path(run_dir))
            for run_dir in list(args.run_dirs)
        ],
    }
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    print(render_distance_matrix_summary(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
