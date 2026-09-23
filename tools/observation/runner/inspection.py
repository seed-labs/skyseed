"""Compose and write the post-run Runner Inspection Tool artifact."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import json
from math import isfinite
import sys
from typing import Any, Mapping

from tools.observation._presentation.html import write_json, write_text

from .projection import project_runner_inspection
from .reader import read_runner_event_history
from .renderer import render_runner_page


def collect_runner_data(
    runner_snapshot: Mapping[str, object],
    runtime_outcome: Mapping[str, object],
    *,
    event_history_path: Path | None = None,
    deployment_startup_path: Path | None = None,
) -> dict[str, Any]:
    """Project detached Runner and invocation facts without live authority."""

    snapshot = dict(runner_snapshot)
    if event_history_path is not None:
        events = read_runner_event_history(event_history_path)
        history = snapshot.get("event_history")
        if isinstance(history, Mapping):
            durable = history.get("durable")
            if isinstance(durable, Mapping) and durable.get("complete") is not True:
                raise ValueError(
                    "Runner bounded event history is incomplete or overflowed"
                )
            total = history.get("total_count")
            if isinstance(total, int) and total != len(events):
                raise ValueError("Runner durable event history is incomplete")
        snapshot["events"] = events
        snapshot["event_history"] = {
            "scope": "complete",
            "returned_count": len(events),
            "total_count": len(events),
            "durable_history_enabled": True,
            "durable": (
                history.get("durable")
                if isinstance(history, Mapping)
                else None
            ),
            "exact_source_path": str(Path(event_history_path).resolve()),
        }
    deployment_startup = None
    recording = None
    if deployment_startup_path is not None:
        try:
            deployment_startup = _read_deployment_startup(deployment_startup_path)
        except (OSError, ValueError, OverflowError) as error:
            recording = {'status': 'unavailable', 'path': str(deployment_startup_path),
                         'error': str(error)}
            try:
                print(f'optional deployment startup evidence unavailable: {error}', file=sys.stderr)
            except Exception:
                pass
    data = project_runner_inspection(
        snapshot,
        runtime_outcome,
        deployment_startup=deployment_startup,
    )
    if recording is not None:
        data['scale_safety']['deployment_startup_recording'] = recording
    return data


def write_runner_tool(
    runner_snapshot: Mapping[str, object],
    runtime_outcome: Mapping[str, object],
    directory: Path,
    *,
    event_history_path: Path | None = None,
    deployment_startup_path: Path | None = None,
) -> dict[str, object]:
    """Write one deterministic standalone post-run inspection bundle."""

    data = collect_runner_data(
        runner_snapshot,
        runtime_outcome,
        event_history_path=event_history_path,
        deployment_startup_path=deployment_startup_path,
    )
    page = render_runner_page(data)
    output = Path(directory)
    output.mkdir(parents=True, exist_ok=True)
    data_path = output / "runner-inspection.json"
    page_path = output / "index.html"
    summary_path = output / "summary.txt"
    inventory_path = output / "inventory.json"
    checksum_path = output / "checksums.sha256"

    write_json(data_path, data)
    write_text(page_path, page)
    inventory = data["inventory"]
    outcome = data["runtime_outcome"]
    write_text(
        summary_path,
        "\n".join(
            (
                f"status={str(outcome['status']).upper()}",
                "tool=runner-inspection",
                "authority=non-authoritative-view",
                f"exit_code={outcome.get('exit_code')}",
                *(f"{key}={value}" for key, value in inventory.items()),
                "",
            )
        ),
    )
    payload_files = (page_path, data_path, summary_path)
    payload_records = [_file_record(path, output) for path in payload_files]
    write_json(
        inventory_path,
        {
            "format_version": 1,
            "files": payload_records,
        },
    )
    checksum_files = (*payload_files, inventory_path)
    write_text(
        checksum_path,
        "".join(
            f"{record['sha256']}  {record['path']}\n"
            for record in (*payload_records, _file_record(inventory_path, output))
        ),
    )
    return {
        "status": str(outcome["status"]).upper(),
        "message": (
            f"{inventory['events']} events / "
            f"{inventory['activations']} activations / "
            f"{inventory['runner_failures']} Runner failures"
        ),
        "directory": output,
        "files": tuple(path.name for path in (*checksum_files, checksum_path)),
        "inventory": inventory,
    }


def _file_record(path: Path, root: Path) -> dict[str, object]:
    return {
        "path": path.relative_to(root).as_posix(),
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_deployment_startup(path: Path) -> dict[str, object]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if (
        not isinstance(value, dict)
        or value.get("artifact_type") != "compose-startup-evidence"
        or value.get("format_version") != 1
        or value.get("status") not in ("completed", "failed")
        or not isinstance(value.get("duration_seconds"), (int, float))
        or isinstance(value.get("duration_seconds"), bool)
        or not isfinite(value["duration_seconds"])
        or float(value["duration_seconds"]) < 0
        or isinstance(value.get("exit_code"), bool)
        or not isinstance(value.get("exit_code"), int)
        or (value.get("status") == "completed") != (value.get("exit_code") == 0)
    ):
        raise ValueError("Compose startup evidence is invalid")
    return value


__all__ = ["collect_runner_data", "write_runner_tool"]
