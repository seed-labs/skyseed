"""Provide authority-free mechanical primitives for durable evidence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
import gzip
import json
from pathlib import Path
from typing import Any

from .node_id import NodeId


def _reset_jsonl(path: Path, header: Mapping[str, object]) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with _open_jsonl(destination, "wt") as stream:
        stream.write(
            json.dumps(dict(header), sort_keys=True, separators=(",", ":"))
            + "\n"
        )
    return destination


def _append_jsonl(path: Path, record: Mapping[str, object]) -> None:
    with _open_jsonl(Path(path), "at") as stream:
        stream.write(
            json.dumps(dict(record), sort_keys=True, separators=(",", ":"))
            + "\n"
        )
        stream.flush()


def _read_jsonl(path: Path) -> tuple[dict[str, Any], ...]:
    return tuple(_iter_jsonl(path))


def _iter_jsonl(path: Path):
    """Yield conventional plain or gzip JSONL records one at a time."""

    with _open_jsonl(Path(path), "rt") as stream:
        for line_number, line in enumerate(stream, start=1):
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"invalid durable evidence JSON at line {line_number}"
                ) from error
            if not isinstance(value, dict):
                raise ValueError("durable evidence records must be JSON objects")
            yield value


def _open_jsonl(path: Path, mode: str):
    if path.suffix == ".gz":
        return gzip.open(path, mode, encoding="utf-8", compresslevel=1)
    return path.open(mode.removesuffix("t"), encoding="utf-8")


def _semantic_value(value: object) -> object:
    """Detach one immutable semantic value into deterministic JSON data."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return {"$type": "datetime", "value": value.isoformat()}
    if isinstance(value, Enum):
        return {
            "$type": f"{type(value).__module__}.{type(value).__qualname__}",
            "value": value.value,
        }
    if isinstance(value, NodeId):
        return {
            "$type": "NodeId",
            "asn": value.asn,
            "entity_kind": value.entity_kind,
            "entity_id": value.entity_id,
        }
    if is_dataclass(value):
        return {
            "$type": f"{type(value).__module__}.{type(value).__qualname__}",
            **{
                item.name: _semantic_value(getattr(value, item.name))
                for item in fields(value)
                if not item.name.startswith("_")
            },
        }
    if isinstance(value, Mapping):
        if all(isinstance(key, str) for key in value):
            return {
                str(key): _semantic_value(item)
                for key, item in sorted(value.items(), key=lambda pair: pair[0])
            }
        return {
            "$type": "mapping",
            "items": [
                {"key": _semantic_value(key), "value": _semantic_value(item)}
                for key, item in sorted(
                    value.items(), key=lambda pair: repr(pair[0])
                )
            ],
        }
    if isinstance(value, (tuple, list)):
        return [_semantic_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [_semantic_value(item) for item in sorted(value, key=repr)]
    raise TypeError(
        f"unsupported durable semantic value: {type(value).__module__}."
        f"{type(value).__qualname__}"
    )
