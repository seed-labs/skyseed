"""Authoritative Network Planner Model contracts and references."""

from __future__ import annotations

from fractions import Fraction

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isfinite
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from skyseed_v2.shared import NodeId
from skyseed_v2.shared.time import normalize_utc_datetime

from .._ground_station_allocation import (
    _GroundAccessAllocationPreference,
    _allocate_continuous_ground_station_accesses,
    _allocate_ground_station_accesses,
)
from .._gateway_candidates import _rank_gateway_candidates
from .._route_computation import _GroupedRouteEngine, _RoutePathRequest
from .._routing_core import (
    _prepare_routing_core_context,
)
from ..physical import (
    GroundStationAccessCandidate,
    InterSatelliteLinkCandidate,
    PhysicalWorld,
    ZoneServiceCandidate,
)
from .values import (
    CandidateDecision,
    CandidateDecisionKind,
    EffectiveConnectionParameters,
    GroundStationRouteDemandTarget,
    OperationalConnection,
    OperationalConnectionId,
    OperationalConnectionKind,
    PlanTransition,
    ReachableRouteOutcome,
    RouteDemand,
    RouteOutcomeTransition,
    RouteOutcome,
    RouteUnreachableReason,
    SemanticEventReference,
    SemanticEventPrecedence,
    UnreachableRouteOutcome,
    ZoneRouteDemandTarget,
    ZoneServiceAssignment,
    ZoneServiceTransition,
)


_IslPair = tuple[NodeId, NodeId]


def _validate_connection_durations(preparation: timedelta, service: timedelta) -> None:
    for name, value in (("acquisition_duration", preparation),
                        ("minimum_service_duration", service)):
        if not isinstance(value, timedelta):
            raise TypeError(f"{name} must be a timedelta")
        if value < timedelta(0):
            raise ValueError(f"{name} must be non-negative")


@dataclass(frozen=True, slots=True)
class InterSatelliteSelection:
    """Return one validated ISL policy selection and its compact evidence.

    Layer: public Simulator planning-Model boundary value. It is public because
    replaceable ISL Models must return one stable contract to ``Planner``. It
    is not an operational Connection set, acquisition state, or physical fact
    owner.
    """

    selected_candidates: tuple[InterSatelliteLinkCandidate, ...]
    decisions: tuple[CandidateDecision, ...]
    underfilled_slots: int

    def __post_init__(self) -> None:
        candidates = tuple(self.selected_candidates)
        if any(
            not isinstance(value, InterSatelliteLinkCandidate)
            for value in candidates
        ):
            raise TypeError(
                "selected_candidates must contain ISL candidate values"
            )
        ordered = tuple(sorted(candidates, key=_isl_candidate_identity_key))
        identities = tuple(_candidate_pair(value) for value in ordered)
        if len(identities) != len(set(identities)):
            raise ValueError("selected_candidates contains a duplicate pair")
        decisions = tuple(self.decisions)
        if any(not isinstance(value, CandidateDecision) for value in decisions):
            raise TypeError("decisions must contain CandidateDecision values")
        if (
            isinstance(self.underfilled_slots, bool)
            or not isinstance(self.underfilled_slots, int)
            or self.underfilled_slots < 0
        ):
            raise ValueError("underfilled_slots must be non-negative")
        object.__setattr__(self, "selected_candidates", ordered)
        object.__setattr__(self, "decisions", decisions)


@runtime_checkable
class InterSatelliteConnectionPlanningModel(Protocol):
    """Define the replaceable ISL candidate-scope and selection policy.

    Layer: public Simulator planning Model contract. It is public because
    callers inject ISL policy into ``Planner`` without replacing the physical
    or downstream layers. It is not a PhysicalLinkModel, lifecycle state
    owner, or hardware-capability aggregate.
    """

    acquisition_duration: timedelta
    minimum_service_duration: timedelta

    def create_inter_satellite_candidate_pairs(
        self, physical_world: PhysicalWorld
    ) -> tuple[_IslPair, ...]:
        """Enumerate only bounded ISL relationships for physical evaluation."""

    def select_inter_satellite_links(
        self,
        physical_world: PhysicalWorld,
        candidates: tuple[InterSatelliteLinkCandidate, ...],
        retained_pairs: frozenset[_IslPair],
    ) -> InterSatelliteSelection:
        """Retain feasible selections and deterministically fill capacity."""


@runtime_checkable
class GroundStationAccessPlanningModel(Protocol):
    """Define the replaceable GroundStation access-selection policy.

    Layer: public Simulator planning Model contract. It is public because
    callers may inject GroundStation policy independently of the other Planner
    roles. It is not physical visibility geometry, ISL acquisition, or Zone
    service planning.
    """

    acquisition_duration: timedelta
    minimum_service_duration: timedelta

    def select_ground_station_accesses(
        self,
        physical_world: PhysicalWorld,
        candidates: tuple[GroundStationAccessCandidate, ...],
        retained_pairs: frozenset[tuple[NodeId, NodeId]] = frozenset(),
    ) -> tuple[GroundStationAccessCandidate, ...]:
        """Select operational GS access from physical candidates."""


@runtime_checkable
class ZoneServicePlanningModel(Protocol):
    """Define the replaceable policy that serves or explicitly unserves Zones.

    Layer: public Simulator planning Model contract. It is public because
    callers may inject Zone assignment policy and return replay-complete
    changes through a stable boundary. It is not physical Zone visibility,
    Route planning, or a static Zone-layout owner.
    """

    def assign_zone_service(
        self,
        physical_world: PhysicalWorld,
        candidates: tuple[ZoneServiceCandidate, ...],
        previous_assignments: tuple[ZoneServiceAssignment, ...],
    ) -> ZoneServicePlanningResult:
        """Advance every Zone and surface changes while choosing service."""


@dataclass(frozen=True, slots=True)
class ZoneServicePlanningResult:
    """Return complete Zone assignments plus their compact semantic changes.

    Layer: public Simulator planning-Model boundary value. It is public because
    replaceable Zone Models must return data that ``Planner`` can validate and
    publish. It is not a complete PlannedOperationalState, physical candidate
    set, or backend mutation list.
    """

    assignments: tuple[ZoneServiceAssignment, ...]
    transitions: tuple[ZoneServiceTransition, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "assignments", tuple(self.assignments))
        object.__setattr__(self, "transitions", tuple(self.transitions))


@dataclass(frozen=True, slots=True)
class ConnectionParameterObservation:
    """Describe the physical delay input for one operational Connection.

    Layer: public Simulator parameter-planning input value. It is public
    because replaceable Parameter Models receive a stable observation contract
    from ``Planner``. It is not an effective-parameter decision, raw physical
    candidate, or backend qdisc command.
    """

    connection_id: OperationalConnectionId
    physical_delay_us: int

    def __post_init__(self) -> None:
        if not isinstance(self.connection_id, OperationalConnectionId):
            raise TypeError("connection_id must be an OperationalConnectionId")
        if (
            isinstance(self.physical_delay_us, bool)
            or not isinstance(self.physical_delay_us, int)
            or self.physical_delay_us < 0
        ):
            raise ValueError("physical_delay_us must be non-negative")


