from __future__ import annotations

from collections import Counter
from datetime import timedelta
from hashlib import sha256
import ast
import json
from math import asin, cos, radians, sin, sqrt
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest
from seedemu.compiler import Docker

from examples.starlink_gen1_satellite_internet import main as starlink_main
from examples.starlink_gen1_satellite_internet.emu import (
    build_starlink_gen1_satellite_internet_emulator,
)
from examples.starlink_gen1_satellite_internet.simu import (
    BANDWIDTH_BPS_BY_KIND,
    GATEWAY_CANDIDATE_COUNT,
    GATEWAY_CANDIDATE_MAX_DISTANCE_M,
    MAXIMUM_GROUND_STATION_ACCESS_COUNT,
    PACKET_LOSS_PPM_BY_KIND,
    REFERENCE_EPOCH,
    STARLINK_GEN1_ACQUISITION_DURATION,
    STARLINK_GEN1_DELAY_RELATIVE_THRESHOLD,
    STARLINK_GEN1_FORMAL_BOUNDARY_COUNT,
    STARLINK_GEN1_OBSERVATION_INTERVAL,
    STARLINK_GEN1_PARAMETER_MAX_STALENESS,
    STARLINK_GEN1_PLAN_BATCH_FRAMES,
    STARLINK_GEN1_ISL_MINIMUM_SERVICE,
    STARLINK_GEN1_GS_MINIMUM_SERVICE,
    STARLINK_GEN1_TIMELINE_END,
    create_starlink_gen1_planner,
    create_starlink_gen1_simulator,
    starlink_gen1_physical_configuration,
    starlink_gen1_planning_configuration,
)
from scripts import generate_zone_layout, refresh_starlink_gen1_ground_stations
from skyseed_v2.backend.bpf import compile_bpf
from skyseed_v2.backend.bpf.compiler import _compile_bpf_observed
from skyseed_v2.configuration.emulator import load_static_emulator_config
from skyseed_v2.configuration.simulator import load_zone_layout_definition
from skyseed_v2.emulator.entities import GroundStation, GroundStationLocation
from skyseed_v2.emulator.seedemu import get_skyseed_autonomous_systems
from skyseed_v2.emulator_to_simulator import create_simulator_static_input
from skyseed_v2.backend.bpf._runner_artifact import (
    _RunnerArtifactWriter,
    _open_runner_artifact,
)
from skyseed_v2.shared._execution_observation import _ExecutionObserver
from skyseed_v2.simulator import (
    AccumulatedDeadbandParameterPlanningModel,
    CircularOrbitPositionModel,
    DefaultCoordinationPolicy,
    GatewayHopCountRoutePlanningModel,
    GeometricPhysicalLinkModel,
    GroundStationRouteDemandTarget,
    HighestElevationZoneServicePlanningModel,
    OperationalConnectionKind,
    PhysicalFrame,
    PhysicalLinkObservation,
    PhysicalWorld,
    PlanTransition,
    CoverageFirstGroundStationAccessPlanningModel,
    RetainFirstInterSatelliteConnectionPlanningModel,
    Simulator,
    FrameBatchPlanningWindowPolicy,
    ZoneRouteDemandTarget,
    ZoneServiceAssignment,
    ZoneServiceTransition,
    apply_plan,
    create_plan,
    plan_transition_fingerprint,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_ROOT = PROJECT_ROOT / "examples/starlink_gen1_satellite_internet"
EXPECTED_LONGITUDE_COUNTS = (
    3,
    9,
    15,
    21,
    27,
    32,
    36,
    40,
    43,
    45,
    47,
    48,
    48,
    47,
    45,
    43,
    40,
    36,
    32,
    27,
    21,
    15,
    9,
    3,
)
FCC_REFERENCE_FILE = (
    PROJECT_ROOT
    / "research/starlink/gen1/shells/"
    "fcc-sat-mod-20200417-00037-gen1.json"
)
GROUND_STATION_EVIDENCE_FILE = (
    PROJECT_ROOT
    / "research/starlink/gen1/ground-stations/"
    "gateway-evidence-2026-08-25.json"
)
CANONICAL_GROUND_STATION_ALLOCATION_REGRESSION = (
    (
        240,
        6_692,
        698,
        "4d7ae7ad29902c40dbe56207b5ac396a2d7d2520702b75c52fa7acc2aee237cd",
    ),
    (
        595,
        6_799,
        700,
        "404801e890242dde3a19e44c0e54e752319b91fdf004c75dd837d2f8bff7db78",
    ),
    (
        600,
        6_794,
        695,
        "eb8b0a649bf5efd12c0fdb3c95a9a2c48effd7fe6bc0965e868cc527682b75d9",
    ),
)


@pytest.fixture(scope="module")
def starlink_simulator() -> Simulator:
    simulator = create_starlink_gen1_simulator(
        build_starlink_gen1_satellite_internet_emulator()
    )
    yield simulator
    try:
        simulator.get_planner().shutdown(wait=True)
    except RuntimeError:
        pass


@pytest.fixture(scope="module")
def starlink_physical_world(starlink_simulator: Simulator) -> PhysicalWorld:
    return starlink_simulator.get_physical_world()


@pytest.fixture(scope="module")
def starlink_planner(starlink_simulator: Simulator):
    return create_starlink_gen1_planner(starlink_simulator)


@pytest.fixture(scope="module")
def first_physical_input(
    starlink_simulator: Simulator,
    starlink_planner,
) -> tuple[PhysicalFrame, PhysicalLinkObservation]:
    physical_world = starlink_simulator.get_physical_world()
    frame = physical_world.frame_at(REFERENCE_EPOCH)
    observation = starlink_simulator.get_physical_link_model().compute_observation(
        physical_world,
        frame,
        starlink_planner.create_physical_link_request(),
    )
    return frame, observation


@pytest.fixture(scope="module")
def starlink_plan_realization(starlink_simulator: Simulator):
    physical_world = starlink_simulator.get_physical_world()
    timeline = starlink_simulator.get_timeline()
    initial = PlanTransition(
        REFERENCE_EPOCH,
        zone_service_transitions=tuple(
            ZoneServiceTransition(zone.zone_id, None, None)
            for zone in physical_world.get_zone_layout().zones
        ),
    )
    unchanged = PlanTransition(
        REFERENCE_EPOCH + STARLINK_GEN1_OBSERVATION_INTERVAL
    )
    plan = create_plan(
        None,
        previous_plan_id=None,
        physical_world_fingerprint=physical_world.semantic_fingerprint(),
        observation_interval=timeline.observation_interval,
        start_timestamp=timeline.start_time,
        transitions=(initial, unchanged),
    )
    return plan, compile_bpf(plan, physical_world, None)


def test_starlink_gen1_runtime_has_no_rolling_availability_policy() -> None:
    source = (EXAMPLE_ROOT / "main.py").read_text(encoding="utf-8")
    assert "_RUNNER_PREPARATION_LOOKAHEAD" not in source
    assert "_RUNNER_PREPARATION_BOUNDARY_DEPTH" not in source
    assert "future_realization_supplier" not in source


def test_starlink_gen1_frozen_inputs_are_deterministic_and_v2_local() -> None:
    layout, rendered_zones = generate_zone_layout._canonical_content(
        latitude_bands=24,
        equatorial_cells=48,
        alignment="antimeridian-centered",
    )
    assert rendered_zones == (EXAMPLE_ROOT / "zones.yaml").read_text(
        encoding="utf-8"
    )
    assert tuple(
        sum(
            zone.zone_id.startswith(f"zone-b{index:02d}-")
            for zone in layout.zones
        )
        for index in range(24)
    ) == EXPECTED_LONGITUDE_COUNTS
    assert sum(EXPECTED_LONGITUDE_COUNTS) == 732
    frozen = EXAMPLE_ROOT / "ground_stations.yaml"
    assert sha256(frozen.read_bytes()).hexdigest() == (
        "18f3531ec1bd535cd87f3f8673717cc6e6d5a9f0866456f8304076fd6415d0e8"
    )
    assert sha256(GROUND_STATION_EVIDENCE_FILE.read_bytes()).hexdigest() == (
        refresh_starlink_gen1_ground_stations.REVIEWED_EVIDENCE_SHA256
    )
    assert len(
        refresh_starlink_gen1_ground_stations.load_reviewed_sites(
            GROUND_STATION_EVIDENCE_FILE
        )
    ) == 355
    assert refresh_starlink_gen1_ground_stations.render_ground_stations(
        GROUND_STATION_EVIDENCE_FILE
    ) == frozen.read_text(encoding="utf-8")

    for runtime_source in ("emu.py", "simu.py", "main.py"):
        source = (EXAMPLE_ROOT / runtime_source).read_text(encoding="utf-8")
        assert "skyseed_v1" not in source
        assert "research/" not in source


def test_starlink_gen1_input_snapshot_contains_every_formal_input(
    tmp_path: Path,
) -> None:
    starlink_main._write_input_snapshots(tmp_path)

    inputs = tmp_path / "inputs"
    assert {path.name for path in inputs.iterdir()} == {
        "scenario.yaml",
        "shells.yaml",
        "ground_stations.yaml",
        "zones.yaml",
    }
    for name in (
        "scenario.yaml",
        "shells.yaml",
        "ground_stations.yaml",
        "zones.yaml",
    ):
        assert (inputs / name).read_bytes() == (EXAMPLE_ROOT / name).read_bytes()


def test_starlink_gen1_cli_uses_only_natural_round_b_boundaries() -> None:
    parser = starlink_main._parser()
    default = parser.parse_args([])
    bounded = parser.parse_args(["--through", "planner", "--output", "/tmp/s4"])

    assert default.through == "compiler"
    assert default.output == starlink_main.DEFAULT_OUTPUT
    assert bounded.through == "planner"
    assert bounded.output == Path("/tmp/s4")
    assert STARLINK_GEN1_FORMAL_BOUNDARY_COUNT == 121
    for obsolete in (
        "--dynamic",
        "--planner",
        "--planner-profile",
        "--boundary-limit",
        "--tool-workers",
    ):
        with pytest.raises(SystemExit):
            parser.parse_args([obsolete])
    with pytest.raises(SystemExit):
        parser.parse_args(["--through", "not-a-stage"])


def test_starlink_gen1_regeneration_guard_allows_only_inactive_output(
    tmp_path: Path,
    monkeypatch,
) -> None:
    compute_only = tmp_path / "compute-only"
    (compute_only / "inspection").mkdir(parents=True)
    monkeypatch.setattr(
        starlink_main.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail(
            "compute-only output has no deployment identity to query"
        ),
    )
    starlink_main._guard_existing_output(compute_only)

    unverifiable = tmp_path / "unverifiable"
    (unverifiable / "runtime").mkdir(parents=True)
    (unverifiable / "runtime/docker-compose.yml").write_text(
        "services: {}\n", encoding="utf-8"
    )
    with pytest.raises(RuntimeError, match="no verifiable Compose identity"):
        starlink_main._guard_existing_output(unverifiable)

    live = tmp_path / "live"
    (live / "runtime").mkdir(parents=True)
    (live / "runtime/compose-project-name").write_text(
        "skyseed-v2-s4-live\n", encoding="utf-8"
    )
    sentinel = live / "preserve-me"
    sentinel.write_text("preserved", encoding="utf-8")
    monkeypatch.setattr(
        starlink_main.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="container-id\n",
            stderr="",
        ),
    )
    with pytest.raises(RuntimeError, match="still has containers"):
        starlink_main._guard_existing_output(live)
    assert sentinel.read_text(encoding="utf-8") == "preserved"


