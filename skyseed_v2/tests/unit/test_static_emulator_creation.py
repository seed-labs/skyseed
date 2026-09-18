from datetime import datetime, timezone

from seedemu.core import Emulator
from seedemu.layers import Base

from skyseed_v2.emulator.construction import create_static_emulator
from skyseed_v2.configuration.emulator import (
    EbgpPeeringConfig,
    EbgpRouterReference,
    GeneratedOrbitLayout,
    GroundStationConfig,
    InOrbitPhaseDistribution,
    InternetExchangeAttachment,
    InternetExchangeConfig,
    LocalNetworkAttachment,
    OrbitConfig,
    OrdinaryAutonomousSystemConfig,
    NetworkConfig,
    RouterConfig,
    SatelliteConfig,
    ShellConfig,
    SkyseedAutonomousSystemConfig,
    StaticEmulatorConfig,
)
from skyseed_v2.emulator.entities import EmulatorOrbit, EmulatorShell
from skyseed_v2.emulator.seedemu import EmulatorAutonomousSystem
from skyseed_v2.adapters.emulator_to_simulator import create_simulator_static_input


def explicit_shell(shell_id: str, satellite_id: str) -> ShellConfig:
    return ShellConfig(
        shell_id=shell_id,
        altitude_km=550.0,
        inclination_deg=53.0,
        epoch_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
        explicit_orbits=(
            OrbitConfig(
                orbit_id=f"{shell_id}-orbit",
                right_ascension_deg=15.0,
                satellites=(
                    SatelliteConfig(
                        satellite_id=satellite_id,
                        index_in_orbit=0,
                        mean_anomaly_deg=0.0,
                    ),
                ),
            ),
        ),
    )


def generated_shell(shell_id: str) -> ShellConfig:
    return ShellConfig(
        shell_id=shell_id,
        altitude_km=560.0,
        inclination_deg=97.6,
        epoch_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
        generated_orbit_layout=GeneratedOrbitLayout(
            orbit_count=2,
            satellites_per_orbit=3,
            in_orbit_phase_distribution=InOrbitPhaseDistribution(
                alternating_orbit_offset_deg=5.0
            ),
        ),
    )


def ground_station(
    station_id: str,
    *,
    ix_id: int | None = None,
    address: str | None = None,
    max_simultaneous_satellite_accesses: int | None = None,
) -> GroundStationConfig:
    attachments = ()
    if ix_id is not None:
        assert address is not None
        attachments = (InternetExchangeAttachment(ix_id, address),)
    return GroundStationConfig(
        ground_station_id=station_id,
        display_name=station_id,
        latitude_deg=40.0,
        longitude_deg=-74.0,
        elevation_m=10.0,
        operator="descriptive-only",
        station_type="gateway",
        ix_attachments=attachments,
        max_simultaneous_satellite_accesses=(
            max_simultaneous_satellite_accesses
        ),
    )


def base_for(emulator: Emulator) -> Base:
    base = emulator.getLayer("Base")
    assert isinstance(base, Base)
    return base


def skyseed_autonomous_systems_for(
    emulator: Emulator,
) -> tuple[EmulatorAutonomousSystem, ...]:
    base = base_for(emulator)
    return tuple(
        autonomous_system
        for asn in sorted(base.getAsns())
        if isinstance(
            (autonomous_system := base.getAutonomousSystem(asn)),
            EmulatorAutonomousSystem,
        )
    )


