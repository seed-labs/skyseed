"""Built-in deterministic geometric Connection algorithm."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from heapq import heappop, heappush, nsmallest
from math import acos, ceil, cos, degrees, floor, isfinite, radians, sin, sqrt
from threading import RLock
from types import MappingProxyType
from weakref import ReferenceType, ref

from skyseed_v2.shared.time import normalize_utc_datetime
from skyseed_v2.shared import NodeId

from ..slicing import (
    Connection,
    ConnectionId,
    ConnectionKind,
    ConnectionSlice,
    DirectionalConnectionParameters,
)
from ..geometry import (
    EarthFixedPosition,
    ecef_distance_m,
    ecef_to_geodetic,
    geodetic_to_ecef,
    has_earth_line_of_sight,
)
from ..topology import (
    GroundStation,
    Orbit,
    Satellite,
    Topology,
    Zone,
)


_NodePair = tuple[NodeId, NodeId]
_LocalUp = tuple[float, float, float]
_GroundAccessCandidate = tuple[
    float,
    tuple[int, str, str],
    NodeId,
    float,
]
_AccessRequest = tuple[NodeId, int]
_SpatialCell = tuple[int, int, int]
_SPATIAL_CELL_SIZE_M = 2_000_000.0
_SPATIAL_QUERY_TOLERANCE_M = 1.0


@dataclass(frozen=True, slots=True)
class _GroundStationSpatialEntry:
    order: int
    ground_station: GroundStation
    position: EarthFixedPosition


@dataclass(frozen=True, slots=True)
class _ZoneSpatialEntry:
    order: int
    prepared_zone: _PreparedZone
    position: EarthFixedPosition


@dataclass(frozen=True, slots=True)
class _GroundStationSpatialIndex:
    entries: tuple[_GroundStationSpatialEntry, ...]
    cells: Mapping[_SpatialCell, tuple[_GroundStationSpatialEntry, ...]]
    minimum_radius_m: float
    maximum_radius_m: float
    maximum_up_deviation_deg: float

    def query(
        self,
        center: EarthFixedPosition,
        radius_m: float,
    ) -> tuple[GroundStation, ...]:
        return tuple(
            entry.ground_station
            for entry in _query_spatial_entries(
                self.cells, center, radius_m
            )
        )


@dataclass(frozen=True, slots=True)
class _ZoneSpatialIndex:
    entries: tuple[_ZoneSpatialEntry, ...]
    cells: Mapping[_SpatialCell, tuple[_ZoneSpatialEntry, ...]]
    minimum_radius_m: float
    maximum_radius_m: float
    maximum_up_deviation_deg: float

    def query(
        self,
        center: EarthFixedPosition,
        radius_m: float,
    ) -> tuple[_PreparedZone, ...]:
        return tuple(
            entry.prepared_zone
            for entry in _query_spatial_entries(
                self.cells, center, radius_m
            )
        )


@dataclass(frozen=True, slots=True)
class _PreparedZone:
    zone: Zone
    representative_position: EarthFixedPosition
    local_up: _LocalUp


@dataclass(frozen=True, slots=True)
class _PreparedStaticGeometry:
    satellites: tuple[Satellite, ...]
    ground_stations: tuple[GroundStation, ...]
    zones_by_id: Mapping[str, _PreparedZone]
    zone_index: _ZoneSpatialIndex | None
    expected_node_ids: frozenset[NodeId]


@dataclass(frozen=True, slots=True)
class _CandidateCheckCounts:
    ground_station_pairs_before: int
    ground_station_pairs_after: int
    zone_pairs_before: int
    zone_pairs_after: int


@dataclass(frozen=True, slots=True)
class _GroundAccessPreference:
    ground_station: GroundStation
    effective_limit: int
    candidates: tuple[_GroundAccessCandidate, ...]


@dataclass(slots=True)
class _MinCostFlowEdge:
    target: int
    reverse_index: int
    residual_capacity: int
    cost: int
    initial_capacity: int


class GeometricConnectionModel:
    """Compute frozen geometric ISL, access and Zone service results.

    Layer: built-in stateful-preparation Simulator Model. Users explicitly
    create it and one Slicer invokes it. It is not a Position source, topology
    owner, fallback or mutable handover history and is public from
    ``skyseed_v2.simulator``.
    """

    __slots__ = (
        "_maximum_isl_distance_m",
        "_ground_station_minimum_elevation_deg",
        "_maximum_ground_station_access_count",
        "_ground_station_maximum_distance_m",
        "_zone_coverage_minimum_elevation_deg",
        "_bandwidth_bps_by_kind",
        "_packet_loss_ppm_by_kind",
        "_propagation_speed_m_per_s",
        "_prepared_topology",
        "_prepared_reference_timestamp",
        "_prepared_isl_pairs",
        "_prepared_static_geometry",
        "_prepared_ground_station_index",
        "_prepared_ground_station_positions",
        "_last_candidate_check_counts",
        "_binding_owner_ref",
        "_binding_topology",
        "_binding_reference_timestamp",
        "_state_lock",
    )

    def __init__(
        self,
        *,
        maximum_isl_distance_m: float,
        ground_station_minimum_elevation_deg: float,
        maximum_ground_station_access_count: int,
        ground_station_maximum_distance_m: float | None,
        zone_coverage_minimum_elevation_deg: float,
        bandwidth_bps_by_kind: Mapping[ConnectionKind, int],
        packet_loss_ppm_by_kind: Mapping[ConnectionKind, int],
        propagation_speed_m_per_s: float,
    ) -> None:
        self._maximum_isl_distance_m = _positive_float(
            maximum_isl_distance_m, "maximum_isl_distance_m"
        )
        self._ground_station_minimum_elevation_deg = _elevation(
            ground_station_minimum_elevation_deg,
            "ground_station_minimum_elevation_deg",
        )
        if (
            isinstance(maximum_ground_station_access_count, bool)
            or not isinstance(maximum_ground_station_access_count, int)
            or maximum_ground_station_access_count < 0
        ):
            raise ValueError(
                "maximum_ground_station_access_count must be a "
                "non-negative integer"
            )
        self._maximum_ground_station_access_count = (
            maximum_ground_station_access_count
        )
        self._ground_station_maximum_distance_m = (
            None
            if ground_station_maximum_distance_m is None
            else _positive_float(
                ground_station_maximum_distance_m,
                "ground_station_maximum_distance_m",
            )
        )
        self._zone_coverage_minimum_elevation_deg = _elevation(
            zone_coverage_minimum_elevation_deg,
            "zone_coverage_minimum_elevation_deg",
        )
        self._bandwidth_bps_by_kind = _validated_kind_mapping(
            bandwidth_bps_by_kind,
            "bandwidth_bps_by_kind",
            minimum=1,
            maximum=None,
        )
        self._packet_loss_ppm_by_kind = _validated_kind_mapping(
            packet_loss_ppm_by_kind,
            "packet_loss_ppm_by_kind",
            minimum=0,
            maximum=1_000_000,
        )
        self._propagation_speed_m_per_s = _positive_float(
            propagation_speed_m_per_s, "propagation_speed_m_per_s"
        )
        self._prepared_topology: Topology | None = None
        self._prepared_reference_timestamp: datetime | None = None
        self._prepared_isl_pairs: tuple[_NodePair, ...] | None = None
        self._prepared_static_geometry: _PreparedStaticGeometry | None = None
        self._prepared_ground_station_index: (
            _GroundStationSpatialIndex | None
        ) = None
        self._prepared_ground_station_positions: (
            Mapping[NodeId, EarthFixedPosition] | None
        ) = None
        self._last_candidate_check_counts: _CandidateCheckCounts | None = None
        self._binding_owner_ref: ReferenceType[object] | None = None
        self._binding_topology: Topology | None = None
        self._binding_reference_timestamp: datetime | None = None
        self._state_lock = RLock()

    def _claim_slicer_context(
        self,
        owner: object,
        topology: Topology,
        reference_timestamp: datetime,
    ) -> None:
        """Bind private prepared state to one exact Slicer running context."""

        if not isinstance(topology, Topology):
            raise TypeError("topology must be a Topology")
        reference_utc = normalize_utc_datetime(
            reference_timestamp, "reference_timestamp"
        )
        owner_reference = ref(owner)
        with self._state_lock:
            self._expire_dead_binding()
            current_owner = (
                None
                if self._binding_owner_ref is None
                else self._binding_owner_ref()
            )
            if current_owner is not None and current_owner is not owner:
                raise ValueError(
                    "GeometricConnectionModel already belongs to another Slicer"
                )
            if current_owner is owner:
                if (
                    self._binding_topology is not topology
                    or self._binding_reference_timestamp != reference_utc
                ):
                    raise ValueError(
                        "Slicer binding cannot change Topology or reference time"
                    )
                return
            self._clear_prepared_state()
            self._prepared_static_geometry = _prepare_static_geometry(topology)
            self._binding_owner_ref = owner_reference
            self._binding_topology = topology
            self._binding_reference_timestamp = reference_utc

    def _release_slicer_context(self, owner: object) -> None:
        """Release a private binding when Slicer construction fails."""

        with self._state_lock:
            current_owner = (
                None
                if self._binding_owner_ref is None
                else self._binding_owner_ref()
            )
            if current_owner is owner:
                self._clear_binding()

    def _prepare_for_slicer(
        self,
        owner: object,
        topology: Topology,
        timestamp: datetime,
        positions: Mapping[NodeId, EarthFixedPosition],
    ) -> None:
        """Prepare stable pairs only from the owning Slicer's first Positions."""

        utc = normalize_utc_datetime(timestamp, "timestamp")
        with self._state_lock:
            self._expire_dead_binding()
            current_owner = (
                None
                if self._binding_owner_ref is None
                else self._binding_owner_ref()
            )
            if current_owner is not owner:
                raise ValueError(
                    "stable pairing preparation requires the owning Slicer"
                )
            if (
                self._binding_topology is not topology
                or self._binding_reference_timestamp != utc
            ):
                raise ValueError(
                    "stable pairing must use exact Topology and Timeline start"
                )
            static_geometry = self._prepared_static_geometry
            if static_geometry is None:
                raise RuntimeError("static Connection geometry was not prepared")
            position_values = _validate_positions(
                static_geometry.expected_node_ids, positions
            )
            self._prepare_ground_station_index(
                static_geometry.ground_stations, position_values
            )
            if self._prepared_topology is None:
                prepared_pairs = _prepare_isl_pairs(
                    topology, position_values
                )
                self._prepared_topology = topology
                self._prepared_reference_timestamp = utc
                self._prepared_isl_pairs = prepared_pairs
            elif (
                self._prepared_topology is not topology
                or self._prepared_reference_timestamp != utc
            ):
                raise ValueError(
                    "stable pairing reference cannot change after preparation"
                )

    def _expire_dead_binding(self) -> None:
        if (
            self._binding_owner_ref is not None
            and self._binding_owner_ref() is None
        ):
            self._clear_binding()

    def _clear_prepared_state(self) -> None:
        self._prepared_topology = None
        self._prepared_reference_timestamp = None
        self._prepared_isl_pairs = None
        self._prepared_static_geometry = None
        self._prepared_ground_station_index = None
        self._prepared_ground_station_positions = None
        self._last_candidate_check_counts = None

    def _clear_binding(self) -> None:
        self._clear_prepared_state()
        self._binding_owner_ref = None
        self._binding_topology = None
        self._binding_reference_timestamp = None

    def _prepare_ground_station_index(
        self,
        ground_stations: tuple[GroundStation, ...],
        positions: Mapping[NodeId, EarthFixedPosition],
    ) -> None:
        current_positions = {
            station.node_id: positions[station.node_id]
            for station in ground_stations
        }
        if self._prepared_ground_station_positions == current_positions:
            return
        self._prepared_ground_station_index = _build_ground_station_index(
            ground_stations, current_positions
        )
        self._prepared_ground_station_positions = MappingProxyType(
            current_positions
        )

    def compute_connection_slice(
        self,
        topology: Topology,
        timestamp: datetime,
        positions: Mapping[NodeId, EarthFixedPosition],
        previous_connection_slice: ConnectionSlice,
    ) -> ConnectionSlice:
        if not isinstance(topology, Topology):
            raise TypeError("topology must be a Topology")
        utc = normalize_utc_datetime(timestamp, "timestamp")
        if not isinstance(previous_connection_slice, ConnectionSlice):
            raise TypeError(
                "previous_connection_slice must be a ConnectionSlice"
        )
        with self._state_lock:
            self._expire_dead_binding()
            bound_owner = (
                None
                if self._binding_owner_ref is None
                else self._binding_owner_ref()
            )
            if bound_owner is not None:
                if self._binding_topology is not topology:
                    raise ValueError(
                        "GeometricConnectionModel is bound to another Topology"
                    )
                static_geometry = self._prepared_static_geometry
                if static_geometry is None:
                    raise RuntimeError("static Connection geometry was not prepared")
                position_values = _validate_positions(
                    static_geometry.expected_node_ids, positions
                )
                prepared_pairs = self._prepared_isl_pairs
                if prepared_pairs is None:
                    # A direct Protocol call cannot persist pairing for an
                    # already claimed Model. Slicer performs the sole formal
                    # preparation immediately before its first activation.
                    prepared_pairs = _prepare_isl_pairs(
                        topology, position_values
                    )
            else:
                if self._prepared_topology is None:
                    static_geometry = _prepare_static_geometry(topology)
                    position_values = _validate_positions(
                        static_geometry.expected_node_ids, positions
                    )
                    prepared_pairs = _prepare_isl_pairs(
                        topology, position_values
                    )
                    self._prepared_topology = topology
                    self._prepared_reference_timestamp = utc
                    self._prepared_isl_pairs = prepared_pairs
                    self._prepared_static_geometry = static_geometry
                elif self._prepared_topology is not topology:
                    raise ValueError(
                        "GeometricConnectionModel cannot be reused with "
                        "another Topology"
                    )
                else:
                    prepared_pairs = self._prepared_isl_pairs
                    static_geometry = self._prepared_static_geometry
                    if static_geometry is None:
                        raise RuntimeError(
                            "static Connection geometry was not prepared"
                        )
                    position_values = _validate_positions(
                        static_geometry.expected_node_ids, positions
                    )
            assert prepared_pairs is not None
            self._prepare_ground_station_index(
                static_geometry.ground_stations, position_values
            )
            ground_station_index = self._prepared_ground_station_index
            assert ground_station_index is not None

        connections: list[Connection] = []
        seen_ids: set[ConnectionId] = set()
        for first, second in prepared_pairs:
            first_position = position_values[first]
            second_position = position_values[second]
            distance = ecef_distance_m(first_position, second_position)
            if (
                distance <= self._maximum_isl_distance_m
                and has_earth_line_of_sight(first_position, second_position)
            ):
                _append_connection(
                    connections,
                    seen_ids,
                    self._create_connection(
                        ConnectionKind.INTER_SATELLITE,
                        first,
                        second,
                        distance,
                    ),
                )

        satellites = static_geometry.satellites
        active_ground_stations = {}
        for ground_station in static_geometry.ground_stations:
            ground_position = position_values[ground_station.node_id]
            site_limit = ground_station.max_simultaneous_satellite_accesses
            effective_limit = self._maximum_ground_station_access_count
            if site_limit is not None:
                effective_limit = min(effective_limit, site_limit)
            if effective_limit == 0:
                continue
            active_ground_stations[ground_station.node_id] = (
                ground_station,
                effective_limit,
                ground_position,
                _local_up(ground_position),
            )
        eligible_by_ground_station: dict[
            NodeId, list[_GroundAccessCandidate]
        ] = {
            node_id: [] for node_id in active_ground_stations
        }
        ground_station_pairs_after = 0
        for satellite in satellites:
            satellite_position = position_values[satellite.node_id]
            search_radius_m = self._ground_station_maximum_distance_m
            if search_radius_m is None:
                search_radius_m = _maximum_eligible_slant_distance_m(
                    satellite_position,
                    self._ground_station_minimum_elevation_deg,
                    ground_station_index.minimum_radius_m,
                    ground_station_index.maximum_radius_m,
                    ground_station_index.maximum_up_deviation_deg,
                )
            for ground_station in ground_station_index.query(
                satellite_position, search_radius_m
            ):
                state = active_ground_stations.get(ground_station.node_id)
                if state is None:
                    continue
                ground_station_pairs_after += 1
                _, _, ground_position, ground_local_up = state
                distance = ecef_distance_m(ground_position, satellite_position)
                elevation = _elevation_deg_with_distance(
                    ground_position,
                    satellite_position,
                    ground_local_up,
                    distance,
                )
                if (
                    elevation
                    >= self._ground_station_minimum_elevation_deg
                    and has_earth_line_of_sight(
                        ground_position, satellite_position
                    )
                    and (
                        self._ground_station_maximum_distance_m is None
                        or distance
                        <= self._ground_station_maximum_distance_m
                    )
                ):
                    eligible_by_ground_station[ground_station.node_id].append(
                        (
                            elevation,
                            _node_key(satellite.node_id),
                            satellite.node_id,
                            distance,
                        )
                    )
        ground_access_preferences = []
        for ground_station in static_geometry.ground_stations:
            state = active_ground_stations.get(ground_station.node_id)
            if state is None:
                continue
            ground_access_preferences.append(
                _GroundAccessPreference(
                    ground_station=ground_station,
                    effective_limit=state[1],
                    candidates=tuple(
                        eligible_by_ground_station[ground_station.node_id]
                    ),
                )
            )

        selected_ground_accesses = _select_ground_station_accesses(
            tuple(ground_access_preferences), satellites
        )
        for preference in ground_access_preferences:
            selected_pairs = selected_ground_accesses.get(
                preference.ground_station.node_id, frozenset()
            )
            selected_candidates = nsmallest(
                len(selected_pairs),
                (
                    candidate
                    for candidate in preference.candidates
                    if candidate[2] in selected_pairs
                ),
                key=lambda value: (-value[0], value[1]),
            )
            for _, _, satellite_id, distance in selected_candidates:
                _append_connection(
                    connections,
                    seen_ids,
                    self._create_connection(
                        ConnectionKind.GROUND_STATION_ACCESS,
                        preference.ground_station.node_id,
                        satellite_id,
                        distance,
                    ),
                )

        assignments, zone_pairs_after = self._assign_zone_service(
            satellites,
            static_geometry.zone_index,
            position_values,
        )
        result = ConnectionSlice(connections, assignments)
        counts = _CandidateCheckCounts(
            ground_station_pairs_before=(
                len(satellites) * len(active_ground_stations)
            ),
            ground_station_pairs_after=ground_station_pairs_after,
            zone_pairs_before=len(satellites) * len(static_geometry.zones_by_id),
            zone_pairs_after=zone_pairs_after,
        )
        with self._state_lock:
            self._last_candidate_check_counts = counts
        return result

    def _create_connection(
        self,
        kind: ConnectionKind,
        first: NodeId,
        second: NodeId,
        distance_m: float,
    ) -> Connection:
        parameters = DirectionalConnectionParameters(
            bandwidth_bps=self._bandwidth_bps_by_kind[kind],
            delay_us=ceil(
                distance_m / self._propagation_speed_m_per_s * 1_000_000.0
            ),
            packet_loss_ppm=self._packet_loss_ppm_by_kind[kind],
        )
        return Connection(
            connection_id=ConnectionId(kind, first, second),
            endpoint_a_to_b=parameters,
            endpoint_b_to_a=parameters,
        )

    def _assign_zone_service(
        self,
        satellites: tuple[Satellite, ...],
        zone_index: _ZoneSpatialIndex | None,
        positions: Mapping[NodeId, EarthFixedPosition],
    ) -> tuple[Mapping[str, NodeId], int]:
        if zone_index is None:
            return MappingProxyType({}), 0
        candidates: dict[
            str, tuple[tuple[float, tuple[int, str, str]], NodeId]
        ] = {}
        pair_checks = 0
        for satellite in satellites:
            satellite_position = positions[satellite.node_id]
            search_radius_m = _maximum_eligible_slant_distance_m(
                satellite_position,
                self._zone_coverage_minimum_elevation_deg,
                zone_index.minimum_radius_m,
                zone_index.maximum_radius_m,
                zone_index.maximum_up_deviation_deg,
            )
            for prepared_zone in zone_index.query(
                satellite_position, search_radius_m
            ):
                pair_checks += 1
                representative_position = (
                    prepared_zone.representative_position
                )
                distance = ecef_distance_m(
                    representative_position, satellite_position
                )
                representative_elevation = _elevation_deg_with_distance(
                    representative_position,
                    satellite_position,
                    prepared_zone.local_up,
                    distance,
                )
                if (
                    representative_elevation
                    >= self._zone_coverage_minimum_elevation_deg
                    and has_earth_line_of_sight(
                        representative_position, satellite_position
                    )
                ):
                    rank = (
                        -representative_elevation,
                        _node_key(satellite.node_id),
                    )
                    current = candidates.get(prepared_zone.zone.zone_id)
                    if current is None or rank < current[0]:
                        candidates[prepared_zone.zone.zone_id] = (
                            rank,
                            satellite.node_id,
                        )

        assignments = {
            zone_id: ranked[1]
            for zone_id, ranked in candidates.items()
        }
        return MappingProxyType(dict(sorted(assignments.items()))), pair_checks


