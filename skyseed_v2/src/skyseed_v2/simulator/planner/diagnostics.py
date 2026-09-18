"""Bounded diagnostic values, separate from the formal Plan and Planner."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from skyseed_v2.shared import NodeId
from skyseed_v2.shared.time import normalize_utc_datetime
from ..physical import (GroundStationAccessCandidate, InterSatelliteLinkCandidate,
    PhysicalLinkWorkCounts, ZoneServiceCandidate)
from .values import Plan, plan_transition_fingerprint, _node_key, _require_node_id

class CandidateDecisionKind(Enum):
    """Classify compact ISL selection and underfill inspection evidence.

    Layer: public immutable Simulator planning-inspection classification. It is
    public because injected Models and detached Planner evidence share the
    selection explanation vocabulary. It is not Connection lifecycle state,
    a Plan event kind, or backend result.
    """

    RETAINED = "retained"
    SELECTED = "selected"
    REJECTED = "rejected"
    UNDERFILLED = "underfilled"


@dataclass(frozen=True, slots=True)
class CandidateDecision:
    """Explain one compact ISL selection, rejection, or underfill decision.

    Layer: public immutable Simulator planning-inspection value. It is public
    because replaceable ISL Models return it and Planner derives bounded reason
    histograms from it. It is not durable per-candidate history, an operational
    Connection, physical candidate authority, or Plan transition payload.
    """

    decision: CandidateDecisionKind
    reason: str
    endpoint_a: NodeId | None = None
    endpoint_b: NodeId | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.decision, CandidateDecisionKind):
            raise TypeError("decision must be a CandidateDecisionKind")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("reason must be a non-empty string")
        object.__setattr__(self, "reason", self.reason.strip())
        if (self.endpoint_a is None) != (self.endpoint_b is None):
            raise ValueError("candidate decision endpoints are all-or-none")
        if self.endpoint_a is not None:
            _require_node_id(self.endpoint_a, "endpoint_a")
            _require_node_id(self.endpoint_b, "endpoint_b")


@dataclass(frozen=True, slots=True)
class PlanningInspection:
    """Summarize bounded planning work and lifecycle outcomes at one boundary.

    Layer: public immutable Simulator planning-inspection value. It is public
    because detached evidence and Tools need a stable compact summary without
    executing Models. It is not authoritative PlannedOperationalState, a full
    candidate history, or a backend/runtime snapshot.
    """

    timestamp: datetime
    feasible_isl_candidates: int
    selected_isls: int
    acquiring_isls: int
    operational_isls: int
    rejected_isls: int
    underfilled_isl_slots: int | None
    ground_station_accesses: int
    zones_served: int
    zones_unserved: int
    routes_reachable: int
    routes_unreachable: int
    route_recomputed: bool
    acquisition_started: int = 0
    acquisition_completed: int = 0
    acquisition_reset: int = 0
    stages: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "timestamp",
            normalize_utc_datetime(self.timestamp, "timestamp"),
        )
        for field_name in (
            "feasible_isl_candidates",
            "selected_isls",
            "acquiring_isls",
            "operational_isls",
            "rejected_isls",
            "underfilled_isl_slots",
            "ground_station_accesses",
            "zones_served",
            "zones_unserved",
            "routes_reachable",
            "routes_unreachable",
            "acquisition_started",
            "acquisition_completed",
            "acquisition_reset",
        ):
            value = getattr(self, field_name)
            if field_name == "underfilled_isl_slots" and value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
        if not isinstance(self.route_recomputed, bool):
            raise TypeError("route_recomputed must be a bool")
        stages = tuple(self.stages)
        if any(not isinstance(value, str) or not value for value in stages):
            raise ValueError("stages must contain non-empty strings")
        object.__setattr__(self, "stages", stages)


@dataclass(frozen=True, slots=True)
class PlanningFrontiers:
    """Expose distinct observed, planned, and published timestamp frontiers.

    Layer: public immutable Simulator Planner lifecycle value. It is public
    because producers, inspection, and qualification must observe each frontier
    without reading Planner internals. It is not a Timeline cursor, publication
    command, or guarantee that the three timestamps are equal.
    """

    physical_observation: datetime | None
    planning: datetime | None
    publication: datetime | None


@dataclass(frozen=True, slots=True)
class InterSatelliteLifecycleEvidence:
    """Describe one selected ISL's physical fact and acquisition state.

    Layer: public immutable Simulator planning-evidence value. It is public
    because detached evidence and Tools must distinguish selected, acquiring,
    and operational ISLs. It is not authoritative lifecycle storage, an
    OperationalConnection, or a physical candidate decision by itself.
    """

    candidate: InterSatelliteLinkCandidate
    selected_since: datetime
    acquisition_duration: timedelta
    remaining_acquisition: timedelta
    operational: bool

    def __post_init__(self) -> None:
        if not isinstance(self.candidate, InterSatelliteLinkCandidate):
            raise TypeError("candidate must be an InterSatelliteLinkCandidate")
        object.__setattr__(
            self,
            "selected_since",
            normalize_utc_datetime(self.selected_since, "selected_since"),
        )
        for field_name in ("acquisition_duration", "remaining_acquisition"):
            value = getattr(self, field_name)
            if not isinstance(value, timedelta):
                raise TypeError(f"{field_name} must be a timedelta")
            if value < timedelta(0):
                raise ValueError(f"{field_name} cannot be negative")
        if self.remaining_acquisition > self.acquisition_duration:
            raise ValueError(
                "remaining_acquisition cannot exceed acquisition_duration"
            )
        if not isinstance(self.operational, bool):
            raise TypeError("operational must be a bool")
        if self.operational and self.remaining_acquisition != timedelta(0):
            raise ValueError("operational lifecycle cannot have remaining acquisition")


@dataclass(frozen=True, slots=True)
class GroundStationLifecycleEvidence:
    """Describe one selected GS access's physical fact and acquisition state.

    Layer: public immutable Simulator planning-evidence value. It is public
    because detached evidence and Tools must distinguish selected, acquiring,
    and operational GS accesses. It is not authoritative lifecycle storage, an
    OperationalConnection, or a physical candidate decision by itself.
    """

    candidate: GroundStationAccessCandidate
    selected_since: datetime
    acquisition_duration: timedelta
    remaining_acquisition: timedelta
    operational: bool

    def __post_init__(self) -> None:
        if not isinstance(self.candidate, GroundStationAccessCandidate):
            raise TypeError("candidate must be an GroundStationAccessCandidate")
        object.__setattr__(
            self,
            "selected_since",
            normalize_utc_datetime(self.selected_since, "selected_since"),
        )
        for field_name in ("acquisition_duration", "remaining_acquisition"):
            value = getattr(self, field_name)
            if not isinstance(value, timedelta):
                raise TypeError(f"{field_name} must be a timedelta")
            if value < timedelta(0):
                raise ValueError(f"{field_name} cannot be negative")
        if self.remaining_acquisition > self.acquisition_duration:
            raise ValueError(
                "remaining_acquisition cannot exceed acquisition_duration"
            )
        if not isinstance(self.operational, bool):
            raise TypeError("operational must be a bool")
        if self.operational and self.remaining_acquisition != timedelta(0):
            raise ValueError("operational lifecycle cannot have remaining acquisition")


@dataclass(frozen=True, slots=True)
class PlanningHistogramBin:
    """Retain one bounded label/count entry in detached planning evidence.

    Layer: public immutable Simulator planning-evidence value. It is public
    because ``Planner`` creates it and codecs plus detached inspection consume
    the exact compact histogram shape. It is not a physical candidate,
    per-candidate decision, semantic Plan event, or backend metric bucket.
    """

    label: str
    count: int

    def __post_init__(self) -> None:
        if not isinstance(self.label, str) or not self.label.strip():
            raise ValueError("histogram label must be a non-empty string")
        label = self.label.strip()
        if len(label) > 160:
            raise ValueError("histogram label must not exceed 160 characters")
        if (
            isinstance(self.count, bool)
            or not isinstance(self.count, int)
            or self.count < 0
        ):
            raise ValueError("histogram count must be a non-negative integer")
        object.__setattr__(self, "label", label)


@dataclass(frozen=True, slots=True)
class PlanningCandidateHistogram:
    """Retain one bounded candidate-quality or decision-reason histogram.

    Layer: public immutable Simulator planning-evidence value. It is public
    because ``Planner`` creates it and producers plus detached Tools consume
    deterministic aggregate candidate evidence without importing internals. It
    is not an unbounded candidate collection, Model selection result, or
    observability telemetry.
    """

    name: str
    sample_count: int
    bins: tuple[PlanningHistogramBin, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("histogram name must be a non-empty string")
        name = self.name.strip()
        if len(name) > 80:
            raise ValueError("histogram name must not exceed 80 characters")
        if (
            isinstance(self.sample_count, bool)
            or not isinstance(self.sample_count, int)
            or self.sample_count < 0
        ):
            raise ValueError("sample_count must be a non-negative integer")
        bins = tuple(self.bins)
        if len(bins) > 64 or any(
            not isinstance(value, PlanningHistogramBin) for value in bins
        ):
            raise ValueError(
                "histogram bins must contain at most 64 PlanningHistogramBin values"
            )
        ordered = tuple(sorted(bins, key=lambda value: value.label))
        if len({value.label for value in ordered}) != len(ordered):
            raise ValueError("histogram bins contain a duplicate label")
        if sum(value.count for value in ordered) != self.sample_count:
            raise ValueError("histogram bin counts must equal sample_count")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "bins", ordered)


@dataclass(frozen=True, slots=True)
class PlanningCandidateSummary:
    """Summarize feasible physical candidates with bounded aggregate evidence.

    Layer: public immutable Simulator planning-evidence value. It is public
    because ``Planner`` creates it and durable codecs plus detached Tools consume
    feasible work counts and stable quality/reason distributions. It is not
    candidate authority, a substitute for caller-supplied
    ``PhysicalLinkQueryResult`` facts, or permission to reconstruct rejected
    candidates.
    """

    feasible_count: int
    histograms: tuple[PlanningCandidateHistogram, ...]

    def __post_init__(self) -> None:
        if (
            isinstance(self.feasible_count, bool)
            or not isinstance(self.feasible_count, int)
            or self.feasible_count < 0
        ):
            raise ValueError("feasible_count must be a non-negative integer")
        histograms = tuple(self.histograms)
        if len(histograms) > 16 or any(
            not isinstance(value, PlanningCandidateHistogram)
            for value in histograms
        ):
            raise ValueError(
                "histograms must contain at most 16 PlanningCandidateHistogram values"
            )
        ordered = tuple(sorted(histograms, key=lambda value: value.name))
        if len({value.name for value in ordered}) != len(ordered):
            raise ValueError("candidate summary contains a duplicate histogram")
        object.__setattr__(self, "histograms", ordered)


@dataclass(frozen=True, slots=True)
class PlanningFrameEvidence:
    """Carry compact physical, selection, and work evidence for one boundary.

    Layer: public immutable Simulator planning-evidence value. It is public
    because the producer codec and detached Planner Tool share one validated
    per-transition evidence contract. It is not a PhysicalPositionSnapshot replacement,
    complete PlannedOperationalState, or unbounded candidate history.
    """

    timestamp: datetime
    inter_satellite_candidate_summary: PlanningCandidateSummary
    ground_station_access_candidate_summary: PlanningCandidateSummary
    zone_service_candidate_summary: PlanningCandidateSummary
    physical_link_work_counts: PhysicalLinkWorkCounts | None
    selected_inter_satellite_links: tuple[
        InterSatelliteLifecycleEvidence, ...
    ]
    selected_ground_station_accesses: tuple[
        GroundStationAccessCandidate, ...
    ]
    selected_zone_service_candidates: tuple[ZoneServiceCandidate, ...]
    inspection: PlanningInspection
    ground_station_preparations: tuple[GroundStationLifecycleEvidence, ...] = ()

    def __post_init__(self) -> None:
        timestamp = normalize_utc_datetime(self.timestamp, "timestamp")
        summaries = (
            self.inter_satellite_candidate_summary,
            self.ground_station_access_candidate_summary,
            self.zone_service_candidate_summary,
        )
        if any(
            not isinstance(value, PlanningCandidateSummary)
            for value in summaries
        ):
            raise TypeError(
                "candidate summaries must be PlanningCandidateSummary values"
            )
        if self.physical_link_work_counts is not None and not isinstance(self.physical_link_work_counts, PhysicalLinkWorkCounts):
            raise TypeError("physical_link_work_counts must be PhysicalLinkWorkCounts or None")
        selected_isls = _ordered_unique_evidence(
            self.selected_inter_satellite_links,
            InterSatelliteLifecycleEvidence,
            lambda value: (
                _node_key(value.candidate.endpoint_a),
                _node_key(value.candidate.endpoint_b),
            ),
            "selected_inter_satellite_links",
        )
        if len(selected_isls) > summaries[0].feasible_count:
            raise ValueError("selected ISL count cannot exceed feasible count")
        selected_ground = _ordered_unique_evidence(
            self.selected_ground_station_accesses,
            GroundStationAccessCandidate,
            lambda value: (
                _node_key(value.ground_station_id),
                _node_key(value.satellite_id),
            ),
            "selected_ground_station_accesses",
        )
        if len(selected_ground) > summaries[1].feasible_count:
            raise ValueError(
                "selected GroundStation count cannot exceed feasible count"
            )
        preparations = _ordered_unique_evidence(
            self.ground_station_preparations, GroundStationLifecycleEvidence,
            lambda value: (_node_key(value.candidate.ground_station_id),
                           _node_key(value.candidate.satellite_id)),
            "ground_station_preparations",
        )
        if len(preparations) > summaries[1].feasible_count:
            raise ValueError("GS preparations cannot exceed feasible candidates")
        if preparations and tuple(value.candidate for value in preparations if value.operational) != selected_ground:
            raise ValueError("operational GS preparation evidence must match selected accesses")
        object.__setattr__(self, "ground_station_preparations", preparations)
        selected_zones = _ordered_unique_evidence(
            self.selected_zone_service_candidates,
            ZoneServiceCandidate,
            lambda value: (value.zone_id, _node_key(value.satellite_id)),
            "selected_zone_service_candidates",
        )
        if len(selected_zones) > summaries[2].feasible_count:
            raise ValueError("selected Zone count cannot exceed feasible count")
        if not isinstance(self.inspection, PlanningInspection):
            raise TypeError("inspection must be a PlanningInspection")
        if self.inspection.timestamp != timestamp:
            raise ValueError("inspection timestamp must match frame evidence")
        if (
            self.inspection.feasible_isl_candidates
            != summaries[0].feasible_count
            or self.inspection.selected_isls != len(selected_isls)
            or self.inspection.acquiring_isls
            != sum(not value.operational for value in selected_isls)
            or self.inspection.operational_isls
            != sum(value.operational for value in selected_isls)
            or self.inspection.ground_station_accesses
            != len(selected_ground)
            or self.inspection.zones_served != len(selected_zones)
        ):
            raise ValueError(
                "inspection counts must match compact selected evidence"
            )
        object.__setattr__(self, "timestamp", timestamp)
        object.__setattr__(
            self, "selected_inter_satellite_links", selected_isls
        )
        object.__setattr__(
            self, "selected_ground_station_accesses", selected_ground
        )
        object.__setattr__(
            self, "selected_zone_service_candidates", selected_zones
        )


@dataclass(frozen=True, slots=True)
class PublishedPlanningEvidence:
    """Bind one published Plan to bounded detached planning evidence.

    Layer: public immutable Simulator planning-evidence value. It is public
    because the common producer, durable codec, and detached Tools consume the
    exact evidence for a published Plan. It is not Planner state ownership, a
    second transition truth, or a complete run history.
    """

    plan: Plan
    transition_fingerprints: tuple[str, ...]
    frames: tuple[PlanningFrameEvidence, ...]
    frontiers: PlanningFrontiers

    def __post_init__(self) -> None:
        if not isinstance(self.plan, Plan):
            raise TypeError("plan must be a Plan")
        fingerprints = tuple(self.transition_fingerprints)
        expected = tuple(
            plan_transition_fingerprint(value)
            for value in self.plan.transitions
        )
        if fingerprints != expected:
            raise ValueError(
                "transition_fingerprints must match exact Plan transitions"
            )
        frames = tuple(self.frames)
        if any(not isinstance(value, PlanningFrameEvidence) for value in frames):
            raise TypeError("frames must contain PlanningFrameEvidence values")
        if tuple(value.timestamp for value in frames) != tuple(
            value.timestamp for value in self.plan.transitions
        ):
            raise ValueError("frame timestamps must match Plan transitions")
        if not isinstance(self.frontiers, PlanningFrontiers):
            raise TypeError("frontiers must be PlanningFrontiers")
        if self.frontiers.publication != self.plan.end_timestamp:
            raise ValueError("evidence publication frontier must equal Plan end")
        object.__setattr__(self, "transition_fingerprints", fingerprints)
        object.__setattr__(self, "frames", frames)

    @property
    def inspections(self) -> tuple[PlanningInspection, ...]:
        return tuple(value.inspection for value in self.frames)


def _ordered_unique_evidence(
    values, expected_type, key, field_name: str
) -> tuple:
    try:
        copied = tuple(values)
    except TypeError as error:
        raise TypeError(f"{field_name} must be iterable") from error
    if any(not isinstance(value, expected_type) for value in copied):
        raise TypeError(f"{field_name} contains an invalid value")
    ordered = tuple(sorted(copied, key=key))
    identities = tuple(key(value) for value in ordered)
    if len(identities) != len(set(identities)):
        raise ValueError(f"{field_name} contains a duplicate identity")
    return ordered
