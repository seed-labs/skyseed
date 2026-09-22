from __future__ import annotations
from tests._support import MODEL_TIMESTAMP, selection_state

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

from examples.Sky04_single_shell_continuation.single_shell import (
    build_emulator as build_starlink_gen1_satellite_internet_emulator,
    create_simulator as create_starlink_gen1_simulator, REFERENCE_EPOCH, DATA_ROOT,
    DEFAULT_OBSERVATION_INTERVAL as STARLINK_GEN1_OBSERVATION_INTERVAL,
)
from skyseed_v2.deployment import seed as seed_adapter
from skyseed_v2.deployment import metadata as deployment_metadata
from skyseed_v2.experiment.output.generation import _write_orchestration_timing, _append_phase
STARLINK_GEN1_FORMAL_BOUNDARY_COUNT = 121
STARLINK_GEN1_TIMELINE_END = REFERENCE_EPOCH + timedelta(seconds=600)
MAXIMUM_GROUND_STATION_ACCESS_COUNT = 4
GATEWAY_CANDIDATE_COUNT = 3
GATEWAY_CANDIDATE_MAX_DISTANCE_M = None
STARLINK_GEN1_PLAN_BATCH_FRAMES = 32
from tools.observation.zone import generate as generate_zone_layout
from data.starlink import rebuild_frozen_ground_stations
from skyseed_v2.backend.bpf import compile_bpf
from skyseed_v2.backend.compiler import _compile_bpf_observed
from skyseed_v2.configuration.emulator import load_static_emulator_config
from skyseed_v2.configuration.simulator import load_zone_layout_definition
from skyseed_v2.emulator.entities import EmulatorGroundStation, GroundStationLocation
from skyseed_v2.emulator.seedemu import get_skyseed_autonomous_systems
from skyseed_v2.adapters.emulator_to_simulator import create_simulator_static_input
from skyseed_v2.backend.execution_materials.sealing import _RunnerArtifactWriter
from skyseed_v2.backend.execution_materials.codec import _open_runner_artifact
from skyseed_v2.experiment.output.observation import _ExecutionObserver
from skyseed_v2.simulator import (
    AccumulatedDeadbandParameterPlanningModel,
    CircularOrbitPositionModel,
    GatewayHopCountRoutePlanningModel,
    GeometricPhysicalLinkModel,
    GroundStationRouteDemandTarget,
    HighestElevationZoneServicePlanningModel,
    OperationalConnectionKind,
    PhysicalPositionSnapshot,
    PhysicalLinkQueryResult,
    PhysicalWorld,
    PlanTransition,
    CoverageFirstGroundStationAccessPlanningModel,
    RetainFirstInterSatelliteConnectionPlanningModel,
    Simulator,
    PlannerConfiguration,
    ZoneRouteDemandTarget,
    ZoneServiceAssignment,
    ZoneServiceTransition,
    apply_plan,
    create_plan,
    plan_transition_fingerprint,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_ROOT = PROJECT_ROOT / "examples/Sky04_single_shell_continuation"
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
        build_starlink_gen1_satellite_internet_emulator("S3")
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
    return starlink_simulator.get_planner()


@pytest.fixture(scope="module")
def first_physical_input(
    starlink_simulator: Simulator,
    starlink_planner,
) -> tuple[PhysicalPositionSnapshot, PhysicalLinkQueryResult]:
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


def test_starlink_gen1_frozen_inputs_are_deterministic_and_v2_local() -> None:
    layout, rendered_zones = generate_zone_layout._canonical_content(
        latitude_bands=24,
        equatorial_cells=48,
        alignment="antimeridian-centered",
    )
    assert rendered_zones == (PROJECT_ROOT / "data/global_zones.yaml").read_text(
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
    frozen = DATA_ROOT / "ground_stations.yaml"
    assert sha256(frozen.read_bytes()).hexdigest() == (
        "18f3531ec1bd535cd87f3f8673717cc6e6d5a9f0866456f8304076fd6415d0e8"
    )
    assert sha256(GROUND_STATION_EVIDENCE_FILE.read_bytes()).hexdigest() == (
        rebuild_frozen_ground_stations.REVIEWED_EVIDENCE_SHA256
    )
    assert len(
        rebuild_frozen_ground_stations.load_reviewed_sites(
            GROUND_STATION_EVIDENCE_FILE
        )
    ) == 355
    assert rebuild_frozen_ground_stations.render_ground_stations(
        GROUND_STATION_EVIDENCE_FILE
    ) == frozen.read_text(encoding="utf-8")

    for runtime_source in ("single_shell.py",):
        source = (EXAMPLE_ROOT / runtime_source).read_text(encoding="utf-8")
        assert "skyseed_v1" not in source
        assert "research/" not in source


def test_starlink_gen1_runtime_docker_preserves_interface_less_router_identity(
    monkeypatch,
) -> None:
    station = EmulatorGroundStation(
        ground_station_id="gateway-a",
        asn=65040,
        location=GroundStationLocation(0.0, 0.0),
        station_type="gateway",
        operator=None,
    )
    compiler = seed_adapter._InterfaceLessRuntimeDocker()
    same_display_station = EmulatorGroundStation(
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
    runtime = tmp_path / "deployment"
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
    plan = deployment_metadata._write_compose_build_plan(runtime).read_text().splitlines()
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


def test_zone_authoring_check_is_exact_and_read_only(tmp_path: Path) -> None:
    existing = tmp_path / "zones.yaml"
    shutil.copyfile(PROJECT_ROOT / "data/global_zones.yaml", existing)
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
    config = load_static_emulator_config(DATA_ROOT / "scenario.yaml")
    autonomous_system = config.skyseed_autonomous_systems[0]
    shell_input = next(shell for shell in autonomous_system.shells if shell.shell_id == "S3")
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
    emulator = build_starlink_gen1_satellite_internet_emulator("S3")
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
    scenario = (DATA_ROOT / "scenario.yaml").read_text(encoding="utf-8")
    (tmp_path / "scenario.yaml").write_text(
        scenario.replace("    shells_file:", f"    selected_shell: {shell_id}\n    shells_file:"),
        encoding="utf-8",
    )
    shutil.copyfile(
        DATA_ROOT / "ground_stations.yaml",
        tmp_path / "ground_stations.yaml",
    )
    shutil.copyfile(DATA_ROOT / "shells.yaml", tmp_path / "shells.yaml")
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


def test_starlink_gen1_zone_partition_has_exact_bands_and_global_resolution(
    starlink_physical_world: PhysicalWorld,
) -> None:
    definition = load_zone_layout_definition(PROJECT_ROOT / "data/global_zones.yaml")
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
    first_physical_input: tuple[PhysicalPositionSnapshot, PhysicalLinkQueryResult],
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
        first = model.create_route_demands(physical_world, (assignment,), timestamp=MODEL_TIMESTAMP)
        second = model.create_route_demands(physical_world, (assignment,), timestamp=MODEL_TIMESTAMP)
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
    first_physical_input: tuple[PhysicalPositionSnapshot, PhysicalLinkQueryResult],
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
    # Phase 4a-1 moves Model classes; provenance changes with their actual module paths.
    assert physical_world.semantic_fingerprint() == (
        "ab2e4e68ffb78d65223fa42dd6fce1dd67bdd1bb7a7543eff8f96f56c364303e"
    )

    # Independent coverage-only bipartite augmenting paths. The complete five-level
    # objective is tested separately by exhaustive enumeration of small graphs.
    for offset, expected_candidates, _, _ in CANONICAL_GROUND_STATION_ALLOCATION_REGRESSION:
        frame = physical_world.frame_at(REFERENCE_EPOCH + timedelta(seconds=offset))
        observation = physical_link_model.compute_observation(physical_world, frame, starlink_planner.create_physical_link_request())
        candidates = observation.ground_station_access_candidates
        selected = allocation_model.select_ground_station_accesses(physical_world, candidates, state=selection_state())
        stations = sorted({c.ground_station_id for c in candidates}, key=lambda n: n.value)
        satellites = {c.satellite_id for c in candidates}
        assert len(candidates) == expected_candidates
        slots = {sat: tuple((sat, i) for i in range(physical_world.get_satellite(sat).max_simultaneous_ground_station_accesses)) for sat in satellites}
        options = {station: tuple(slot for c in candidates if c.ground_station_id == station for slot in slots[c.satellite_id]) for station in stations}
        matched = {}
        def augment(station, seen):
            for slot in options[station]:
                if slot in seen:
                    continue
                seen.add(slot)
                if slot not in matched or augment(matched[slot], seen):
                    matched[slot] = station
                    return True
            return False
        maximum_coverage = sum(augment(station, set()) for station in stations)
        assert len({c.ground_station_id for c in selected}) == maximum_coverage
        assert {(c.ground_station_id, c.satellite_id) for c in selected} <= {(c.ground_station_id, c.satellite_id) for c in candidates}
        assert all(sum(c.satellite_id == sat for c in selected) <= physical_world.get_satellite(sat).max_simultaneous_ground_station_accesses for sat in satellites)
        retained = frozenset((c.ground_station_id, c.satellite_id) for c in selected)
        assert allocation_model.select_ground_station_accesses(physical_world, tuple(reversed(candidates)), selection_state(retained)) == selected


def test_orchestration_timing_distinguishes_spans_from_aggregates(
    tmp_path: Path,
) -> None:
    records = []
    _append_phase(
        records,
        "interleaved-writer-total",
        "persistence",
        3.0,
        additive=True,
        interval_kind="aggregate",
    )
    _append_phase(
        records,
        "measured-stage",
        "semantic-core",
        2.0,
        additive=False,
        started_monotonic=101.0,
        ended_monotonic=103.0,
    )
    _write_orchestration_timing(
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
        "backend-state-freeze",
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
    assert manifest["encoding"] == "prepared-tc-elf-v7"
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
