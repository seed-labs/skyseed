from __future__ import annotations

from collections import Counter, defaultdict
import json
from inspect import Parameter, signature
from pathlib import Path
from subprocess import run
import sys

import pytest
from seedemu.core import Emulator
from yaml import safe_load

from examples.dynamic_slice_network import main as reference_main
from examples.dynamic_slice_network import simu
from examples.dynamic_slice_network.emu import (
    build_dynamic_reference_emulator,
)
from skyseed_v2.emulator.seedemu import SatelliteBase
from skyseed_v2.simulator import (
    ConnectionKind,
    GeometricConnectionModel,
    Slicer,
)
from skyseed_v2.simulator.geometry import (
    elevation_deg,
    geodetic_to_ecef,
    has_earth_line_of_sight,
)
from tools.dynamic_slice import collect_dynamic_slice_data
from tools.zone import collect_zone_data


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_ROOT = PROJECT_ROOT / "examples/dynamic_slice_network"


@pytest.fixture(scope="module")
def reference_emulator():
    return build_dynamic_reference_emulator()


@pytest.fixture(scope="module")
def reference_slicer(reference_emulator) -> Slicer:
    return simu.create_dynamic_reference_slicer(reference_emulator)


@pytest.fixture(scope="module")
def reference_view_data(reference_slicer: Slicer) -> dict[str, object]:
    return collect_dynamic_slice_data(reference_slicer)


def test_richer_reference_uses_the_formal_bounded_a_f_chain(
    reference_slicer: Slicer,
) -> None:
    topology = reference_slicer.get_topology()
    retained_ids = reference_slicer.get_retained_slice_ids()

    assert retained_ids == tuple(range(simu.REAL_SLICE_COUNT + 1))
    assert reference_slicer.is_exhausted()
    assert reference_slicer.get_slice(0).timestamp is None
    assert not reference_slicer.get_slice(0).positions
    assert sum(
        len(orbit.satellites)
        for autonomous_system in topology.autonomous_systems
        for shell in autonomous_system.shells
        for orbit in shell.orbits
    ) == 32
    assert sum(
        len(autonomous_system.ground_stations)
        for autonomous_system in topology.autonomous_systems
    ) == 3
    assert len(topology.get_zone_layout().zones) == 5

    differences = tuple(
        reference_slicer.create_difference(slice_id - 1, slice_id)
        for slice_id in retained_ids[1:]
    )
    assert differences[0].previous_slice_id == 0
    assert differences[0].previous_timestamp is None
    assert all(
        difference.current_slice_id == difference.previous_slice_id + 1
        for difference in differences
    )
    assert all(not difference.is_empty for difference in differences[1:])


