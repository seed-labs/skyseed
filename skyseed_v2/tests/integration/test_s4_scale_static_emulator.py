from json import loads
from pathlib import Path

from yaml import compose as compose_yaml
from yaml import safe_load
from yaml.nodes import MappingNode, Node, SequenceNode

from examples.s4_scale_static_emulator import build_scenario, compile_scenario


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


def test_s4_scale_domain_and_catalog_are_deterministic() -> None:
    first = build_scenario()
    second = build_scenario()

    assert first.summary.shell_count == 1
    assert first.summary.orbit_count == 6
    assert first.summary.satellite_count == 348
    assert first.summary.ground_station_count == 12
    assert first.summary.ordinary_as_count == 2
    assert first.summary.ix_count == 2
    assert first.summary.ebgp_peering_count == 2
    assert first.summary.catalog_node_count == 360
    assert len({node.node_id for node in first.catalog.list_nodes()}) == 360
    assert tuple(node.node_id for node in first.catalog.list_nodes()) == tuple(
        node.node_id for node in second.catalog.list_nodes()
    )
    assert tuple(
        satellite.satellite_id for satellite in first.constellation.satellites
    ) == tuple(
        satellite.satellite_id for satellite in second.constellation.satellites
    )
    assert all(satellite.getInterfaces() == [] for satellite in first.constellation.satellites)


def test_s4_scale_compose_inventory_and_static_routing(tmp_path: Path) -> None:
    scenario = build_scenario()
    output = tmp_path / "output"
    summary, _ = compile_scenario(
        scenario,
        output,
        override=False,
        internet_map_enabled=True,
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
        elif labels.get(f"{METADATA_PREFIX}skyseed.entity_type") == "GroundStation":
            ground_station_services.append(service)
        elif labels.get(f"{METADATA_PREFIX}nodename") in {
            "ground-a-router",
            "ground-b-router",
        }:
            ordinary_router_services.append(service)

    assert summary.compose_service_count == 365
    assert summary.compose_file_size_bytes == len(compose_text.encode())
    assert len(satellite_services) == 348
    assert len(ground_station_services) == 12
    assert len(ordinary_router_services) == 2
    assert all(
        service.get("network_mode") == "none" and "networks" not in service
        for service in satellite_services
    )
    assert all(len(service.get("networks", {})) == 1 for service in ground_station_services)
    station_networks = [
        next(iter(service["networks"])) for service in ground_station_services
    ]
    assert station_networks.count("net_ix_ix100") == 6
    assert station_networks.count("net_ix_ix101") == 6
    assert all(len(service.get("networks", {})) == 2 for service in ordinary_router_services)
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
    ):
        assert forbidden not in compose_text

    selected_ids = {
        station.ground_station_id for station in scenario.peering_ground_stations
    }
    for station in scenario.ground_stations.ground_stations:
        bird = node_files(station)["/etc/bird/bird.conf"]
        assert "protocol ospf" not in bird
        assert bird.count("protocol bgp ") == (
            1 if station.ground_station_id in selected_ids else 0
        )
        assert len(station.getInterfaces()) == 1
    for router in scenario.ground_routers:
        bird = node_files(router)["/etc/bird/bird.conf"]
        assert "protocol ospf" in bird
        assert bird.count("protocol bgp ") == 1

    report = loads((output / "scenario-summary.json").read_text())
    assert report["summary"]["catalog_node_count"] == 360
    assert report["summary"]["compose_service_count"] == 365
    assert report["semantics"] == {
        "dynamic_satellite_network": False,
        "historical_service_prefixes_imported": False,
        "reference_epoch_in_emulator": False,
        "s4_is_scale_reference_only": True,
    }
