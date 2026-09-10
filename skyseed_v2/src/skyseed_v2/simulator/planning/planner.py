"""Authoritative incremental Network Planner and bounded Plan publisher."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from hashlib import sha256
from time import perf_counter

from skyseed_v2.shared import NodeId
from skyseed_v2.shared._execution_observation import _ExecutionObserver
from skyseed_v2.shared.time import normalize_utc_datetime

from ..physical import (
    GroundStationAccessCandidate,
    InterSatelliteLinkCandidate,
    PhysicalFrame,
    PhysicalLinkObservation,
    PhysicalLinkRequest,
    PhysicalLinkWorkCounts,
    PhysicalWorld,
    ZoneServiceCandidate,
)
from .._routing_core import (
    _prepare_routing_core_context,
    _required_ground_next_hops,
    _required_zone_next_hops,
    _route_step_allows_transit,
)
from .models import (
    ConnectionParameterObservation,
    CoordinationPolicy,
    GroundStationAccessPlanningModel,
    InterSatelliteSelection,
    InterSatelliteConnectionPlanningModel,
    ParameterPlanningMemory,
    ParameterPlanningModel,
    ParameterPlanningResult,
    PlanningWindowPolicy,
    RoutePlanningModel,
    RoutePlanningResult,
    FrameBatchPlanningWindowPolicy,
    ZoneServicePlanningModel,
    ZoneServicePlanningResult,
)
from .coordination import (
    _required_semantic_precedences,
    _validate_coordinated_precedences,
)
from .values import (
    CandidateDecision,
    CandidateDecisionKind,
    OperationalConnection,
    OperationalConnectionId,
    OperationalConnectionKind,
    Plan,
    PlanTransition,
    PlannedOperationalState,
    PlanningInspection,
    GroundStationRouteDemandTarget,
    ReachableRouteOutcome,
    RouteDemand,
    RouteOutcomeTransition,
    RouteUnreachableReason,
    SemanticEventPrecedence,
    UnreachableRouteOutcome,
    ZoneRouteDemandTarget,
    ZoneServiceAssignment,
    ZoneServiceTransition,
    _create_plan_with_output_state,
    plan_transition_fingerprint,
    planned_operational_state_fingerprint,
    semantic_event_references,
)


_IslPair = tuple[NodeId, NodeId]
_STAGES = (
    "physical-input-validation",
    "isl-candidate-selection",
    "acquisition/lifecycle-advancement",
    "ground-station-planning",
    "zone-planning",
    "parameter-planning",
    "route-planning",
    "coordination",
    "state-advancement",
)


@dataclass(frozen=True, slots=True)
class _ConnectionPreparation:
    candidate: InterSatelliteLinkCandidate | GroundStationAccessCandidate
    selected_since: datetime
    operational: bool


@dataclass(frozen=True, slots=True)
class _PrehistorySelectionEvidence:
    timestamp: datetime
    selected_pairs: tuple[_IslPair, ...]


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
    ``PhysicalLinkObservation`` facts, or permission to reconstruct rejected
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
    per-transition evidence contract. It is not a PhysicalFrame replacement,
    complete PlannedOperationalState, or unbounded candidate history.
    """

    timestamp: datetime
    inter_satellite_candidate_summary: PlanningCandidateSummary
    ground_station_access_candidate_summary: PlanningCandidateSummary
    zone_service_candidate_summary: PlanningCandidateSummary
    physical_link_work_counts: PhysicalLinkWorkCounts
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
        if not isinstance(self.physical_link_work_counts, PhysicalLinkWorkCounts):
            raise TypeError(
                "physical_link_work_counts must be PhysicalLinkWorkCounts"
            )
        if (
            summaries[0].feasible_count
            != self.physical_link_work_counts.inter_satellite_pairs_feasible
            or summaries[1].feasible_count
            != self.physical_link_work_counts.ground_station_pairs_feasible
            or summaries[2].feasible_count
            != self.physical_link_work_counts.zone_service_pairs_feasible
        ):
            raise ValueError(
                "candidate cardinalities must match physical work counts"
            )
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
            lambda value: value.zone_id,
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


@dataclass(frozen=True, slots=True)
class _FramePlanningResult:
    transition: PlanTransition
    precedences: tuple[SemanticEventPrecedence, ...]
    evidence: PlanningFrameEvidence

    @property
    def inspection(self) -> PlanningInspection:
        return self.evidence.inspection


