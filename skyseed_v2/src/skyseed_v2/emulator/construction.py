"""Construct one upstream Emulator from immutable Emulator Config values."""

from __future__ import annotations

from seedemu.core import AutonomousSystem, Emulator
from seedemu.layers import Base, Ebgp, Ibgp, Ospf, PeerRelationship, Routing

from skyseed_v2.configuration.emulator import (
    OrdinaryAutonomousSystemConfig,
    ShellConfig,
    SkyseedAutonomousSystemConfig,
    StaticEmulatorConfig,
)
from skyseed_v2.configuration.emulator.ground_station import (
    _derive_ground_station_id,
)
from skyseed_v2.emulator.entities import GroundStationLocation, OrbitParameters
from skyseed_v2.emulator.seedemu import (
    SkyseedAutonomousSystem,
    get_skyseed_autonomous_systems,
)


__all__ = ["create_static_emulator"]


_SatelliteInput = tuple[str, int, float, dict[str, str], int | None]
_OrbitInput = tuple[str, OrbitParameters, tuple[_SatelliteInput, ...]]


def create_static_emulator(config: StaticEmulatorConfig) -> Emulator:
    """Create and return one upstream Emulator through existing owner APIs."""

    if not isinstance(config, StaticEmulatorConfig):
        raise TypeError("config must be a StaticEmulatorConfig")

    emulator = Emulator()
    base = Base()

    for exchange in config.internet_exchanges:
        base.createInternetExchange(
            exchange.ix_id,
            prefix=exchange.prefix,
            create_rs=exchange.route_server,
        )

    for autonomous_system_config in config.ordinary_autonomous_systems:
        _create_ordinary_autonomous_system(base, autonomous_system_config)

    for autonomous_system_config in config.skyseed_autonomous_systems:
        _create_skyseed_autonomous_system(base, autonomous_system_config)

    _add_routing_layers(emulator, base, config)
    return emulator


def _create_skyseed_autonomous_system(
    base: Base,
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
            for (
                satellite_id,
                orbit_index,
                anomaly,
                payload,
                max_ground_station_accesses,
            ) in satellite_inputs:
                orbit.create_satellite(
                    satellite_id,
                    orbit_index=orbit_index,
                    mean_anomaly_deg=anomaly,
                    payload=payload,
                    max_simultaneous_ground_station_accesses=(
                        max_ground_station_accesses
                    ),
                )

    for station_config in config.ground_stations:
        station = autonomous_system.create_ground_station(
            _derive_ground_station_id(station_config),
            location=GroundStationLocation(
                latitude_deg=station_config.latitude_deg,
                longitude_deg=station_config.longitude_deg,
                elevation_m=station_config.elevation_m,
            ),
            station_type=station_config.station_type,
            operator=station_config.operator,
            display_name=station_config.display_name,
            max_simultaneous_satellite_accesses=(
                station_config.max_simultaneous_satellite_accesses
            ),
        )
        for attachment in station_config.ix_attachments:
            station.joinNetwork(f"ix{attachment.ix_id}", attachment.address)
    return autonomous_system


def _create_ordinary_autonomous_system(
    base: Base,
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
    base: Base,
    config: StaticEmulatorConfig,
) -> None:
    if not config.ebgp_peerings:
        emulator.addLayer(base)
        return

    ospf = Ospf()
    ibgp = Ibgp()
    for autonomous_system in get_skyseed_autonomous_systems(base):
        ospf.maskAsn(autonomous_system.getAsn())
        ibgp.maskAsn(autonomous_system.getAsn())
    ebgp = Ebgp()
    for peering in config.ebgp_peerings:
        first, second = peering.peer_routers
        ebgp.addPrivatePeeringByRouters(
            peering.ix_id,
            first.asn,
            first.router_id,
            second.asn,
            second.router_id,
            PeerRelationship.Unfiltered,
        )
    for layer in (base, Routing(), ospf, ibgp, ebgp):
        emulator.addLayer(layer)

def _expand_orbit_inputs(config: ShellConfig) -> tuple[_OrbitInput, ...]:
    """Expand one immutable Shell Config without creating an entity."""

    if config.generated_orbit_layout is None:
        return tuple(
            (
                orbit.orbit_id,
                OrbitParameters(
                    altitude_km=config.altitude_km,
                    inclination_deg=config.inclination_deg,
                    right_ascension_deg=orbit.right_ascension_deg,
                    epoch_utc=config.epoch_utc,
                ),
                tuple(
                    (
                        satellite.satellite_id,
                        satellite.index_in_orbit,
                        satellite.mean_anomaly_deg,
                        dict(satellite.payload_items),
                        satellite.max_simultaneous_ground_station_accesses,
                    )
                    for satellite in orbit.satellites
                ),
            )
            for orbit in config.explicit_orbits
        )

    generated_orbit_layout = config.generated_orbit_layout
    right_ascension_distribution = (
        generated_orbit_layout.right_ascension_distribution
    )
    in_orbit_phase_distribution = (
        generated_orbit_layout.in_orbit_phase_distribution
    )
    raan_step = (
        360.0 / generated_orbit_layout.orbit_count
        if right_ascension_distribution.step_deg is None
        else right_ascension_distribution.step_deg
    )
    phase_step = (
        360.0 / generated_orbit_layout.satellites_per_orbit
        if in_orbit_phase_distribution.step_deg is None
        else in_orbit_phase_distribution.step_deg
    )
    raan_values = tuple(
        (right_ascension_distribution.start_deg + index * raan_step) % 360.0
        for index in range(generated_orbit_layout.orbit_count)
    )
    inputs: list[_OrbitInput] = []
    for orbit_index, right_ascension in enumerate(raan_values):
        orbit_id = f"{config.shell_id}-orbit-{orbit_index + 1:03d}"
        orbit_phase_step = (
            in_orbit_phase_distribution.orbit_phase_step_deg
        )
        orbit_offset = (
            (orbit_index % 2)
            * in_orbit_phase_distribution.alternating_orbit_offset_deg
            if orbit_phase_step is None
            else orbit_index * orbit_phase_step
        )
        orbit_start = (
            in_orbit_phase_distribution.start_deg + orbit_offset
        ) % 360.0
        anomalies = tuple(
            (orbit_start + index * phase_step) % 360.0
            for index in range(generated_orbit_layout.satellites_per_orbit)
        )
        satellites = tuple(
            (
                f"{orbit_id}-satellite-{satellite_index + 1:04d}",
                satellite_index,
                mean_anomaly,
                {},
                generated_orbit_layout.max_simultaneous_ground_station_accesses,
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
                    epoch_utc=config.epoch_utc,
                ),
                satellites,
            )
        )
    return tuple(inputs)
