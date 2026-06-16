#!/usr/bin/env python3
# encoding: utf-8

"""Run planned-shell preview matrices for multiple Starlink shell sizes."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

from _path_setup import bootstrap_app_import_path

SATELLITE_DIR = bootstrap_app_import_path()


DEFAULT_CONFIG_ROOT = SATELLITE_DIR / "configs" / "starlink_shell_preview_matrix"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run planned-shell preview matrices and summarize results."
    )
    parser.add_argument(
        "--planned-shell-id",
        default="S5",
        help="Planned shell preset to exercise.",
    )
    parser.add_argument(
        "--preview-sizes",
        default="24,48,72,172",
        help="Comma-separated satellite counts to preview.",
    )
    parser.add_argument(
        "--slice-count",
        type=int,
        default=3,
        help="Slice count per preview run.",
    )
    parser.add_argument(
        "--step-seconds",
        type=float,
        default=120.0,
        help="Propagation step per preview run.",
    )
    parser.add_argument(
        "--max-uts",
        type=int,
        default=2,
        help="Selected UT count.",
    )
    parser.add_argument(
        "--max-gateways",
        type=int,
        default=3,
        help="Selected gateway count.",
    )
    parser.add_argument(
        "--ground-placement",
        choices=("catalog", "shell-aware"),
        default="shell-aware",
        help="Ground placement mode passed through to the preview builder.",
    )
    parser.add_argument(
        "--config-root",
        default=str(DEFAULT_CONFIG_ROOT),
        help="Artifact root for per-size preview outputs.",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output matrix summary as text or JSON.",
    )
    return parser.parse_args()


def _preview_sizes(value: str) -> List[int]:
    return [int(item.strip()) for item in value.split(",") if item.strip()]


def _run_preview(
    *,
    shell_id: str,
    satellite_count: int,
    slice_count: int,
    step_seconds: float,
    max_uts: int,
    max_gateways: int,
    ground_placement: str,
    config_root: Path,
) -> Dict[str, Any]:
    config_dir = config_root / f"{shell_id.lower()}_{satellite_count:03d}"
    command = [
        sys.executable,
        str((SATELLITE_DIR / "apps" / "build_starlink_shell_preview.py").resolve()),
        "--orbit-input-mode",
        "planned-shell",
        "--planned-shell-id",
        shell_id,
        "--max-satellites",
        str(satellite_count),
        "--slice-count",
        str(slice_count),
        "--step-seconds",
        str(step_seconds),
        "--max-uts",
        str(max_uts),
        "--max-gateways",
        str(max_gateways),
        "--ground-placement",
        str(ground_placement),
        "--skip-seed-render",
        "--config-dir",
        str(config_dir),
        "--format",
        "json",
    ]
    completed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        cwd=str(SATELLITE_DIR.parent),
    )
    result = json.loads(completed.stdout)
    snapshot_summaries = list(result.get("snapshot_preview", {}).get("snapshot_summaries", []))
    return {
        "satellite_count": satellite_count,
        "selected_satellite_count": result.get("selected_satellite_count"),
        "ut_count": result.get("ground_model", {}).get("selected_ut_count"),
        "gateway_count": result.get("ground_model", {}).get("selected_gateway_count"),
        "time_slices": result.get("time_slices", {}),
        "planner_valid": result.get("planner_preview", {}).get("all_valid"),
        "active_links_per_slice": [
            {
                "slice_id": summary.get("slice_id"),
                "active_link_count": summary.get("active_link_count"),
            }
            for summary in snapshot_summaries
        ],
        "serving_satellite_changes": result.get("snapshot_preview", {}).get("serving_satellite_changes", []),
        "gateway_exit_changes": result.get("snapshot_preview", {}).get("gateway_exit_changes", []),
        "network_mutation_operations": [
            {
                "slice_id": summary.get("slice_id"),
                "operation_count": summary.get("network_mutation_operation_count"),
            }
            for summary in snapshot_summaries
        ],
        "topology_artifacts": result.get("topology_artifacts", {}),
    }


def main() -> int:
    args = parse_args()
    config_root = Path(args.config_root).resolve()
    results = [
        _run_preview(
            shell_id=str(args.planned_shell_id),
            satellite_count=count,
            slice_count=int(args.slice_count),
            step_seconds=float(args.step_seconds),
            max_uts=int(args.max_uts),
            max_gateways=int(args.max_gateways),
            ground_placement=str(args.ground_placement),
            config_root=config_root,
        )
        for count in _preview_sizes(str(args.preview_sizes))
    ]
    summary = {
        "planned_shell_id": str(args.planned_shell_id),
        "preview_sizes": _preview_sizes(str(args.preview_sizes)),
        "slice_count": int(args.slice_count),
        "step_seconds": float(args.step_seconds),
        "ground_placement": str(args.ground_placement),
        "results": results,
    }
    if args.format == "json":
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    print(f"Planned shell {args.planned_shell_id} preview matrix")
    for result in results:
        print(
            f"- {result['satellite_count']} sats: planner_valid={result['planner_valid']} "
            f"UTs={result['ut_count']} gateways={result['gateway_count']} "
            f"slices={result['time_slices'].get('slice_count')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
