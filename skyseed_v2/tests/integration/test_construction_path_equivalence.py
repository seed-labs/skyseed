from pathlib import Path

from yaml import safe_load

from examples.complete_static_emulator.main import build_emulator
from skyseed_v2.static_emulator import create_static_emulator
from skyseed_v2.configuration import load_static_emulator_config
from skyseed_v2.configuration import StaticEmulatorConfig
from skyseed_v2.seedemu_integration import SkyseedDockerCompiler
from skyseed_v2.simulator_input_generation import create_simulator_static_input


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


def test_handwritten_and_file_inputs_share_entity_and_compiler_semantics(
    tmp_path: Path,
) -> None:
    manual_emulator, manual_as, manual_simulator_input = build_emulator()

    config = load_static_emulator_config(FIXTURE)
    assert isinstance(config, StaticEmulatorConfig)
    file_result = create_static_emulator(config)
    file_as = file_result.skyseed_autonomous_systems[0]
    file_simulator_input = create_simulator_static_input(
        file_result.skyseed_autonomous_systems
    )

    assert manual_simulator_input == file_simulator_input
    assert tuple(
        satellite.satellite_id for satellite in manual_as.satellites()
    ) == tuple(satellite.satellite_id for satellite in file_as.satellites())
    assert tuple(
        station.ground_station_id for station in manual_as.ground_stations()
    ) == tuple(
        station.ground_station_id for station in file_as.ground_stations()
    )

    manual_emulator.render()
    file_result.emulator.render()
    for entity_type, entity_id in (
        ("satnode", "satellite-0"),
        ("satnode", "satellite-1"),
        ("rnode", "gs-new-york"),
    ):
        assert manual_emulator.getRegistry().get(
            "65001", entity_type, entity_id
        ) is not None
        assert file_result.emulator.getRegistry().get(
            "65001", entity_type, entity_id
        ) is not None

    manual_output = tmp_path / "manual"
    file_output = tmp_path / "file"
    manual_emulator.compile(
        SkyseedDockerCompiler(internet_map_enabled=False),
        str(manual_output),
    )
    file_result.emulator.compile(
        SkyseedDockerCompiler(internet_map_enabled=False),
        str(file_output),
    )
    manual_compose = safe_load(
        (manual_output / "docker-compose.yml").read_text(encoding="utf-8")
    )
    file_compose = safe_load(
        (file_output / "docker-compose.yml").read_text(encoding="utf-8")
    )

    assert len(manual_compose["services"]) == len(file_compose["services"]) == 6
    assert entity_semantics(manual_compose) == entity_semantics(file_compose)
