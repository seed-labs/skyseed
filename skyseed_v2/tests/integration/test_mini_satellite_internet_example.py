from __future__ import annotations

from collections import Counter, defaultdict
import json
from inspect import Parameter, signature
from pathlib import Path
from subprocess import run
import sys
from types import SimpleNamespace

import pytest
from seedemu.core import Emulator
from yaml import safe_load

from examples.mini_satellite_internet import main as reference_main
from examples.mini_satellite_internet import simu
from examples.mini_satellite_internet.emu import (
    build_mini_satellite_internet_emulator,
)
from seedemu.layers import Base
from skyseed_v2.emulator.seedemu import SkyseedAutonomousSystem
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
from tools.slice import collect_slice_difference_data
from tools.zone import collect_zone_data


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_ROOT = PROJECT_ROOT / "examples/mini_satellite_internet"


@pytest.fixture(scope="module")
def reference_emulator():
    return build_mini_satellite_internet_emulator()


@pytest.fixture(scope="module")
def reference_slicer(reference_emulator) -> Slicer:
    slicer = simu.create_mini_satellite_internet_slicer(reference_emulator)
    slicer.create_all_slices()
    return slicer


@pytest.fixture(scope="module")
def reference_view_data(reference_slicer: Slicer) -> dict[str, object]:
    return collect_slice_difference_data(reference_slicer)


def test_richer_reference_uses_the_formal_bounded_a_f_chain(
    reference_slicer: Slicer,
) -> None:
    topology = reference_slicer.get_topology()
    retained_ids = reference_slicer.get_retained_slice_ids()

    assert simu.DEFAULT_START_TIME.isoformat() == "2026-01-01T00:00:30+00:00"
    assert simu.DEFAULT_END_TIME.isoformat() == "2026-01-01T00:11:00+00:00"
    assert simu.DEFAULT_SLICE_INTERVAL.total_seconds() == 10
    assert simu.REAL_SLICE_COUNT == 64
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

    observed_degree_distributions = Counter()
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
        degree_distribution = Counter(
            active_degree.get(node_id, 0) for node_id in parent_by_node
        )
        observed_degree_distributions[
            tuple(sorted(degree_distribution.items()))
        ] += 1

    assert observed_degree_distributions == Counter(
        {
            ((0, 4), (1, 24), (2, 4)): 56,
            ((0, 8), (1, 24)): 8,
        }
    )

    assert model._prepared_isl_pairs is prepared


def test_richer_emulator_contains_static_network_and_routing_layers() -> None:
    emulator = build_mini_satellite_internet_emulator()
    base = emulator.getLayer("Base")

    assert isinstance(base, Base)
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

    assert emu_source.count("def build_mini_satellite_internet_emulator(") == 1
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
    assert simu_source.count("mini_satellite_internet.emu") == 1
    assert "def create_mini_satellite_internet_progression(" in simu_source
    assert "def build_mini_satellite_internet_emulator(" not in main_source

    with pytest.raises(TypeError):
        simu.create_mini_satellite_internet_slicer()
    with pytest.raises(TypeError, match="upstream Emulator"):
        simu.create_mini_satellite_internet_slicer(object())