def test_starlink_gen1_runtime_docker_preserves_interface_less_router_identity(
    monkeypatch,
) -> None:
    station = GroundStation(
        ground_station_id="gateway-a",
        asn=65040,
        location=GroundStationLocation(0.0, 0.0),
        station_type="gateway",
        operator=None,
    )
    compiler = starlink_main._InterfaceLessRuntimeDocker()
    same_display_station = GroundStation(
        ground_station_id="gateway-b",
        asn=65040,
        location=GroundStationLocation(1.0, 1.0),
        station_type="gateway",
        operator=None,
    )
    station.setDisplayName("Repeated gateway display name")
    same_display_station.setDisplayName("Repeated gateway display name")

    assert station.getInterfaces() == []
    assert compiler._getComposeNodeName(station) == "as65040r-gateway-a"
    assert compiler._getComposeNodeName(same_display_station) == (
        "as65040r-gateway-b"
    )

    monkeypatch.setattr(
        Docker,
        "_compileNode",
        lambda _self, _node: (
            "    rnode_65040_gateway-a:\n"
            "        networks:\n"
            "        labels:\n"
        ),
    )
    service = compiler._compileNode(station)
    assert "        network_mode: none\n" in service
    assert "        networks:\n" not in service


def test_starlink_gen1_build_plan_groups_only_identical_contexts(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    (runtime / "dummies").mkdir(parents=True)
    (runtime / "dummies/base-image").write_text("FROM base\n")
    for name, start in (
        ("satellite-a", "satellite"),
        ("satellite-b", "satellite"),
        ("gateway-a", "gateway"),
    ):
        directory = runtime / name
        directory.mkdir()
        (directory / "Dockerfile").write_text("FROM base\n")
        (directory / "start").write_text(start)

    from yaml import safe_dump, safe_load
    services = {name: {"build": f"./{name}", "container_name": name,
                       "environment": {"NODE": name}, "volumes": [f"./{name}/start:/start"]}
                for name in ("satellite-a", "satellite-b", "gateway-a")}
    original = {name: dict(value) for name, value in services.items()}
    (runtime / "docker-compose.yml").write_text(safe_dump({"services": services}))
    plan = starlink_main._write_compose_build_plan(runtime).read_text().splitlines()
    deployed = safe_load((runtime / "docker-compose.yml").read_text())["services"]
    assert "# contexts=3" in plan
    assert "# identical-content-groups=2" in plan
    assert "prerequisite base-image" in plan
    assert len([row for row in plan if row.startswith("build ")]) == 2
    assert deployed["satellite-a"]["image"] == deployed["satellite-b"]["image"]
    assert deployed["gateway-a"]["image"] != deployed["satellite-a"]["image"]
    assert "build" not in deployed["satellite-b"]
    for name in original:
        for key in ("container_name", "environment", "volumes"):
            assert deployed[name][key] == original[name][key]


def test_starlink_gen1_generated_run_path_owns_runtime_tools_and_final_index(
    tmp_path: Path,
) -> None:
    starlink_main._write_generated_run_script(
        tmp_path,
        python_executable="python3",
        compiler_tool_directory="compiler",
        tool_package_parent=starlink_main.PROJECT_ROOT,
    )

    run_script = (tmp_path / "run.sh").read_text(encoding="utf-8")
    assert "-m skyseed_v2.experiment.execution" in run_script
    assert "docker compose" not in run_script
    assert "-m tools.runner" in run_script
    assert "-m tools.traffic" in run_script
    assert "write_observation_tool" in run_script
    assert 'PYTHONPATH="$TOOL_PYTHONPATH"' in run_script
    assert "inspection/compiler/bpf-compiler-inspection.json" in run_script
    assert "inputs/shells.yaml" in run_script
    assert "inspection/runtime-runs/$INVOCATION_ID" in run_script
    assert '--invocation-directory "$INVOCATION_DIR"' in run_script
    assert '--events "$INVOCATION_DIR/runner/runner-events.jsonl"' in run_script
    assert '--telemetry "$INVOCATION_DIR/traffic/traffic-telemetry.jsonl"' in run_script
    assert "finalize_runtime_aggregate_index" in run_script
    assert "exit \"$execution_status\"" in run_script
    assert "exit \"$runner_tool_status\"" not in run_script
    assert run_script.index("-m tools.traffic") < run_script.index(
        "finalize_runtime_aggregate_index"
    )
    syntax = starlink_main.subprocess.run(
        ("bash", "-n", str(tmp_path / "run.sh")),
        check=False,
        capture_output=True,
        text=True,
    )
    assert syntax.returncode == 0, syntax.stderr
    launch_script = (tmp_path / "launch.sh").read_text(encoding="utf-8")
    assert '"$SCRIPT_DIR/start.sh"' in launch_script
    assert 'exec "$SCRIPT_DIR/run.sh"' in launch_script
    assert "docker compose" not in launch_script


def test_starlink_gen1_physical_world_boundary_writes_only_static_tools(
    tmp_path: Path,
    monkeypatch,
) -> None:
    starlink_simulator = create_starlink_gen1_simulator(
        build_starlink_gen1_satellite_internet_emulator()
    )
    output = tmp_path / "physical-world"
    frame_timestamps = []
    original_frame_at = PhysicalWorld.frame_at

    def recording_frame_at(physical_world, timestamp):
        frame_timestamps.append(timestamp)
        return original_frame_at(physical_world, timestamp)

    monkeypatch.setattr(PhysicalWorld, "frame_at", recording_frame_at)
    monkeypatch.setattr(
        starlink_main,
        "build_starlink_gen1_satellite_internet_emulator",
        lambda *_args: object(),
    )
    monkeypatch.setattr(
        starlink_main,
        "create_starlink_gen1_simulator",
        lambda _emulator, **_kwargs: starlink_simulator,
    )
    # Selection composition supplies only request/dependency facts. No semantic
    # progression or compilation is permitted through this cutoff.
    from skyseed_v2.simulator import Planner
    monkeypatch.setattr(Planner, "observe", lambda *args: pytest.fail("physical cutoff cannot plan"))


    assert (
        starlink_main.main(
            ["--through", "physical-world", "--output", str(output)]
        )
        == 0
    )
    formal = tuple(starlink_simulator.get_timeline().iter_timestamps())
    assert all(timestamp in frame_timestamps for timestamp in formal)
    status = json.loads((output / "generation-status.json").read_text())
    assert status["producer"]["formal_frame_count"] == 121
    assert status["producer"]["plan_count"] == 0
    assert status["producer"]["physical_prediction"]["prediction_frames"] > 121
    assert (output / "inspection/index.html").is_file()
    assert (output / "inspection/physical-world/index.html").is_file()
    assert (output / "inspection/zone/index.html").is_file()
    assert not (output / "inspection/physical-frame").exists()
    assert (output / "domain-history/domain-history-manifest.json").is_file()
    assert not (output / "runtime").exists()
    assert not (output / "inspection/planner").exists()
    assert not (output / "inspection/bpf-compiler").exists()


def test_zone_authoring_check_is_exact_and_read_only(tmp_path: Path) -> None:
    existing = tmp_path / "zones.yaml"
    shutil.copyfile(EXAMPLE_ROOT / "zones.yaml", existing)
    before = existing.read_bytes()

    assert (
        generate_zone_layout.main(
            [
                "--latitude-bands",
                "24",
                "--equatorial-cells",
                "48",
                "--alignment",
                "antimeridian-centered",
                "--check",
                str(existing),
            ]
        )
        == 0
    )
    assert existing.read_bytes() == before
    prime = generate_zone_layout.build_global_zone_layout(
        latitude_bands=6,
        equatorial_cells=12,
        alignment="prime-meridian-centered",
    )
    assert prime.allow_overlap is False
    assert prime.zones


def test_starlink_gen1_scenario_expands_exact_constellation_and_ground_body(
    starlink_physical_world: PhysicalWorld,
) -> None:
    config = load_static_emulator_config(EXAMPLE_ROOT / "scenario.yaml")
    autonomous_system = config.skyseed_autonomous_systems[0]
    shell_input = autonomous_system.shells[0]
    assert shell_input.shell_id == "S3"
    assert shell_input.generated_orbit_layout is None
    assert shell_input.altitude_km == 570.0
    assert shell_input.inclination_deg == 70.0
    assert shell_input.epoch_utc.isoformat() == "2015-01-01T00:00:00+00:00"
    assert len(shell_input.explicit_orbits) == 36
    assert all(len(orbit.satellites) == 20 for orbit in shell_input.explicit_orbits)
    assert len(autonomous_system.ground_stations) == 355
    assert all(
        station.operator is None for station in autonomous_system.ground_stations
    )
    assert all(
        station.max_simultaneous_satellite_accesses is None
        for station in autonomous_system.ground_stations
    )
    assert all(
        not station.ix_attachments
        for station in autonomous_system.ground_stations
    )

    shell = starlink_physical_world.autonomous_systems[0].shells[0]
    assert [orbit.right_ascension_deg for orbit in shell.orbits] == pytest.approx(
        [float(value) for value in range(0, 360, 10)]
    )
    assert all(len(orbit.satellites) == 20 for orbit in shell.orbits)
    assert all(
        satellite.max_simultaneous_ground_station_accesses == 2
        for orbit in shell.orbits
        for satellite in orbit.satellites
    )
    assert all(
        satellite.inter_satellite_link_capability.maximum_simultaneous_links == 4
        for orbit in shell.orbits
        for satellite in orbit.satellites
    )
    satellite_ids = [
        satellite.satellite_id
        for orbit in shell.orbits
        for satellite in orbit.satellites
    ]
    assert len(satellite_ids) == len(set(satellite_ids)) == 720
    assert satellite_ids[0] == "s3-orbit-001-satellite-0001"
    assert satellite_ids[-1] == "s3-orbit-036-satellite-0020"

    ground_station_count = sum(
        len(value.ground_stations)
        for value in starlink_physical_world.autonomous_systems
    )
    assert ground_station_count == 355
    assert len(satellite_ids) + ground_station_count == 1_075


def test_starlink_gen1_capabilities_cross_the_formal_fact_chain() -> None:
    emulator = build_starlink_gen1_satellite_internet_emulator()
    autonomous_systems = get_skyseed_autonomous_systems(
        emulator.getLayer("Base")
    )
    emulator_satellites = tuple(
        satellite
        for autonomous_system in autonomous_systems
        for satellite in autonomous_system.satellites()
    )
    simulator_input = create_simulator_static_input(autonomous_systems)

    assert len(emulator_satellites) == 720
    assert {
        satellite.max_simultaneous_ground_station_accesses
        for satellite in emulator_satellites
    } == {2}
    assert {
        satellite.maximum_simultaneous_inter_satellite_links
        for satellite in emulator_satellites
    } == {4}
    assert {
        satellite.max_simultaneous_ground_station_accesses
        for satellite in simulator_input.list_satellites()
    } == {2}
    assert {
        satellite.maximum_simultaneous_inter_satellite_links
        for satellite in simulator_input.list_satellites()
    } == {4}
    assert MAXIMUM_GROUND_STATION_ACCESS_COUNT == 4


def test_starlink_gen1_planner_factory_uses_explicit_normal_configuration(
    starlink_simulator: Simulator,
    starlink_planner,
) -> None:
    planner = starlink_planner
    configuration = starlink_gen1_planning_configuration(starlink_simulator)
    physical = starlink_gen1_physical_configuration()

    assert starlink_simulator.get_planner() is planner
    assert isinstance(
        planner._planning_window_policy, FrameBatchPlanningWindowPolicy
    )
    assert isinstance(
        planner._inter_satellite_model,
        RetainFirstInterSatelliteConnectionPlanningModel,
    )
    assert isinstance(
        planner._ground_station_model,
        CoverageFirstGroundStationAccessPlanningModel,
    )
    assert isinstance(
        planner._zone_service_model,
        HighestElevationZoneServicePlanningModel,
    )
    assert isinstance(
        planner._parameter_model,
        AccumulatedDeadbandParameterPlanningModel,
    )
    assert isinstance(planner._route_model, GatewayHopCountRoutePlanningModel)
    assert isinstance(planner._coordination_policy, DefaultCoordinationPolicy)
    assert configuration["observation_interval_seconds"] == 5
    assert configuration["formal_boundary_count"] == 121
    assert configuration["frames_per_batch"] == 32
    assert configuration["acquisition_duration_seconds"] == 5
    assert configuration["isl_policy"] == (
        "retain-first 2 same-plane + 2 cross-plane / K1"
    )
    assert configuration["ground_station_policy"] == (
        "GS coverage / distinct satellites / connections / continuity / quality"
    )
    assert configuration["zone_policy"] == (
        "highest elevation with explicit unserved state"
    )
    assert configuration["route_policy"] == (
        "gateway demand-bounded hop count"
    )
    assert set(configuration["frontiers"].values()) == {None}
    window = planner._planning_window_policy
    assert window.frames_per_batch == STARLINK_GEN1_PLAN_BATCH_FRAMES
    assert planner.required_prehistory == STARLINK_GEN1_ACQUISITION_DURATION
    isl = planner._inter_satellite_model._configuration
    assert (
        isl.desired_same_plane_links,
        isl.desired_cross_plane_links,
        isl.maximum_cross_plane_separation,
        isl.acquisition_duration,
    ) == (2, 2, 1, timedelta(seconds=5))
    assert planner._ground_station_model._default_ground_station_limit == 4
    assert planner._route_model._gateway_candidate_count == 3
    assert planner._route_model._gateway_candidate_max_distance_m is None
    for kind in OperationalConnectionKind:
        parameter = planner._parameter_model._configuration[kind]
        assert parameter.bandwidth_bps == BANDWIDTH_BPS_BY_KIND[kind]
        assert parameter.packet_loss_ppm == PACKET_LOSS_PPM_BY_KIND[kind]
        assert parameter.delay_relative_threshold == STARLINK_GEN1_DELAY_RELATIVE_THRESHOLD
        assert parameter.max_staleness == STARLINK_GEN1_PARAMETER_MAX_STALENESS
    assert physical["physical_input"]["owner"] == "PhysicalWorld"
    assert physical["physical_input"]["frame"] == "PhysicalFrame"
    request = planner.create_physical_link_request()
    assert len(request.inter_satellite_pairs) == 15_120


@pytest.mark.parametrize(
    ("shell_id", "source_element_id", "source_plane_ids", "satellite_count"),
    (
        ("S1", 2, tuple(range(1, 73)), 1_584),
        ("S2", 1, tuple(range(1, 73)), 1_584),
        ("S3", 4, tuple(range(1, 37)), 720),
        ("S4", 3, tuple(range(5, 11)), 348),
        ("S5", 3, tuple(range(1, 5)), 172),
    ),
)
def test_starlink_gen1_shell_selection_matches_all_fcc_published_orbits(
    tmp_path: Path,
    shell_id: str,
    source_element_id: int,
    source_plane_ids: tuple[int, ...],
    satellite_count: int,
) -> None:
    scenario = (EXAMPLE_ROOT / "scenario.yaml").read_text(encoding="utf-8")
    (tmp_path / "scenario.yaml").write_text(
        scenario.replace(
            "    selected_shell: S3", f"    selected_shell: {shell_id}"
        ),
        encoding="utf-8",
    )
    shutil.copyfile(
        EXAMPLE_ROOT / "ground_stations.yaml",
        tmp_path / "ground_stations.yaml",
    )
    shutil.copyfile(EXAMPLE_ROOT / "shells.yaml", tmp_path / "shells.yaml")
    config = load_static_emulator_config(tmp_path / "scenario.yaml")
    selected = config.skyseed_autonomous_systems[0].shells
    assert len(selected) == 1
    shell = selected[0]

    reference = json.loads(FCC_REFERENCE_FILE.read_text(encoding="utf-8"))
    element = next(
        value
        for value in reference["shells"]
        if value["source_element_id"] == source_element_id
    )
    planes = tuple(
        plane
        for plane in element["planes"]
        if plane["source_plane_id"] in source_plane_ids
    )
    assert shell.shell_id == shell_id
    assert shell.altitude_km == element["apogee_km"]
    assert shell.inclination_deg == element["inclination_deg"]
    assert tuple(plane["source_plane_id"] for plane in planes) == source_plane_ids
    assert [orbit.right_ascension_deg for orbit in shell.explicit_orbits] == (
        pytest.approx([plane["raan_deg"] for plane in planes])
    )
    compared = 0
    for orbit, plane in zip(shell.explicit_orbits, planes, strict=True):
        configured = [
            satellite.mean_anomaly_deg
            for satellite in sorted(
                orbit.satellites,
                key=lambda satellite: satellite.index_in_orbit,
            )
        ]
        published = [
            value % 360.0
            for value in plane["mean_anomaly_deg_by_source_satellite_id"]
        ]
        assert configured == pytest.approx(published)
        assert {
            satellite.max_simultaneous_ground_station_accesses
            for satellite in orbit.satellites
        } == {2}
        compared += len(configured)
    assert compared == satellite_count
    if shell_id in {"S1", "S3"}:
        assert {
            satellite.maximum_simultaneous_inter_satellite_links
            for orbit in shell.explicit_orbits
            for satellite in orbit.satellites
        } == {4}


def test_starlink_gen1_runtime_construction_uses_self_contained_input_copy(
    tmp_path: Path,
) -> None:
    copied = tmp_path / "starlink-gen1-inputs"
    copied.mkdir()
    for name in (
        "scenario.yaml",
        "shells.yaml",
        "ground_stations.yaml",
        "zones.yaml",
    ):
        shutil.copyfile(EXAMPLE_ROOT / name, copied / name)

    emulator = build_starlink_gen1_satellite_internet_emulator(
        copied / "scenario.yaml"
    )
    physical_world = create_starlink_gen1_simulator(
        emulator,
        zone_layout_path=copied / "zones.yaml",
    ).get_physical_world()
    assert len(physical_world.get_zone_layout().zones) == 732
    assert sum(
        len(value.ground_stations)
        for value in physical_world.autonomous_systems
    ) == 355
    assert physical_world.semantic_fingerprint()


def test_starlink_gen1_zone_partition_has_exact_bands_and_global_resolution(
    starlink_physical_world: PhysicalWorld,
) -> None:
    definition = load_zone_layout_definition(EXAMPLE_ROOT / "zones.yaml")
    assert definition.allow_overlap is False
    assert len(definition.zones) == 732

    counts = Counter(
        int(zone.zone_id.split("-")[1][1:]) for zone in definition.zones
    )
    assert tuple(counts[index] for index in range(24)) == (
        EXPECTED_LONGITUDE_COUNTS
    )
    for zone in definition.zones:
        _, band_text, cell_text = zone.zone_id.split("-")
        band_index = int(band_text[1:])
        cell_index = int(cell_text[1:])
        geometry = zone.geometry
        assert zone.zone_id == f"zone-b{band_index:02d}-c{cell_index:03d}"
        assert geometry.south_latitude_deg == -90.0 + 7.5 * band_index
        assert geometry.north_latitude_deg == -82.5 + 7.5 * band_index

    layout = starlink_physical_world.get_zone_layout()
    assert len(layout.zones) == 732
    assert len(layout.find_zones(-90.0, 0.0)) == 3
    assert len(layout.find_zones(90.0, 0.0)) == 3
    assert layout.resolve_zone(0.0, -180.0).zone_id == (
        layout.resolve_zone(0.0, 180.0).zone_id
    )
    assert layout.resolve_zone(0.0, -179.0).zone_id == (
        layout.resolve_zone(0.0, 179.0).zone_id
    )
    assert sum(
        zone.geometry.west_longitude_deg
        > zone.geometry.east_longitude_deg
        for zone in layout.zones
    ) == 24

    for latitude_index in range(73):
        latitude = -90.0 + 2.5 * latitude_index
        for longitude in range(-180, 180, 5):
            assert layout.resolve_zone(latitude, float(longitude)) is not None


def test_starlink_gen1_zone_corner_distance_matches_candidate_sanity_range(
    starlink_physical_world: PhysicalWorld,
) -> None:
    earth_radius_km = 6_378.137

    def central_angle(
        first_latitude: float,
        first_longitude: float,
        second_latitude: float,
        second_longitude: float,
    ) -> float:
        first_latitude = radians(first_latitude)
        first_longitude = radians(first_longitude)
        second_latitude = radians(second_latitude)
        second_longitude = radians(second_longitude)
        haversine = sin((second_latitude - first_latitude) / 2.0) ** 2 + (
            cos(first_latitude)
            * cos(second_latitude)
            * sin((second_longitude - first_longitude) / 2.0) ** 2
        )
        return 2.0 * asin(sqrt(min(1.0, max(0.0, haversine))))

    maximum_corner_distances = []
    for zone in starlink_physical_world.get_zone_layout().zones:
        geometry = zone.geometry
        center_latitude, center_longitude = geometry.representative_center
        maximum_corner_distances.append(
            max(
                earth_radius_km
                * central_angle(
                    center_latitude,
                    center_longitude,
                    latitude,
                    longitude,
                )
                for latitude in (
                    geometry.south_latitude_deg,
                    geometry.north_latitude_deg,
                )
                for longitude in (
                    geometry.west_longitude_deg,
                    geometry.east_longitude_deg,
                )
            )
        )

    assert min(maximum_corner_distances) == pytest.approx(590.15, abs=0.02)
    assert max(maximum_corner_distances) == pytest.approx(722.52, abs=0.02)


def test_starlink_gen1_timeline_and_physical_model_chain_are_explicit(
    starlink_simulator: Simulator,
) -> None:
    timeline = starlink_simulator.get_timeline()

    assert timeline.start_time == REFERENCE_EPOCH
    assert timeline.end_time == STARLINK_GEN1_TIMELINE_END
    assert (
        timeline.observation_interval
        == STARLINK_GEN1_OBSERVATION_INTERVAL
        == timedelta(seconds=5)
    )
    assert len(tuple(timeline.iter_timestamps())) == (
        STARLINK_GEN1_FORMAL_BOUNDARY_COUNT
    ) == 121
    assert isinstance(
        starlink_simulator.get_physical_world().get_position_model(),
        CircularOrbitPositionModel,
    )
    assert isinstance(
        starlink_simulator.get_physical_link_model(),
        GeometricPhysicalLinkModel,
    )


def test_starlink_gen1_spatial_pruning_preserves_exact_physical_work_counts(
    first_physical_input: tuple[PhysicalFrame, PhysicalLinkObservation],
) -> None:
    _, observation = first_physical_input
    counts = observation.work_counts

    assert counts.inter_satellite_pairs_requested == 15_120
    assert 0 < counts.inter_satellite_pairs_feasible <= counts.inter_satellite_pairs_evaluated < 13_507
    assert counts.ground_station_pairs_requested == 720 * 355
    assert counts.ground_station_pairs_evaluated == 12_316
    assert counts.ground_station_pairs_feasible == 6_769
    assert counts.zone_service_pairs_requested == 720 * 732
    assert counts.zone_service_pairs_evaluated == 3_341
    assert counts.zone_service_pairs_feasible == 3_167
    assert counts.ground_station_pairs_evaluated < (
        counts.ground_station_pairs_requested
    )
    assert counts.zone_service_pairs_evaluated < counts.zone_service_pairs_requested
    assert len(observation.inter_satellite_candidates) == (
        counts.inter_satellite_pairs_feasible
    )
    assert len(observation.ground_station_access_candidates) == (
        counts.ground_station_pairs_feasible
    )
    assert len(observation.zone_service_candidates) == (
        counts.zone_service_pairs_feasible
    )


def test_starlink_gen1_route_demands_are_bounded_and_deterministic(
    starlink_physical_world: PhysicalWorld,
) -> None:
    physical_world = starlink_physical_world
    satellite = (
        physical_world.autonomous_systems[0]
        .shells[0]
        .orbits[0]
        .satellites[0]
    )
    zone = physical_world.get_zone_layout().zones[0]
    assignment = ZoneServiceAssignment(zone.zone_id, satellite.node_id)
    model = GatewayHopCountRoutePlanningModel(
        gateway_candidate_count=GATEWAY_CANDIDATE_COUNT,
        gateway_candidate_max_distance_m=GATEWAY_CANDIDATE_MAX_DISTANCE_M,
    )
    try:
        first = model.create_route_demands(physical_world, (assignment,))
        second = model.create_route_demands(physical_world, (assignment,))
    finally:
        model.shutdown(wait=True)

    assert first == second
    assert len(first) == 2 * GATEWAY_CANDIDATE_COUNT == 6
    assert sum(
        isinstance(demand.target, GroundStationRouteDemandTarget)
        for demand in first
    ) == 3
    assert sum(
        isinstance(demand.target, ZoneRouteDemandTarget) for demand in first
    ) == 3
    assert all(
        demand.source == satellite.node_id
        for demand in first
        if isinstance(demand.target, GroundStationRouteDemandTarget)
    )
    assert all(
        demand.target.zone_id == zone.zone_id
        for demand in first
        if isinstance(demand.target, ZoneRouteDemandTarget)
    )
    assert len(first) < 720 * 355


def test_starlink_gen1_first_physical_input_publishes_complete_sparse_facts(
    starlink_physical_world: PhysicalWorld,
    first_physical_input: tuple[PhysicalFrame, PhysicalLinkObservation],
) -> None:
    frame, observation = first_physical_input
    known_nodes = {
        node.node_id
        for autonomous_system in starlink_physical_world.autonomous_systems
        for node in (
            *autonomous_system.ground_stations,
            *(
                satellite
                for shell in autonomous_system.shells
                for orbit in shell.orbits
                for satellite in orbit.satellites
            ),
        )
    }
    zone_ids = {
        zone.zone_id for zone in starlink_physical_world.get_zone_layout().zones
    }

    assert frame.timestamp == observation.timestamp == REFERENCE_EPOCH
    assert set(frame.positions) == known_nodes
    assert len(frame.positions) == 1_075
    assert sum(node.entity_kind == "satellite" for node in frame.positions) == 720
    assert sum(
        node.entity_kind == "ground-station" for node in frame.positions
    ) == 355
    assert observation.inter_satellite_candidates
    assert observation.ground_station_access_candidates
    assert observation.zone_service_candidates
    assert all(
        candidate.endpoint_a in known_nodes
        and candidate.endpoint_b in known_nodes
        and candidate.endpoint_a.entity_kind == "satellite"
        and candidate.endpoint_b.entity_kind == "satellite"
        for candidate in observation.inter_satellite_candidates
    )
    assert all(
        candidate.ground_station_id in known_nodes
        and candidate.satellite_id in known_nodes
        for candidate in observation.ground_station_access_candidates
    )
    assert all(
        candidate.zone_id in zone_ids and candidate.satellite_id in known_nodes
        for candidate in observation.zone_service_candidates
    )


def test_starlink_gen1_ground_station_allocation_preserves_canonical_identity(
    starlink_simulator: Simulator,
    starlink_planner,
) -> None:
    physical_world = starlink_simulator.get_physical_world()
    physical_link_model = starlink_simulator.get_physical_link_model()
    allocation_model = CoverageFirstGroundStationAccessPlanningModel(
        default_ground_station_limit=MAXIMUM_GROUND_STATION_ACCESS_COUNT
    )
    assert physical_world.semantic_fingerprint() == (
        "e5af32b6aa3300f0ac356d264bdc019c8156ca4f6e94b040d0bccb26062e9308"
    )

    # The old edge-maximization hashes are historical policy evidence. Under
    # the new policy compare coverage with the exhaustive-capable reference
    # maximum-cardinality matching, then verify deterministic continuity.
    from skyseed_v2.simulator._ground_station_allocation import _allocate_ground_station_accesses, _GroundAccessAllocationPreference, _GroundAccessChoice
    for offset, expected_candidates, _, _ in CANONICAL_GROUND_STATION_ALLOCATION_REGRESSION:
        frame = physical_world.frame_at(REFERENCE_EPOCH + timedelta(seconds=offset))
        observation = physical_link_model.compute_observation(physical_world, frame, starlink_planner.create_physical_link_request())
        candidates = observation.ground_station_access_candidates
        selected = allocation_model.select_ground_station_accesses(physical_world, candidates)
        stations = sorted({c.ground_station_id for c in candidates}, key=lambda n: n.value)
        satellites = {c.satellite_id for c in candidates}
        preferences = tuple(_GroundAccessAllocationPreference(station, 1, tuple(
            _GroundAccessChoice(c.elevation_deg, c.satellite_id) for c in candidates if c.ground_station_id == station
        )) for station in stations)
        capacities = {n: physical_world.get_satellite(n).max_simultaneous_ground_station_accesses for n in satellites}
        reference = _allocate_ground_station_accesses(preferences, capacities)
        assert len(candidates) == expected_candidates
        assert len({c.ground_station_id for c in selected}) == sum(bool(v) for v in reference.values())
        retained = frozenset((c.ground_station_id, c.satellite_id) for c in selected)
        assert allocation_model.select_ground_station_accesses(physical_world, tuple(reversed(candidates)), retained) == selected


def test_starlink_gen1_composition_is_explicitly_unadvanced_by_default() -> None:
    simulator = create_starlink_gen1_simulator(
        build_starlink_gen1_satellite_internet_emulator()
    )

    physical_world = simulator.get_physical_world()
    assert physical_world is not None
    assert simulator.get_timeline().start_time == REFERENCE_EPOCH
    with pytest.raises(RuntimeError, match="Planner has not been created"):
        simulator.get_planner()
    with pytest.raises(RuntimeError, match="Planner has not been created"):
        simulator.get_physical_link_model()
    assert physical_world.frame_at(REFERENCE_EPOCH).timestamp == REFERENCE_EPOCH


def test_starlink_gen1_entry_packages_common_producer_and_runner_artifact() -> None:
    imported_modules = set()
    for name in ("main.py", "simu.py"):
        tree = ast.parse((EXAMPLE_ROOT / name).read_text(encoding="utf-8"))
        imported_modules.update(
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        )
        imported_modules.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
    assert "skyseed_v2.experiment._realization_producer" in imported_modules
    assert "skyseed_v2.backend.bpf._runner_artifact" in imported_modules
    assert "skyseed_v2.experiment.execution" in imported_modules
    assert "skyseed_v2.backend.bpf" not in imported_modules
    assert "tools._detached_inspection" in imported_modules
    assert not (EXAMPLE_ROOT / "qualification.py").exists()
    assert not (EXAMPLE_ROOT / "generate_inputs.py").exists()
    main_source = (EXAMPLE_ROOT / "main.py").read_text(encoding="utf-8")
    assert "_IncrementalRealizationProducer" in main_source
    assert "produce_next_plan()" in main_source
    assert "produce_next_realization(delivery_end=" in main_source
    assert "first_formal_input()" in main_source
    assert "compile_bpf(" not in main_source
    assert "_RunnerArtifactWriter" in main_source
    assert "_write_execution_definition" in main_source
    assert "_write_generated_run_script" in main_source
    assert "SKYSEED_RUNNER_ACTIVATION_LIMIT" not in main_source
    assert "qualification" not in main_source
    detached_source = (
        PROJECT_ROOT / "tools/_detached_inspection.py"
    ).read_text(encoding="utf-8")
    assert "tools.planner" in detached_source


class _FocusedMainSimulator:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.observation_path: Path | None = None
        self.timeline = SimpleNamespace(
            start_time=REFERENCE_EPOCH,
            end_time=STARLINK_GEN1_TIMELINE_END,
            observation_interval=STARLINK_GEN1_OBSERVATION_INTERVAL,
        )

    def get_timeline(self):
        return self.timeline

    def _enable_execution_observation(self, path, *, metadata):
        assert metadata["progression"] == (
            "physical-world-planner-plan-compiler"
        )
        self.observation_path = Path(path)
        self.events.append("observation-enabled")
        return object()

    def _write_execution_observations(self):
        assert self.observation_path is not None
        self.observation_path.parent.mkdir(parents=True, exist_ok=True)
        self.observation_path.write_text("{}\n", encoding="utf-8")
        self.events.append("observations-persisted")
        return self.observation_path


def _focused_snapshot(*, realization_count: int) -> dict[str, object]:
    return {
        "formal_frame_count": 121,
        "transition_count": 121,
        "plan_count": 4,
        "realization_count": realization_count,
        "domain_history_sealed": True,
        "formal_observation_sink_seconds": 0.0,
        "domain_evidence_persistence_seconds": 0.0,
    }


def test_starlink_gen1_dynamic_tools_follow_sealed_domain_and_exact_input(
    tmp_path: Path,
    monkeypatch,
) -> None:
    output = tmp_path / "planner"
    events: list[str] = []
    simulator = _FocusedMainSimulator(events)
    first_frame = object()
    first_observation = object()

    monkeypatch.setattr(
        starlink_main,
        "build_starlink_gen1_satellite_internet_emulator",
        lambda *_args: object(),
    )
    monkeypatch.setattr(
        starlink_main,
        "create_starlink_gen1_simulator",
        lambda _emulator, **_kwargs: simulator,
    )
    monkeypatch.setattr(
        starlink_main,
        "create_starlink_gen1_planner",
        lambda _simulator: events.append("planner-created"),
    )

    def run_planner(candidate, destination, **_kwargs):
        assert candidate is simulator
        assert destination == output
        history = destination / "domain-history"
        history.mkdir()
        (history / "domain-history-manifest.json").write_text(
            '{"terminal":true}\n', encoding="utf-8"
        )
        events.append("domain-history-sealed")
        return _focused_snapshot(realization_count=0), first_frame, first_observation

    def run_static(
        candidate,
        inspection,
        *,
        physical_frame=None,
        physical_link_observation=None,
        **_kwargs,
    ):
        assert candidate is simulator
        assert inspection == output / "inspection"
        assert physical_frame is first_frame
        assert physical_link_observation is first_observation
        assert (output / "domain-history/domain-history-manifest.json").is_file()
        events.append("exact-input-tools")
        return {"physical-world": {"status": "PASS", "counts": {}}}

    def run_dynamic(destination, *, through, **_kwargs):
        assert destination == output
        assert through == "planner"
        assert events[-1] == "exact-input-tools"
        assert json.loads((destination / "generation-status.json").read_text())["status"] == "MATERIALS_READY"
        events.append("dynamic-tools")
        return ({"planner": {"status": "PASS"}}, ())

    monkeypatch.setattr(starlink_main, "_run_planner_stage", run_planner)
    monkeypatch.setattr(starlink_main, "_run_static_tools", run_static)
    monkeypatch.setattr(starlink_main, "_run_dynamic_tools", run_dynamic)
    monkeypatch.setattr(
        starlink_main,
        "write_aggregate_index",
        lambda *_args, **_kwargs: events.append("aggregate-index"),
    )

    assert (
        starlink_main.main(
            ["--through", "planner", "--output", str(output)]
        )
        == 0
    )
    assert events == [
        "planner-created",
        "observation-enabled",
        "domain-history-sealed",
        "observations-persisted",
        "exact-input-tools",
        "dynamic-tools",
        "aggregate-index",
    ]


def test_starlink_gen1_compiler_handoff_survives_static_tool_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    output = tmp_path / "compiler"
    events: list[str] = []
    simulator = _FocusedMainSimulator(events)
    first_frame = object()
    first_observation = object()

    monkeypatch.setattr(
        starlink_main,
        "build_starlink_gen1_satellite_internet_emulator",
        lambda *_args: object(),
    )
    monkeypatch.setattr(
        starlink_main,
        "create_starlink_gen1_simulator",
        lambda _emulator, **_kwargs: simulator,
    )
    monkeypatch.setattr(
        starlink_main,
        "create_starlink_gen1_planner",
        lambda _simulator: events.append("planner-created"),
    )

    def run_compiler(candidate, destination, **_kwargs):
        assert candidate is simulator
        staging = destination / ".runner-artifact"
        staging.mkdir()
        manifest = staging / "manifest.json"
        manifest.write_text("{}\n", encoding="utf-8")
        events.append("compiler-domain-complete")
        return (
            manifest,
            _focused_snapshot(realization_count=4),
            first_frame,
            first_observation,
        )

    def write_runtime(
        destination, *, emulator, simulator, staged_manifest, **_kwargs
    ):
        assert staged_manifest == destination / ".runner-artifact/manifest.json"
        manifest = destination / "runtime/runner-artifact/manifest.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text('{"terminal":true}\n', encoding="utf-8")
        events.append("runtime-handoff-ready")
        return manifest

    def fail_static(
        candidate,
        inspection,
        *,
        physical_frame=None,
        physical_link_observation=None,
        **_kwargs,
    ):
        assert candidate is simulator
        assert physical_frame is first_frame
        assert physical_link_observation is first_observation
        assert (output / "runtime/runner-artifact/manifest.json").is_file()
        events.append("static-tool-failed")
        return {
            "physical-world": {
                "status": "FAILED",
                "message": "focused Tool input failure",
            }
        }

    monkeypatch.setattr(starlink_main, "_run_compiler_stage", run_compiler)
    monkeypatch.setattr(starlink_main, "_write_runtime_package", write_runtime)
    monkeypatch.setattr(starlink_main, "_run_static_tools", fail_static)
    monkeypatch.setattr(
        starlink_main,
        "_run_dynamic_tools",
        lambda *_args, **_kwargs: (
            {"planner": {"status": "PASS"}},
            (),
        ),
    )
    monkeypatch.setattr(starlink_main, "write_aggregate_index", lambda *_args: None)

    assert (
        starlink_main.main(
            ["--through", "compiler", "--output", str(output)]
        )
        == 0
    )
    assert events.index("compiler-domain-complete") < events.index(
        "runtime-handoff-ready"
    ) < events.index("static-tool-failed")
    assert (output / "runtime/runner-artifact/manifest.json").is_file()


def test_starlink_gen1_planner_boundary_never_pulls_compiler(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[str] = []
    first_frame = object()
    first_observation = object()

    class FocusedProducer:
        def __init__(
            self, *_args, evidence_path, formal_observation_sink=None
        ):
            assert evidence_path == tmp_path / "domain-history"
            assert callable(formal_observation_sink)
            self.index = 0

        def produce_next_plan(self):
            calls.append("plan")
            if self.index == 4:
                return None
            self.index += 1
            return object()

        def produce_next_realization(self):
            pytest.fail("Planner boundary must not pull Compiler output")

        def snapshot(self):
            return _focused_snapshot(realization_count=0)

        def first_formal_input(self):
            return first_frame, first_observation

        def close(self):
            calls.append("close")

    class FocusedEvidenceWriter:
        def __init__(self, *_args):
            pass

        def record(self, _value):
            pass

        def finalize(self):
            return tmp_path / "inspection-evidence/manifest.json"

        def close(self):
            pass

    simulator = SimpleNamespace(
        get_timeline=lambda: _FocusedMainSimulator([]).get_timeline(),
        get_physical_world=lambda: object(),
        get_physical_link_model=lambda: object(),
        get_planner=lambda: object(),
    )
    monkeypatch.setattr(
        starlink_main,
        "_IncrementalRealizationProducer",
        FocusedProducer,
    )
    monkeypatch.setattr(
        starlink_main,
        "_PhysicalLinkInspectionWriter",
        FocusedEvidenceWriter,
    )

    snapshot, frame, observation = starlink_main._run_planner_stage(
        simulator, tmp_path
    )

    assert calls == ["plan"] * 5 + ["close"]
    assert snapshot["plan_count"] == 4
    assert snapshot["realization_count"] == 0
    assert frame is first_frame
    assert observation is first_observation


def test_starlink_gen1_compiler_stage_drains_before_artifact_seal(
    tmp_path: Path,
    monkeypatch,
) -> None:
    emitted = tuple(object() for _ in range(4))
    appended: list[object] = []
    traffic_appended: list[tuple[object, object]] = []
    first_frame = object()
    first_observation = object()

    class FocusedProducer:
        def __init__(
            self, *_args, evidence_path, formal_observation_sink=None
        ):
            assert evidence_path == tmp_path / "domain-history"
            assert callable(formal_observation_sink)
            self.index = 0

        def produce_next_realization(self, *, delivery_end=None):
            if self.index == len(emitted):
                self.index += 1
                return None
            value = emitted[self.index]
            self.index += 1
            return value

        def snapshot(self):
            assert self.index == 5
            return _focused_snapshot(realization_count=4)

        def first_formal_input(self):
            return first_frame, first_observation

        @property
        def latest_plan(self):
            return ("plan", self.index - 1)

        def close(self):
            appended.append("producer-closed")

    class FocusedWriter:
        def __init__(self, directory, *, execution_observer=None, final=True):
            assert directory == tmp_path / ".runner-artifact"
            assert execution_observer is None

        def append(self, realization):
            appended.append(realization)

        def close(self):
            pass

        def finalize(self):
            appended.append("handoff-finalized")
            path = tmp_path / ".runner-artifact/manifest.json"
            path.parent.mkdir(exist_ok=True)
            path.write_text(json.dumps({"timeline": {"boundary_count": 121}}))
            return path

    class FocusedEvidenceWriter:
        def __init__(self, *_args):
            pass

        def record(self, _value):
            pass

        def finalize(self):
            return tmp_path / "inspection-evidence/manifest.json"

        def close(self):
            pass

    class FocusedTrafficBuilder:
        def __init__(self, *, physical_world):
            assert physical_world is not None

        def append(self, plan, realization):
            traffic_appended.append((plan, realization))

        def snapshot(self):
            return {"realization_count": len(traffic_appended)}

        def finalize(self):
            return object()

    simulator = SimpleNamespace(
        get_timeline=lambda: _FocusedMainSimulator([]).get_timeline(),
        get_physical_world=lambda: object(),
        get_physical_link_model=lambda: object(),
        get_planner=lambda: SimpleNamespace(
            _get_execution_observer=lambda: None
        ),
    )
    monkeypatch.setattr(
        starlink_main,
        "_IncrementalRealizationProducer",
        FocusedProducer,
    )
    monkeypatch.setattr(
        starlink_main,
        "_RunnerArtifactWriter",
        FocusedWriter,
    )
    monkeypatch.setattr(
        starlink_main,
        "_PhysicalLinkInspectionWriter",
        FocusedEvidenceWriter,
    )
    monkeypatch.setattr(
        starlink_main,
        "IncrementalTrafficProbePlanBuilder",
        FocusedTrafficBuilder,
    )
    monkeypatch.setattr(
        starlink_main,
        "write_traffic_probe_plan",
        lambda _plan, path: path,
    )

    manifest, snapshot, frame, observation = starlink_main._run_compiler_stage(
        simulator, tmp_path
    )

    assert appended[:4] == list(emitted)
    assert traffic_appended == [
        (("plan", index), realization)
        for index, realization in enumerate(emitted)
    ]
    assert appended[4:] == ["producer-closed", "handoff-finalized"]
    assert manifest == tmp_path / ".runner-artifact/manifest.json"
    assert snapshot["transition_count"] == 121
    assert snapshot["plan_count"] == snapshot["realization_count"] == 4
    assert frame is first_frame
    assert observation is first_observation


def test_orchestration_timing_distinguishes_spans_from_aggregates(
    tmp_path: Path,
) -> None:
    records = []
    starlink_main._append_phase(
        records,
        "interleaved-writer-total",
        "persistence",
        3.0,
        additive=True,
        interval_kind="aggregate",
    )
    starlink_main._append_phase(
        records,
        "measured-stage",
        "semantic-core",
        2.0,
        additive=False,
        started_monotonic=101.0,
        ended_monotonic=103.0,
    )
    starlink_main._write_orchestration_timing(
        tmp_path,
        records,
        total_wall_seconds=5.0,
        run_origin_monotonic=100.0,
    )

    artifact = json.loads(
        (tmp_path / "orchestration-timing.json").read_text(encoding="utf-8")
    )
    aggregate, span = artifact["records"]
    assert artifact["format_version"] == 2
    assert aggregate["interval_kind"] == "aggregate"
    assert aggregate["run_relative_start_seconds"] == (
        aggregate["run_relative_end_seconds"]
    )
    assert span["interval_kind"] == "span"
    assert span["run_relative_start_seconds"] == 1.0
    assert span["run_relative_end_seconds"] == 3.0


def test_starlink_gen1_public_compiler_emits_plan_aligned_observation(
    starlink_simulator: Simulator,
    starlink_plan_realization,
) -> None:
    plan, expected = starlink_plan_realization
    observer = _ExecutionObserver()

    actual = _compile_bpf_observed(
        plan,
        starlink_simulator.get_physical_world(),
        None,
        execution_observer=observer,
        compile_index=7,
    )

    assert actual == expected
    records = observer.snapshot()["records"]
    assert tuple(record["stage"] for record in records) == (
        "plan-lineage",
        "event-and-world-indexing",
        "transition-lowering",
        "constraint-folding-and-materialization",
        "backend-state-validation-and-freeze",
        "semantic-to-bpf-lowering",
        "compile-total",
    )
    assert all(record["component"] == "compiler" for record in records)
    assert all(record["phase"] == "one-shot" for record in records)
    assert all(record["scope"]["compile_index"] == 7 for record in records)
    assert all(record["scope"]["plan_id"] == plan.plan_id for record in records)
    assert records[-1]["duration_role"] == "rollup"


def test_starlink_gen1_precomputed_artifact_is_packed_and_complete(
    tmp_path: Path,
    starlink_simulator: Simulator,
    starlink_plan_realization,
) -> None:
    plan, realization = starlink_plan_realization
    writer = _RunnerArtifactWriter(tmp_path / "realizations")
    writer.append(realization)
    manifest_path = writer.finalize()
    artifact = _open_runner_artifact(manifest_path)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["encoding"] == "typed-packed-runtime-tables-v3"
    assert manifest["terminal"] is True
    assert manifest["realization_count"] == 1
    assert manifest["lineage"][0]["source_plan_id"] == plan.plan_id
    assert manifest["binary"]["boundary_count"] == 2
    assert artifact.boundary_count == 2
    assert artifact.snapshot()["rich_compiled_realizations_reconstructed"] is False
    artifact.close()


def test_starlink_gen1_plan_and_compiler_boundaries_are_formal_and_adjacent(
    starlink_simulator: Simulator,
    starlink_plan_realization,
) -> None:
    plan, realization = starlink_plan_realization
    expected_timestamps = (
        REFERENCE_EPOCH,
        REFERENCE_EPOCH + STARLINK_GEN1_OBSERVATION_INTERVAL,
    )

    assert tuple(transition.timestamp for transition in plan.transitions) == (
        expected_timestamps
    )
    assert plan.start_timestamp == expected_timestamps[0]
    assert plan.end_timestamp == expected_timestamps[-1]
    assert plan.observation_interval == STARLINK_GEN1_OBSERVATION_INTERVAL
    assert realization.source_plan_id == plan.plan_id
    assert realization.activation_timestamps == expected_timestamps
    assert len(realization.compiled_activation_boundaries) == 2
    assert (
        realization.compiled_activation_boundaries[-1].transition_fingerprint
        == plan_transition_fingerprint(PlanTransition(expected_timestamps[-1]))
    )
    assert len(realization.output_planned_state.zone_service_assignments) == 732
    assert all(
        not assignment.is_served
        for assignment in realization.output_planned_state.zone_service_assignments
    )
    assert realization.output_planned_state == apply_plan(
        None,
        plan,
        physical_world_fingerprint=(
            starlink_simulator.get_physical_world().semantic_fingerprint()
        ),
    )


@pytest.mark.parametrize("seconds, boundaries, plans", [(5, 121, 4), (1, 601, 19)])
def test_starlink_matrix_cadence_retains_seconds_policies(tmp_path, seconds, boundaries, plans):
    starlink_main._write_input_snapshots(tmp_path, selected_shell="S1")
    emulator = build_starlink_gen1_satellite_internet_emulator(tmp_path / "inputs/scenario.yaml")
    simulator = create_starlink_gen1_simulator(emulator, observation_interval=timedelta(seconds=seconds))
    timeline = simulator.get_timeline()
    assert len(tuple(timeline.iter_timestamps())) == boundaries
    assert (boundaries + STARLINK_GEN1_PLAN_BATCH_FRAMES - 1) // STARLINK_GEN1_PLAN_BATCH_FRAMES == plans
    assert timeline.end_time - timeline.start_time == timedelta(seconds=600)
    assert sum(len(orbit.satellites) for autonomous_system in simulator.get_physical_world().autonomous_systems for shell in autonomous_system.shells for orbit in shell.orbits) == 1584
    assert all(satellite.inter_satellite_link_capability.maximum_simultaneous_links == 4
               for autonomous_system in simulator.get_physical_world().autonomous_systems
               for shell in autonomous_system.shells for orbit in shell.orbits
               for satellite in orbit.satellites)
    descriptors = starlink_main.starlink_gen1_model_descriptors(observation_interval=timeline.observation_interval)
    assert descriptors["planning_window"]["configuration"]["observation_interval_seconds"] == seconds
    assert STARLINK_GEN1_ACQUISITION_DURATION == timedelta(seconds=5)
    assert STARLINK_GEN1_ISL_MINIMUM_SERVICE == timedelta(seconds=180)
    assert STARLINK_GEN1_GS_MINIMUM_SERVICE == timedelta(seconds=60)
    assert STARLINK_GEN1_PARAMETER_MAX_STALENESS == timedelta(seconds=120)
    from skyseed_v2.experiment.traffic.probe_plan import IncrementalTrafficProbePlanBuilder
    builder = IncrementalTrafficProbePlanBuilder()
    assert builder._persistent_interval == 1.0
    assert builder._activation_interval == 0.5
