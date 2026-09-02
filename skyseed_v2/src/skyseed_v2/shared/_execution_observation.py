"""Record optional detached execution-cost observations for offline Tools."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import json
from math import isfinite
from pathlib import Path
from time import perf_counter


class _ExecutionObserver:
    """Retain coarse numeric observations without owning execution semantics."""

    __slots__ = (
        "_metadata",
        "_records",
        "_recording_failure",
        "_run_origin_monotonic",
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
            self._records.append(
                {
                    "record_type": "execution_observation",
                    "sequence": len(self._records),
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
            )
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

    def write_jsonl(self, path: Path) -> Path:
        """Persist the detached records after the observed execution."""

        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
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
