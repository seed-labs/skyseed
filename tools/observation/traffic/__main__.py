"""Write Traffic Replay from detached generated-execution evidence."""

from __future__ import annotations

import argparse
from contextlib import nullcontext
import json
from pathlib import Path

from skyseed.experiment.output.history import _reading_completed_range
from .inspection import write_traffic_replay_tool


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--telemetry", type=Path, required=True)
    parser.add_argument("--physical-frames", type=Path, required=True)
    parser.add_argument("--workload", type=Path)
    parser.add_argument("--probe-execution", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    # run.sh may finish while retained calculation publishes another request.
    # Pin the experiment-owned completion once across every Traffic reader.
    root = arguments.experiment.resolve()
    completed = root / "results/experiment/completed.json"
    reading = (_reading_completed_range(root, json.loads(completed.read_text()))
               if completed.is_file() else nullcontext())
    with reading:
        result = write_traffic_replay_tool(
            arguments.telemetry,
            arguments.physical_frames,
            arguments.output,
            workload_path=arguments.workload,
            probe_execution_path=arguments.probe_execution,
        )
    print(json.dumps({
        "artifact_type": "traffic-tool-completion",
        "scope": "Tool function; excludes Python startup",
        "artifact_metrics": result["artifact_metrics"],
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
