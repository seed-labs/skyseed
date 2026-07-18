from seedemu.core import Emulator

from skyseed_v2.scenario_application import StaticEmulatorSpecificationApplier
from skyseed_v2.specifications import (
    EbgpPeeringSpecification,
    EbgpRouterReferenceSpecification,
    GroundStationSpecification,
    InOrbitPhaseDistributionSpecification,
    InternetExchangeAttachmentSpecification,
    InternetExchangeSpecification,
    LocalNetworkAttachmentSpecification,
    OrbitGenerationSpecification,
    OrbitSpecification,
    OrdinaryAutonomousSystemSpecification,
    OrdinaryNetworkSpecification,
    OrdinaryRouterSpecification,
    SatelliteSpecification,
    ShellSpecification,
    SkyseedAutonomousSystemSpecification,
    StaticEmulatorScenarioSpecification,
)
from skyseed_v2.entities import Orbit, Shell
from skyseed_v2.seed import SkyseedAutonomousSystem


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


def generated_shell(shell_id: str) -> ShellSpecification:
    return ShellSpecification(
        shell_id=shell_id,
        altitude_km=560.0,
        inclination_deg=97.6,
        generation=OrbitGenerationSpecification(
            orbit_count=2,
            satellites_per_orbit=3,
            phase=InOrbitPhaseDistributionSpecification(
                alternating_orbit_offset_deg=5.0
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


def test_applier_supports_satellite_only_ground_only_and_multiple_skyseed_as() -> None:
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

    result = StaticEmulatorSpecificationApplier().apply(specification)
    satellite_as, station_as = result.skyseed_autonomous_systems

    assert isinstance(result.emulator, Emulator)
    assert tuple(
        autonomous_system.getAsn()
        for autonomous_system in result.skyseed_autonomous_systems
    ) == (65001, 65002)
    assert tuple(shell.shell_id for shell in satellite_as.shells) == ("shell-a",)
    assert tuple(
        satellite.satellite_id for satellite in satellite_as.satellites()
    ) == ("satellite-a",)
    assert tuple(
        station.ground_station_id for station in station_as.ground_stations()
    ) == ("gs-a",)
    assert len(result.simulator_input.list_satellites()) == 1
    assert len(result.simulator_input.list_ground_stations()) == 1


def test_applier_calls_the_approved_public_entity_creation_methods(
    monkeypatch,
) -> None:
    calls: list[str] = []

    def record(cls, method_name: str) -> None:
        original = getattr(cls, method_name)

        def wrapper(self, *args, **kwargs):
            calls.append(f"{cls.__name__}.{method_name}")
            return original(self, *args, **kwargs)

        monkeypatch.setattr(cls, method_name, wrapper)

    record(SkyseedAutonomousSystem, "create_shell")
    record(Shell, "create_orbit")
    record(Orbit, "create_satellite")
    record(SkyseedAutonomousSystem, "create_ground_station")

    specification = StaticEmulatorScenarioSpecification(
        skyseed_autonomous_systems=(
            SkyseedAutonomousSystemSpecification(
                65001,
                shells=(explicit_shell("shell-a", "satellite-a"),),
                ground_stations=(ground_station("gs-a"),),
            ),
        )
    )
    StaticEmulatorSpecificationApplier().apply(specification)

    assert calls == [
        "SkyseedAutonomousSystem.create_shell",
        "Shell.create_orbit",
        "Orbit.create_satellite",
        "SkyseedAutonomousSystem.create_ground_station",
    ]


def test_applier_supports_ground_network_ix_and_explicit_ebgp() -> None:
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

    result = StaticEmulatorSpecificationApplier().apply(specification)
    result.emulator.render()
    skyseed_as = result.skyseed_autonomous_systems[0]
    station = skyseed_as.get_ground_station("gs-a")
    router = result.ordinary_autonomous_systems[0].getRouter("ground-router")
    station_bird = {
        file.get()[0]: file.get()[1] for file in station.getFiles()
    }["/etc/bird/bird.conf"]
    router_bird = {file.get()[0]: file.get()[1] for file in router.getFiles()}[
        "/etc/bird/bird.conf"
    ]

    assert len(skyseed_as.shells) == 1
    assert len(skyseed_as.shells[0].orbits) == 1
    assert len(skyseed_as.satellites()) == 1
    assert len(result.simulator_input.list_nodes()) == 2
    assert station.getInterfaces()[0].getNet().getName() == "ix100"
    assert "neighbor 10.100.0.254 as 65002" in station_bird
    assert "protocol ospf" not in station_bird
    assert "neighbor 10.100.0.10 as 65001" in router_bird
    assert "protocol ospf" in router_bird


def test_generated_input_is_stable_without_a_population_abstraction() -> None:
    specification = StaticEmulatorScenarioSpecification(
        skyseed_autonomous_systems=(
            SkyseedAutonomousSystemSpecification(
                65001,
                shells=(generated_shell("shell-b"), generated_shell("shell-a")),
                ground_stations=(ground_station("gs-a"),),
            ),
        )
    )

    first = StaticEmulatorSpecificationApplier().apply(specification)
    second = StaticEmulatorSpecificationApplier().apply(specification)
    first_as = first.skyseed_autonomous_systems[0]
    second_as = second.skyseed_autonomous_systems[0]

    assert tuple(
        item.node_id for item in first.simulator_input.list_nodes()
    ) == tuple(item.node_id for item in second.simulator_input.list_nodes())
    assert tuple(shell.shell_id for shell in first_as.shells) == (
        "shell-a",
        "shell-b",
    )
    assert tuple(
        satellite.satellite_id for satellite in first_as.satellites()
    ) == tuple(satellite.satellite_id for satellite in second_as.satellites())
    assert len(first_as.satellites()) == 12
    assert first.emulator is not second.emulator
