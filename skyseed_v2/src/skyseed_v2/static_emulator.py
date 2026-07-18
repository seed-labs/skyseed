"""Create a static Emulator from immutable Config values."""

from __future__ import annotations

from dataclasses import dataclass

from seedemu.core import AutonomousSystem, Emulator
from seedemu.layers import Ebgp, Ibgp, Ospf, PeerRelationship, Routing

from skyseed_v2.configuration import (
    OrdinaryAutonomousSystemConfig,
    ShellConfig,
    SkyseedAutonomousSystemConfig,
    StaticEmulatorConfig,
    ground_station_id_for,
)
from skyseed_v2.entities import GroundStationLocation, OrbitParameters
from skyseed_v2.seedemu_integration import SatelliteBase, SkyseedAutonomousSystem


__all__ = [
    "StaticEmulatorCreationResult",
    "create_static_emulator",
]


_SatelliteInput = tuple[str, int, float, dict[str, str]]
_OrbitInput = tuple[str, OrbitParameters, tuple[_SatelliteInput, ...]]


@dataclass(frozen=True, slots=True)
class StaticEmulatorCreationResult:
    """Expose the three outputs of one static Emulator creation operation.

    Layer: operation result. `create_static_emulator` creates it; file-driven
    callers use the Emulator and both AS collections. It is not an Emulator
    entity, ownership layer, Config or Simulator input. It is public from
    `skyseed_v2.static_emulator` because no existing object owns all outputs.
    """

    emulator: Emulator
    skyseed_autonomous_systems: tuple[SkyseedAutonomousSystem, ...]
    ordinary_autonomous_systems: tuple[AutonomousSystem, ...]


def create_static_emulator(
    config: StaticEmulatorConfig,
) -> StaticEmulatorCreationResult:
    """Create Emulator, ASes, entities, networks and routing from one Config."""

    if not isinstance(config, StaticEmulatorConfig):
        raise TypeError("config must be a StaticEmulatorConfig")

    emulator = Emulator()
    base = SatelliteBase()
    for exchange in config.internet_exchanges:
        base.createInternetExchange(
            exchange.ix_id,
            prefix=exchange.prefix,
            create_rs=exchange.route_server,
        )

    skyseed_autonomous_systems = tuple(
        _create_skyseed_autonomous_system(base, as_config)
        for as_config in config.skyseed_autonomous_systems
    )
    ordinary_autonomous_systems = tuple(
        _create_ordinary_autonomous_system(base, as_config)
        for as_config in config.ordinary_autonomous_systems
    )
    _add_routing_layers(
        emulator,
        base,
        skyseed_autonomous_systems,
        config,
    )
    return StaticEmulatorCreationResult(
        emulator=emulator,
        skyseed_autonomous_systems=skyseed_autonomous_systems,
        ordinary_autonomous_systems=ordinary_autonomous_systems,
    )


def _create_skyseed_autonomous_system(
    base: SatelliteBase,
    config: SkyseedAutonomousSystemConfig,
) -> SkyseedAutonomousSystem:
    autonomous_system = SkyseedAutonomousSystem(config.asn)
    autonomous_system.setIbgpMode("disabled")
    base.setAutonomousSystem(autonomous_system)

    for shell_config in config.shells:
        orbit_inputs = _expand_orbit_inputs(shell_config)
        shell = autonomous_system.create_shell(shell_config.shell_id)
        for orbit_id, parameters, satellite_inputs in orbit_inputs:
            orbit = shell.create_orbit(orbit_id, parameters=parameters)
            for satellite_id, orbit_index, anomaly, payload in satellite_inputs:
                orbit.create_satellite(
                    satellite_id,
                    orbit_index=orbit_index,
                    mean_anomaly_deg=anomaly,
                    payload=payload,
                )

    for station_config in config.ground_stations:
        station = autonomous_system.create_ground_station(
            ground_station_id_for(station_config),
            location=GroundStationLocation(
                latitude_deg=station_config.latitude_deg,
                longitude_deg=station_config.longitude_deg,
                elevation_m=station_config.elevation_m,
            ),
            station_type=station_config.station_type,
            operator=station_config.operator,
            display_name=station_config.display_name,
        )
        for attachment in station_config.ix_attachments:
            station.joinNetwork(f"ix{attachment.ix_id}", attachment.address)
    return autonomous_system


