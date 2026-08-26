"""Aggregate detached execution records for system observation inspection."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from math import ceil
from statistics import median
from typing import Any

from .reader import ObservationRead


_TOP_LEVEL_TIMING_STAGES = {
    "slicer": ("complete-slice", "difference"),
    "planner": ("total-synchronize",),
    "compiler": ("total-compile",),
}


def project_observations(observations: ObservationRead) -> dict[str, Any]:
    """Create compact summaries while retaining records only in the read model."""

    if not isinstance(observations, ObservationRead):
        raise TypeError("observations must be an ObservationRead")
    records = observations.records
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    components: dict[str, list[dict[str, Any]]] = defaultdict(list)
    work_names = set()
    stage_work_names = set()
    resource_names = set()
    for record in records:
        component = str(record["component"])
        stage = str(record["stage"])
        grouped[(component, stage)].append(record)
        components[component].append(record)
        work_names.update(record["work"])
        if record.get("duration_role", "stage") == "stage":
            stage_work_names.update(
                name
                for name, value in record["work"].items()
                if float(value) > 0
            )
        resource_names.update(record["resources"])

    component_rows = []
    top_level_timing_series = []
    for component in sorted(components):
        component_records = components[component]
        stage_rows = [
            _summary_row(stage, grouped[(component, stage)])
            for stage in sorted(
                stage_name
                for component_name, stage_name in grouped
                if component_name == component
            )
        ]
        stages_by_name = {row["label"]: row for row in stage_rows}
        component_top_level = [
            stages_by_name[stage]
            for stage in _TOP_LEVEL_TIMING_STAGES.get(component, ())
            if stage in stages_by_name
        ]
        top_level_timing_series.extend(
            {"component": component, **row} for row in component_top_level
        )
        component_rows.append(
            {
                "component": component,
                "label": component,
                "observations": len(component_records),
                "resource_maxima": _resource_maxima(component_records),
                "stages": stage_rows,
                "top_level_timing_series": [
                    row["label"] for row in component_top_level
                ],
            }
        )

    phases = {
        phase: sum(1 for record in records if record["phase"] == phase)
        for phase in ("bootstrap", "steady", "idle", "one-shot")
    }
    return {
        "format_version": 1,
        "tool": "system-observation",
        "authority": {
            "status": "non-authoritative-view",
            "source": "detached records emitted by completed real execution",
            "execution": "the Tool parses, aggregates, and renders only",
            "semantics": "durations, numeric work, and optional resources",
        },
        "metadata": observations.header["metadata"],
        "timeline": _timeline_summary(
            records,
            observations.header["metadata"],
        ),
        "inventory": {
            "observations": len(records),
            "components": len(components),
            "stages": len(grouped),
            "bootstrap_observations": phases["bootstrap"],
            "steady_observations": phases["steady"],
            "idle_observations": phases["idle"],
            "one_shot_observations": phases["one-shot"],
            "work_measurements": len(work_names),
            "resource_measurements": len(resource_names),
        },
        "components": component_rows,
        "top_level_timing_series": top_level_timing_series,
        "work_measurement_names": sorted(work_names),
        "stage_work_measurement_names": sorted(stage_work_names),
        "resource_measurement_names": sorted(resource_names),
    }


def _timeline_summary(
    records: tuple[dict[str, Any], ...],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    timestamps = []
    slices: dict[int, datetime] = {}
    planner_publications = set()
    compiler_invocations = set()
    for record in records:
        scope = record["scope"]
        for field_name in ("timestamp", "start_timestamp", "end_timestamp"):
            value = scope.get(field_name)
            parsed = _parse_timestamp(value)
            if parsed is not None:
                timestamps.append(parsed)
        if record["component"] == "slicer":
            slice_id = scope.get("slice_id", scope.get("current_slice_id"))
            timestamp = _parse_timestamp(scope.get("timestamp"))
            if isinstance(slice_id, int) and timestamp is not None:
                slices.setdefault(slice_id, timestamp)
        if record["component"] == "planner":
            publication_index = scope.get("publication_index")
            if isinstance(publication_index, int):
                planner_publications.add(publication_index)
        if record["component"] == "compiler":
            compile_index = scope.get("compile_index")
            if isinstance(compile_index, int):
                compiler_invocations.add(compile_index)

    slice_interval_seconds = _metadata_slice_interval(metadata)
    if slice_interval_seconds is None and len(slices) > 1:
        ordered = sorted(slices.items())
        intervals = [
            (right_time - left_time).total_seconds()
            / (right_id - left_id)
            for (left_id, left_time), (right_id, right_time) in zip(
                ordered,
                ordered[1:],
                strict=False,
            )
            if right_id > left_id and right_time > left_time
        ]
        if intervals:
            slice_interval_seconds = median(intervals)

    return {
        "simulation_start_timestamp": (
            min(timestamps).isoformat() if timestamps else None
        ),
        "simulation_end_timestamp": (
            max(timestamps).isoformat() if timestamps else None
        ),
        "slice_interval_seconds": slice_interval_seconds,
        "slicer_unit_count": len(slices),
        "planner_unit_count": len(planner_publications),
        "compiler_unit_count": len(compiler_invocations),
        "x_axis_semantics": (
            "simulation timestamp aligned by Slice identity, Planner "
            "publication coverage, and its 1:1 Compiler realization"
        ),
    }


def _metadata_slice_interval(metadata: dict[str, Any]) -> float | None:
    value = metadata.get("slice_interval_seconds")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    interval = float(value)
    return interval if interval > 0 else None


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _summary_row(label: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    roles = {record.get("duration_role", "stage") for record in records}
    if len(roles) != 1:
        raise ValueError(f"stage {label!r} mixes duration roles")
    duration_role = roles.pop()
    timed = duration_role in {"stage", "rollup"}
    bootstrap_records = [
        record for record in records if record["phase"] == "bootstrap"
    ]
    steady_records = [
        record for record in records if record["phase"] == "steady"
    ]
    one_shot_records = [
        record for record in records if record["phase"] == "one-shot"
    ]
    bootstrap = [
        float(record["duration_seconds"])
        for record in bootstrap_records
        if timed
    ]
    steady = [
        float(record["duration_seconds"])
        for record in steady_records
        if timed
    ]
    one_shot = [
        float(record["duration_seconds"])
        for record in one_shot_records
        if timed
    ]
    work_totals: dict[str, int | float] = defaultdict(int)
    for record in records:
        for name, value in record["work"].items():
            work_totals[name] += value
    return {
        "label": label,
        "duration_role": duration_role,
        "observations": len(records),
        "bootstrap_count": len(bootstrap_records),
        "bootstrap_run_seconds": sum(bootstrap) if timed else None,
        "steady_count": len(steady_records),
        "steady_p50_seconds": median(steady) if steady else None,
        "steady_p95_seconds": _nearest_rank(steady, 0.95),
        "steady_max_seconds": max(steady) if steady else None,
        "one_shot_count": len(one_shot_records),
        "one_shot_p50_seconds": median(one_shot) if one_shot else None,
        "one_shot_p95_seconds": _nearest_rank(one_shot, 0.95),
        "one_shot_max_seconds": max(one_shot) if one_shot else None,
        "run_seconds": (
            sum(float(record["duration_seconds"]) for record in records)
            if timed
            else None
        ),
        "work_totals": dict(sorted(work_totals.items())),
        "resource_maxima": _resource_maxima(records),
    }


def _resource_maxima(
    records: list[dict[str, Any]],
) -> dict[str, int | float]:
    maxima: dict[str, int | float] = {}
    for record in records:
        for name, value in record["resources"].items():
            maxima[name] = max(value, maxima.get(name, value))
    return dict(sorted(maxima.items()))


def _nearest_rank(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, ceil(percentile * len(ordered)) - 1)]


__all__ = ["project_observations"]