def _prepare_isl_pairs(
    topology: Topology,
    positions: Mapping[NodeId, EarthFixedPosition],
) -> tuple[_NodePair, ...]:
    pairs: set[_NodePair] = set()
    for autonomous_system in topology.autonomous_systems:
        for shell in autonomous_system.shells:
            orbits = tuple(shell.orbits)
            for orbit in orbits:
                ordered_satellites = _ordered_orbit_satellites(orbit)
                count = len(ordered_satellites)
                if count == 2:
                    pairs.add(_pair_ids(*ordered_satellites))
                elif count >= 3:
                    for index, satellite in enumerate(ordered_satellites):
                        pairs.add(
                            _pair_ids(
                                satellite,
                                ordered_satellites[(index + 1) % count],
                            )
                        )

            if len(orbits) <= 1:
                continue
            if any(orbit.inclination_deg in (0.0, 180.0) for orbit in orbits):
                raise ValueError(
                    "multi-Orbit Shell inclination cannot be 0 or 180 degrees"
                )
            ordered_orbits = tuple(
                sorted(orbits, key=lambda value: value.right_ascension_deg)
            )
            right_ascensions = [
                orbit.right_ascension_deg for orbit in ordered_orbits
            ]
            if len(set(right_ascensions)) != len(right_ascensions):
                raise ValueError("adjacent-Orbit preparation requires unique RAAN")
            orbit_pairs = (
                ((ordered_orbits[0], ordered_orbits[1]),)
                if len(ordered_orbits) == 2
                else tuple(
                    (
                        orbit,
                        ordered_orbits[(index + 1) % len(ordered_orbits)],
                    )
                    for index, orbit in enumerate(ordered_orbits)
                )
            )
            for first_orbit, second_orbit in orbit_pairs:
                first = _ordered_orbit_satellites(first_orbit)
                second = _ordered_orbit_satellites(second_orbit)
                if len(first) != len(second):
                    raise ValueError(
                        "adjacent Orbits must contain equal Satellite counts"
                    )
                offset = _best_cyclic_offset(first, second, positions)
                for index, first_satellite in enumerate(first):
                    pairs.add(
                        _pair_ids(
                            first_satellite,
                            second[(index + offset) % len(second)],
                        )
                    )
    return tuple(
        sorted(
            pairs,
            key=lambda pair: (_node_key(pair[0]), _node_key(pair[1])),
        )
    )


