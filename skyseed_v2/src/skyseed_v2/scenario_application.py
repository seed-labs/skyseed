"""Apply immutable static specifications through the public construction API."""

from __future__ import annotations

from dataclasses import dataclass

from seedemu.core import AutonomousSystem, Emulator
from seedemu.layers import Ebgp, Ibgp, Ospf, PeerRelationship, Routing

from skyseed_v2.entities import GroundStationLocation, OrbitParameters
from skyseed_v2.seed import SatelliteBase, SkyseedAutonomousSystem
from skyseed_v2.simulator_input import SimulatorStaticInput
from skyseed_v2.specifications import (
    OrdinaryAutonomousSystemSpecification,
    ShellSpecification,
    SkyseedAutonomousSystemSpecification,
    StaticEmulatorScenarioSpecification,
    ground_station_id_for,
)


__all__ = [
    "StaticEmulatorApplyResult",
    "StaticEmulatorSpecificationApplier",
]


_SatelliteInput = tuple[str, int, float, dict[str, str]]
_OrbitInput = tuple[str, OrbitParameters, tuple[_SatelliteInput, ...]]


@dataclass(frozen=True, slots=True)
class StaticEmulatorApplyResult:
    """Expose the flat output of one complete specification application.

    Layer: operation result. The specification Applier creates it; file-driven
    callers use the Emulator, owned AS collections and Simulator input. It is
    not a domain entity, ownership layer or Simulator input member. A named
    result is retained because no one existing object truthfully owns all four
    outputs, but it is public only from this application submodule.
    """

    emulator: Emulator
    skyseed_autonomous_systems: tuple[SkyseedAutonomousSystem, ...]
    ordinary_autonomous_systems: tuple[AutonomousSystem, ...]
    simulator_input: SimulatorStaticInput


class StaticEmulatorSpecificationApplier:
    """Apply one validated specification through public domain/SEED methods.

    Layer: application service. File-driven callers create it and consume its
    operation result. It reads specifications, sequences approved create/join
    methods, builds the static Simulator input and does not own entities or
    lifecycle. It is
    not a Loader, domain facade, Registry adapter or Compiler. It remains
    public from this submodule because complete file inputs need heterogeneous
    orchestration; handwritten scenarios bypass it.
    """

    def apply(
        self,
        specification: StaticEmulatorScenarioSpecification,
    ) -> StaticEmulatorApplyResult:
        if not isinstance(specification, StaticEmulatorScenarioSpecification):
            raise TypeError(
                "specification must be a StaticEmulatorScenarioSpecification"
            )

        emulator = Emulator()
        base = SatelliteBase()
        for exchange in specification.internet_exchanges:
            base.createInternetExchange(
                exchange.ix_id,
                prefix=exchange.prefix,
                create_rs=exchange.route_server,
            )

        skyseed_autonomous_systems = tuple(
            self._apply_skyseed_autonomous_system(base, as_specification)
            for as_specification in specification.skyseed_autonomous_systems
        )
        ordinary_autonomous_systems = tuple(
            self._apply_ordinary_autonomous_system(base, as_specification)
            for as_specification in specification.ordinary_autonomous_systems
        )
        self._add_routing_layers(
            emulator,
            base,
            skyseed_autonomous_systems,
            specification,
        )
        simulator_input = SimulatorStaticInput.from_autonomous_systems(
            skyseed_autonomous_systems
        )
        return StaticEmulatorApplyResult(
            emulator=emulator,
            skyseed_autonomous_systems=skyseed_autonomous_systems,
            ordinary_autonomous_systems=ordinary_autonomous_systems,
            simulator_input=simulator_input,
        )

    @staticmethod
    def _apply_skyseed_autonomous_system(
        base: SatelliteBase,
        specification: SkyseedAutonomousSystemSpecification,
    ) -> SkyseedAutonomousSystem:
        autonomous_system = SkyseedAutonomousSystem(specification.asn)
        autonomous_system.setIbgpMode("disabled")
        base.setAutonomousSystem(autonomous_system)

        for shell_specification in specification.shells:
            orbit_inputs = _expand_orbit_inputs(shell_specification)
            shell = autonomous_system.create_shell(shell_specification.shell_id)
            for orbit_id, parameters, satellite_inputs in orbit_inputs:
                orbit = shell.create_orbit(orbit_id, parameters=parameters)
                for satellite_id, orbit_index, anomaly, payload in satellite_inputs:
                    orbit.create_satellite(
                        satellite_id,
                        orbit_index=orbit_index,
                        mean_anomaly_deg=anomaly,
                        payload=payload,
                    )

        for station_specification in specification.ground_stations:
            station = autonomous_system.create_ground_station(
                ground_station_id_for(station_specification),
                location=GroundStationLocation(
                    latitude_deg=station_specification.latitude_deg,
                    longitude_deg=station_specification.longitude_deg,
                    elevation_m=station_specification.elevation_m,
                ),
                station_type=station_specification.station_type,
                operator=station_specification.operator,
                display_name=station_specification.display_name,
            )
            for attachment in station_specification.ix_attachments:
                station.joinNetwork(f"ix{attachment.ix_id}", attachment.address)
        return autonomous_system

    @staticmethod
    def _apply_ordinary_autonomous_system(
        base: SatelliteBase,
        specification: OrdinaryAutonomousSystemSpecification,
    ) -> AutonomousSystem:
        autonomous_system = base.createAutonomousSystem(specification.asn)
        for network in specification.networks:
            autonomous_system.createNetwork(network.network_id, network.prefix)
        for router_specification in specification.routers:
            router = autonomous_system.createRouter(router_specification.router_id)
            for attachment in router_specification.network_attachments:
                router.joinNetwork(attachment.network_id, attachment.address)
            for attachment in router_specification.ix_attachments:
                router.joinNetwork(f"ix{attachment.ix_id}", attachment.address)
        return autonomous_system

    @staticmethod
    def _add_routing_layers(
        emulator: Emulator,
        base: SatelliteBase,
        skyseed_autonomous_systems: tuple[SkyseedAutonomousSystem, ...],
        specification: StaticEmulatorScenarioSpecification,
    ) -> None:
        if not specification.ebgp_peerings:
            emulator.addLayer(base)
            return

        ospf = Ospf()
        ibgp = Ibgp()
        for autonomous_system in skyseed_autonomous_systems:
            ospf.maskAsn(autonomous_system.getAsn())
            ibgp.maskAsn(autonomous_system.getAsn())
        ebgp = Ebgp()
        for peering in specification.ebgp_peerings:
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
    specification: ShellSpecification,
) -> tuple[_OrbitInput, ...]:
    """Expand one immutable Shell input without creating an entity."""

    if specification.generation is None:
        return tuple(
            (
                orbit.orbit_id,
                OrbitParameters(
                    altitude_km=specification.altitude_km,
                    inclination_deg=specification.inclination_deg,
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
            for orbit in specification.orbits
        )

    generation = specification.generation
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
        orbit_id = f"{specification.shell_id}-orbit-{orbit_index + 1:03d}"
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
                    altitude_km=specification.altitude_km,
                    inclination_deg=specification.inclination_deg,
                    right_ascension_deg=right_ascension,
                ),
                satellites,
            )
        )
    return tuple(inputs)
