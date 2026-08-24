"""Read complete durable BpfRunner event evidence for the Runner Tool."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tools._history import read_jsonl_records


def read_runner_event_history(path: Path) -> tuple[dict[str, Any], ...]:
    records = read_jsonl_records(path)
    if not records or records[0] != {
        "record_type": "history_header",
        "format_version": 1,
        "owner": "BpfRunner",
    }:
        raise ValueError("Runner durable event history header is invalid")
    events = []
    for value in records[1:]:
        if value.get("record_type") != "runner_event":
            raise ValueError("Runner durable history contains an unknown record")
        events.append(
            {key: item for key, item in value.items() if key != "record_type"}
        )
    return tuple(events)


__all__ = ["read_runner_event_history"]