def _select_ground_station_accesses(
    preferences: tuple[_GroundAccessPreference, ...],
    satellites: tuple[Satellite, ...],
) -> Mapping[NodeId, frozenset[NodeId]]:
    """Allocate quality-first primary access, then residual redundancy."""

    satellite_capacities = {
        satellite.node_id: satellite.max_simultaneous_ground_station_accesses
        for satellite in sorted(satellites, key=lambda value: _node_key(value.node_id))
    }
    if all(value is None for value in satellite_capacities.values()):
        return MappingProxyType(
            {
                preference.ground_station.node_id: frozenset(
                    candidate[2]
                    for candidate in nsmallest(
                        preference.effective_limit,
                        preference.candidates,
                        key=lambda value: (-value[0], value[1]),
                    )
                )
                for preference in sorted(
                    preferences,
                    key=lambda value: _node_key(value.ground_station.node_id),
                )
            }
        )

    ordered_preferences = tuple(
        sorted(
            preferences,
            key=lambda value: _node_key(value.ground_station.node_id),
        )
    )
    ordered_candidates_by_ground_station: dict[
        NodeId, tuple[_GroundAccessCandidate, ...]
    ] = {
        value.ground_station.node_id: tuple(
            sorted(
                value.candidates,
                key=lambda candidate: (-candidate[0], candidate[1]),
            )
        )
        for value in ordered_preferences
    }
    finite_default = len(ordered_preferences)
    capacity_by_satellite = {
        node_id: finite_default if value is None else value
        for node_id, value in satellite_capacities.items()
    }
    primary_by_ground_station = _select_primary_ground_station_accesses(
        ordered_candidates_by_ground_station,
        capacity_by_satellite,
    )
    selected: dict[NodeId, set[NodeId]] = {
        value.ground_station.node_id: set() for value in ordered_preferences
    }
    frozen_primary_count_by_satellite: dict[NodeId, int] = {
        node_id: 0 for node_id in capacity_by_satellite
    }
    for ground_station_id, satellite_id in primary_by_ground_station.items():
        selected[ground_station_id].add(satellite_id)
        frozen_primary_count_by_satellite[satellite_id] += 1

    assigned_redundancy: dict[_AccessRequest, NodeId] = {}
    redundant_requests_by_satellite: dict[NodeId, list[_AccessRequest]] = {
        node_id: [] for node_id in capacity_by_satellite
    }
    used_by_ground_station: dict[NodeId, set[NodeId]] = {
        node_id: set(values) for node_id, values in selected.items()
    }

    def request_key(request: _AccessRequest) -> tuple[int, tuple[int, str, str]]:
        return request[1], _node_key(request[0])

    def move(request: _AccessRequest, satellite_id: NodeId) -> None:
        previous = assigned_redundancy.get(request)
        if previous is not None:
            redundant_requests_by_satellite[previous].remove(request)
            used_by_ground_station[request[0]].remove(previous)
        assigned_redundancy[request] = satellite_id
        redundant_requests_by_satellite[satellite_id].append(request)
        redundant_requests_by_satellite[satellite_id].sort(key=request_key)
        used_by_ground_station[request[0]].add(satellite_id)

    def augment(
        request: _AccessRequest,
        visited_requests: set[_AccessRequest],
        visited_satellites: set[NodeId],
    ) -> bool:
        if request in visited_requests:
            return False
        visited_requests.add(request)
        ground_station_id = request[0]
        current = assigned_redundancy.get(request)
        for candidate in ordered_candidates_by_ground_station[
            ground_station_id
        ]:
            satellite_id = candidate[2]
            if (
                satellite_id == current
                or satellite_id in visited_satellites
                or satellite_id in used_by_ground_station[ground_station_id]
            ):
                continue
            visited_satellites.add(satellite_id)
            occupants = redundant_requests_by_satellite[satellite_id]
            if (
                frozen_primary_count_by_satellite[satellite_id]
                + len(occupants)
                < capacity_by_satellite[satellite_id]
            ):
                move(request, satellite_id)
                return True
            # Frozen primaries never enter this movable occupant collection.
            for occupant in sorted(
                tuple(occupants), key=request_key, reverse=True
            ):
                if augment(occupant, visited_requests, visited_satellites):
                    move(request, satellite_id)
                    return True
        return False

    maximum_limit = max(
        (value.effective_limit for value in ordered_preferences), default=0
    )
    for slot in range(1, maximum_limit):
        requests = tuple(
            (value.ground_station.node_id, slot)
            for value in ordered_preferences
            if (
                slot < value.effective_limit
                and value.ground_station.node_id in primary_by_ground_station
            )
        )
        for request in reversed(
            sorted(requests, key=lambda value: _node_key(value[0]))
        ):
            augment(request, set(), set())

    for request, satellite_id in assigned_redundancy.items():
        selected[request[0]].add(satellite_id)
    return MappingProxyType(
        {
            node_id: frozenset(values)
            for node_id, values in selected.items()
        }
    )


