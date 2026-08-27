from __future__ import annotations

from collections import Counter
from datetime import timedelta
from hashlib import sha256
import ast
import json
from math import asin, cos, radians, sin, sqrt
from pathlib import Path
import shutil

import pytest

from examples.s4_satellite_internet import main as s4_main
from examples.s4_satellite_internet.emu import (
    build_s4_satellite_internet_emulator,
)
from examples.s4_satellite_internet.simu import (
    BANDWIDTH_BPS_BY_KIND,
    GROUND_STATION_MAXIMUM_DISTANCE_M,
    GROUND_STATION_MINIMUM_ELEVATION_DEG,
    GATEWAY_CANDIDATE_COUNT,
    GATEWAY_CANDIDATE_MAX_DISTANCE_M,
    REFERENCE_EPOCH,
    MAXIMUM_GROUND_STATION_ACCESS_COUNT,
    MAXIMUM_ISL_DISTANCE_M,
    PACKET_LOSS_PPM_BY_KIND,
    PROPAGATION_SPEED_M_PER_S,
    S4_REAL_SLICE_COUNT,
    S4_SLICE_INTERVAL,
    S4_TIMELINE_END,
    ZONE_COVERAGE_MINIMUM_ELEVATION_DEG,
    create_s4_planner,
    create_s4_simulator,
    s4_planning_configuration,
    s4_slicing_configuration,
    _IdentityCoordinationPolicy,
)
from scripts import generate_zone_layout, refresh_s4_ground_stations
from skyseed_v2.configuration.emulator import load_static_emulator_config
from skyseed_v2.configuration.simulator import load_zone_layout_definition
from skyseed_v2.emulator.seedemu import get_skyseed_autonomous_systems
from skyseed_v2.emulator_to_simulator import create_simulator_static_input
from skyseed_v2.simulator import (
    AccumulatedDeadbandParameterPlanningModel,
    CircularOrbitPositionModel,
    ConnectionKind,
    ConnectionSlice,
    DependencyAwareForwardingPlanningModel,
    GatewayHopCountRouteModel,
    GeometricConnectionModel,
    Planner,
    RouteSlice,
    SampleBoundaryConnectionPlanningModel,
    TimeHorizonPlanningWindowPolicy,
    Simulator,
    Topology,
    TopologyDifference,
    TopologySlice,
)
import skyseed_v2.simulator.models.geometric_connection as geometric_connection
import skyseed_v2.simulator.models.gateway_hop_count_route as gateway_route


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_ROOT = PROJECT_ROOT / "examples/s4_satellite_internet"
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
    EXAMPLE_ROOT
    / "reference/fcc-sat-mod-20200417-00037-560km-97.6deg-6x58.json"
)
GROUND_STATION_EVIDENCE_FILE = (
    PROJECT_ROOT
    / "research/starlink/gen1/ground-stations/"
    "gateway-evidence-2026-08-25.json"
)


class _RecordingInspectionExecutor:
    max_workers = 8

    def __init__(self) -> None:
        self.submissions: list[tuple[str, Path]] = []

    def submit(self, task, _source, output):
        self.submissions.append((task, Path(output)))
        return {"task": task, "status": "BUILDING"}

    def poll(self):
        return self.records()

    def records(self):
        return tuple(
            {"task": task, "status": "BUILDING"}
            for task, _output in self.submissions
        )


@pytest.fixture(scope="module")
def s4_topology() -> Topology:
    emulator = build_s4_satellite_internet_emulator()
    return create_s4_simulator(emulator).get_topology()


@pytest.fixture(scope="module")
def s4_dynamic_progression() -> tuple[
    Simulator,
    TopologySlice,
    TopologySlice,
    TopologyDifference,
    TopologyDifference,
]:
    emulator = build_s4_satellite_internet_emulator()
    simulator = create_s4_simulator(emulator)
    slicer = simulator.get_slicer()
    first = slicer.create_next_slice()
    first_difference = slicer.create_difference(0, 1)
    second = slicer.create_next_slice()
    second_difference = slicer.create_difference(1, 2)
    return simulator, first, second, first_difference, second_difference


