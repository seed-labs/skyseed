#!/usr/bin/env python3
# encoding: utf-8

"""Replay frozen prepared inputs through runtime consumer contracts offline."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Dict

try:
    from ._path_setup import bootstrap_app_import_path
except ImportError:  # pragma: no cover - direct script execution path.
    from _path_setup import bootstrap_app_import_path

bootstrap_app_import_path()

try:
    from .run_starlink_shell_experiment import (
        _prepared_bundle_executable_runtime_inputs,
        _prepared_runtime_consumer_contract_gate,
    )
except ImportError:  # pragma: no cover - direct script execution path.
    from run_starlink_shell_experiment import (
        _prepared_bundle_executable_runtime_inputs,
        _prepared_runtime_consumer_contract_gate,
    )


SCHEMA_VERSION = "skyseed-prepared-runtime-consumer-contract-replay-v1"


def replay_prepared_runtime_consumer_contracts(run_dir: Path) -> Dict[str, Any]:
    resolved_run_dir = run_dir.expanduser().resolve()
    bundle_path = resolved_run_dir / "frozen_prepared_update_bundle.json"
    topology_path = resolved_run_dir / "topology.json"
    started_at = time.perf_counter()
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    executable_inputs = _prepared_bundle_executable_runtime_inputs(bundle)
    normal_runtime_inputs = dict(
        executable_inputs.get("normal_runtime_inputs") or {}
    )
    gate = _prepared_runtime_consumer_contract_gate(
        normal_runtime_inputs=normal_runtime_inputs,
        topology_path=topology_path,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "source_run_dir": str(resolved_run_dir),
        "source_bundle_path": str(bundle_path),
        "source_topology_path": str(topology_path),
        "wall_seconds": round(time.perf_counter() - started_at, 6),
        "ok": gate.get("ok") is True,
        "gate": gate,
        "blockers": list(gate.get("blockers") or []),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = replay_prepared_runtime_consumer_contracts(args.run_dir)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        args.output.expanduser().resolve().write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report.get("ok") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