def _create_ordinary_autonomous_system(
    base: SatelliteBase,
    config: OrdinaryAutonomousSystemConfig,
) -> AutonomousSystem:
    autonomous_system = base.createAutonomousSystem(config.asn)
    for network in config.networks:
        autonomous_system.createNetwork(network.network_id, network.prefix)
    for router_config in config.routers:
        router = autonomous_system.createRouter(router_config.router_id)
        for attachment in router_config.network_attachments:
            router.joinNetwork(attachment.network_id, attachment.address)
        for attachment in router_config.ix_attachments:
            router.joinNetwork(f"ix{attachment.ix_id}", attachment.address)
    return autonomous_system


def _add_routing_layers(
    emulator: Emulator,
    base: SatelliteBase,
    skyseed_autonomous_systems: tuple[SkyseedAutonomousSystem, ...],
    config: StaticEmulatorConfig,
) -> None:
    if not config.ebgp_peerings:
        emulator.addLayer(base)
        return

    ospf = Ospf()
    ibgp = Ibgp()
    for autonomous_system in skyseed_autonomous_systems:
        ospf.maskAsn(autonomous_system.getAsn())
        ibgp.maskAsn(autonomous_system.getAsn())
    ebgp = Ebgp()
    for peering in config.ebgp_peerings:
        ebgp.addPrivatePeeringByRouters(
            peering.ix_id,
            peering.first.asn,
            peering.first.router_id,
            peering.second.asn,
            peering.second.router_id,
            PeerRelationship.Unfiltered,
        )
    for layer in (base, Routing(), ospf, ibgp, ebgp):
        emulator.addLayer(layer)


def _expand_orbit_inputs(
    config: ShellConfig,
) -> tuple[_OrbitInput, ...]:
    """Expand one immutable Shell Config without creating an entity."""

    if config.generation is None:
        return tuple(
            (
                orbit.orbit_id,
                OrbitParameters(
                    altitude_km=config.altitude_km,
                    inclination_deg=config.inclination_deg,
                    right_ascension_deg=orbit.right_ascension_deg,
                ),
                tuple(
                    (
                        satellite.satellite_id,
                        satellite.index_in_orbit,
                        satellite.mean_anomaly_deg,
                        dict(satellite.payload_items),
                    )
                    for satellite in orbit.satellites
                ),
            )
            for orbit in config.orbits
        )

    generation = config.generation
    raan_step = (
        360.0 / generation.orbit_count
        if generation.raan.step_deg is None
        else generation.raan.step_deg
    )
    phase_step = (
        360.0 / generation.satellites_per_orbit
        if generation.phase.step_deg is None
        else generation.phase.step_deg
    )
    raan_values = tuple(
        (generation.raan.start_deg + index * raan_step) % 360.0
        for index in range(generation.orbit_count)
    )
    inputs: list[_OrbitInput] = []
    for orbit_index, right_ascension in enumerate(raan_values):
        orbit_id = f"{config.shell_id}-orbit-{orbit_index + 1:03d}"
        orbit_start = (
            generation.phase.start_deg
            + (orbit_index % 2)
            * generation.phase.alternating_orbit_offset_deg
        ) % 360.0
        anomalies = tuple(
            (orbit_start + index * phase_step) % 360.0
            for index in range(generation.satellites_per_orbit)
        )
        satellites = tuple(
            (
                f"{orbit_id}-satellite-{satellite_index + 1:04d}",
                satellite_index,
                mean_anomaly,
                {},
            )
            for satellite_index, mean_anomaly in enumerate(anomalies)
        )
        inputs.append(
            (
                orbit_id,
                OrbitParameters(
                    altitude_km=config.altitude_km,
                    inclination_deg=config.inclination_deg,
                    right_ascension_deg=right_ascension,
                ),
                satellites,
            )
        )
    return tuple(inputs)
