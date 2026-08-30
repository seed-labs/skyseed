"""Persist trusted local immutable BPF realizations for experiment execution."""

from __future__ import annotations

import gzip
from hashlib import sha256
import json
import os
from pathlib import Path
import pickle
import tempfile
from typing import Any, Mapping, Sequence

from ..backend.bpf.compiled_realization import BpfCompiledRealization


_ARTIFACT_TYPE = "bpf-compiled-realization-sequence"
_FORMAT_VERSION = 1
_ENCODING = "gzip-pickle-v5"


def _write_realization_handoff(
    realizations: Sequence[BpfCompiledRealization], directory: Path
) -> Path:
    """Write one bounded deterministic local file per immutable realization."""

    writer = _RealizationHandoffWriter(directory)
    for realization in realizations:
        writer.append(realization)
    return writer.finalize()


class _RealizationHandoffWriter:
    """Incrementally persist the canonical immutable realization stream."""

    def __init__(self, directory: Path) -> None:
        self._root = Path(directory)
        self._root.mkdir(parents=True, exist_ok=True)
        if any(self._root.iterdir()):
            raise FileExistsError(
                f"realization handoff directory is not empty: {self._root}"
            )
        self._entries: list[dict[str, object]] = []
        self._previous: BpfCompiledRealization | None = None
        self._finalized = False

    def append(self, realization: BpfCompiledRealization) -> None:
        """Write one value without retaining the historical object sequence."""

        if self._finalized:
            raise RuntimeError("realization handoff is already finalized")
        if not isinstance(realization, BpfCompiledRealization):
            raise TypeError("realization handoff requires BpfCompiledRealization values")
        if self._previous is not None and (
            self._previous.end_slice_id != realization.start_slice_id
            or self._previous.end_timestamp != realization.start_timestamp
            or self._previous._topology_signature != realization._topology_signature
        ):
            raise ValueError("realization handoff input must be continuous")
        index = len(self._entries)
        raw = pickle.dumps(realization, protocol=5)
        compressed = gzip.compress(raw, compresslevel=6, mtime=0)
        name = f"realization-{index:05d}.pickle.gz"
        _write_bytes_atomic(self._root / name, compressed)
        self._entries.append(
            {
                "index": index,
                "path": name,
                "start_slice_id": realization.start_slice_id,
                "start_timestamp": (
                    None
                    if realization.start_timestamp is None
                    else realization.start_timestamp.isoformat()
                ),
                "end_slice_id": realization.end_slice_id,
                "end_timestamp": realization.end_timestamp.isoformat(),
                "compressed_bytes": len(compressed),
                "uncompressed_bytes": len(raw),
                "sha256": sha256(compressed).hexdigest(),
                "uncompressed_sha256": sha256(raw).hexdigest(),
            }
        )
        self._previous = realization

    def finalize(self) -> Path:
        """Seal and checksum the complete continuous sequence."""

        if self._finalized:
            raise RuntimeError("realization handoff is already finalized")
        if not self._entries:
            raise ValueError("realization handoff requires at least one realization")
        self._finalized = True
        manifest = {
            "artifact_type": _ARTIFACT_TYPE,
            "format_version": _FORMAT_VERSION,
            "encoding": _ENCODING,
            "trusted_local_rebuild_evidence": True,
            "realization_count": len(self._entries),
            "terminal": True,
            "sequence_sha256": _sequence_sha256(self._entries),
            "realizations": self._entries,
        }
        manifest_path = self._root / "manifest.json"
        _write_bytes_atomic(
            manifest_path,
            (
                json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode("utf-8"),
        )
        return manifest_path


class _PersistedRealizationSupplier:
    """Validate and decode one persisted realization on each Runner pull."""

    def __init__(self, manifest_path: Path) -> None:
        self._manifest_path = Path(manifest_path).resolve()
        self._root = self._manifest_path.parent
        self._entries = self._read_manifest()
        self._index = 0
        self._previous: BpfCompiledRealization | None = None
        self._terminal_returned = False

    def __call__(self) -> BpfCompiledRealization | None:
        if self._index >= len(self._entries):
            self._terminal_returned = True
            return None
        entry = self._entries[self._index]
        relative = _safe_relative_path(entry.get("path"))
        path = self._root / relative
        try:
            compressed = path.read_bytes()
        except (EOFError, OSError) as error:
            raise RuntimeError(
                f"persisted BPF realization is unavailable: {relative}"
            ) from error
        if (
            len(compressed) != entry.get("compressed_bytes")
            or sha256(compressed).hexdigest() != entry.get("sha256")
        ):
            raise ValueError(
                f"persisted BPF realization checksum differs: {relative}"
            )
        try:
            raw = gzip.decompress(compressed)
        except OSError as error:
            raise ValueError(
                f"persisted BPF realization compression is invalid: {relative}"
            ) from error
        if (
            len(raw) != entry.get("uncompressed_bytes")
            or sha256(raw).hexdigest() != entry.get("uncompressed_sha256")
        ):
            raise ValueError(
                f"persisted BPF realization payload checksum differs: {relative}"
            )
        try:
            value = pickle.loads(raw)
        except Exception as error:
            raise ValueError(
                f"persisted BPF realization is incompatible: {relative}"
            ) from error
        if not isinstance(value, BpfCompiledRealization):
            raise TypeError("persisted handoff decoded a non-BPF realization")
        _validate_entry(value, entry, index=self._index)
        if self._previous is not None and (
            self._previous.end_slice_id != value.start_slice_id
            or self._previous.end_timestamp != value.start_timestamp
            or self._previous._topology_signature != value._topology_signature
        ):
            raise ValueError("persisted BPF realization sequence is not continuous")
        self._previous = value
        self._index += 1
        return value

    def snapshot(self) -> dict[str, object]:
        return {
            "manifest": str(self._manifest_path),
            "realization_count": len(self._entries),
            "consumed_count": self._index,
            "terminal_returned": self._terminal_returned,
        }

    def _read_manifest(self) -> tuple[dict[str, Any], ...]:
        try:
            value = json.loads(self._manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("persisted BPF realization manifest is invalid") from error
        if not isinstance(value, Mapping):
            raise ValueError("persisted BPF realization manifest must be an object")
        if (
            value.get("artifact_type") != _ARTIFACT_TYPE
            or value.get("format_version") != _FORMAT_VERSION
            or value.get("encoding") != _ENCODING
            or value.get("trusted_local_rebuild_evidence") is not True
            or value.get("terminal") is not True
        ):
            raise ValueError("persisted BPF realization manifest is incompatible")
        raw_entries = value.get("realizations")
        if not isinstance(raw_entries, list) or not raw_entries:
            raise ValueError("persisted BPF realization manifest has no sequence")
        entries = tuple(
            _mapping(entry, "persisted BPF realization entry")
            for entry in raw_entries
        )
        if value.get("realization_count") != len(entries):
            raise ValueError("persisted BPF realization count differs")
        if value.get("sequence_sha256") != _sequence_sha256(entries):
            raise ValueError("persisted BPF realization sequence checksum differs")
        for index, entry in enumerate(entries):
            if entry.get("index") != index:
                raise ValueError("persisted BPF realization indexes are not continuous")
            _safe_relative_path(entry.get("path"))
        return entries


def _read_persisted_realization_sequence(
    manifest_path: Path,
) -> tuple[BpfCompiledRealization, ...]:
    """Materialize one trusted local handoff for a full-horizon consumer.

    This is an application-side consumption mode.  It does not make the
    handoff encoding part of any downstream semantic contract.
    """

    supplier = _PersistedRealizationSupplier(manifest_path)
    values = []
    while (value := supplier()) is not None:
        values.append(value)
    return tuple(values)


def _validate_entry(
    value: BpfCompiledRealization, entry: Mapping[str, Any], *, index: int
) -> None:
    expected = {
        "index": index,
        "start_slice_id": value.start_slice_id,
        "start_timestamp": (
            None if value.start_timestamp is None else value.start_timestamp.isoformat()
        ),
        "end_slice_id": value.end_slice_id,
        "end_timestamp": value.end_timestamp.isoformat(),
    }
    if any(entry.get(key) != item for key, item in expected.items()):
        raise ValueError("persisted BPF realization interval evidence differs")


def _sequence_sha256(entries: Sequence[Mapping[str, Any]]) -> str:
    payload = "\n".join(
        "|".join(
            (
                str(entry.get("index")),
                str(entry.get("start_slice_id")),
                str(entry.get("start_timestamp")),
                str(entry.get("end_slice_id")),
                str(entry.get("end_timestamp")),
                str(entry.get("sha256")),
                str(entry.get("uncompressed_sha256")),
            )
        )
        for entry in entries
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def _safe_relative_path(value: object) -> Path:
    if not isinstance(value, str):
        raise ValueError("persisted BPF realization path is invalid")
    path = Path(value)
    if path.is_absolute() or len(path.parts) != 1 or path.name != value:
        raise ValueError("persisted BPF realization path leaves its handoff root")
    return path


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return dict(value)


def _write_bytes_atomic(path: Path, content: bytes) -> None:
    destination = Path(path)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


__all__: list[str] = []
