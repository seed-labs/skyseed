"""Read append-only inspection evidence without owning its retention."""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

from skyseed_v2.shared._domain_history import (
    _iter_verified_domain_history_stream,
    _read_domain_history_manifest,
)


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


def read_sealed_physical_frames(path: Path) -> tuple[dict[str, Any], ...]:
    """Read one verified formal PhysicalFrame stream as detached ECEF values."""

    source = Path(path)
    manifest = _read_domain_history_manifest(source, minimum_through="planner")
    if source.is_symlink():
        raise ValueError("PhysicalFrame history must not be a symlink")
    try:
        resolved = source.resolve(strict=True)
    except OSError as error:
        raise ValueError("PhysicalFrame history is unavailable") from error
    if resolved != manifest.stream_path("physical_frames"):
        raise ValueError(
            "PhysicalFrame input must reference its sealed domain-history root"
        )
    rows = _iter_verified_domain_history_stream(manifest, "physical_frames")
    try:
        header = next(rows)
    except StopIteration as error:
        raise ValueError("PhysicalFrame history is empty") from error
    if header != {
        "record_type": "physical_frame_history_header",
        "format_version": 1,
        "coordinate_system": "WGS84-ECEF-metres",
        "formal_frames_only": True,
    }:
        raise ValueError("PhysicalFrame history header is invalid")

    frames: list[dict[str, Any]] = []
    expected_nodes: set[str] | None = None
    previous_timestamp: str | None = None
    for row in rows:
        if set(row) != {"record_type", "timestamp", "positions"}:
            raise ValueError("PhysicalFrame history row has unknown fields")
        if row["record_type"] != "physical_frame":
            raise ValueError("PhysicalFrame history record type is invalid")
        timestamp = row["timestamp"]
        if not isinstance(timestamp, str) or not timestamp:
            raise ValueError("PhysicalFrame history timestamp is invalid")
        if previous_timestamp is not None and timestamp <= previous_timestamp:
            raise ValueError("PhysicalFrame history timestamps are not ordered")
        positions = []
        node_ids = set()
        if not isinstance(row["positions"], list):
            raise TypeError("PhysicalFrame history positions must be a list")
        for value in row["positions"]:
            if not isinstance(value, dict) or set(value) != {
                "node",
                "x_m",
                "y_m",
                "z_m",
            }:
                raise ValueError("PhysicalFrame history position is malformed")
            node = value["node"]
            if not isinstance(node, dict) or set(node) != {
                "asn",
                "entity_kind",
                "entity_id",
            }:
                raise ValueError("PhysicalFrame history node is malformed")
            node_id = (
                f"{node['asn']}/{node['entity_kind']}/{node['entity_id']}"
            )
            if node_id in node_ids:
                raise ValueError("PhysicalFrame history contains duplicate nodes")
            node_ids.add(node_id)
            positions.append(
                {
                    "node_id": node_id,
                    "kind": node["entity_kind"],
                    "ecef_m": {
                        "x": value["x_m"],
                        "y": value["y_m"],
                        "z": value["z_m"],
                    },
                }
            )
        if expected_nodes is None:
            expected_nodes = node_ids
        elif node_ids != expected_nodes:
            raise ValueError("PhysicalFrame node coverage changes across history")
        frames.append(
            {
                "timestamp": timestamp,
                "positions": tuple(
                    sorted(positions, key=lambda item: item["node_id"])
                ),
            }
        )
        previous_timestamp = timestamp
    if not frames:
        raise ValueError("PhysicalFrame history has no formal frames")
    return tuple(frames)


__all__ = [
    "iter_jsonl_records",
    "read_jsonl_records",
    "read_sealed_physical_frames",
]
