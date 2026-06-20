#!/usr/bin/env python3
# encoding: utf-8

"""Check a Skyseed run directory against a regression metrics baseline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from _path_setup import bootstrap_app_import_path

bootstrap_app_import_path()

from regression_metrics import (  # noqa: E402
    evaluate_regression,
    extract_regression_metrics,
    read_json,
    render_check_text,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare Skyseed 1-shell regression metrics with a baseline."
    )
    parser.add_argument("--run-dir", required=True, help="Finalized Skyseed run directory.")
    parser.add_argument("--baseline", required=True, help="Regression baseline JSON.")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args()

    metrics = extract_regression_metrics(Path(args.run_dir))
    baseline = read_json(Path(args.baseline))
    report = evaluate_regression(metrics, baseline)
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_check_text(report))
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
