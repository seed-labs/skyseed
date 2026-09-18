from datetime import datetime, timezone
from pathlib import Path

from seedemu.compiler import Docker
from seedemu.core import Emulator
from seedemu.layers import Base
from yaml import compose as compose_yaml
from yaml import safe_load
from yaml.nodes import MappingNode, Node, SequenceNode

from skyseed_v2.emulator.entities import OrbitParameters
from skyseed_v2.emulator.seedemu import EmulatorAutonomousSystem


METADATA_PREFIX = "org.seedsecuritylabs.seedemu.meta."


def compile_two_satellites(
    output: Path,
    *,
    internet_map_enabled: bool = False,
):
    emulator = Emulator()
    base = Base()
    satellite_as = EmulatorAutonomousSystem(65000)
    base.setAutonomousSystem(satellite_as)
    orbit = satellite_as.create_shell("shell-1").create_orbit(
        "orbit-1",
        parameters=OrbitParameters(
            550.0, 53.0, 15.0, datetime(2026, 1, 1, tzinfo=timezone.utc)
        ),
    )
    satellites = orbit.create_satellites(2, satellite_id_prefix="satellite")
    emulator.addLayer(base)
    emulator.render()
    compiler = Docker(
        namingScheme="as{asn}{role}-{displayName}",
        internetMapEnabled=internet_map_enabled,
    )
    emulator.compile(compiler, str(output))
    compose_text = (output / "docker-compose.yml").read_text()
    return emulator, compiler, safe_load(compose_text), compose_text, satellites


def satellite_services(compose):
    return {
        name: service
        for name, service in compose["services"].items()
        if service.get("labels", {}).get(
            f"{METADATA_PREFIX}skyseed.entity_type"
        )
        == "Satellite"
    }


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


def test_standard_compiler_dispatches_unique_extension_nodes(tmp_path) -> None:
    emulator, compiler, compose, _, satellites = compile_two_satellites(
        tmp_path / "output"
    )
    services = satellite_services(compose)

    assert type(compiler) is Docker
    assert set(services) == {
        "extnode_65000_satellite-0",
        "extnode_65000_satellite-1",
    }
    assert {
        service["container_name"] for service in services.values()
    } == {"as65000ext-satellite-0", "as65000ext-satellite-1"}
    assert emulator.getRegistry().getByType("65000", "extnode") == list(
        satellites
    )


def test_satellite_services_are_explicitly_networkless(tmp_path) -> None:
    emulator, _, compose, _, _ = compile_two_satellites(tmp_path / "output")

    assert compose["networks"] == {}
    assert emulator.getRegistry().getByType("65000", "net") == []
    for service in satellite_services(compose).values():
        assert service["network_mode"] == "none"
        assert "networks" not in service


def test_remote_image_satellites_keep_scalar_build_context(tmp_path) -> None:
    _, _, compose, _, _ = compile_two_satellites(tmp_path / "output")

    for service_name, service in satellite_services(compose).items():
        assert service["build"] == f"./{service_name}"


def test_public_node_labels_survive_standard_compiler_metadata(tmp_path) -> None:
    _, _, compose, compose_text, _ = compile_two_satellites(
        tmp_path / "output"
    )

    document = compose_yaml(compose_text)
    assert isinstance(document, MappingNode)
    assert_no_duplicate_yaml_keys(document)
    for service in satellite_services(compose).values():
        labels = service["labels"]
        assert labels[f"{METADATA_PREFIX}role"] == "ExtensionNode"
        assert labels[
            f"{METADATA_PREFIX}skyseed.entity_type"
        ] == "Satellite"
        assert labels[f"{METADATA_PREFIX}skyseed.shell_id"] == "shell-1"
        assert labels[f"{METADATA_PREFIX}skyseed.orbit_id"] == "orbit-1"
        assert labels[
            f"{METADATA_PREFIX}skyseed.satellite_id"
        ] in {"satellite-0", "satellite-1"}
        assert not any("runtime_node" in key for key in labels)
        assert not any(
            key.startswith(f"{METADATA_PREFIX}net.") for key in labels
        )


def test_compiler_and_as_inventory_share_same_satellite_instances(
    tmp_path,
) -> None:
    emulator, _, _, _, satellites = compile_two_satellites(
        tmp_path / "output"
    )

    registered = emulator.getRegistry().getByType("65000", "extnode")
    assert all(
        created is compiled
        for created, compiled in zip(satellites, registered, strict=True)
    )
    for satellite in satellites:
        assert satellite.hasAttribute("__soft_install_tiers")
        assert satellite.getAttribute("__soft_install_tiers")


def test_default_map_preserves_the_published_image(tmp_path) -> None:
    output = tmp_path / "output"
    _, _, compose, _, _ = compile_two_satellites(
        output,
        internet_map_enabled=True,
    )

    map_service = compose["services"]["seedemu_internet_map"]
    assert "build" not in map_service
    assert map_service["image"] == "handsonsecurity/seedemu-internetmap:2.0"
    assert not (output / "seedemu-internet-map").exists()
