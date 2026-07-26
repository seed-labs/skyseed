from datetime import datetime, timezone
from pathlib import Path

from yaml import safe_load

from examples.complete_static_emulator.emu import build_emulator
from skyseed_v2.emulator.construction import create_static_emulator
from skyseed_v2.configuration.emulator import (
    EbgpPeeringConfig,
    EbgpRouterReference,
    GroundStationConfig,
    InternetExchangeAttachment,
    InternetExchangeConfig,
    LocalNetworkAttachment,
    NetworkConfig,
    OrbitConfig,
    OrdinaryAutonomousSystemConfig,
    RouterConfig,
    SatelliteConfig,
    ShellConfig,
    SkyseedAutonomousSystemConfig,
    StaticEmulatorConfig,
    load_static_emulator_config,
)
from skyseed_v2.emulator.seedemu import SatelliteBase, SkyseedDockerCompiler
from skyseed_v2.emulator_to_simulator import create_simulator_static_input


FIXTURE = Path(__file__).parents[1] / "fixtures/complete_equivalent.yaml"
METADATA_PREFIX = "org.seedsecuritylabs.seedemu.meta."


def entity_semantics(compose: dict) -> dict[str, dict]:
    semantics = {}
    for service in compose["services"].values():
        labels = service.get("labels", {})
        entity_id = labels.get(f"{METADATA_PREFIX}skyseed.satellite_id")
        entity_id = entity_id or labels.get(
            f"{METADATA_PREFIX}skyseed.ground_station_id"
        )
        if entity_id is None:
            continue
        semantics[entity_id] = {
            "network_mode": service.get("network_mode"),
            "networks": tuple(service.get("networks", {})),
            "role": labels[f"{METADATA_PREFIX}role"],
            "type": labels[f"{METADATA_PREFIX}type"],
        }
    return semantics


def complete_programmatic_config() -> StaticEmulatorConfig:
    return StaticEmulatorConfig(
        skyseed_autonomous_systems=(
            SkyseedAutonomousSystemConfig(
                asn=65001,
                shells=(
                    ShellConfig(
                        shell_id="shell-1",
                        altitude_km=550.0,
                        inclination_deg=53.0,
                        epoch_utc=datetime(
                            2026, 1, 1, tzinfo=timezone.utc
                        ),
                        explicit_orbits=(
                            OrbitConfig(
                                orbit_id="orbit-1",
                                right_ascension_deg=245.0,
                                satellites=(
                                    SatelliteConfig("satellite-0", 0, 126.0),
                                    SatelliteConfig("satellite-1", 1, 146.0),
                                ),
                            ),
                        ),
                    ),
                ),
                ground_stations=(
                    GroundStationConfig(
                        ground_station_id="gs-new-york",
                        display_name="New York Ground Station",
                        latitude_deg=40.7128,
                        longitude_deg=-74.006,
                        elevation_m=10.0,
                        operator="Skyseed",
                        station_type="gateway",
                        ix_attachments=(
                            InternetExchangeAttachment(100, "10.100.0.101"),
                        ),
                    ),
                ),
            ),
        ),
        internet_exchanges=(
            InternetExchangeConfig(100, "10.100.0.0/24"),
        ),
        ordinary_autonomous_systems=(
            OrdinaryAutonomousSystemConfig(
                asn=65002,
                networks=(NetworkConfig("ground-net", "10.202.0.0/24"),),
                routers=(
                    RouterConfig(
                        "ground-router",
                        network_attachments=(
                            LocalNetworkAttachment(
                                "ground-net", "10.202.0.254"
                            ),
                        ),
                        ix_attachments=(
                            InternetExchangeAttachment(100, "10.100.0.102"),
                        ),
                    ),
                ),
            ),
        ),
        ebgp_peerings=(
            EbgpPeeringConfig(
                100,
                (
                    EbgpRouterReference(65001, "gs-new-york"),
                    EbgpRouterReference(65002, "ground-router"),
                ),
            ),
        ),
    )


def test_handwritten_and_file_inputs_share_entity_and_compiler_semantics(
    tmp_path: Path,
) -> None:
    manual_emulator = build_emulator()
    manual_base = manual_emulator.getLayer("Base")
    assert isinstance(manual_base, SatelliteBase)
    manual_autonomous_systems = manual_base.get_skyseed_autonomous_systems()
    manual_as = manual_autonomous_systems[0]
    manual_simulator_input = create_simulator_static_input(
        manual_autonomous_systems
    )

    config = load_static_emulator_config(FIXTURE)
    assert isinstance(config, StaticEmulatorConfig)
    file_emulator = create_static_emulator(config)
    file_base = file_emulator.getLayer("Base")
    assert isinstance(file_base, SatelliteBase)
    file_autonomous_systems = file_base.get_skyseed_autonomous_systems()
    file_as = file_autonomous_systems[0]
    file_simulator_input = create_simulator_static_input(
        file_autonomous_systems
    )
    programmatic_emulator = create_static_emulator(
        complete_programmatic_config()
    )
    programmatic_base = programmatic_emulator.getLayer("Base")
    assert isinstance(programmatic_base, SatelliteBase)
    programmatic_autonomous_systems = (
        programmatic_base.get_skyseed_autonomous_systems()
    )
    programmatic_simulator_input = create_simulator_static_input(
        programmatic_autonomous_systems
    )

    assert (
        manual_simulator_input
        == file_simulator_input
        == programmatic_simulator_input
    )
    assert tuple(
        satellite.satellite_id for satellite in manual_as.satellites()
    ) == tuple(satellite.satellite_id for satellite in file_as.satellites())
    assert tuple(
        station.ground_station_id for station in manual_as.ground_stations()
    ) == tuple(
        station.ground_station_id for station in file_as.ground_stations()
    )

    manual_emulator.render()
    file_emulator.render()
    programmatic_emulator.render()
    for entity_type, entity_id in (
        ("satnode", "satellite-0"),
        ("satnode", "satellite-1"),
        ("rnode", "gs-new-york"),
    ):
        assert manual_emulator.getRegistry().get(
            "65001", entity_type, entity_id
        ) is not None
        assert file_emulator.getRegistry().get(
            "65001", entity_type, entity_id
        ) is not None
        assert programmatic_emulator.getRegistry().get(
            "65001", entity_type, entity_id
        ) is not None

    manual_output = tmp_path / "manual"
    file_output = tmp_path / "file"
    programmatic_output = tmp_path / "programmatic"
    manual_emulator.compile(
        SkyseedDockerCompiler(internet_map_enabled=False),
        str(manual_output),
    )
    file_emulator.compile(
        SkyseedDockerCompiler(internet_map_enabled=False),
        str(file_output),
    )
    programmatic_emulator.compile(
        SkyseedDockerCompiler(internet_map_enabled=False),
        str(programmatic_output),
    )
    manual_compose = safe_load(
        (manual_output / "docker-compose.yml").read_text(encoding="utf-8")
    )
    file_compose = safe_load(
        (file_output / "docker-compose.yml").read_text(encoding="utf-8")
    )
    programmatic_compose = safe_load(
        (programmatic_output / "docker-compose.yml").read_text(
            encoding="utf-8"
        )
    )

    assert (
        len(manual_compose["services"])
        == len(file_compose["services"])
        == len(programmatic_compose["services"])
        == 6
    )
    assert (
        entity_semantics(manual_compose)
        == entity_semantics(file_compose)
        == entity_semantics(programmatic_compose)
    )