def test_cross_plane_pairing_is_one_predecessor_and_one_successor_candidate(
    reference_slicer: Slicer,
) -> None:
    topology = reference_slicer.get_topology()
    model = reference_slicer.get_connection_model()
    assert isinstance(model, GeometricConnectionModel)
    prepared = model._prepared_isl_pairs
    assert prepared is not None
    assert model._prepared_reference_timestamp == (
        reference_slicer.get_timeline().start_time
    )

    parent_by_node = {}
    orbit_ring_by_shell = {}
    for autonomous_system in topology.autonomous_systems:
        for shell in autonomous_system.shells:
            ring = tuple(
                sorted(
                    shell.orbits,
                    key=lambda orbit: orbit.right_ascension_deg,
                )
            )
            orbit_ring_by_shell[(autonomous_system.asn, shell.shell_id)] = (
                tuple(orbit.orbit_id for orbit in ring)
            )
            for orbit in ring:
                for satellite in orbit.satellites:
                    parent_by_node[satellite.node_id] = (
                        autonomous_system.asn,
                        shell.shell_id,
                        orbit.orbit_id,
                    )

    def side(source, neighbor) -> str:
        source_parent = parent_by_node[source]
        neighbor_parent = parent_by_node[neighbor]
        ring = orbit_ring_by_shell[source_parent[:2]]
        index = ring.index(source_parent[2])
        if neighbor_parent[2] == ring[(index - 1) % len(ring)]:
            return "predecessor"
        if neighbor_parent[2] == ring[(index + 1) % len(ring)]:
            return "successor"
        return "nonadjacent"

    candidate_sides = defaultdict(Counter)
    prepared_cross_pairs = set()
    for first, second in prepared:
        if parent_by_node[first] == parent_by_node[second]:
            continue
        prepared_cross_pairs.add(frozenset((first, second)))
        candidate_sides[first][side(first, second)] += 1
        candidate_sides[second][side(second, first)] += 1

    assert set(candidate_sides) == set(parent_by_node)
    assert all(
        counts == Counter({"predecessor": 1, "successor": 1})
        for counts in candidate_sides.values()
    )

    expected_degree_distribution = Counter({1: 24, 0: 4, 2: 4})
    for slice_id in reference_slicer.get_retained_slice_ids()[1:]:
        active_cross_pairs = set()
        active_sides = defaultdict(Counter)
        active_degree = Counter()
        for connection in (
            reference_slicer.get_slice(slice_id).connection_slice.connections
        ):
            identity = connection.connection_id
            if (
                identity.kind is not ConnectionKind.INTER_SATELLITE
                or parent_by_node[identity.endpoint_a]
                == parent_by_node[identity.endpoint_b]
            ):
                continue
            active_cross_pairs.add(
                frozenset((identity.endpoint_a, identity.endpoint_b))
            )
            for source, neighbor in (
                (identity.endpoint_a, identity.endpoint_b),
                (identity.endpoint_b, identity.endpoint_a),
            ):
                active_degree[source] += 1
                active_sides[source][side(source, neighbor)] += 1
        assert active_cross_pairs <= prepared_cross_pairs
        assert all(
            set(counts) <= {"predecessor", "successor"}
            and max(counts.values(), default=0) <= 1
            for counts in active_sides.values()
        )
        assert Counter(
            active_degree.get(node_id, 0) for node_id in parent_by_node
        ) == expected_degree_distribution

    assert model._prepared_isl_pairs is prepared


def test_richer_emulator_contains_static_network_and_routing_layers() -> None:
    emulator = build_dynamic_reference_emulator()
    base = emulator.getLayer("Base")

    assert isinstance(base, SatelliteBase)
    assert str(base.getInternetExchange(110).getPeeringLan().getPrefix()) == (
        "10.110.0.0/24"
    )
    assert base.getAutonomousSystem(65110).getAsn() == 65110
    assert set(layer.getName() for layer in emulator.getLayers()) == {
        "Base",
        "Routing",
        "Ospf",
        "Ibgp",
        "Ebgp",
    }


def test_richer_example_has_one_emulator_builder_and_fail_closed_projection() -> None:
    emu_source = (EXAMPLE_ROOT / "emu.py").read_text(encoding="utf-8")
    simu_source = (EXAMPLE_ROOT / "simu.py").read_text(encoding="utf-8")
    main_source = (EXAMPLE_ROOT / "main.py").read_text(encoding="utf-8")

    assert emu_source.count("def build_dynamic_reference_emulator(") == 1
    assert "load_static_emulator_config(" in emu_source
    assert "create_static_emulator(" in emu_source
    for forbidden in (
        "createAutonomousSystem(",
        "createInternetExchange(",
        "createRouter(",
        "createNetwork(",
        "Simulator(",
    ):
        assert forbidden not in emu_source
    assert "dynamic_slice_network.emu" not in simu_source
    assert "def build_dynamic_reference_emulator(" not in main_source

    with pytest.raises(TypeError):
        simu.create_dynamic_reference_slicer()
    with pytest.raises(TypeError, match="upstream Emulator"):
        simu.create_dynamic_reference_slicer(object())


