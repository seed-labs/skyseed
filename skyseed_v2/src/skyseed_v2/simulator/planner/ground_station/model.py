"""Ground-station access contract and coverage-first allocation."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol, runtime_checkable
from skyseed_v2.shared import NodeId
from collections.abc import Mapping
from types import MappingProxyType
from ...physical import PhysicalWorld, GroundStationAccessCandidate
from .._durations import _validate_service_duration, _required_contact_duration, _has_useful_contact
from ..selection import ConnectionSelectionState
from .._scopes import _validated_satellite_scopes, _validate_scope_world
from ..values import _node_key, OperationalConnectionId, OperationalConnectionKind
from ._allocation import (
    _GroundAccessAllocationPreference,
    _allocate_continuous_ground_station_accesses,
)


@runtime_checkable
class GroundStationAccessPlanningModel(Protocol):
    """Define the replaceable GroundStation access-selection policy.

    Layer: public Simulator planning Model contract. It is public because
    callers may inject GroundStation policy independently of the other Planner
    roles. It is not physical visibility geometry, ISL acquisition, or Zone
    service planning.

    Optional read-only ``allocation_statistics()`` returns named numeric work
    counters from the last selection. Planner queries it only when recording;
    custom Models need not provide it or expose any private counter fields.
    """

    def select_ground_station_accesses(
        self,
        physical_world: PhysicalWorld,
        candidates: tuple[GroundStationAccessCandidate, ...],
        state: ConnectionSelectionState,
    ) -> tuple[GroundStationAccessCandidate, ...]:
        """Select GS candidates for preparation or continued operation."""


@dataclass(frozen=True, slots=True)
class GroundStationCandidateSearch:
    """Model advice for the physical per-station candidate count; None is unrestricted.

    Layer: Planner Model value. It is public because Models create advice for
    Planner and Producer queries. It is not a selection or physical fact.
    """

    candidate_limit: int | None

    def __post_init__(self):
        value = self.candidate_limit
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 1):
            raise ValueError("candidate_limit must be positive or None")


@runtime_checkable
class GroundStationCandidateSearchModel(Protocol):
    """Optional query optimization; selection remains independently replaceable.

    Layer: Planner Model capability. It is public because custom Model authors
    implement search advice used by Planner. It is not a required selection
    interface or physical query executor.
    """

    def initial_candidate_search(self, physical_world: PhysicalWorld, *,
            minimum_contact_duration: timedelta) -> GroundStationCandidateSearch:
        """Advise the initial count for this world's admission requirement."""

    def expand_candidate_search(self, physical_world: PhysicalWorld, *,
            search: GroundStationCandidateSearch, minimum_contact_duration: timedelta,
            state: ConnectionSelectionState,
            returned_candidates: tuple[GroundStationAccessCandidate, ...],
    ) -> GroundStationCandidateSearch | None:
        """Return wider advice (possibly unrestricted), or None when complete.

        Planner supplies lifecycle state and physically returned candidates
        from the same snapshot; this Model assesses its own eligibility. Advice must preserve physical coverage; Planner
        adds retained relationships and Producer performs the expanded query.
        """


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
                 minimum_service_duration: timedelta = timedelta(0)) -> None:
        if (
            isinstance(default_ground_station_limit, bool)
            or not isinstance(default_ground_station_limit, int)
            or default_ground_station_limit < 0
        ):
            raise ValueError(
                "default_ground_station_limit must be non-negative"
            )
        _validate_service_duration(minimum_service_duration)
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

    def required_contact_duration(self, *, acquisition_duration, observation_interval):
        return _required_contact_duration(acquisition_duration, observation_interval, self.minimum_service_duration)

    def _eligible_candidates(self, candidates, state):
        return tuple(candidate for candidate in candidates if _has_useful_contact(candidate,
            OperationalConnectionId(OperationalConnectionKind.GROUND_STATION_ACCESS,
                candidate.ground_station_id, candidate.satellite_id), state, self.minimum_service_duration))

    def allocation_statistics(self) -> Mapping[str, int]:
        """Read the most recent selection's bounded work counters without mutation."""
        return MappingProxyType(self._allocation_work)

    def describe_configuration(self):
        return {
            "default_ground_station_limit": self._default_ground_station_limit,
            "ground_station_policy_limit": self._ground_station_policy_limit,
            "initial_candidate_count": self.initial_candidate_count,
            "minimum_service_duration": self.minimum_service_duration,
            "satellite_scopes": self._satellite_scopes,
            "ground_station_scope_limits": self._scope_limits,
        }

    def initial_candidate_search(self, physical_world, *, minimum_contact_duration):
        self._prepare_static_limits(physical_world)
        dependency = self.initial_candidate_count, minimum_contact_duration
        limit = self.initial_candidate_count
        if self._search_hint is not None and self._search_hint[0] == dependency:
            limit = self._search_hint[1]
        return GroundStationCandidateSearch(limit)

    def expand_candidate_search(self, physical_world, *, search,
            minimum_contact_duration, state, returned_candidates):
        admissible_candidates = self._eligible_candidates(returned_candidates, state)
        limit = search.candidate_limit
        if limit is None:
            return None
        if not self._candidate_search_needs_expansion(physical_world,
                admissible_candidates, returned_candidates, limit):
            self._search_hint = (self.initial_candidate_count, minimum_contact_duration), limit
            return None
        total = len(self._satellite_limits)
        return GroundStationCandidateSearch(limit * 2 if limit * 2 < total else None)

    def select_ground_station_accesses(
        self,
        physical_world: PhysicalWorld,
        candidates: tuple[GroundStationAccessCandidate, ...],
        state: ConnectionSelectionState,
    ) -> tuple[GroundStationAccessCandidate, ...]:
        if not isinstance(physical_world, PhysicalWorld):
            raise TypeError("physical_world must be a PhysicalWorld")
        self._prepare_static_limits(physical_world)
        retained_pairs = frozenset((key.endpoint_a, key.endpoint_b) if key.endpoint_a.entity_kind == "ground-station"
                                   else (key.endpoint_b, key.endpoint_a) for key in state.selected_since)
        candidates = self._eligible_candidates(candidates, state)
        by_station = {}
        by_pair: dict[tuple[NodeId, NodeId], GroundStationAccessCandidate] = {}
        for candidate in candidates:
            if not isinstance(candidate, GroundStationAccessCandidate):
                raise TypeError("candidates must contain GS candidate values")
            if candidate.elevation_deg is None:
                raise ValueError("CoverageFirstGroundStationAccessPlanningModel requires elevation_deg")
            if candidate.ground_station_id not in self._station_limit_by_id:
                physical_world.get_ground_station(candidate.ground_station_id)
            if candidate.satellite_id not in self._satellite_limits:
                physical_world.get_satellite(candidate.satellite_id)
            key = self._satellite_scopes.get(candidate.satellite_id, ""), candidate.ground_station_id
            by_station.setdefault(key, []).append(candidate)
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
                    tuple(by_station.get((scope, station), ())))
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
        # Only explicit physical feedback establishes exhaustion. Here the
        # Model may stop early only when its allocation goals are satisfied.
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
