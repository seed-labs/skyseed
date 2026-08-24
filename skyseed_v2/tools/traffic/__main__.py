"""Write Traffic Replay from detached generated-execution evidence."""

from __future__ import annotations

import argparse
from pathlib import Path

from .inspection import write_traffic_replay_tool


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--telemetry", type=Path, required=True)
    parser.add_argument("--slice", type=Path, required=True)
    parser.add_argument("--bpf-compiler", type=Path, required=True)
    parser.add_argument("--workload", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    write_traffic_replay_tool(
        arguments.telemetry,
        arguments.slice,
        arguments.bpf_compiler,
        arguments.workload,
        arguments.output,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