def test_richer_main_builds_once_and_emits_static_and_dynamic_outputs(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    output = tmp_path / "output"
    built = []
    compiled = []
    projected = []
    projection_inputs = []
    created_slicers = []
    topology_tool_inputs = []
    zone_tool_inputs = []
    dynamic_tool_inputs = []
    real_build = reference_main.build_dynamic_reference_emulator
    real_compile = Emulator.compile
    real_project = reference_main.create_dynamic_reference_slicer
    real_input_projection = simu.create_simulator_static_input
    real_topology_tool = reference_main.write_topology_tool
    real_zone_tool = reference_main.write_zone_tool
    real_dynamic_tool = reference_main.write_dynamic_slice_tool

    def build_spy(path):
        emulator = real_build(path)
        built.append(emulator)
        return emulator

    def compile_spy(self, compiler, directory, override=False):
        compiled.append(self)
        return real_compile(self, compiler, directory, override)

    def input_projection_spy(autonomous_systems):
        exact_owners = tuple(autonomous_systems)
        projection_inputs.append(exact_owners)
        return real_input_projection(exact_owners)

    def projection_spy(emulator, **kwargs):
        projected.append(emulator)
        slicer = real_project(emulator, **kwargs)
        created_slicers.append(slicer)
        return slicer

    def topology_tool_spy(topology, directory):
        topology_tool_inputs.append(topology)
        return real_topology_tool(topology, directory)

    def zone_tool_spy(topology, directory):
        zone_tool_inputs.append(topology)
        return real_zone_tool(topology, directory)

    def dynamic_tool_spy(slicer, directory):
        dynamic_tool_inputs.append(slicer)
        return real_dynamic_tool(slicer, directory)

    monkeypatch.setattr(
        reference_main,
        "build_dynamic_reference_emulator",
        build_spy,
    )
    monkeypatch.setattr(Emulator, "compile", compile_spy)
    monkeypatch.setattr(
        simu,
        "create_simulator_static_input",
        input_projection_spy,
    )
    monkeypatch.setattr(
        reference_main,
        "create_dynamic_reference_slicer",
        projection_spy,
    )
    monkeypatch.setattr(
        reference_main,
        "write_topology_tool",
        topology_tool_spy,
    )
    monkeypatch.setattr(reference_main, "write_zone_tool", zone_tool_spy)
    monkeypatch.setattr(
        reference_main,
        "write_dynamic_slice_tool",
        dynamic_tool_spy,
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "main.py",
            "--output",
            str(output),
            "--no-internet-map",
        ],
    )

    assert reference_main.main() == 0
    assert len(built) == 1
    assert len(projected) == 1
    assert projected[0] is built[0]
    assert len(compiled) == 1
    assert compiled[0] is built[0]
    assert len(projection_inputs) == 1
    base = built[0].getLayer("Base")
    assert isinstance(base, SatelliteBase)
    expected_as_owners = base.get_skyseed_autonomous_systems()
    assert len(projection_inputs[0]) == len(expected_as_owners)
    assert all(
        actual is expected
        for actual, expected in zip(
            projection_inputs[0],
            expected_as_owners,
            strict=True,
        )
    )
    assert len(created_slicers) == 1
    exact_slicer = created_slicers[0]
    exact_topology = exact_slicer.get_topology()
    assert len(topology_tool_inputs) == 1
    assert topology_tool_inputs[0] is exact_topology
    assert len(zone_tool_inputs) == 1
    assert zone_tool_inputs[0] is exact_topology
    assert len(dynamic_tool_inputs) == 1
    assert dynamic_tool_inputs[0] is exact_slicer

    runtime_output = output / "runtime"
    topology_output = output / "inspection/topology"
    zone_output = output / "inspection/zone"
    dynamic_output = output / "inspection/dynamic-slice"
    compose_file = runtime_output / "docker-compose.yml"
    compose = safe_load(compose_file.read_text(encoding="utf-8"))
    assert isinstance(compose["services"], dict)
    assert "net_ix_ix110" in compose["networks"]
    assert "net_65110_service-net" in compose["networks"]
    labels = [
        service.get("labels", {})
        for service in compose["services"].values()
    ]
    assert sum(
        "org.seedsecuritylabs.seedemu.meta.skyseed.satellite_id" in value
        for value in labels
    ) == 32
    assert sum(
        "org.seedsecuritylabs.seedemu.meta.skyseed.ground_station_id" in value
        for value in labels
    ) == 3

    start = (output / "start.sh").read_text(encoding="utf-8")
    cleanup = (output / "cleanup.sh").read_text(encoding="utf-8")
    assert "skyseed_v2_dynamic_slice_network" in start
    assert "config --quiet" in start
    assert "up -d --build" in start
    assert "down --remove-orphans" in cleanup
    assert 'COMPOSE_FILE="$SCRIPT_DIR/runtime/docker-compose.yml"' in start
    assert 'COMPOSE_FILE="$SCRIPT_DIR/runtime/docker-compose.yml"' in cleanup
    assert (output / "start.sh").stat().st_mode & 0o111
    assert (output / "cleanup.sh").stat().st_mode & 0o111
    assert {path.name for path in output.iterdir()} == {
        "start.sh",
        "cleanup.sh",
        "runtime",
        "inspection",
    }
    assert {path.name for path in (output / "inspection").iterdir()} == {
        "topology",
        "zone",
        "dynamic-slice",
    }
    for forbidden in ("emulator", "simulator", "dynamic-slice", "tools"):
        assert not (output / forbidden).exists()

    for directory, names in (
        (topology_output, ("index.html", "topology.json", "summary.txt")),
        (zone_output, ("index.html", "probes.json", "summary.txt")),
    ):
        for name in names:
            assert (directory / name).is_file()
    for name in (
        "index.html",
        "dynamic-slice.json",
        "summary.txt",
        "inventory.json",
        "checksums.sha256",
    ):
        assert (dynamic_output / name).is_file()

    topology_payload = json.loads(
        (topology_output / "topology.json").read_text(encoding="utf-8")
    )
    zone_payload = json.loads(
        (zone_output / "probes.json").read_text(encoding="utf-8")
    )
    dynamic_payload = json.loads(
        (dynamic_output / "dynamic-slice.json").read_text(encoding="utf-8")
    )
    expected_asns = {
        autonomous_system.asn
        for autonomous_system in exact_topology.autonomous_systems
    }
    expected_orbits = {
        (orbit.asn, orbit.shell_id, orbit.orbit_id)
        for autonomous_system in exact_topology.autonomous_systems
        for shell in autonomous_system.shells
        for orbit in shell.orbits
    }
    expected_satellites = {
        satellite.node_id.value
        for autonomous_system in exact_topology.autonomous_systems
        for shell in autonomous_system.shells
        for orbit in shell.orbits
        for satellite in orbit.satellites
    }
    expected_ground_stations = {
        ground_station.node_id.value
        for autonomous_system in exact_topology.autonomous_systems
        for ground_station in autonomous_system.ground_stations
    }
    expected_zones = {
        zone.zone_id for zone in exact_topology.get_zone_layout().zones
    }
    assert {
        item["asn"] for item in topology_payload["hierarchy"]
    } == expected_asns
    assert {
        (item["asn"], item["shell_id"], item["orbit_id"])
        for item in topology_payload["orbit_rings"]
    } == expected_orbits
    assert {
        satellite["node_id"]
        for autonomous_system in topology_payload["hierarchy"]
        for shell in autonomous_system["shells"]
        for orbit in shell["orbits"]
        for satellite in orbit["satellites"]
    } == expected_satellites
    assert {
        item["node_id"] for item in topology_payload["ground_stations"]
    } == expected_ground_stations
    assert {
        item["zone_id"] for item in zone_payload["zones"]
    } == expected_zones
    assert {
        item["node_id"] for item in zone_payload["ground_stations"]
    } == expected_ground_stations
    assert {
        item["node_id"]
        for item in dynamic_payload["topology"]["nodes"]
        if item["kind"] == "satellite"
    } == expected_satellites
    assert {
        item["node_id"]
        for item in dynamic_payload["topology"]["nodes"]
        if item["kind"] == "ground-station"
    } == expected_ground_stations
    assert {
        (item["asn"], item["shell_id"], item["orbit_id"])
        for item in dynamic_payload["topology"]["orbits"]
    } == expected_orbits
    assert {
        item["zone_id"] for item in dynamic_payload["topology"]["zones"]
    } == expected_zones

    output_text = capsys.readouterr().out
    assert f"Topology Tool entry: {topology_output / 'index.html'}" in output_text
    assert f"Zone Tool entry: {zone_output / 'index.html'}" in output_text
    assert (
        f"Dynamic Tool entry: {dynamic_output / 'index.html'}"
        in output_text
    )
    assert "static containers are not updated" in output_text
    main_source = (EXAMPLE_ROOT / "main.py").read_text(encoding="utf-8")
    for removed_regression_logic in (
        "_validate_compose_structure",
        "safe_load",
        "satellite_services",
        "ground_station_services",
        "_behavior_summary",
    ):
        assert removed_regression_logic not in main_source


