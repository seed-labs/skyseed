"""Compose and write the post-run Traffic / Path Replay Tool artifact."""

from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import resource
from time import perf_counter
from typing import Any

from tools._html import write_json, write_text

from .projection import project_traffic_replay
from .reader import read_traffic_replay
from .renderer import render_traffic_replay_page


def collect_traffic_replay_data(
    telemetry_path: Path,
    physical_frames_path: Path,
    planning_path: Path,
    bpf_compiler_path: Path,
    workload_path: Path | None = None,
    probe_plan_path: Path | None = None,
    probe_execution_path: Path | None = None,
    runner_events_path: Path | None = None,
) -> dict[str, Any]:
    """Read detached durable artifacts and build one replay projection."""

    return project_traffic_replay(
        read_traffic_replay(
            telemetry_path,
            physical_frames_path,
            planning_path,
            bpf_compiler_path,
            workload_path,
            probe_plan_path,
            probe_execution_path,
            runner_events_path,
        )
    )


def write_traffic_replay_tool(
    telemetry_path: Path,
    physical_frames_path: Path,
    planning_path: Path,
    bpf_compiler_path: Path,
    workload_path: Path | None,
    directory: Path,
    probe_plan_path: Path | None = None,
    probe_execution_path: Path | None = None,
    runner_events_path: Path | None = None,
) -> dict[str, object]:
    """Write one dependency-free traffic evidence bundle."""

    started = perf_counter()
    telemetry = Path(telemetry_path).resolve()
    frames = Path(physical_frames_path).resolve()
    planning = Path(planning_path).resolve()
    compiler = Path(bpf_compiler_path).resolve()
    workload = None if workload_path is None else Path(workload_path).resolve()
    optional = tuple(
        None if value is None else Path(value).resolve()
        for value in (probe_plan_path, probe_execution_path, runner_events_path)
    )
    if any(value is not None for value in optional) and not all(
        value is not None for value in optional
    ):
        raise ValueError(
            "Traffic qualification requires plan, execution, and Runner events"
        )
    probe_plan, probe_execution, runner_events = optional
    inputs = (
        telemetry,
        frames,
        planning,
        compiler,
        *((workload,) if workload is not None else ()),
        *(value for value in optional if value is not None),
    )
    output = Path(directory).resolve()
    output.mkdir(parents=True, exist_ok=True)
    data_path = output / "traffic-replay.json"
    page_path = output / "index.html"
    summary_path = output / "summary.txt"
    inventory_path = output / "inventory.json"
    checksum_path = output / "checksums.sha256"
    page_path.unlink(missing_ok=True)
    data = collect_traffic_replay_data(
        telemetry,
        frames,
        planning,
        compiler,
        workload,
        probe_plan,
        probe_execution,
        runner_events,
    )
    data["tool_accounting"] = {
        "input_bytes": sum(path.stat().st_size for path in inputs),
        "collection_seconds": perf_counter() - started,
        "peak_rss_bytes": int(
            resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        )
        * 1024,
    }
    page = render_traffic_replay_page(data)
    write_json(data_path, data)
    write_text(page_path, page)
    inventory = data["inventory"]
    overview = data["overview"]
    write_text(
        summary_path,
        "\n".join(
            (
                f"status={overview['qualification_status'] or 'PASS'}",
                "tool=traffic-path-replay",
                "authority=non-authoritative-view",
                f"telemetry_complete={overview['telemetry_complete']}",
                *(f"{key}={value}" for key, value in inventory.items()),
                "",
            )
        ),
    )
    payloads = (data_path, page_path, summary_path)
    write_json(
        inventory_path,
        {
            "format_version": 2,
            "inputs": {
                "traffic_telemetry": _input_record(telemetry, output),
                "physical_frames": _input_record(frames, output),
                "planning": _input_record(planning, output),
                "bpf_compiler": _input_record(compiler, output),
                **(
                    {}
                    if workload is None
                    else {"traffic_workload": _input_record(workload, output)}
                ),
                **(
                    {}
                    if probe_plan is None
                    else {
                        "traffic_probe_plan": _input_record(probe_plan, output),
                        "traffic_probe_execution": _input_record(
                            probe_execution, output
                        ),
                        "runner_events": _input_record(runner_events, output),
                    }
                ),
            },
            "files": [_file_record(path, output) for path in payloads],
        },
    )
    checksum_files = (*payloads, inventory_path)
    write_text(
        checksum_path,
        "".join(
            f"{_digest(path)}  {path.relative_to(output).as_posix()}\n"
            for path in checksum_files
        ),
    )
    elapsed = perf_counter() - started
    return {
        "status": overview["qualification_status"] or "PASS",
        "message": (
            f"{inventory['ping_transactions']} Ping transaction(s) / "
            f"{inventory['unclassified_observations']} unclassified"
        ),
        "directory": output,
        "files": tuple(
            path.relative_to(output).as_posix()
            for path in (*checksum_files, checksum_path)
        ),
        "inventory": dict(inventory),
        "artifact_metrics": {
            "wall_seconds": elapsed,
            "input_bytes": sum(path.stat().st_size for path in inputs),
            "output_bytes": sum(
                path.stat().st_size
                for path in (*checksum_files, checksum_path)
            ),
            "peak_rss_bytes": int(
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            )
            * 1024,
        },
    }


def _input_record(path: Path | None, root: Path) -> dict[str, object]:
    assert path is not None
    return {
        "path": Path(os.path.relpath(path, root)).as_posix(),
        "size_bytes": path.stat().st_size,
        "sha256": _digest(path),
    }


def _file_record(path: Path, root: Path) -> dict[str, object]:
    return {
        "path": path.relative_to(root).as_posix(),
        "size_bytes": path.stat().st_size,
        "sha256": _digest(path),
    }


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


__all__ = ["collect_traffic_replay_data", "write_traffic_replay_tool"]
