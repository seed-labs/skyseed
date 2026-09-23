"""Project Emulator objects into immutable Simulator input."""

from __future__ import annotations

from collections.abc import Iterable

from skyseed.emulator.entities import (
    EmulatorGroundStation,
    EmulatorOrbit,
    OrbitParameters,
    EmulatorSatellite,
    EmulatorShell,
)
from skyseed.shared import NodeId
from skyseed.emulator.seedemu import EmulatorAutonomousSystem
from skyseed.simulator.input import (
    GroundStationSimulationInput,
    SatelliteSimulationInput,
    SimulatorStaticInput,
)


_SATELLITE_ENTITY_KIND = "satellite"
_GROUND_STATION_ENTITY_KIND = "ground-station"


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


def _create_satellite_simulation_input(
    satellite: EmulatorSatellite,
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
        epoch_utc=orbit_parameters.epoch_utc,
        max_simultaneous_ground_station_accesses=(
            satellite.max_simultaneous_ground_station_accesses
        ),
        maximum_simultaneous_inter_satellite_links=(
            satellite.maximum_simultaneous_inter_satellite_links
        ),
    )


def _create_ground_station_simulation_input(
    ground_station: EmulatorGroundStation,
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
        max_simultaneous_satellite_accesses=(
            ground_station.max_simultaneous_satellite_accesses
        ),
    )


def create_satellite_simulation_input(
    satellite: EmulatorSatellite,
    orbit_parameters: OrbitParameters,
) -> SatelliteSimulationInput:
    """Combine one EmulatorSatellite's values with its owning EmulatorOrbit's parameters."""

    if not isinstance(satellite, EmulatorSatellite):
        raise TypeError("satellite must be an EmulatorSatellite")
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
    ground_station: EmulatorGroundStation,
) -> GroundStationSimulationInput:
    """Copy approved static scalar values from one EmulatorGroundStation."""

    if not isinstance(ground_station, EmulatorGroundStation):
        raise TypeError("ground_station must be an EmulatorGroundStation")
    return _create_ground_station_simulation_input(
        ground_station,
        _derive_node_id(
            asn=ground_station.getAsn(),
            entity_kind=_GROUND_STATION_ENTITY_KIND,
            entity_id=ground_station.ground_station_id,
        ),
    )


def create_simulator_static_input(
    skyseed_autonomous_systems: Iterable[EmulatorAutonomousSystem],
) -> SimulatorStaticInput:
    """Project each actual owner's entities directly into immutable input values."""

    try:
        autonomous_systems = iter(skyseed_autonomous_systems)
    except TypeError as error:
        raise TypeError(
            "skyseed_autonomous_systems must be an iterable of "
            "EmulatorAutonomousSystem values"
        ) from error
    satellites: list[SatelliteSimulationInput] = []
    ground_stations: list[GroundStationSimulationInput] = []
    for autonomous_system in autonomous_systems:
        if not isinstance(autonomous_system, EmulatorAutonomousSystem):
            raise TypeError(
                "skyseed_autonomous_systems must contain only "
                "EmulatorAutonomousSystem values"
            )
        asn = autonomous_system.getAsn()
        for shell in autonomous_system.shells:
            if not isinstance(shell, EmulatorShell):
                raise TypeError("EmulatorAutonomousSystem contains an unsupported EmulatorShell")
            for orbit in shell.orbits:
                if not isinstance(orbit, EmulatorOrbit):
                    raise TypeError("EmulatorShell contains an unsupported EmulatorOrbit")
                if orbit.shell_id != shell.shell_id:
                    raise ValueError("EmulatorOrbit identity must match its owning EmulatorShell")
                if not isinstance(orbit.parameters, OrbitParameters):
                    raise TypeError("EmulatorOrbit contains unsupported OrbitParameters")
                for satellite in orbit.satellites:
                    if not isinstance(satellite, EmulatorSatellite):
                        raise TypeError("EmulatorOrbit contains an unsupported EmulatorSatellite")
                    if (satellite.getAsn() != asn or satellite.shell_id != shell.shell_id
                            or satellite.orbit_id != orbit.orbit_id):
                        raise ValueError("EmulatorSatellite identity must match its owning AS/Shell/Orbit")
                    satellites.append(_create_satellite_simulation_input(
                        satellite, orbit.parameters,
                        _derive_node_id(asn=asn, entity_kind=_SATELLITE_ENTITY_KIND,
                                        entity_id=satellite.satellite_id),
                    ))
        for station in autonomous_system.ground_stations():
            if not isinstance(station, EmulatorGroundStation):
                raise TypeError("EmulatorAutonomousSystem contains an unsupported EmulatorGroundStation")
            if station.getAsn() != asn:
                raise ValueError("EmulatorGroundStation identity must match its owning AS")
            ground_stations.append(_create_ground_station_simulation_input(
                station, _derive_node_id(asn=asn, entity_kind=_GROUND_STATION_ENTITY_KIND,
                                         entity_id=station.ground_station_id),
            ))
    return SimulatorStaticInput(satellites, ground_stations)


__all__ = [
    "create_ground_station_simulation_input",
    "create_satellite_simulation_input",
    "create_simulator_static_input",
]
