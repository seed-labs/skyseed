"""Built-in deterministic geometric Connection algorithm."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from math import ceil, isfinite
from threading import RLock
from types import MappingProxyType
from weakref import ReferenceType, ref

from skyseed_v2._time import normalize_utc_datetime
from skyseed_v2.node_id import NodeId

from ..connection import (
    Connection,
    ConnectionId,
    ConnectionKind,
    ConnectionSlice,
    DirectionalConnectionParameters,
)
from ..coordinates import (
    ecef_distance_m,
    elevation_deg,
    geodetic_to_ecef,
    has_earth_line_of_sight,
)
from ..position import EarthFixedPosition
from ..topology import GroundStation, Orbit, Satellite, Topology
from ..zone import SphericalCap


_NodePair = tuple[NodeId, NodeId]


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
        position_values = _validate_positions(topology, positions)
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

    def _clear_binding(self) -> None:
        self._clear_prepared_state()
        self._binding_owner_ref = None
        self._binding_topology = None
        self._binding_reference_timestamp = None

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
        position_values = _validate_positions(topology, positions)

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
                    prepared_pairs = _prepare_isl_pairs(
                        topology, position_values
                    )
                    self._prepared_topology = topology
                    self._prepared_reference_timestamp = utc
                    self._prepared_isl_pairs = prepared_pairs
                elif self._prepared_topology is not topology:
                    raise ValueError(
                        "GeometricConnectionModel cannot be reused with "
                        "another Topology"
                    )
                else:
                    prepared_pairs = self._prepared_isl_pairs
            assert prepared_pairs is not None

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

        satellites, ground_stations = _spatial_nodes(topology)
        for ground_station in ground_stations:
            ground_position = position_values[ground_station.node_id]
            eligible = []
            for satellite in satellites:
                satellite_position = position_values[satellite.node_id]
                distance = ecef_distance_m(ground_position, satellite_position)
                elevation = elevation_deg(ground_position, satellite_position)
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
                    eligible.append(
                        (elevation, _node_key(satellite.node_id), satellite, distance)
                    )
            eligible.sort(key=lambda value: (-value[0], value[1]))
            for _, _, satellite, distance in eligible[
                : self._maximum_ground_station_access_count
            ]:
                _append_connection(
                    connections,
                    seen_ids,
                    self._create_connection(
                        ConnectionKind.GROUND_STATION_ACCESS,
                        ground_station.node_id,
                        satellite.node_id,
                        distance,
                    ),
                )

        assignments = self._assign_zone_service(
            topology, satellites, position_values
        )
        return ConnectionSlice(connections, assignments)

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
        topology: Topology,
        satellites: tuple[Satellite, ...],
        positions: Mapping[NodeId, EarthFixedPosition],
    ) -> Mapping[str, NodeId]:
        candidates: dict[
            str, list[tuple[float, tuple[int, str, str], NodeId]]
        ] = {}
        cap_radius = 90.0 - self._zone_coverage_minimum_elevation_deg
        for satellite in satellites:
            satellite_position = positions[satellite.node_id]
            subpoint = (
                satellite_position.latitude_deg,
                satellite_position.longitude_deg,
            )
            try:
                coarse_zones = topology.find_intersecting_zones(
                    SphericalCap(subpoint, cap_radius)
                )
            except RuntimeError:
                return MappingProxyType({})
            for zone in coarse_zones:
                latitude, longitude = zone.representative_point
                representative_position = geodetic_to_ecef(
                    latitude, longitude, 0.0
                )
                representative_elevation = elevation_deg(
                    representative_position, satellite_position
                )
                if (
                    representative_elevation
                    >= self._zone_coverage_minimum_elevation_deg
                    and has_earth_line_of_sight(
                        representative_position, satellite_position
                    )
                ):
                    candidates.setdefault(zone.zone_id, []).append(
                        (
                            representative_elevation,
                            _node_key(satellite.node_id),
                            satellite.node_id,
                        )
                    )

        assignments = {}
        for zone_id, eligible in candidates.items():
            eligible.sort(key=lambda value: (-value[0], value[1]))
            assignments[zone_id] = eligible[0][2]
        return MappingProxyType(dict(sorted(assignments.items())))


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
    return tuple(sorted(pairs, key=lambda pair: (_node_key(pair[0]), _node_key(pair[1]))))


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


def _validate_positions(
    topology: Topology,
    positions: Mapping[NodeId, EarthFixedPosition],
) -> Mapping[NodeId, EarthFixedPosition]:
    if not isinstance(positions, Mapping):
        raise TypeError("positions must be a Mapping")
    satellites, ground_stations = _spatial_nodes(topology)
    expected = {
        node.node_id for node in (*satellites, *ground_stations)
    }
    if set(positions) != expected:
        missing = expected - set(positions)
        foreign = set(positions) - expected
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