def test_richer_reference_naturally_exercises_representative_behavior(
    reference_view_data: dict[str, object],
) -> None:
    assert reference_view_data["inventory"] == {
        "nodes": 35,
        "satellites": 32,
        "ground_stations": 3,
        "orbits": 4,
        "zones": 5,
        "slices": 21,
        "differences": 20,
        "paths": 3475,
    }
    assert reference_view_data["behavior"] == {
        "later_nonempty_differences": 19,
        "connection_additions": 42,
        "connection_removals": 42,
        "directional_parameter_patches": 690,
        "zone_assignment_additions": 12,
        "zone_assignment_changes": 4,
        "zone_assignment_removals": 12,
        "route_additions": 719,
        "route_changes": 305,
        "route_removals": 684,
        "maximum_path_hops": 7,
    }
    slices = reference_view_data["slices"]
    first_connections = slices[1]["connections"]
    assert sum(
        value["display_kind"] == "same-orbit-isl"
        for value in first_connections
    ) == 32
    assert sum(
        value["display_kind"] == "cross-orbit-isl"
        for value in first_connections
    ) == 16
    assert any(
        connection["connection_id"]["kind"] == "ground-station-access"
        for topology_slice in slices[1:]
        for connection in topology_slice["connections"]
    )
    assert all(
        topology_slice["zone_service_assignments"]
        for topology_slice in slices[1:]
    )
    later_differences = (
        topology_slice["difference_from_previous"]
        for topology_slice in slices[2:]
    )
    zone_operations = {
        patch["operation"]
        for difference in later_differences
        for patch in difference["zone_service_assignment_patches"]
    }
    later_differences = (
        topology_slice["difference_from_previous"]
        for topology_slice in slices[2:]
    )
    route_operations = {
        patch["operation"]
        for difference in later_differences
        for patch in difference["route_patches"]
    }
    assert zone_operations == {"added", "changed", "removed"}
    assert route_operations == {"added", "changed", "removed"}