@dataclass(frozen=True, slots=True)
class ParameterPlanningMemory:
    """Carry bounded per-Connection memory across Parameter Model calls.

    Layer: public Simulator parameter-planning Model value. It is public
    because replaceable stateful Parameter Models exchange this validated
    memory with ``Planner``. It is not authoritative Planner state, a Plan
    payload, or realized backend state.
    """

    connection_id: OperationalConnectionId
    effective: EffectiveConnectionParameters
    last_update: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.connection_id, OperationalConnectionId):
            raise TypeError("connection_id must be an OperationalConnectionId")
        if not isinstance(self.effective, EffectiveConnectionParameters):
            raise TypeError("effective must be EffectiveConnectionParameters")
        object.__setattr__(
            self,
            "last_update",
            normalize_utc_datetime(self.last_update, "last_update"),
        )


@dataclass(frozen=True, slots=True)
class ParameterPlanningResult:
    """Return complete parameterized Connections, memory, and exact changes.

    Layer: public Simulator planning-Model boundary value. It is public because
    replaceable Parameter Models need one replay-complete result contract. It
    is not a complete PlannedOperationalState, a physical observation, or a
    compiled realization.
    """

    connections: tuple[OperationalConnection, ...]
    memory: tuple[ParameterPlanningMemory, ...]
    established_connections: tuple[OperationalConnection, ...]
    withdrawn_connection_ids: tuple[OperationalConnectionId, ...]
    parameter_updated_connections: tuple[OperationalConnection, ...]

    def __post_init__(self) -> None:
        for field_name in (
            "connections",
            "memory",
            "established_connections",
            "withdrawn_connection_ids",
            "parameter_updated_connections",
        ):
            object.__setattr__(self, field_name, tuple(getattr(self, field_name)))


@runtime_checkable
class ParameterPlanningModel(Protocol):
    """Define replaceable effective-Connection-parameter planning.

    Layer: public Simulator planning Model contract. It is public because
    callers inject parameter policy independently and ``Planner`` validates its
    stable result values. It is not a physical-link calculator, semantic state
    owner, or backend traffic-control compiler.
    """

    def plan_effective_parameters(
        self,
        timestamp: datetime,
        observations: tuple[ConnectionParameterObservation, ...],
        previous_memory: tuple[ParameterPlanningMemory, ...],
    ) -> ParameterPlanningResult:
        """Advance complete effective parameters and compact memory."""


@runtime_checkable
class RoutePlanningModel(Protocol):
    """Define replaceable demand-bounded Route outcome planning.

    Layer: public Simulator planning Model contract. It is public because
    callers inject Route policy and ``Planner`` owns its deterministic worker
    shutdown boundary. It is not a physical path-feasibility model, forwarding
    compiler, or owner of PlannedOperationalState.
    """

    def plan_route_outcomes(
        self,
        physical_world: PhysicalWorld,
        operational_connections: tuple[OperationalConnection, ...],
        zone_service_assignments: tuple[ZoneServiceAssignment, ...],
        demands: tuple[RouteDemand, ...],
        previous_outcomes: tuple[RouteOutcome, ...],
    ) -> RoutePlanningResult:
        """Plan outcomes and surface compact changes during resolution."""

    def shutdown(self, *, wait: bool = True) -> None:
        """Deterministically stop every worker owned by this Model."""


@dataclass(frozen=True, slots=True)
class RoutePlanningResult:
    """Return complete Route outcomes and their replay-complete changes.

    Layer: public Simulator planning-Model boundary value. It is public because
    replaceable Route Models must return one validated contract to ``Planner``.
    It is not a forwarding table, backend intent set, or complete operational
    state.
    """

    outcomes: tuple[RouteOutcome, ...]
    transitions: tuple[RouteOutcomeTransition, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "outcomes", tuple(self.outcomes))
        object.__setattr__(self, "transitions", tuple(self.transitions))


@runtime_checkable
class CoordinationPolicy(Protocol):
    """Define replaceable precedence over transition-derived semantic events.

    Layer: public Simulator planning coordination contract. It is public
    because callers may inject ordering policy without changing transition
    payload truth. It is not an event owner, state-transition generator, or
    backend resource scheduler.
    """

    def coordinate(
        self,
        transition: PlanTransition,
        canonical_events: tuple[SemanticEventReference, ...],
        required_precedences: tuple[SemanticEventPrecedence, ...],
    ) -> tuple[SemanticEventPrecedence, ...]:
        """Return only precedence relationships over canonical events."""


@runtime_checkable
class PlanningWindowPolicy(Protocol):
    """Define bounded planning, publication, and warm-prehistory horizons.

    Layer: public Simulator Plan-publication policy contract. It is public
    because callers inject timestamp-window policy into ``Planner``. It is not
    a Timeline cursor, transition buffer, or alternate batch Planner.
    """

    frames_per_batch: int

    def validate_observation_interval(self, observation_interval: timedelta) -> None:
        """Validate the formal cadence, independently of batch size."""