def _select_primary_ground_station_accesses(
    candidates_by_ground_station: Mapping[
        NodeId, tuple[_GroundAccessCandidate, ...]
    ],
    capacity_by_satellite: Mapping[NodeId, int],
) -> Mapping[NodeId, NodeId]:
    """Return a maximum-cardinality, minimum-aggregate-rank matching."""

    ground_station_ids = tuple(
        sorted(candidates_by_ground_station, key=_node_key)
    )
    satellite_ids = tuple(sorted(capacity_by_satellite, key=_node_key))
    ground_index = {
        node_id: index for index, node_id in enumerate(ground_station_ids)
    }
    satellite_index = {
        node_id: len(ground_station_ids) + index
        for index, node_id in enumerate(satellite_ids)
    }
    satellite_order = {
        node_id: index for index, node_id in enumerate(satellite_ids)
    }
    rank_scale = len(ground_station_ids) * max(1, len(satellite_ids)) + 1
    source = len(ground_station_ids) + len(satellite_ids)
    sink = source + 1
    graph: list[list[_MinCostFlowEdge]] = [
        [] for _ in range(sink + 1)
    ]

    def add_edge(
        first: int,
        second: int,
        capacity: int,
        cost: int,
    ) -> _MinCostFlowEdge:
        forward = _MinCostFlowEdge(
            second,
            len(graph[second]),
            capacity,
            cost,
            capacity,
        )
        reverse = _MinCostFlowEdge(
            first,
            len(graph[first]),
            0,
            -cost,
            0,
        )
        graph[first].append(forward)
        graph[second].append(reverse)
        return forward

    for ground_station_id in ground_station_ids:
        add_edge(source, ground_index[ground_station_id], 1, 0)
    for satellite_id in satellite_ids:
        add_edge(
            satellite_index[satellite_id],
            sink,
            capacity_by_satellite[satellite_id],
            0,
        )

    candidate_edges: dict[
        tuple[NodeId, NodeId], _MinCostFlowEdge
    ] = {}
    for ground_station_id in ground_station_ids:
        for rank, candidate in enumerate(
            candidates_by_ground_station[ground_station_id], start=1
        ):
            satellite_id = candidate[2]
            candidate_edges[(ground_station_id, satellite_id)] = add_edge(
                ground_index[ground_station_id],
                satellite_index[satellite_id],
                1,
                rank * rank_scale + satellite_order[satellite_id],
            )

    _run_min_cost_maximum_flow(graph, source, sink)
    selected = {
        ground_station_id: satellite_id
        for (ground_station_id, satellite_id), edge in candidate_edges.items()
        if edge.initial_capacity == 1 and edge.residual_capacity == 0
    }
    return MappingProxyType(
        dict(sorted(selected.items(), key=lambda item: _node_key(item[0])))
    )


