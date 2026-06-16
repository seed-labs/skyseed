#!/usr/bin/env python3
# encoding: utf-8

"""Compare multiple temporal run directories using durable artifacts."""

from __future__ import annotations

import argparse
from pathlib import Path

from _path_setup import bootstrap_app_import_path

bootstrap_app_import_path()

from temporal_run_comparator import (  # noqa: E402
    compare_temporal_runs,
    render_temporal_run_comparison_json,
    render_temporal_run_comparison_summary,
)
from temporal_run_inspector import DEFAULT_STALL_THRESHOLD_SECONDS  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare multiple Skyseed temporal run directories using checkpoint and "
            "finalized artifacts."
        ),
    )
    parser.add_argument(
        "run_dirs",
        nargs="+",
        help="One or more starlink_shell_experiments run directories.",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format.",
    )
    parser.add_argument(
        "--stall-threshold-seconds",
        type=float,
        default=DEFAULT_STALL_THRESHOLD_SECONDS,
        help="Pass-through stall threshold used by the underlying run inspector.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = compare_temporal_runs(
        [Path(run_dir) for run_dir in args.run_dirs],
        stall_threshold_seconds=float(args.stall_threshold_seconds),
    )
    if args.format == "json":
        print(render_temporal_run_comparison_json(report), end="")
    else:
        print(render_temporal_run_comparison_summary(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
