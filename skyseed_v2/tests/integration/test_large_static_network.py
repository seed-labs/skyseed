from pathlib import Path

from yaml import compose as compose_yaml
from yaml import safe_load
from yaml.nodes import MappingNode, Node, SequenceNode

from examples.large_static_network._compose_support import (
    materialization_expectations,
)
from examples.large_static_network.main import (
    DEFAULT_SCENARIO,
    apply_scenario_file,
)
from skyseed_v2.serialization import StaticEmulatorScenarioLoader
from skyseed_v2.seedemu_integration import SkyseedDockerCompiler


METADATA_PREFIX = "org.seedsecuritylabs.seedemu.meta."


def node_files(node) -> dict[str, str]:
    return {file.get()[0]: file.get()[1] for file in node.getFiles()}


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


def test_large_static_entities_and_simulator_input_are_deterministic() -> None:
    specification = StaticEmulatorScenarioLoader().load(DEFAULT_SCENARIO)
    first = apply_scenario_file()
    second = apply_scenario_file()
    first_as = first.skyseed_autonomous_systems[0]
    second_as = second.skyseed_autonomous_systems[0]

    assert len(first_as.shells) == 1
    assert sum(len(shell.orbits) for shell in first_as.shells) == 6
    assert len(first_as.satellites()) == 348
    assert len(first_as.ground_stations()) == 12
    assert len(first.ordinary_autonomous_systems) == 2
    assert len(specification.internet_exchanges) == 2
    assert len(specification.ebgp_peerings) == 2
    assert len(first.simulator_input.list_nodes()) == 360
    first_input = first.simulator_input
    second_input = second.simulator_input
    assert len({node.node_id for node in first_input.list_nodes()}) == 360
    assert tuple(node.node_id for node in first_input.list_nodes()) == tuple(
        node.node_id for node in second_input.list_nodes()
    )
    first_satellites = first_as.satellites()
    second_satellites = second_as.satellites()
    assert tuple(satellite.satellite_id for satellite in first_satellites) == tuple(
        satellite.satellite_id for satellite in second_satellites
    )
    assert all(satellite.getInterfaces() == [] for satellite in first_satellites)


def test_large_static_compose_inventory_and_static_routing(
    tmp_path: Path,
) -> None:
    result = apply_scenario_file()
    output = tmp_path / "output"
    result.emulator.render()
    result.emulator.compile(
        SkyseedDockerCompiler(internet_map_enabled=True),
        str(output),
    )
    compose_text = (output / "docker-compose.yml").read_text()
    compose = safe_load(compose_text)
    document = compose_yaml(compose_text)
    assert document is not None
    assert_no_duplicate_yaml_keys(document)

    satellite_services = []
    ground_station_services = []
    ordinary_router_services = []
    for service in compose["services"].values():
        labels = service.get("labels", {})
        if labels.get(f"{METADATA_PREFIX}type") == "satnode":
            satellite_services.append(service)
        elif (
            labels.get(f"{METADATA_PREFIX}skyseed.entity_type")
            == "GroundStation"
        ):
            ground_station_services.append(service)
        elif labels.get(f"{METADATA_PREFIX}nodename") in {
            "ground-a-router",
            "ground-b-router",
        }:
            ordinary_router_services.append(service)

    assert len(compose["services"]) == 365
    assert len(satellite_services) == 348
    assert len(ground_station_services) == 12
    assert len(ordinary_router_services) == 2
    assert all(
        service.get("network_mode") == "none" and "networks" not in service
        for service in satellite_services
    )
    assert all(
        len(service.get("networks", {})) == 1
        for service in ground_station_services
    )
    station_networks = [
        next(iter(service["networks"])) for service in ground_station_services
    ]
    assert station_networks.count("net_ix_ix100") == 6
    assert station_networks.count("net_ix_ix101") == 6
    assert all(
        len(service.get("networks", {})) == 2
        for service in ordinary_router_services
    )
    assert "seedemu_internet_map" in compose["services"]
    assert "198.18." not in compose_text
    for forbidden in (
        "RuntimeNodeReference",
        "runtime_node",
        "resource_locator",
        "epoch_utc",
        "current_position",
        "visibility",
        "gs_satellite_link",
        "skyseed_v1",
        "reference_shell_label",
    ):
        assert forbidden not in compose_text

    stations = tuple(
        station
        for autonomous_system in result.skyseed_autonomous_systems
        for station in autonomous_system.ground_stations()
    )
    bgp_station_count = 0
    for station in stations:
        bird = node_files(station)["/etc/bird/bird.conf"]
        assert "protocol ospf" not in bird
        bgp_station_count += bird.count("protocol bgp ")
        assert len(station.getInterfaces()) == 1
    assert bgp_station_count == 2
    for autonomous_system in result.ordinary_autonomous_systems:
        for router_name in autonomous_system.getRouters():
            bird = node_files(autonomous_system.getRouter(router_name))[
                "/etc/bird/bird.conf"
            ]
            assert "protocol ospf" in bird
            assert bird.count("protocol bgp ") == 1
    expected_services = materialization_expectations(
        output / "docker-compose.yml"
    )
    assert len(expected_services.long_running) == 363
    assert len(expected_services.successful_exit_helpers) == 2
    assert set(expected_services.all_services) == set(compose["services"])