def test_zone_service_uses_representative_point_not_subpoint_containment(
    reference_slicer: Slicer,
    reference_view_data: dict[str, object],
) -> None:
    topology = reference_slicer.get_topology()
    layout = topology.get_zone_layout()
    outside_assignments = [
        (record, assignment)
        for record in reference_view_data["slices"][1:]
        for assignment in record["zone_service_assignments"]
        if not assignment["display_explanation"]["subpoint_inside_zone"]
    ]
    assert outside_assignments
    record, assignment = outside_assignments[0]
    assert (
        assignment["display_explanation"][
            "representative_point_elevation_deg"
        ]
        >= simu.ZONE_COVERAGE_MINIMUM_ELEVATION_DEG
    )

    formal_slice = reference_slicer.get_slice(record["slice_id"])
    zone = layout.get_zone(assignment["zone_id"])
    service_satellite = formal_slice.connection_slice.get_service_satellite(
        zone.zone_id
    )
    representative_position = geodetic_to_ecef(
        zone.representative_point[0],
        zone.representative_point[1],
        0.0,
    )
    eligible = []
    for autonomous_system in topology.autonomous_systems:
        for shell in autonomous_system.shells:
            for orbit in shell.orbits:
                for satellite in orbit.satellites:
                    position = formal_slice.positions[satellite.node_id]
                    elevation = elevation_deg(
                        representative_position,
                        position,
                    )
                    if (
                        elevation
                        >= simu.ZONE_COVERAGE_MINIMUM_ELEVATION_DEG
                        and has_earth_line_of_sight(
                            representative_position,
                            position,
                        )
                    ):
                        eligible.append(
                            (
                                -elevation,
                                (
                                    satellite.node_id.asn,
                                    satellite.node_id.entity_kind,
                                    satellite.node_id.entity_id,
                                ),
                                satellite.node_id,
                            )
                        )
    assert eligible
    assert service_satellite == min(eligible)[2]


