from seedemu.core import Emulator

from skyseed_v2 import (
    EbgpPeeringSpecification,
    EbgpRouterReferenceSpecification,
    GroundStationSpecification,
    InternetExchangeAttachmentSpecification,
    InternetExchangeSpecification,
    LocalNetworkAttachmentSpecification,
    OrbitSpecification,
    OrdinaryAutonomousSystemSpecification,
    OrdinaryNetworkSpecification,
    OrdinaryRouterSpecification,
    SatelliteSpecification,
    ShellSpecification,
    SkyseedAutonomousSystemSpecification,
    StaticEmulatorScenarioBuilder,
    StaticEmulatorScenarioSpecification,
)


def explicit_shell(shell_id: str, satellite_id: str) -> ShellSpecification:
    return ShellSpecification(
        shell_id=shell_id,
        altitude_km=550.0,
        inclination_deg=53.0,
        orbits=(
            OrbitSpecification(
                orbit_id=f"{shell_id}-orbit",
                right_ascension_deg=15.0,
                satellites=(
                    SatelliteSpecification(
                        satellite_id=satellite_id,
                        index_in_orbit=0,
                        mean_anomaly_deg=0.0,
                    ),
                ),
            ),
        ),
    )


def ground_station(
    station_id: str,
    *,
    ix_id: int | None = None,
    address: str | None = None,
) -> GroundStationSpecification:
    attachments = ()
    if ix_id is not None:
        assert address is not None
        attachments = (InternetExchangeAttachmentSpecification(ix_id, address),)
    return GroundStationSpecification(
        ground_station_id=station_id,
        display_name=station_id,
        latitude_deg=40.0,
        longitude_deg=-74.0,
        elevation_m=10.0,
        operator="descriptive-only",
        station_type="gateway",
        ix_attachments=attachments,
    )


def test_builder_supports_satellite_only_ground_only_and_multiple_skyseed_as() -> None:
    specification = StaticEmulatorScenarioSpecification(
        skyseed_autonomous_systems=(
            SkyseedAutonomousSystemSpecification(
                65001,
                shells=(explicit_shell("shell-a", "satellite-a"),),
            ),
            SkyseedAutonomousSystemSpecification(
                65002,
                ground_stations=(ground_station("gs-a"),),
            ),
        )
    )

    result = StaticEmulatorScenarioBuilder().build(specification)

    assert isinstance(result.emulator, Emulator)
    assert tuple(
        autonomous_system.getAsn()
        for autonomous_system in result.skyseed_autonomous_systems
    ) == (65001, 65002)
    assert tuple(shell.shell_id for shell in result.shell_population.shells) == (
        "shell-a",
    )
    assert tuple(
        satellite.satellite_id
        for satellite in result.shell_population.satellites
    ) == ("satellite-a",)
    assert tuple(
        station.ground_station_id
        for station in result.ground_station_population.ground_stations
    ) == ("gs-a",)
    assert len(result.catalog.list_satellites()) == 1
    assert len(result.catalog.list_ground_stations()) == 1


def test_builder_supports_mixed_as_ground_network_ix_and_explicit_ebgp() -> None:
    specification = StaticEmulatorScenarioSpecification(
        skyseed_autonomous_systems=(
            SkyseedAutonomousSystemSpecification(
                65001,
                shells=(explicit_shell("shell-a", "satellite-a"),),
                ground_stations=(
                    ground_station("gs-a", ix_id=100, address="10.100.0.10"),
                ),
            ),
        ),
        internet_exchanges=(
            InternetExchangeSpecification(100, "10.100.0.0/24"),
        ),
        ordinary_autonomous_systems=(
            OrdinaryAutonomousSystemSpecification(
                65002,
                networks=(
                    OrdinaryNetworkSpecification("service-net", "10.202.0.0/24"),
                ),
                routers=(
                    OrdinaryRouterSpecification(
                        "ground-router",
                        network_attachments=(
                            LocalNetworkAttachmentSpecification(
                                "service-net", "10.202.0.254"
                            ),
                        ),
                        ix_attachments=(
                            InternetExchangeAttachmentSpecification(
                                100, "10.100.0.254"
                            ),
                        ),
                    ),
                ),
            ),
        ),
        ebgp_peerings=(
            EbgpPeeringSpecification(
                100,
                EbgpRouterReferenceSpecification(65001, "gs-a"),
                EbgpRouterReferenceSpecification(65002, "ground-router"),
            ),
        ),
    )

    result = StaticEmulatorScenarioBuilder().build(specification)
    result.emulator.render()
    station = result.ground_station_population.ground_stations[0]
    router = result.ground_routers[0]
    station_bird = {
        file.get()[0]: file.get()[1] for file in station.getFiles()
    }["/etc/bird/bird.conf"]
    router_bird = {file.get()[0]: file.get()[1] for file in router.getFiles()}[
        "/etc/bird/bird.conf"
    ]

    assert len(result.shell_population.shells) == 1
    assert len(result.shell_population.orbits) == 1
    assert len(result.shell_population.satellites) == 1
    assert len(result.catalog.list_nodes()) == 2
    assert station.getInterfaces()[0].getNet().getName() == "ix100"
    assert "neighbor 10.100.0.254 as 65002" in station_bird
    assert "protocol ospf" not in station_bird
    assert "neighbor 10.100.0.10 as 65001" in router_bird
    assert "protocol ospf" in router_bird


def test_same_input_rebuilds_the_same_static_identity_and_catalog_order() -> None:
    specification = StaticEmulatorScenarioSpecification(
        skyseed_autonomous_systems=(
            SkyseedAutonomousSystemSpecification(
                65001,
                shells=(
                    explicit_shell("shell-b", "satellite-b"),
                    explicit_shell("shell-a", "satellite-a"),
                ),
                ground_stations=(ground_station("gs-a"),),
            ),
        )
    )

    first = StaticEmulatorScenarioBuilder().build(specification)
    second = StaticEmulatorScenarioBuilder().build(specification)

    assert tuple(
        descriptor.node_id for descriptor in first.catalog.list_nodes()
    ) == tuple(descriptor.node_id for descriptor in second.catalog.list_nodes())
    assert tuple(shell.shell_id for shell in first.shell_population.shells) == (
        "shell-a",
        "shell-b",
    )
    assert first.emulator is not second.emulator
