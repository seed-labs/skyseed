"""Application service that constructs approved static Emulator objects."""

from __future__ import annotations

from dataclasses import dataclass

from seedemu.core import Emulator
from seedemu.layers import Ebgp, Ibgp, Ospf, PeerRelationship, Routing

from skyseed_v2.catalog_builder import SimulationEntityCatalogBuilder
from skyseed_v2.contracts import SimulationEntityCatalog
from skyseed_v2.domain import GroundStation, Orbit, Satellite, Shell
from skyseed_v2.ground_station_builder import (
    GroundStationBuildResult,
    GroundStationBuilder,
)
from skyseed_v2.seed import SatelliteBase, SkyseedAutonomousSystem
from skyseed_v2.shell_population_builder import (
    ShellPopulationBuildResult,
    ShellPopulationBuilder,
)
from skyseed_v2.specifications import StaticEmulatorScenarioSpecification
from skyseed_v2.specifications import ground_station_id_for


@dataclass(frozen=True, slots=True)
class StaticEmulatorBuildResult:
    """Objects created by the static scenario application service."""

    emulator: Emulator
    skyseed_autonomous_systems: tuple[SkyseedAutonomousSystem, ...]
    ordinary_autonomous_systems: tuple[object, ...]
    shell_population: ShellPopulationBuildResult
    ground_station_population: GroundStationBuildResult
    ground_routers: tuple[object, ...]
    catalog: SimulationEntityCatalog
    ebgp: Ebgp | None


class StaticEmulatorScenarioBuilder:
    """Build static entities from generic specs; never parse or compile files."""

    def build(
        self,
        specification: StaticEmulatorScenarioSpecification,
    ) -> StaticEmulatorBuildResult:
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

        skyseed_ases: list[SkyseedAutonomousSystem] = []
        shells: list[Shell] = []
        orbits: list[Orbit] = []
        satellites: list[Satellite] = []
        ground_stations: list[GroundStation] = []
        for autonomous_system_specification in (
            specification.skyseed_autonomous_systems
        ):
            autonomous_system = SkyseedAutonomousSystem(
                autonomous_system_specification.asn
            )
            autonomous_system.setIbgpMode("disabled")
            base.setAutonomousSystem(autonomous_system)
            skyseed_ases.append(autonomous_system)

            if autonomous_system_specification.shells:
                population = ShellPopulationBuilder().build(
                    autonomous_system,
                    autonomous_system_specification.shells,
                )
                shells.extend(population.shells)
                orbits.extend(population.orbits)
                satellites.extend(population.satellites)

            if autonomous_system_specification.ground_stations:
                station_population = GroundStationBuilder().build(
                    autonomous_system,
                    autonomous_system_specification.ground_stations,
                )
                ground_stations.extend(station_population.ground_stations)
                station_by_id = {
                    station.ground_station_id: station
                    for station in station_population.ground_stations
                }
                for station_specification in (
                    autonomous_system_specification.ground_stations
                ):
                    station_id = ground_station_id_for(station_specification)
                    station = station_by_id[station_id]
                    for attachment in station_specification.ix_attachments:
                        station.joinNetwork(
                            f"ix{attachment.ix_id}", attachment.address
                        )

        ordinary_ases: list[object] = []
        ordinary_routers: list[object] = []
        for autonomous_system_specification in (
            specification.ordinary_autonomous_systems
        ):
            autonomous_system = base.createAutonomousSystem(
                autonomous_system_specification.asn
            )
            for network in autonomous_system_specification.networks:
                autonomous_system.createNetwork(network.network_id, network.prefix)
            for router_specification in autonomous_system_specification.routers:
                router = autonomous_system.createRouter(
                    router_specification.router_id
                )
                for attachment in router_specification.network_attachments:
                    router.joinNetwork(attachment.network_id, attachment.address)
                for attachment in router_specification.ix_attachments:
                    router.joinNetwork(
                        f"ix{attachment.ix_id}", attachment.address
                    )
                ordinary_routers.append(router)
            ordinary_ases.append(autonomous_system)

        ebgp: Ebgp | None = None
        if specification.ebgp_peerings:
            ospf = Ospf()
            ibgp = Ibgp()
            for autonomous_system in skyseed_ases:
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
        else:
            emulator.addLayer(base)

        catalog = SimulationEntityCatalogBuilder().build(
            (*skyseed_ases, *ordinary_ases)
        )
        return StaticEmulatorBuildResult(
            emulator=emulator,
            skyseed_autonomous_systems=tuple(skyseed_ases),
            ordinary_autonomous_systems=tuple(ordinary_ases),
            shell_population=ShellPopulationBuildResult(
                shells=tuple(shells),
                orbits=tuple(orbits),
                satellites=tuple(satellites),
            ),
            ground_station_population=GroundStationBuildResult(
                ground_stations=tuple(ground_stations)
            ),
            ground_routers=tuple(ordinary_routers),
            catalog=catalog,
            ebgp=ebgp,
        )