def _run_min_cost_maximum_flow(
    graph: list[list[_MinCostFlowEdge]],
    source: int,
    sink: int,
) -> None:
    """Exhaust deterministic shortest residual augmenting paths."""

    node_count = len(graph)
    potential = [0] * node_count
    infinity = 1 << 60
    while True:
        distances = [infinity] * node_count
        predecessors: list[tuple[int, int] | None] = [None] * node_count
        distances[source] = 0
        pending = [(0, source)]
        while pending:
            distance, node = heappop(pending)
            if distance != distances[node]:
                continue
            for edge_index, edge in enumerate(graph[node]):
                if edge.residual_capacity == 0:
                    continue
                candidate_distance = (
                    distance
                    + edge.cost
                    + potential[node]
                    - potential[edge.target]
                )
                if candidate_distance >= distances[edge.target]:
                    continue
                distances[edge.target] = candidate_distance
                predecessors[edge.target] = (node, edge_index)
                heappush(pending, (candidate_distance, edge.target))
        if predecessors[sink] is None:
            return
        for node, distance in enumerate(distances):
            if distance != infinity:
                potential[node] += distance

        node = sink
        while node != source:
            previous, edge_index = predecessors[node]
            edge = graph[previous][edge_index]
            edge.residual_capacity -= 1
            graph[node][edge.reverse_index].residual_capacity += 1
            node = previous


