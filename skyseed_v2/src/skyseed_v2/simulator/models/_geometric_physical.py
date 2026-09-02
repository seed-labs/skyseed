"""Share architecture-neutral geometric physical-link evaluation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from math import acos, ceil, cos, degrees, floor, isfinite, radians, sin, sqrt
from types import MappingProxyType

from skyseed_v2.shared import NodeId
from skyseed_v2.shared.time import normalize_utc_datetime

from ..geometry import (
    EarthFixedPosition,
    ecef_distance_m,
    geodetic_to_ecef,
    has_earth_line_of_sight,
)
from ..geometry._elevation import (
    _elevation_deg_from_local_up_and_distance,
    _wgs84_local_up_unit_vector,
)
from ..physical.physical_link import (
    GroundStationAccessCandidate,
    InterSatelliteLinkCandidate,
    PhysicalLinkObservation,
    PhysicalLinkRequest,
    PhysicalLinkWorkCounts,
    ZoneServiceCandidate,
)
from ..physical.world import (
    GroundStation,
    Orbit,
    Satellite,
    PhysicalWorld,
)
from ..physical.zone import Zone


_NodePair = tuple[NodeId, NodeId]
_LocalUp = tuple[float, float, float]
_SpatialCell = tuple[int, int, int]
_ShellKey = tuple[int, str]
_OrbitKey = tuple[int, str, str]
_SPATIAL_CELL_SIZE_M = 2_000_000.0
_SPATIAL_QUERY_TOLERANCE_M = 1.0


@dataclass(frozen=True, slots=True)
class _GroundStationSpatialEntry:
    order: int
    ground_station: GroundStation
    position: EarthFixedPosition


@dataclass(frozen=True, slots=True)
class _PreparedZone:
    zone: Zone
    representative_position: EarthFixedPosition
    local_up: _LocalUp


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
        self, center: EarthFixedPosition, radius_m: float
    ) -> tuple[GroundStation, ...]:
        return tuple(
            entry.ground_station
            for entry in _query_spatial_entries(self.cells, center, radius_m)
        )


@dataclass(frozen=True, slots=True)
class _ZoneSpatialIndex:
    entries: tuple[_ZoneSpatialEntry, ...]
    cells: Mapping[_SpatialCell, tuple[_ZoneSpatialEntry, ...]]
    minimum_radius_m: float
    maximum_radius_m: float
    maximum_up_deviation_deg: float

    def query(
        self, center: EarthFixedPosition, radius_m: float
    ) -> tuple[_PreparedZone, ...]:
        return tuple(
            entry.prepared_zone
            for entry in _query_spatial_entries(self.cells, center, radius_m)
        )


@dataclass(frozen=True, slots=True)
class _SatellitePlane:
    shell_key: _ShellKey
    orbit_key: _OrbitKey
    plane_index: int
    plane_count: int


@dataclass(frozen=True, slots=True)
class _PreparedStaticGeometry:
    satellites: tuple[Satellite, ...]
    ground_stations: tuple[GroundStation, ...]
    zones: tuple[_PreparedZone, ...]
    zone_index: _ZoneSpatialIndex | None
    satellite_planes: Mapping[NodeId, _SatellitePlane]
    expected_node_ids: frozenset[NodeId]


@dataclass(frozen=True, slots=True)
class _GeometricPhysicalConfiguration:
    maximum_isl_distance_m: float
    ground_station_minimum_elevation_deg: float
    ground_station_maximum_distance_m: float | None
    zone_coverage_minimum_elevation_deg: float
    propagation_speed_m_per_s: float


class _GeometricPhysicalCore:
    """Evaluate geometry for both physical facts and the legacy consumer.

    This private core has no dependency on planning, Connection values, Planner
    policy, or operational state. It owns only immutable static preparation and
    a conservative GroundStation spatial index cache.
    """

    __slots__ = (
        "_physical_world",
        "_maximum_isl_distance_m",
        "_ground_station_minimum_elevation_deg",
        "_ground_station_maximum_distance_m",
        "_zone_coverage_minimum_elevation_deg",
        "_propagation_speed_m_per_s",
        "_static_geometry",
        "_ground_station_index",
        "_ground_station_positions",
    )

    def __init__(
        self,
        physical_world: PhysicalWorld,
        *,
        maximum_isl_distance_m: float,
        ground_station_minimum_elevation_deg: float,
        ground_station_maximum_distance_m: float | None,
        zone_coverage_minimum_elevation_deg: float,
        propagation_speed_m_per_s: float,
    ) -> None:
        if not isinstance(physical_world, PhysicalWorld):
            raise TypeError("physical_world must be a PhysicalWorld")
        self._physical_world = physical_world
        configuration = validate_geometric_physical_configuration(
            maximum_isl_distance_m=maximum_isl_distance_m,
            ground_station_minimum_elevation_deg=(
                ground_station_minimum_elevation_deg
            ),
            ground_station_maximum_distance_m=(
                ground_station_maximum_distance_m
            ),
            zone_coverage_minimum_elevation_deg=(
                zone_coverage_minimum_elevation_deg
            ),
            propagation_speed_m_per_s=propagation_speed_m_per_s,
        )
        self._maximum_isl_distance_m = configuration.maximum_isl_distance_m
        self._ground_station_minimum_elevation_deg = (
            configuration.ground_station_minimum_elevation_deg
        )
        self._ground_station_maximum_distance_m = (
            configuration.ground_station_maximum_distance_m
        )
        self._zone_coverage_minimum_elevation_deg = (
            configuration.zone_coverage_minimum_elevation_deg
        )
        self._propagation_speed_m_per_s = (
            configuration.propagation_speed_m_per_s
        )
        self._static_geometry = _prepare_static_geometry(physical_world)
        self._ground_station_index: _GroundStationSpatialIndex | None = None
        self._ground_station_positions: (
            Mapping[NodeId, EarthFixedPosition] | None
        ) = None

    @property
    def physical_world(self) -> PhysicalWorld:
        return self._physical_world

    @property
    def satellites(self) -> tuple[Satellite, ...]:
        return self._static_geometry.satellites

    @property
    def ground_stations(self) -> tuple[GroundStation, ...]:
        return self._static_geometry.ground_stations

    def validate_positions(
        self, positions: Mapping[NodeId, EarthFixedPosition]
    ) -> Mapping[NodeId, EarthFixedPosition]:
        return _validate_positions(
            self._static_geometry.expected_node_ids, positions
        )

    def compute(
        self,
        timestamp: datetime,
        positions: Mapping[NodeId, EarthFixedPosition],
        request: PhysicalLinkRequest,
        *,
        ground_station_ids: frozenset[NodeId] | None = None,
    ) -> PhysicalLinkObservation:
        """Return feasible-only facts for one exact timestamp and request."""

        utc = normalize_utc_datetime(timestamp, "timestamp")
        if not isinstance(request, PhysicalLinkRequest):
            raise TypeError("request must be a PhysicalLinkRequest")
        position_values = self.validate_positions(positions)
        static = self._static_geometry

        inter_satellite_candidates = []
        for first, second in request.inter_satellite_pairs:
            relation = _isl_relation(static, first, second)
            first_position = position_values[first]
            second_position = position_values[second]
            distance = ecef_distance_m(first_position, second_position)
            if (
                distance <= self._maximum_isl_distance_m
                and has_earth_line_of_sight(first_position, second_position)
            ):
                inter_satellite_candidates.append(
                    InterSatelliteLinkCandidate(
                        endpoint_a=first,
                        endpoint_b=second,
                        same_plane=relation.same_plane,
                        cyclic_plane_separation=(
                            relation.cyclic_plane_separation
                        ),
                        distance_m=distance,
                        propagation_delay_us=propagation_delay_us(
                            distance, self._propagation_speed_m_per_s
                        ),
                    )
                )

        ground_candidates: list[GroundStationAccessCandidate] = []
        requested_ground_station_ids: frozenset[NodeId]
        if not request.include_ground_station_access:
            requested_ground_station_ids = frozenset()
        elif ground_station_ids is None:
            requested_ground_station_ids = frozenset(
                value.node_id for value in static.ground_stations
            )
        else:
            requested_ground_station_ids = _validate_ground_station_ids(
                static, ground_station_ids
            )
        ground_pairs_evaluated = 0
        if requested_ground_station_ids:
            ground_index = self._prepare_ground_station_index(position_values)
            local_up_by_ground_station = {
                node_id: _wgs84_local_up_unit_vector(position_values[node_id])
                for node_id in requested_ground_station_ids
            }
            for satellite in static.satellites:
                satellite_position = position_values[satellite.node_id]
                search_radius_m = self._ground_station_maximum_distance_m
                if search_radius_m is None:
                    search_radius_m = _maximum_eligible_slant_distance_m(
                        satellite_position,
                        self._ground_station_minimum_elevation_deg,
                        ground_index.minimum_radius_m,
                        ground_index.maximum_radius_m,
                        ground_index.maximum_up_deviation_deg,
                    )
                for ground_station in ground_index.query(
                    satellite_position, search_radius_m
                ):
                    if ground_station.node_id not in requested_ground_station_ids:
                        continue
                    ground_pairs_evaluated += 1
                    ground_position = position_values[ground_station.node_id]
                    distance = ecef_distance_m(
                        ground_position, satellite_position
                    )
                    elevation = _elevation_deg_from_local_up_and_distance(
                        ground_position,
                        satellite_position,
                        local_up_by_ground_station[ground_station.node_id],
                        distance,
                    )
                    if (
                        elevation >= self._ground_station_minimum_elevation_deg
                        and has_earth_line_of_sight(
                            ground_position, satellite_position
                        )
                        and (
                            self._ground_station_maximum_distance_m is None
                            or distance
                            <= self._ground_station_maximum_distance_m
                        )
                    ):
                        ground_candidates.append(
                            GroundStationAccessCandidate(
                                ground_station_id=ground_station.node_id,
                                satellite_id=satellite.node_id,
                                distance_m=distance,
                                elevation_deg=elevation,
                                propagation_delay_us=propagation_delay_us(
                                    distance, self._propagation_speed_m_per_s
                                ),
                            )
                        )

        zone_candidates: list[ZoneServiceCandidate] = []
        zone_pairs_evaluated = 0
        zone_index = static.zone_index
        if request.include_zone_service and zone_index is not None:
            for satellite in static.satellites:
                satellite_position = position_values[satellite.node_id]
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
                    zone_pairs_evaluated += 1
                    representative = prepared_zone.representative_position
                    distance = ecef_distance_m(
                        representative, satellite_position
                    )
                    elevation = _elevation_deg_from_local_up_and_distance(
                        representative,
                        satellite_position,
                        prepared_zone.local_up,
                        distance,
                    )
                    if (
                        elevation >= self._zone_coverage_minimum_elevation_deg
                        and has_earth_line_of_sight(
                            representative, satellite_position
                        )
                    ):
                        zone_candidates.append(
                            ZoneServiceCandidate(
                                zone_id=prepared_zone.zone.zone_id,
                                satellite_id=satellite.node_id,
                                distance_m=distance,
                                elevation_deg=elevation,
                                propagation_delay_us=propagation_delay_us(
                                    distance, self._propagation_speed_m_per_s
                                ),
                            )
                        )

        work_counts = PhysicalLinkWorkCounts(
            inter_satellite_pairs_requested=len(
                request.inter_satellite_pairs
            ),
            inter_satellite_pairs_evaluated=len(
                request.inter_satellite_pairs
            ),
            inter_satellite_pairs_feasible=len(inter_satellite_candidates),
            ground_station_pairs_requested=(
                len(static.satellites) * len(requested_ground_station_ids)
            ),
            ground_station_pairs_evaluated=ground_pairs_evaluated,
            ground_station_pairs_feasible=len(ground_candidates),
            zone_service_pairs_requested=(
                len(static.satellites) * len(static.zones)
                if request.include_zone_service
                else 0
            ),
            zone_service_pairs_evaluated=zone_pairs_evaluated,
            zone_service_pairs_feasible=len(zone_candidates),
        )
        return PhysicalLinkObservation(
            timestamp=utc,
            inter_satellite_candidates=tuple(inter_satellite_candidates),
            ground_station_access_candidates=tuple(ground_candidates),
            zone_service_candidates=tuple(zone_candidates),
            work_counts=work_counts,
        )

    def _prepare_ground_station_index(
        self, positions: Mapping[NodeId, EarthFixedPosition]
    ) -> _GroundStationSpatialIndex:
        current_positions = {
            station.node_id: positions[station.node_id]
            for station in self._static_geometry.ground_stations
        }
        if self._ground_station_positions != current_positions:
            self._ground_station_index = _build_ground_station_index(
                self._static_geometry.ground_stations, current_positions
            )
            self._ground_station_positions = MappingProxyType(
                current_positions
            )
        assert self._ground_station_index is not None
        return self._ground_station_index


@dataclass(frozen=True, slots=True)
class _IslRelation:
    same_plane: bool
    cyclic_plane_separation: int


def _isl_relation(
    static: _PreparedStaticGeometry, first: NodeId, second: NodeId
) -> _IslRelation:
    try:
        first_plane = static.satellite_planes[first]
        second_plane = static.satellite_planes[second]
    except KeyError as error:
        raise ValueError(
            "ISL request endpoints must belong to the supplied PhysicalWorld"
        ) from error
    if first_plane.orbit_key == second_plane.orbit_key:
        return _IslRelation(
            same_plane=True,
            cyclic_plane_separation=0,
        )
    if first_plane.shell_key != second_plane.shell_key:
        raise ValueError("ISL request endpoints must belong to one Shell")
    difference = abs(first_plane.plane_index - second_plane.plane_index)
    separation = min(difference, first_plane.plane_count - difference)
    if separation <= 0:
        raise ValueError("cross-plane ISL request has no plane separation")
    return _IslRelation(
        same_plane=False,
        cyclic_plane_separation=separation,
    )


def prepare_legacy_isl_pairs(
    physical_world: PhysicalWorld,
    positions: Mapping[NodeId, EarthFixedPosition],
) -> tuple[_NodePair, ...]:
    """Prepare the unchanged legacy adjacent-plane candidate relationships."""

    pairs: set[_NodePair] = set()
    for autonomous_system in physical_world.autonomous_systems:
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
                raise ValueError(
                    "adjacent-Orbit preparation requires unique RAAN"
                )
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


def propagation_delay_us(distance_m: float, speed_m_per_s: float) -> int:
    """Convert a physical distance to deterministic one-way delay."""

    distance = _non_negative_float(distance_m, "distance_m")
    speed = _positive_float(speed_m_per_s, "speed_m_per_s")
    return ceil(distance / speed * 1_000_000.0)


def validate_geometric_physical_configuration(
    *,
    maximum_isl_distance_m: float,
    ground_station_minimum_elevation_deg: float,
    ground_station_maximum_distance_m: float | None,
    zone_coverage_minimum_elevation_deg: float,
    propagation_speed_m_per_s: float,
) -> _GeometricPhysicalConfiguration:
    """Validate and normalize the shared geometric physical parameters."""

    return _GeometricPhysicalConfiguration(
        maximum_isl_distance_m=_positive_float(
            maximum_isl_distance_m, "maximum_isl_distance_m"
        ),
        ground_station_minimum_elevation_deg=_elevation(
            ground_station_minimum_elevation_deg,
            "ground_station_minimum_elevation_deg",
        ),
        ground_station_maximum_distance_m=(
            None
            if ground_station_maximum_distance_m is None
            else _positive_float(
                ground_station_maximum_distance_m,
                "ground_station_maximum_distance_m",
            )
        ),
        zone_coverage_minimum_elevation_deg=_elevation(
            zone_coverage_minimum_elevation_deg,
            "zone_coverage_minimum_elevation_deg",
        ),
        propagation_speed_m_per_s=_positive_float(
            propagation_speed_m_per_s, "propagation_speed_m_per_s"
        ),
    )


def _prepare_static_geometry(physical_world: PhysicalWorld) -> _PreparedStaticGeometry:
    satellites, ground_stations = _spatial_nodes(physical_world)
    try:
        zones = physical_world.get_zone_layout().zones
    except RuntimeError:
        zones = ()
    prepared_zones = []
    for zone in sorted(zones, key=lambda value: value.zone_id):
        latitude, longitude = zone.representative_point
        representative_position = geodetic_to_ecef(
            latitude, longitude, 0.0
        )
        prepared_zones.append(
            _PreparedZone(
                zone=zone,
                representative_position=representative_position,
                local_up=_wgs84_local_up_unit_vector(representative_position),
            )
        )

    satellite_planes = {}
    for autonomous_system in physical_world.autonomous_systems:
        for shell in autonomous_system.shells:
            ordered_orbits = tuple(
                sorted(
                    shell.orbits,
                    key=lambda value: (
                        value.right_ascension_deg,
                        value.orbit_id,
                    ),
                )
            )
            for plane_index, orbit in enumerate(ordered_orbits):
                plane = _SatellitePlane(
                    shell_key=(autonomous_system.asn, shell.shell_id),
                    orbit_key=(
                        autonomous_system.asn,
                        shell.shell_id,
                        orbit.orbit_id,
                    ),
                    plane_index=plane_index,
                    plane_count=len(ordered_orbits),
                )
                for satellite in orbit.satellites:
                    satellite_planes[satellite.node_id] = plane

    prepared_zone_values = tuple(prepared_zones)
    return _PreparedStaticGeometry(
        satellites=satellites,
        ground_stations=ground_stations,
        zones=prepared_zone_values,
        zone_index=(
            _build_zone_index(prepared_zone_values)
            if prepared_zone_values
            else None
        ),
        satellite_planes=MappingProxyType(satellite_planes),
        expected_node_ids=frozenset(
            node.node_id for node in (*satellites, *ground_stations)
        ),
    )


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
        local_up = _wgs84_local_up_unit_vector(position)
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
        -minimum_ground_radius_m * sin(radial_elevation) + sqrt(radicand)
    )
    if maximum_distance < 0.0:
        return satellite_radius + maximum_ground_radius_m
    return maximum_distance


def _position_radius_m(position: EarthFixedPosition) -> float:
    return sqrt(position.x_m**2 + position.y_m**2 + position.z_m**2)


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
            "positions must contain exactly all PhysicalWorld nodes "
            f"(missing={len(missing)}, foreign={len(foreign)})"
        )
    for node_id, position in positions.items():
        if not isinstance(node_id, NodeId):
            raise TypeError("positions keys must be NodeId values")
        if not isinstance(position, EarthFixedPosition):
            raise TypeError(
                "positions values must be EarthFixedPosition values"
            )
    return positions


def _validate_ground_station_ids(
    static: _PreparedStaticGeometry, values: frozenset[NodeId]
) -> frozenset[NodeId]:
    if not isinstance(values, frozenset):
        raise TypeError("ground_station_ids must be a frozenset")
    available = frozenset(
        station.node_id for station in static.ground_stations
    )
    if not values <= available:
        raise ValueError(
            "ground_station_ids must belong to the supplied PhysicalWorld"
        )
    return values


def _spatial_nodes(
    physical_world: PhysicalWorld,
) -> tuple[tuple[Satellite, ...], tuple[GroundStation, ...]]:
    satellites = []
    ground_stations = []
    for autonomous_system in physical_world.autonomous_systems:
        for shell in autonomous_system.shells:
            for orbit in shell.orbits:
                satellites.extend(orbit.satellites)
        ground_stations.extend(autonomous_system.ground_stations)
    return (
        tuple(
            sorted(satellites, key=lambda value: _node_key(value.node_id))
        ),
        tuple(
            sorted(
                ground_stations, key=lambda value: _node_key(value.node_id)
            )
        ),
    )


def _pair_ids(first: Satellite, second: Satellite) -> _NodePair:
    if _node_key(first.node_id) <= _node_key(second.node_id):
        return first.node_id, second.node_id
    return second.node_id, first.node_id


def _node_key(node_id: NodeId) -> tuple[int, str, str]:
    return node_id.asn, node_id.entity_kind, node_id.entity_id


def _positive_float(value: float, field_name: str) -> float:
    converted = _non_negative_float(value, field_name)
    if converted == 0.0:
        raise ValueError(f"{field_name} must be a positive finite number")
    return converted


def _non_negative_float(value: float, field_name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(
            f"{field_name} must be a non-negative finite number"
        )
    try:
        converted = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"{field_name} must be a non-negative finite number"
        ) from error
    if not isfinite(converted) or converted < 0.0:
        raise ValueError(
            f"{field_name} must be a non-negative finite number"
        )
    return converted


def _elevation(value: float, field_name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be between -90 and 90")
    try:
        converted = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_name} must be between -90 and 90") from error
    if not isfinite(converted) or not -90.0 <= converted <= 90.0:
        raise ValueError(f"{field_name} must be between -90 and 90")
    return converted


__all__ = [
    "_GeometricPhysicalCore",
    "prepare_legacy_isl_pairs",
    "propagation_delay_us",
    "validate_geometric_physical_configuration",
]