def test_s4_frozen_inputs_are_deterministic_and_v2_local() -> None:
    layout, rendered_zones = generate_zone_layout._canonical_content(
        latitude_bands=24,
        equatorial_cells=48,
        alignment="antimeridian-centered",
    )
    assert rendered_zones == (EXAMPLE_ROOT / "zones.yaml").read_text(
        encoding="utf-8"
    )
    assert tuple(
        sum(zone.zone_id.startswith(f"zone-b{index:02d}-") for zone in layout.zones)
        for index in range(24)
    ) == EXPECTED_LONGITUDE_COUNTS
    assert sum(EXPECTED_LONGITUDE_COUNTS) == 732
    frozen = EXAMPLE_ROOT / "ground_stations.yaml"
    assert sha256(frozen.read_bytes()).hexdigest() == (
        "d570e715d40b167da51dc265668457aaf1fd1d7ea1baf1fd2caa87189efffafd"
    )
    assert sha256(GROUND_STATION_EVIDENCE_FILE.read_bytes()).hexdigest() == (
        refresh_s4_ground_stations.REVIEWED_EVIDENCE_SHA256
    )
    assert len(
        refresh_s4_ground_stations.load_reviewed_sites(
            GROUND_STATION_EVIDENCE_FILE
        )
    ) == 355
    assert refresh_s4_ground_stations.render_ground_stations(
        GROUND_STATION_EVIDENCE_FILE
    ) == frozen.read_text(encoding="utf-8")

    for runtime_source in ("emu.py", "simu.py", "main.py"):
        source = (EXAMPLE_ROOT / runtime_source).read_text(encoding="utf-8")
        assert "skyseed_v1" not in source
        assert "research/" not in source


def test_s4_cli_uses_natural_stage_boundaries_and_one_formal_timeline() -> None:
    parser = s4_main._parser()
    default = s4_main._validated_arguments(parser, parser.parse_args([]))
    bounded = s4_main._validated_arguments(
        parser,
        parser.parse_args(
            ["--through", "compiler", "--slice-limit", "20"]
        ),
    )

    assert default.through == "topology"
    assert default.slice_limit == S4_REAL_SLICE_COUNT
    assert bounded.through == "compiler"
    assert bounded.slice_limit == 20
    for obsolete in ("--dynamic", "--planner", "--planner-profile"):
        with pytest.raises(SystemExit):
            parser.parse_args([obsolete])
    with pytest.raises(SystemExit):
        s4_main._validated_arguments(
            parser,
            parser.parse_args(["--slice-limit", "20"]),
        )
    with pytest.raises(SystemExit):
        s4_main._validated_arguments(
            parser,
            parser.parse_args(["--tool-workers", "1"]),
        )


def test_s4_default_main_generates_only_static_tools(tmp_path: Path) -> None:
    output = tmp_path / "s4"

    assert s4_main.main(["--output", str(output)]) == 0
    assert (output / "inspection/index.html").is_file()
    assert (output / "inspection/topology/index.html").is_file()
    assert (output / "inspection/zone/index.html").is_file()
    assert not (output / "inspection/slice").exists()
    assert not (output / "inspection/observation").exists()
    assert not (output / "inspection/planner").exists()
    assert not (output / "inspection/compiler").exists()
    assert not (output / "inspection/_execution").exists()


def test_s4_slicer_boundary_stops_before_planner(tmp_path: Path) -> None:
    simulator = create_s4_simulator(build_s4_satellite_internet_emulator())
    observer = simulator._enable_execution_observation(
        tmp_path / "slicer-observations.jsonl"
    )
    executor = _RecordingInspectionExecutor()

    result = s4_main._run_dynamic_progression(
        simulator,
        through="slicer",
        final_slice_id=1,
        observer=observer,
        executor=executor,
        inspection=tmp_path / "inspection",
    )

    assert len(tuple(simulator.get_timeline().iter_timestamps())) == (
        S4_REAL_SLICE_COUNT
    )
    assert simulator.get_slicer().get_retained_slice_ids() == (0, 1)
    assert result["plans"] == ()
    assert result["realizations"] == ()
    with pytest.raises(RuntimeError, match="Planner has not been created"):
        simulator.get_planner()
    assert [task for task, _output in executor.submissions] == ["slice"]


