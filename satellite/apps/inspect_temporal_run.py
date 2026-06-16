#!/usr/bin/env python3
# encoding: utf-8

"""Summarize the current state of a temporal run from durable artifacts."""

from __future__ import annotations

import argparse
from pathlib import Path

from _path_setup import bootstrap_app_import_path

bootstrap_app_import_path()

from temporal_run_inspector import (  # noqa: E402
    DEFAULT_STALL_THRESHOLD_SECONDS,
    inspect_temporal_run,
    render_temporal_run_json,
    render_temporal_run_summary,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect a live or completed Skyseed temporal run using durable artifacts such as "
            "events.jsonl, runtime_progress.json, timing.json, and temporal_audit.json."
        ),
    )
    parser.add_argument(
        "run_dir",
        help="Path to a starlink_shell_experiments run directory.",
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
        help="Mark the run stalled if seconds_since_progress reaches this threshold.",
    )
    parser.add_argument(
        "--write-report",
        default=None,
        help="Optional path to write the full JSON report.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = inspect_temporal_run(
        Path(args.run_dir),
        stall_threshold_seconds=float(args.stall_threshold_seconds),
    )
    if args.write_report:
        path = Path(args.write_report).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_temporal_run_json(report), encoding="utf-8")
    if args.format == "json":
        print(render_temporal_run_json(report), end="")
    else:
        print(render_temporal_run_summary(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
