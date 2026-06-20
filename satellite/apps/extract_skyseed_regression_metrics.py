#!/usr/bin/env python3
# encoding: utf-8

"""Extract compact Skyseed regression metrics from an existing run directory."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from _path_setup import bootstrap_app_import_path

bootstrap_app_import_path()

from regression_metrics import extract_regression_metrics, render_metrics_text, write_json  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract Skyseed 1-shell regression metrics from finalized artifacts."
    )
    parser.add_argument("--run-dir", required=True, help="Finalized Skyseed run directory.")
    parser.add_argument("--output", help="Optional JSON output path.")
    parser.add_argument("--format", choices=("text", "json"), default="json")
    args = parser.parse_args()

    metrics = extract_regression_metrics(Path(args.run_dir))
    if args.output:
        write_json(Path(args.output), metrics)
    if args.format == "json":
        print(json.dumps(metrics, sort_keys=True, separators=(",", ":")))
    else:
        print(render_metrics_text(metrics))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