def test_s4_planner_boundary_stops_before_compiler(
    tmp_path: Path,
    monkeypatch,
) -> None:
    simulator = create_s4_simulator(build_s4_satellite_internet_emulator())
    observer = simulator._enable_execution_observation(
        tmp_path / "planner-observations.jsonl"
    )
    executor = _RecordingInspectionExecutor()

    def reject_compiler(*_args, **_kwargs):
        raise AssertionError("Planner boundary must not call Compiler")

    monkeypatch.setattr(s4_main, "compile_bpf", reject_compiler)
    result = s4_main._run_dynamic_progression(
        simulator,
        through="planner",
        final_slice_id=1,
        observer=observer,
        executor=executor,
        inspection=tmp_path / "inspection",
    )

    plans = result["plans"]
    assert isinstance(plans, tuple)
    assert tuple((plan.start_slice_id, plan.end_slice_id) for plan in plans) == (
        (0, 1),
    )
    assert result["realizations"] == ()
    assert len(simulator.get_planner().get_consumed_differences()) == 1
    assert [task for task, _output in executor.submissions] == [
        "slice",
        "planner",
    ]


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


def test_s4_scenario_expands_exact_accepted_constellation_and_ground_body(
    s4_topology: Topology,
) -> None:
    config = load_static_emulator_config(EXAMPLE_ROOT / "scenario.yaml")
    autonomous_system = config.skyseed_autonomous_systems[0]
    shell_input = autonomous_system.shells[0]
    generated = shell_input.generated_orbit_layout
    assert generated is not None
    assert shell_input.altitude_km == 560.0
    assert shell_input.inclination_deg == 97.6
    assert shell_input.epoch_utc.isoformat() == "2026-01-01T00:00:00+00:00"
    assert generated.orbit_count == 6
    assert generated.satellites_per_orbit == 58
    assert generated.max_simultaneous_ground_station_accesses == 2
    assert generated.right_ascension_distribution.start_deg == 63.7
    assert generated.right_ascension_distribution.step_deg == 60.0
    assert generated.in_orbit_phase_distribution.step_deg == pytest.approx(
        360.0 / 58
    )
    assert (
        generated.in_orbit_phase_distribution.alternating_orbit_offset_deg
        == 0.0
    )
    assert (
        generated.in_orbit_phase_distribution.orbit_phase_step_deg
        == pytest.approx(360.0 / 348)
    )
    assert len(autonomous_system.ground_stations) == 355
    assert all(
        station.operator is None
        for station in autonomous_system.ground_stations
    )
    assert all(
        station.max_simultaneous_satellite_accesses is None
        for station in autonomous_system.ground_stations
    )
    assert all(
        not station.ix_attachments
        for station in autonomous_system.ground_stations
    )

    shell = s4_topology.autonomous_systems[0].shells[0]
    assert [orbit.right_ascension_deg for orbit in shell.orbits] == (
        pytest.approx([63.7, 123.7, 183.7, 243.7, 303.7, 3.7])
    )
    assert all(len(orbit.satellites) == 58 for orbit in shell.orbits)
    assert all(
        satellite.max_simultaneous_ground_station_accesses == 2
        for orbit in shell.orbits
        for satellite in orbit.satellites
    )
    satellite_ids = [
        satellite.satellite_id
        for orbit in shell.orbits
        for satellite in orbit.satellites
    ]
    assert len(satellite_ids) == len(set(satellite_ids)) == 348
    assert satellite_ids[0] == "s4-shell-orbit-001-satellite-0001"
    assert satellite_ids[-1] == "s4-shell-orbit-006-satellite-0058"

    ground_station_count = sum(
        len(value.ground_stations) for value in s4_topology.autonomous_systems
    )
    assert ground_station_count == 355
    assert len(satellite_ids) + ground_station_count == 703
    assert all(
        station.operator is None
        for autonomous_system in s4_topology.autonomous_systems
        for station in autonomous_system.ground_stations
    )
    assert all(
        station.max_simultaneous_satellite_accesses is None
        for autonomous_system in s4_topology.autonomous_systems
        for station in autonomous_system.ground_stations
    )


