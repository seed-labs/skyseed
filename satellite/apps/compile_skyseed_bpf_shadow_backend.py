#!/usr/bin/env python3
# encoding: utf-8

"""Compile Skyseed artifacts into an experimental BPF shadow backend report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from satellite.bpf_backend import (  # noqa: E402
    compact_for_baseline,
    compile_from_run_dir,
    consistency_check_for_run_dir,
    render_text_summary,
    write_json,
)


DEFAULT_RUN_DIR = (
    REPO_ROOT
    / "satellite"
    / "output"
    / "starlink_shell_experiments"
    / "gz-z6metadata-2h-gated-20260611-0824"
)
DEFAULT_JSON = (
    REPO_ROOT / "satellite" / "baselines" / "skyseed_bpf_shadow_backend_summary.json"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    parser.add_argument(
        "--input-mode",
        choices=["existing_artifact", "short_runtime_shadow"],
        default="existing_artifact",
        help=(
            "existing_artifact reads an existing run directory; short_runtime_shadow "
            "uses the same contract for a freshly produced bounded runtime run-dir."
        ),
    )
    parser.add_argument("--json-out", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--consistency-json-out", type=Path, default=None)
    parser.add_argument("--full-json-out", type=Path, default=None)
    parser.add_argument(
        "--shadow-artifact-out",
        type=Path,
        default=None,
        help="Optional sidecar path for short-runtime shadow artifacts.",
    )
    parser.add_argument("--active-runtime-generation", type=int, default=0)
    parser.add_argument("--active-parameter-generation", type=int, default=0)
    parser.add_argument("--format", choices=["text", "json"], default="text")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    compiled = compile_from_run_dir(
        args.run_dir,
        input_mode=args.input_mode,
        active_runtime_generation=args.active_runtime_generation,
        active_parameter_generation=args.active_parameter_generation,
    )
    report = compact_for_baseline(compiled)
    consistency = consistency_check_for_run_dir(args.run_dir, report)
    if args.json_out:
        write_json(args.json_out, report)
    if args.consistency_json_out:
        write_json(args.consistency_json_out, consistency)
    if args.full_json_out:
        write_json(args.full_json_out, compiled)
    if args.shadow_artifact_out:
        shadow_artifact = {
            "schema_version": 1,
            "artifact_kind": "skyseed_bpf_shadow_backend_artifact",
            "input_mode": args.input_mode,
            "source_run_dir": str(args.run_dir),
            "backend_mode": compiled["backend_mode"],
            "scope": compiled["scope"],
            "compact_summary": compiled["compact_summary"],
            "backend_audit_report": compiled["backend_audit_report"],
            "consistency": consistency,
            "bpf_maps": compiled["bpf_maps"],
        }
        write_json(args.shadow_artifact_out, shadow_artifact)
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_text_summary(report))
        print(f"consistency_ok={str(consistency['ok']).lower()}")
        if args.json_out:
            print(f"wrote {args.json_out}")
        if args.consistency_json_out:
            print(f"wrote consistency {args.consistency_json_out}")
        if args.shadow_artifact_out:
            print(f"wrote shadow artifact {args.shadow_artifact_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
