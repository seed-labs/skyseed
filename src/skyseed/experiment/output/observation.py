"""Record optional detached execution-cost observations for offline Tools."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import json
from math import isfinite
from pathlib import Path
from time import perf_counter
from threading import RLock


class _ExecutionObserver:
    """Retain coarse numeric observations without owning execution semantics."""

    __slots__ = (
        "_metadata",
        "_records",
        "_recording_failure",
        "_run_origin_monotonic",
        "_sequence", "_lock", "_streamed_path",
    )

    def __init__(self, metadata: Mapping[str, object] | None = None) -> None:
        if metadata is not None and not isinstance(metadata, Mapping):
            raise TypeError("metadata must be a mapping or None")
        copied = deepcopy(dict(metadata or {}))
        _require_json_value(copied, "metadata")
        self._metadata = copied
        self._records: list[dict[str, object]] = []
        self._recording_failure: Exception | None = None
        self._run_origin_monotonic = perf_counter()
        self._sequence = 0
        self._lock = RLock()
        self._streamed_path = None

    def record(
        self,
        *,
        component: str,
        stage: str,
        phase: str,
        duration_seconds: float,
        duration_role: str = "stage",
        scope: Mapping[str, object] | None = None,
        work: Mapping[str, int | float] | None = None,
        resources: Mapping[str, int | float] | None = None,
        started_monotonic: float | None = None,
        ended_monotonic: float | None = None,
    ) -> None:
        """Append one already-completed stage observation."""

        if self._recording_failure is not None:
            return
        try:
            component = _require_label(component, "component")
            stage = _require_label(stage, "stage")
            if phase not in {"bootstrap", "steady", "idle", "one-shot"}:
                raise ValueError(
                    "phase must be bootstrap, steady, idle, or one-shot"
                )
            if duration_role not in {"stage", "rollup", "sample"}:
                raise ValueError(
                    "duration_role must be stage, rollup, or sample"
                )
            duration = _require_number(duration_seconds, "duration_seconds")
            if duration < 0.0:
                raise ValueError("duration_seconds must be non-negative")
            copied_scope = deepcopy(dict(scope or {}))
            _require_json_value(copied_scope, "scope")
            copied_work = _numeric_mapping(work, "work")
            copied_resources = _numeric_mapping(resources, "resources")
            ended = (
                perf_counter()
                if ended_monotonic is None
                else _require_number(ended_monotonic, "ended_monotonic")
            )
            started = (
                float(ended) - float(duration)
                if started_monotonic is None
                else _require_number(
                    started_monotonic, "started_monotonic"
                )
            )
            if float(started) > float(ended):
                raise ValueError(
                    "started_monotonic must not exceed ended_monotonic"
                )
            relative_start = max(
                0.0, float(started) - self._run_origin_monotonic
            )
            relative_end = max(
                relative_start, float(ended) - self._run_origin_monotonic
            )
            record = {
                    "record_type": "execution_observation",
                    "component": component,
                    "stage": stage,
                    "phase": phase,
                    "duration_seconds": duration,
                    "run_relative_start_seconds": relative_start,
                    "run_relative_end_seconds": relative_end,
                    "span_source": (
                        "measured-monotonic"
                        if started_monotonic is not None
                        else "completion-timestamp-and-measured-duration"
                    ),
                    "duration_role": duration_role,
                    "scope": copied_scope,
                    "work": copied_work,
                    "resources": copied_resources,
                }
            with self._lock:
                record["sequence"] = self._sequence
                self._sequence += 1
                self._records.append(record)
        except Exception as error:
            self._recording_failure = error

    def _get_recording_failure(self) -> Exception | None:
        """Return the first private recording failure for evidence orchestration."""

        return self._recording_failure

    def _require_healthy(self) -> None:
        failure = self._recording_failure
        if failure is not None:
            raise RuntimeError(
                "execution observation is incomplete after a recording failure"
            ) from failure

    def snapshot(self) -> dict[str, object]:
        """Return detached Tool input without exposing the mutable recorder."""

        self._require_healthy()
        return {
            "header": {
                "record_type": "execution_observation_header",
                "format_version": 2,
                "metadata": deepcopy(self._metadata),
            },
            "records": tuple(deepcopy(self._records)),
        }

    def write_jsonl(self, path: Path, *, release: bool = False) -> Path:
        try:
            return self._write_jsonl(path, release=release)
        except Exception as error:
            with self._lock:
                self._recording_failure = error
                self._records.clear()
            raise

    def _write_jsonl(self, path: Path, *, release: bool = False) -> Path:
        """Persist the detached records after the observed execution."""

        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if release or self._streamed_path is not None:
            self._require_healthy()
            with self._lock:
                if self._streamed_path not in (None, destination):
                    raise ValueError("streamed observations belong to their original evidence path")
                with destination.open("w" if self._streamed_path is None else "a") as stream:
                    if self._streamed_path is None:
                        stream.write(json.dumps({"record_type": "execution_observation_header",
                            "format_version": 2, "metadata": self._metadata}) + "\n")
                    for record in self._records:
                        stream.write(json.dumps(record, separators=(",", ":")) + "\n")
                self._records.clear()
                self._streamed_path = destination
            return destination
        snapshot = self.snapshot()
        records = snapshot["records"]
        assert isinstance(records, tuple)
        values = (snapshot["header"], *records)
        destination.write_text(
            "".join(
                json.dumps(
                    value,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                + "\n"
                for value in values
            ),
            encoding="utf-8",
        )
        return destination


def _numeric_mapping(
    value: Mapping[str, int | float] | None,
    field_name: str,
) -> dict[str, int | float]:
    if value is not None and not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping or None")
    result = {}
    for key, item in dict(value or {}).items():
        label = _require_label(key, f"{field_name} key")
        result[label] = _require_number(item, f"{field_name}.{label}")
    return result


def _require_number(value: object, field_name: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be numeric")
    if not isfinite(float(value)):
        raise ValueError(f"{field_name} must be finite")
    return value


def _require_label(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _require_json_value(value: object, field_name: str) -> None:
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise TypeError(f"{field_name} must contain detached JSON values") from error


__all__: list[str] = []

from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from skyseed.shared.node_id import NodeId

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