def test_s4_satellite_gateway_capability_crosses_the_formal_fact_chain() -> None:
    emulator = build_s4_satellite_internet_emulator()
    autonomous_systems = get_skyseed_autonomous_systems(
        emulator.getLayer("Base")
    )
    emulator_satellites = tuple(
        satellite
        for autonomous_system in autonomous_systems
        for satellite in autonomous_system.satellites()
    )
    simulator_input = create_simulator_static_input(autonomous_systems)

    assert len(emulator_satellites) == 348
    assert all(
        satellite.max_simultaneous_ground_station_accesses == 2
        for satellite in emulator_satellites
    )
    assert all(
        satellite.max_simultaneous_ground_station_accesses == 2
        for satellite in simulator_input.list_satellites()
    )
    assert MAXIMUM_GROUND_STATION_ACCESS_COUNT == 8


def test_s4_planner_factory_uses_the_explicit_normal_configuration(
    s4_dynamic_progression,
) -> None:
    simulator = s4_dynamic_progression[0]
    differences = s4_dynamic_progression[3:]
    configuration = s4_planning_configuration(simulator)
    planner = create_s4_planner(simulator)
    plan = planner.get_plan()

    assert simulator.get_planner() is planner
    assert isinstance(
        planner._planning_window_policy, TimeHorizonPlanningWindowPolicy
    )
    assert isinstance(
        planner._connection_model,
        SampleBoundaryConnectionPlanningModel,
    )
    assert isinstance(
        planner._parameter_model, AccumulatedDeadbandParameterPlanningModel
    )
    assert isinstance(
        planner._forwarding_model,
        DependencyAwareForwardingPlanningModel,
    )
    assert isinstance(planner._coordination_policy, _IdentityCoordinationPolicy)
    assert "profile" not in configuration
    assert configuration["numeric_defaults_status"] == (
        "S4 scenario candidate; no generic Planner defaults selected"
    )
    assert configuration["planning_window"] == "bounded exact time prefix"
    assert configuration["planning_window_policy"] == (
        "TimeHorizonPlanningWindowPolicy"
    )
    assert configuration["planning_lookahead_seconds"] == 50
    assert configuration["publication_horizon_seconds"] == 20
    assert configuration["connection_model"] == (
        "SampleBoundaryConnectionPlanningModel"
    )
    assert configuration["parameter_model"] == (
        "AccumulatedDeadbandParameterPlanningModel"
    )
    assert configuration["forwarding_model"] == (
        "DependencyAwareForwardingPlanningModel"
    )
    assert configuration["coordination_policy"] == "_IdentityCoordinationPolicy"
    assert configuration["connection_suppression"] == "disabled"
    assert plan.start_slice_id == 0
    assert plan.end_slice_id == 2
    assert len(planner.get_consumed_differences()) == 2
    assert len(plan.connection_transitions) == sum(
        len(difference.added_connections)
        + len(difference.removed_connection_ids)
        for difference in differences
    )
    assert len(plan.forwarding_transitions) == sum(
        len(difference.route_patches) for difference in differences
    )
    assert len(plan.parameter_transitions) < sum(
        len(difference.connection_parameter_patches)
        for difference in differences
    )
    formal_times = {
        difference.current_timestamp for difference in differences
    }
    assert all(
        transition.nominal_time in formal_times
        for transitions in (
            plan.connection_transitions,
            plan.parameter_transitions,
            plan.forwarding_transitions,
        )
        for transition in transitions
    )
    assert all(
        decision.decision != "suppressed"
        and "HOLD" not in decision.reason
        for decision in planner.get_planning_decisions()
    )