@dataclass(frozen=True, slots=True)
class RetainFirstInterSatelliteConfiguration:
    """Configure the reference retain-first ISL planning policy.

    Layer: public Simulator planning configuration value. It is public because
    callers configure the supplied ISL Model through constructor injection. It
    is not PhysicalWorld hardware capability, a selected-link set, or lifecycle
    state.
    """

    desired_same_plane_links: int = 2
    desired_cross_plane_links: int = 2
    maximum_cross_plane_separation: int = 3
    same_plane_neighbor_span: int | None = None
    cross_plane_neighbor_count: int | None = None
    acquisition_duration: timedelta = timedelta(0)
    minimum_service_duration: timedelta = timedelta(0)

    def __post_init__(self) -> None:
        for field_name in (
            "desired_same_plane_links",
            "desired_cross_plane_links",
            "maximum_cross_plane_separation",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
        for name in ("same_plane_neighbor_span", "cross_plane_neighbor_count"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 1):
                raise ValueError(f"{name} must be positive or None")
        if self.maximum_cross_plane_separation == 0:
            if self.desired_cross_plane_links:
                raise ValueError(
                    "cross-plane demand requires a positive separation"
                )
        _validate_connection_durations(self.acquisition_duration, self.minimum_service_duration)
        if not isinstance(self.acquisition_duration, timedelta):
            raise TypeError("acquisition_duration must be a timedelta")
        if self.acquisition_duration < timedelta(0):
            raise ValueError("acquisition_duration must be non-negative")


def _isl_blocked_reason(candidate, total, same, cross, capabilities, configuration):
    first, second = _candidate_pair(candidate)
    if not candidate.same_plane and candidate.cyclic_plane_separation > configuration.maximum_cross_plane_separation:
        return "outside configured cross-plane candidate scope"
    if total[first] >= capabilities[first] or total[second] >= capabilities[second]:
        return "PhysicalWorld capability exhausted at an endpoint"
    if candidate.same_plane and (same[first] >= configuration.desired_same_plane_links or same[second] >= configuration.desired_same_plane_links):
        return "same-plane policy target reached at an endpoint"
    if not candidate.same_plane and (cross[first] >= configuration.desired_cross_plane_links or cross[second] >= configuration.desired_cross_plane_links):
        return "cross-plane policy target reached at an endpoint"
    return None


class RetainFirstInterSatelliteConnectionPlanningModel:
    """Apply the reference bounded K-scope, retain-first ISL policy.

    Layer: public Simulator planning Model implementation. It is public because
    composition roots need a supplied, configurable implementation of the ISL
    Model contract. It is not PhysicalWorld capability, physical visibility,
    or Planner-owned acquisition state.
    """

    __slots__ = ("_configuration", "_prepared_world", "_capabilities", "_decisions", "_selection_cache", "_selection_work")

    def __init__(
        self,
        configuration: RetainFirstInterSatelliteConfiguration = (
            RetainFirstInterSatelliteConfiguration()
        ),
    ) -> None:
        if not isinstance(
            configuration, RetainFirstInterSatelliteConfiguration
        ):
            raise TypeError(
                "configuration must be RetainFirstInterSatelliteConfiguration"
            )
        self._configuration = configuration
        self._prepared_world = None
        self._capabilities = {}
        self._decisions = {}
        self._selection_cache = None
        self._selection_work = {}

    @property
    def acquisition_duration(self) -> timedelta:
        return self._configuration.acquisition_duration

    @property
    def minimum_service_duration(self) -> timedelta:
        return self._configuration.minimum_service_duration

    def create_inter_satellite_candidate_pairs(
        self, physical_world: PhysicalWorld
    ) -> tuple[_IslPair, ...]:
        if not isinstance(physical_world, PhysicalWorld):
            raise TypeError("physical_world must be a PhysicalWorld")
        pairs: set[_IslPair] = set()
        maximum_separation = self._configuration.maximum_cross_plane_separation
        for autonomous_system in physical_world.autonomous_systems:
            for shell in autonomous_system.shells:
                orbits = tuple(
                    sorted(
                        shell.orbits,
                        key=lambda value: (
                            value.right_ascension_deg,
                            value.orbit_id,
                        ),
                    )
                )
                for orbit in orbits:
                    satellites = tuple(value.node_id for value in sorted(
                        orbit.satellites, key=lambda value: (value.mean_anomaly_deg, _node_key(value.node_id))))
                    span = self._configuration.same_plane_neighbor_span
                    for first_index, first in enumerate(satellites):
                        offsets = range(1, len(satellites)) if span is None else range(1, min(span, len(satellites) // 2) + 1)
                        for offset in offsets:
                            second = satellites[(first_index + offset) % len(satellites)]
                            if first != second:
                                pairs.add(_canonical_pair(first, second))
                orbit_count = len(orbits)
                for first_index, first_orbit in enumerate(orbits):
                    for second_index in range(first_index + 1, orbit_count):
                        separation = min(
                            second_index - first_index,
                            orbit_count - (second_index - first_index),
                        )
                        if separation == 0 or separation > maximum_separation:
                            continue
                        second_orbit = orbits[second_index]
                        for first in first_orbit.satellites:
                            for second in second_orbit.satellites:
                                pairs.add(
                                    _canonical_pair(
                                        first.node_id, second.node_id
                                    )
                                )
        return tuple(
            sorted(
                pairs,
                key=lambda value: (_node_key(value[0]), _node_key(value[1])),
            )
        )

    def select_inter_satellite_links(
        self,
        physical_world: PhysicalWorld,
        candidates: tuple[InterSatelliteLinkCandidate, ...],
        retained_pairs: frozenset[_IslPair],
    ) -> InterSatelliteSelection:
        if not isinstance(physical_world, PhysicalWorld):
            raise TypeError("physical_world must be a PhysicalWorld")
        if not isinstance(retained_pairs, frozenset):
            raise TypeError("retained_pairs must be a frozenset")
        if self._prepared_world is not physical_world:
            self._capabilities = _isl_capabilities(physical_world)
            self._prepared_world = physical_world
            self._decisions = {}
            self._selection_cache = None
        capabilities = self._capabilities
        by_pair = {}
        shapes = set()
        for candidate in candidates:
            if not isinstance(candidate, InterSatelliteLinkCandidate):
                raise TypeError("candidates must contain ISL candidate values")
            pair = _candidate_pair(candidate)
            by_pair[pair] = candidate
            shapes.add((pair, candidate.same_plane, candidate.cyclic_plane_separation))
        self._selection_work = {"isl_candidate_index_entries": len(candidates),
                                "isl_candidate_policy_sorts": 0, "isl_retained_selection_reused": 0}
        cached = self._selection_cache
        if cached is not None and retained_pairs == cached[0] and shapes == cached[1] and len(by_pair) == len(candidates):
            # Greedy completion left every unselected edge blocked by a fixed
            # capacity/scope/degree constraint. Retaining the whole selection
            # preserves those constraints regardless of changing distances.
            self._selection_work["isl_retained_selection_reused"] = 1
            return InterSatelliteSelection(tuple(by_pair[pair] for pair in cached[2]), cached[3], cached[4])
        decisions_by_key = {}
        def decide(kind, reason, first=None, second=None):
            key = kind, reason, first, second
            value = self._decisions.get(key)
            if value is None:
                value = CandidateDecision(kind, reason, first, second)
            decisions_by_key[key] = value
            return value
        if (cached is not None and retained_pairs == cached[0] and len(by_pair) == len(candidates)
            and all(pair in by_pair and (pair, by_pair[pair].same_plane,
                    by_pair[pair].cyclic_plane_separation) in cached[1] for pair in cached[0])):
            rejected = []
            for pair, candidate in by_pair.items():
                if pair in retained_pairs:
                    continue
                reason = _isl_blocked_reason(candidate, *cached[5], capabilities, self._configuration)
                if reason is None:
                    break
                rejected.append(decide(CandidateDecisionKind.REJECTED, reason, *pair))
            else:
                # Candidate arrivals/departures do not invalidate an incumbent
                # when every current alternative is still provably blocked.
                decisions = tuple(value for value in cached[3]
                    if value.decision is CandidateDecisionKind.RETAINED) + tuple(rejected) + tuple(
                    value for value in cached[3] if value.decision is CandidateDecisionKind.UNDERFILLED)
                self._selection_cache = cached[0], shapes, cached[2], decisions, cached[4], cached[5]
                self._decisions = decisions_by_key
                self._selection_work["isl_retained_selection_reused"] = 1
                return InterSatelliteSelection(tuple(by_pair[pair] for pair in cached[2]), decisions, cached[4])
        ordered_candidates = tuple(
            sorted(candidates, key=_isl_candidate_policy_key)
        )
        self._selection_work["isl_candidate_policy_sorts"] = 1
        selected: dict[_IslPair, InterSatelliteLinkCandidate] = {}
        total_degree = {node_id: 0 for node_id in capabilities}
        same_degree = {node_id: 0 for node_id in capabilities}
        cross_degree = {node_id: 0 for node_id in capabilities}
        decisions: list[CandidateDecision] = []

        for pair in sorted(retained_pairs, key=_pair_key):
            candidate = by_pair.get(_canonical_pair(*pair))
            if candidate is None:
                continue
            _select_candidate(
                selected,
                total_degree,
                same_degree,
                cross_degree,
                candidate,
            )
            decisions.append(
                decide(
                    CandidateDecisionKind.RETAINED,
                    "selected relationship remains physically feasible",
                    *pair,
                )
            )

        for candidate in ordered_candidates:
            pair = _candidate_pair(candidate)
            if pair in selected:
                continue
            first, second = pair
            reason = _isl_blocked_reason(candidate, total_degree, same_degree, cross_degree,
                                         capabilities, self._configuration)
            if reason is None:
                _select_candidate(
                    selected,
                    total_degree,
                    same_degree,
                    cross_degree,
                    candidate,
                )
                decisions.append(
                    decide(
                        CandidateDecisionKind.SELECTED,
                        "filled remaining policy and PhysicalWorld capacity",
                        first,
                        second,
                    )
                )
                continue
            decisions.append(
                decide(
                    CandidateDecisionKind.REJECTED,
                    reason,
                    first,
                    second,
                )
            )

        desired_total = (
            self._configuration.desired_same_plane_links
            + self._configuration.desired_cross_plane_links
        )
        underfilled = sum(
            max(0, min(capabilities[node_id], desired_total) - degree)
            for node_id, degree in total_degree.items()
        )
        if underfilled:
            decisions.append(
                decide(
                    CandidateDecisionKind.UNDERFILLED,
                    f"{underfilled} endpoint slots had no conflict-free "
                    "feasible candidate",
                )
            )
        self._decisions = decisions_by_key
        selected_order = tuple(sorted(selected, key=_pair_key))
        stable_decisions = tuple(CandidateDecision(
            CandidateDecisionKind.RETAINED, "selected relationship remains physically feasible", *pair)
            for pair in selected_order) + tuple(sorted(
                (value for value in decisions if value.decision is CandidateDecisionKind.REJECTED),
                key=lambda value: _pair_key((value.endpoint_a, value.endpoint_b)))) + tuple(
                value for value in decisions if value.decision is CandidateDecisionKind.UNDERFILLED)
        self._selection_cache = (frozenset(selected), shapes, selected_order, stable_decisions, underfilled,
                                 (total_degree, same_degree, cross_degree))
        return InterSatelliteSelection(
            tuple(selected.values()), tuple(decisions), underfilled
        )


class CoverageFirstGroundStationAccessPlanningModel:
    """Maximize GS coverage, distinct satellites, links, continuity, then quality.

    Layer: public Simulator planning Model implementation. It is public because
    composition roots need a supplied implementation of the GroundStation
    access contract. It is not GroundStation visibility geometry, ISL
    acquisition, or a Route planner.
    """

    __slots__ = (
        "_satellite_scopes", "_scope_limits",
        "_default_ground_station_limit",
        "_ground_station_policy_limit",
        "initial_candidate_count",
        "_search_hint",
        "acquisition_duration",
        "minimum_service_duration",
        "_prepared_world",
        "_station_limits",
        "_allocation_work",
        "_station_limit_by_id",
        "_satellite_limits",
    )

    def __init__(self, *, default_ground_station_limit: int,
                 satellite_scopes: Mapping[NodeId, str] | None = None,
                 ground_station_scope_limits: Mapping[str, int] | None = None,
                 ground_station_policy_limit: int | None = None,
                 initial_candidate_count: int | None = None,
                 acquisition_duration: timedelta = timedelta(0),
                 minimum_service_duration: timedelta = timedelta(0)) -> None:
        if (
            isinstance(default_ground_station_limit, bool)
            or not isinstance(default_ground_station_limit, int)
            or default_ground_station_limit < 0
        ):
            raise ValueError(
                "default_ground_station_limit must be non-negative"
            )
        _validate_connection_durations(acquisition_duration, minimum_service_duration)
        self.acquisition_duration = acquisition_duration
        self.minimum_service_duration = minimum_service_duration
        if ground_station_policy_limit is not None and (
            isinstance(ground_station_policy_limit, bool)
            or not isinstance(ground_station_policy_limit, int)
            or ground_station_policy_limit < 0
        ):
            raise ValueError("ground_station_policy_limit must be non-negative or None")
        if initial_candidate_count is not None and (isinstance(initial_candidate_count, bool)
                or not isinstance(initial_candidate_count, int) or initial_candidate_count < 1):
            raise ValueError("initial_candidate_count must be positive or None")
        self.initial_candidate_count = initial_candidate_count
        self._satellite_scopes = _validated_satellite_scopes(satellite_scopes)
        self._scope_limits = dict(ground_station_scope_limits or {})
        if set(self._scope_limits) != set(self._satellite_scopes.values()):
            raise ValueError("scope quotas must match satellite_scopes")
        if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in self._scope_limits.values()):
            raise ValueError("scope quotas must be non-negative integers")
        self._search_hint = None
        self._ground_station_policy_limit = ground_station_policy_limit
        self._default_ground_station_limit = default_ground_station_limit
        self._prepared_world: PhysicalWorld | None = None
        self._station_limits: tuple[tuple[NodeId, int], ...] = ()
        self._allocation_work = {}
        self._station_limit_by_id: Mapping[NodeId, int] = MappingProxyType({})
        self._satellite_limits: Mapping[NodeId, int | None] = MappingProxyType({})

    def select_ground_station_accesses(
        self,
        physical_world: PhysicalWorld,
        candidates: tuple[GroundStationAccessCandidate, ...],
        retained_pairs: frozenset[tuple[NodeId, NodeId]] = frozenset(),
    ) -> tuple[GroundStationAccessCandidate, ...]:
        if not isinstance(physical_world, PhysicalWorld):
            raise TypeError("physical_world must be a PhysicalWorld")
        self._prepare_static_limits(physical_world)
        by_station: dict[NodeId, list[GroundStationAccessCandidate]] = {}
        by_pair: dict[tuple[NodeId, NodeId], GroundStationAccessCandidate] = {}
        for candidate in candidates:
            if not isinstance(candidate, GroundStationAccessCandidate):
                raise TypeError("candidates must contain GS candidate values")
            if candidate.ground_station_id not in self._station_limit_by_id:
                physical_world.get_ground_station(candidate.ground_station_id)
            if candidate.satellite_id not in self._satellite_limits:
                physical_world.get_satellite(candidate.satellite_id)
            by_station.setdefault(candidate.ground_station_id, []).append(
                candidate
            )
            by_pair[(candidate.ground_station_id, candidate.satellite_id)] = (
                candidate
            )
        selected = {}
        work = {}
        for scope, quota in sorted(self._scope_limits.items()) if self._scope_limits else [("", None)]:
            partial_work = {}
            partial = _allocate_continuous_ground_station_accesses(
                tuple(_GroundAccessAllocationPreference(station,
                    limit if quota is None else quota,
                    tuple(c for c in by_station.get(station, ())
                          if not self._satellite_scopes or self._satellite_scopes[c.satellite_id] == scope))
                      for station, limit in self._station_limits),
                self._satellite_limits, retained_pairs, work=partial_work)
            for station, satellites in partial.items():
                selected.setdefault(station, []).extend(satellites)
            for key, value in partial_work.items():
                work[key] = work.get(key, 0) + value
        self._allocation_work = work
        return tuple(
            sorted(
                (
                    by_pair[(station, satellite)]
                    for station, satellites in selected.items()
                    for satellite in satellites
                ),
                key=lambda value: (
                    _node_key(value.ground_station_id),
                    -value.elevation_deg,
                    _node_key(value.satellite_id),
                ),
            )
        )

    def _candidate_search_needs_expansion(self, physical_world, candidates, returned, limit):
        self._prepare_static_limits(physical_world)
        counts = {}
        for candidate in returned:
            counts[candidate.ground_station_id] = counts.get(candidate.ground_station_id, 0) + 1
        # Exhaustion is independent of allocation. Contact-rejected candidates
        # cannot be recovered by increasing a rank limit.
        if not any(count >= limit for count in counts.values()):
            return False
        if self._scope_limits:
            # A global geometric rank limit may hide an entire scope. Widen
            # until no scope has a shortage or the physical query is exhausted.
            per_scope = {}
            for candidate in candidates:
                key = candidate.ground_station_id, self._satellite_scopes[candidate.satellite_id]
                if self._satellite_limits[candidate.satellite_id] != 0:
                    per_scope.setdefault(key, set()).add(candidate.satellite_id)
            return any(len(per_scope.get((station, scope), ())) < quota
                       for station, _ in self._station_limits for scope, quota in self._scope_limits.items()) or any(
                       count >= limit for count in counts.values())
        grouped = {}
        for candidate in candidates:
            if self._satellite_limits[candidate.satellite_id] != 0:
                grouped.setdefault(candidate.ground_station_id, []).append(candidate.satellite_id)
        # A full distinct assignment certifies every search cardinality goal.
        # Unit satellite capacity is intentional here: duplicates require wider
        # search. This feasibility matching has no quality costs or flow graph.
        demand = sum(capacity for _, capacity in self._station_limits)
        if len({sat for values in grouped.values() for sat in values}) < demand:
            return True
        owners = {}
        for station, capacity in self._station_limits:
            for _ in range(capacity):
                pending, previous = [station], {station: None}
                end = None
                for current in pending:
                    for satellite in grouped.get(current, ()):
                        owner = owners.get(satellite)
                        if owner is None:
                            end = current, satellite
                            break
                        if owner not in previous:
                            previous[owner] = current, satellite
                            pending.append(owner)
                    if end is not None:
                        break
                if end is None:
                    return True
                current, satellite = end
                while True:
                    owners[satellite] = current
                    if previous[current] is None:
                        break
                    current, satellite = previous[current]
        return False

    def _prepare_static_limits(self, physical_world: PhysicalWorld) -> None:
        """Cache immutable capacity indexes for one exact PhysicalWorld."""

        if self._prepared_world is physical_world:
            return
        _validate_scope_world(physical_world, self._satellite_scopes)
        station_limit_by_id: dict[NodeId, int] = {}
        satellite_limits: dict[NodeId, int | None] = {}
        for autonomous_system in physical_world.autonomous_systems:
            for ground_station in autonomous_system.ground_stations:
                limit = self._default_ground_station_limit
                if ground_station.max_simultaneous_satellite_accesses is not None:
                    limit = ground_station.max_simultaneous_satellite_accesses
                if self._ground_station_policy_limit is not None:
                    limit = min(limit, self._ground_station_policy_limit)
                if sum(self._scope_limits.values()) > limit:
                    raise ValueError("reserved scope quotas exceed physical GS capacity")
                station_limit_by_id[ground_station.node_id] = limit
            for shell in autonomous_system.shells:
                for orbit in shell.orbits:
                    for satellite in orbit.satellites:
                        satellite_limits[satellite.node_id] = (
                            satellite.max_simultaneous_ground_station_accesses
                        )
        self._station_limits = tuple(
            sorted(station_limit_by_id.items(), key=lambda item: _node_key(item[0]))
        )
        self._station_limit_by_id = MappingProxyType(station_limit_by_id)
        self._satellite_limits = MappingProxyType(satellite_limits)
        self._prepared_world = physical_world
        self._search_hint = None


class HighestElevationZoneServicePlanningModel:
    """Assign each Zone to its deterministic highest-elevation candidate.

    Layer: public Simulator planning Model implementation. It is public because
    composition roots need a supplied implementation of the Zone service
    contract. It is not a physical-candidate generator, static Zone owner, or
    Route planner.
    """

    __slots__ = ("_world", "_zones", "_zone_ids", "_satellite_ids", "_satellite_scopes")

    def __init__(self, *, satellite_scopes: Mapping[NodeId, str] | None = None) -> None:
        self._satellite_scopes = _validated_satellite_scopes(satellite_scopes)
        self._world = None
        self._zones = ()
        self._zone_ids = frozenset()
        self._satellite_ids = frozenset()

    def assign_zone_service(
        self,
        physical_world: PhysicalWorld,
        candidates: tuple[ZoneServiceCandidate, ...],
        previous_assignments: tuple[ZoneServiceAssignment, ...],
    ) -> ZoneServicePlanningResult:
        if not isinstance(physical_world, PhysicalWorld):
            raise TypeError("physical_world must be a PhysicalWorld")
        if self._world is not physical_world:
            _validate_scope_world(physical_world, self._satellite_scopes)
            try:
                zones = physical_world.get_zone_layout().zones
            except RuntimeError:
                zones = ()
            self._zones = tuple(sorted(zones, key=lambda value: value.zone_id))
            self._zone_ids = frozenset(zone.zone_id for zone in zones)
            self._satellite_ids = frozenset(satellite.node_id for system in physical_world.autonomous_systems
                for shell in system.shells for orbit in shell.orbits for satellite in orbit.satellites)
            self._world = physical_world
        zones = self._zones
        previous_by_zone: dict[str, NodeId | None] = {}
        for assignment in previous_assignments:
            if not isinstance(assignment, ZoneServiceAssignment):
                raise TypeError(
                    "previous_assignments must contain Zone assignments"
                )
            if assignment.service_key in previous_by_zone:
                raise ValueError("previous_assignments contains a duplicate")
            previous_by_zone[assignment.service_key] = assignment.satellite_id
        best: dict[str, ZoneServiceCandidate] = {}
        zone_ids = self._zone_ids
        for candidate in candidates:
            if not isinstance(candidate, ZoneServiceCandidate):
                raise TypeError("candidates must contain Zone candidate values")
            if candidate.zone_id not in zone_ids:
                raise ValueError("Zone candidate is foreign to PhysicalWorld")
            if candidate.satellite_id not in self._satellite_ids:
                raise ValueError("Zone candidate Satellite is foreign to PhysicalWorld")
            key = self._satellite_scopes.get(candidate.satellite_id, ""), candidate.zone_id
            current = best.get(key)
            if current is None or (
                -candidate.elevation_deg,
                _node_key(candidate.satellite_id),
            ) < (
                -current.elevation_deg,
                _node_key(current.satellite_id),
            ):
                best[key] = candidate
        assignments = []
        changes = []
        scopes = sorted(set(self._satellite_scopes.values())) if self._satellite_scopes else [""]
        for scope in scopes:
            for zone in zones:
                key = scope, zone.zone_id
                satellite_id = None if key not in best else best[key].satellite_id
                assignments.append(ZoneServiceAssignment(zone.zone_id, satellite_id, scope))
                had_previous = key in previous_by_zone
                previous_satellite_id = previous_by_zone.pop(key, None)
                if not had_previous or previous_satellite_id != satellite_id:
                    changes.append(ZoneServiceTransition(zone.zone_id, previous_satellite_id, satellite_id, scope))
        if previous_by_zone:
            raise ValueError(
                "previous_assignments contains a Zone foreign to PhysicalWorld"
            )
        return ZoneServicePlanningResult(
            tuple(assignments), tuple(changes)
        )


@dataclass(frozen=True, slots=True)
class ParameterKindConfiguration:
    """Configure effective parameters and delay suppression for one kind.

    Layer: public Simulator parameter-planning configuration value. It is
    public because callers configure the supplied Parameter Model by
    Connection kind. It is not an observed physical delay, effective
    Connection instance, or backend qdisc instruction.
    """

    bandwidth_bps: int
    packet_loss_ppm: int
    delay_relative_threshold: float = 0.0
    max_staleness: timedelta | None = None

    def __post_init__(self) -> None:
        EffectiveConnectionParameters(
            self.bandwidth_bps, 0, self.packet_loss_ppm
        )
        if (
            isinstance(self.delay_relative_threshold, bool)
            or not isinstance(self.delay_relative_threshold, (int, float))
            or not isfinite(self.delay_relative_threshold)
            or self.delay_relative_threshold < 0
        ):
            raise ValueError("delay_relative_threshold must be finite and non-negative")
        if self.max_staleness is not None and (
            not isinstance(self.max_staleness, timedelta)
            or self.max_staleness <= timedelta(0)
        ):
            raise ValueError("max_staleness must be positive or None")


class AccumulatedDeadbandParameterPlanningModel:
    """Carry physical delay into effective state with bounded suppression.

    Layer: public Simulator planning Model implementation. It is public because
    composition roots need a configurable implementation of the Parameter
    Model contract. It is not the owner of operational state, a physical-link
    Model, or a backend qdisc compiler.
    """

    __slots__ = ("_configuration", "_thresholds")

    def __init__(
        self,
        configuration: Mapping[
            OperationalConnectionKind, ParameterKindConfiguration
        ],
    ) -> None:
        if set(configuration) != set(OperationalConnectionKind):
            raise ValueError(
                "configuration must contain every OperationalConnectionKind"
            )
        copied = {}
        for kind, value in configuration.items():
            if not isinstance(kind, OperationalConnectionKind):
                raise TypeError("configuration keys must be Connection kinds")
            if not isinstance(value, ParameterKindConfiguration):
                raise TypeError(
                    "configuration values must be ParameterKindConfiguration"
                )
            copied[kind] = value
        self._configuration = MappingProxyType(copied)
        self._thresholds = {kind: Fraction(str(value.delay_relative_threshold)) for kind, value in copied.items()}

    def plan_effective_parameters(
        self,
        timestamp: datetime,
        observations: tuple[ConnectionParameterObservation, ...],
        previous_memory: tuple[ParameterPlanningMemory, ...],
    ) -> ParameterPlanningResult:
        utc = normalize_utc_datetime(timestamp, "timestamp")
        previous: dict[OperationalConnectionId, ParameterPlanningMemory] = {}
        for value in previous_memory:
            if not isinstance(value, ParameterPlanningMemory):
                raise TypeError(
                    "previous_memory must contain ParameterPlanningMemory"
                )
            if value.connection_id in previous:
                raise ValueError("previous_memory contains a duplicate")
            previous[value.connection_id] = value
        next_memory: list[ParameterPlanningMemory] = []
        connections: list[OperationalConnection] = []
        established: list[OperationalConnection] = []
        parameter_updated: list[OperationalConnection] = []
        for observation in sorted(
            observations, key=lambda value: _connection_key(value.connection_id)
        ):
            if not isinstance(observation, ConnectionParameterObservation):
                raise TypeError("observations contains an invalid value")
            identifier = observation.connection_id
            configuration = self._configuration[identifier.kind]
            prior = previous.pop(identifier, None)
            if prior is None:
                effective = EffectiveConnectionParameters(
                    configuration.bandwidth_bps,
                    observation.physical_delay_us,
                    configuration.packet_loss_ppm,
                )
                memory = ParameterPlanningMemory(
                    identifier, effective, utc
                )
            else:
                drift = abs(
                    observation.physical_delay_us - prior.effective.delay_us
                )
                stale = (
                    configuration.max_staleness is not None
                    and utc - prior.last_update >= configuration.max_staleness
                )
                # Comparing against last effective (not the last observation)
                # accumulates drift. A zero baseline changes on any nonzero value.
                threshold = self._thresholds[identifier.kind]
                threshold_reached = drift > 0 and (
                    prior.effective.delay_us == 0
                    or drift * threshold.denominator >= threshold.numerator
                    * abs(prior.effective.delay_us)
                )
                if threshold_reached or stale:
                    effective = EffectiveConnectionParameters(
                        configuration.bandwidth_bps,
                        observation.physical_delay_us,
                        configuration.packet_loss_ppm,
                    )
                    memory = ParameterPlanningMemory(identifier, effective, utc)
                else:
                    memory = prior
            next_memory.append(memory)
            connection = OperationalConnection(
                identifier, memory.effective, memory.effective
            )
            connections.append(connection)
            if prior is None:
                established.append(connection)
            elif prior.effective != memory.effective:
                parameter_updated.append(connection)
        return ParameterPlanningResult(
            tuple(connections),
            tuple(next_memory),
            tuple(established),
            tuple(sorted(previous, key=_connection_key)),
            tuple(parameter_updated),
        )


class GatewayHopCountRoutePlanningModel:
    """Plan demand-bounded hop-count Routes over operational Connections.

    Layer: public Simulator planning Model implementation. It is public because
    composition roots need a supplied Route Model with deterministic worker
    lifecycle. It is not a physical-candidate router, forwarding compiler, or
    owner of Planner publication state.
    """

    __slots__ = (
        "_satellite_scopes", "_scope_models",
        "_route_engine",
        "_gateway_candidate_count",
        "_gateway_candidate_max_distance_m",
        "_prepared_physical_world",
        "_gateway_candidates_by_zone",
        "_routing_edges",
        "_routing_context",
        "_demands_by_service",
        "_demand_assignments", "_last_demands", "_requests", "_construction_work",
    )

    def __init__(
        self,
        *,
        satellite_scopes: Mapping[NodeId, str] | None = None,
        gateway_candidate_count: int = 1,
        gateway_candidate_max_distance_m: float | None = None,
    ) -> None:
        if (
            isinstance(gateway_candidate_count, bool)
            or not isinstance(gateway_candidate_count, int)
            or gateway_candidate_count <= 0
        ):
            raise ValueError("gateway_candidate_count must be positive")
        if gateway_candidate_max_distance_m is not None:
            if isinstance(gateway_candidate_max_distance_m, bool):
                raise ValueError(
                    "gateway_candidate_max_distance_m must be positive or None"
                )
            converted = float(gateway_candidate_max_distance_m)
            if not isfinite(converted) or converted <= 0.0:
                raise ValueError(
                    "gateway_candidate_max_distance_m must be positive or None"
                )
            gateway_candidate_max_distance_m = converted
        self._satellite_scopes = _validated_satellite_scopes(satellite_scopes)
        self._scope_models = {}
        self._route_engine = _GroupedRouteEngine()
        self._gateway_candidate_count = gateway_candidate_count
        self._gateway_candidate_max_distance_m = (
            gateway_candidate_max_distance_m
        )
        self._prepared_physical_world: PhysicalWorld | None = None
        self._gateway_candidates_by_zone = None
        self._routing_edges = None
        self._routing_context = None
        self._demands_by_service = {}
        self._demand_assignments = None
        self._last_demands = ()
        self._requests = ()
        self._construction_work = {}

    def __del__(self) -> None:
        route_engine = getattr(self, "_route_engine", None)
        if route_engine is not None:
            route_engine.shutdown(wait=False)

    def shutdown(self, *, wait: bool = True) -> None:
        self._route_engine.shutdown(wait=wait)
        for model in self._scope_models.values():
            model.shutdown(wait=wait)

    def _active_compute_worker_count(self) -> int:
        if self._scope_models:
            return sum(model._active_compute_worker_count() for model in self._scope_models.values())
        if not self._route_engine.executor_lifecycle.executor_live:
            return 0
        return self._route_engine._executor_worker_count

    def _route_work_counts(self):
        if self._scope_models:
            totals = dict(self._construction_work)
            for model in self._scope_models.values():
                for key, value in model._route_work_counts().items():
                    totals[key] = totals.get(key, 0) + value
            return totals
        observation = self._route_engine.last_compute_observation
        if observation is None:
            return {}
        return {
            **self._construction_work,
            "route_path_requests_reused": observation.reused_request_count,
            "route_path_requests_computed": sum(v.request_count for v in observation.worker_activities),
            "route_destination_jobs": sum(v.non_empty_job_count for v in observation.worker_activities),
        }

    def create_route_demands(
        self,
        physical_world: PhysicalWorld,
        zone_service_assignments: tuple[ZoneServiceAssignment, ...],
    ) -> tuple[RouteDemand, ...]:
        candidates_by_zone = self._prepare_gateway_candidates(physical_world)
        identities = tuple((value.service_key, value.satellite_id) for value in zone_service_assignments)
        if identities == self._demand_assignments:
            self._construction_work["route_demands_reused"] = len(self._last_demands)
            return self._last_demands
        self._construction_work["route_demands_reused"] = 0
        demands = set()
        current_services = {}
        for assignment in zone_service_assignments:
            previous = self._demands_by_service.get(assignment.service_key)
            if previous is not None and previous[0] == assignment.satellite_id:
                values = previous[1]
                self._construction_work["route_demands_reused"] += len(values)
            else:
                values = []
                for gateway_id in candidates_by_zone.get(assignment.zone_id, ()):
                    if assignment.satellite_id is not None:
                        values.append(RouteDemand(assignment.satellite_id,
                            GroundStationRouteDemandTarget(gateway_id, assignment.network_scope)))
                    values.append(RouteDemand(gateway_id, ZoneRouteDemandTarget(assignment.zone_id, assignment.network_scope)))
                values = tuple(values)
            current_services[assignment.service_key] = (assignment.satellite_id, values)
            demands.update(values)
        self._demands_by_service = current_services
        self._demand_assignments = identities
        self._last_demands = tuple(sorted(demands, key=_route_demand_key))
        return self._last_demands

    def _prepare_gateway_candidates(self, physical_world: PhysicalWorld):
        if self._prepared_physical_world is not None:
            if self._prepared_physical_world is not physical_world:
                raise ValueError(
                    "RoutePlanningModel cannot be reused with another "
                    "PhysicalWorld"
                )
            return self._gateway_candidates_by_zone
        prepared = _rank_gateway_candidates(
            physical_world,
            gateway_candidate_count=self._gateway_candidate_count,
            gateway_candidate_max_distance_m=(
                self._gateway_candidate_max_distance_m
            ),
        )
        self._prepared_physical_world = physical_world
        self._gateway_candidates_by_zone = prepared
        return prepared

    def plan_route_outcomes(
        self,
        physical_world: PhysicalWorld,
        operational_connections: tuple[OperationalConnection, ...],
        zone_service_assignments: tuple[ZoneServiceAssignment, ...],
        demands: tuple[RouteDemand, ...],
        previous_outcomes: tuple[RouteOutcome, ...],
    ) -> RoutePlanningResult:
        if self._satellite_scopes:
            _validate_scope_world(physical_world, self._satellite_scopes)
            scopes = set(self._satellite_scopes.values())
            if any(d.target.network_scope not in scopes for d in demands):
                raise ValueError("Route demand has unknown network scope")
            outcomes, transitions = [], []
            for scope in sorted(scopes):
                model = self._scope_models.setdefault(scope, None)
                if model is None:
                    model = self._scope_models[scope] = GatewayHopCountRoutePlanningModel(
                        gateway_candidate_count=self._gateway_candidate_count,
                        gateway_candidate_max_distance_m=self._gateway_candidate_max_distance_m)
                connections = tuple(c for c in operational_connections if all(
                    n.entity_kind == "ground-station" or self._satellite_scopes[n] == scope
                    for n in (c.connection_id.endpoint_a, c.connection_id.endpoint_b)))
                scoped_demands = tuple(d for d in demands if d.target.network_scope == scope)
                if any(d.source.entity_kind == "satellite" and self._satellite_scopes[d.source] != scope for d in scoped_demands):
                    raise ValueError("Route source is outside its network scope")
                result = model.plan_route_outcomes(physical_world, connections,
                    tuple(v for v in zone_service_assignments if v.network_scope == scope),
                    scoped_demands, tuple(v for v in previous_outcomes if v.demand.target.network_scope == scope))
                outcomes.extend(result.outcomes)
                transitions.extend(result.transitions)
            return RoutePlanningResult(tuple(outcomes), tuple(transitions))
        previous_by_demand: dict[RouteDemand, RouteOutcome] = {}
        for previous in previous_outcomes:
            if not isinstance(
                previous,
                (ReachableRouteOutcome, UnreachableRouteOutcome),
            ):
                raise TypeError(
                    "previous_outcomes must contain Route outcomes"
                )
            if previous.demand in previous_by_demand:
                raise ValueError("previous_outcomes contains a duplicate")
            previous_by_demand[previous.demand] = previous
        edges = tuple(
            (
                value.connection_id.endpoint_a,
                value.connection_id.endpoint_b,
            )
            for value in operational_connections
        )
        self._prepare_gateway_candidates(physical_world)
        if self._routing_edges != edges:
            self._routing_context = _prepare_routing_core_context(physical_world, edges, previous=self._routing_context)
            self._routing_edges = edges
        context = self._routing_context
        assignments = {
            value.service_key: value.satellite_id
            for value in zone_service_assignments
        }
        ordered_demands = tuple(sorted(demands, key=_route_demand_key))
        old_requests = self._requests if isinstance(self._requests, dict) else {}
        current_requests = {}
        requests, unresolved = [], {}
        reused = 0
        for index, demand in enumerate(ordered_demands):
            ground_target = isinstance(demand.target, GroundStationRouteDemandTarget)
            terminal = demand.target.ground_station_id if ground_target else assignments.get(demand.target.service_key)
            if terminal is None:
                unresolved[index] = RouteUnreachableReason.ZONE_UNSERVED
                continue
            key = demand, index, terminal
            request = old_requests.get(key)
            if request is None:
                physical_world.get_node(demand.source)
                if ground_target:
                    physical_world.get_ground_station(terminal)
                request = _RoutePathRequest(index, ground_target, demand.source, terminal)
            else:
                reused += 1
            current_requests[key] = request
            requests.append(request)
        self._requests = current_requests
        self._construction_work["route_request_objects_reused"] = reused
        self._construction_work["route_request_objects_constructed"] = len(requests)-reused
        paths = self._route_engine.compute(context, tuple(requests))
        outcomes: list[RouteOutcome] = []
        changes: list[RouteOutcomeTransition] = []
        reused_outcomes = 0
        for index, demand in enumerate(ordered_demands):
            reason = unresolved.get(index)
            path = paths.get(index)
            previous = previous_by_demand.pop(demand, None)
            if reason is None and (path is None or len(path) < 2):
                reason = RouteUnreachableReason.NO_OPERATIONAL_PATH
            if (reason is not None and isinstance(previous, UnreachableRouteOutcome) and previous.reason is reason
                or reason is None and isinstance(previous, ReachableRouteOutcome) and previous.path == path):
                outcome = previous
                reused_outcomes += 1
            elif reason is not None:
                outcome = UnreachableRouteOutcome(demand, reason)
            else:
                outcome = ReachableRouteOutcome(demand, path[1], path)
            outcomes.append(outcome)
            if previous is not outcome:
                changes.append(RouteOutcomeTransition(demand, previous, outcome))
        self._construction_work["route_outcome_objects_reused"] = reused_outcomes
        self._construction_work["route_outcome_objects_constructed"] = len(outcomes) - reused_outcomes
        changes.extend(
            RouteOutcomeTransition(demand, previous_by_demand[demand], None)
            for demand in sorted(
                previous_by_demand, key=_route_demand_key
            )
        )
        return RoutePlanningResult(tuple(outcomes), tuple(changes))


@dataclass(frozen=True, slots=True)
class FrameBatchPlanningWindowPolicy:
    """Batch consecutive formal frames without an independent lookahead wait.

    Layer: public Simulator publication policy. It is public because callers
    select the finite batching size. Composition roots select an
    engineering batch size; Planner preserves each transition's exact time.
    This is not a control interval or a physical prediction horizon.
    """

    frames_per_batch: int = 32

    def __post_init__(self) -> None:
        if (isinstance(self.frames_per_batch, bool)
                or not isinstance(self.frames_per_batch, int)
                or self.frames_per_batch <= 0):
            raise ValueError("frames_per_batch must be a positive integer")

    def validate_observation_interval(self, observation_interval: timedelta) -> None:
        if not isinstance(observation_interval, timedelta):
            raise TypeError("observation_interval must be a timedelta")
        if observation_interval <= timedelta(0):
            raise ValueError("observation_interval must be positive")


def _isl_capabilities(physical_world: PhysicalWorld) -> dict[NodeId, int]:
    capabilities: dict[NodeId, int] = {}
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
                    capabilities[satellite.node_id] = (
                        capability.maximum_simultaneous_links
                    )
    return capabilities


def _select_candidate(
    selected: dict[_IslPair, InterSatelliteLinkCandidate],
    total_degree: dict[NodeId, int],
    same_degree: dict[NodeId, int],
    cross_degree: dict[NodeId, int],
    candidate: InterSatelliteLinkCandidate,
) -> None:
    pair = _candidate_pair(candidate)
    selected[pair] = candidate
    for endpoint in pair:
        total_degree[endpoint] += 1
        (same_degree if candidate.same_plane else cross_degree)[endpoint] += 1


def _resolve_path(
    source: NodeId,
    terminal: NodeId,
    choices: Mapping[NodeId, tuple[NodeId, ...]],
) -> tuple[NodeId, ...] | None:
    if source == terminal:
        return None
    path = [source]
    seen = {source}
    current = source
    while current != terminal:
        available = choices.get(current, ())
        if not available:
            return None
        current = available[0]
        if current in seen:
            raise ValueError("routing core produced a loop")
        seen.add(current)
        path.append(current)
    return tuple(path)


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


def _isl_candidate_policy_key(
    candidate: InterSatelliteLinkCandidate,
) -> tuple:
    # Same-plane is the explicit first category for the canonical reference.
    return (
        0 if candidate.same_plane else 1,
        candidate.distance_m,
        _node_key(candidate.endpoint_a),
        _node_key(candidate.endpoint_b),
    )


def _isl_candidate_identity_key(
    candidate: InterSatelliteLinkCandidate,
) -> tuple:
    return _pair_key(_candidate_pair(candidate))


def _pair_key(pair: _IslPair) -> tuple:
    canonical = _canonical_pair(*pair)
    return _node_key(canonical[0]), _node_key(canonical[1])


def _connection_key(identifier: OperationalConnectionId) -> tuple:
    return (
        identifier.kind.value,
        _node_key(identifier.endpoint_a),
        _node_key(identifier.endpoint_b),
    )


def _route_demand_key(demand: RouteDemand) -> tuple:
    if not isinstance(demand, RouteDemand):
        raise TypeError("demands must contain RouteDemand values")
    target = demand.target
    target_key = (
        ("G", _node_key(target.ground_station_id))
        if isinstance(target, GroundStationRouteDemandTarget)
        else ("Z", target.zone_id)
    )
    return _node_key(demand.source), demand.target.network_scope, target_key


def _node_key(node_id: NodeId) -> tuple[int, str, str]:
    return node_id.asn, node_id.entity_kind, node_id.entity_id


def _validated_satellite_scopes(values):
    if values is None:
        return MappingProxyType({})
    from .values import _validate_network_scope
    result = dict(values)
    for node, scope in result.items():
        if not isinstance(node, NodeId) or node.entity_kind != "satellite":
            raise ValueError("satellite_scopes keys must identify satellites")
        _validate_network_scope(scope)
        if not scope:
            raise ValueError("configured network scopes must be non-empty")
    if not result:
        raise ValueError("configured satellite_scopes must not be empty")
    return MappingProxyType(result)


def _validate_scope_world(world, scopes):
    if scopes:
        actual = {sat.node_id for system in world.autonomous_systems
                  for shell in system.shells for orbit in shell.orbits for sat in orbit.satellites}
        if set(scopes) != actual:
            raise ValueError("satellite_scopes must cover exactly the PhysicalWorld satellites")
