"""Generate immutable Simulator input from constructed Emulator entities."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from types import MappingProxyType

from skyseed_v2.entities import (
    GroundStation,
    Orbit,
    OrbitParameters,
    Satellite,
    Shell,
)
from skyseed_v2.node_id import NodeId
from skyseed_v2.seedemu_integration import SkyseedAutonomousSystem
from skyseed_v2.simulator_input import (
    GroundStationSimulationInput,
    SatelliteSimulationInput,
    SimulatorStaticInput,
)


_SATELLITE_ENTITY_KIND = "satellite"
_GROUND_STATION_ENTITY_KIND = "ground-station"
_EmulatorNode = Satellite | GroundStation
_NodeRecord = tuple[NodeId, _EmulatorNode, OrbitParameters | None]


def _derive_node_id(
    *,
    asn: int,
    entity_kind: str,
    entity_id: str,
) -> NodeId:
    """Apply the sole NodeId derivation rule for supported Emulator nodes."""

    return NodeId(
        asn=asn,
        entity_kind=entity_kind,
        entity_id=entity_id,
    )


def _coerce_skyseed_autonomous_systems(
    skyseed_autonomous_systems: Iterable[SkyseedAutonomousSystem],
) -> tuple[SkyseedAutonomousSystem, ...]:
    try:
        autonomous_systems = tuple(skyseed_autonomous_systems)
    except TypeError as error:
        raise TypeError(
            "skyseed_autonomous_systems must be an iterable of "
            "SkyseedAutonomousSystem values"
        ) from error
    if any(
        not isinstance(autonomous_system, SkyseedAutonomousSystem)
        for autonomous_system in autonomous_systems
    ):
        raise TypeError(
            "skyseed_autonomous_systems must contain only "
            "SkyseedAutonomousSystem values"
        )
    return autonomous_systems


def _iter_skyseed_nodes(
    skyseed_autonomous_systems: Iterable[SkyseedAutonomousSystem],
) -> Iterator[_NodeRecord]:
    """Traverse the one supported entity tree and derive every NodeId."""

    autonomous_systems = _coerce_skyseed_autonomous_systems(
        skyseed_autonomous_systems
    )
    for autonomous_system in autonomous_systems:
        for shell in autonomous_system.shells:
            if not isinstance(shell, Shell):
                raise TypeError(
                    "SkyseedAutonomousSystem contains an unsupported Shell"
                )
            for orbit in shell.orbits:
                if not isinstance(orbit, Orbit):
                    raise TypeError("Shell contains an unsupported Orbit")
                if not isinstance(orbit.parameters, OrbitParameters):
                    raise TypeError(
                        "Orbit contains unsupported OrbitParameters"
                    )
                for satellite in orbit.satellites:
                    if not isinstance(satellite, Satellite):
                        raise TypeError("Orbit contains an unsupported Satellite")
                    yield (
                        _derive_node_id(
                            asn=satellite.getAsn(),
                            entity_kind=_SATELLITE_ENTITY_KIND,
                            entity_id=satellite.satellite_id,
                        ),
                        satellite,
                        orbit.parameters,
                    )
        for ground_station in autonomous_system.ground_stations():
            if not isinstance(ground_station, GroundStation):
                raise TypeError(
                    "SkyseedAutonomousSystem contains an unsupported GroundStation"
                )
            yield (
                _derive_node_id(
                    asn=ground_station.getAsn(),
                    entity_kind=_GROUND_STATION_ENTITY_KIND,
                    entity_id=ground_station.ground_station_id,
                ),
                ground_station,
                None,
            )


def _create_satellite_simulation_input(
    satellite: Satellite,
    orbit_parameters: OrbitParameters,
    node_id: NodeId,
) -> SatelliteSimulationInput:
    return SatelliteSimulationInput(
        node_id=node_id,
        satellite_id=satellite.satellite_id,
        shell_id=satellite.shell_id,
        orbit_id=satellite.orbit_id,
        orbit_index=satellite.orbit_index,
        altitude_km=orbit_parameters.altitude_km,
        inclination_deg=orbit_parameters.inclination_deg,
        right_ascension_deg=orbit_parameters.right_ascension_deg,
        mean_anomaly_deg=satellite.mean_anomaly_deg,
    )


def _create_ground_station_simulation_input(
    ground_station: GroundStation,
    node_id: NodeId,
) -> GroundStationSimulationInput:
    location = ground_station.location
    return GroundStationSimulationInput(
        node_id=node_id,
        ground_station_id=ground_station.ground_station_id,
        latitude_deg=location.latitude_deg,
        longitude_deg=location.longitude_deg,
        elevation_m=location.elevation_m,
        station_type=ground_station.station_type,
        operator=ground_station.operator,
    )


def create_satellite_simulation_input(
    satellite: Satellite,
    orbit_parameters: OrbitParameters,
) -> SatelliteSimulationInput:
    """Combine one Satellite's values with its owning Orbit's parameters."""

    if not isinstance(satellite, Satellite):
        raise TypeError("satellite must be a Satellite")
    if not isinstance(orbit_parameters, OrbitParameters):
        raise TypeError("orbit_parameters must be an OrbitParameters value")
    return _create_satellite_simulation_input(
        satellite,
        orbit_parameters,
        _derive_node_id(
            asn=satellite.getAsn(),
            entity_kind=_SATELLITE_ENTITY_KIND,
            entity_id=satellite.satellite_id,
        ),
    )


def create_ground_station_simulation_input(
    ground_station: GroundStation,
) -> GroundStationSimulationInput:
    """Copy approved static scalar values from one Emulator GroundStation."""

    if not isinstance(ground_station, GroundStation):
        raise TypeError("ground_station must be a GroundStation")
    return _create_ground_station_simulation_input(
        ground_station,
        _derive_node_id(
            asn=ground_station.getAsn(),
            entity_kind=_GROUND_STATION_ENTITY_KIND,
            entity_id=ground_station.ground_station_id,
        ),
    )


def create_simulator_static_input(
    skyseed_autonomous_systems: Iterable[SkyseedAutonomousSystem],
) -> SimulatorStaticInput:
    """Copy all supported entities from explicitly supplied Skyseed AS values."""

    records = tuple(_iter_skyseed_nodes(skyseed_autonomous_systems))
    satellites: list[SatelliteSimulationInput] = []
    ground_stations: list[GroundStationSimulationInput] = []
    for node_id, entity, orbit_parameters in records:
        if isinstance(entity, Satellite):
            if not isinstance(orbit_parameters, OrbitParameters):
                raise TypeError("Satellite record has no OrbitParameters")
            satellites.append(
                _create_satellite_simulation_input(
                    entity,
                    orbit_parameters,
                    node_id,
                )
            )
        elif isinstance(entity, GroundStation):
            if orbit_parameters is not None:
                raise TypeError(
                    "GroundStation record must not have OrbitParameters"
                )
            ground_stations.append(
                _create_ground_station_simulation_input(entity, node_id)
            )
        else:
            raise TypeError("unsupported Emulator node record")
    return SimulatorStaticInput(satellites, ground_stations)


def create_emulator_node_index(
    skyseed_autonomous_systems: Iterable[SkyseedAutonomousSystem],
) -> Mapping[NodeId, Satellite | GroundStation]:
    """Index original Emulator entities by their shared cross-layer NodeId.

    The returned mapping is read-only. Its values are the exact Satellite and
    GroundStation instances from the supplied entity trees; it contains no
    Registry, container, Compose, namespace or interface information.
    """

    node_index: dict[NodeId, Satellite | GroundStation] = {}
    for node_id, entity, _ in _iter_skyseed_nodes(skyseed_autonomous_systems):
        if node_id in node_index:
            raise ValueError(f"duplicate NodeId: {node_id.value}")
        node_index[node_id] = entity
    return MappingProxyType(node_index)


__all__ = [
    "create_emulator_node_index",
    "create_ground_station_simulation_input",
    "create_satellite_simulation_input",
    "create_simulator_static_input",
]