def _ordered_orbit_satellites(orbit: Orbit) -> tuple[Satellite, ...]:
    satellites = tuple(
        sorted(
            orbit.satellites,
            key=lambda value: (
                value.mean_anomaly_deg,
                _node_key(value.node_id),
            ),
        )
    )
    anomalies = [value.mean_anomaly_deg for value in satellites]
    if len(set(anomalies)) != len(anomalies):
        raise ValueError("Orbit Satellites must have unique mean_anomaly_deg")
    return satellites


def _best_cyclic_offset(
    first: tuple[Satellite, ...],
    second: tuple[Satellite, ...],
    positions: Mapping[NodeId, EarthFixedPosition],
) -> int:
    if not first:
        raise ValueError("Orbit must contain at least one Satellite")
    scores = []
    for offset in range(len(first)):
        distances = tuple(
            ecef_distance_m(
                positions[satellite.node_id],
                positions[second[(index + offset) % len(second)].node_id],
            )
            for index, satellite in enumerate(first)
        )
        scores.append((max(distances), sum(distances), offset))
    return min(scores)[2]


def _prepare_static_geometry(topology: Topology) -> _PreparedStaticGeometry:
    satellites, ground_stations = _spatial_nodes(topology)
    try:
        zones = topology.get_zone_layout().zones
    except RuntimeError:
        zones = ()
    prepared_zones = {}
    for zone in zones:
        latitude, longitude = zone.representative_point
        representative_position = geodetic_to_ecef(
            latitude, longitude, 0.0
        )
        prepared_zones[zone.zone_id] = _PreparedZone(
            zone=zone,
            representative_position=representative_position,
            local_up=_local_up(representative_position),
        )
    ordered_prepared_zones = tuple(
        prepared_zones[zone_id] for zone_id in sorted(prepared_zones)
    )
    return _PreparedStaticGeometry(
        satellites=satellites,
        ground_stations=ground_stations,
        zones_by_id=MappingProxyType(prepared_zones),
        zone_index=(
            _build_zone_index(ordered_prepared_zones)
            if ordered_prepared_zones
            else None
        ),
        expected_node_ids=frozenset(
            node.node_id for node in (*satellites, *ground_stations)
        ),
    )


