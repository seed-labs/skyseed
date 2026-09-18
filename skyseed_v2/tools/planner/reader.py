"""Read immutable Planner publication evidence into detached Tool values."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from skyseed_v2.experiment.output.history import (
    _DomainHistoryManifest,
    _iter_verified_domain_history_stream,
    _read_domain_history_manifest,
    _timedelta_from_data,
)
from skyseed_v2.simulator import (
    PublishedPlanningEvidence, Plan, PlanningFrontiers, apply_plan,
    plan_transition_fingerprint,
)
from skyseed_v2.simulator.planner import (
    published_planning_evidence_from_data,
    plan_from_data,
)

from skyseed_v2.simulator.planner.evidence import _frontiers_from_data


@dataclass(frozen=True, slots=True)
class PlannerInspectionRead:
    """Hold validated immutable evidence plus its detached JSON projection."""

    evidence: tuple[PublishedPlanningEvidence, ...]
    snapshot: dict[str, Any]


def read_planner_history(path: Path) -> tuple[PublishedPlanningEvidence, ...]:
    """Read and replay one terminal producer-owned domain history."""

    return tuple(iter_planner_history(path))


def iter_planner_history(path: Path) -> Iterator[PublishedPlanningEvidence]:
    """Stream validated publications without retaining the whole history."""

    manifest = _read_domain_history_manifest(
        Path(path), minimum_through="planner"
    )
    return _iter_planner_history(manifest)


def _iter_projection_planner_history(path):
    # The projection performs the required replay and checks both fingerprints;
    # this path validates stream integrity/lineage without replaying it twice.
    manifest = _read_domain_history_manifest(Path(path), minimum_through='planner')
    return _iter_planner_history(manifest, replay=False)


@dataclass(frozen=True, slots=True)
class _PlanHistoryRead:
    """Semantic portion of a sealed publication for the Traffic consumer."""

    plan: Plan
    transition_fingerprints: tuple[str, ...]
    frontiers: PlanningFrontiers


def _read_plan_history_entry(data):
    if not isinstance(data, dict) or set(data) != {
        "schema", "plan", "transition_fingerprints", "frames", "frontiers"
    }:
        raise ValueError("invalid published planning evidence envelope")
    if data["schema"] not in {"skyseed-v2-published-planning-evidence-v5"}:
        raise ValueError("unsupported PublishedPlanningEvidence schema")
    plan = plan_from_data(data["plan"])
    fingerprints = tuple(data["transition_fingerprints"])
    if fingerprints != tuple(plan_transition_fingerprint(value) for value in plan.transitions):
        raise ValueError("transition_fingerprints must match exact Plan transitions")
    frames = data["frames"]
    if not isinstance(frames, list) or any(not isinstance(frame, dict) for frame in frames):
        raise ValueError("invalid planning frame evidence")
    if tuple(frame.get("timestamp") for frame in frames) != tuple(
        value.timestamp.isoformat() for value in plan.transitions
    ):
        raise ValueError("frame timestamps must match Plan transitions")
    if any("ground_station_preparations" not in frame for frame in frames):
        raise ValueError("current evidence requires GS preparation records")
    frontiers = _frontiers_from_data(data["frontiers"])
    if frontiers.publication != plan.end_timestamp:
        raise ValueError("evidence publication frontier must equal Plan end")
    return _PlanHistoryRead(plan, fingerprints, frontiers)


def _iter_traffic_planner_history(path):
    # Integrity, lineage, full Plan validation and terminal coverage are shared.
    # Traffic replays the semantic Plan once; Model inspection remains in the
    # authoritative sealed stream and is reconstructed by the Planner Tool.
    manifest = _read_domain_history_manifest(Path(path), minimum_through="compiler")
    return _iter_planner_history(manifest, replay=False, plan_only=True)


def _iter_planner_history(
    manifest: _DomainHistoryManifest,
    *, replay: bool = True, plan_only: bool = False,
) -> Iterator[PublishedPlanningEvidence]:
    """Validate the sealed streams together while yielding one Plan at a time."""

    values = _iter_verified_domain_history_stream(manifest, "planning")
    try:
        header = next(values)
    except StopIteration as error:
        raise ValueError("Planner evidence stream is empty") from error
    if header != _PLANNING_HEADER:
        raise ValueError("Planner evidence header is invalid")

    physical_rows = _iter_verified_domain_history_stream(
        manifest, "physical_frames"
    )
    try:
        physical_header = next(physical_rows)
    except StopIteration as error:
        raise ValueError("PhysicalPositionSnapshot evidence stream is empty") from error
    if physical_header != _PHYSICAL_HEADER:
        raise ValueError("PhysicalPositionSnapshot evidence header is invalid")

    compiler_rows = _iter_verified_domain_history_stream(manifest, "compiler")
    try:
        compiler_header = next(compiler_rows)
    except StopIteration as error:
        raise ValueError("Compiler evidence stream is empty") from error
    if compiler_header not in (_COMPILER_HEADER, _COMPILER_HEADER_V2):
        raise ValueError("Compiler evidence header is invalid")

    data = manifest.data
    timeline = data["timeline"]
    start = _history_timestamp(timeline["start_timestamp"], "Timeline start")
    end = _history_timestamp(timeline["end_timestamp"], "Timeline end")
    interval = _timedelta_from_data(
        timeline["observation_interval"], "observation_interval"
    )
    counts = data["counts"]
    world_fingerprint = data["physical_world_fingerprint"]
    through = data["through"]
    previous = None
    state = None
    plan_count = 0
    transition_count = 0
    first_plan_id = None
    final_evidence = None
    for row in values:
        if set(row) != {"record_type", "data"}:
            raise ValueError("Planner evidence row has unknown fields")
        if row["record_type"] != "published_planning_evidence":
            raise ValueError("Planner evidence record type is invalid")
        data = row["data"]
        if not isinstance(data, dict):
            raise TypeError("Planner evidence data must be an object")
        evidence = (_read_plan_history_entry(data) if plan_only
                    else published_planning_evidence_from_data(data))
        plan = evidence.plan
        if plan.physical_world_fingerprint != world_fingerprint:
            raise ValueError(
                "Planner evidence PhysicalWorld differs from terminal manifest"
            )
        if plan.observation_interval != interval:
            raise ValueError(
                "Planner evidence cadence differs from terminal manifest"
            )
        if previous is None:
            if plan.previous_plan_id is not None:
                raise ValueError("first Planner evidence must be bootstrap")
            first_plan_id = plan.plan_id
        else:
            if plan.previous_plan_id != previous.plan.plan_id:
                raise ValueError("Planner evidence Plan lineage is not continuous")
            if (
                plan.input_state_fingerprint
                != previous.plan.output_state_fingerprint
            ):
                raise ValueError(
                    "Planner evidence state lineage is not continuous"
                )
            if plan.start_timestamp <= previous.plan.end_timestamp:
                raise ValueError("Planner evidence timestamp coverage overlaps")

        if replay:
            state = apply_plan(
                state,
                plan,
                physical_world_fingerprint=world_fingerprint,
            )
        for transition in plan.transitions:
            if transition_count >= counts["expected_formal_frames"]:
                raise ValueError(
                    "Planner evidence does not cover the exact terminal "
                    "Timeline cadence"
                )
            expected_timestamp = start + transition_count * interval
            if transition.timestamp != expected_timestamp:
                raise ValueError(
                    "Planner evidence does not cover the exact terminal "
                    "Timeline cadence"
                )
            try:
                physical_row = next(physical_rows)
            except StopIteration as error:
                raise ValueError(
                    "PhysicalPositionSnapshot and Planner formal timestamp coverage differ"
                ) from error
            _validate_physical_row(physical_row, expected_timestamp)
            transition_count += 1

        if through == "compiler":
            try:
                compiler_row = next(compiler_rows)
            except StopIteration as error:
                raise ValueError(
                    "Compiler and Planner publication counts differ"
                ) from error
            _validate_compiler_row(
                compiler_row,
                index=plan_count,
                plan=plan,
                world_fingerprint=world_fingerprint,
            )
        plan_count += 1
        previous = evidence
        final_evidence = evidence
        yield evidence

    if final_evidence is None:
        raise TypeError(
            "evidence must contain at least one PublishedPlanningEvidence"
        )
    if plan_count != counts["plans"]:
        raise ValueError("Planner evidence count differs from terminal manifest")
    if transition_count != counts["transitions"]:
        raise ValueError("Planner transition count differs from terminal manifest")
    if transition_count != counts["expected_formal_frames"]:
        raise ValueError(
            "Planner evidence does not cover the exact terminal Timeline cadence"
        )
    if final_evidence.plan.end_timestamp != end:
        raise ValueError("Planner evidence does not reach the Timeline end")
    if (
        final_evidence.frontiers.physical_observation != end
        or final_evidence.frontiers.planning != end
        or final_evidence.frontiers.publication != end
    ):
        raise ValueError("Planner evidence final frontiers are incomplete")
    lineage = manifest.data["lineage"]
    if (
        first_plan_id != lineage["first_plan_id"]
        or final_evidence.plan.plan_id != lineage["last_plan_id"]
        or final_evidence.plan.previous_plan_id
        != lineage["last_plan_previous_plan_id"]
        or final_evidence.plan.output_state_fingerprint
        != lineage["last_plan_output_state_fingerprint"]
    ):
        raise ValueError("Planner evidence final lineage differs from manifest")
    _require_stream_exhausted(
        physical_rows,
        "PhysicalPositionSnapshot and Planner formal timestamp coverage differ",
    )
    _require_stream_exhausted(
        compiler_rows,
        (
            "Planner-only history contains Compiler evidence"
            if through == "planner"
            else "Compiler and Planner publication counts differ"
        ),
    )


def _validate_physical_row(
    row: dict[str, Any], expected_timestamp: datetime
) -> None:
    if set(row) != {"record_type", "timestamp", "positions"}:
        raise ValueError("PhysicalPositionSnapshot evidence row has unknown fields")
    if row["record_type"] != "physical_frame":
        raise ValueError("PhysicalPositionSnapshot evidence record type is invalid")
    if not isinstance(row["positions"], list):
        raise TypeError("PhysicalPositionSnapshot positions must be a list")
    timestamp = _history_timestamp(row["timestamp"], "PhysicalPositionSnapshot timestamp")
    if timestamp != expected_timestamp:
        raise ValueError(
            "PhysicalPositionSnapshot and Planner formal timestamp coverage differ"
        )


def _validate_compiler_row(
    row: dict[str, Any],
    *,
    index: int,
    plan: Any,
    world_fingerprint: str,
) -> None:
    if set(row) not in (_COMPILER_FIELDS, _COMPILER_FIELDS_V2):
        raise ValueError("Compiler evidence row has unknown fields")
    expected = {
        "record_type": "compiled_realization",
        "realization_index": index,
        "source_plan_id": plan.plan_id,
        "source_previous_plan_id": plan.previous_plan_id,
        "physical_world_fingerprint": world_fingerprint,
        "input_state_fingerprint": plan.input_state_fingerprint,
        "output_state_fingerprint": plan.output_state_fingerprint,
        "start_timestamp": plan.start_timestamp.isoformat(),
        "end_timestamp": plan.end_timestamp.isoformat(),
        "activation_timestamps": [
            transition.timestamp.isoformat()
            for transition in plan.transitions
        ],
    }
    for name, expected_value in expected.items():
        if row[name] != expected_value:
            raise ValueError(f"Compiler {name} differs from Planner lineage")
    if not isinstance(row["intent_counts"], dict):
        raise TypeError("Compiler intent counts must be an object")
    if not isinstance(row["semantic_precedence_folding"], dict):
        raise TypeError("Compiler semantic precedence folding must be an object")


def _require_stream_exhausted(
    rows: Iterator[dict[str, Any]], message: str
) -> None:
    try:
        next(rows)
    except StopIteration:
        return
    raise ValueError(message)


def read_planner_inspection(
    evidence: tuple[PublishedPlanningEvidence, ...],
) -> PlannerInspectionRead:
    """Detach an aligned publication sequence without executing Models."""

    values = _validated_sequence(tuple(evidence))
    return PlannerInspectionRead(
        evidence=values,
        snapshot={
            "format_version": 2,
            "tool": "planner-inspection",
            "authority": {
                "status": "non-authoritative-view",
                "source": "immutable PublishedPlanningEvidence publications",
                "planner_models": "not invoked by this Tool",
                "operational_state": (
                    "replayed with the public canonical transition application"
                ),
                "physical_candidates": (
                    "feasible physical facts; never presented as operational"
                ),
                "compiled": "not inferred from Planner evidence",
                "realized": "not inferred from Planner evidence",
            },
        },
    )


def _validated_sequence(
    evidence: tuple[PublishedPlanningEvidence, ...],
) -> tuple[PublishedPlanningEvidence, ...]:
    if not evidence or any(
        not isinstance(value, PublishedPlanningEvidence) for value in evidence
    ):
        raise TypeError(
            "evidence must contain at least one PublishedPlanningEvidence"
        )
    world_fingerprint = evidence[0].plan.physical_world_fingerprint
    previous = None
    for index, value in enumerate(evidence):
        plan = value.plan
        if plan.physical_world_fingerprint != world_fingerprint:
            raise ValueError("Planner evidence mixes PhysicalWorld provenance")
        if index == 0:
            if plan.previous_plan_id is not None:
                raise ValueError("first Planner evidence must be bootstrap")
        else:
            assert previous is not None
            if plan.previous_plan_id != previous.plan.plan_id:
                raise ValueError("Planner evidence Plan lineage is not continuous")
            if plan.input_state_fingerprint != previous.plan.output_state_fingerprint:
                raise ValueError("Planner evidence state lineage is not continuous")
            if plan.start_timestamp <= previous.plan.end_timestamp:
                raise ValueError("Planner evidence timestamp coverage overlaps")
        previous = value
    return evidence


_PLANNING_HEADER = {
    "record_type": "published_planning_history_header",
    "format_version": 2,
    "codec": "skyseed-v2-planning-evidence",
}
_PHYSICAL_HEADER = {
    "record_type": "physical_frame_history_header",
    "format_version": 1,
    "coordinate_system": "WGS84-ECEF-metres",
    "formal_frames_only": True,
}
_COMPILER_HEADER = {
    "record_type": "compiled_realization_history_header",
    "format_version": 1,
    "semantic_payload_owner": "canonical realization handoff",
}
_COMPILER_HEADER_V2 = {
    "record_type": "compiled_realization_history_header",
    "format_version": 3,
    "semantic_payload_owner": "sealed Runner artifact",
    "projection": "compact-exact-compiler-inspection-v2",
}
_COMPILER_FIELDS = {
    "record_type",
    "realization_index",
    "source_plan_id",
    "source_previous_plan_id",
    "physical_world_fingerprint",
    "input_state_fingerprint",
    "output_state_fingerprint",
    "start_timestamp",
    "end_timestamp",
    "activation_timestamps",
    "intent_counts",
    "semantic_precedence_folding",
}
_COMPILER_FIELDS_V2 = _COMPILER_FIELDS | {
    "compact_summary",
    "operation_counts",
    "boundary_workloads",
    "intended_state_timestamp",
    "zone_prefixes",
}


def _history_timestamp(value: object, field_name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{field_name} is invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    utc = parsed.astimezone(timezone.utc)
    if utc.isoformat() != value:
        raise ValueError(f"{field_name} must use canonical UTC")
    return utc


__all__ = [
    "PlannerInspectionRead",
    "iter_planner_history",
    "read_planner_history",
    "read_planner_inspection",
]