class Planner:
    """Own semantic planning state and bounded windowed Plan publication.

    Layer: public Simulator operational-planning lifecycle owner. It is public
    because ``Simulator`` composition and the common realization producer need
    one authoritative Model-injected planning boundary. It is not a Timeline
    cursor, PhysicalWorld owner, Compiler, Runner, or wrapper around a second
    planning state machine.
    """

    __slots__ = (
        "_physical_world",
        "_physical_world_fingerprint",
        "_observation_interval",
        "_inter_satellite_model",
        "_ground_station_model",
        "_zone_service_model",
        "_parameter_model",
        "_route_model",
        "_coordination_policy",
        "_planning_window_policy",
        "_frames_per_batch",
        "_required_prehistory",
        "_execution_observer",
        "_lifecycle_by_pair",
        "_ground_lifecycle_by_pair",
        "_parameter_memory",
        "_route_demands",
        "_route_demands_dirty",
        "_physical_observation_frontier",
        "_planning_frontier",
        "_publication_frontier",
        "_expected_formal_start",
        "_formal_started",
        "_finished_end",
        "_shutdown",
        "_current_state",
        "_last_published_state",
        "_last_published_plan_id",
        "_last_transition",
        "_last_inspection",
        "_last_route_demands",
        "_route_recomputation_count",
        "_unpublished",
        "_published_evidence",
        "_maximum_retained_evidence",
    )

    def __init__(
        self,
        *,
        physical_world: PhysicalWorld,
        observation_interval: timedelta,
        inter_satellite_model: InterSatelliteConnectionPlanningModel,
        ground_station_model: GroundStationAccessPlanningModel,
        zone_service_model: ZoneServicePlanningModel,
        parameter_model: ParameterPlanningModel,
        route_model: RoutePlanningModel,
        coordination_policy: CoordinationPolicy,
        route_demands: Iterable[RouteDemand] = (),
        planning_window_policy: PlanningWindowPolicy = (
            FrameBatchPlanningWindowPolicy()
        ),
        execution_observer: _ExecutionObserver | None = None,
    ) -> None:
        if not isinstance(physical_world, PhysicalWorld):
            raise TypeError("physical_world must be a PhysicalWorld")
        if not isinstance(observation_interval, timedelta):
            raise TypeError("observation_interval must be a timedelta")
        if observation_interval <= timedelta(0):
            raise ValueError("observation_interval must be positive")
        _require_methods(
            inter_satellite_model,
            "inter_satellite_model",
            (
                "create_inter_satellite_candidate_pairs",
                "select_inter_satellite_links",
            ),
        )
        acquisition_duration = getattr(
            inter_satellite_model, "acquisition_duration", None
        )
        if not isinstance(acquisition_duration, timedelta):
            raise TypeError(
                "inter_satellite_model must expose timedelta acquisition_duration"
            )
        if acquisition_duration.total_seconds() < 0.0:
            raise ValueError("acquisition_duration must be non-negative")
        _require_methods(
            ground_station_model,
            "ground_station_model",
            ("select_ground_station_accesses",),
        )
        _require_methods(
            zone_service_model,
            "zone_service_model",
            ("assign_zone_service",),
        )
        _require_methods(
            parameter_model,
            "parameter_model",
            ("plan_effective_parameters",),
        )
        _require_methods(
            route_model,
            "route_model",
            ("plan_route_outcomes", "shutdown"),
        )
        _require_methods(
            coordination_policy, "coordination_policy", ("coordinate",)
        )
        _require_methods(
            planning_window_policy,
            "planning_window_policy",
            ("validate_observation_interval",),
        )
        planning_window_policy.validate_observation_interval(
            observation_interval
        )
        frames_per_batch = getattr(planning_window_policy, "frames_per_batch", None)
        if (isinstance(frames_per_batch, bool)
                or not isinstance(frames_per_batch, int) or frames_per_batch <= 0):
            raise ValueError("frames_per_batch must be a positive integer")
        preparation = max(acquisition_duration, getattr(
            ground_station_model, "acquisition_duration", timedelta(0)))
        # Round the actual preparation dependency up to formal samples.
        required_prehistory = observation_interval * (
            (preparation + observation_interval - timedelta.resolution)
            // observation_interval
        )
        if execution_observer is not None and not isinstance(
            execution_observer, _ExecutionObserver
        ):
            raise TypeError(
                "execution_observer must be an _ExecutionObserver or None"
            )
        self._physical_world = physical_world
        self._physical_world_fingerprint = (
            physical_world.semantic_fingerprint()
        )
        self._observation_interval = observation_interval
        self._inter_satellite_model = inter_satellite_model
        self._ground_station_model = ground_station_model
        self._zone_service_model = zone_service_model
        self._parameter_model = parameter_model
        self._route_model = route_model
        self._coordination_policy = coordination_policy
        self._planning_window_policy = planning_window_policy
        self._frames_per_batch = frames_per_batch
        self._required_prehistory = required_prehistory
        self._execution_observer = execution_observer
        self._lifecycle_by_pair: dict[
            _IslPair, _ConnectionPreparation
        ] = {}
        self._ground_lifecycle_by_pair = {}
        self._parameter_memory: tuple[ParameterPlanningMemory, ...] = ()
        self._route_demands = _validated_route_demands(route_demands)
        self._route_demands_dirty = True
        self._physical_observation_frontier: datetime | None = None
        self._planning_frontier: datetime | None = None
        self._publication_frontier: datetime | None = None
        self._expected_formal_start: datetime | None = None
        self._formal_started = False
        self._finished_end: datetime | None = None
        self._shutdown = False
        self._current_state: PlannedOperationalState | None = None
        self._last_published_state: PlannedOperationalState | None = None
        self._last_published_plan_id: str | None = None
        self._last_transition: PlanTransition | None = None
        self._last_inspection: PlanningInspection | None = None
        self._last_route_demands: tuple[RouteDemand, ...] = ()
        self._route_recomputation_count = 0
        self._unpublished: list[_FramePlanningResult] = []
        self._published_evidence: list[PublishedPlanningEvidence] = []
        self._maximum_retained_evidence = 2

    def _set_execution_observer(self, observer: _ExecutionObserver) -> None:
        if not isinstance(observer, _ExecutionObserver):
            raise TypeError("observer must be an _ExecutionObserver")
        if self._execution_observer is not None:
            raise RuntimeError("Network Planner observation is already enabled")
        self._execution_observer = observer

    def _get_execution_observer(self) -> _ExecutionObserver | None:
        """Return the private recorder used by the common producer."""

        return self._execution_observer

    def _producer_input_contract(self) -> tuple[str, timedelta]:
        """Return exact immutable provenance required by the producer."""

        return self._physical_world_fingerprint, self._observation_interval

    def _physical_prediction_requirements(self):
        """Derive per-kind physical tails from preparation and useful service."""
        def required(model):
            preparation = getattr(model, 'acquisition_duration', timedelta(0))
            rounded = self._observation_interval * (
                (preparation + self._observation_interval - timedelta.resolution)
                // self._observation_interval)
            return rounded + getattr(model, 'minimum_service_duration', timedelta(0))
        return required(self._inter_satellite_model), required(self._ground_station_model)

    def _admissible_candidates(self, timestamp, candidates, lifecycle, model, key):
        preparation = getattr(model, 'acquisition_duration', timedelta(0))
        service = getattr(model, 'minimum_service_duration', timedelta(0))
        rounded = self._observation_interval * (
            (preparation + self._observation_interval - timedelta.resolution)
            // self._observation_interval)
        accepted = []
        for candidate in candidates:
            prior = lifecycle.get(key(candidate))
            if prior is not None and prior.operational:
                accepted.append(candidate)
                continue
            start = timestamp if prior is None else prior.selected_since
            end = start + rounded + service
            if (end <= timestamp or (candidate.contact_window is not None
                    and candidate.contact_window.covers(timestamp, end))):
                accepted.append(candidate)
        return tuple(accepted)

    def create_physical_link_request(self) -> PhysicalLinkRequest:
        pairs = self._inter_satellite_model.create_inter_satellite_candidate_pairs(
            self._physical_world
        )
        if not isinstance(pairs, tuple):
            raise TypeError("ISL model must return a tuple of candidate pairs")
        canonical_pairs = []
        seen = set()
        for pair in pairs:
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise TypeError("ISL model candidate pairs must be two-item tuples")
            first, second = pair
            self._physical_world.get_satellite(first)
            self._physical_world.get_satellite(second)
            if first == second:
                raise ValueError("ISL model returned a self-link candidate pair")
            canonical = _canonical_pair(first, second)
            if pair != canonical:
                raise ValueError("ISL model candidate pairs must be canonical")
            if canonical in seen:
                raise ValueError("ISL model returned a duplicate candidate pair")
            seen.add(canonical)
            canonical_pairs.append(canonical)
        return PhysicalLinkRequest(
            inter_satellite_pairs=tuple(canonical_pairs),
            include_ground_station_access=True,
            include_zone_service=True,
        )

    def _candidate_search_request(self, base):
        # Lifecycle belongs to Planner; both pending and operational pairs
        # remain in every physical query irrespective of nearest rank.
        isl_tail, ground_tail = self._physical_prediction_requirements()
        model = self._ground_station_model
        limit = getattr(model, "initial_candidate_count", None)
        if hasattr(model, "_search_hint"):
            model._prepare_static_limits(self._physical_world)
            dependency = limit, isl_tail, ground_tail, base
            if model._search_hint is not None and model._search_hint[0] == dependency:
                limit = model._search_hint[1]
        return replace(base,
            ground_station_minimum_contact_duration=ground_tail,
            inter_satellite_minimum_contact_duration=isl_tail,
            ground_station_candidate_limit=limit,
            retained_ground_station_pairs=tuple(self._ground_lifecycle_by_pair),
            cross_plane_neighbor_count=getattr(getattr(self._inter_satellite_model, "_configuration", None), "cross_plane_neighbor_count", None),
            retained_inter_satellite_pairs=tuple(self._lifecycle_by_pair))

    def _expanded_candidate_request(self, request, observation):
        limit = request.ground_station_candidate_limit
        model = self._ground_station_model
        if (limit is None or not observation.work_counts.ground_station_pairs_not_returned
                or not hasattr(model, "_station_limit_by_id")):
            return None
        candidates = self._admissible_candidates(observation.timestamp,
            observation.ground_station_access_candidates, self._ground_lifecycle_by_pair,
            model, lambda c: (c.ground_station_id, c.satellite_id))
        if not model._candidate_search_needs_expansion(self._physical_world, candidates,
                observation.ground_station_access_candidates, limit):
            counts = {}
            for candidate in observation.ground_station_access_candidates:
                counts[candidate.ground_station_id] = counts.get(candidate.ground_station_id, 0) + 1
            if all(count < limit for count in counts.values()):
                isl_tail, ground_tail = self._physical_prediction_requirements()
                base = replace(request, ground_station_minimum_contact_duration=timedelta(0),
                    inter_satellite_minimum_contact_duration=timedelta(0), ground_station_candidate_limit=None,
                    retained_ground_station_pairs=(), cross_plane_neighbor_count=None,
                    retained_inter_satellite_pairs=())
                model._search_hint = (model.initial_candidate_count, isl_tail, ground_tail, base), limit
            return None
        total = len(model._satellite_limits)
        return replace(request, ground_station_candidate_limit=limit * 2 if limit * 2 < total else None)

    def set_route_demands(self, demands: Iterable[RouteDemand]) -> None:
        if self._finished_end is not None or self._shutdown:
            raise RuntimeError("cannot change Route demands after completion")
        values = _validated_route_demands(demands)
        if values != self._route_demands:
            self._route_demands = values
            self._route_demands_dirty = True

    @property
    def required_prehistory(self) -> timedelta:
        """Return the bounded warm-start duration needed by the producer."""

        return self._required_prehistory

    def prime(
        self,
        physical_frame: PhysicalFrame,
        observation: PhysicalLinkObservation,
        *,
        formal_start: datetime,
    ) -> None:
        """Advance one ordered bounded prehistory frame without a transition."""

        if self._formal_started or self._finished_end is not None:
            raise RuntimeError("prehistory must precede formal observation")
        if self._shutdown:
            raise RuntimeError("Planner is shut down")
        if not isinstance(physical_frame, PhysicalFrame):
            raise TypeError("physical_frame must be a PhysicalFrame")
        start = normalize_utc_datetime(formal_start, "formal_start")
        if (
            self._expected_formal_start is not None
            and start != self._expected_formal_start
        ):
            raise ValueError("formal_start must remain identical across prime calls")
        if physical_frame.timestamp >= start:
            raise ValueError("prehistory timestamp must precede formal_start")
        if start - physical_frame.timestamp > self._required_prehistory:
            raise ValueError("prehistory exceeds the bounded initialization window")
        if (
            self._physical_observation_frontier is not None
            and physical_frame.timestamp
            != self._physical_observation_frontier + self._observation_interval
        ):
            raise ValueError(
                "prehistory frames must follow observation_interval"
            )
        snapshot = self._snapshot_private_state()
        try:
            self._expected_formal_start = start
            result = self._advance(
                physical_frame,
                observation,
                publish=False,
                phase="bootstrap",
            )
            assert isinstance(result, _PrehistorySelectionEvidence)
        except Exception:
            self._restore_private_state(snapshot)
            raise
        return None

    def observe(
        self,
        physical_frame: PhysicalFrame,
        observation: PhysicalLinkObservation,
    ) -> tuple[Plan, ...]:
        """Plan one formal frame and return every newly publishable Plan."""

        if self._finished_end is not None:
            raise RuntimeError("Planner is already finished")
        if self._shutdown:
            raise RuntimeError("Planner is shut down")
        if not isinstance(physical_frame, PhysicalFrame):
            raise TypeError("physical_frame must be a PhysicalFrame")
        snapshot = self._snapshot_private_state()
        try:
            if not self._formal_started:
                if self._expected_formal_start is None:
                    self._expected_formal_start = physical_frame.timestamp
                elif physical_frame.timestamp != self._expected_formal_start:
                    raise ValueError(
                        "first formal frame must equal the declared formal_start"
                    )
                if (
                    self._physical_observation_frontier is not None
                    and physical_frame.timestamp
                    != self._physical_observation_frontier
                    + self._observation_interval
                ):
                    raise ValueError(
                        "first formal frame must follow prehistory by "
                        "observation_interval"
                    )
            elif (
                self._planning_frontier is None
                or physical_frame.timestamp
                != self._planning_frontier + self._observation_interval
            ):
                raise ValueError(
                    "formal frames must follow observation_interval"
                )
            result = self._advance(
                physical_frame, observation, publish=True, phase="steady"
            )
            assert isinstance(result, _FramePlanningResult)
            self._formal_started = True
            self._last_transition = result.transition
            self._last_inspection = result.inspection
            self._unpublished.append(result)
            return self._publish_ready()
        except Exception:
            self._restore_private_state(snapshot)
            raise

    def flush(self) -> tuple[Plan, ...]:
        """Publish the observed prefix without ending acquisition or retention.

        A delivered coverage seam may cut an internal publication batch. No
        observation, contact ending, state reset or synthetic event is added.
        """
        if self._shutdown or self._finished_end is not None:
            raise RuntimeError("Planner is closed for publication")
        snapshot = self._snapshot_private_state()
        try:
            plans = []
            while self._unpublished:
                plans.append(self._publish_one_prefix(phase="steady"))
            return tuple(plans)
        except Exception:
            self._restore_private_state(snapshot)
            raise

    def finish(self, *, end_timestamp: datetime) -> tuple[Plan, ...]:
        """Verify Timeline completion and flush every remaining prefix once."""

        end = normalize_utc_datetime(end_timestamp, "end_timestamp")
        if self._shutdown:
            raise RuntimeError("Planner is shut down")
        if self._finished_end is not None:
            if end != self._finished_end:
                raise ValueError("finished Planner has a different end timestamp")
            return ()
        if not self._formal_started or self._planning_frontier is None:
            raise RuntimeError("Planner has no formal observations to finish")
        if self._planning_frontier != end:
            raise ValueError("end_timestamp must equal the planning frontier")
        snapshot = self._snapshot_private_state()
        try:
            plans: list[Plan] = []
            while self._unpublished:
                plans.append(self._publish_one_prefix(phase="one-shot"))
            if self._publication_frontier != end:
                raise RuntimeError("publication did not reach Timeline end")
            self._finished_end = end
            return tuple(plans)
        except Exception:
            self._restore_private_state(snapshot)
            raise

    def get_state(self) -> PlannedOperationalState:
        if self._current_state is None:
            raise RuntimeError("Planner has not observed a formal frame")
        return self._current_state

    def get_last_transition(self) -> PlanTransition:
        if self._last_transition is None:
            raise RuntimeError("Planner has not observed a formal transition")
        return self._last_transition

    def get_state_fingerprint(self) -> str:
        return planned_operational_state_fingerprint(self.get_state())

    def get_last_transition_fingerprint(self) -> str:
        return plan_transition_fingerprint(self.get_last_transition())

    def get_frontiers(self) -> PlanningFrontiers:
        return PlanningFrontiers(
            physical_observation=self._physical_observation_frontier,
            planning=self._planning_frontier,
            publication=self._publication_frontier,
        )

    def get_route_recomputation_count(self) -> int:
        return self._route_recomputation_count

    def get_published_evidence(
        self, plan_id: str
    ) -> PublishedPlanningEvidence:
        for evidence in self._published_evidence:
            if evidence.plan.plan_id == plan_id:
                return evidence
        raise KeyError(f"published Plan evidence not retained: {plan_id}")

    def release_published_through(self, plan_id: str) -> dict[str, int]:
        if not isinstance(plan_id, str) or not plan_id:
            raise ValueError("plan_id must be a non-empty string")
        for index, evidence in enumerate(self._published_evidence):
            if evidence.plan.plan_id == plan_id:
                released = self._published_evidence[: index + 1]
                del self._published_evidence[: index + 1]
                return {
                    "plans": len(released),
                    "transitions": sum(
                        len(value.plan.transitions) for value in released
                    ),
                    "inspections": sum(
                        len(value.inspections) for value in released
                    ),
                }
        raise KeyError(f"published Plan evidence not retained: {plan_id}")

    def shutdown(self, *, wait: bool = True) -> None:
        if not isinstance(wait, bool):
            raise TypeError("wait must be a bool")
        if self._shutdown:
            return
        self._route_model.shutdown(wait=wait)
        self._shutdown = True

    def _publish_ready(self) -> tuple[Plan, ...]:
        plans: list[Plan] = []
        while len(self._unpublished) >= self._frames_per_batch:
            plans.append(self._publish_one_prefix(phase="steady"))
        return tuple(plans)

    def _publish_one_prefix(self, *, phase: str) -> Plan:
        publication_started = perf_counter()
        if not self._unpublished:
            raise RuntimeError("no unpublished transition is available")
        if len(self._published_evidence) >= self._maximum_retained_evidence:
            raise RuntimeError(
                "published Planner evidence must be released before progression"
            )
        count = min(len(self._unpublished), self._frames_per_batch)
        prefix = tuple(self._unpublished[:count])
        transitions = tuple(value.transition for value in prefix)
        precedences = tuple(
            precedence
            for value in prefix
            for precedence in value.precedences
        )
        plan_started = perf_counter()
        plan, published_state = _create_plan_with_output_state(
            self._last_published_state,
            previous_plan_id=self._last_published_plan_id,
            physical_world_fingerprint=self._physical_world_fingerprint,
            observation_interval=self._observation_interval,
            start_timestamp=transitions[0].timestamp,
            transitions=transitions,
            semantic_event_precedences=precedences,
            _constructed_state=self._current_state,
        )
        plan_scope = {
            "plan_id": plan.plan_id,
            "previous_plan_id": plan.previous_plan_id,
            "start_timestamp": plan.start_timestamp.isoformat(),
            "end_timestamp": plan.end_timestamp.isoformat(),
        }
        self._record(
            "plan-creation",
            phase,
            plan_started,
            plan_scope,
            {
                "plans": 1,
                "transitions": len(plan.transitions),
                "semantic_precedences": len(
                    plan.semantic_event_precedences
                ),
            },
        )
        reuse_started = perf_counter()
        self._record(
            "publication-output-state-reuse",
            phase,
            reuse_started,
            plan_scope,
            {"replayed_transitions_avoided": len(plan.transitions)},
        )
        self._last_published_state = published_state
        self._last_published_plan_id = plan.plan_id
        self._publication_frontier = plan.end_timestamp
        del self._unpublished[:count]
        frontiers = self.get_frontiers()
        evidence = PublishedPlanningEvidence(
            plan=plan,
            transition_fingerprints=tuple(
                plan_transition_fingerprint(value) for value in transitions
            ),
            frames=tuple(value.evidence for value in prefix),
            frontiers=frontiers,
        )
        self._published_evidence.append(evidence)
        self._record(
            "publication-total",
            phase,
            publication_started,
            plan_scope,
            {
                "published": 1,
                "transitions": len(plan.transitions),
                "semantic_precedences": len(
                    plan.semantic_event_precedences
                ),
            },
            duration_role="rollup",
        )
        return plan

    def _snapshot_private_state(self) -> tuple:
        return (
            dict(self._lifecycle_by_pair),
            dict(self._ground_lifecycle_by_pair),
            self._parameter_memory,
            self._route_demands_dirty,
            self._physical_observation_frontier,
            self._planning_frontier,
            self._publication_frontier,
            self._expected_formal_start,
            self._formal_started,
            self._finished_end,
            self._current_state,
            self._last_published_state,
            self._last_published_plan_id,
            self._last_transition,
            self._last_inspection,
            self._last_route_demands,
            self._route_recomputation_count,
            list(self._unpublished),
            list(self._published_evidence),
        )

    def _restore_private_state(self, snapshot: tuple) -> None:
        (
            lifecycle,
            self._ground_lifecycle_by_pair,
            self._parameter_memory,
            self._route_demands_dirty,
            self._physical_observation_frontier,
            self._planning_frontier,
            self._publication_frontier,
            self._expected_formal_start,
            self._formal_started,
            self._finished_end,
            self._current_state,
            self._last_published_state,
            self._last_published_plan_id,
            self._last_transition,
            self._last_inspection,
            self._last_route_demands,
            self._route_recomputation_count,
            unpublished,
            published_evidence,
        ) = snapshot
        self._lifecycle_by_pair = lifecycle
        self._unpublished = unpublished
        self._published_evidence = published_evidence

    def _advance(
        self,
        physical_frame: PhysicalFrame,
        observation: PhysicalLinkObservation,
        *,
        publish: bool,
        phase: str,
    ) -> _FramePlanningResult | _PrehistorySelectionEvidence:
        scope: dict[str, object] = {}
        total_started = perf_counter()
        stage_started = perf_counter()
        timestamp = _validate_physical_inputs(
            self._physical_world,
            self._physical_observation_frontier,
            physical_frame,
            observation,
        )
        scope["timestamp"] = timestamp.isoformat()
        self._physical_observation_frontier = timestamp
        self._record(
            "physical-input-validation",
            phase,
            stage_started,
            scope,
            {
                "feasible_isl_candidates": len(
                    observation.inter_satellite_candidates
                ),
                "feasible_ground_station_candidates": len(
                    observation.ground_station_access_candidates
                ),
                "feasible_zone_candidates": len(
                    observation.zone_service_candidates
                ),
                "physical_candidate_pairs_evaluated": (
                    observation.work_counts.inter_satellite_pairs_evaluated
                    + observation.work_counts.ground_station_pairs_evaluated
                    + observation.work_counts.zone_service_pairs_evaluated
                ),
            },
        )

        stage_started = perf_counter()
        retained_pairs = frozenset(self._lifecycle_by_pair)
        observed_isl = {_candidate_pair(c): c for c in observation.inter_satellite_candidates}
        admissible_isl = self._admissible_candidates(
            timestamp, observation.inter_satellite_candidates,
            self._lifecycle_by_pair, self._inter_satellite_model, _candidate_pair)
        selection = (
            self._inter_satellite_model.select_inter_satellite_links(
                self._physical_world,
                admissible_isl,
                retained_pairs,
            )
        )
        admitted_pairs = {_candidate_pair(c) for c in admissible_isl}
        excluded = tuple(CandidateDecision(
            CandidateDecisionKind.REJECTED,
            'future physical service after preparation is not established',
            c.endpoint_a, c.endpoint_b)
            for c in observation.inter_satellite_candidates
            if _candidate_pair(c) not in admitted_pairs)
        if excluded:
            selection = InterSatelliteSelection(selection.selected_candidates,
                selection.decisions + excluded, selection.underfilled_slots)
        selected_candidates = _validate_inter_satellite_selection(
            self._physical_world,
            selection,
            observation.inter_satellite_candidates,
            retained_pairs,
            authoritative=observed_isl,
        )
        feasible_pairs = observed_isl.keys()
        selected_pairs = {
            _candidate_pair(value) for value in selected_candidates
        }
        self._record(
            "isl-candidate-selection",
            phase,
            stage_started,
            scope,
            {
                "feasible_candidates": len(feasible_pairs),
                "selected_candidates": len(selected_pairs),
                "rejected_candidates": sum(
                    value.decision is CandidateDecisionKind.REJECTED
                    for value in selection.decisions
                ),
                "underfilled_slots": selection.underfilled_slots,
                **getattr(self._inter_satellite_model, "_selection_work", {}),
            },
        )

        stage_started = perf_counter()
        previous_lifecycle = self._lifecycle_by_pair
        (
            lifecycle,
            acquisition_started,
            acquisition_completed,
            acquisition_reset,
        ) = self._advance_isl_lifecycle(
            timestamp, selected_candidates
        )
        _validate_lifecycle_transition(
            previous_lifecycle,
            lifecycle,
            selected_candidates,
            timestamp,
            self._inter_satellite_model.acquisition_duration,
        )
        self._lifecycle_by_pair = lifecycle
        operational_isl_candidates = tuple(
            value.candidate
            for value in lifecycle.values()
            if value.operational
        )
        acquiring_count = sum(
            not value.operational for value in lifecycle.values()
        )
        self._record(
            "acquisition/lifecycle-advancement",
            phase,
            stage_started,
            scope,
            {
                "selected_isls": len(lifecycle),
                "acquiring_isls": acquiring_count,
                "operational_isls": len(operational_isl_candidates),
                "acquisition_started": acquisition_started,
                "acquisition_completed": acquisition_completed,
                "acquisition_reset": acquisition_reset,
            },
            resources={"lifecycle_entries": len(lifecycle)},
        )

        ground_station_started = perf_counter()
        stage_started = ground_station_started
        admissible_ground = self._admissible_candidates(
            timestamp, observation.ground_station_access_candidates,
            self._ground_lifecycle_by_pair, self._ground_station_model,
            lambda c: (c.ground_station_id, c.satellite_id))
        proposed_ground = (
            self._ground_station_model.select_ground_station_accesses(
                self._physical_world,
                admissible_ground,
                frozenset(self._ground_lifecycle_by_pair),
            )
        )
        self._record(
            "ground-station-selection-and-ranking",
            phase,
            stage_started,
            scope,
            {
                "feasible_candidates": len(
                    observation.ground_station_access_candidates
                ),
                "proposed_accesses": (
                    len(proposed_ground)
                    if isinstance(proposed_ground, tuple)
                    else 0
                ),
                **getattr(self._ground_station_model, "_allocation_work", {}),
            },
        )
        stage_started = perf_counter()
        selected_ground, ground_cardinality = _validate_selected_ground_candidates(
            self._physical_world,
            proposed_ground,
            observation.ground_station_access_candidates,
        )
        self._record(
            "ground-station-validation",
            phase,
            stage_started,
            scope,
            ground_cardinality,
        )
        previous_ground = self._ground_lifecycle_by_pair
        duration = getattr(self._ground_station_model, 'acquisition_duration', timedelta(0))
        next_ground = {}
        for candidate in selected_ground:
            key = (candidate.ground_station_id, candidate.satellite_id)
            prior = previous_ground.get(key)
            since = timestamp if prior is None else prior.selected_since
            # Selected preparations consume the same endpoint capacities as
            # operational links. Reassignment releases before acquiring anew.
            next_ground[key] = _ConnectionPreparation(
                candidate, since, timestamp-since >= duration)
        self._ground_lifecycle_by_pair = next_ground
        selected_ground = tuple(v.candidate for v in next_ground.values() if v.operational)
        self._record(
            "ground-station-planning",
            phase,
            ground_station_started,
            scope,
            {
                "feasible_candidates": len(
                    observation.ground_station_access_candidates
                ),
                "operational_accesses": len(selected_ground),
            },
            duration_role="rollup",
        )

        if not publish:
            result = _PrehistorySelectionEvidence(
                timestamp,
                tuple(sorted(selected_pairs, key=_pair_key)),
            )
            self._record(
                "planning-total",
                phase,
                total_started,
                scope,
                {
                    "formal_transitions": 0,
                    "prehistory_frames": 1,
                    "selected_isls": len(lifecycle),
                    "operational_isls": len(operational_isl_candidates),
                },
                duration_role="rollup",
            )
            return result

        stage_started = perf_counter()
        previous_state = self._current_state
        previous_zone_assignments = (
            ()
            if previous_state is None
            else previous_state.zone_service_assignments
        )
        zone_result = self._zone_service_model.assign_zone_service(
            self._physical_world,
            observation.zone_service_candidates,
            previous_zone_assignments,
        )
        _validate_zone_result(
            self._physical_world,
            observation.zone_service_candidates,
            previous_zone_assignments,
            zone_result,
        )
        zone_assignments = zone_result.assignments
        served_count = sum(
            value.satellite_id is not None for value in zone_assignments
        )
        self._record(
            "zone-planning",
            phase,
            stage_started,
            scope,
            {
                "feasible_candidates": len(
                    observation.zone_service_candidates
                ),
                "zones_served": served_count,
                "zones_unserved": len(zone_assignments) - served_count,
            },
        )

        stage_started = perf_counter()
        parameter_observations = _parameter_observations(
            operational_isl_candidates, selected_ground
        )
        parameter_result = self._parameter_model.plan_effective_parameters(
            timestamp, parameter_observations, self._parameter_memory
        )
        _validate_parameter_result(
            timestamp,
            parameter_observations,
            previous_state,
            parameter_result,
        )
        transition = PlanTransition(
            timestamp=timestamp,
            established_connections=(
                parameter_result.established_connections
            ),
            withdrawn_connection_ids=(
                parameter_result.withdrawn_connection_ids
            ),
            parameter_updated_connections=(
                parameter_result.parameter_updated_connections
            ),
            zone_service_transitions=zone_result.transitions,
        )
        parameter_updates = len(
            transition.parameter_updated_connections
        )
        self._record(
            "parameter-planning",
            phase,
            stage_started,
            scope,
            {
                "operational_connections": len(parameter_result.connections),
                "parameter_updates": parameter_updates,
            },
            resources={"parameter_memory_entries": len(parameter_result.memory)},
        )

        stage_started = perf_counter()
        route_relevant = transition.route_relevant or self._route_demands_dirty
        if previous_state is None or route_relevant:
            active_route_demands = self._route_demands
            demand_factory = getattr(
                self._route_model, "create_route_demands", None
            )
            if not active_route_demands and callable(demand_factory):
                active_route_demands = _validated_route_demands(
                    demand_factory(self._physical_world, zone_assignments)
                )
            route_result = self._route_model.plan_route_outcomes(
                self._physical_world,
                parameter_result.connections,
                zone_assignments,
                active_route_demands,
                ()
                if previous_state is None
                else previous_state.route_outcomes,
            )
            previous_route_outcomes = (
                () if previous_state is None else previous_state.route_outcomes
            )
            validated_outcomes = _validate_route_result(
                previous_route_outcomes,
                active_route_demands,
                route_result,
            )
            route_outcomes = route_result.outcomes
            _validate_route_outcomes(
                self._physical_world,
                parameter_result.connections,
                zone_assignments,
                active_route_demands,
                route_outcomes,
                by_demand=validated_outcomes,
            )
            self._route_recomputation_count += 1
            route_recomputed = True
            self._route_demands_dirty = False
            route_changes = route_result.transitions
        else:
            route_outcomes = previous_state.route_outcomes
            active_route_demands = tuple(
                value.demand for value in route_outcomes
            )
            route_recomputed = False
            route_changes = ()
        transition = transition.with_route_outcome_transitions(route_changes)
        reachable_count = sum(
            isinstance(value, ReachableRouteOutcome)
            for value in route_outcomes
        )
        self._record(
            "route-planning",
            phase,
            stage_started,
            scope,
            {
                "route_demands": len(active_route_demands),
                "route_recomputed": int(route_recomputed),
                **(getattr(self._route_model, "_route_work_counts", lambda: {})()
                   if route_recomputed else {}),
                "reachable_routes": reachable_count,
                "unreachable_routes": len(route_outcomes) - reachable_count,
            },
            resources={
                "route_outcome_entries": len(route_outcomes),
                "route_recomputation_count": self._route_recomputation_count,
            },
        )

        authoritative_state = PlannedOperationalState(
            timestamp,
            parameter_result.connections,
            zone_assignments,
            route_outcomes,
        )
        stage_started = perf_counter()
        canonical_events = semantic_event_references(transition)
        required_precedences = _required_semantic_precedences(
            transition, canonical_events
        )
        proposed_precedences = self._coordination_policy.coordinate(
            transition, canonical_events, required_precedences
        )
        precedences = _validate_coordinated_precedences(
            transition,
            canonical_events,
            required_precedences,
            proposed_precedences,
        )
        self._record(
            "coordination",
            phase,
            stage_started,
            scope,
            {
                "semantic_events": len(canonical_events),
                "semantic_precedences": len(precedences),
            },
        )

        stage_started = perf_counter()
        self._current_state = authoritative_state
        self._parameter_memory = parameter_result.memory
        self._last_transition = transition
        self._last_route_demands = active_route_demands
        self._planning_frontier = timestamp
        self._record(
            "state-advancement",
            phase,
            stage_started,
            scope,
            {
                "operational_connections": len(
                    authoritative_state.operational_connections
                ),
                "zone_assignments": len(
                    authoritative_state.zone_service_assignments
                ),
                "route_outcomes": len(authoritative_state.route_outcomes),
            },
            resources={
                "selected_isl_entries": len(self._lifecycle_by_pair),
                "parameter_memory_entries": len(self._parameter_memory),
            },
        )

        rejected_count = sum(
            value.decision is CandidateDecisionKind.REJECTED
            for value in selection.decisions
        )
        inspection = PlanningInspection(
            timestamp=timestamp,
            feasible_isl_candidates=len(feasible_pairs),
            selected_isls=len(lifecycle),
            acquiring_isls=acquiring_count,
            operational_isls=len(operational_isl_candidates),
            rejected_isls=rejected_count,
            underfilled_isl_slots=selection.underfilled_slots,
            ground_station_accesses=len(selected_ground),
            zones_served=served_count,
            zones_unserved=len(zone_assignments) - served_count,
            routes_reachable=reachable_count,
            routes_unreachable=len(route_outcomes) - reachable_count,
            route_recomputed=route_recomputed,
            acquisition_started=acquisition_started,
            acquisition_completed=acquisition_completed,
            acquisition_reset=acquisition_reset,
            stages=_STAGES,
        )
        lifecycle_evidence = tuple(
            InterSatelliteLifecycleEvidence(
                candidate=value.candidate,
                selected_since=value.selected_since,
                acquisition_duration=(
                    self._inter_satellite_model.acquisition_duration
                ),
                remaining_acquisition=(
                    timedelta(0)
                    if value.operational
                    else max(
                        timedelta(0),
                        self._inter_satellite_model.acquisition_duration
                        - (timestamp - value.selected_since),
                    )
                ),
                operational=value.operational,
            )
            for _, value in sorted(
                lifecycle.items(), key=lambda item: _pair_key(item[0])
            )
        )
        stage_started = perf_counter()
        ground_candidate_summary = _ground_station_candidate_summary(
            observation.ground_station_access_candidates
        )
        self._record(
            "ground-station-evidence-projection",
            phase,
            stage_started,
            scope,
            {
                "candidate_entries_projected": len(
                    observation.ground_station_access_candidates
                ),
                "histograms": len(ground_candidate_summary.histograms),
            },
        )
        zone_candidates_by_identity = {
            (value.zone_id, value.satellite_id): value
            for value in observation.zone_service_candidates
        }
        selected_zone = tuple(
            zone_candidates_by_identity[
                (assignment.zone_id, assignment.satellite_id)
            ]
            for assignment in zone_assignments
            if assignment.satellite_id is not None
        )
        evidence = PlanningFrameEvidence(
            timestamp=timestamp,
            inter_satellite_candidate_summary=(
                _inter_satellite_candidate_summary(
                    observation.inter_satellite_candidates,
                    selection.decisions,
                )
            ),
            ground_station_access_candidate_summary=ground_candidate_summary,
            zone_service_candidate_summary=(
                _zone_service_candidate_summary(
                    observation.zone_service_candidates
                )
            ),
            physical_link_work_counts=observation.work_counts,
            selected_inter_satellite_links=lifecycle_evidence,
            selected_ground_station_accesses=selected_ground,
            ground_station_preparations=tuple(
                GroundStationLifecycleEvidence(
                    candidate=value.candidate,
                    selected_since=value.selected_since,
                    acquisition_duration=duration,
                    remaining_acquisition=max(timedelta(0), duration-(timestamp-value.selected_since)),
                    operational=value.operational,
                ) for value in self._ground_lifecycle_by_pair.values()
            ),
            selected_zone_service_candidates=selected_zone,
            inspection=inspection,
        )
        result = _FramePlanningResult(transition, precedences, evidence)
        self._record(
            "planning-total",
            phase,
            total_started,
            scope,
            {
                "formal_transitions": 1,
                "prehistory_frames": 0,
                "operational_connections": len(
                    authoritative_state.operational_connections
                ),
                "route_outcomes": len(authoritative_state.route_outcomes),
                "semantic_events": len(canonical_events),
            },
            duration_role="rollup",
        )
        return result

    def _advance_isl_lifecycle(
        self,
        timestamp: datetime,
        selected: tuple[InterSatelliteLinkCandidate, ...],
    ) -> tuple[dict[_IslPair, _ConnectionPreparation], int, int, int]:
        previous = self._lifecycle_by_pair
        by_pair = {_candidate_pair(value): value for value in selected}
        reset = len(set(previous) - set(by_pair))
        started = 0
        completed = 0
        next_values: dict[_IslPair, _ConnectionPreparation] = {}
        acquisition_duration = self._inter_satellite_model.acquisition_duration
        for pair in sorted(by_pair, key=_pair_key):
            candidate = by_pair[pair]
            prior = previous.get(pair)
            if prior is None:
                started += 1
                operational = acquisition_duration.total_seconds() == 0.0
                if operational:
                    completed += 1
                value = _ConnectionPreparation(
                    candidate, timestamp, operational
                )
            else:
                operational = prior.operational or (
                    timestamp - prior.selected_since >= acquisition_duration
                )
                if operational and not prior.operational:
                    completed += 1
                value = _ConnectionPreparation(
                    candidate, prior.selected_since, operational
                )
            next_values[pair] = value
        return next_values, started, completed, reset

    def _record(
        self,
        stage: str,
        phase: str,
        started: float,
        scope: dict[str, object],
        work: dict[str, int | float],
        *,
        resources: dict[str, int | float] | None = None,
        duration_role: str = "stage",
    ) -> None:
        if self._execution_observer is None:
            return
        self._execution_observer.record(
            component="planner",
            stage=stage,
            phase=phase,
            duration_seconds=perf_counter() - started,
            duration_role=duration_role,
            scope=scope,
            work=work,
            resources=resources,
            started_monotonic=started,
        )


def _validate_physical_inputs(
    physical_world: PhysicalWorld,
    previous_timestamp: datetime | None,
    physical_frame: PhysicalFrame,
    observation: PhysicalLinkObservation,
) -> datetime:
    if not isinstance(physical_frame, PhysicalFrame):
        raise TypeError("physical_frame must be a PhysicalFrame")
    if not isinstance(observation, PhysicalLinkObservation):
        raise TypeError("observation must be a PhysicalLinkObservation")
    if physical_frame.timestamp != observation.timestamp:
        raise ValueError(
            "PhysicalFrame and PhysicalLinkObservation timestamps must match"
        )
    timestamp = physical_frame.timestamp
    if previous_timestamp is not None and timestamp <= previous_timestamp:
        raise ValueError("physical observations must be strictly increasing")
    if set(physical_frame.positions) != _physical_world_node_ids(physical_world):
        raise ValueError("PhysicalFrame does not cover the exact PhysicalWorld")
    return timestamp


def _parameter_observations(
    operational_isls: tuple[InterSatelliteLinkCandidate, ...],
    ground_accesses: tuple[GroundStationAccessCandidate, ...],
) -> tuple[ConnectionParameterObservation, ...]:
    observations = []
    for candidate in operational_isls:
        observations.append(
            ConnectionParameterObservation(
                OperationalConnectionId(
                    OperationalConnectionKind.INTER_SATELLITE,
                    candidate.endpoint_a,
                    candidate.endpoint_b,
                ),
                candidate.propagation_delay_us,
            )
        )
    for candidate in ground_accesses:
        observations.append(
            ConnectionParameterObservation(
                OperationalConnectionId(
                    OperationalConnectionKind.GROUND_STATION_ACCESS,
                    candidate.ground_station_id,
                    candidate.satellite_id,
                ),
                candidate.propagation_delay_us,
            )
        )
    return tuple(
        sorted(
            observations,
            key=lambda value: _connection_key(value.connection_id),
        )
    )


def _validate_inter_satellite_selection(
    physical_world: PhysicalWorld,
    selection: object,
    observed: tuple[InterSatelliteLinkCandidate, ...],
    retained_pairs: frozenset[_IslPair],
    *, authoritative=None,
) -> tuple[InterSatelliteLinkCandidate, ...]:
    if not isinstance(selection, InterSatelliteSelection):
        raise TypeError(
            "InterSatelliteConnectionPlanningModel must return "
            "InterSatelliteSelection"
        )
    if authoritative is None:
        authoritative = {_candidate_pair(value): value for value in observed}
    selected = []
    for candidate in selection.selected_candidates:
        physical_world.get_satellite(candidate.endpoint_a)
        physical_world.get_satellite(candidate.endpoint_b)
        pair = _candidate_pair(candidate)
        observed_candidate = authoritative.get(pair)
        if observed_candidate is None:
            raise ValueError(
                "ISL model selected a relationship absent from physical facts"
            )
        if candidate != observed_candidate:
            raise ValueError("ISL model falsified authoritative physical values")
        selected.append(observed_candidate)
    values = tuple(selected)
    _validate_selection_capabilities(physical_world, values)
    _validate_inter_satellite_selection_evidence(
        physical_world,
        selection,
        authoritative,
        frozenset(_candidate_pair(value) for value in values),
        retained_pairs,
    )
    return values


def _validate_inter_satellite_selection_evidence(
    physical_world: PhysicalWorld,
    selection: InterSatelliteSelection,
    authoritative: dict[_IslPair, InterSatelliteLinkCandidate],
    selected_pairs: frozenset[_IslPair],
    retained_pairs: frozenset[_IslPair],
) -> None:
    remaining = dict.fromkeys(authoritative)
    underfilled_decisions = 0
    for decision in selection.decisions:
        if decision.decision is CandidateDecisionKind.UNDERFILLED:
            if decision.endpoint_a is not None or decision.endpoint_b is not None:
                raise ValueError("UNDERFILLED ISL evidence must not identify a candidate")
            underfilled_decisions += 1
            continue
        if decision.endpoint_a is None or decision.endpoint_b is None:
            raise ValueError("per-candidate ISL evidence must identify both endpoints")
        pair = decision.endpoint_a, decision.endpoint_b
        if pair not in authoritative:
            if (pair[1], pair[0]) in authoritative:
                raise ValueError("ISL decision endpoints must be canonical")
            raise ValueError("ISL decision refers to a relationship absent from physical facts")
        # Membership in the validated observation already establishes node
        # ownership and canonical endpoints. Consume each decision exactly once.
        if pair not in remaining:
            raise ValueError("ISL decisions contain a duplicate candidate")
        del remaining[pair]
        expected = (CandidateDecisionKind.REJECTED if pair not in selected_pairs
                    else CandidateDecisionKind.RETAINED if pair in retained_pairs
                    else CandidateDecisionKind.SELECTED)
        if decision.decision is not expected:
            raise ValueError("ISL decision contradicts the selected and retained relationships")
    if remaining:
        raise ValueError("ISL decisions must cover every authoritative candidate exactly once")

    if selection.underfilled_slots == 0:
        if underfilled_decisions:
            raise ValueError(
                "UNDERFILLED evidence contradicts zero underfilled slots"
            )
    elif underfilled_decisions != 1:
        raise ValueError(
            "positive underfilled slots require one compact UNDERFILLED decision"
        )

    degree = Counter(
        endpoint for pair in selected_pairs for endpoint in pair
    )
    maximum_unfilled_slots = 0
    for autonomous_system in physical_world.autonomous_systems:
        for shell in autonomous_system.shells:
            for orbit in shell.orbits:
                for satellite in orbit.satellites:
                    capability = satellite.inter_satellite_link_capability
                    if capability is None:
                        raise ValueError(
                            "Network Planner requires explicit ISL capability "
                            f"for {satellite.node_id.value}"
                        )
                    maximum_unfilled_slots += max(
                        0,
                        capability.maximum_simultaneous_links
                        - degree[satellite.node_id],
                    )
    if selection.underfilled_slots > maximum_unfilled_slots:
        raise ValueError(
            "ISL underfilled slots exceed remaining PhysicalWorld capacity"
        )


def _validate_selection_capabilities(
    physical_world: PhysicalWorld,
    selected: tuple[InterSatelliteLinkCandidate, ...],
) -> None:
    degree: dict[NodeId, int] = {}
    for candidate in selected:
        for endpoint in _candidate_pair(candidate):
            degree[endpoint] = degree.get(endpoint, 0) + 1
    for node_id, value in degree.items():
        satellite = physical_world.get_satellite(node_id)
        capability = satellite.inter_satellite_link_capability
        if capability is None:
            raise ValueError(
                "Network Planner requires explicit ISL capability for "
                f"{node_id.value}"
            )
        if value > capability.maximum_simultaneous_links:
            raise ValueError("ISL model exceeded PhysicalWorld capability")


def _validate_lifecycle_transition(
    previous: dict[_IslPair, _ConnectionPreparation],
    current: dict[_IslPair, _ConnectionPreparation],
    selected: tuple[InterSatelliteLinkCandidate, ...],
    timestamp: datetime,
    acquisition_duration: timedelta,
) -> None:
    selected_by_pair = {_candidate_pair(value): value for value in selected}
    if set(current) != set(selected_by_pair):
        raise ValueError("ISL lifecycle does not exactly cover current selection")
    for pair, value in current.items():
        if not isinstance(value, _ConnectionPreparation):
            raise TypeError("ISL lifecycle contains an invalid value")
        if value.candidate != selected_by_pair[pair]:
            raise ValueError("ISL lifecycle candidate is not authoritative")
        prior = previous.get(pair)
        expected_since = timestamp if prior is None else prior.selected_since
        expected_operational = (
            acquisition_duration == timedelta(0)
            if prior is None
            else prior.operational
            or timestamp - prior.selected_since >= acquisition_duration
        )
        if value.selected_since != expected_since:
            raise ValueError("ISL lifecycle selected_since is inconsistent")
        if value.operational is not expected_operational:
            raise ValueError("ISL lifecycle operational state is inconsistent")


def _validate_selected_ground_candidates(
    physical_world: PhysicalWorld,
    selected: object,
    feasible: tuple[GroundStationAccessCandidate, ...],
) -> tuple[tuple[GroundStationAccessCandidate, ...], dict[str, int]]:
    if not isinstance(selected, tuple):
        raise TypeError("GS model must return a tuple of candidates")
    if any(
        not isinstance(value, GroundStationAccessCandidate)
        for value in selected
    ):
        raise TypeError("GS model returned an invalid candidate")
    authoritative = {}
    candidates_per_ground_station: Counter[NodeId] = Counter()
    candidates_per_satellite: Counter[NodeId] = Counter()
    for value in feasible:
        authoritative[(value.ground_station_id, value.satellite_id)] = value
        candidates_per_ground_station[value.ground_station_id] += 1
        candidates_per_satellite[value.satellite_id] += 1
    selected_pairs: set[tuple[NodeId, NodeId]] = set()
    canonical = []
    station_degree: dict[NodeId, int] = {}
    satellite_degree: dict[NodeId, int] = {}
    station_caps: dict[NodeId, int | None] = {}
    satellite_caps: dict[NodeId, int | None] = {}
    for candidate in selected:
        pair = (candidate.ground_station_id, candidate.satellite_id)
        if pair in selected_pairs:
            raise ValueError("GS model returned a duplicate candidate pair")
        selected_pairs.add(pair)
        observed_candidate = authoritative.get(pair)
        if observed_candidate is None:
            raise ValueError("GS model selected a physically infeasible candidate")
        if candidate != observed_candidate:
            raise ValueError("GS model falsified authoritative physical values")
        if candidate.ground_station_id not in station_caps:
            station_caps[candidate.ground_station_id] = (
                physical_world.get_ground_station(
                    candidate.ground_station_id
                ).max_simultaneous_satellite_accesses
            )
        if candidate.satellite_id not in satellite_caps:
            satellite_caps[candidate.satellite_id] = (
                physical_world.get_satellite(
                    candidate.satellite_id
                ).max_simultaneous_ground_station_accesses
            )
        station_degree[candidate.ground_station_id] = (
            station_degree.get(candidate.ground_station_id, 0) + 1
        )
        satellite_degree[candidate.satellite_id] = (
            satellite_degree.get(candidate.satellite_id, 0) + 1
        )
        station_cap = station_caps[candidate.ground_station_id]
        satellite_cap = satellite_caps[candidate.satellite_id]
        if (
            station_cap is not None
            and station_degree[candidate.ground_station_id] > station_cap
        ):
            raise ValueError("GS model exceeded GroundStation physical capability")
        if (
            satellite_cap is not None
            and satellite_degree[candidate.satellite_id] > satellite_cap
        ):
            raise ValueError("GS model exceeded Satellite GS-access capability")
        canonical.append(observed_candidate)
    ground_counts = tuple(candidates_per_ground_station.values())
    satellite_counts = tuple(candidates_per_satellite.values())
    return tuple(canonical), {
        "candidate_entries_scanned": len(feasible),
        "candidate_ground_stations": len(ground_counts),
        "candidate_satellites": len(satellite_counts),
        "minimum_candidates_per_ground_station": min(ground_counts, default=0),
        "maximum_candidates_per_ground_station": max(ground_counts, default=0),
        "minimum_candidates_per_satellite": min(satellite_counts, default=0),
        "maximum_candidates_per_satellite": max(satellite_counts, default=0),
        "selected_entries_validated": len(canonical),
    }


def _validate_parameter_result(
    timestamp: datetime,
    observations: tuple[ConnectionParameterObservation, ...],
    previous_state: PlannedOperationalState | None,
    result: object,
) -> None:
    if not isinstance(result, ParameterPlanningResult):
        raise TypeError(
            "ParameterPlanningModel must return ParameterPlanningResult"
        )
    expected = {value.connection_id for value in observations}
    connections = _unique_by_connection_id(
        result.connections,
        OperationalConnection,
        "Parameter model connections",
    )
    memory = _unique_by_connection_id(
        result.memory,
        ParameterPlanningMemory,
        "Parameter model memory",
    )
    if set(connections) != expected or set(memory) != expected:
        raise ValueError(
            "Parameter model must return each operational Connection exactly once"
        )
    for identifier, value in connections.items():
        remembered = memory[identifier]
        if not (
            remembered.effective == value.endpoint_a_to_b
            == value.endpoint_b_to_a
        ):
            raise ValueError(
                "Parameter memory effective value must equal published parameters"
            )
        if remembered.last_update > timestamp:
            raise ValueError("Parameter memory last_update cannot be in the future")
    previous = {
        value.connection_id: value
        for value in (
            ()
            if previous_state is None
            else previous_state.operational_connections
        )
    }
    expected_established = {
        identifier: connections[identifier]
        for identifier in set(connections) - set(previous)
    }
    expected_withdrawn = set(previous) - set(connections)
    expected_updated = {
        identifier: connections[identifier]
        for identifier in set(connections) & set(previous)
        if connections[identifier] != previous[identifier]
    }
    established = _unique_by_connection_id(
        result.established_connections,
        OperationalConnection,
        "established_connections",
    )
    updated = _unique_by_connection_id(
        result.parameter_updated_connections,
        OperationalConnection,
        "parameter_updated_connections",
    )
    withdrawn_values = tuple(result.withdrawn_connection_ids)
    if any(
        not isinstance(value, OperationalConnectionId)
        for value in withdrawn_values
    ):
        raise TypeError("withdrawn_connection_ids contains an invalid value")
    withdrawn = set(withdrawn_values)
    if len(withdrawn) != len(withdrawn_values):
        raise ValueError("withdrawn_connection_ids contains a duplicate")
    if (
        established != expected_established
        or updated != expected_updated
        or withdrawn != expected_withdrawn
    ):
        raise ValueError(
            "Parameter compact changes do not exactly describe previous/current state"
        )


def _validate_zone_result(
    physical_world: PhysicalWorld,
    candidates: tuple[ZoneServiceCandidate, ...],
    previous_assignments: tuple[ZoneServiceAssignment, ...],
    result: object,
) -> None:
    if not isinstance(result, ZoneServicePlanningResult):
        raise TypeError(
            "ZoneServicePlanningModel must return ZoneServicePlanningResult"
        )
    try:
        zones = physical_world.get_zone_layout().zones
    except RuntimeError:
        zones = ()
    zone_ids = {value.zone_id for value in zones}
    assignments = _unique_by(
        result.assignments,
        ZoneServiceAssignment,
        lambda value: value.zone_id,
        "Zone model assignments",
    )
    if set(assignments) != zone_ids:
        raise ValueError(
            "Zone model must return exactly one explicit assignment per Zone"
        )
    candidate_pairs = set()
    for candidate in candidates:
        if candidate.zone_id not in zone_ids:
            raise ValueError("Zone physical candidate is foreign to PhysicalWorld")
        physical_world.get_satellite(candidate.satellite_id)
        candidate_pairs.add((candidate.zone_id, candidate.satellite_id))
    for assignment in assignments.values():
        if assignment.satellite_id is not None:
            physical_world.get_satellite(assignment.satellite_id)
            if (assignment.zone_id, assignment.satellite_id) not in candidate_pairs:
                raise ValueError(
                    "Zone model selected a Satellite absent from physical candidates"
                )
    previous = {value.zone_id: value.satellite_id for value in previous_assignments}
    expected_changes = {
        zone_id: ZoneServiceTransition(
            zone_id,
            previous.get(zone_id),
            assignments[zone_id].satellite_id,
        )
        for zone_id in zone_ids
        if zone_id not in previous
        or previous[zone_id] != assignments[zone_id].satellite_id
    }
    changes = _unique_by(
        result.transitions,
        ZoneServiceTransition,
        lambda value: value.zone_id,
        "Zone model compact changes",
    )
    if changes != expected_changes:
        raise ValueError(
            "Zone compact changes do not exactly describe previous/current state"
        )


def _validate_route_result(
    previous_outcomes,
    demands: tuple[RouteDemand, ...],
    result: object,
) -> dict:
    if not isinstance(result, RoutePlanningResult):
        raise TypeError(
            "RoutePlanningModel must return RoutePlanningResult"
        )
    outcomes = _unique_by(
        result.outcomes,
        (ReachableRouteOutcome, UnreachableRouteOutcome),
        lambda value: value.demand,
        "Route model outcomes",
    )
    if set(outcomes) != set(demands):
        raise ValueError("Route model must return exactly one outcome per demand")
    previous = {value.demand: value for value in previous_outcomes}
    expected_changes = {
        demand: RouteOutcomeTransition(
            demand,
            previous.get(demand),
            outcomes.get(demand),
        )
        for demand in set(previous) | set(outcomes)
        if previous.get(demand) != outcomes.get(demand)
    }
    changes = _unique_by(
        result.transitions,
        RouteOutcomeTransition,
        lambda value: value.demand,
        "Route model compact changes",
    )
    if changes != expected_changes:
        raise ValueError(
            "Route compact changes do not exactly describe previous/current state"
        )
    return outcomes


def _validate_route_outcomes(
    physical_world: PhysicalWorld,
    connections: tuple[OperationalConnection, ...],
    zone_assignments: tuple[ZoneServiceAssignment, ...],
    demands: tuple[RouteDemand, ...],
    outcomes,
    *, by_demand=None,
) -> None:
    if by_demand is None:
        values = tuple(outcomes)
        if any(
            not isinstance(value, (ReachableRouteOutcome, UnreachableRouteOutcome))
            for value in values
        ):
            raise TypeError("Route model returned an invalid outcome")
        by_demand = {value.demand: value for value in values}
        if set(by_demand) != set(demands) or len(values) != len(demands):
            raise ValueError("Route model must return exactly one outcome per demand")
    assignments = {value.zone_id: value.satellite_id for value in zone_assignments}
    edges = {
        pair for value in connections
        for pair in ((value.connection_id.endpoint_a, value.connection_id.endpoint_b),
                     (value.connection_id.endpoint_b, value.connection_id.endpoint_a))
    }
    unresolved_ground: dict[NodeId, set[NodeId]] = {}
    unresolved_zone: dict[NodeId, set[NodeId]] = {}
    for demand in demands:
        physical_world.get_node(demand.source)
        outcome = by_demand[demand]
        ground_target = isinstance(demand.target, GroundStationRouteDemandTarget)
        if ground_target:
            terminal = demand.target.ground_station_id
            physical_world.get_ground_station(terminal)
            unserved = False
        else:
            if not isinstance(demand.target, ZoneRouteDemandTarget):
                raise TypeError("Route demand target is invalid")
            if demand.target.zone_id not in assignments:
                raise ValueError("Route demand references a foreign Zone")
            terminal = assignments[demand.target.zone_id]
            unserved = terminal is None
        if isinstance(outcome, ReachableRouteOutcome):
            if unserved:
                raise ValueError("an unserved Zone cannot have a reachable Route")
            assert terminal is not None
            if outcome.path[-1] != terminal:
                raise ValueError("reachable Route terminates at the wrong target")
            for current, next_hop in zip(
                outcome.path, outcome.path[1:], strict=False
            ):
                if (current, next_hop) not in edges:
                    raise ValueError(
                        "reachable Route uses a non-operational Connection"
                    )
                if not _route_step_allows_transit(
                    source=demand.source,
                    current=current,
                    next_hop=next_hop,
                    terminal=terminal,
                    ground_station_target=ground_target,
                ):
                    raise ValueError(
                        "reachable Route violates GroundStation transit restrictions"
                    )
        else:
            if unserved:
                if outcome.reason is not RouteUnreachableReason.ZONE_UNSERVED:
                    raise ValueError("unserved Zone requires ZONE_UNSERVED reason")
                continue
            if outcome.reason is not RouteUnreachableReason.NO_OPERATIONAL_PATH:
                raise ValueError("reachable target requires NO_OPERATIONAL_PATH reason")
            assert terminal is not None
            grouped = unresolved_ground if ground_target else unresolved_zone
            grouped.setdefault(terminal, set()).add(demand.source)
    if unresolved_ground or unresolved_zone:
        context = _prepare_routing_core_context(physical_world, tuple(
            (v.connection_id.endpoint_a, v.connection_id.endpoint_b) for v in connections))
    for terminal, sources in unresolved_ground.items():
        choices = _required_ground_next_hops(
            context, terminal, frozenset(sources)
        )
        if set(choices) & sources:
            raise ValueError("Route model claimed unreachable for a reachable demand")
    for terminal, sources in unresolved_zone.items():
        choices = _required_zone_next_hops(
            context, terminal, frozenset(sources)
        )
        if set(choices) & sources:
            raise ValueError("Route model claimed unreachable for a reachable demand")


def _unique_by_connection_id(values, expected_type, field_name: str) -> dict:
    return _unique_by(
        values,
        expected_type,
        lambda value: value.connection_id,
        field_name,
    )


def _unique_by(values, expected_type, key, field_name: str) -> dict:
    try:
        copied = tuple(values)
    except TypeError as error:
        raise TypeError(f"{field_name} must be iterable") from error
    if any(not isinstance(value, expected_type) for value in copied):
        raise TypeError(f"{field_name} contains an invalid value")
    result = {}
    for value in copied:
        identity = key(value)
        if identity in result:
            raise ValueError(f"{field_name} contains a duplicate identity")
        result[identity] = value
    return result


def _validated_route_demands(
    demands: Iterable[RouteDemand],
) -> tuple[RouteDemand, ...]:
    values = tuple(demands)
    if any(not isinstance(value, RouteDemand) for value in values):
        raise TypeError("route_demands must contain RouteDemand values")
    ordered = tuple(sorted(values, key=_route_demand_key))
    if len(set(ordered)) != len(ordered):
        raise ValueError("route_demands contains a duplicate")
    return ordered


def _inter_satellite_candidate_summary(
    candidates: tuple[InterSatelliteLinkCandidate, ...],
    decisions: tuple[CandidateDecision, ...],
) -> PlanningCandidateSummary:
    return PlanningCandidateSummary(
        feasible_count=len(candidates),
        histograms=(
            _categorical_histogram(
                "relationship",
                tuple(
                    "same-plane" if value.same_plane else "cross-plane"
                    for value in candidates
                ),
            ),
            _categorical_histogram(
                "cyclic-plane-separation",
                tuple(
                    str(value.cyclic_plane_separation)
                    if value.cyclic_plane_separation < 4
                    else "4+"
                    for value in candidates
                ),
            ),
            _numeric_histogram(
                "distance-m",
                tuple(value.distance_m for value in candidates),
                (500_000, 1_000_000, 2_000_000, 5_000_000, 10_000_000),
            ),
            _numeric_histogram(
                "propagation-delay-us",
                tuple(value.propagation_delay_us for value in candidates),
                (1_000, 5_000, 10_000, 25_000, 50_000),
            ),
            _decision_histogram(decisions),
        ),
    )


def _ground_station_candidate_summary(
    candidates: tuple[GroundStationAccessCandidate, ...],
) -> PlanningCandidateSummary:
    return PlanningCandidateSummary(
        feasible_count=len(candidates),
        histograms=_access_quality_histograms(candidates),
    )


def _zone_service_candidate_summary(
    candidates: tuple[ZoneServiceCandidate, ...],
) -> PlanningCandidateSummary:
    return PlanningCandidateSummary(
        feasible_count=len(candidates),
        histograms=_access_quality_histograms(candidates),
    )


def _access_quality_histograms(candidates) -> tuple[PlanningCandidateHistogram, ...]:
    specifications = (
        (
            "distance-m",
            "distance_m",
            (500_000, 1_000_000, 2_000_000, 5_000_000, 10_000_000),
        ),
        ("elevation-deg", "elevation_deg", (0, 10, 30, 60, 80)),
        (
            "propagation-delay-us",
            "propagation_delay_us",
            (1_000, 5_000, 10_000, 25_000, 50_000),
        ),
    )
    counts = [Counter() for _ in specifications]
    sample_count = 0
    for candidate in candidates:
        sample_count += 1
        for index, (_name, attribute, thresholds) in enumerate(specifications):
            counts[index][
                _numeric_histogram_label(getattr(candidate, attribute), thresholds)
            ] += 1
    return tuple(
        PlanningCandidateHistogram(
            name=name,
            sample_count=sample_count,
            bins=tuple(
                PlanningHistogramBin(label, count)
                for label, count in sorted(counts[index].items())
            ),
        )
        for index, (name, _attribute, _thresholds) in enumerate(specifications)
    )


def _numeric_histogram_label(
    value: int | float, thresholds: tuple[int, ...]
) -> str:
    return next(
        (f"<{threshold}" for threshold in thresholds if value < threshold),
        f">={thresholds[-1]}",
    )


def _numeric_histogram(
    name: str, values: tuple[int | float, ...], thresholds: tuple[int, ...]
) -> PlanningCandidateHistogram:
    labels = tuple(_numeric_histogram_label(value, thresholds) for value in values)
    return _categorical_histogram(name, labels)


def _categorical_histogram(
    name: str, labels: tuple[str, ...]
) -> PlanningCandidateHistogram:
    counts = Counter(labels)
    return PlanningCandidateHistogram(
        name=name,
        sample_count=len(labels),
        bins=tuple(
            PlanningHistogramBin(label, count)
            for label, count in sorted(counts.items())
        ),
    )


def _decision_histogram(
    decisions: tuple[CandidateDecision, ...],
) -> PlanningCandidateHistogram:
    counts = Counter(
        _bounded_histogram_label(
            f"{value.decision.value}:{value.reason}"
        )
        for value in decisions
    )
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    if len(ranked) > 32:
        kept = ranked[:31]
        kept.append(("__other_reasons__", sum(value for _, value in ranked[31:])))
        ranked = kept
    return PlanningCandidateHistogram(
        name="selection-decision-reason",
        sample_count=len(decisions),
        bins=tuple(
            PlanningHistogramBin(label, count) for label, count in ranked
        ),
    )


def _bounded_histogram_label(value: str) -> str:
    if len(value) <= 120:
        return value
    digest = sha256(value.encode("utf-8")).hexdigest()[:16]
    return f"{value[:96]}#{digest}"


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


def _require_methods(
    value: object, field_name: str, methods: tuple[str, ...]
) -> None:
    if any(not callable(getattr(value, method, None)) for method in methods):
        raise TypeError(f"{field_name} does not implement its planning role")


def _candidate_pair(
    candidate: InterSatelliteLinkCandidate,
) -> _IslPair:
    return candidate.endpoint_a, candidate.endpoint_b


def _canonical_pair(first: NodeId, second: NodeId) -> _IslPair:
    return (
        (first, second)
        if _node_key(first) < _node_key(second)
        else (second, first)
    )


def _pair_key(pair: _IslPair) -> tuple:
    return _node_key(pair[0]), _node_key(pair[1])


def _connection_key(identifier: OperationalConnectionId) -> tuple:
    return (
        identifier.kind.value,
        _node_key(identifier.endpoint_a),
        _node_key(identifier.endpoint_b),
    )


def _route_demand_key(demand: RouteDemand) -> tuple:
    target = demand.target
    identity = (
        ("G", _node_key(target.ground_station_id))
        if hasattr(target, "ground_station_id")
        else ("Z", target.zone_id)
    )
    return _node_key(demand.source), identity


def _physical_world_node_ids(physical_world: PhysicalWorld) -> set[NodeId]:
    return {
        node.node_id
        for autonomous_system in physical_world.autonomous_systems
        for node in (
            *(
                satellite
                for shell in autonomous_system.shells
                for orbit in shell.orbits
                for satellite in orbit.satellites
            ),
            *autonomous_system.ground_stations,
        )
    }


def _node_key(node_id: NodeId) -> tuple[int, str, str]:
    return node_id.asn, node_id.entity_kind, node_id.entity_id
