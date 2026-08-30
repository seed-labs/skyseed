"""Read append-only inspection evidence without owning its retention."""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any


def read_jsonl_records(path: Path) -> tuple[dict[str, Any], ...]:
    return tuple(iter_jsonl_records(path))


def iter_jsonl_records(path: Path):
    """Yield plain or gzip JSONL records without retaining unrelated rows."""

    source = Path(path)
    opener = (
        gzip.open(source, "rt", encoding="utf-8")
        if source.suffix == ".gz"
        else source.open(encoding="utf-8")
    )
    with opener as stream:
        for line_number, line in enumerate(stream, start=1):
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"invalid inspection JSONL at line {line_number}"
                ) from error
            if not isinstance(value, dict):
                raise ValueError("inspection JSONL records must be objects")
            yield value


__all__ = ["iter_jsonl_records", "read_jsonl_records"]
