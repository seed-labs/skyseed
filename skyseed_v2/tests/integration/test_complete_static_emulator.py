from pathlib import Path

from yaml import compose as compose_yaml
from yaml import safe_load
from yaml.nodes import MappingNode, Node, SequenceNode

from examples.complete_static_emulator.emu import build_emulator
from examples.complete_static_emulator.main import main
from seedemu.compiler import Docker
from seedemu.layers import Base
from skyseed_v2.emulator.seedemu import SkyseedAutonomousSystem
from skyseed_v2.shared import NodeId
from skyseed_v2.simulator.input import (
    GroundStationSimulationInput,
    SatelliteSimulationInput,
)
from skyseed_v2.emulator_to_simulator import create_simulator_static_input


METADATA_PREFIX = "org.seedsecuritylabs.seedemu.meta."


def entity_services(compose: dict) -> dict[str, dict]:
    services = {}
    for service in compose["services"].values():
        labels = service.get("labels", {})
        entity_id = labels.get(f"{METADATA_PREFIX}skyseed.satellite_id") or labels.get(
            f"{METADATA_PREFIX}skyseed.ground_station_id"
        )
        if entity_id is not None:
            services[entity_id] = service
    return services


def assert_no_duplicate_yaml_keys(node: Node, path: str = "$") -> None:
    if isinstance(node, MappingNode):
        keys: set[str] = set()
        for key_node, value_node in node.value:
            key = key_node.value
            assert key not in keys, f"duplicate YAML key at {path}: {key}"
            keys.add(key)
            assert_no_duplicate_yaml_keys(value_node, f"{path}.{key}")
    elif isinstance(node, SequenceNode):
        for index, child in enumerate(node.value):
            assert_no_duplicate_yaml_keys(child, f"{path}[{index}]")


def test_complete_example_input_matches_registry_and_compiler_output(
    tmp_path: Path,
) -> None:
    emulator = build_emulator()
    base = emulator.getLayer("Base")
    assert isinstance(base, Base)
    skyseed_autonomous_systems = tuple(
        autonomous_system
        for asn in sorted(base.getAsns())
        if isinstance(
            (autonomous_system := base.getAutonomousSystem(asn)),
            SkyseedAutonomousSystem,
        )
    )
    skyseed_as = skyseed_autonomous_systems[0]
    simulator_input = create_simulator_static_input(skyseed_autonomous_systems)
    ground_station = skyseed_as.get_ground_station("gs-new-york")

    assert len(simulator_input.list_nodes()) == 3
    assert all(
        isinstance(item, (SatelliteSimulationInput, GroundStationSimulationInput))
        for item in simulator_input.list_nodes()
    )
    assert all(
        isinstance(item.node_id, NodeId)
        for item in simulator_input.list_nodes()
    )

    emulator.render()
    registry = emulator.getRegistry()
    assert registry.get("65001", "extnode", "satellite-0") is (
        skyseed_as.get_satellite("satellite-0")
    )
    assert registry.get("65001", "extnode", "satellite-1") is (
        skyseed_as.get_satellite("satellite-1")
    )
    assert registry.get("65001", "rnode", "gs-new-york") is ground_station
    assert registry.get("65001", "brdnode", "gs-new-york") is ground_station
    assert simulator_input.get_satellite(
        NodeId(65001, "satellite", "satellite-0")
    ).satellite_id == skyseed_as.get_satellite("satellite-0").satellite_id
    assert simulator_input.get_ground_station(
        NodeId(65001, "ground-station", "gs-new-york")
    ).ground_station_id == ground_station.ground_station_id

    output = tmp_path / "output"
    emulator.compile(
        Docker(
            namingScheme="as{asn}{role}-{displayName}",
            internetMapEnabled=True,
        ),
        str(output),
    )
    compose_text = (output / "docker-compose.yml").read_text()
    compose = safe_load(compose_text)
    services = entity_services(compose)
    document = compose_yaml(compose_text)
    assert document is not None
    assert_no_duplicate_yaml_keys(document)

    assert set(services) == {"satellite-0", "satellite-1", "gs-new-york"}
    for satellite_id in ("satellite-0", "satellite-1"):
        service = services[satellite_id]
        labels = service["labels"]
        assert service["network_mode"] == "none"
        assert "networks" not in service
        assert labels[f"{METADATA_PREFIX}role"] == "ExtensionNode"
        assert labels[
            f"{METADATA_PREFIX}skyseed.entity_type"
        ] == "Satellite"
    station_service = services["gs-new-york"]
    station_labels = station_service["labels"]
    assert station_service.get("network_mode") != "none"
    assert list(station_service["networks"]) == ["net_ix_ix100"]
    assert station_labels[f"{METADATA_PREFIX}role"] == "BorderRouter"
    assert station_labels[
        f"{METADATA_PREFIX}skyseed.entity_type"
    ] == "GroundStation"
    for service_name in (
        "brdnode_65001_gs-new-york",
        "brdnode_65002_ground-router",
    ):
        service = compose["services"][service_name]
        dependency = service["depends_on"][0]
        assert service["build"] == f"./{service_name}"
        assert compose["services"][dependency]["image"] == dependency

    assert "SimulatorStaticInput" not in compose_text
    assert "NodeId" not in compose_text
    assert "runtime_node" not in compose_text
    assert "RuntimeNodeReference" not in compose_text


def test_complete_example_cli_creates_a_new_output_parent(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    output = tmp_path / "new-parent" / "output"
    internet_map_source = tmp_path / "internet-map-source"
    internet_map_source.mkdir()
    (internet_map_source / "Dockerfile").write_text("FROM scratch\n")
    monkeypatch.setattr(
        "sys.argv",
        [
            "complete_static_emulator.py",
            "--output",
            str(output),
            "--internet-map-source",
            str(internet_map_source),
        ],
    )

    assert main() == 0

    assert (output / "runtime/docker-compose.yml").is_file()
    for relative_path in (
        "start.sh",
        "cleanup.sh",
        "inspection/index.html",
        "inspection/topology/index.html",
        "inspection/topology/topology.json",
        "inspection/topology/summary.txt",
        "inspection/zone/index.html",
        "inspection/zone/probes.json",
        "inspection/zone/summary.txt",
    ):
        assert (output / relative_path).is_file()
    assert {
        path.name for path in output.iterdir()
    } == {"start.sh", "cleanup.sh", "runtime", "inspection"}
    output_text = capsys.readouterr().out
    assert "compiled complete static Emulator" in output_text
    assert "SimulationInput" not in output_text
    assert "Topology Tool: PASS" in output_text
    assert "Zone Tool: PASS" in output_text
    assert "Tooling overall: PASS" in output_text
