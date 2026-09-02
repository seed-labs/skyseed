"""Authoritative Network Planner Model contracts and references."""

from __future__ import annotations

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
    _GroundAccessChoice,
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

    def select_ground_station_accesses(
        self,
        physical_world: PhysicalWorld,
        candidates: tuple[GroundStationAccessCandidate, ...],
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
    pending_delay_us: int | None
    pending_since: datetime | None
    last_update: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.connection_id, OperationalConnectionId):
            raise TypeError("connection_id must be an OperationalConnectionId")
        if not isinstance(self.effective, EffectiveConnectionParameters):
            raise TypeError("effective must be EffectiveConnectionParameters")
        if self.pending_delay_us is None:
            if self.pending_since is not None:
                raise ValueError("pending_since requires pending_delay_us")
        else:
            if (
                isinstance(self.pending_delay_us, bool)
                or not isinstance(self.pending_delay_us, int)
                or self.pending_delay_us < 0
            ):
                raise ValueError("pending_delay_us must be non-negative")
            if self.pending_since is None:
                raise ValueError("pending_delay_us requires pending_since")
            object.__setattr__(
                self,
                "pending_since",
                normalize_utc_datetime(self.pending_since, "pending_since"),
            )
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

    @property
    def planning_horizon(self) -> timedelta:
        """Return the required lookahead before normal publication."""

    @property
    def publication_horizon(self) -> timedelta:
        """Return the maximum duration published by one Plan."""

    def required_prehistory(self, acquisition_duration: timedelta) -> timedelta:
        """Return the bounded warm-start observation duration."""

    def validate_observation_interval(
        self, observation_interval: timedelta
    ) -> None:
        """Fail closed unless all window boundaries align to the cadence."""


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
    acquisition_duration: timedelta = timedelta(seconds=20)

    def __post_init__(self) -> None:
        for field_name in (
            "desired_same_plane_links",
            "desired_cross_plane_links",
            "maximum_cross_plane_separation",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
        if self.maximum_cross_plane_separation == 0:
            if self.desired_cross_plane_links:
                raise ValueError(
                    "cross-plane demand requires a positive separation"
                )
        if not isinstance(self.acquisition_duration, timedelta):
            raise TypeError("acquisition_duration must be a timedelta")
        if self.acquisition_duration < timedelta(0):
            raise ValueError("acquisition_duration must be non-negative")


class RetainFirstInterSatelliteConnectionPlanningModel:
    """Apply the reference bounded K-scope, retain-first ISL policy.

    Layer: public Simulator planning Model implementation. It is public because
    composition roots need a supplied, configurable implementation of the ISL
    Model contract. It is not PhysicalWorld capability, physical visibility,
    or Planner-owned acquisition state.
    """

    __slots__ = ("_configuration",)

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

    @property
    def acquisition_duration(self) -> timedelta:
        return self._configuration.acquisition_duration

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
                    satellites = tuple(
                        value.node_id for value in orbit.satellites
                    )
                    for first_index, first in enumerate(satellites):
                        for second in satellites[first_index + 1 :]:
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
        capabilities = _isl_capabilities(physical_world)
        ordered_candidates = tuple(
            sorted(tuple(candidates), key=_isl_candidate_policy_key)
        )
        if any(
            not isinstance(value, InterSatelliteLinkCandidate)
            for value in ordered_candidates
        ):
            raise TypeError("candidates must contain ISL candidate values")
        by_pair = {_candidate_pair(value): value for value in ordered_candidates}
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
                CandidateDecision(
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
            if (
                not candidate.same_plane
                and candidate.cyclic_plane_separation
                > self._configuration.maximum_cross_plane_separation
            ):
                reason = "outside configured cross-plane candidate scope"
            elif (
                total_degree[first] >= capabilities[first]
                or total_degree[second] >= capabilities[second]
            ):
                reason = "PhysicalWorld capability exhausted at an endpoint"
            elif candidate.same_plane and (
                same_degree[first]
                >= self._configuration.desired_same_plane_links
                or same_degree[second]
                >= self._configuration.desired_same_plane_links
            ):
                reason = "same-plane policy target reached at an endpoint"
            elif not candidate.same_plane and (
                cross_degree[first]
                >= self._configuration.desired_cross_plane_links
                or cross_degree[second]
                >= self._configuration.desired_cross_plane_links
            ):
                reason = "cross-plane policy target reached at an endpoint"
            else:
                _select_candidate(
                    selected,
                    total_degree,
                    same_degree,
                    cross_degree,
                    candidate,
                )
                decisions.append(
                    CandidateDecision(
                        CandidateDecisionKind.SELECTED,
                        "filled remaining policy and PhysicalWorld capacity",
                        first,
                        second,
                    )
                )
                continue
            decisions.append(
                CandidateDecision(
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
                CandidateDecision(
                    CandidateDecisionKind.UNDERFILLED,
                    f"{underfilled} endpoint slots had no conflict-free "
                    "feasible candidate",
                )
            )
        return InterSatelliteSelection(
            tuple(selected.values()), tuple(decisions), underfilled
        )


class QualityFirstGroundStationAccessPlanningModel:
    """Select quality-ranked GroundStation accesses under hard caps.

    Layer: public Simulator planning Model implementation. It is public because
    composition roots need a supplied implementation of the GroundStation
    access contract. It is not GroundStation visibility geometry, ISL
    acquisition, or a Route planner.
    """

    __slots__ = (
        "_default_ground_station_limit",
        "_prepared_world",
        "_station_limits",
        "_station_limit_by_id",
        "_satellite_limits",
    )

    def __init__(self, *, default_ground_station_limit: int) -> None:
        if (
            isinstance(default_ground_station_limit, bool)
            or not isinstance(default_ground_station_limit, int)
            or default_ground_station_limit < 0
        ):
            raise ValueError(
                "default_ground_station_limit must be non-negative"
            )
        self._default_ground_station_limit = default_ground_station_limit
        self._prepared_world: PhysicalWorld | None = None
        self._station_limits: tuple[tuple[NodeId, int], ...] = ()
        self._station_limit_by_id: Mapping[NodeId, int] = MappingProxyType({})
        self._satellite_limits: Mapping[NodeId, int | None] = MappingProxyType({})

    def select_ground_station_accesses(
        self,
        physical_world: PhysicalWorld,
        candidates: tuple[GroundStationAccessCandidate, ...],
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
        selected = _allocate_ground_station_accesses(
            tuple(
                _GroundAccessAllocationPreference(
                    station,
                    limit,
                    tuple(
                        _GroundAccessChoice(
                            candidate.elevation_deg,
                            candidate.satellite_id,
                        )
                        for candidate in by_station.get(station, ())
                    ),
                )
                for station, limit in self._station_limits
            ),
            self._satellite_limits,
        )
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

    def _prepare_static_limits(self, physical_world: PhysicalWorld) -> None:
        """Cache immutable capacity indexes for one exact PhysicalWorld."""

        if self._prepared_world is physical_world:
            return
        station_limit_by_id: dict[NodeId, int] = {}
        satellite_limits: dict[NodeId, int | None] = {}
        for autonomous_system in physical_world.autonomous_systems:
            for ground_station in autonomous_system.ground_stations:
                limit = self._default_ground_station_limit
                if ground_station.max_simultaneous_satellite_accesses is not None:
                    limit = min(
                        limit,
                        ground_station.max_simultaneous_satellite_accesses,
                    )
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


class HighestElevationZoneServicePlanningModel:
    """Assign each Zone to its deterministic highest-elevation candidate.

    Layer: public Simulator planning Model implementation. It is public because
    composition roots need a supplied implementation of the Zone service
    contract. It is not a physical-candidate generator, static Zone owner, or
    Route planner.
    """

    __slots__ = ()

    def assign_zone_service(
        self,
        physical_world: PhysicalWorld,
        candidates: tuple[ZoneServiceCandidate, ...],
        previous_assignments: tuple[ZoneServiceAssignment, ...],
    ) -> ZoneServicePlanningResult:
        if not isinstance(physical_world, PhysicalWorld):
            raise TypeError("physical_world must be a PhysicalWorld")
        try:
            zones = physical_world.get_zone_layout().zones
        except RuntimeError:
            zones = ()
        previous_by_zone: dict[str, NodeId | None] = {}
        for assignment in previous_assignments:
            if not isinstance(assignment, ZoneServiceAssignment):
                raise TypeError(
                    "previous_assignments must contain Zone assignments"
                )
            if assignment.zone_id in previous_by_zone:
                raise ValueError("previous_assignments contains a duplicate")
            previous_by_zone[assignment.zone_id] = assignment.satellite_id
        best: dict[str, ZoneServiceCandidate] = {}
        zone_ids = {zone.zone_id for zone in zones}
        for candidate in candidates:
            if not isinstance(candidate, ZoneServiceCandidate):
                raise TypeError("candidates must contain Zone candidate values")
            if candidate.zone_id not in zone_ids:
                raise ValueError("Zone candidate is foreign to PhysicalWorld")
            physical_world.get_satellite(candidate.satellite_id)
            current = best.get(candidate.zone_id)
            if current is None or (
                -candidate.elevation_deg,
                _node_key(candidate.satellite_id),
            ) < (
                -current.elevation_deg,
                _node_key(current.satellite_id),
            ):
                best[candidate.zone_id] = candidate
        assignments = []
        changes = []
        for zone in sorted(zones, key=lambda value: value.zone_id):
            satellite_id = (
                None
                if zone.zone_id not in best
                else best[zone.zone_id].satellite_id
            )
            assignment = ZoneServiceAssignment(
                zone.zone_id,
                satellite_id,
            )
            assignments.append(assignment)
            had_previous = zone.zone_id in previous_by_zone
            previous_satellite_id = previous_by_zone.pop(zone.zone_id, None)
            if not had_previous or previous_satellite_id != satellite_id:
                changes.append(
                    ZoneServiceTransition(
                        zone.zone_id,
                        previous_satellite_id,
                        satellite_id,
                    )
                )
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
    delay_deadband_us: int = 0
    persistence: timedelta = timedelta(0)
    max_staleness: timedelta | None = None

    def __post_init__(self) -> None:
        EffectiveConnectionParameters(
            self.bandwidth_bps, 0, self.packet_loss_ppm
        )
        if (
            isinstance(self.delay_deadband_us, bool)
            or not isinstance(self.delay_deadband_us, int)
            or self.delay_deadband_us < 0
        ):
            raise ValueError("delay_deadband_us must be non-negative")
        if not isinstance(self.persistence, timedelta):
            raise TypeError("persistence must be a timedelta")
        if self.persistence < timedelta(0):
            raise ValueError("persistence must be non-negative")
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

    __slots__ = ("_configuration",)

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
                    identifier, effective, None, None, utc
                )
            else:
                drift = abs(
                    observation.physical_delay_us - prior.effective.delay_us
                )
                stale = (
                    configuration.max_staleness is not None
                    and utc - prior.last_update >= configuration.max_staleness
                )
                if drift <= configuration.delay_deadband_us and not stale:
                    memory = ParameterPlanningMemory(
                        identifier,
                        prior.effective,
                        None,
                        None,
                        prior.last_update,
                    )
                else:
                    pending_since = (
                        prior.pending_since
                        if prior.pending_delay_us == observation.physical_delay_us
                        else utc
                    )
                    ready = (
                        stale
                        or utc - pending_since >= configuration.persistence
                    )
                    if ready:
                        effective = EffectiveConnectionParameters(
                            configuration.bandwidth_bps,
                            observation.physical_delay_us,
                            configuration.packet_loss_ppm,
                        )
                        memory = ParameterPlanningMemory(
                            identifier, effective, None, None, utc
                        )
                    else:
                        memory = ParameterPlanningMemory(
                            identifier,
                            prior.effective,
                            observation.physical_delay_us,
                            pending_since,
                            prior.last_update,
                        )
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
        "_route_engine",
        "_gateway_candidate_count",
        "_gateway_candidate_max_distance_m",
        "_prepared_physical_world",
        "_gateway_candidates_by_zone",
    )

    def __init__(
        self,
        *,
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
        self._route_engine = _GroupedRouteEngine()
        self._gateway_candidate_count = gateway_candidate_count
        self._gateway_candidate_max_distance_m = (
            gateway_candidate_max_distance_m
        )
        self._prepared_physical_world: PhysicalWorld | None = None
        self._gateway_candidates_by_zone = None

    def __del__(self) -> None:
        route_engine = getattr(self, "_route_engine", None)
        if route_engine is not None:
            route_engine.shutdown(wait=False)

    def shutdown(self, *, wait: bool = True) -> None:
        self._route_engine.shutdown(wait=wait)

    def _active_compute_worker_count(self) -> int:
        if not self._route_engine.executor_lifecycle.executor_live:
            return 0
        return self._route_engine.worker_count

    def create_route_demands(
        self,
        physical_world: PhysicalWorld,
        zone_service_assignments: tuple[ZoneServiceAssignment, ...],
    ) -> tuple[RouteDemand, ...]:
        candidates_by_zone = self._prepare_gateway_candidates(physical_world)
        demands = set()
        for assignment in zone_service_assignments:
            for gateway_id in candidates_by_zone.get(
                assignment.zone_id, ()
            ):
                if assignment.satellite_id is not None:
                    demands.add(
                        RouteDemand(
                            assignment.satellite_id,
                            GroundStationRouteDemandTarget(gateway_id),
                        )
                    )
                demands.add(
                    RouteDemand(
                        gateway_id,
                        ZoneRouteDemandTarget(assignment.zone_id),
                    )
                )
        return tuple(sorted(demands, key=_route_demand_key))

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
        context = _prepare_routing_core_context(physical_world, edges)
        assignments = {
            value.zone_id: value.satellite_id
            for value in zone_service_assignments
        }
        ordered_demands = tuple(sorted(demands, key=_route_demand_key))
        requests: list[_RoutePathRequest] = []
        unresolved: dict[int, RouteUnreachableReason] = {}
        for index, demand in enumerate(ordered_demands):
            physical_world.get_node(demand.source)
            if isinstance(demand.target, GroundStationRouteDemandTarget):
                terminal = demand.target.ground_station_id
                physical_world.get_ground_station(terminal)
                requests.append(
                    _RoutePathRequest(index, True, demand.source, terminal)
                )
            else:
                terminal = assignments.get(demand.target.zone_id)
                if terminal is None:
                    unresolved[index] = RouteUnreachableReason.ZONE_UNSERVED
                    continue
                requests.append(
                    _RoutePathRequest(index, False, demand.source, terminal)
                )
        paths = self._route_engine.compute(context, tuple(requests))
        outcomes: list[RouteOutcome] = []
        changes: list[RouteOutcomeTransition] = []
        for index, demand in enumerate(ordered_demands):
            reason = unresolved.get(index)
            path = paths.get(index)
            if reason is not None:
                outcome = UnreachableRouteOutcome(demand, reason)
            elif path is None or len(path) < 2:
                outcome = UnreachableRouteOutcome(
                    demand, RouteUnreachableReason.NO_OPERATIONAL_PATH
                )
            else:
                outcome = ReachableRouteOutcome(demand, path[1], path)
            outcomes.append(outcome)
            previous = previous_by_demand.pop(demand, None)
            if previous != outcome:
                changes.append(
                    RouteOutcomeTransition(demand, previous, outcome)
                )
        changes.extend(
            RouteOutcomeTransition(demand, previous_by_demand[demand], None)
            for demand in sorted(
                previous_by_demand, key=_route_demand_key
            )
        )
        return RoutePlanningResult(tuple(outcomes), tuple(changes))


@dataclass(frozen=True, slots=True)
class TimeHorizonPlanningWindowPolicy:
    """Apply explicit time horizons to lookahead, publication, and prehistory.

    Layer: public Simulator Plan-publication policy implementation. It is
    public because composition roots configure the canonical 50-second/
    20-second window through the PlanningWindowPolicy contract. It is not a
    Timeline, producer cursor, or batch-planning implementation.
    """

    planning_horizon: timedelta = timedelta(seconds=50)
    publication_horizon: timedelta = timedelta(seconds=20)
    maximum_prehistory: timedelta = timedelta(seconds=20)

    def __post_init__(self) -> None:
        for field_name in (
            "planning_horizon",
            "publication_horizon",
            "maximum_prehistory",
        ):
            if not isinstance(getattr(self, field_name), timedelta):
                raise TypeError(f"{field_name} must be a timedelta")
        if self.planning_horizon <= timedelta(0):
            raise ValueError("planning_horizon must be positive")
        if self.publication_horizon <= timedelta(0):
            raise ValueError("publication_horizon must be positive")
        if self.publication_horizon > self.planning_horizon:
            raise ValueError(
                "publication_horizon cannot exceed planning_horizon"
            )
        if self.maximum_prehistory < timedelta(0):
            raise ValueError("maximum_prehistory must be non-negative")

    def required_prehistory(self, acquisition_duration: timedelta) -> timedelta:
        if not isinstance(acquisition_duration, timedelta):
            raise TypeError("acquisition_duration must be a timedelta")
        if acquisition_duration < timedelta(0):
            raise ValueError("acquisition_duration must be non-negative")
        return min(self.maximum_prehistory, acquisition_duration)

    def validate_observation_interval(
        self, observation_interval: timedelta
    ) -> None:
        if not isinstance(observation_interval, timedelta):
            raise TypeError("observation_interval must be a timedelta")
        if observation_interval <= timedelta(0):
            raise ValueError("observation_interval must be positive")
        for field_name in (
            "planning_horizon",
            "publication_horizon",
            "maximum_prehistory",
        ):
            value = getattr(self, field_name)
            if value and value % observation_interval != timedelta(0):
                raise ValueError(
                    f"{field_name} must align to observation_interval"
                )


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
    return _node_key(demand.source), target_key


def _node_key(node_id: NodeId) -> tuple[int, str, str]:
    return node_id.asn, node_id.entity_kind, node_id.entity_id
