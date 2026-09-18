"""Share architecture-neutral geometric physical-link evaluation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from math import acos, ceil, cos, degrees, isfinite, radians, sin, sqrt
from types import MappingProxyType

from skyseed_v2.shared import NodeId

from skyseed_v2.simulator.geometry import (
    EarthFixedPosition,
    geodetic_to_ecef,
)
from skyseed_v2.simulator.geometry._elevation import (
    _wgs84_local_up_unit_vector,
)
from skyseed_v2.simulator.physical.queries.physical_link import (
    GroundStationAccessCandidate,
    InterSatelliteLinkCandidate,
    PhysicalLinkQueryResult,
    PhysicalLinkWorkCounts,
    ZoneServiceCandidate,
)
from ...world.entities import PhysicalGroundStation, PhysicalSatellite
from ...world.world import PhysicalWorld
from skyseed_v2.simulator.physical.world.zone import Zone


_LocalUp = tuple[float, float, float]
_ShellKey = tuple[int, str]
_OrbitKey = tuple[int, str, str]


@dataclass(frozen=True, slots=True)
class _PreparedZone:
    zone: Zone
    representative_position: EarthFixedPosition
    local_up: _LocalUp


@dataclass(frozen=True, slots=True)
class _SatellitePlane:
    shell_key: _ShellKey
    orbit_key: _OrbitKey
    plane_index: int
    plane_count: int


@dataclass(frozen=True, slots=True)
class _PreparedStaticGeometry:
    satellites: tuple[PhysicalSatellite, ...]
    ground_stations: tuple[PhysicalGroundStation, ...]
    zones: tuple[_PreparedZone, ...]
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
    """Evaluate batched physical candidates and shared contact geometry.

    This private core has no dependency on planning, Connection values, Planner
    policy, or operational state. It owns immutable static preparation, bounded batched query work,
    same-snapshot reuse and shared contact-prediction geometry.
    """

    __slots__ = (
        "_physical_world",
        "_maximum_isl_distance_m",
        "_ground_station_minimum_elevation_deg",
        "_ground_station_maximum_distance_m",
        "_zone_coverage_minimum_elevation_deg",
        "_propagation_speed_m_per_s",
        "_static_geometry",
        "_batch",
        "_prediction",
        "_frame_cache",
        "_query_work",
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
        self._batch = None
        self._prediction = None
        self._frame_cache = {}
        self._query_work = {}
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
    @property
    def physical_world(self) -> PhysicalWorld:
        return self._physical_world

    @property
    def satellites(self) -> tuple[PhysicalSatellite, ...]:
        return self._static_geometry.satellites

    @property
    def ground_stations(self) -> tuple[PhysicalGroundStation, ...]:
        return self._static_geometry.ground_stations

    def validate_positions(
        self, positions: Mapping[NodeId, EarthFixedPosition]
    ) -> Mapping[NodeId, EarthFixedPosition]:
        return _validate_positions(
            self._static_geometry.expected_node_ids, positions
        )

    def _prepare_batch(self, request):
        from skyseed_v2.simulator.physical.models.links._batched_geometry import _BatchedPhysicalGeometry
        if self._batch is None or self._batch.pairs != request.inter_satellite_pairs:
            self._batch = _BatchedPhysicalGeometry(self, request, relation=_isl_relation,
                radius_bounds=_spatial_radius_bounds, maximum_slant=_maximum_eligible_slant_distance_m)
        return self._batch

    def compute(self, timestamp, positions, request):
        positions = self.validate_positions(positions)
        batch = self._prepare_batch(request)
        # One frame, one world/core, one prediction and exact immutable inputs.
        # Preserve a value snapshot as callers may supply a mutable mapping.
        identity = timestamp, tuple(positions.items()), batch, self._prediction
        cache = self._frame_cache
        if cache.get("identity") != identity:
            cache = self._frame_cache = {"identity": identity}
        work = self._query_work = {}
        if "arrays" not in cache:
            cache["arrays"] = batch.arrays(positions)
        satellites = cache["arrays"]
        prediction = self._prediction
        def window(kind, key):
            return None if prediction is None else prediction.window(kind, key, timestamp)
        def meets_tail(kind, key, duration):
            if not duration:
                return True
            value = window(kind, key)
            return value is None or value.covers(timestamp, timestamp + duration)
        isl_key = ("isl", request.cross_plane_neighbor_count,
                   request.inter_satellite_minimum_contact_duration,
                   request.retained_inter_satellite_pairs)
        if isl_key not in cache:
            indices, distances, evaluated_isl = batch.isl(satellites)
            all_isl_count = len(indices)
            keep = set(map(int, indices))
            rejected_isl = 0
            if request.cross_plane_neighbor_count is not None:
                groups, keep = {}, set()
                retained = set(request.retained_inter_satellite_pairs)
                planes = self._static_geometry.satellite_planes
                for index, distance in zip(indices, distances):
                    index = int(index)
                    first, second = batch.pairs[index]
                    if batch.relations[index].same_plane or (first, second) in retained:
                        keep.add(index)
                    elif meets_tail('isl', batch.pairs[index], request.inter_satellite_minimum_contact_duration):
                        groups.setdefault((first, planes[second].orbit_key), []).append((float(distance), index))
                        groups.setdefault((second, planes[first].orbit_key), []).append((float(distance), index))
                    else:
                        rejected_isl += 1
                for values in groups.values():
                    keep.update(i for _, i in sorted(values)[:request.cross_plane_neighbor_count])
            isl = tuple(_candidate_from_geometry(InterSatelliteLinkCandidate,
                *batch.pairs[int(i)], batch.relations[int(i)].same_plane,
                batch.relations[int(i)].cyclic_plane_separation, float(d),
                ceil(float(d) / self._propagation_speed_m_per_s * 1_000_000.0),
                window('isl', batch.pairs[int(i)]),
            ) for i, d in zip(indices, distances) if int(i) in keep)
            cache[isl_key] = isl, evaluated_isl, all_isl_count, rejected_isl
            work['inter_satellite_geometry_pairs_computed'] = evaluated_isl
        else:
            work['inter_satellite_geometry_pairs_reused'] = cache[isl_key][1]
        isl, evaluated_isl, all_isl_count, rejected_isl = cache[isl_key]
        ground, zone = (), ()
        evaluated_ground = evaluated_zone = all_ground_count = rejected_ground = rank_omitted = 0
        if request.include_ground_station_access:
            if "ground_geometry" not in cache:
                cache["ground_geometry"] = batch.surface(satellites)
                work['ground_station_geometry_pairs_computed'] = cache['ground_geometry'][3]
            else:
                work['ground_station_geometry_pairs_reused'] = cache['ground_geometry'][3]
            rows, distances, elevations, evaluated_ground = cache['ground_geometry']
            all_ground_count = len(rows)
            key = ("ground_groups", request.ground_station_minimum_contact_duration,
                   request.retained_ground_station_pairs)
            if key not in cache:
                groups, retained_rows = {}, set()
                retained = set(request.retained_ground_station_pairs)
                rejected = set()
                for i, ((a, g), distance) in enumerate(zip(rows, distances)):
                    if meets_tail('ground', int(a)*len(batch.stations)+int(g), request.ground_station_minimum_contact_duration):
                        groups.setdefault(int(g), []).append((float(distance), int(a), i))
                    else:
                        rejected.add(i)
                    if (batch.stations[int(g)], batch.satellites[int(a)]) in retained:
                        retained_rows.add(i)
                cache[key] = (tuple(tuple(i for _, _, i in sorted(values)) for values in groups.values()),
                              retained_rows, len(rejected-retained_rows))
                work['ground_station_contact_rows_computed'] = len(rows)
            else:
                work['ground_station_contact_rows_reused'] = len(rows)
            groups, retained_rows, rejected_ground = cache[key]
            if request.ground_station_candidate_limit is None:
                chosen = range(len(rows))
                rejected_ground = 0
            else:
                keep = set(retained_rows)
                for values in groups:
                    keep.update(values[:request.ground_station_candidate_limit])
                chosen = sorted(keep)
                rank_omitted = all_ground_count - len(chosen) - rejected_ground
            objects = cache.setdefault("ground_objects", {})
            old_count = len(objects)
            for i in chosen:
                if i not in objects:
                    a, g = map(int, rows[i]); d, e = float(distances[i]), float(elevations[i])
                    objects[i] = _candidate_from_geometry(GroundStationAccessCandidate,
                        batch.stations[g], batch.satellites[a], d, e,
                        ceil(d/self._propagation_speed_m_per_s*1_000_000.0),
                        window('ground', a*len(batch.stations)+g))
            ground = tuple(sorted((objects[i] for i in chosen), key=lambda c: (
                c.ground_station_id.asn, c.ground_station_id.entity_id,
                c.satellite_id.asn, c.satellite_id.entity_id)))
            work['ground_station_candidate_objects_computed'] = len(objects)-old_count
            work['ground_station_candidate_objects_reused'] = len(ground)-(len(objects)-old_count)
        if request.include_zone_service:
            if "zone" not in cache:
                rows, distances, elevations, evaluated_zone = batch.surface(satellites, zones=True)
                zone = tuple(_candidate_from_geometry(ZoneServiceCandidate,
                    batch.zones[int(z)].zone.zone_id, batch.satellites[int(a)], float(d), float(e),
                    ceil(float(d)/self._propagation_speed_m_per_s*1_000_000.0),
                ) for (a, z), d, e in zip(rows, distances, elevations))
                zone = tuple(sorted(zone, key=lambda c: (c.zone_id, c.satellite_id.asn, c.satellite_id.entity_id)))
                cache['zone'] = zone, evaluated_zone
                work['zone_service_geometry_pairs_computed'] = evaluated_zone
            else:
                work['zone_service_geometry_pairs_reused'] = cache['zone'][1]
            zone, evaluated_zone = cache['zone']
        work.update(ground_station_pairs_rank_omitted=rank_omitted,
                    ground_station_pairs_contact_rejected=rejected_ground,
                    inter_satellite_pairs_contact_rejected=rejected_isl,
                    inter_satellite_pairs_rank_omitted=all_isl_count-len(isl)-rejected_isl)
        return _candidate_from_geometry(PhysicalLinkQueryResult, timestamp, isl, ground, zone,
            PhysicalLinkWorkCounts(
                len(batch.pairs), evaluated_isl, len(isl),
                len(batch.satellites)*len(batch.stations) if request.include_ground_station_access else 0,
                evaluated_ground, len(ground),
                len(batch.satellites)*len(batch.zones) if request.include_zone_service else 0,
                evaluated_zone, len(zone), all_ground_count-len(ground), all_isl_count-len(isl)),
            rank_omitted == 0)


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


def _candidate_from_geometry(value_type, *fields):
    """Freeze facts produced by validated static identities and exact geometry.

    Public constructors retain their caller-input checks. This private producer
    has already validated positions/configuration and computed finite feasible
    rows, canonical ISL pairs, and typed contact windows; it does not validate
    those same identities and scalar fields for every sample again.
    """
    value = object.__new__(value_type)
    for name, item in zip(value_type.__slots__, fields, strict=True):
        object.__setattr__(value, name, item)
    return value


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
        satellite_planes=MappingProxyType(satellite_planes),
        expected_node_ids=frozenset(
            node.node_id for node in (*satellites, *ground_stations)
        ),
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


def _spatial_nodes(
    physical_world: PhysicalWorld,
) -> tuple[tuple[PhysicalSatellite, ...], tuple[PhysicalGroundStation, ...]]:
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
    "propagation_delay_us",
    "validate_geometric_physical_configuration",
]