def test_s4_compiler_progression_is_rolling_at_real_window_boundaries(
    tmp_path: Path,
    monkeypatch,
) -> None:
    simulator = create_s4_simulator(build_s4_satellite_internet_emulator())
    observer = simulator._enable_execution_observation(
        tmp_path / "observations.jsonl"
    )
    executor = _RecordingInspectionExecutor()
    events: list[str] = []
    compiled = []
    slicer_type = type(simulator.get_slicer())
    planner_type = Planner
    original_slice = slicer_type.create_next_slice
    original_create_planner = s4_main.create_s4_planner
    original_synchronize = planner_type.synchronize
    original_compile = s4_main.compile_bpf

    def create_slice(self):
        result = original_slice(self)
        events.append(f"S{result.slice_id}")
        return result

    def create_planner(value):
        result = original_create_planner(value)
        plan = result.get_plan()
        events.append(f"P{plan.start_slice_id}-{plan.end_slice_id}")
        return result

    def synchronize(self):
        result = original_synchronize(self)
        events.append(f"P{result.start_slice_id}-{result.end_slice_id}")
        return result

    def compile_plan(plan, *, slicer, previous=None):
        assert previous is (compiled[-1] if compiled else None)
        result = original_compile(plan, slicer=slicer, previous=previous)
        compiled.append(result)
        events.append(f"C{plan.start_slice_id}-{plan.end_slice_id}")
        return result

    monkeypatch.setattr(slicer_type, "create_next_slice", create_slice)
    monkeypatch.setattr(s4_main, "create_s4_planner", create_planner)
    monkeypatch.setattr(planner_type, "synchronize", synchronize)
    monkeypatch.setattr(s4_main, "compile_bpf", compile_plan)

    result = s4_main._run_dynamic_progression(
        simulator,
        through="compiler",
        final_slice_id=8,
        observer=observer,
        executor=executor,
        inspection=tmp_path / "inspection",
    )

    plans = result["plans"]
    assert isinstance(plans, tuple)
    assert tuple((plan.start_slice_id, plan.end_slice_id) for plan in plans) == (
        (0, 3),
        (3, 5),
        (5, 7),
        (7, 8),
    )
    assert events[:6] == ["S1", "S2", "S3", "S4", "S5", "S6"]
    assert events.index("P0-3") < events.index("C0-3") < events.index("S7")
    assert events.index("S8") < events.index("P3-5") < events.index("C3-5")
    assert len(compiled) == len(plans) == 4
    assert simulator.get_slicer().get_latest_slice_id() == 8
    assert len(simulator.get_planner().get_consumed_differences()) == 8
    assert [task for task, _output in executor.submissions] == [
        "slice",
        "planner",
    ]


def test_s4_normal_public_compiler_emits_detailed_plan_aligned_observation(
    tmp_path: Path,
) -> None:
    simulator = create_s4_simulator(build_s4_satellite_internet_emulator())
    observer = simulator._enable_execution_observation(
        tmp_path / "compiler-observations.jsonl"
    )

    result = s4_main._run_dynamic_progression(
        simulator,
        through="compiler",
        final_slice_id=5,
        observer=observer,
        executor=_RecordingInspectionExecutor(),
        inspection=tmp_path / "inspection",
    )

    plans = result["plans"]
    realizations = result["realizations"]
    compile_evidence = result["compile_evidence"]
    assert isinstance(plans, tuple)
    assert isinstance(realizations, tuple)
    assert isinstance(compile_evidence, tuple)
    assert len(plans) == len(realizations) == len(compile_evidence) == 2

    compiler_records = tuple(
        record
        for record in observer.snapshot()["records"]
        if record["component"] == "compiler"
    )
    expected_stages = {
        "input-validation",
        "slice-coverage",
        "temporal-source-indexing",
        "topology-preparation",
        "baseline-initialization",
        "plan-transition-validation",
        "resource-parameter-lowering",
        "forwarding-lowering",
        "zone-edge-lowering",
        "constraint-compilation",
        "end-state-validation",
        "realization-freeze",
        "total-compile",
    }
    assert {record["stage"] for record in compiler_records} == expected_stages
    assert "compile" not in {record["stage"] for record in compiler_records}
    assert {record["phase"] for record in compiler_records} == {"one-shot"}

    for compile_index, plan in enumerate(plans):
        unit = tuple(
            record
            for record in compiler_records
            if record["scope"]["compile_index"] == compile_index
        )
        assert {record["stage"] for record in unit} == expected_stages
        assert all(
            record["scope"]["publication_index"] == compile_index
            and record["scope"]["plan_index"] == compile_index
            and record["scope"]["start_slice_id"] == plan.start_slice_id
            and record["scope"]["end_slice_id"] == plan.end_slice_id
            for record in unit
        )
        rollup = next(record for record in unit if record["stage"] == "total-compile")
        assert rollup["duration_role"] == "rollup"
        assert all(
            record["duration_role"] == "stage"
            for record in unit
            if record is not rollup
        )
        assert compile_evidence[compile_index] == {
            "compile_index": compile_index,
            "compile_seconds": rollup["duration_seconds"],
        }


