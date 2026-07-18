"""Convert constructed Emulator entities to immutable Simulator input data."""

from __future__ import annotations

from collections.abc import Iterable

from skyseed_v2.entities import GroundStation, Satellite
from skyseed_v2.seedemu_integration import SkyseedAutonomousSystem
from skyseed_v2.simulator_input import (
    GroundStationSimulationInput,
    SatelliteSimulationInput,
    SimulationNodeId,
    SimulatorStaticInput,
)
from skyseed_v2.simulator_input.identity import (
    GROUND_STATION_ENTITY_KIND,
    SATELLITE_ENTITY_KIND,
)


def create_satellite_simulation_input(
    satellite: Satellite,
) -> SatelliteSimulationInput:
    """Copy approved static scalar values from one Emulator Satellite."""

    if not isinstance(satellite, Satellite):
        raise TypeError("satellite must be a Satellite")
    parameters = satellite.orbit_parameters
    return SatelliteSimulationInput(
        node_id=SimulationNodeId(
            asn=satellite.getAsn(),
            entity_kind=SATELLITE_ENTITY_KIND,
            entity_id=satellite.satellite_id,
        ),
        satellite_id=satellite.satellite_id,
        shell_id=satellite.shell_id,
        orbit_id=satellite.orbit_id,
        orbit_index=satellite.orbit_index,
        altitude_km=parameters.altitude_km,
        inclination_deg=parameters.inclination_deg,
        right_ascension_deg=parameters.right_ascension_deg,
        mean_anomaly_deg=satellite.mean_anomaly_deg,
    )


def create_ground_station_simulation_input(
    ground_station: GroundStation,
) -> GroundStationSimulationInput:
    """Copy approved static scalar values from one Emulator GroundStation."""

    if not isinstance(ground_station, GroundStation):
        raise TypeError("ground_station must be a GroundStation")
    location = ground_station.location
    return GroundStationSimulationInput(
        node_id=SimulationNodeId(
            asn=ground_station.getAsn(),
            entity_kind=GROUND_STATION_ENTITY_KIND,
            entity_id=ground_station.ground_station_id,
        ),
        ground_station_id=ground_station.ground_station_id,
        latitude_deg=location.latitude_deg,
        longitude_deg=location.longitude_deg,
        elevation_m=location.elevation_m,
        station_type=ground_station.station_type,
        operator=ground_station.operator,
    )


def create_simulator_static_input(
    skyseed_autonomous_systems: Iterable[SkyseedAutonomousSystem],
) -> SimulatorStaticInput:
    """Copy all supported entities from explicitly supplied Skyseed AS values."""

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

    satellites = tuple(
        create_satellite_simulation_input(satellite)
        for autonomous_system in autonomous_systems
        for satellite in autonomous_system.satellites()
    )
    ground_stations = tuple(
        create_ground_station_simulation_input(ground_station)
        for autonomous_system in autonomous_systems
        for ground_station in autonomous_system.ground_stations()
    )
    return SimulatorStaticInput(satellites, ground_stations)


__all__ = [
    "create_ground_station_simulation_input",
    "create_satellite_simulation_input",
    "create_simulator_static_input",
]