def test_creation_supports_satellite_only_ground_only_and_multiple_skyseed_as() -> None:
    config = StaticEmulatorConfig(
        skyseed_autonomous_systems=(
            SkyseedAutonomousSystemConfig(
                65001,
                shells=(explicit_shell("shell-a", "satellite-a"),),
            ),
            SkyseedAutonomousSystemConfig(
                65002,
                ground_stations=(
                    ground_station(
                        "gs-a", max_simultaneous_satellite_accesses=4
                    ),
                ),
            ),
        )
    )

    emulator = create_static_emulator(config)
    skyseed_autonomous_systems = skyseed_autonomous_systems_for(emulator)
    satellite_as, station_as = skyseed_autonomous_systems
    simulator_input = create_simulator_static_input(skyseed_autonomous_systems)

    assert isinstance(emulator, Emulator)
    assert not hasattr(emulator, "emulator")
    assert tuple(
        autonomous_system.getAsn()
        for autonomous_system in skyseed_autonomous_systems
    ) == (65001, 65002)
    assert tuple(shell.shell_id for shell in satellite_as.shells) == ("shell-a",)
    assert tuple(
        satellite.satellite_id for satellite in satellite_as.satellites()
    ) == ("satellite-a",)
    assert tuple(
        station.ground_station_id for station in station_as.ground_stations()
    ) == ("gs-a",)
    assert (
        station_as.ground_stations()[0].max_simultaneous_satellite_accesses
        == 4
    )
    assert len(simulator_input.list_satellites()) == 1
    assert len(simulator_input.list_ground_stations()) == 1
    assert (
        simulator_input.list_ground_stations()[
            0
        ].max_simultaneous_satellite_accesses
        == 4
    )
    orbit = satellite_as.get_shell("shell-a").get_orbit("shell-a-orbit")
    assert orbit.parameters.epoch_utc == datetime(
        2026, 1, 1, tzinfo=timezone.utc
    )
    assert simulator_input.list_satellites()[0].epoch_utc == (
        orbit.parameters.epoch_utc
    )


def test_generated_shell_copies_common_epoch_into_every_orbit() -> None:
    config = StaticEmulatorConfig(
        skyseed_autonomous_systems=(
            SkyseedAutonomousSystemConfig(
                65001,
                shells=(generated_shell("generated"),),
            ),
        )
    )
    emulator = create_static_emulator(config)
    skyseed_as = skyseed_autonomous_systems_for(emulator)[0]
    orbits = skyseed_as.get_shell("generated").orbits

    assert len(orbits) == 2
    assert all(
        orbit.parameters.epoch_utc
        == datetime(2026, 1, 1, tzinfo=timezone.utc)
        for orbit in orbits
    )
    assert len({id(orbit.parameters) for orbit in orbits}) == 2
    assert [orbit.satellites[0].mean_anomaly_deg for orbit in orbits] == [
        0.0,
        5.0,
    ]


def test_generated_shell_applies_linear_phase_progression_per_orbit() -> None:
    shell_config = ShellConfig(
        shell_id="linear",
        altitude_km=560.0,
        inclination_deg=97.6,
        epoch_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
        generated_orbit_layout=GeneratedOrbitLayout(
            orbit_count=4,
            satellites_per_orbit=3,
            in_orbit_phase_distribution=InOrbitPhaseDistribution(
                start_deg=10.0,
                step_deg=120.0,
                orbit_phase_step_deg=7.5,
            ),
        ),
    )
    emulator = create_static_emulator(
        StaticEmulatorConfig(
            skyseed_autonomous_systems=(
                SkyseedAutonomousSystemConfig(65001, shells=(shell_config,)),
            )
        )
    )
    orbits = skyseed_autonomous_systems_for(emulator)[0].get_shell(
        "linear"
    ).orbits

    assert [orbit.satellites[0].mean_anomaly_deg for orbit in orbits] == [
        10.0,
        17.5,
        25.0,
        32.5,
    ]
    assert [orbit.satellites[1].mean_anomaly_deg for orbit in orbits] == [
        130.0,
        137.5,
        145.0,
        152.5,
    ]


def test_creation_calls_the_approved_public_entity_creation_methods(
    monkeypatch,
) -> None:
    calls: list[str] = []

    def record(cls, method_name: str) -> None:
        original = getattr(cls, method_name)

        def wrapper(self, *args, **kwargs):
            calls.append(f"{cls.__name__}.{method_name}")
            return original(self, *args, **kwargs)

        monkeypatch.setattr(cls, method_name, wrapper)

    record(EmulatorAutonomousSystem, "create_shell")
    record(EmulatorShell, "create_orbit")
    record(EmulatorOrbit, "create_satellite")
    record(EmulatorAutonomousSystem, "create_ground_station")

    config = StaticEmulatorConfig(
        skyseed_autonomous_systems=(
            SkyseedAutonomousSystemConfig(
                65001,
                shells=(explicit_shell("shell-a", "satellite-a"),),
                ground_stations=(ground_station("gs-a"),),
            ),
        )
    )
    create_static_emulator(config)

    assert calls == [
        "EmulatorAutonomousSystem.create_shell",
        "EmulatorShell.create_orbit",
        "EmulatorOrbit.create_satellite",
        "EmulatorAutonomousSystem.create_ground_station",
    ]


