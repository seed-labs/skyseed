"""Read detached execution observations without importing domain execution."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from math import isfinite
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ObservationRead:
    """Hold one validated, detached observation stream."""

    header: dict[str, Any]
    records: tuple[dict[str, Any], ...]


def read_execution_observations(path: Path) -> ObservationRead:
    """Parse one completed JSONL stream; never execute observed components."""

    source = Path(path)
    values = []
    for line_number, raw_line in enumerate(
        source.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw_line.strip():
            continue
        try:
            value = json.loads(raw_line)
        except json.JSONDecodeError as error:
            raise ValueError(
                f"invalid observation JSON on line {line_number}"
            ) from error
        if not isinstance(value, dict):
            raise TypeError("observation rows must be JSON objects")
        values.append(value)
    if not values:
        raise ValueError("observation stream is empty")

    header = values[0]
    if header.get("record_type") != "execution_observation_header":
        raise ValueError("first observation row must be the header")
    format_version = header.get("format_version")
    if format_version not in {1, 2}:
        raise ValueError("unsupported execution observation format version")
    metadata = header.get("metadata")
    if not isinstance(metadata, dict):
        raise TypeError("observation metadata must be an object")

    records = []
    for sequence, record in enumerate(values[1:]):
        _validate_record(record, sequence, format_version=format_version)
        records.append(record)
    return ObservationRead(deepcopy(header), tuple(deepcopy(records)))


def _validate_record(
    record: dict[str, Any], sequence: int, *, format_version: int
) -> None:
    if record.get("record_type") != "execution_observation":
        raise ValueError("non-header rows must be execution observations")
    if record.get("sequence") != sequence:
        raise ValueError("observation sequence must be continuous and ordered")
    for field_name in ("component", "stage"):
        value = record.get(field_name)
        if not isinstance(value, str) or not value:
            raise ValueError(f"{field_name} must be a non-empty string")
    if record.get("phase") not in {
        "bootstrap",
        "steady",
        "idle",
        "one-shot",
    }:
        raise ValueError("observation phase is invalid")
    if record.get("duration_role", "stage") not in {
        "stage",
        "rollup",
        "sample",
    }:
        raise ValueError("observation duration_role is invalid")
    duration = record.get("duration_seconds")
    if not _is_number(duration) or float(duration) < 0.0:
        raise ValueError("duration_seconds must be finite and non-negative")
    if format_version >= 2:
        start = record.get("run_relative_start_seconds")
        end = record.get("run_relative_end_seconds")
        if (
            not _is_number(start)
            or not _is_number(end)
            or float(start) < 0.0
            or float(end) < float(start)
        ):
            raise ValueError("observation monotonic span is invalid")
        if record.get("span_source") not in {
            "measured-monotonic",
            "completion-timestamp-and-measured-duration",
        }:
            raise ValueError("observation span_source is invalid")
    if not isinstance(record.get("scope"), dict):
        raise TypeError("observation scope must be an object")
    for field_name in ("work", "resources"):
        values = record.get(field_name)
        if not isinstance(values, dict):
            raise TypeError(f"observation {field_name} must be an object")
        for key, value in values.items():
            if not isinstance(key, str) or not key or not _is_number(value):
                raise TypeError(
                    f"observation {field_name} must contain named finite numbers"
                )


def _is_number(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and isfinite(float(value))
    )


__all__ = ["ObservationRead", "read_execution_observations"]
