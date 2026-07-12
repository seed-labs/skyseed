#!/usr/bin/env python3
# encoding: utf-8

"""Audit frozen runtime queue producer/consumer coverage without containers."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from ._path_setup import bootstrap_app_import_path
except ImportError:  # pragma: no cover - direct script execution path.
    from _path_setup import bootstrap_app_import_path

bootstrap_app_import_path()

from runtime_queue_coverage_audit import (  # noqa: E402
    audit_runtime_queue_coverage_from_run_dir,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument(
        "--require-actual-evidence",
        action="store_true",
        help=(
            "Require pre_runtime_bpf_owned_prepare.json to contain actual "
            "prepared route identity evidence instead of using planned coverage."
        ),
    )
    parser.add_argument(
        "--expected-horizon-count",
        type=int,
        default=None,
        help=(
            "Fail if normal_runtime_inputs.json does not contain exactly this "
            "many prepared horizon segments."
        ),
    )
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = audit_runtime_queue_coverage_from_run_dir(
        args.run_dir,
        actual_evidence_required=bool(args.require_actual_evidence),
        expected_horizon_count=args.expected_horizon_count,
    )
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if bool(report.get("ok")) else 2


if __name__ == "__main__":
    raise SystemExit(main())
