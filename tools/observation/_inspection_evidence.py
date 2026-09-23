"""Capture and verify exact Tool-owned physical-link inspection evidence."""

from __future__ import annotations

from dataclasses import dataclass
from skyseed.experiment.output.history import _PublishedPrefix, _completed_range
import gzip
from hashlib import sha256
from io import TextIOWrapper
import json
from pathlib import Path
from typing import Any, Iterator


from skyseed.experiment.output.physical_links import (
    _FORMAT_VERSION, _ARTIFACT_TYPE, _MANIFEST_NAME, _WORK_FIELDS,
)

@dataclass(frozen=True, slots=True)
class PhysicalLinkInspectionIndex:
    """Describe the sealed static index; consuming iteration validates stream integrity."""

    path: Path
    nodes: tuple[dict[str, object], ...]
    zones: tuple[str, ...]
    zone_records: tuple[dict[str, object], ...]
    timestamps: tuple[str, ...]
    manifest: dict[str, object]


def read_physical_link_inspection_index(
    path: Path,
) -> PhysicalLinkInspectionIndex:
    """Read the sealed header/manifest; iteration validates the complete stream."""

    source, manifest = _source_metadata(path)
    with source.open("rb") as raw, gzip.GzipFile(fileobj=_PublishedPrefix(raw, manifest["stream_size_bytes"])) as compressed, TextIOWrapper(compressed, encoding="utf-8") as stream:
        header = _read_header(stream)
    nodes = tuple(header["nodes"])
    zones = tuple(header["zones"])
    zone_records = tuple(header.get("zone_records", ()))
    if len(zone_records) != len(zones) or any(
        not isinstance(value, dict) or value.get("zone_id") != zone_id
        for value, zone_id in zip(zone_records, zones, strict=True)
    ):
        raise ValueError("physical-link inspection Zone records differ")
    selected = _selected_sources(source)
    if selected is not None:
        timestamps = []
        for selected_path, start, end in selected:
            part = _read_manifest(selected_path)
            timestamps.extend(t for t in part['timestamps'] if start <= t <= end)
        manifest = {**manifest, 'timestamps': timestamps, 'formal_observation_count': len(timestamps),
                    'selection_scope': 'selected-publication'}
    return PhysicalLinkInspectionIndex(
        path=source,
        nodes=nodes,
        zones=zones,
        zone_records=zone_records,
        timestamps=tuple(manifest["timestamps"]),
        manifest=manifest,
    )


def _selected_sources(path):
    source = Path(path).resolve()
    if source.parent.name != 'candidates' or source.parent.parent.name != 'physical-world':
        return None
    output = source.parents[3]
    publication = _completed_range.get()
    if publication is not None and publication[0] == output / 'results':
        selected = publication[1].get('selected_materials', ())
    else:
        completed = output / 'results/experiment/completed.json'
        selected = json.loads(completed.read_text()).get('selected_materials', ()) if completed.exists() else ()
    if any(r.get('evidence') and r.get('physical_links') is None for r in selected):
        raise ValueError('selected candidate physical evidence is unavailable')
    if not any(r.get('physical_link_source') for r in selected):
        return None
    groups = []
    for row in selected:
        path = output / row['physical_link_source'] if row.get('physical_link_source') else source
        if groups and groups[-1][0] == path:
            groups[-1][2] = row['timeline']['end']
        else:
            groups.append([path, row['timeline']['start'], row['timeline']['end']])
    return groups


def iter_physical_link_inspection(path: Path) -> Iterator[dict[str, Any]]:
    sources = _selected_sources(path)
    if sources is None:
        yield from _iter_raw_physical_link_inspection(path)
        return
    header = None
    for source, start, end in sources:
        records = _iter_raw_physical_link_inspection(source)
        current = next(records)
        if header is None:
            header = current
            yield header
        elif current != header:
            raise ValueError('selected physical candidate identities differ')
        for row in records:
            if start <= row['timestamp'] <= end:
                yield row


def _iter_raw_physical_link_inspection(path: Path) -> Iterator[dict[str, Any]]:
    """Yield a verified header and exact compact formal observations."""

    source, manifest = _source_metadata(path)
    with source.open("rb") as raw, gzip.GzipFile(fileobj=(hashed := _PublishedPrefix(raw, manifest["stream_size_bytes"]))) as compressed, TextIOWrapper(compressed, encoding="utf-8") as stream:
        header = _read_header(stream)
        nodes, zones = header["nodes"], header["zones"]
        yield header
        timestamps = tuple(manifest["timestamps"])
        count = 0
        for line_number, line in enumerate(stream, start=2):
            record = _json_line(line, line_number)
            _validate_record(record, len(nodes), len(zones))
            if count >= len(timestamps) or record["timestamp"] != timestamps[count]:
                raise ValueError("physical-link inspection timestamps differ")
            count += 1
            yield record
        if count != len(timestamps) or count != manifest["formal_observation_count"]:
            raise ValueError("physical-link inspection timestamp count differs")
        if hashed.digest.hexdigest() != manifest.get("stream_sha256"):
            raise ValueError("physical-link inspection checksum differs")