def test_reference_zone_overlap_preserves_all_memberships_and_priority_winner(
    reference_slicer: Slicer,
) -> None:
    topology = reference_slicer.get_topology()
    layout = topology.get_zone_layout()
    probe = (42.0, -74.0)
    assert layout.allow_overlap
    assert {
        zone.zone_id for zone in topology.find_zones(*probe)
    } == {"north-america-east", "northeast-priority"}
    assert topology.resolve_zone(*probe).zone_id == "northeast-priority"
    for zone_id in ("north-america-east", "northeast-priority"):
        zone = layout.get_zone(zone_id)
        assert zone.contains(*zone.representative_point)

    tool_data = collect_zone_data(topology)
    overlap_probes = [
        value
        for value in tool_data["probes"]
        if {
            match["zone_id"] for match in value["physical_matches"]
        }
        == {"north-america-east", "northeast-priority"}
    ]
    assert overlap_probes
    assert all(
        value["authoritative_result"]["zone_id"] == "northeast-priority"
        for value in overlap_probes
    )


def test_reference_ground_stations_copy_v1_reference_values_without_runtime_dependency(
    reference_slicer: Slicer,
    reference_view_data: dict[str, object],
) -> None:
    expected = {
        "gs-porto-alegre-reference": (-30.0346, -51.2177, 0.0),
        "gs-marsala-reference": (37.7986, 12.4357, 0.0),
        "gs-yamaguchi-reference": (34.1861, 131.4706, 0.0),
    }
    topology = reference_slicer.get_topology()
    ground_stations = {
        station.ground_station_id: (
            station.latitude_deg,
            station.longitude_deg,
            station.elevation_m,
        )
        for autonomous_system in topology.autonomous_systems
        for station in autonomous_system.ground_stations
    }
    assert ground_stations == expected
    representative_points = {
        zone.representative_point
        for zone in topology.get_zone_layout().zones
    }
    assert not representative_points.intersection(
        (latitude, longitude)
        for latitude, longitude, _ in ground_stations.values()
    )

    ground_station_ids = {
        node["node_id"]
        for node in reference_view_data["topology"]["nodes"]
        if node["kind"] == "ground-station"
    }
    access_sets = {node_id: [] for node_id in ground_station_ids}
    for record in reference_view_data["slices"][1:]:
        per_station = defaultdict(set)
        for connection in record["connections"]:
            identity = connection["connection_id"]
            if identity["kind"] != "ground-station-access":
                continue
            endpoints = (identity["endpoint_a"], identity["endpoint_b"])
            station_id = next(
                endpoint
                for endpoint in endpoints
                if endpoint in ground_station_ids
            )
            per_station[station_id].add(
                next(
                    endpoint
                    for endpoint in endpoints
                    if endpoint != station_id
                )
            )
        for station_id in ground_station_ids:
            access_sets[station_id].append(
                frozenset(per_station[station_id])
            )
    assert all(any(values) for values in access_sets.values())
    assert all(len(set(values)) > 1 for values in access_sets.values())

    later_differences = [
        record["difference_from_previous"]
        for record in reference_view_data["slices"][2:]
    ]
    assert any(
        connection["connection_id"]["kind"] == "ground-station-access"
        for difference in later_differences
        for connection in difference["added_connections"]
    )
    assert any(
        identity["connection_id"]["kind"] == "ground-station-access"
        for difference in later_differences
        for identity in difference["removed_connection_ids"]
    )
    assert any(
        patch["connection_id"]["kind"] == "ground-station-access"
        for difference in later_differences
        for patch in difference["connection_parameter_patches"]
    )

    scenario_source = (EXAMPLE_ROOT / "scenario.yaml").read_text(
        encoding="utf-8"
    )
    readme_source = (EXAMPLE_ROOT / "README.md").read_text(encoding="utf-8")
    assert "starlink_gateways.json" in scenario_source
    assert "s4_visualization_gateway_binding_12.json" in scenario_source
    assert "does not establish current operational status" in scenario_source
    assert "external source URL" in readme_source
    assert "currently\noperational sites" in readme_source
    for path in ("emu.py", "simu.py", "main.py"):
        assert "skyseed_v1" not in (
            EXAMPLE_ROOT / path
        ).read_text(encoding="utf-8")


