"""Integration contracts for the static GroundStation slice."""

from datetime import datetime, timezone
from pathlib import Path

from seedemu.compiler import Docker
from seedemu.core import Emulator, Router
from seedemu.layers import Base, Ebgp, Ibgp, Ospf, PeerRelationship, Routing
from yaml import compose as compose_yaml
from yaml import safe_load
from yaml.nodes import MappingNode, Node, SequenceNode

from skyseed_v2.emulator_to_simulator import create_ground_station_simulation_input
from skyseed_v2.emulator.entities import GroundStation, GroundStationLocation, OrbitParameters
from skyseed_v2.shared import NodeId
from skyseed_v2.emulator.seedemu import SkyseedAutonomousSystem
from skyseed_v2.simulator.input import (
    GroundStationSimulationInput,
)


def build_rendered_ground_scenario(*, operator: str | None = "Skyseed"):
    emulator = Emulator()
    base = Base()
    base.createInternetExchange(100, create_rs=False)

    skyseed_as = SkyseedAutonomousSystem(65001)
    skyseed_as.setIbgpMode("disabled")
    base.setAutonomousSystem(skyseed_as)
    orbit = skyseed_as.create_shell("shell-1").create_orbit(
        "orbit-1",
        parameters=OrbitParameters(
            550.0, 53.0, 15.0, datetime(2026, 1, 1, tzinfo=timezone.utc)
        ),
    )
    satellites = orbit.create_satellites(2, satellite_id_prefix="satellite")

    station = skyseed_as.create_ground_station(
        "gs-1",
        location=GroundStationLocation(40.0, -74.0, 12.5),
        station_type="gateway",
        operator=operator,
        service_config={"endpoint": "uplink-a"},
    )
    input_before_ix = create_ground_station_simulation_input(station)
    station.joinNetwork("ix100", "10.100.0.101")

    ground_as = base.createAutonomousSystem(65002)
    ground_as.createNetwork("ground-net", "10.202.0.0/24")
    ground_router = ground_as.createRouter("ground-router")
    ground_router.joinNetwork("ground-net", "10.202.0.254")
    ground_router.joinNetwork("ix100", "10.100.0.102")

    ospf = Ospf().maskAsn(65001)
    ibgp = Ibgp().maskAsn(65001)
    ebgp = Ebgp().addPrivatePeering(
        100, 65001, 65002, PeerRelationship.Unfiltered
    )
    for layer in (base, Routing(), ospf, ibgp, ebgp):
        emulator.addLayer(layer)
    emulator.render()
    return (
        emulator,
        skyseed_as,
        station,
        ground_router,
        satellites,
        input_before_ix,
    )


def node_files(node) -> dict[str, str]:
    return {file.get()[0]: file.get()[1] for file in node.getFiles()}


def ground_station_service(compose: dict) -> tuple[str, dict]:
    for name, service in compose["services"].items():
        labels = service.get("labels", {})
        if labels.get(
            "org.seedsecuritylabs.seedemu.meta.skyseed.entity_type"
        ) == "GroundStation":
            return name, service
    raise AssertionError("GroundStation service is missing")


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


def raw_service_text(compose_text: str, service_name: str) -> str:
    lines = compose_text.splitlines(keepends=True)
    marker = f"    {service_name}:\n"
    start = lines.index(marker)
    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if lines[index].startswith("    ")
            and not lines[index].startswith("        ")
            and lines[index].rstrip().endswith(":")
        ),
        len(lines),
    )
    return "".join(lines[start:end])


def test_ground_station_uses_one_standard_rnode_and_border_router_instance() -> None:
    emulator, station_as, station, _, _, input_before_ix = (
        build_rendered_ground_scenario()
    )
    registry = emulator.getRegistry()

    assert isinstance(station, GroundStation)
    assert isinstance(station, Router)
    assert station_as.get_ground_station("gs-1") is station
    assert station_as.getRouter("gs-1") is station
    assert station_as.get_satellite("satellite-0") is not station
    assert registry.get("65001", "rnode", "gs-1") is station
    assert registry.get("65001", "brdnode", "gs-1") is station
    input_after_brd_registration = (
        create_ground_station_simulation_input(station)
    )
    assert input_after_brd_registration == input_before_ix
    assert input_after_brd_registration.node_id == NodeId(
        65001, "ground-station", "gs-1"
    )
    assert "rnode" not in input_after_brd_registration.node_id.value
    assert "brdnode" not in input_after_brd_registration.node_id.value
    assert station.isBorderRouter()
    assert station.getGeo() == (40.0, -74.0, "")
    assert {
        key: station.getLabel()[key]
        for key in (
            "skyseed.entity_type",
            "skyseed.ground_station_id",
            "skyseed.station_type",
            "skyseed.operator",
            "skyseed.latitude_deg",
            "skyseed.longitude_deg",
            "skyseed.elevation_m",
        )
    } == {
        "skyseed.entity_type": "GroundStation",
        "skyseed.ground_station_id": "gs-1",
        "skyseed.station_type": "gateway",
        "skyseed.operator": "Skyseed",
        "skyseed.latitude_deg": "40.0",
        "skyseed.longitude_deg": "-74.0",
        "skyseed.elevation_m": "12.5",
    }
    assert dict(station.service_config) == {"endpoint": "uplink-a"}
    assert len(station.getInterfaces()) == 1
    assert station.getInterfaces()[0].getNet().getName() == "ix100"
    assert registry.getByType("65001", "hnode") == []
    assert len(registry.getByType("65001", "extnode")) == 2


