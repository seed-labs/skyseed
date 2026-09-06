from __future__ import annotations

from collections import Counter, defaultdict
from inspect import Parameter, signature
import json
import os
from pathlib import Path
from subprocess import run
import sys
from types import SimpleNamespace

import pytest
from seedemu.core import Emulator
from seedemu.layers import Base
from yaml import safe_load

from examples.mini_satellite_internet import main as mini_main
from examples.mini_satellite_internet import simu as mini_simu
from examples.mini_satellite_internet.emu import (
    build_mini_satellite_internet_emulator,
)
from examples.mini_satellite_internet.simu import (
    FORMAL_BOUNDARY_COUNT,
    create_mini_satellite_internet_producer,
    drain_mini_satellite_internet,
)
from skyseed_v2.emulator.seedemu import SkyseedAutonomousSystem
from skyseed_v2.simulator import (
    OperationalConnectionKind,
    Simulator,
    apply_plan_transition,
)
from skyseed_v2.simulator.geometry import (
    elevation_deg,
    geodetic_to_ecef,
    has_earth_line_of_sight,
)
from tools.bpf_compiler import read_bpf_compiler_history
from tools.planner import read_planner_history


EXAMPLE_ROOT = (
    Path(__file__).resolve().parents[2]
    / "examples/mini_satellite_internet"
)
PROJECT_ROOT = EXAMPLE_ROOT.parents[1]


@pytest.fixture(scope="module")
def reference_emulator() -> Emulator:
    return build_mini_satellite_internet_emulator()


@pytest.fixture(scope="module")
def reference_chain(
    tmp_path_factory: pytest.TempPathFactory,
    reference_emulator: Emulator,
) -> SimpleNamespace:
    history = tmp_path_factory.mktemp("mini-domain-history")
    simulator, producer = create_mini_satellite_internet_producer(
        EXAMPLE_ROOT,
        emulator=reference_emulator,
        evidence_path=history,
    )
    plans, realizations, snapshot = drain_mini_satellite_internet(
        simulator,
        producer,
    )
    evidence = read_planner_history(history / "planning.jsonl.gz")
    transitions = tuple(
        transition
        for plan in plans
        for transition in plan.transitions
    )
    frames = tuple(frame for value in evidence for frame in value.frames)
    states = []
    state = None
    for transition in transitions:
        state = apply_plan_transition(state, transition)
        states.append(state)
    return SimpleNamespace(
        simulator=simulator,
        physical_world=simulator.get_physical_world(),
        plans=plans,
        realizations=realizations,
        snapshot=snapshot,
        history=history,
        evidence=evidence,
        transitions=transitions,
        frames=frames,
        states=tuple(states),
    )


def _satellite_parents(physical_world) -> dict[object, tuple[int, str, str]]:
    return {
        satellite.node_id: (
            autonomous_system.asn,
            shell.shell_id,
            orbit.orbit_id,
        )
        for autonomous_system in physical_world.autonomous_systems
        for shell in autonomous_system.shells
        for orbit in shell.orbits
        for satellite in orbit.satellites
    }


def _is_dynamic_transition(transition) -> bool:
    return bool(
        transition.established_connections
        or transition.withdrawn_connection_ids
        or transition.parameter_updated_connections
        or transition.zone_service_transitions
        or transition.route_outcome_transitions
    )


