"""Aggregate detached execution records for system observation inspection."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from math import ceil
from statistics import median
from typing import Any

from .reader import ObservationRead


_TOP_LEVEL_TIMING_STAGES = {
    "physical-world": ("physical-projection",),
    "planner": ("planning-total", "publication-total"),
    "compiler": ("compile-total",),
    "realization-producer": ("producer-total",),
    "realization-handoff-writer": ("writer-total",),
    "runner": ("runner-total",),
    "traffic": ("traffic-total",),
}

_PIPELINE_GROUPS = (
    ("physical projection", (("physical-world", "physical-projection"),)),
    (
        "physical-link observation",
        (("physical-link", "physical-link-observation"),),
    ),
    ("ISL selection", (("planner", "isl-candidate-selection"),)),
    (
        "acquisition / lifecycle",
        (("planner", "acquisition/lifecycle-advancement"),),
    ),
    (
        "GroundStation planning",
        (("planner", "ground-station-planning"),),
    ),
    (
        "GroundStation selection / ranking",
        (("planner", "ground-station-selection-and-ranking"),),
    ),
    (
        "GroundStation validation",
        (("planner", "ground-station-validation"),),
    ),
    (
        "GroundStation evidence projection",
        (("planner", "ground-station-evidence-projection"),),
    ),
    ("Zone planning", (("planner", "zone-planning"),)),
    ("parameter planning", (("planner", "parameter-planning"),)),
    ("Route planning", (("planner", "route-planning"),)),
    (
        "semantic transition / application",
        (("planner", "state-advancement"),),
    ),
    ("coordination / precedence", (("planner", "coordination"),)),
    (
        "Plan publication",
        (
            ("planner", "plan-creation"),
            ("planner", "publication-total"),
        ),
    ),
    (
        "Compiler",
        (
            ("compiler", "plan-lineage-and-replay"),
            ("compiler", "semantic-to-bpf-lowering"),
            ("compiler", "compile-total"),
        ),
    ),
    ("realization producer", (("realization-producer", "*"),)),
    (
        "realization handoff writer",
        (("realization-handoff-writer", "*"),),
    ),
    ("Runner", (("runner", "*"),)),
    ("Traffic", (("traffic", "*"),)),
)


def project_observations(observations: ObservationRead) -> dict[str, Any]:
    """Create summaries while retaining exact invocation records separately."""

    if not isinstance(observations, ObservationRead):
        raise TypeError("observations must be an ObservationRead")
    records = observations.records
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    components: dict[str, list[dict[str, Any]]] = defaultdict(list)
    work_names: set[str] = set()
    stage_work_names: set[str] = set()
    resource_names: set[str] = set()
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
    ground_station_stage_names = {
        "ground-station-planning",
        "ground-station-selection-and-ranking",
        "ground-station-validation",
        "ground-station-evidence-projection",
    }
    ground_station_timing = [
        {"component": component["component"], **stage}
        for component in component_rows
        for stage in component["stages"]
        if stage["label"] in ground_station_stage_names
    ]
    return {
        "format_version": 2,
        "tool": "system-observation",
        "authority": {
            "status": "non-authoritative-view",
            "source": "detached records emitted by completed real execution",
            "execution": "the Tool parses, aggregates, and renders only",
            "duration_accounting": (
                "each invocation retains its owner and duration role; rollups "
                "overlap their child stages and are never summed with them"
            ),
            "work_and_resources": (
                "numeric work, RSS, input, output, and other recorded resource "
                "facts are copied without semantic inference"
            ),
        },
        "metadata": observations.header["metadata"],
        "timeline": _timeline_summary(records, observations.header["metadata"]),
        "execution_timeline": _execution_timeline(records),
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
        "pipeline_groups": _pipeline_groups(grouped),
        "components": component_rows,
        "top_level_timing_series": top_level_timing_series,
        "ground_station_timing": ground_station_timing,
        "work_measurement_names": sorted(work_names),
        "stage_work_measurement_names": sorted(stage_work_names),
        "resource_measurement_names": sorted(resource_names),
    }


def _pipeline_groups(
    grouped: dict[tuple[str, str], list[dict[str, Any]]],
) -> list[dict[str, object]]:
    rows = []
    for label, selectors in _PIPELINE_GROUPS:
        stage_refs = sorted(
            {
                (component, stage)
                for component, stage in grouped
                if any(
                    component == wanted_component
                    and (wanted_stage == "*" or stage == wanted_stage)
                    for wanted_component, wanted_stage in selectors
                )
            }
        )
        rows.append(
            {
                "label": label,
                "observations": sum(len(grouped[ref]) for ref in stage_refs),
                "stages": [
                    {"component": component, "stage": stage}
                    for component, stage in stage_refs
                ],
            }
        )
    return rows


def _execution_timeline(
    records: tuple[dict[str, Any], ...]
) -> dict[str, Any]:
    """Project directly recorded monotonic spans and overlap accounting."""

    spanned = [
        record
        for record in records
        if "run_relative_start_seconds" in record
        and "run_relative_end_seconds" in record
    ]
    if not spanned:
        return {
            "available": False,
            "axis_label": "run-relative monotonic seconds",
            "reason": "legacy records contain no measured monotonic spans",
            "lanes": [],
            "statistics": {},
        }
    start = min(float(record["run_relative_start_seconds"]) for record in spanned)
    end = max(float(record["run_relative_end_seconds"]) for record in spanned)
    intervals = sorted(
        (
            float(record["run_relative_start_seconds"]),
            float(record["run_relative_end_seconds"]),
        )
        for record in spanned
        if float(record["run_relative_end_seconds"])
        > float(record["run_relative_start_seconds"])
    )
    busy, gaps = _interval_union_and_gaps(intervals)
    event_deltas: dict[float, int] = defaultdict(int)
    for record in spanned:
        event_deltas[float(record["run_relative_start_seconds"])] += 1
        event_deltas[float(record["run_relative_end_seconds"])] -= 1
    concurrency = 0
    maximum_concurrency = 0
    overlap_wall = 0.0
    previous_time: float | None = None
    for timestamp, delta in sorted(event_deltas.items()):
        if previous_time is not None and concurrency >= 2:
            overlap_wall += timestamp - previous_time
        concurrency += delta
        maximum_concurrency = max(maximum_concurrency, concurrency)
        previous_time = timestamp
    summed = sum(float(record["duration_seconds"]) for record in spanned)
    lane_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in spanned:
        lane_groups[str(record["component"])].append(
            {
                "sequence": record["sequence"],
                "stage": record["stage"],
                "phase": record["phase"],
                "duration_role": record.get("duration_role", "stage"),
                "start_seconds": record["run_relative_start_seconds"],
                "end_seconds": record["run_relative_end_seconds"],
                "duration_seconds": record["duration_seconds"],
                "scope": record["scope"],
                "work": record["work"],
                "resources": record["resources"],
                "span_source": record["span_source"],
            }
        )
    component_active_wall = {
        component: _interval_union_and_gaps(
            sorted(
                (
                    float(value["start_seconds"]),
                    float(value["end_seconds"]),
                )
                for value in values
                if float(value["end_seconds"])
                > float(value["start_seconds"])
            )
        )[0]
        for component, values in sorted(lane_groups.items())
    }
    component_events: dict[float, int] = defaultdict(int)
    for values in lane_groups.values():
        merged = _merge_intervals(
            sorted(
                (
                    float(value["start_seconds"]),
                    float(value["end_seconds"]),
                )
                for value in values
                if float(value["end_seconds"])
                > float(value["start_seconds"])
            )
        )
        for left, right in merged:
            component_events[left] += 1
            component_events[right] -= 1
    active_components = 0
    maximum_active_components = 0
    cross_component_overlap = 0.0
    previous_component_time: float | None = None
    for timestamp, delta in sorted(component_events.items()):
        if previous_component_time is not None and active_components >= 2:
            cross_component_overlap += timestamp - previous_component_time
        active_components += delta
        maximum_active_components = max(
            maximum_active_components, active_components
        )
        previous_component_time = timestamp
    excess_span_time = max(0.0, summed - busy)
    return {
        "available": True,
        "axis_label": "run-relative monotonic seconds",
        "start_seconds": start,
        "end_seconds": end,
        "span_seconds": max(0.0, end - start),
        "lanes": [
            {
                "component": component,
                "spans": sorted(
                    values,
                    key=lambda value: (
                        value["start_seconds"], value["sequence"]
                    ),
                ),
            }
            for component, values in sorted(lane_groups.items())
        ],
        "statistics": {
            "recorded_span_count": len(spanned),
            "summed_duration_seconds": summed,
            "busy_union_seconds": busy,
            "cross_component_overlap_seconds": cross_component_overlap,
            "cross_component_overlap_ratio": (
                cross_component_overlap / busy if busy else 0.0
            ),
            "maximum_active_distinct_components": maximum_active_components,
            "overlap_seconds": overlap_wall,
            "overlap_ratio": overlap_wall / busy if busy else 0.0,
            "excess_span_seconds": excess_span_time,
            "idle_gap_seconds": gaps,
            "maximum_concurrency": maximum_concurrency,
            "component_active_wall_seconds": component_active_wall,
        },
    }


def _interval_union_and_gaps(
    intervals: list[tuple[float, float]],
) -> tuple[float, float]:
    merged = _merge_intervals(intervals)
    busy = sum(right - left for left, right in merged)
    gaps = sum(
        right[0] - left[1]
        for left, right in zip(merged, merged[1:], strict=False)
    )
    return busy, gaps


def _merge_intervals(
    intervals: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """Merge sorted half-open wall-time intervals exactly once."""

    merged: list[tuple[float, float]] = []
    for left, right in intervals:
        if not merged or left > merged[-1][1]:
            merged.append((left, right))
        elif right > merged[-1][1]:
            merged[-1] = (merged[-1][0], right)
    return merged


def _timeline_summary(
    records: tuple[dict[str, Any], ...], metadata: dict[str, Any]
) -> dict[str, Any]:
    timestamps: list[datetime] = []
    formal_timestamps: set[datetime] = set()
    all_physical_frame_timestamps: set[datetime] = set()
    plan_publications: set[object] = set()
    compiler_realizations: set[object] = set()
    producer_invocations: set[object] = set()
    for record in records:
        scope = record["scope"]
        for field_name in ("timestamp", "start_timestamp", "end_timestamp"):
            parsed = _parse_timestamp(scope.get(field_name))
            if parsed is not None:
                timestamps.append(parsed)
        component = record["component"]
        stage = record["stage"]
        timestamp = _parse_timestamp(scope.get("timestamp"))
        if (
            timestamp is not None
            and component == "planner"
            and stage == "planning-total"
            and _is_positive_count(record["work"].get("formal_transitions"))
        ):
            formal_timestamps.add(timestamp)
        if component == "physical-world" and timestamp is not None:
            all_physical_frame_timestamps.add(timestamp)
        if component == "planner" and stage == "publication-total":
            identity = scope.get("plan_id", scope.get("publication_index"))
            if identity is not None:
                plan_publications.add(identity)
        if component == "compiler":
            identity = scope.get("plan_id", scope.get("compile_index"))
            if identity is not None:
                compiler_realizations.add(identity)
        if component == "realization-producer":
            identity = scope.get("invocation_index", scope.get("timestamp"))
            if identity is not None:
                producer_invocations.add(identity)

    formal_interval_seconds = _metadata_formal_interval(metadata)
    if formal_interval_seconds is None and len(formal_timestamps) > 1:
        ordered = sorted(formal_timestamps)
        intervals = [
            (right - left).total_seconds()
            for left, right in zip(ordered, ordered[1:], strict=False)
            if right > left
        ]
        if intervals:
            formal_interval_seconds = median(intervals)

    formal_or_all = formal_timestamps or set(timestamps)
    physical_frame_timestamps = all_physical_frame_timestamps & formal_timestamps
    prehistory_frame_timestamps = (
        all_physical_frame_timestamps - formal_timestamps
    )
    return {
        "simulation_start_timestamp": (
            min(formal_or_all).isoformat() if formal_or_all else None
        ),
        "simulation_end_timestamp": (
            max(formal_or_all).isoformat() if formal_or_all else None
        ),
        "formal_interval_seconds": formal_interval_seconds,
        "formal_transition_unit_count": len(formal_timestamps),
        "physical_frame_unit_count": len(physical_frame_timestamps),
        "prehistory_physical_frame_unit_count": len(prehistory_frame_timestamps),
        "plan_publication_unit_count": len(plan_publications),
        "compiler_realization_unit_count": len(compiler_realizations),
        "producer_invocation_unit_count": len(producer_invocations),
        "x_axis_semantics": (
            "formal simulation timestamps aligned across PhysicalFrame, "
            "Planner transition/publication, Compiler realization, Runner, and Traffic evidence"
        ),
    }


def _is_positive_count(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and value > 0
    )


def _metadata_formal_interval(metadata: dict[str, Any]) -> float | None:
    for name in ("formal_interval_seconds", "observation_interval_seconds"):
        value = metadata.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        interval = float(value)
        if interval > 0:
            return interval
    return None


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
    by_phase = {
        phase: [record for record in records if record["phase"] == phase]
        for phase in ("bootstrap", "steady", "one-shot")
    }
    durations = {
        phase: [float(record["duration_seconds"]) for record in values]
        if timed
        else []
        for phase, values in by_phase.items()
    }
    work_totals: dict[str, int | float] = defaultdict(int)
    for record in records:
        for name, value in record["work"].items():
            work_totals[name] += value
    steady = durations["steady"]
    one_shot = durations["one-shot"]
    return {
        "label": label,
        "duration_role": duration_role,
        "observations": len(records),
        "bootstrap_count": len(by_phase["bootstrap"]),
        "bootstrap_run_seconds": sum(durations["bootstrap"]) if timed else None,
        "steady_count": len(by_phase["steady"]),
        "steady_p50_seconds": median(steady) if steady else None,
        "steady_p95_seconds": _nearest_rank(steady, 0.95),
        "steady_max_seconds": max(steady) if steady else None,
        "one_shot_count": len(by_phase["one-shot"]),
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


def _resource_maxima(records: list[dict[str, Any]]) -> dict[str, int | float]:
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
