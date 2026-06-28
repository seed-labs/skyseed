#!/usr/bin/env python3
# encoding: utf-8

"""Run the small live tc-BPF role/counter repro."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from satellite.bpf_mpls_live import run_bpf_live_role_repro


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("satellite/output/bpf-live-role-repro-20260622"),
    )
    args = parser.parse_args()
    report = run_bpf_live_role_repro(output_dir=args.output_dir)
    print(
        json.dumps(
            {
                "ok": report.get("ok"),
                "reason": report.get("reason"),
                "json": dict(report.get("artifacts") or {}).get("json"),
                "markdown": dict(report.get("artifacts") or {}).get("markdown"),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
