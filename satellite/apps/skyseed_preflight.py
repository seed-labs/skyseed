#!/usr/bin/env python3
# encoding: utf-8

"""Project-level Skyseed preflight and scoped cleanup guidance."""

from __future__ import annotations

import argparse
from pathlib import Path

from _path_setup import bootstrap_app_import_path

bootstrap_app_import_path()

from skyseed_preflight import (  # noqa: E402
    PREFLIGHT_PROFILES,
    cleanup_skyseed_environment_residue,
    render_preflight_json,
    render_preflight_summary,
    run_skyseed_preflight,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Probe the operational-clean BPF-owned environment for stale Skyseed "
            "runtime state and recurring experiment blockers."
        ),
    )
    parser.add_argument(
        "--profile",
        choices=PREFLIGHT_PROFILES,
        default="operational-clean-bpf-owned",
        help=(
            "Preflight policy profile. The standard path is operational-clean-bpf-"
            "owned and does not inspect Linux native MPLS readiness."
        ),
    )
    parser.add_argument(
        "--cleanup",
        choices=("off", "dry-run", "apply"),
        default="off",
        help=(
            "Optionally plan or apply scoped Skyseed residue cleanup. "
            "'dry-run' only reports docker/interface/lock cleanup commands; "
            "'apply' executes them."
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
    report = run_skyseed_preflight(profile=str(args.profile))
    if str(args.cleanup) != "off":
        cleanup_result = cleanup_skyseed_environment_residue(
            preflight_report=report,
            apply=str(args.cleanup) == "apply",
        )
        report["cleanup_result"] = cleanup_result
        if str(args.cleanup) == "apply":
            post_cleanup_report = run_skyseed_preflight(profile=str(args.profile))
            report = {
                **post_cleanup_report,
                "pre_cleanup_preflight": report,
                "cleanup_result": cleanup_result,
            }
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