def test_richer_main_builds_once_and_emits_static_and_slice_outputs(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    output = tmp_path / "output"
    built = []
    build_paths = []
    compiled = []
    projected = []
    created_simulators = []
    projection_inputs = []
    projection_zone_paths = []
    created_slicers = []
    topology_tool_inputs = []
    zone_tool_inputs = []
    dynamic_tool_inputs = []
    bpf_compiler_inputs = []
    bpf_tool_inputs = []
    real_build = reference_main.build_mini_satellite_internet_emulator
    real_compile = Emulator.compile
    real_progression = reference_main.create_mini_satellite_internet_progression
    real_input_projection = simu.create_simulator_static_input
    real_topology_tool = reference_main.write_topology_tool
    real_zone_tool = reference_main.write_zone_tool
    real_slice_tool = reference_main.write_slice_difference_tool
    real_prepare_planning = (
        reference_main.prepare_mini_satellite_internet_planning
    )
    real_planner_tool = reference_main.write_planner_inspection
    real_bpf_compiler = (
        reference_main._compile_bpf_realizations
    )
    real_bpf_tool = reference_main.write_bpf_compiler_tool

    def build_spy(path):
        emulator = real_build(path)
        built.append(emulator)
        build_paths.append(Path(path))
        return emulator

    def compile_spy(self, compiler, directory, override=False):
        compiled.append(self)
        return real_compile(self, compiler, directory, override)

    def input_projection_spy(autonomous_systems):
        exact_owners = tuple(autonomous_systems)
        projection_inputs.append(exact_owners)
        return real_input_projection(exact_owners)

    def progression_spy(input_directory, *, emulator=None):
        projected.append(emulator)
        projection_zone_paths.append(Path(input_directory) / "zones.yaml")
        simulator, planner = real_progression(
            input_directory,
            emulator=emulator,
        )
        created_simulators.append(simulator)
        created_slicers.append(simulator.get_slicer())
        return simulator, planner

    def topology_tool_spy(topology, directory):
        topology_tool_inputs.append(topology)
        return real_topology_tool(topology, directory)

    def zone_tool_spy(topology, directory):
        zone_tool_inputs.append(topology)
        return real_zone_tool(topology, directory)

    def dynamic_tool_spy(slicer, directory):
        dynamic_tool_inputs.append(slicer)
        return real_slice_tool(slicer, directory)

    planner_tool_inputs = []
    planning_inputs = []

    def planning_spy(simulator, planner=None):
        planning_inputs.append(simulator)
        return real_prepare_planning(simulator, planner)

    def planner_tool_spy(slicer, planner, plans, configuration, directory):
        planner_tool_inputs.append((slicer, planner, plans, configuration))
        return real_planner_tool(
            slicer,
            planner,
            plans,
            configuration,
            directory,
        )

    def bpf_compiler_spy(slicer, plans):
        bpf_compiler_inputs.append((slicer, plans))
        return real_bpf_compiler(slicer, plans)

    def bpf_tool_spy(plans, realizations, directory):
        bpf_tool_inputs.append((plans, realizations))
        return real_bpf_tool(plans, realizations, directory)

    monkeypatch.setattr(
        reference_main,
        "build_mini_satellite_internet_emulator",
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
        "create_mini_satellite_internet_progression",
        progression_spy,
    )
    monkeypatch.setattr(
        reference_main,
        "write_topology_tool",
        topology_tool_spy,
    )
    monkeypatch.setattr(reference_main, "write_zone_tool", zone_tool_spy)
    monkeypatch.setattr(
        reference_main,
        "write_slice_difference_tool",
        dynamic_tool_spy,
    )
    monkeypatch.setattr(
        reference_main,
        "prepare_mini_satellite_internet_planning",
        planning_spy,
    )
    monkeypatch.setattr(
        reference_main,
        "write_planner_inspection",
        planner_tool_spy,
    )
    monkeypatch.setattr(
        reference_main,
        "_compile_bpf_realizations",
        bpf_compiler_spy,
    )
    monkeypatch.setattr(
        reference_main,
        "write_bpf_compiler_tool",
        bpf_tool_spy,
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
    assert build_paths == [output / "inputs/scenario.yaml"]
    assert len(projected) == 1
    assert projected[0] is built[0]
    assert projection_zone_paths == [output / "inputs/zones.yaml"]
    assert len(compiled) == 1
    assert compiled[0] is built[0]
    assert len(projection_inputs) == 1
    base = built[0].getLayer("Base")
    assert isinstance(base, Base)
    expected_as_owners = tuple(
        autonomous_system
        for asn in sorted(base.getAsns())
        if isinstance(
            (autonomous_system := base.getAutonomousSystem(asn)),
            SkyseedAutonomousSystem,
        )
    )
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
    assert planning_inputs == created_simulators
    exact_slicer = created_slicers[0]
    exact_topology = exact_slicer.get_topology()
    assert len(topology_tool_inputs) == 1
    assert topology_tool_inputs[0] is exact_topology
    assert len(zone_tool_inputs) == 1
    assert zone_tool_inputs[0] is exact_topology
    assert len(dynamic_tool_inputs) == 1
    assert dynamic_tool_inputs[0] is exact_slicer
    assert len(planner_tool_inputs) == 1
    assert planner_tool_inputs[0][0] is exact_slicer
    exact_plans = planner_tool_inputs[0][2]
    assert bpf_compiler_inputs == [(exact_slicer, exact_plans)]
    assert len(bpf_tool_inputs) == 1
    assert bpf_tool_inputs[0][0] is exact_plans
    assert bpf_tool_inputs[0][1]

    runtime_output = output / "runtime"
    topology_output = output / "inspection/topology"
    zone_output = output / "inspection/zone"
    slice_output = output / "inspection/slice"
    planner_output = output / "inspection/planner"
    bpf_compiler_output = output / "inspection/bpf-compiler"
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
    run_script = (output / "run.sh").read_text(encoding="utf-8")
    launch = (output / "launch.sh").read_text(encoding="utf-8")
    cleanup = (output / "cleanup.sh").read_text(encoding="utf-8")
    project_name = (output / ".compose-project-name").read_text(
        encoding="utf-8"
    ).strip()
    assert project_name.startswith("skyseed_v2_mini_satellite_internet-")
    assert project_name in start
    assert project_name in cleanup
    assert "config --quiet" in start
    assert "up -d --build" in start
    assert "down --remove-orphans" in cleanup
    assert "docker compose" not in run_script
    assert "-m skyseed_v2._generated_bpf_execution" in run_script
    assert "-m tools.runner" in run_script
    assert "-m tools.traffic" in run_script
    assert "runner-invocation.json" in run_script
    assert "runtime.py" not in run_script
    assert "traffic_workload" not in run_script
    assert "traffic-workload.json" not in run_script
    assert '"$SCRIPT_DIR/start.sh"' in launch
    assert 'exec "$SCRIPT_DIR/run.sh"' in launch
    assert "cleanup" not in launch
    assert "docker" not in launch
    assert "run.sh" not in start
    assert "runtime.py" not in start + cleanup
    assert 'COMPOSE_FILE="$SCRIPT_DIR/runtime/docker-compose.yml"' in start
    assert 'COMPOSE_FILE="$SCRIPT_DIR/runtime/docker-compose.yml"' in cleanup
    for script_name in ("start.sh", "run.sh", "launch.sh", "cleanup.sh"):
        script = output / script_name
        assert script.stat().st_mode & 0o111
        syntax = run(
            ("bash", "-n", str(script)),
            check=False,
            capture_output=True,
            text=True,
        )
        assert syntax.returncode == 0, syntax.stderr
    assert {path.name for path in output.iterdir()} == {
        ".compose-project-name",
        "start.sh",
        "run.sh",
        "launch.sh",
        "cleanup.sh",
        "inputs",
        "runtime",
        "inspection",
    }
    assert (output / "inputs/scenario.yaml").read_bytes() == (
        EXAMPLE_ROOT / "scenario.yaml"
    ).read_bytes()
    assert (output / "inputs/zones.yaml").read_bytes() == (
        EXAMPLE_ROOT / "zones.yaml"
    ).read_bytes()
    execution = json.loads(
        (output / "inputs/execution.json").read_text(encoding="utf-8")
    )
    assert execution["generated_output_identity"] == project_name
    assert execution["progression_builder"] == {
        "module": "examples.mini_satellite_internet.simu",
        "callable": "create_mini_satellite_internet_progression",
        "input_directory": "inputs",
    }
    assert execution["simulation"]["generation_lookahead_seconds"] == 300
    assert execution["runner"] == {
        "preparation_lookahead_seconds": 60,
        "preparation_boundary_depth": 8,
    }
    assert execution["source_fingerprint"]["algorithm"] == "sha256"
    assert [
        scope["label"] for scope in execution["source_fingerprint"]["scopes"]
    ] == ["skyseed", "example-progression", "seedemu"]
    input_scope = next(
        scope
        for scope in execution["generated_inputs"]["scopes"]
        if scope["label"] == "input-snapshots"
    )
    assert input_scope["excludes"] == [
        "execution.json",
        "traffic-workload.json",
    ]
    assert {path.name for path in (output / "inspection").iterdir()} == {
        "topology",
        "zone",
        "slice",
        "planner",
        "bpf-compiler",
    }
    assert (output / "inputs/traffic-workload.json").is_file()
    assert not (output / "inspection/traffic").exists()
    for forbidden in ("emulator", "simulator", "slice", "tools"):
        assert not (output / forbidden).exists()

    for directory, names in (
        (topology_output, ("index.html", "topology.json", "summary.txt")),
        (zone_output, ("index.html", "probes.json", "summary.txt")),
    ):
        for name in names:
            assert (directory / name).is_file()
    for name in (
        "index.html",
        "slice-difference.json",
        "summary.txt",
        "inventory.json",
        "checksums.sha256",
    ):
        assert (slice_output / name).is_file()
    for name in (
        "index.html",
        "planner-inspection.json",
        "summary.txt",
        "inventory.json",
        "checksums.sha256",
    ):
        assert (planner_output / name).is_file()
    for name in (
        "index.html",
        "bpf-compiler-inspection.json",
        "summary.txt",
        "inventory.json",
        "checksums.sha256",
    ):
        assert (bpf_compiler_output / name).is_file()

    topology_payload = json.loads(
        (topology_output / "topology.json").read_text(encoding="utf-8")
    )
    zone_payload = json.loads(
        (zone_output / "probes.json").read_text(encoding="utf-8")
    )
    dynamic_payload = json.loads(
        (slice_output / "slice-difference.json").read_text(encoding="utf-8")
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
        f"Slice / Difference Tool entry: {slice_output / 'index.html'}"
        in output_text
    )
    assert f"Planner Tool entry: {planner_output / 'index.html'}" in output_text
    assert (
        f"BPF Compiler Tool entry: {bpf_compiler_output / 'index.html'}"
        in output_text
    )
    assert "static containers are not updated" in output_text
    main_source = (EXAMPLE_ROOT / "main.py").read_text(encoding="utf-8")
    for removed_regression_logic in (
        "_validate_compose_structure",
        "satellite_services",
        "ground_station_services",
        "_behavior_summary",
    ):
        assert removed_regression_logic not in main_source


def test_generation_guard_preserves_a_live_or_unverifiable_generation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    output = tmp_path / "output"
    output.mkdir()
    identity = output / ".compose-project-name"
    identity.write_text("mini-generation-one\n", encoding="utf-8")
    sentinel = output / "preserve-me"
    sentinel.write_text("original", encoding="utf-8")
    commands = []

    def live_project(command, **kwargs):
        commands.append((tuple(command), kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout="container-id\n",
            stderr="",
        )

    monkeypatch.setattr(reference_main.subprocess, "run", live_project)
    with pytest.raises(RuntimeError, match="cleanup.sh"):
        reference_main._guard_existing_output(output, override=True)
    assert identity.read_text(encoding="utf-8") == "mini-generation-one\n"
    assert sentinel.read_text(encoding="utf-8") == "original"
    assert commands == [
        (
            (
                "docker",
                "ps",
                "--all",
                "--quiet",
                "--filter",
                "label=com.docker.compose.project=mini-generation-one",
            ),
            {"check": False, "capture_output": True, "text": True},
        )
    ]

    monkeypatch.setattr(
        reference_main.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="daemon unavailable",
        ),
    )
    with pytest.raises(RuntimeError, match="cannot verify|failed"):
        reference_main._guard_existing_output(output, override=True)
    assert sentinel.read_text(encoding="utf-8") == "original"


def test_generation_guard_rejects_legacy_output_and_replaces_incomplete_output(
    tmp_path: Path,
    monkeypatch,
) -> None:
    output = tmp_path / "output"
    output.mkdir()
    sentinel = output / "preserve-me"
    sentinel.write_text("legacy", encoding="utf-8")

    def unexpected_docker(*_args, **_kwargs):
        raise AssertionError("legacy output must not guess a Compose project")

    monkeypatch.setattr(reference_main.subprocess, "run", unexpected_docker)
    reference_main._guard_existing_output(
        tmp_path / "absent-output",
        override=True,
    )
    with pytest.raises(RuntimeError, match="unsupported older layout"):
        reference_main._guard_existing_output(output, override=True)
    assert sentinel.read_text(encoding="utf-8") == "legacy"

    (output / ".compose-project-name").write_text(
        "mini-incomplete\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        reference_main.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="",
            stderr="",
        ),
    )
    reference_main._guard_existing_output(output, override=True)
    runtime = reference_main.prepare_example_output(output, override=True)
    assert runtime == output / "runtime"
    assert tuple(output.iterdir()) == ()


def test_supported_external_ground_station_input_is_snapshotted_relatively(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    (source / "ground").mkdir(parents=True)
    scenario = source / "scenario.yaml"
    zones = source / "zones.yaml"
    stations = source / "ground/stations.yaml"
    scenario.write_text(
        "skyseed_autonomous_systems:\n"
        "  - ground_stations_file: ground/stations.yaml\n"
        "  - ground_stations_file: ground/stations.yaml\n",
        encoding="utf-8",
    )
    zones.write_text("zones: []\n", encoding="utf-8")
    stations.write_text("ground_stations: []\n", encoding="utf-8")

    snapshots = reference_main._capture_input_files(scenario, zones)
    output = tmp_path / "output"
    reference_main._write_input_snapshots(output, snapshots)

    assert set(snapshots) == {
        Path("scenario.yaml"),
        Path("zones.yaml"),
        Path("ground/stations.yaml"),
    }
    assert (output / "inputs/scenario.yaml").read_bytes() == scenario.read_bytes()
    assert (output / "inputs/zones.yaml").read_bytes() == zones.read_bytes()
    assert (output / "inputs/ground/stations.yaml").read_bytes() == (
        stations.read_bytes()
    )


def test_richer_reference_naturally_exercises_representative_behavior(
    reference_view_data: dict[str, object],
) -> None:
    assert reference_view_data["inventory"] == {
        "nodes": 35,
        "satellites": 32,
        "ground_stations": 3,
        "orbits": 4,
        "zones": 5,
        "slices": 65,
        "differences": 64,
        "paths": 11264,
    }
    assert reference_view_data["behavior"] == {
        "later_nonempty_differences": 63,
        "connection_additions": 11,
        "connection_removals": 14,
        "directional_parameter_patches": 2530,
        "zone_assignment_additions": 4,
        "zone_assignment_changes": 1,
        "zone_assignment_removals": 4,
        "route_additions": 169,
        "route_changes": 137,
        "route_removals": 239,
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
    assert sum(len(set(values)) > 1 for values in access_sets.values()) == 3

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
    assert "Reference Ground Station - Porto Alegre, Brazil" in scenario_source
    assert "Reference Ground Station - Marsala, Italy" in scenario_source
    assert "Reference Ground Station - Yamaguchi, Japan" in scenario_source
    assert "Starlink Gateway -" not in scenario_source
    assert "commercial operator's current" in scenario_source
    assert "external source URL" in readme_source
    assert "commercial\noperator's current in-service facilities" in readme_source
    for path in ("emu.py", "simu.py", "main.py"):
        assert "skyseed_v1" not in (
            EXAMPLE_ROOT / path
        ).read_text(encoding="utf-8")


def test_richer_reference_core_identities_and_counts_repeat_deterministically(
    reference_view_data: dict[str, object],
) -> None:
    repeated_emulator = build_mini_satellite_internet_emulator()
    repeated = simu.create_mini_satellite_internet_slicer(repeated_emulator)
    repeated.create_all_slices()

    assert collect_slice_difference_data(repeated) == reference_view_data


def test_richer_reference_sources_do_not_construct_formal_slice_results() -> None:
    source = (EXAMPLE_ROOT / "simu.py").read_text(encoding="utf-8")
    for forbidden in (
        "argparse",
        "write_topology_tool",
        "write_zone_tool",
        "write_slice_difference_tool",
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
        "def create_mini_satellite_internet_slicer(",
        "def prepare_mini_satellite_internet_planning(",
        "create_simulator_static_input(",
        "Simulator(",
        ".create_topology(",
        ".create_slicer(",
    ):
        assert required in source
    main_source = (EXAMPLE_ROOT / "main.py").read_text(encoding="utf-8")
    bootstrap_source = (
        PROJECT_ROOT / "src/skyseed_v2/_generated_bpf_execution.py"
    ).read_text(encoding="utf-8")
    assert main_source.count("write_topology_tool(") == 1
    assert main_source.count("write_zone_tool(") == 1
    assert main_source.count("write_slice_difference_tool(") == 1
    assert main_source.count("write_canonical_traffic_workload(") == 1
    assert main_source.count("slicer.create_all_slices()") == 1
    assert main_source.count(".get_topology()") == 1
    assert main_source.index(
        "(arguments.output / _PROJECT_NAME_FILE).write_text"
    ) < main_source.index("_write_input_snapshots(arguments.output, snapshots)")
    assert main_source.index(
        "_write_input_snapshots(arguments.output, snapshots)"
    ) < main_source.index(
        "emulator = build_mini_satellite_internet_emulator(snapshot_scenario)"
    )
    assert main_source.index("if not all(result[") < main_source.index(
        "write_compose_project_scripts("
    )
    for forbidden in (
        "AccumulatedDeadbandParameterPlanningModel",
        "DefaultCoordinationPolicy",
        "DependencyAwareForwardingPlanningModel",
        "MidpointConnectionPlanningModel",
        ".create_planner(",
        ".synchronize(",
        "Simulator(",
        ".create_topology(",
        "load_zone_layout_definition(",
        "create_simulator_static_input(",
    ):
        assert forbidden not in main_source
    for forbidden in (
        "ComposeProjectLauncher",
        "ComposeServiceExpectations",
        "ComposeMaterializationResult",
        "dummies",
    ):
        assert forbidden not in main_source
    assert bootstrap_source.count("runner._enable_traffic_telemetry(") == 1
    for forbidden in (
        "BpfRunner",
        "_materialize_runtime",
        "_run_bpf_vertical_slice",
        "--run-bpf",
    ):
        assert forbidden not in main_source
    assert bootstrap_source.count("runner.prepare()") == 1
    assert bootstrap_source.count("runner.run()") == 1
    assert bootstrap_source.count("runner.cleanup()") == 1
    assert bootstrap_source.count("_write_runner_inspection_handoff(") == 2
    assert bootstrap_source.index("runner.cleanup()") < bootstrap_source.index(
        "_write_runner_inspection_handoff("
    )
    for forbidden in (
        "from tools",
        "import tools",
        "write_runner_tool",
        "write_traffic_replay_tool",
        "realization_observer",
        "automatic_run",
        "finalizer",
        "_MiniZoneServiceProgression",
        "traffic_workload",
    ):
        assert forbidden not in bootstrap_source
    assert bootstrap_source.count('"traffic-workload.json"') == 1
    assert (
        'excludes=("execution.json", "traffic-workload.json")'
        in bootstrap_source
    )
    assert not (EXAMPLE_ROOT / "_compose_support.py").exists()
    parameters = signature(
        simu.create_mini_satellite_internet_slicer
    ).parameters
    assert tuple(parameters) == ("emulator", "zone_layout_path")
    assert parameters["emulator"].default is Parameter.empty
    assert parameters["emulator"].annotation == "Emulator"
    for name in (
        "scenario.yaml",
        "zones.yaml",
        "emu.py",
        "simu.py",
        "main.py",
        "traffic_workload.py",
        "README.md",
    ):
        assert (EXAMPLE_ROOT / name).is_file()
    for retired in ("runtime.py", "run.py", "canonical_run.py"):
        assert not (EXAMPLE_ROOT / retired).exists()


def test_generation_and_generated_execution_compile_loops_stay_separate() -> None:
    main_source = (EXAMPLE_ROOT / "main.py").read_text(encoding="utf-8")
    bootstrap_source = (
        PROJECT_ROOT / "src/skyseed_v2/_generated_bpf_execution.py"
    ).read_text(encoding="utf-8")

    assert main_source.count("def _compile_bpf_realizations(") == 1
    assert "_RollingRealizationSupplier(" in bootstrap_source
    assert main_source.count("result = compile_bpf(") == 1
    assert bootstrap_source.count("realization = compile_bpf(") == 1
    for forbidden in (
        "create_all_slices(",
        "prepare_mini_satellite_internet_planning(",
        "iter(realizations)",
        "_probe_ground_station_access_peer(",
        "_probe_zone_ground_round_trip(",
        "_probe_",
    ):
        assert forbidden not in bootstrap_source
    for mini_specific in (
        "examples.mini_satellite_internet",
        "north-america-east",
        "Marsala",
        "gs-marsala-reference",
        "2026, 1, 1",
        "O4 Sat-0002",
        "O1 Sat-0001",
    ):
        assert mini_specific not in bootstrap_source


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
    assert "--run-bpf" not in main_result.stdout