def test_s4_generated_orbits_match_all_fcc_published_anomalies(
    s4_topology: Topology,
) -> None:
    reference = json.loads(FCC_REFERENCE_FILE.read_text(encoding="utf-8"))
    shell = s4_topology.autonomous_systems[0].shells[0]
    orbits = sorted(shell.orbits, key=lambda orbit: orbit.orbit_id)

    assert reference["shell_selection"]["source_plane_ids"] == [
        5,
        6,
        7,
        8,
        9,
        10,
    ]
    assert [orbit.right_ascension_deg for orbit in orbits] == pytest.approx(
        [plane["raan_deg"] for plane in reference["planes"]]
    )
    compared = 0
    for orbit, plane in zip(orbits, reference["planes"], strict=True):
        generated_anomalies = [
            satellite.mean_anomaly_deg
            for satellite in sorted(
                orbit.satellites,
                key=lambda satellite: satellite.orbit_index,
            )
        ]
        published_anomalies = plane[
            "mean_anomaly_deg_by_source_satellite_id_1_to_58"
        ]
        assert [round(value, 1) for value in generated_anomalies] == (
            published_anomalies
        )
        compared += len(generated_anomalies)
    assert compared == 348


def test_s4_runtime_construction_survives_as_a_self_contained_input_copy(
    tmp_path: Path,
) -> None:
    copied = tmp_path / "s4-inputs"
    copied.mkdir()
    for name in ("scenario.yaml", "ground_stations.yaml", "zones.yaml"):
        shutil.copyfile(EXAMPLE_ROOT / name, copied / name)

    emulator = build_s4_satellite_internet_emulator(copied / "scenario.yaml")
    topology = create_s4_simulator(
        emulator,
        zone_layout_path=copied / "zones.yaml",
    ).get_topology()
    assert len(topology.get_zone_layout().zones) == 732
    assert sum(
        len(value.ground_stations) for value in topology.autonomous_systems
    ) == 355


