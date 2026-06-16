#!/usr/bin/env python3
# encoding: utf-8

"""Project-level Skyseed preflight and scoped cleanup guidance."""

from __future__ import annotations

import argparse
from pathlib import Path

from _path_setup import bootstrap_app_import_path

bootstrap_app_import_path()

from skyseed_preflight import (  # noqa: E402
    render_preflight_json,
    render_preflight_summary,
    run_skyseed_preflight,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Probe for stale Skyseed runtime state, Docker residue, host MPLS readiness, "
            "and other recurring environment blockers before real experiments."
        ),
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format.",
    )
    parser.add_argument(
        "--write-report",
        default=None,
        help="Optional path to write the full JSON report.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = run_skyseed_preflight()
    if args.write_report:
        path = Path(args.write_report).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_preflight_json(report), encoding="utf-8")
    if args.format == "json":
        print(render_preflight_json(report), end="")
    else:
        print(render_preflight_summary(report))
    return 0 if report["assessment"]["ready"] is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
