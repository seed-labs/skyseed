"""Compose and write the post-run Traffic / Path Replay Tool artifact."""

from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
from typing import Any

from tools._html import write_json, write_text

from .projection import project_traffic_replay
from .reader import read_traffic_replay
from .renderer import render_traffic_replay_page


def collect_traffic_replay_data(
    telemetry_path: Path,
    slice_path: Path,
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
            slice_path,
            bpf_compiler_path,
            workload_path,
            probe_plan_path,
            probe_execution_path,
            runner_events_path,
        )
    )


def write_traffic_replay_tool(
    telemetry_path: Path,
    slice_path: Path,
    bpf_compiler_path: Path,
    workload_path: Path | None,
    directory: Path,
    probe_plan_path: Path | None = None,
    probe_execution_path: Path | None = None,
    runner_events_path: Path | None = None,
) -> dict[str, object]:
    """Write one dependency-free offline traffic replay bundle."""

    telemetry = Path(telemetry_path).resolve()
    dynamic = Path(slice_path).resolve()
    compiler = Path(bpf_compiler_path).resolve()
    workload = None if workload_path is None else Path(workload_path).resolve()
    qualification_paths = tuple(
        None if value is None else Path(value).resolve()
        for value in (probe_plan_path, probe_execution_path, runner_events_path)
    )
    if any(value is not None for value in qualification_paths) and not all(
        value is not None for value in qualification_paths
    ):
        raise ValueError(
            "Traffic qualification requires plan, execution, and Runner events"
        )
    probe_plan, probe_execution, runner_events = qualification_paths
    output = Path(directory).resolve()
    if telemetry.parent != output:
        raise ValueError("traffic telemetry must remain in the replay Tool root")
    output.mkdir(parents=True, exist_ok=True)
    data_path = output / "traffic-replay.json"
    page_path = output / "index.html"
    summary_path = output / "summary.txt"
    inventory_path = output / "inventory.json"
    checksum_path = output / "checksums.sha256"
    page_path.unlink(missing_ok=True)

    data = collect_traffic_replay_data(
        telemetry,
        dynamic,
        compiler,
        workload,
        probe_plan,
        probe_execution,
        runner_events,
    )
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
    payload_files = (telemetry, page_path, data_path, summary_path)
    write_json(
        inventory_path,
        {
            "format_version": 1,
            "inputs": {
                "traffic_telemetry": _file_record(telemetry, output),
                "slice": {
                    "path": _relative_path(dynamic, output),
                    "size_bytes": dynamic.stat().st_size,
                    "sha256": _sha256(dynamic),
                },
                "bpf_compiler": {
                    "path": _relative_path(compiler, output),
                    "size_bytes": compiler.stat().st_size,
                    "sha256": _sha256(compiler),
                },
                **(
                    {}
                    if workload is None
                    else {"traffic_workload": _file_record(workload, output)}
                ),
                **(
                    {}
                    if probe_plan is None
                    else {
                        "traffic_probe_plan": _file_record(probe_plan, output),
                        "traffic_probe_execution": _file_record(
                            probe_execution, output  # type: ignore[arg-type]
                        ),
                        "runner_events": _file_record(
                            runner_events, output  # type: ignore[arg-type]
                        ),
                    }
                ),
            },
            "files": tuple(
                _file_record(path, output) for path in payload_files[1:]
            ),
        },
    )
    checksum_files = (
        *payload_files,
        *((workload,) if workload is not None else ()),
        *(
            ()
            if probe_plan is None
            else (
                probe_plan,
                probe_execution,  # type: ignore[misc]
                runner_events,  # type: ignore[misc]
            )
        ),
        inventory_path,
    )
    write_text(
        checksum_path,
        "".join(
            f"{_sha256(path)}  {_relative_path(path, output)}\n"
            for path in checksum_files
        ),
    )
    return {
        "status": overview["qualification_status"] or "PASS",
        "message": (
            f"{inventory['ping_transactions']} Ping transaction(s) / "
            f"{inventory['unclassified_observations']} unclassified observation(s)"
            + (
                ""
                if overview["qualification_status"] is None
                else f" / qualification {overview['qualification_status']}"
            )
        ),
        "directory": output,
        "files": tuple(
            path.name
            for path in (
                telemetry,
                *((workload,) if workload is not None else ()),
                page_path,
                data_path,
                summary_path,
                inventory_path,
                checksum_path,
            )
        ),
        "inventory": inventory,
    }


def _file_record(path: Path, root: Path) -> dict[str, object]:
    return {
        "path": _relative_path(path, root),
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _relative_path(path: Path, root: Path) -> str:
    return Path(os.path.relpath(path, root)).as_posix()


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = ["collect_traffic_replay_data", "write_traffic_replay_tool"]