def test_s4_zone_partition_has_exact_bands_counts_and_global_resolution(
    s4_topology: Topology,
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

    layout = s4_topology.get_zone_layout()
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


def test_s4_zone_corner_distance_matches_candidate_sanity_range(
    s4_topology: Topology,
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
    for zone in s4_topology.get_zone_layout().zones:
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


def test_s4_dynamic_timeline_and_existing_model_chain_are_explicit(
    s4_dynamic_progression,
) -> None:
    simulator, _, _, _, _ = s4_dynamic_progression
    timeline = simulator.get_timeline()
    slicer = simulator.get_slicer()

    assert timeline.start_time == REFERENCE_EPOCH
    assert timeline.end_time == S4_TIMELINE_END
    assert timeline.slice_interval == S4_SLICE_INTERVAL == timedelta(
        seconds=10
    )
    assert len(tuple(timeline.iter_timestamps())) == S4_REAL_SLICE_COUNT == 181
    assert isinstance(slicer.get_position_model(), CircularOrbitPositionModel)
    assert isinstance(slicer.get_connection_model(), GeometricConnectionModel)
    assert isinstance(slicer.get_route_model(), GatewayHopCountRouteModel)


def test_s4_spatial_pruning_matches_exhaustive_reference_at_three_times(
    s4_topology: Topology,
    monkeypatch,
) -> None:
    timestamps = tuple(
        REFERENCE_EPOCH + timedelta(seconds=offset)
        for offset in (0, 600, 1_200)
    )
    position_model = CircularOrbitPositionModel()
    positions = tuple(
        position_model.compute_positions(s4_topology, timestamp)
        for timestamp in timestamps
    )

    def connection_model() -> GeometricConnectionModel:
        return GeometricConnectionModel(
            maximum_isl_distance_m=MAXIMUM_ISL_DISTANCE_M,
            ground_station_minimum_elevation_deg=(
                GROUND_STATION_MINIMUM_ELEVATION_DEG
            ),
            maximum_ground_station_access_count=(
                MAXIMUM_GROUND_STATION_ACCESS_COUNT
            ),
            ground_station_maximum_distance_m=(
                GROUND_STATION_MAXIMUM_DISTANCE_M
            ),
            zone_coverage_minimum_elevation_deg=(
                ZONE_COVERAGE_MINIMUM_ELEVATION_DEG
            ),
            bandwidth_bps_by_kind=BANDWIDTH_BPS_BY_KIND,
            packet_loss_ppm_by_kind=PACKET_LOSS_PPM_BY_KIND,
            propagation_speed_m_per_s=PROPAGATION_SPEED_M_PER_S,
        )

    exhaustive_slices = []
    exhaustive_counts = []
    with monkeypatch.context() as exhaustive:
        exhaustive.setattr(
            geometric_connection._GroundStationSpatialIndex,
            "query",
            lambda self, _center, _radius: tuple(
                entry.ground_station for entry in self.entries
            ),
        )
        exhaustive.setattr(
            geometric_connection._ZoneSpatialIndex,
            "query",
            lambda self, _center, _radius: tuple(
                entry.prepared_zone for entry in self.entries
            ),
        )
        model = connection_model()
        previous = ConnectionSlice()
        for timestamp, position_values in zip(
            timestamps, positions, strict=True
        ):
            previous = model.compute_connection_slice(
                s4_topology, timestamp, position_values, previous
            )
            exhaustive_slices.append(previous)
            exhaustive_counts.append(model._last_candidate_check_counts)

    optimized_slices = []
    optimized_counts = []
    model = connection_model()
    previous = ConnectionSlice()
    for timestamp, position_values in zip(timestamps, positions, strict=True):
        previous = model.compute_connection_slice(
            s4_topology, timestamp, position_values, previous
        )
        optimized_slices.append(previous)
        optimized_counts.append(model._last_candidate_check_counts)

    assert optimized_slices == exhaustive_slices
    assert all(
        counts.ground_station_pairs_before
        == counts.ground_station_pairs_after
        == 348 * 355
        and counts.zone_pairs_before
        == counts.zone_pairs_after
        == 348 * 732
        for counts in exhaustive_counts
    )
    assert all(
        counts.ground_station_pairs_after
        < counts.ground_station_pairs_before
        and counts.zone_pairs_after < counts.zone_pairs_before
        for counts in optimized_counts
    )


def test_s4_demand_bounded_routes_match_full_graph_search_at_two_times(
    s4_dynamic_progression,
    monkeypatch,
) -> None:
    simulator, first, second, _, _ = s4_dynamic_progression
    topology = simulator.get_topology()
    original = gateway_route._eligible_shortest_next_hops

    def exhaustive(context, target, terminal, **_kwargs):
        return original(context, target, terminal)

    with monkeypatch.context() as full_search:
        full_search.setattr(
            gateway_route, "_eligible_shortest_next_hops", exhaustive
        )
        reference_model = GatewayHopCountRouteModel(
            gateway_candidate_count=GATEWAY_CANDIDATE_COUNT,
            gateway_candidate_max_distance_m=(
                GATEWAY_CANDIDATE_MAX_DISTANCE_M
            ),
        )
        previous = RouteSlice()
        for topology_slice in (first, second):
            previous = reference_model.compute_route_slice(
                topology,
                topology_slice.timestamp,
                topology_slice.connection_slice,
                previous,
            )
            assert topology_slice.route_slice == previous


def test_s4_first_dynamic_slices_publish_complete_formal_state(
    s4_dynamic_progression,
) -> None:
    simulator, first, second, _, _ = s4_dynamic_progression
    topology = simulator.get_topology()
    known_nodes = {
        node.node_id
        for autonomous_system in topology.autonomous_systems
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

    for index, topology_slice in enumerate((first, second)):
        assert topology_slice.slice_id == index + 1
        assert topology_slice.timestamp == REFERENCE_EPOCH + index * timedelta(
            seconds=10
        )
        assert set(topology_slice.positions) == known_nodes
        assert sum(
            node_id.entity_kind == "satellite"
            for node_id in topology_slice.positions
        ) == 348
        assert sum(
            node_id.entity_kind == "ground-station"
            for node_id in topology_slice.positions
        ) == 355
        assert topology_slice.connection_slice.connections
        assert topology_slice.connection_slice.zone_service_assignments
        assert topology_slice.route_slice.routes
        assert all(
            connection.connection_id.endpoint_a in known_nodes
            and connection.connection_id.endpoint_b in known_nodes
            for connection in topology_slice.connection_slice.connections
        )
        assert all(
            satellite_id in known_nodes
            for satellite_id in (
                topology_slice.connection_slice.zone_service_assignments.values()
            )
        )

    first_connections = first.connection_slice.connections
    assert sum(
        value.connection_id.kind is ConnectionKind.GROUND_STATION_ACCESS
        for value in first_connections
    ) == 311
    assert sum(
        value.connection_id.kind is ConnectionKind.INTER_SATELLITE
        for value in first_connections
    ) == 552
    # This is the canonical S4 reference-scenario snapshot at 25 degrees,
    # not the generic GeometricConnectionModel default.
    assert len(first.connection_slice.zone_service_assignments) == 422
    assert sum(len(value) for value in first.route_slice.routes.values()) < 50_000


def test_s4_slice_tool_context_projects_final_scenario_policy() -> None:
    context = s4_slicing_configuration()

    assert context["zone_service"] == {"minimum_elevation_deg": 25.0}
    assert context["ground_station_access"] == {
        "minimum_elevation_deg": 5.0,
        "maximum_slant_distance_km": 3_000.0,
        "ground_station_capacity": 8,
    }
    assert context["isl"] == {
        "same_orbit_policy": "fixed predecessor/successor ring",
        "cross_plane_policy": "RAAN-neighbour fixed cyclic pairing",
        "maximum_distance_km": 6_000.0,
        "earth_clearance": "exact Earth-clear LOS required",
    }
    assert context["routing"] == {
        "model": "GatewayHopCountRouteModel",
        "gateway_candidates_per_zone": 3,
        "metric": "minimum hop count on the published Connection graph",
    }


def test_s4_dynamic_difference_progression_is_formal_and_adjacent(
    s4_dynamic_progression,
) -> None:
    simulator, first, second, first_difference, second_difference = (
        s4_dynamic_progression
    )
    slicer = simulator.get_slicer()

    assert first_difference.previous_slice_id == 0
    assert first_difference.current_slice_id == first.slice_id == 1
    assert len(first_difference.added_connections) == len(
        first.connection_slice.connections
    )
    assert len(first_difference.route_patches) == sum(
        len(value) for value in first.route_slice.routes.values()
    )
    assert second_difference.previous_slice_id == first.slice_id
    assert second_difference.current_slice_id == second.slice_id == 2
    assert second_difference.current_timestamp == second.timestamp
    assert slicer.get_retained_slice_ids() == (0, 1, 2)


def test_s4_one_composition_has_an_unadvanced_slicer_by_default() -> None:
    simulator = create_s4_simulator(build_s4_satellite_internet_emulator())
    assert simulator.get_topology() is not None
    assert simulator.get_slicer().get_retained_slice_ids() == (0,)
    assert simulator.get_slicer().get_latest_slice_id() == 0


def test_s4_normal_entry_includes_compiler_and_stops_before_runner() -> None:
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
    assert "skyseed_v2.backend.bpf" in imported_modules
    assert not any("runner" in module for module in imported_modules)
    assert "tools._detached_inspection" in imported_modules
    assert not (EXAMPLE_ROOT / "qualification.py").exists()
    assert not (EXAMPLE_ROOT / "generate_inputs.py").exists()
    main_source = (EXAMPLE_ROOT / "main.py").read_text(encoding="utf-8")
    assert "_compile_bpf_observed" not in main_source
    assert "compile_bpf(" in main_source
    assert "qualification" not in main_source
    detached_source = (
        PROJECT_ROOT / "tools/_detached_inspection.py"
    ).read_text(encoding="utf-8")
    assert "tools.planner" in detached_source