def test_richer_reference_core_identities_and_counts_repeat_deterministically(
    reference_view_data: dict[str, object],
) -> None:
    repeated_emulator = build_dynamic_reference_emulator()
    repeated = simu.create_dynamic_reference_slicer(repeated_emulator)

    assert collect_dynamic_slice_data(repeated) == reference_view_data


def test_richer_reference_sources_do_not_construct_formal_dynamic_results() -> None:
    source = (EXAMPLE_ROOT / "simu.py").read_text(encoding="utf-8")
    for forbidden in (
        "argparse",
        "build_dynamic_reference_emulator",
        "examples.dynamic_slice_network.emu",
        "scenario_path",
        "write_topology_tool",
        "write_zone_tool",
        "write_dynamic_slice_tool",
        "_behavior_summary",
        "def main(",
        "__main__",
        "TopologySlice(",
        "TopologyDifference(",
        "Connection(",
        "ConnectionSlice(",
        "RouteSlice(",
        "._slices",
        "._compare_topology_slices",
        "compute_positions(",
        "compute_connection_slice(",
        "compute_route_slice(",
        ".resolve_path(",
        ".create_difference(",
    ):
        assert forbidden not in source
    for required in (
        "def create_dynamic_reference_slicer(",
        "create_simulator_static_input(",
        "Simulator(",
        ".create_topology(",
        ".create_slicer(",
        ".create_all_slices(",
    ):
        assert required in source
    main_source = (EXAMPLE_ROOT / "main.py").read_text(encoding="utf-8")
    assert main_source.count("write_topology_tool(") == 1
    assert main_source.count("write_zone_tool(") == 1
    assert main_source.count("write_dynamic_slice_tool(") == 1
    assert main_source.count(".get_topology()") == 1
    for forbidden in (
        "Simulator(",
        ".create_topology(",
        "load_zone_layout_definition(",
        "create_simulator_static_input(",
    ):
        assert forbidden not in main_source
    parameters = signature(simu.create_dynamic_reference_slicer).parameters
    assert tuple(parameters) == ("emulator", "zone_layout_path")
    assert parameters["emulator"].default is Parameter.empty
    assert parameters["emulator"].annotation == "Emulator"
    for name in (
        "scenario.yaml",
        "zones.yaml",
        "emu.py",
        "simu.py",
        "main.py",
        "README.md",
    ):
        assert (EXAMPLE_ROOT / name).is_file()


def test_richer_simu_has_no_standalone_assembly_or_cli() -> None:
    result = run(
        (sys.executable, "simu.py"),
        cwd=EXAMPLE_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""


def test_richer_main_supports_direct_file_execution() -> None:
    main_result = run(
        (sys.executable, "main.py", "--help"),
        cwd=EXAMPLE_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert main_result.returncode == 0, main_result.stderr
    assert "--scenario" in main_result.stdout
    assert "--zones" in main_result.stdout
