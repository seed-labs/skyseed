"""Compose and write the post-run Traffic / Path Replay Tool artifact."""

from __future__ import annotations

from hashlib import file_digest
import os
from pathlib import Path
import resource
from time import perf_counter
from typing import Any

from tools.observation._presentation.html import _write_compact_json, write_json, write_text

from .projection import _project_traffic_replay
from .reader import read_traffic_replay
from .renderer import render_traffic_replay_page
from ._spatial import _write_spatial_assets


def collect_traffic_replay_data(
    telemetry_path: Path,
    physical_frames_path: Path,
    planning_path: Path,
    bpf_compiler_path: Path,
    workload_path: Path | None = None,
    probe_plan_path: Path | None = None,
    probe_execution_path: Path | None = None,
    runner_events_path: Path | None = None,
    physical_link_inspection_path: Path | None = None,
) -> dict[str, Any]:
    """Read detached durable artifacts and build one replay projection."""

    return _project_traffic_replay(
        read_traffic_replay(
            telemetry_path,
            physical_frames_path,
            planning_path,
            bpf_compiler_path,
            workload_path,
            probe_plan_path,
            probe_execution_path,
            runner_events_path,
            physical_link_inspection_path,
        ),
        reader_validated=True,
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
    physical_link_inspection_path: Path | None = None,
) -> dict[str, object]:
    """Write one dependency-free traffic evidence bundle."""

    if not Path(telemetry_path).is_file():
        from .sender_records import write_sender_observation
        return write_sender_observation(probe_execution_path or Path(telemetry_path).with_name('probe-execution.jsonl'), directory,
            physical_frames_path=physical_frames_path, planning_path=planning_path,
            probe_plan_path=probe_plan_path, runner_events_path=runner_events_path,
            provenance_paths=(bpf_compiler_path, workload_path, physical_link_inspection_path))
    started = perf_counter()
    source_files = (*sorted(Path(__file__).parent.glob("*.py")),
                    *(Path(__file__).parent.parent / "_presentation" / name for name in ("shell_display.py", "spatial_display.py", "display_geometry.py", "earth_reference.py")))
    tool_source = {"projection_version": "compact-generation-identity-v4-spatial-v2",
                   "files": {p.name: _digest(p) for p in source_files}}
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
        *((Path(physical_link_inspection_path),) if physical_link_inspection_path is not None else ()),
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
        physical_link_inspection_path,
    )
    if tool_source["files"] != {p.name: _digest(p) for p in source_files}:
        raise RuntimeError("Traffic Tool source changed during replay")
    data["tool_source"] = tool_source
    data["tool_accounting"] = {
        "input_bytes": sum(path.stat().st_size for path in inputs),
        "collection_seconds": perf_counter() - started,
        "peak_rss_bytes": int(
            resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        )
        * 1024,
    }
    spatial_paths = _write_spatial_assets(data, output)
    page = render_traffic_replay_page(data)
    _write_compact_json(data_path, data)
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
    payloads = (data_path, page_path, summary_path, *spatial_paths)
    payload_records = [_file_record(path, output) for path in payloads]
    write_json(
        inventory_path,
        {
            "format_version": 2,
            "inputs": {
                "traffic_telemetry": _input_record(telemetry, output),
                "physical_frames": _input_record(frames, output),
                "planning": _input_record(planning, output),
                "bpf_compiler": _input_record(compiler, output),
                **({} if physical_link_inspection_path is None else {
                    "physical_links": _input_record(Path(physical_link_inspection_path).resolve(), output)
                }),
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
            "files": payload_records,
        },
    )
    checksum_files = (*payloads, inventory_path)
    write_text(
        checksum_path,
        "".join(
            f"{record['sha256']}  {record['path']}\n"
            for record in (*payload_records, _file_record(inventory_path, output))
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
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


__all__ = ["collect_traffic_replay_data", "write_traffic_replay_tool"]
