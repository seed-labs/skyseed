"""Compose and write the post-run Runner Inspection Tool artifact."""

from __future__ import annotations

from hashlib import sha256
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from tools._html import write_json, write_text

from .projection import project_runner_inspection
from .reader import read_runner_event_history
from .renderer import render_runner_page


def collect_runner_data(
    runner_snapshot: Mapping[str, object],
    runtime_outcome: Mapping[str, object],
    *,
    event_history_path: Path | None = None,
) -> dict[str, Any]:
    """Project detached Runner and invocation facts without live authority."""

    snapshot = deepcopy(dict(runner_snapshot))
    if event_history_path is not None:
        events = read_runner_event_history(event_history_path)
        history = snapshot.get("event_history")
        if isinstance(history, Mapping):
            total = history.get("total_count")
            if isinstance(total, int) and total != len(events):
                raise ValueError("Runner durable event history is incomplete")
        snapshot["events"] = events
        snapshot["event_history"] = {
            "scope": "complete",
            "returned_count": len(events),
            "total_count": len(events),
            "durable_history_enabled": True,
        }
    return project_runner_inspection(snapshot, runtime_outcome)


def write_runner_tool(
    runner_snapshot: Mapping[str, object],
    runtime_outcome: Mapping[str, object],
    directory: Path,
    *,
    event_history_path: Path | None = None,
) -> dict[str, object]:
    """Write one deterministic standalone post-run inspection bundle."""

    data = collect_runner_data(
        runner_snapshot,
        runtime_outcome,
        event_history_path=event_history_path,
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
    write_json(
        inventory_path,
        {
            "format_version": 1,
            "files": tuple(_file_record(path, output) for path in payload_files),
        },
    )
    checksum_files = (*payload_files, inventory_path)
    write_text(
        checksum_path,
        "".join(
            f"{_sha256(path)}  {path.relative_to(output).as_posix()}\n"
            for path in checksum_files
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


__all__ = ["collect_runner_data", "write_runner_tool"]