def _read_header(stream):
    header = _json_line(next(stream, ""), 1)
    if (header.get("record_type") != "physical_link_inspection_header"
            or header.get("format_version") != _FORMAT_VERSION
            or header.get("artifact_type") != _ARTIFACT_TYPE
            or header.get("formal_observations_only") is not True):
        raise ValueError("physical-link inspection header is invalid")
    if not isinstance(header.get("nodes"), list) or not isinstance(header.get("zones"), list):
        raise ValueError("physical-link inspection index is malformed")
    return header


def _source_metadata(path):
    source = Path(path).resolve(strict=True)
    manifest = _read_manifest(source)
    if source.name != manifest.get("stream"):
        raise ValueError("physical-link inspection manifest names another stream")
    if source.stat().st_size < manifest.get("stream_size_bytes"):
        raise ValueError("physical-link inspection size differs")
    if manifest.get("formal_observation_count") != len(manifest["timestamps"]):
        raise ValueError("physical-link inspection record count differs")
    return source, manifest


def _read_manifest(source: Path) -> dict[str, Any]:
    path = source.with_name(_MANIFEST_NAME)
    if path.is_symlink():
        raise ValueError("physical-link inspection manifest must not be a symlink")
    try:
        publication = _completed_range.get()
        value = (publication[1]["physical_links"] if publication is not None
                 and source == publication[0] / 'physical-world/candidates/physical-link-observations.jsonl.gz'
                 else json.loads(path.read_text(encoding="utf-8")))
        value = dict(value)
        with source.with_name("timestamps.jsonl").open("rb") as index:
            value["timestamps"] = [json.loads(line) for line in index.read(value["timestamp_index_bytes"]).splitlines()]
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("physical-link inspection manifest is invalid") from error
    if (
        not isinstance(value, dict)
        or value.get("format_version") != _FORMAT_VERSION
        or value.get("artifact_type") != _ARTIFACT_TYPE
        or not isinstance(value.get("timestamps"), list)
    ):
        raise ValueError("physical-link inspection manifest is malformed")
    return value


def _validate_record(
    value: dict[str, Any], node_count: int, zone_count: int
) -> None:
    if set(value) != {
        "record_type",
        "timestamp",
        "inter_satellite",
        "ground_station",
        "zone_service",
        "work_counts",
        "ground_station_search_complete",
    } or value["record_type"] != "physical_link_observation":
        raise ValueError("physical-link inspection record is malformed")
    if not isinstance(value["timestamp"], str) or not value["timestamp"]:
        raise ValueError("physical-link inspection timestamp is invalid")
    for name, width, index_fields in (
        ("inter_satellite", 6, (0, 1)),
        ("ground_station", 5, (0, 1)),
        ("zone_service", 5, (1,)),
    ):
        rows = value[name]
        if not isinstance(rows, list) or any(
            not isinstance(row, list) or len(row) != width for row in rows
        ):
            raise ValueError(f"physical-link {name} rows are malformed")
        for row in rows:
            for field in index_fields:
                if (
                    isinstance(row[field], bool)
                    or not isinstance(row[field], int)
                    or not 0 <= row[field] < node_count
                ):
                    raise ValueError(f"physical-link {name} node index is invalid")
            if name == "zone_service" and (
                isinstance(row[0], bool)
                or not isinstance(row[0], int)
                or not 0 <= row[0] < zone_count
            ):
                raise ValueError("physical-link Zone index is invalid")
    work = value["work_counts"]
    if work is not None and (not isinstance(work, dict) or set(work) != set(_WORK_FIELDS)):
        raise ValueError("physical-link inspection work counts are malformed")
    complete = value["ground_station_search_complete"]
    if complete is not None and not isinstance(complete, bool):
        raise ValueError("physical-link search completeness is malformed")


def _json_line(line: str, line_number: int) -> dict[str, Any]:
    try:
        value = json.loads(line)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"invalid physical-link inspection JSON at line {line_number}"
        ) from error
    if not isinstance(value, dict):
        raise ValueError("physical-link inspection records must be objects")
    return value


__all__ = [
    "PhysicalLinkInspectionIndex",
    "iter_physical_link_inspection",
    "read_physical_link_inspection_index",
]
