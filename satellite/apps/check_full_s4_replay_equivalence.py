#!/usr/bin/env python3
# encoding: utf-8

"""Check shortened full-S4 replay equivalence before runtime startup."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_SATELLITE = Path(__file__).resolve().parents[1]
if str(REPO_SATELLITE) not in sys.path:
    sys.path.insert(0, str(REPO_SATELLITE))

from full_s4_replay_equivalence import write_replay_equivalence_report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare regenerated shortened full-S4 artifacts with a source run."
    )
    parser.add_argument(
        "--source-run-dir",
        required=True,
        type=Path,
        help="Prior prepared run directory to use as the equivalence source.",
    )
    parser.add_argument(
        "--candidate-run-dir",
        required=True,
        type=Path,
        help="Newly prepared run directory to validate before startup.",
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        default=None,
        help="Optional JSON report path. Defaults inside the candidate run dir.",
    )
    parser.add_argument(
        "--markdown-path",
        type=Path,
        default=None,
        help="Optional Markdown report path. Defaults inside the candidate run dir.",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format for the command summary.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = write_replay_equivalence_report(
        source_run_dir=args.source_run_dir.resolve(),
        candidate_run_dir=args.candidate_run_dir.resolve(),
        report_path=args.report_path,
        markdown_path=args.markdown_path,
    )
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"equivalent={report['equivalent']}")
        print(f"mismatch_count={report['mismatch_count']}")
        print(f"report_path={report['report_path']}")
        if report["mismatches"]:
            first = report["mismatches"][0]
            print(f"first_mismatch={first.get('name')}: {first.get('mismatch')}")
    return 0 if bool(report.get("equivalent")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