def test_mini_chain_uses_one_producer_and_preserves_all_boundaries(
    reference_chain: SimpleNamespace,
) -> None:
    simulator = reference_chain.simulator
    plans = reference_chain.plans
    realizations = reference_chain.realizations
    snapshot = reference_chain.snapshot
    history = reference_chain.history

    transition_timestamps = tuple(
        transition.timestamp
        for plan in plans
        for transition in plan.transitions
    )
    activation_timestamps = tuple(
        timestamp
        for realization in realizations
        for timestamp in realization.activation_timestamps
    )
    assert transition_timestamps == tuple(
        simulator.get_timeline().iter_timestamps()
    )
    assert activation_timestamps == transition_timestamps
    assert len(transition_timestamps) == FORMAL_BOUNDARY_COUNT == 64
    assert len(plans) == len(realizations) == 32
    assert snapshot["formal_frame_count"] == FORMAL_BOUNDARY_COUNT
    assert snapshot["physical_link_observation_count"] == FORMAL_BOUNDARY_COUNT
    assert snapshot["transition_count"] == FORMAL_BOUNDARY_COUNT
    assert snapshot["plan_count"] == 32
    assert snapshot["realization_count"] == 32
    assert snapshot["domain_history_sealed"] is True

    manifest = json.loads(
        (history / "domain-history-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["through"] == "compiler"
    assert manifest["counts"]["formal_frames"] == FORMAL_BOUNDARY_COUNT
    assert manifest["counts"]["transitions"] == FORMAL_BOUNDARY_COUNT
    assert manifest["counts"]["plans"] == 32
    assert manifest["counts"]["realizations"] == 32
    assert len(reference_chain.evidence) == 32
    assert len(
        read_bpf_compiler_history(history / "compiler.jsonl")[
            "realizations"
        ]
    ) == 32


def test_mini_observation_and_tool_failures_preserve_runner_artifact(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    output = tmp_path / "output"

    def fail_observation_write(_simulator):
        raise RuntimeError("forced observation write failure")

    def fail_tool_batch(*_args, **_kwargs):
        raise RuntimeError("forced Tool batch failure")

    monkeypatch.setattr(
        Simulator,
        "_write_execution_observations",
        fail_observation_write,
    )
    monkeypatch.setattr(
        mini_main,
        "_write_inspection_tools",
        fail_tool_batch,
    )

    assert mini_main.main(
        ["--output", str(output), "--no-internet-map"]
    ) == 0

    for relative in (
        "runtime/runner-artifact/manifest.json",
        "runtime/execution.json",
        "domain-history/domain-history-manifest.json",
        "start.sh",
        "run.sh",
        "launch.sh",
        "cleanup.sh",
    ):
        assert (output / relative).is_file()
    capture = capsys.readouterr()
    assert "Runner artifact:" in capture.out
    assert "did not invalidate Runner artifact" in capture.err
    assert "inspection-batch" in capture.err
    assert "observation-evidence" in capture.err


def test_mini_generated_anomalies_preserve_the_alternating_policy(
    reference_chain: SimpleNamespace,
) -> None:
    physical_world = reference_chain.physical_world
    orbits = sorted(
        physical_world.autonomous_systems[0].shells[0].orbits,
        key=lambda orbit: orbit.orbit_id,
    )

    assert [
        [
            satellite.mean_anomaly_deg
            for satellite in sorted(
                orbit.satellites,
                key=lambda satellite: satellite.orbit_index,
            )
        ]
        for orbit in orbits
    ] == [
        [0.0, 45.0, 90.0, 135.0, 180.0, 225.0, 270.0, 315.0],
        [22.5, 67.5, 112.5, 157.5, 202.5, 247.5, 292.5, 337.5],
        [0.0, 45.0, 90.0, 135.0, 180.0, 225.0, 270.0, 315.0],
        [22.5, 67.5, 112.5, 157.5, 202.5, 247.5, 292.5, 337.5],
    ]
    assert all(
        station.max_simultaneous_satellite_accesses is None
        for autonomous_system in physical_world.autonomous_systems
        for station in autonomous_system.ground_stations
    )


def test_mini_reference_uses_the_bounded_physical_planning_compiler_chain(
    reference_chain: SimpleNamespace,
) -> None:
    timeline = reference_chain.simulator.get_timeline()
    physical_world = reference_chain.physical_world

    assert mini_simu.DEFAULT_START_TIME.isoformat() == (
        "2026-01-01T00:00:30+00:00"
    )
    assert mini_simu.DEFAULT_END_TIME.isoformat() == (
        "2026-01-01T00:11:00+00:00"
    )
    assert mini_simu.DEFAULT_OBSERVATION_INTERVAL.total_seconds() == 10
    assert mini_simu.PLANNING_HORIZON.total_seconds() == 50
    assert mini_simu.PUBLICATION_HORIZON.total_seconds() == 20
    assert mini_simu.ACQUISITION_DURATION.total_seconds() == 20
    assert tuple(
        transition.timestamp for transition in reference_chain.transitions
    ) == tuple(timeline.iter_timestamps())
    assert Counter(
        len(plan.transitions) for plan in reference_chain.plans
    ) == Counter({2: 30, 3: 1, 1: 1})
    assert len(reference_chain.plans) == 32
    assert len(reference_chain.transitions) == FORMAL_BOUNDARY_COUNT == 64
    assert all(
        _is_dynamic_transition(transition)
        for transition in reference_chain.transitions[1:]
    )
    assert sum(
        len(orbit.satellites)
        for autonomous_system in physical_world.autonomous_systems
        for shell in autonomous_system.shells
        for orbit in shell.orbits
    ) == 32
    assert sum(
        len(autonomous_system.ground_stations)
        for autonomous_system in physical_world.autonomous_systems
    ) == 3
    assert len(physical_world.get_zone_layout().zones) == 5


def test_cross_plane_scope_and_selected_degree_preserve_the_k3_policy(
    reference_chain: SimpleNamespace,
) -> None:
    physical_world = reference_chain.physical_world
    parent_by_node = _satellite_parents(physical_world)
    request = reference_chain.simulator.get_planner().create_physical_link_request()
    pairs = request.inter_satellite_pairs

    assert len(pairs) == len(set(pairs)) == 496
    assert all(
        (first.asn, first.entity_kind, first.entity_id)
        < (second.asn, second.entity_kind, second.entity_id)
        for first, second in pairs
    )
    assert sum(
        parent_by_node[first] == parent_by_node[second]
        for first, second in pairs
    ) == 112
    assert sum(
        parent_by_node[first] != parent_by_node[second]
        for first, second in pairs
    ) == 384

    selected_cross_degree_distributions = Counter()
    for frame in reference_chain.frames:
        same_degree = Counter()
        cross_degree = Counter()
        total_degree = Counter()
        for lifecycle in frame.selected_inter_satellite_links:
            candidate = lifecycle.candidate
            first = candidate.endpoint_a
            second = candidate.endpoint_b
            total_degree[first] += 1
            total_degree[second] += 1
            target = (
                same_degree
                if parent_by_node[first] == parent_by_node[second]
                else cross_degree
            )
            target[first] += 1
            target[second] += 1
        assert max(total_degree.values(), default=0) <= 4
        assert max(same_degree.values(), default=0) <= 2
        assert max(cross_degree.values(), default=0) <= 2
        selected_cross_degree_distributions[
            tuple(
                sorted(
                    Counter(
                        cross_degree.get(node_id, 0)
                        for node_id in parent_by_node
                    ).items()
                )
            )
        ] += 1

    assert selected_cross_degree_distributions == Counter(
        {
            ((1, 8), (2, 24)): 33,
            ((2, 32),): 18,
            ((1, 4), (2, 28)): 13,
        }
    )


def test_mini_emulator_contains_static_network_and_routing_layers() -> None:
    emulator = build_mini_satellite_internet_emulator()
    base = emulator.getLayer("Base")

    assert isinstance(base, Base)
    assert str(base.getInternetExchange(110).getPeeringLan().getPrefix()) == (
        "10.110.0.0/24"
    )
    assert base.getAutonomousSystem(65110).getAsn() == 65110
    assert {layer.getName() for layer in emulator.getLayers()} == {
        "Base",
        "Routing",
        "Ospf",
        "Ibgp",
        "Ebgp",
    }


def test_mini_has_one_emulator_builder_and_fail_closed_projection() -> None:
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
    assert "def create_mini_satellite_internet_producer(" in simu_source
    assert "def create_mini_satellite_internet_planner(" in simu_source
    assert "def build_mini_satellite_internet_emulator(" not in main_source

    with pytest.raises(TypeError, match="upstream Emulator"):
        mini_simu.create_mini_satellite_internet_simulator(object())
    with pytest.raises(TypeError, match="upstream Emulator"):
        create_mini_satellite_internet_producer(
            EXAMPLE_ROOT,
            emulator=object(),
        )
    with pytest.raises(TypeError, match="Simulator"):
        mini_simu.create_mini_satellite_internet_planner(object())


def test_mini_main_builds_once_and_emits_current_round_b_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "output"
    built = []
    build_paths = []
    compiled = []
    projected = []
    producer_calls = []
    tool_calls = []
    real_build = mini_main.build_mini_satellite_internet_emulator
    real_compile = Emulator.compile
    real_projection = mini_simu.create_simulator_static_input
    real_producer = mini_main.create_mini_satellite_internet_producer
    real_tools = mini_main._write_inspection_tools

    def build_spy(path):
        emulator = real_build(path)
        built.append(emulator)
        build_paths.append(Path(path))
        return emulator

    def compile_spy(self, compiler, directory, override=False):
        compiled.append(self)
        return real_compile(self, compiler, directory, override)

    def projection_spy(autonomous_systems):
        owners = tuple(autonomous_systems)
        projected.append(owners)
        return real_projection(owners)

    def producer_spy(
        input_directory,
        *,
        emulator=None,
        evidence_path=None,
    ):
        result = real_producer(
            input_directory,
            emulator=emulator,
            evidence_path=evidence_path,
        )
        producer_calls.append(
            {
                "input_directory": Path(input_directory),
                "emulator": emulator,
                "evidence_path": Path(evidence_path),
                "simulator": result[0],
                "producer": result[1],
            }
        )
        return result

    def tools_spy(output_directory, **kwargs):
        tool_calls.append((Path(output_directory), kwargs))
        return real_tools(output_directory, **kwargs)

    monkeypatch.setattr(
        mini_main,
        "build_mini_satellite_internet_emulator",
        build_spy,
    )
    monkeypatch.setattr(Emulator, "compile", compile_spy)
    monkeypatch.setattr(
        mini_simu,
        "create_simulator_static_input",
        projection_spy,
    )
    monkeypatch.setattr(
        mini_main,
        "create_mini_satellite_internet_producer",
        producer_spy,
    )
    monkeypatch.setattr(mini_main, "_write_inspection_tools", tools_spy)

    assert mini_main.main(
        ["--output", str(output), "--no-internet-map"]
    ) == 0
    assert len(built) == 1
    assert build_paths == [output / "inputs/scenario.yaml"]
    assert compiled == built
    assert len(projected) == 1
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
    assert len(projected[0]) == len(expected_as_owners)
    assert all(
        actual is expected
        for actual, expected in zip(
            projected[0], expected_as_owners, strict=True
        )
    )
    assert len(producer_calls) == 1
    producer_call = producer_calls[0]
    assert producer_call["input_directory"] == output / "inputs"
    assert producer_call["emulator"] is built[0]
    assert producer_call["evidence_path"] == output / "domain-history"
    assert len(tool_calls) == 1
    tool_output, tool_values = tool_calls[0]
    assert tool_output == output
    assert tool_values["simulator"] is producer_call["simulator"]
    first_frame, first_observation = producer_call[
        "producer"
    ].first_formal_input()
    assert tool_values["physical_frame"] is first_frame
    assert tool_values["physical_link_observation"] is first_observation
    assert tool_values["plans"][-1] is producer_call["producer"].latest_plan
    assert len(tool_values["plans"]) == len(tool_values["realizations"]) == 32

    runtime_output = output / "runtime"
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
    project_name = (output / "runtime/compose-project-name").read_text(
        encoding="utf-8"
    ).strip()
    assert project_name.startswith("skyseed_v2_mini_satellite_internet-")
    assert project_name in start
    assert project_name in cleanup
    assert "config --quiet" in start
    assert "up -d --build" in start
    assert "down --remove-orphans" in cleanup
    assert "docker compose" not in run_script
    assert "-m skyseed_v2.experiment.execution" in run_script
    assert "-m tools.runner" in run_script
    assert "-m tools.traffic" in run_script
    assert "tools.observation" in run_script
    assert "inspection/runtime-runs/$INVOCATION_ID" in run_script
    assert '--invocation-directory "$INVOCATION_DIR"' in run_script
    assert '"$INVOCATION_DIR/runner/runner-invocation.json"' in run_script
    assert '"$INVOCATION_DIR/runner/runner-events.jsonl"' in run_script
    assert '"$INVOCATION_DIR/traffic/traffic-telemetry.jsonl"' in run_script
    assert '"$INVOCATION_DIR/traffic/probe-execution.jsonl"' in run_script
    assert (
        '"$INVOCATION_DIR/observation/execution-observations.jsonl"'
        in run_script
    )
    assert '--workload "$SCRIPT_DIR/inputs/traffic-workload.json"' in run_script
    assert "inputs/shells.yaml" not in run_script
    assert "inputs/ground_stations.yaml" not in run_script
    assert 'exit "$runner_tool_status"' not in run_script
    assert 'exit "$traffic_tool_status"' not in run_script
    assert '"$SCRIPT_DIR/start.sh"' in launch
    assert 'exec "$SCRIPT_DIR/run.sh"' in launch
    assert "cleanup" not in launch
    assert "docker" not in launch
    assert "run.sh" not in start
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
        "start.sh",
        "run.sh",
        "launch.sh",
        "cleanup.sh",
        "inputs",
        "runtime",
        "domain-history",
        "inspection",
    }
    assert (output / "inputs/scenario.yaml").read_bytes() == (
        EXAMPLE_ROOT / "scenario.yaml"
    ).read_bytes()
    assert (output / "inputs/zones.yaml").read_bytes() == (
        EXAMPLE_ROOT / "zones.yaml"
    ).read_bytes()
    assert (output / "inputs/traffic-workload.json").is_file()
    assert (output / "inspection/traffic/probe-plan.json").is_file()
    assert (output / "inspection/index.html").is_file()
    for directory, names in (
        (
            output / "inspection/physical-world",
            ("index.html", "physical-world.json", "summary.txt"),
        ),
        (
            output / "inspection/zone",
            ("index.html", "zones.json", "summary.txt"),
        ),
        (
            output / "inspection/planner",
            (
                "index.html",
                "planner-inspection.json",
                "summary.txt",
                "inventory.json",
                "checksums.sha256",
            ),
        ),
        (
            output / "inspection/bpf-compiler",
            (
                "index.html",
                "bpf-compiler-inspection.json",
                "summary.txt",
                "inventory.json",
                "checksums.sha256",
            ),
        ),
    ):
        for name in names:
            assert (directory / name).is_file()

    execution = json.loads(
        (output / "runtime/execution.json").read_text(encoding="utf-8")
    )
    assert execution["format_version"] == 3
    assert execution["generated_output_identity"] == project_name
    assert execution["progression"] == {
        "strategy": "sealed-precomputed-runner-artifact",
        "producer": (
            "skyseed-v2-common-incremental-realization-producer-v1"
        ),
    }
    assert execution["simulation"] == {
        "start_time": mini_simu.DEFAULT_START_TIME.isoformat(),
        "end_time": mini_simu.DEFAULT_END_TIME.isoformat(),
        "observation_interval_seconds": 10.0,
        "bounded": True,
    }
    assert execution["runner"] == {
        "execution_contract": "sealed-precomputed-only",
    }
    assert execution["runner_artifact"]["realization_count"] == 32
    assert execution["runner_artifact"]["format_version"] == 3
    assert execution["runner_artifact"]["encoding"] == (
        "typed-packed-runtime-tables-v3"
    )
    assert [
        scope["label"] for scope in execution["source_fingerprint"]["scopes"]
    ] == ["skyseed", "example-progression", "seedemu"]

    manifest = json.loads(
        (output / "domain-history/domain-history-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["through"] == "compiler"
    assert manifest["counts"]["formal_frames"] == 64
    assert manifest["counts"]["plans"] == 32
    assert manifest["counts"]["realizations"] == 32
    output_text = capsys.readouterr().out
    assert f"runtime directory: {runtime_output}" in output_text
    assert "producer: 32 Plans / 64 formal transitions" in output_text
    assert "Runner artifact:" in output_text


def test_generation_guard_preserves_a_live_or_unverifiable_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "output"
    output.mkdir()
    identity = output / "runtime/compose-project-name"
    identity.parent.mkdir()
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

    monkeypatch.setattr(mini_main.subprocess, "run", live_project)
    with pytest.raises(RuntimeError, match="cleanup.sh"):
        mini_main._guard_existing_output(output, override=True)
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
        mini_main.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="daemon unavailable",
        ),
    )
    with pytest.raises(RuntimeError, match="cannot verify"):
        mini_main._guard_existing_output(output, override=True)
    assert sentinel.read_text(encoding="utf-8") == "original"


def test_generation_guard_rejects_unowned_runtime_and_replaces_incomplete_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "output"
    runtime = output / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "docker-compose.yml").write_text(
        "services: {}\n", encoding="utf-8"
    )
    sentinel = output / "preserve-me"
    sentinel.write_text("legacy", encoding="utf-8")

    def unexpected_docker(*_args, **_kwargs):
        raise AssertionError("unowned output must not guess a Compose project")

    monkeypatch.setattr(mini_main.subprocess, "run", unexpected_docker)
    with pytest.raises(RuntimeError, match="no verifiable Compose identity"):
        mini_main._guard_existing_output(output, override=True)
    assert sentinel.read_text(encoding="utf-8") == "legacy"

    (runtime / "docker-compose.yml").unlink()
    mini_main._guard_existing_output(output, override=True)
    prepared = mini_main._prepare_generated_output(output, replace=True)
    assert prepared == output / "runtime"
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

    snapshots = mini_main._capture_input_files(scenario, zones)
    output = tmp_path / "output"
    mini_main._write_input_snapshots(output, snapshots)

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


def test_mini_reference_naturally_exercises_representative_behavior(
    reference_chain: SimpleNamespace,
) -> None:
    transitions = reference_chain.transitions
    frames = reference_chain.frames
    totals = {
        field_name: sum(
            len(getattr(transition, field_name))
            for transition in transitions
        )
        for field_name in (
            "established_connections",
            "withdrawn_connection_ids",
            "parameter_updated_connections",
            "zone_service_transitions",
            "route_outcome_transitions",
        )
    }
    assert totals == {
        "established_connections": 86,
        "withdrawn_connection_ids": 26,
        "parameter_updated_connections": 1273,
        "zone_service_transitions": 14,
        "route_outcome_transitions": 43,
    }
    assert sum(_is_dynamic_transition(value) for value in transitions[1:]) == 63
    assert len(frames) == 64
    assert all(
        frame.inspection.stages
        == (
            "physical-input-validation",
            "isl-candidate-selection",
            "acquisition/lifecycle-advancement",
            "ground-station-planning",
            "zone-planning",
            "parameter-planning",
            "route-planning",
            "coordination",
            "state-advancement",
        )
        for frame in frames
    )
    assert sum(frame.inspection.acquisition_started for frame in frames) == 18
    assert sum(frame.inspection.acquisition_completed for frame in frames) == 80
    assert sum(frame.inspection.acquisition_reset for frame in frames) == 20
    assert Counter(
        frame.inspection.route_recomputed for frame in frames
    ) == Counter({False: 41, True: 23})

    parent_by_node = _satellite_parents(reference_chain.physical_world)
    first_connections = Counter()
    for connection in reference_chain.states[0].operational_connections:
        identity = connection.connection_id
        if identity.kind is OperationalConnectionKind.GROUND_STATION_ACCESS:
            first_connections["ground-station-access"] += 1
        elif parent_by_node[identity.endpoint_a] == parent_by_node[
            identity.endpoint_b
        ]:
            first_connections["same-plane"] += 1
        else:
            first_connections["cross-plane"] += 1
    assert first_connections == Counter(
        {
            "same-plane": 32,
            "cross-plane": 30,
            "ground-station-access": 3,
        }
    )
    assert all(
        len(state.zone_service_assignments) == 5
        for state in reference_chain.states
    )

    zone_operations = Counter()
    route_operations = Counter()
    for transition in transitions:
        for value in transition.zone_service_transitions:
            if (
                value.previous_satellite_id is None
                and value.current_satellite_id is None
            ):
                operation = "explicit-unserved"
            elif value.previous_satellite_id is None:
                operation = "added"
            elif value.current_satellite_id is None:
                operation = "removed"
            else:
                operation = "changed"
            zone_operations[operation] += 1
        for value in transition.route_outcome_transitions:
            if value.previous is None:
                operation = "added"
            elif value.current is None:
                operation = "removed"
            else:
                operation = "changed"
            route_operations[operation] += 1
    assert zone_operations == Counter(
        {"added": 7, "removed": 4, "explicit-unserved": 2, "changed": 1}
    )
    assert route_operations == Counter(
        {"changed": 29, "added": 11, "removed": 3}
    )


def test_zone_service_uses_representative_point_not_subpoint_containment(
    reference_chain: SimpleNamespace,
) -> None:
    physical_world = reference_chain.physical_world
    outside = None
    for evidence in reference_chain.frames:
        physical_frame = physical_world.frame_at(evidence.timestamp)
        for candidate in evidence.selected_zone_service_candidates:
            position = physical_frame.positions[candidate.satellite_id]
            zone = physical_world.get_zone(candidate.zone_id)
            if not zone.contains(
                position.latitude_deg,
                position.longitude_deg,
            ):
                outside = (evidence, candidate, physical_frame, zone)
                break
        if outside is not None:
            break

    assert outside is not None
    evidence, candidate, physical_frame, zone = outside
    assert candidate.elevation_deg >= mini_simu.ZONE_COVERAGE_MINIMUM_ELEVATION_DEG
    representative_position = geodetic_to_ecef(
        zone.representative_point[0],
        zone.representative_point[1],
        0.0,
    )
    eligible = []
    for node_id, position in physical_frame.positions.items():
        if node_id.entity_kind != "satellite":
            continue
        elevation = elevation_deg(representative_position, position)
        if (
            elevation >= mini_simu.ZONE_COVERAGE_MINIMUM_ELEVATION_DEG
            and has_earth_line_of_sight(representative_position, position)
        ):
            eligible.append(
                (
                    -elevation,
                    (node_id.asn, node_id.entity_kind, node_id.entity_id),
                    node_id,
                )
            )
    assert eligible
    assert candidate.satellite_id == min(eligible)[2]
    assert candidate.elevation_deg == pytest.approx(-min(eligible)[0])
    assert evidence.timestamp == physical_frame.timestamp


def test_reference_zone_overlap_preserves_all_memberships_and_priority_winner(
    reference_chain: SimpleNamespace,
) -> None:
    physical_world = reference_chain.physical_world
    layout = physical_world.get_zone_layout()
    probe = (42.0, -74.0)

    assert layout.allow_overlap
    assert {
        zone.zone_id for zone in physical_world.find_zones(*probe)
    } == {"north-america-east", "northeast-priority"}
    assert physical_world.resolve_zone(*probe).zone_id == "northeast-priority"
    for zone_id in ("north-america-east", "northeast-priority"):
        zone = layout.get_zone(zone_id)
        assert zone.contains(*zone.representative_point)


def test_reference_ground_stations_keep_reference_values_and_dynamic_access(
    reference_chain: SimpleNamespace,
) -> None:
    expected = {
        "gs-porto-alegre-reference": (-30.0346, -51.2177, 0.0),
        "gs-marsala-reference": (37.7986, 12.4357, 0.0),
        "gs-yamaguchi-reference": (34.1861, 131.4706, 0.0),
    }
    physical_world = reference_chain.physical_world
    ground_stations = {
        station.ground_station_id: (
            station.latitude_deg,
            station.longitude_deg,
            station.elevation_m,
        )
        for autonomous_system in physical_world.autonomous_systems
        for station in autonomous_system.ground_stations
    }
    assert ground_stations == expected
    representative_points = {
        zone.representative_point
        for zone in physical_world.get_zone_layout().zones
    }
    assert not representative_points.intersection(
        (latitude, longitude)
        for latitude, longitude, _ in ground_stations.values()
    )

    station_ids = {
        station.node_id
        for autonomous_system in physical_world.autonomous_systems
        for station in autonomous_system.ground_stations
    }
    access_sets = {node_id: [] for node_id in station_ids}
    for state in reference_chain.states:
        per_station = defaultdict(set)
        for connection in state.operational_connections:
            identity = connection.connection_id
            if identity.kind is not OperationalConnectionKind.GROUND_STATION_ACCESS:
                continue
            ground_station = next(
                endpoint
                for endpoint in (identity.endpoint_a, identity.endpoint_b)
                if endpoint in station_ids
            )
            satellite = (
                identity.endpoint_b
                if ground_station == identity.endpoint_a
                else identity.endpoint_a
            )
            per_station[ground_station].add(satellite)
        for station_id in station_ids:
            access_sets[station_id].append(
                frozenset(per_station[station_id])
            )
    assert all(any(values) for values in access_sets.values())
    assert all(len(set(values)) > 1 for values in access_sets.values())

    later = reference_chain.transitions[1:]
    assert any(
        connection.connection_id.kind
        is OperationalConnectionKind.GROUND_STATION_ACCESS
        for transition in later
        for connection in transition.established_connections
    )
    assert any(
        identity.kind is OperationalConnectionKind.GROUND_STATION_ACCESS
        for transition in later
        for identity in transition.withdrawn_connection_ids
    )
    assert any(
        connection.connection_id.kind
        is OperationalConnectionKind.GROUND_STATION_ACCESS
        for transition in later
        for connection in transition.parameter_updated_connections
    )

    scenario_source = (EXAMPLE_ROOT / "scenario.yaml").read_text(
        encoding="utf-8"
    )
    assert "starlink_gateways.json" in scenario_source
    assert "s4_visualization_gateway_binding_12.json" in scenario_source
    assert "Reference Ground Station - Porto Alegre, Brazil" in scenario_source
    assert "Reference Ground Station - Marsala, Italy" in scenario_source
    assert "Reference Ground Station - Yamaguchi, Japan" in scenario_source
    assert "Starlink Gateway -" not in scenario_source
    assert "commercial operator's current" in scenario_source
    for path in ("emu.py", "simu.py", "main.py"):
        assert "skyseed_v1" not in (EXAMPLE_ROOT / path).read_text(
            encoding="utf-8"
        )


def test_mini_core_identities_and_results_repeat_deterministically(
    reference_chain: SimpleNamespace,
) -> None:
    repeated_emulator = build_mini_satellite_internet_emulator()
    repeated_simulator, repeated_producer = (
        create_mini_satellite_internet_producer(
            EXAMPLE_ROOT,
            emulator=repeated_emulator,
        )
    )
    repeated_plans, repeated_realizations, repeated_snapshot = (
        drain_mini_satellite_internet(
            repeated_simulator,
            repeated_producer,
        )
    )

    assert repeated_simulator.get_physical_world().semantic_fingerprint() == (
        reference_chain.physical_world.semantic_fingerprint()
    )
    assert repeated_plans == reference_chain.plans
    assert repeated_realizations == reference_chain.realizations
    assert repeated_snapshot["formal_frame_count"] == 64
    assert repeated_snapshot["plan_count"] == 32
    assert repeated_snapshot["realization_count"] == 32


def test_mini_sources_delegate_formal_state_to_the_common_producer() -> None:
    source = (EXAMPLE_ROOT / "simu.py").read_text(encoding="utf-8")
    for forbidden in (
        "argparse",
        "def main(",
        "__main__",
        "PhysicalFrame(",
        "PhysicalLinkObservation(",
        "PlanTransition(",
        "OperationalConnection(",
        "apply_plan_transition(",
        ".frame_at(",
        ".compute_observation(",
        "compile_bpf(",
    ):
        assert forbidden not in source
    for required in (
        "def create_mini_satellite_internet_simulator(",
        "def create_mini_satellite_internet_planner(",
        "def create_mini_satellite_internet_producer(",
        "create_simulator_static_input(",
        "Simulator(",
        ".create_physical_world(",
        ".create_planner(",
        "_IncrementalRealizationProducer(",
    ):
        assert required in source

    main_source = (EXAMPLE_ROOT / "main.py").read_text(encoding="utf-8")
    execution_source = (
        PROJECT_ROOT / "src/skyseed_v2/experiment/execution.py"
    ).read_text(encoding="utf-8")
    assert main_source.count("write_physical_world_tool(") == 1
    assert main_source.count("write_zone_tool(") == 1
    assert "write_physical_frame_tool(" not in main_source
    assert main_source.count("write_planner_tool(") == 1
    assert main_source.count("write_bpf_compiler_tool(") == 1
    assert main_source.count("write_canonical_traffic_workload(") == 1
    assert main_source.count("producer.produce_next_realization()") == 1
    assert main_source.count("producer.first_formal_input()") == 1
    assert main_source.index(
        "_write_input_snapshots(arguments.output, snapshots)"
    ) < main_source.index(
        "emulator = build_mini_satellite_internet_emulator(snapshot_scenario)"
    )
    for forbidden in (
        "AccumulatedDeadbandParameterPlanningModel",
        "DefaultCoordinationPolicy",
        "GatewayHopCountRoutePlanningModel",
        "GeometricPhysicalLinkModel",
        ".create_planner(",
        "Simulator(",
        "load_zone_layout_definition(",
        "create_simulator_static_input(",
    ):
        assert forbidden not in main_source
    assert execution_source.count("runner.prepare()") == 1
    assert execution_source.count("runner.run()") == 1
    assert execution_source.count("runner.cleanup()") == 1
    assert "_read_runner_artifact_manifest_snapshot(" in execution_source
    for forbidden in (
        "write_runner_tool",
        "write_traffic_replay_tool",
        "_IncrementalRealizationProducer",
    ):
        assert forbidden not in execution_source
    assert "\nfrom tools" not in execution_source
    assert "\nimport tools" not in execution_source

    parameters = signature(
        mini_simu.create_mini_satellite_internet_producer
    ).parameters
    assert tuple(parameters) == (
        "input_directory",
        "emulator",
        "evidence_path",
    )
    assert parameters["input_directory"].default is Parameter.empty
    assert parameters["emulator"].default is None
    assert parameters["evidence_path"].default is None
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


def test_generation_and_persisted_execution_loops_stay_separate() -> None:
    main_source = (EXAMPLE_ROOT / "main.py").read_text(encoding="utf-8")
    execution_source = (
        PROJECT_ROOT / "src/skyseed_v2/experiment/execution.py"
    ).read_text(encoding="utf-8")

    assert main_source.count("producer.produce_next_realization()") == 1
    assert main_source.count("_write_generated_run_script(") == 1
    assert "def _write_execution_scripts(" not in main_source
    assert main_source.index("_write_runner_artifact(") < main_source.index(
        "_write_execution_definition("
    )
    assert "_read_runner_artifact_manifest_snapshot(" in execution_source
    assert "future_realization_supplier" not in execution_source
    assert "produce_next_plan(" not in execution_source
    assert "produce_next_realization(" not in execution_source
    assert "compile_bpf(" not in execution_source
    assert "create_planner(" not in execution_source
    for mini_specific in (
        "examples.mini_satellite_internet",
        "north-america-east",
        "Marsala",
        "gs-marsala-reference",
        "2026, 1, 1",
        "dynamic-shell-orbit-004",
    ):
        assert mini_specific not in execution_source


def test_mini_simu_has_no_standalone_assembly_or_cli() -> None:
    result = run(
        (sys.executable, "simu.py"),
        cwd=EXAMPLE_ROOT,
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONWARNINGS": "ignore::SyntaxWarning"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""


def test_mini_main_supports_direct_file_execution() -> None:
    result = run(
        (sys.executable, "main.py", "--help"),
        cwd=EXAMPLE_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "--scenario" in result.stdout
    assert "--zones" in result.stdout
    assert "--output" in result.stdout