def _build_ground_station_index(
    ground_stations: tuple[GroundStation, ...],
    positions: Mapping[NodeId, EarthFixedPosition],
) -> _GroundStationSpatialIndex:
    entries = tuple(
        _GroundStationSpatialEntry(
            order=index,
            ground_station=station,
            position=positions[station.node_id],
        )
        for index, station in enumerate(ground_stations)
    )
    minimum_radius, maximum_radius, maximum_deviation = (
        _spatial_radius_bounds(tuple(entry.position for entry in entries))
    )
    cells: dict[_SpatialCell, list[_GroundStationSpatialEntry]] = {}
    for entry in entries:
        cells.setdefault(_spatial_cell(entry.position), []).append(entry)
    return _GroundStationSpatialIndex(
        entries=entries,
        cells=MappingProxyType(
            {cell: tuple(values) for cell, values in cells.items()}
        ),
        minimum_radius_m=minimum_radius,
        maximum_radius_m=maximum_radius,
        maximum_up_deviation_deg=maximum_deviation,
    )


def _build_zone_index(
    prepared_zones: tuple[_PreparedZone, ...],
) -> _ZoneSpatialIndex:
    entries = tuple(
        _ZoneSpatialEntry(
            order=index,
            prepared_zone=prepared_zone,
            position=prepared_zone.representative_position,
        )
        for index, prepared_zone in enumerate(prepared_zones)
    )
    minimum_radius, maximum_radius, maximum_deviation = (
        _spatial_radius_bounds(tuple(entry.position for entry in entries))
    )
    cells: dict[_SpatialCell, list[_ZoneSpatialEntry]] = {}
    for entry in entries:
        cells.setdefault(_spatial_cell(entry.position), []).append(entry)
    return _ZoneSpatialIndex(
        entries=entries,
        cells=MappingProxyType(
            {cell: tuple(values) for cell, values in cells.items()}
        ),
        minimum_radius_m=minimum_radius,
        maximum_radius_m=maximum_radius,
        maximum_up_deviation_deg=maximum_deviation,
    )


def _query_spatial_entries(
    cells: Mapping[
        _SpatialCell,
        tuple[_GroundStationSpatialEntry, ...]
        | tuple[_ZoneSpatialEntry, ...],
    ],
    center: EarthFixedPosition,
    radius_m: float,
) -> tuple[_GroundStationSpatialEntry | _ZoneSpatialEntry, ...]:
    radius = max(0.0, radius_m) + _SPATIAL_QUERY_TOLERANCE_M
    x_start = floor((center.x_m - radius) / _SPATIAL_CELL_SIZE_M)
    x_end = floor((center.x_m + radius) / _SPATIAL_CELL_SIZE_M)
    y_start = floor((center.y_m - radius) / _SPATIAL_CELL_SIZE_M)
    y_end = floor((center.y_m + radius) / _SPATIAL_CELL_SIZE_M)
    z_start = floor((center.z_m - radius) / _SPATIAL_CELL_SIZE_M)
    z_end = floor((center.z_m + radius) / _SPATIAL_CELL_SIZE_M)
    radius_squared = radius * radius
    candidates = []
    for x_index in range(x_start, x_end + 1):
        for y_index in range(y_start, y_end + 1):
            for z_index in range(z_start, z_end + 1):
                for entry in cells.get((x_index, y_index, z_index), ()):
                    position = entry.position
                    if (
                        (position.x_m - center.x_m) ** 2
                        + (position.y_m - center.y_m) ** 2
                        + (position.z_m - center.z_m) ** 2
                        <= radius_squared
                    ):
                        candidates.append(entry)
    return tuple(sorted(candidates, key=lambda entry: entry.order))


def _spatial_cell(position: EarthFixedPosition) -> _SpatialCell:
    return (
        floor(position.x_m / _SPATIAL_CELL_SIZE_M),
        floor(position.y_m / _SPATIAL_CELL_SIZE_M),
        floor(position.z_m / _SPATIAL_CELL_SIZE_M),
    )


def _spatial_radius_bounds(
    positions: tuple[EarthFixedPosition, ...],
) -> tuple[float, float, float]:
    if not positions:
        return 0.0, 0.0, 0.0
    radii = []
    maximum_deviation = 0.0
    for position in positions:
        radius = _position_radius_m(position)
        if radius == 0.0:
            return 0.0, 0.0, 180.0
        radii.append(radius)
        local_up = _local_up(position)
        radial_up = (
            position.x_m / radius,
            position.y_m / radius,
            position.z_m / radius,
        )
        cosine = sum(
            first * second
            for first, second in zip(local_up, radial_up, strict=True)
        )
        maximum_deviation = max(
            maximum_deviation,
            degrees(acos(min(1.0, max(-1.0, cosine)))),
        )
    return min(radii), max(radii), maximum_deviation


