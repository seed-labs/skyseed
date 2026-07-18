from pathlib import Path

import pytest
from seedemu.core import Emulator
from yaml import compose as compose_yaml
from yaml import safe_load
from yaml.nodes import MappingNode, Node, SequenceNode

from skyseed_v2.entities import OrbitParameters
from skyseed_v2.seed import (
    SATELLITE_REGISTRY_TYPE,
    SatelliteBase,
    SkyseedAutonomousSystem,
    SkyseedDockerCompiler,
)


METADATA_PREFIX = "org.seedsecuritylabs.seedemu.meta."


def compile_two_satellites(output: Path, *, conflicting_metadata: bool = False):
    emulator = Emulator()
    base = SatelliteBase()
    satellite_as = SkyseedAutonomousSystem(65000)
    base.setAutonomousSystem(satellite_as)
    orbit = satellite_as.create_shell("shell-1").create_orbit(
        "orbit-1", parameters=OrbitParameters(550.0, 53.0, 15.0)
    )
    satellites = orbit.create_satellites(2, satellite_id_prefix="satellite")
    emulator.addLayer(base)
    emulator.render()
    if conflicting_metadata:
        for node in emulator.getRegistry().getByType(
            "65000", SATELLITE_REGISTRY_TYPE
        ):
            node.setLabel("role", "Host")
            node.setLabel("type", "hnode")
            node.setLabel("skyseed.entity_type", "Host")
    compiler = SkyseedDockerCompiler(internet_map_enabled=False)
    emulator.compile(compiler, str(output))
    compose_text = (output / "docker-compose.yml").read_text()
    return emulator, compiler, safe_load(compose_text), compose_text, satellites


def satellite_services(compose):
    return {
        name: service
        for name, service in compose["services"].items()
        if service.get("labels", {}).get(
            "org.seedsecuritylabs.seedemu.meta.type"
        )
        == SATELLITE_REGISTRY_TYPE
    }


def mapping_value(mapping: MappingNode, key: str) -> Node:
    for key_node, value_node in mapping.value:
        if key_node.value == key:
            return value_node
    raise KeyError(key)


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


def test_compiler_dispatches_two_unique_satellites_through_formal_output(
    tmp_path,
) -> None:
    emulator, compiler, compose, _, _ = compile_two_satellites(
        tmp_path / "output"
    )
    services = satellite_services(compose)

    assert isinstance(compiler, SkyseedDockerCompiler)
    assert set(services) == {
        "satnode_65000_satellite-0",
        "satnode_65000_satellite-1",
    }
    assert {
        service["container_name"] for service in services.values()
    } == {"as65000sat-satellite-0", "as65000sat-satellite-1"}
    assert emulator.getRegistry().getByType("65000", "hnode") == []


def test_satellite_services_are_explicitly_networkless(tmp_path) -> None:
    emulator, _, compose, _, _ = compile_two_satellites(tmp_path / "output")

    assert "networks" not in compose
    assert emulator.getRegistry().getByType("65000", "net") == []
    for service in satellite_services(compose).values():
        assert service["network_mode"] == "none"
        assert "networks" not in service


def test_satellite_builds_declare_native_buildkit_image_dependencies(
    tmp_path,
) -> None:
    _, _, compose, _, _ = compile_two_satellites(tmp_path / "output")

    for service_name, service in satellite_services(compose).items():
        dependency = service["depends_on"][0]
        assert compose["services"][dependency]["image"] == dependency
        assert service["build"] == {
            "context": f"./{service_name}",
            "additional_contexts": {
                dependency: f"service:{dependency}",
            },
        }


