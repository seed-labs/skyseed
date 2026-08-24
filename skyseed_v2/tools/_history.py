"""Read append-only inspection evidence without owning its retention."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_jsonl_records(path: Path) -> tuple[dict[str, Any], ...]:
    records = []
    with Path(path).open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"invalid inspection JSONL at line {line_number}"
                ) from error
            if not isinstance(value, dict):
                raise ValueError("inspection JSONL records must be objects")
            records.append(value)
    return tuple(records)


__all__ = ["read_jsonl_records"]
