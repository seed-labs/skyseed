"""Write Traffic Replay from detached generated-execution evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .inspection import write_traffic_replay_tool


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--telemetry", type=Path, required=True)
    parser.add_argument("--physical-frames", type=Path, required=True)
    parser.add_argument("--physical-links", type=Path)
    parser.add_argument("--planning", type=Path, required=True)
    parser.add_argument("--bpf-compiler", type=Path, required=True)
    parser.add_argument("--workload", type=Path)
    parser.add_argument("--probe-plan", type=Path)
    parser.add_argument("--probe-execution", type=Path)
    parser.add_argument("--runner-events", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    result = write_traffic_replay_tool(
        arguments.telemetry,
        arguments.physical_frames,
        arguments.planning,
        arguments.bpf_compiler,
        arguments.workload,
        arguments.output,
        arguments.probe_plan,
        arguments.probe_execution,
        arguments.runner_events,
        arguments.physical_links,
    )
    print(json.dumps({
        "artifact_type": "traffic-tool-completion",
        "scope": "Tool function; excludes Python startup",
        "artifact_metrics": result["artifact_metrics"],
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
