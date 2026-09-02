"""Seal and verify deterministic producer-owned domain-history streams."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import gzip
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
from typing import Any, Iterator, Mapping


_DOMAIN_HISTORY_ARTIFACT_TYPE = "skyseed-v2-domain-history"
_DOMAIN_HISTORY_FORMAT_VERSION = 1
_DOMAIN_HISTORY_MANIFEST_NAME = "domain-history-manifest.json"
_DOMAIN_HISTORY_STREAM_NAMES = {
    "physical_frames": "physical-frames.jsonl",
    "planning": "planning.jsonl.gz",
    "compiler": "compiler.jsonl",
}
_THROUGH_RANK = {"planner": 1, "compiler": 2}
_MANIFEST_FIELDS = {
    "artifact_type",
    "format_version",
    "terminal",
    "through",
    "physical_world_fingerprint",
    "timeline",
    "counts",
    "frontiers",
    "lineage",
    "streams",
}
_TIMELINE_FIELDS = {
    "start_timestamp",
    "end_timestamp",
    "observation_interval",
}
_COUNT_FIELDS = {
    "expected_prehistory_frames",
    "expected_formal_frames",
    "prehistory_frames",
    "formal_frames",
    "plans",
    "transitions",
    "realizations",
}
_FRONTIER_FIELDS = {
    "physical_observation",
    "planning",
    "publication",
}
_LINEAGE_FIELDS = {
    "first_plan_id",
    "last_plan_id",
    "last_plan_previous_plan_id",
    "last_plan_output_state_fingerprint",
    "last_realization_plan_id",
    "last_realization_output_state_fingerprint",
}
_STREAM_FIELDS = {"path", "sha256", "bytes", "record_count"}
_TIMEDELTA_FIELDS = {"days", "seconds", "microseconds"}


@dataclass(frozen=True, slots=True)
class _DomainHistoryManifest:
    """Retain one validated seal and its canonical non-symlink root."""

    root: Path
    data: dict[str, Any]

    def stream_path(self, stream_name: str) -> Path:
        try:
            expected_name = _DOMAIN_HISTORY_STREAM_NAMES[stream_name]
            entry = self.data["streams"][stream_name]
        except KeyError as error:
            raise ValueError(
                f"unknown domain-history stream: {stream_name}"
            ) from error
        if entry["path"] != expected_name:
            raise ValueError(
                f"domain-history {stream_name} path is not canonical"
            )
        path = self.root / expected_name
        if path.is_symlink():
            raise ValueError(
                f"domain-history {stream_name} stream must not be a symlink"
            )
        try:
            resolved = path.resolve(strict=True)
        except OSError as error:
            raise ValueError(
                f"domain-history {stream_name} stream is unavailable"
            ) from error
        if resolved.parent != self.root or not resolved.is_file():
            raise ValueError(
                f"domain-history {stream_name} stream escapes its root"
            )
        return resolved


def _timedelta_to_data(value: timedelta) -> dict[str, int]:
    if not isinstance(value, timedelta):
        raise TypeError("duration must be a timedelta")
    return {
        "days": value.days,
        "seconds": value.seconds,
        "microseconds": value.microseconds,
    }


def _timedelta_from_data(value: object, field_name: str) -> timedelta:
    if not isinstance(value, dict) or set(value) != _TIMEDELTA_FIELDS:
        raise ValueError(f"{field_name} duration is malformed")
    parts = []
    for name in ("days", "seconds", "microseconds"):
        item = value[name]
        if isinstance(item, bool) or not isinstance(item, int):
            raise ValueError(f"{field_name}.{name} must be an integer")
        parts.append(item)
    result = timedelta(days=parts[0], seconds=parts[1], microseconds=parts[2])
    if _timedelta_to_data(result) != value:
        raise ValueError(f"{field_name} duration is not canonical")
    return result


def _summarize_domain_history_stream(path: Path) -> dict[str, int | str]:
    """Hash one completed JSONL stream without retaining its rows."""

    source = Path(path)
    digest = sha256()
    byte_count = 0
    line_count = 0
    with source.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            byte_count += len(chunk)
    with _open_domain_history_text(source) as stream:
        for line in stream:
            if not line.endswith("\n"):
                raise ValueError(
                    f"domain-history stream is not newline terminated: {source.name}"
                )
            line_count += 1
    if line_count == 0:
        raise ValueError(f"domain-history stream is empty: {source.name}")
    return {
        "sha256": digest.hexdigest(),
        "bytes": byte_count,
        "record_count": line_count - 1,
    }


def _write_domain_history_manifest_atomic(
    path: Path, manifest: Mapping[str, object]
) -> Path:
    """Atomically publish canonical JSON bytes in the destination directory."""

    destination = Path(path)
    if destination.name != _DOMAIN_HISTORY_MANIFEST_NAME:
        raise ValueError("domain-history manifest has a non-canonical name")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("domain-history manifest is already sealed")
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(
            dict(manifest),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    except BaseException:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return destination


def _read_domain_history_manifest(
    stream_path: Path, *, minimum_through: str
) -> _DomainHistoryManifest:
    """Read and structurally validate the mandatory sibling terminal seal."""

    if minimum_through not in _THROUGH_RANK:
        raise ValueError("minimum_through must be planner or compiler")
    raw_source = Path(stream_path)
    if raw_source.name not in _DOMAIN_HISTORY_STREAM_NAMES.values():
        raise ValueError("domain-history stream has a non-canonical name")
    if raw_source.is_symlink():
        raise ValueError("domain-history stream must not be a symlink")
    raw_root = raw_source.parent
    if raw_root.is_symlink():
        raise ValueError("domain-history root must not be a symlink")
    root = raw_root.resolve()
    source = raw_source.resolve()
    if source.parent != root:
        raise ValueError("domain-history stream escapes its root")
    manifest_path = root / _DOMAIN_HISTORY_MANIFEST_NAME
    if manifest_path.is_symlink():
        raise ValueError("domain-history manifest must not be a symlink")
    try:
        with manifest_path.open("r", encoding="utf-8") as stream:
            data = json.load(stream)
    except FileNotFoundError as error:
        raise ValueError(
            "domain-history is incomplete: terminal manifest is missing"
        ) from error
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("domain-history manifest is invalid") from error
    if not isinstance(data, dict):
        raise ValueError("domain-history manifest must be a JSON object")
    _validate_domain_history_manifest_data(data, minimum_through)
    result = _DomainHistoryManifest(root, data)
    for stream_name in _DOMAIN_HISTORY_STREAM_NAMES:
        result.stream_path(stream_name)
    return result


def _iter_verified_domain_history_stream(
    manifest: _DomainHistoryManifest, stream_name: str
) -> Iterator[dict[str, Any]]:
    """Yield one JSON object at a time and verify the sealed raw stream."""

    path = manifest.stream_path(stream_name)
    expected = manifest.data["streams"][stream_name]
    digest = sha256()
    byte_count = 0
    with path.open("rb") as raw_stream:
        while chunk := raw_stream.read(1024 * 1024):
            digest.update(chunk)
            byte_count += len(chunk)
    if digest.hexdigest() != expected["sha256"]:
        raise ValueError(
            f"domain-history {stream_name} sha256 differs from manifest"
        )
    if byte_count != expected["bytes"]:
        raise ValueError(
            f"domain-history {stream_name} bytes differs from manifest"
        )
    line_count = 0
    with _open_domain_history_text(path) as stream:
        for line_number, line in enumerate(stream, start=1):
            line_count += 1
            if not line.endswith("\n"):
                raise ValueError(
                    f"domain-history {stream_name} line {line_number} "
                    "is not newline terminated"
                )
            try:
                value = json.loads(line)
            except (UnicodeError, json.JSONDecodeError) as error:
                raise ValueError(
                    f"invalid domain-history {stream_name} JSON on "
                    f"line {line_number}"
                ) from error
            if not isinstance(value, dict):
                raise ValueError(
                    f"domain-history {stream_name} rows must be objects"
                )
            yield value
    if max(0, line_count - 1) != expected["record_count"]:
        raise ValueError(
            f"domain-history {stream_name} record_count differs from manifest"
        )


def _open_domain_history_text(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8")
    return path.open("r", encoding="utf-8")


def _validate_domain_history_manifest_data(
    data: dict[str, Any], minimum_through: str
) -> None:
    if set(data) != _MANIFEST_FIELDS:
        raise ValueError("domain-history manifest has unknown fields")
    if (
        data["artifact_type"] != _DOMAIN_HISTORY_ARTIFACT_TYPE
        or data["format_version"] != _DOMAIN_HISTORY_FORMAT_VERSION
        or data["terminal"] is not True
    ):
        raise ValueError("domain-history manifest is incompatible")
    through = data["through"]
    if (
        not isinstance(through, str)
        or through not in _THROUGH_RANK
        or _THROUGH_RANK[through] < _THROUGH_RANK[minimum_through]
    ):
        raise ValueError(
            f"domain-history is not sealed through {minimum_through}"
        )
    _require_sha256(
        data["physical_world_fingerprint"],
        "physical_world_fingerprint",
    )

    timeline = _exact_mapping(data["timeline"], _TIMELINE_FIELDS, "timeline")
    start = _timestamp(timeline["start_timestamp"], "timeline start")
    end = _timestamp(timeline["end_timestamp"], "timeline end")
    interval = _timedelta_from_data(
        timeline["observation_interval"], "observation_interval"
    )
    if interval <= timedelta(0) or end < start:
        raise ValueError("domain-history Timeline is invalid")
    span = end - start
    if span % interval != timedelta(0):
        raise ValueError("domain-history Timeline end is not interval aligned")
    expected_formal = span // interval + 1

    counts = _exact_mapping(data["counts"], _COUNT_FIELDS, "counts")
    for name, value in counts.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"domain-history count {name} is invalid")
    if counts["expected_formal_frames"] != expected_formal:
        raise ValueError("domain-history expected formal count is invalid")
    if counts["formal_frames"] != counts["expected_formal_frames"]:
        raise ValueError("domain-history formal coverage is incomplete")
    if counts["prehistory_frames"] != counts["expected_prehistory_frames"]:
        raise ValueError("domain-history prehistory coverage is incomplete")
    if counts["plans"] <= 0 or counts["transitions"] != counts["formal_frames"]:
        raise ValueError("domain-history Plan coverage is incomplete")
    if through == "planner" and counts["realizations"] != 0:
        raise ValueError("Planner-only domain-history contains realizations")
    if through == "compiler" and counts["realizations"] != counts["plans"]:
        raise ValueError("Compiler domain-history realization coverage is incomplete")

    frontiers = _exact_mapping(
        data["frontiers"], _FRONTIER_FIELDS, "frontiers"
    )
    if any(
        _timestamp(value, f"{name} frontier") != end
        for name, value in frontiers.items()
    ):
        raise ValueError("domain-history final frontiers are incomplete")

    lineage = _exact_mapping(data["lineage"], _LINEAGE_FIELDS, "lineage")
    for name in (
        "first_plan_id",
        "last_plan_id",
        "last_plan_output_state_fingerprint",
    ):
        _require_string(lineage[name], name)
    previous_id = lineage["last_plan_previous_plan_id"]
    if previous_id is not None:
        _require_string(previous_id, "last_plan_previous_plan_id")
    if through == "planner":
        if (
            lineage["last_realization_plan_id"] is not None
            or lineage["last_realization_output_state_fingerprint"] is not None
        ):
            raise ValueError("Planner-only lineage contains a realization")
    else:
        if lineage["last_realization_plan_id"] != lineage["last_plan_id"]:
            raise ValueError("domain-history final Compiler Plan lineage differs")
        if (
            lineage["last_realization_output_state_fingerprint"]
            != lineage["last_plan_output_state_fingerprint"]
        ):
            raise ValueError("domain-history final Compiler state lineage differs")

    streams = _exact_mapping(
        data["streams"], set(_DOMAIN_HISTORY_STREAM_NAMES), "streams"
    )
    for stream_name, expected_path in _DOMAIN_HISTORY_STREAM_NAMES.items():
        entry = _exact_mapping(
            streams[stream_name], _STREAM_FIELDS, f"streams.{stream_name}"
        )
        relative = entry["path"]
        if (
            not isinstance(relative, str)
            or relative != expected_path
            or PurePosixPath(relative).is_absolute()
            or len(PurePosixPath(relative).parts) != 1
        ):
            raise ValueError(
                f"domain-history {stream_name} path is invalid"
            )
        _require_sha256(entry["sha256"], f"streams.{stream_name}.sha256")
        for name in ("bytes", "record_count"):
            value = entry[name]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(
                    f"domain-history {stream_name} {name} is invalid"
                )
    if streams["physical_frames"]["record_count"] != counts["formal_frames"]:
        raise ValueError("domain-history PhysicalFrame count differs")
    if streams["planning"]["record_count"] != counts["plans"]:
        raise ValueError("domain-history Planning count differs")
    if streams["compiler"]["record_count"] != counts["realizations"]:
        raise ValueError("domain-history Compiler count differs")


def _exact_mapping(
    value: object, fields: set[str], field_name: str
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f"domain-history {field_name} is malformed")
    return value


def _timestamp(value: object, field_name: str) -> datetime:
    text = _require_string(value, field_name)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise ValueError(f"domain-history {field_name} is invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"domain-history {field_name} must be timezone-aware")
    utc = parsed.astimezone(timezone.utc)
    if utc.isoformat() != text:
        raise ValueError(f"domain-history {field_name} is not canonical UTC")
    return utc


def _require_sha256(value: object, field_name: str) -> str:
    text = _require_string(value, field_name)
    if len(text) != 64 or any(
        character not in "0123456789abcdef" for character in text
    ):
        raise ValueError(f"domain-history {field_name} is not SHA256")
    return text


def _require_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"domain-history {field_name} must be a non-empty string")
    return value


__all__: list[str] = []