def _maximum_eligible_slant_distance_m(
    satellite_position: EarthFixedPosition,
    minimum_elevation_deg: float,
    minimum_ground_radius_m: float,
    maximum_ground_radius_m: float,
    maximum_up_deviation_deg: float,
) -> float:
    satellite_radius = _position_radius_m(satellite_position)
    if minimum_ground_radius_m <= 0.0 or maximum_ground_radius_m <= 0.0:
        return 0.0
    radial_elevation_deg = (
        minimum_elevation_deg - maximum_up_deviation_deg
    )
    if radial_elevation_deg <= 0.0:
        return satellite_radius + maximum_ground_radius_m
    radial_elevation = radians(radial_elevation_deg)
    radicand = (
        satellite_radius**2
        - minimum_ground_radius_m**2 * cos(radial_elevation) ** 2
    )
    if radicand <= 0.0:
        return satellite_radius + maximum_ground_radius_m
    maximum_distance = (
        -minimum_ground_radius_m * sin(radial_elevation)
        + sqrt(radicand)
    )
    if maximum_distance < 0.0:
        return satellite_radius + maximum_ground_radius_m
    return maximum_distance


def _position_radius_m(position: EarthFixedPosition) -> float:
    return sqrt(
        position.x_m**2 + position.y_m**2 + position.z_m**2
    )


def _local_up(position: EarthFixedPosition) -> _LocalUp:
    latitude_deg, longitude_deg, _ = ecef_to_geodetic(position)
    latitude = radians(latitude_deg)
    longitude = radians(longitude_deg)
    return (
        cos(latitude) * cos(longitude),
        cos(latitude) * sin(longitude),
        sin(latitude),
    )


def _elevation_deg_with_distance(
    observer: EarthFixedPosition,
    target: EarthFixedPosition,
    local_up: _LocalUp,
    distance_m: float,
) -> float:
    if distance_m == 0.0:
        raise ValueError("elevation is undefined for coincident positions")
    delta = (
        target.x_m - observer.x_m,
        target.y_m - observer.y_m,
        target.z_m - observer.z_m,
    )
    sine_elevation = sum(
        direction * up
        for direction, up in zip(delta, local_up, strict=True)
    ) / distance_m
    return 90.0 - degrees(
        acos(min(1.0, max(-1.0, sine_elevation)))
    )


def _validate_positions(
    expected_node_ids: frozenset[NodeId],
    positions: Mapping[NodeId, EarthFixedPosition],
) -> Mapping[NodeId, EarthFixedPosition]:
    if not isinstance(positions, Mapping):
        raise TypeError("positions must be a Mapping")
    if set(positions) != expected_node_ids:
        missing = expected_node_ids - set(positions)
        foreign = set(positions) - expected_node_ids
        raise ValueError(
            "positions must contain exactly all Topology nodes "
            f"(missing={len(missing)}, foreign={len(foreign)})"
        )
    for node_id, position in positions.items():
        if not isinstance(node_id, NodeId):
            raise TypeError("positions keys must be NodeId values")
        if not isinstance(position, EarthFixedPosition):
            raise TypeError("positions values must be EarthFixedPosition values")
    return positions


def _spatial_nodes(
    topology: Topology,
) -> tuple[tuple[Satellite, ...], tuple[GroundStation, ...]]:
    satellites = []
    ground_stations = []
    for autonomous_system in topology.autonomous_systems:
        for shell in autonomous_system.shells:
            for orbit in shell.orbits:
                satellites.extend(orbit.satellites)
        ground_stations.extend(autonomous_system.ground_stations)
    return (
        tuple(sorted(satellites, key=lambda value: _node_key(value.node_id))),
        tuple(
            sorted(ground_stations, key=lambda value: _node_key(value.node_id))
        ),
    )


def _append_connection(
    connections: list[Connection],
    seen_ids: set[ConnectionId],
    connection: Connection,
) -> None:
    if connection.connection_id in seen_ids:
        raise ValueError(
            f"duplicate effective Connection: {connection.connection_id!r}"
        )
    seen_ids.add(connection.connection_id)
    connections.append(connection)


def _pair_ids(first: Satellite, second: Satellite) -> _NodePair:
    if _node_key(first.node_id) <= _node_key(second.node_id):
        return first.node_id, second.node_id
    return second.node_id, first.node_id


def _node_key(node_id: NodeId) -> tuple[int, str, str]:
    return node_id.asn, node_id.entity_kind, node_id.entity_id


def _positive_float(value: float, field_name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a positive finite number")
    converted = float(value)
    if not isfinite(converted) or converted <= 0.0:
        raise ValueError(f"{field_name} must be a positive finite number")
    return converted


def _elevation(value: float, field_name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be between -90 and 90")
    converted = float(value)
    if not isfinite(converted) or not -90.0 <= converted <= 90.0:
        raise ValueError(f"{field_name} must be between -90 and 90")
    return converted


def _validated_kind_mapping(
    values: Mapping[ConnectionKind, int],
    field_name: str,
    *,
    minimum: int,
    maximum: int | None,
) -> Mapping[ConnectionKind, int]:
    if not isinstance(values, Mapping):
        raise TypeError(f"{field_name} must be a Mapping")
    if set(values) != set(ConnectionKind):
        raise ValueError(
            f"{field_name} must contain exactly every ConnectionKind"
        )
    copied = {}
    for kind, value in values.items():
        if (
            not isinstance(kind, ConnectionKind)
            or isinstance(value, bool)
            or not isinstance(value, int)
            or value < minimum
            or (maximum is not None and value > maximum)
        ):
            raise ValueError(f"{field_name} contains an invalid value")
        copied[kind] = value
    return MappingProxyType(copied)


__all__ = ["GeometricConnectionModel"]