def test_ground_station_has_only_explicit_ebgp_and_no_ospf_or_ibgp() -> None:
    emulator, station_as, station, ground_router, _, _ = (
        build_rendered_ground_scenario()
    )
    bird = node_files(station)["/etc/bird/bird.conf"]
    ground_bird = node_files(ground_router)["/etc/bird/bird.conf"]

    assert station_as.getIbgpMode() == "disabled"
    assert bird.count("protocol bgp ") == 1
    assert "protocol bgp x_as65002" in bird
    assert "local 10.100.0.101 as 65001" in bird
    assert "neighbor 10.100.0.102 as 65002" in bird
    assert "protocol ospf" not in bird
    assert emulator.getRegistry().get(
        "seedemu", "layer", "Ospf"
    ).getMaskedAsns() == {65001}
    assert emulator.getRegistry().get(
        "seedemu", "layer", "Ibgp"
    ).getMaskedAsns() == {65001}
    assert "protocol ospf" in ground_bird


def test_ground_station_compiles_through_standard_router_path_with_metadata(
    tmp_path: Path,
) -> None:
    emulator, _, station, _, _, _ = build_rendered_ground_scenario()
    output = tmp_path / "output"

    class RecordingCompiler(Docker):
        def __init__(self) -> None:
            super().__init__(
                namingScheme="as{asn}{role}-{displayName}",
                internetMapEnabled=False,
            )
            self.standard_nodes = []

        def _compileNode(self, node):
            self.standard_nodes.append(node)
            return super()._compileNode(node)

    compiler = RecordingCompiler()
    emulator.compile(compiler, str(output))
    compose_text = (output / "docker-compose.yml").read_text()
    compose = safe_load(compose_text)
    service_name, service = ground_station_service(compose)
    service_text = raw_service_text(compose_text, service_name)
    labels = service["labels"]

    document = compose_yaml(compose_text)
    assert document is not None
    assert_no_duplicate_yaml_keys(document)

    assert service_name == "brdnode_65001_gs-1"
    assert service.get("network_mode") != "none"
    assert list(service["networks"]) == ["net_ix_ix100"]
    assert labels["org.seedsecuritylabs.seedemu.meta.role"] == "BorderRouter"
    assert labels[
        "org.seedsecuritylabs.seedemu.meta.skyseed.entity_type"
    ] == "GroundStation"
    assert labels[
        "org.seedsecuritylabs.seedemu.meta.skyseed.ground_station_id"
    ] == "gs-1"
    assert labels[
        "org.seedsecuritylabs.seedemu.meta.skyseed.station_type"
    ] == "gateway"
    assert labels[
        "org.seedsecuritylabs.seedemu.meta.skyseed.operator"
    ] == "Skyseed"
    assert labels[
        "org.seedsecuritylabs.seedemu.meta.skyseed.latitude_deg"
    ] == "40.0"
    assert labels[
        "org.seedsecuritylabs.seedemu.meta.skyseed.longitude_deg"
    ] == "-74.0"
    assert labels[
        "org.seedsecuritylabs.seedemu.meta.skyseed.elevation_m"
    ] == "12.5"
    assert not any("runtime_node" in key for key in labels)
    assert not any("service_config" in key for key in labels)
    assert "uplink-a" not in labels.values()
    assert "Host" not in labels.values()
    assert "Satellite" not in labels.values()
    assert station.hasAttribute("__soft_install_tiers")
    assert station in compiler.standard_nodes
    assert all(
        not isinstance(node, GroundStation)
        or node is station
        for node in compiler.standard_nodes
    )
    for key in (
        "role",
        "skyseed.entity_type",
        "skyseed.ground_station_id",
    ):
        assert service_text.count(
            f"org.seedsecuritylabs.seedemu.meta.{key}:"
        ) == 1
    assert "skyseed.runtime_node_ref" not in service_text


def test_unknown_ground_station_operator_creates_no_seed_or_compose_label(
    tmp_path: Path,
) -> None:
    emulator, _, station, _, _, simulation_input = (
        build_rendered_ground_scenario(operator=None)
    )
    assert station.operator is None
    assert simulation_input.operator is None
    assert "skyseed.operator" not in station.getLabel()

    output = tmp_path / "output"
    emulator.compile(
        Docker(
            namingScheme="as{asn}{role}-{displayName}",
            internetMapEnabled=False,
        ),
        str(output),
    )
    _, service = ground_station_service(
        safe_load((output / "docker-compose.yml").read_text())
    )
    assert (
        "org.seedsecuritylabs.seedemu.meta.skyseed.operator"
        not in service["labels"]
    )


def test_satellites_remain_isolated_in_ground_station_scenario() -> None:
    emulator, _, station, _, satellites, _ = build_rendered_ground_scenario()

    assert emulator.getRegistry().getByType(
        "65001", "extnode"
    ) == list(satellites)
    assert all(satellite.getInterfaces() == [] for satellite in satellites)
    assert all(satellite is not station for satellite in satellites)