def test_creation_supports_ground_network_ix_and_explicit_ebgp() -> None:
    config = StaticEmulatorConfig(
        skyseed_autonomous_systems=(
            SkyseedAutonomousSystemConfig(
                65001,
                shells=(explicit_shell("shell-a", "satellite-a"),),
                ground_stations=(
                    ground_station("gs-a", ix_id=100, address="10.100.0.10"),
                ),
            ),
        ),
        internet_exchanges=(
            InternetExchangeConfig(100, "10.100.0.0/24"),
        ),
        ordinary_autonomous_systems=(
            OrdinaryAutonomousSystemConfig(
                65002,
                networks=(
                    NetworkConfig("service-net", "10.202.0.0/24"),
                ),
                routers=(
                    RouterConfig(
                        "ground-router",
                        network_attachments=(
                            LocalNetworkAttachment(
                                "service-net", "10.202.0.254"
                            ),
                        ),
                        ix_attachments=(
                            InternetExchangeAttachment(
                                100, "10.100.0.254"
                            ),
                        ),
                    ),
                ),
            ),
        ),
        ebgp_peerings=(
            EbgpPeeringConfig(
                100,
                (
                    EbgpRouterReference(65001, "gs-a"),
                    EbgpRouterReference(65002, "ground-router"),
                ),
            ),
        ),
    )

    emulator = create_static_emulator(config)
    base = base_for(emulator)
    emulator.render()
    skyseed_autonomous_systems = skyseed_autonomous_systems_for(emulator)
    skyseed_as = skyseed_autonomous_systems[0]
    station = skyseed_as.get_ground_station("gs-a")
    router = base.getAutonomousSystem(65002).getRouter("ground-router")
    station_bird = {
        file.get()[0]: file.get()[1] for file in station.getFiles()
    }["/etc/bird/bird.conf"]
    router_bird = {file.get()[0]: file.get()[1] for file in router.getFiles()}[
        "/etc/bird/bird.conf"
    ]

    assert len(skyseed_as.shells) == 1
    assert len(skyseed_as.shells[0].orbits) == 1
    assert len(skyseed_as.satellites()) == 1
    assert len(
        create_simulator_static_input(
            skyseed_autonomous_systems
        ).list_nodes()
    ) == 2
    assert station.getInterfaces()[0].getNet().getName() == "ix100"
    assert "neighbor 10.100.0.254 as 65002" in station_bird
    assert "protocol ospf" not in station_bird
    assert "neighbor 10.100.0.10 as 65001" in router_bird
    assert "protocol ospf" in router_bird


def test_generated_input_is_stable_without_a_population_abstraction() -> None:
    config = StaticEmulatorConfig(
        skyseed_autonomous_systems=(
            SkyseedAutonomousSystemConfig(
                65001,
                shells=(generated_shell("shell-b"), generated_shell("shell-a")),
                ground_stations=(ground_station("gs-a"),),
            ),
        )
    )

    first = create_static_emulator(config)
    second = create_static_emulator(config)
    first_autonomous_systems = skyseed_autonomous_systems_for(first)
    second_autonomous_systems = skyseed_autonomous_systems_for(second)
    first_as = first_autonomous_systems[0]
    second_as = second_autonomous_systems[0]

    first_input = create_simulator_static_input(first_autonomous_systems)
    second_input = create_simulator_static_input(second_autonomous_systems)
    assert tuple(item.node_id for item in first_input.list_nodes()) == tuple(
        item.node_id for item in second_input.list_nodes()
    )
    assert tuple(shell.shell_id for shell in first_as.shells) == (
        "shell-a",
        "shell-b",
    )
    assert tuple(
        satellite.satellite_id for satellite in first_as.satellites()
    ) == tuple(satellite.satellite_id for satellite in second_as.satellites())
    assert len(first_as.satellites()) == 12
    assert first is not second