def test_compiler_emits_unique_satellite_metadata_in_raw_compose(tmp_path) -> None:
    _, _, compose, compose_text, _ = compile_two_satellites(
        tmp_path / "output", conflicting_metadata=True
    )

    document = compose_yaml(compose_text)
    assert isinstance(document, MappingNode)
    assert_no_duplicate_yaml_keys(document)
    services_node = mapping_value(document, "services")
    assert isinstance(services_node, MappingNode)

    for service_name, service in satellite_services(compose).items():
        labels = service["labels"]
        assert labels["org.seedsecuritylabs.seedemu.meta.role"] == "Satellite"
        assert labels["org.seedsecuritylabs.seedemu.meta.type"] == "satnode"
        assert labels["org.seedsecuritylabs.seedemu.meta.skyseed.entity_type"] == "Satellite"
        assert labels["org.seedsecuritylabs.seedemu.meta.skyseed.shell_id"] == "shell-1"
        assert labels["org.seedsecuritylabs.seedemu.meta.skyseed.orbit_id"] == "orbit-1"
        assert labels["org.seedsecuritylabs.seedemu.meta.skyseed.satellite_id"] in {
            "satellite-0",
            "satellite-1",
        }
        assert not any("runtime_node" in key for key in labels)
        assert not any(
            key.startswith("org.seedsecuritylabs.seedemu.meta.net.")
            for key in labels
        )
        assert "Host" not in labels.values()

        service_node = mapping_value(services_node, service_name)
        service_text = compose_text[
            service_node.start_mark.index:service_node.end_mark.index
        ]
        for key in (
            "role",
            "type",
            "skyseed.satellite_id",
            "skyseed.shell_id",
            "skyseed.orbit_id",
        ):
            assert service_text.count(f"{METADATA_PREFIX}{key}:") == 1
        assert 'meta.role: "Host"' not in service_text
        assert 'meta.type: "hnode"' not in service_text
        assert f"{METADATA_PREFIX}net." not in service_text
        assert "skyseed.runtime_node_ref" not in service_text


def test_user_registry_and_compiler_share_same_satellite_instances(tmp_path) -> None:
    emulator, _, _, _, satellites = compile_two_satellites(tmp_path / "output")

    registered = emulator.getRegistry().getByType(
        "65000", SATELLITE_REGISTRY_TYPE
    )
    assert all(
        created is compiled
        for created, compiled in zip(satellites, registered, strict=True)
    )
    for satellite in satellites:
        assert satellite.hasAttribute("__soft_install_tiers")
        assert satellite.getAttribute("__soft_install_tiers")


def test_runtime_map_uses_auditable_no_network_adapter(tmp_path) -> None:
    output = tmp_path / "output"
    emulator = Emulator()
    base = SatelliteBase()
    satellite_as = SkyseedAutonomousSystem(65000)
    base.setAutonomousSystem(satellite_as)
    orbit = satellite_as.create_shell("shell-1").create_orbit(
        "orbit-1", parameters=OrbitParameters(550.0, 53.0, 15.0)
    )
    orbit.create_satellites(2, satellite_id_prefix="satellite")
    emulator.addLayer(base)
    emulator.render()
    emulator.compile(SkyseedDockerCompiler(internet_map_enabled=True), str(output))

    compose = safe_load((output / "docker-compose.yml").read_text())
    map_service = compose["services"]["seedemu_internet_map"]
    assert map_service["build"] == "./skyseed-internet-map"
    assert map_service["image"] == "skyseed/seedemu-internetmap:2.0-no-network"
    patch = output / "skyseed-internet-map" / "internet_map_none_network_patch.sh"
    assert patch.is_file()
    patch_source = patch.read_text()
    assert "HostConfig.NetworkMode" in patch_source
    assert "NetworkSettings.Networks = {}" in patch_source


def test_private_upstream_service_accumulator_fails_closed_on_drift() -> None:
    compiler = SkyseedDockerCompiler(internet_map_enabled=False)
    compiler.__dict__.pop("_Docker__services")

    with pytest.raises(RuntimeError, match="private service accumulator"):
        compiler._append_upstream_service("service")

    compiler.__dict__["_Docker__services"] = []
    with pytest.raises(TypeError, match="must be a string"):
        compiler._append_upstream_service("service")
