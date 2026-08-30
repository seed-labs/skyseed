"""Write Runner inspection from one detached generated-execution handoff."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Mapping

from .inspection import write_runner_tool


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"Runner inspection handoff {label} must be an object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handoff", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--deployment-startup", type=Path)
    arguments = parser.parse_args()

    value = json.loads(arguments.handoff.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("format_version") != 1:
        raise ValueError("unsupported Runner inspection handoff format")
    if value.get("artifact_type") != "runner-inspection-handoff":
        raise ValueError("unsupported Runner inspection handoff type")
    write_runner_tool(
        _mapping(value.get("runner_snapshot"), "runner_snapshot"),
        _mapping(value.get("runtime_outcome"), "runtime_outcome"),
        arguments.output,
        event_history_path=arguments.events,
        deployment_startup_path=arguments.deployment_startup,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
