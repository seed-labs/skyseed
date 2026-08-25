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

from examples.s4_satellite_internet import generate_inputs
from examples.s4_satellite_internet.emu import (
    build_s4_satellite_internet_emulator,
)
from examples.s4_satellite_internet.qualification import _slice_tool_context
from examples.s4_satellite_internet.simu import (
    DYNAMIC_REAL_SLICE_COUNT,
    DYNAMIC_SLICE_INTERVAL,
    DYNAMIC_TIMELINE_END,
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
    ZONE_COVERAGE_MINIMUM_ELEVATION_DEG,
    create_s4_dynamic_simulator,
    create_s4_static_simulator,
)
from skyseed_v2.configuration.emulator import load_static_emulator_config
from skyseed_v2.configuration.simulator import load_zone_layout_definition
from skyseed_v2.emulator.seedemu import get_skyseed_autonomous_systems
from skyseed_v2.emulator_to_simulator import create_simulator_static_input
from skyseed_v2.simulator import (
    CircularOrbitPositionModel,
    ConnectionKind,
    ConnectionSlice,
    GatewayHopCountRouteModel,
    GeometricConnectionModel,
    RouteSlice,
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


@pytest.fixture(scope="module")
def s4_topology() -> Topology:
    emulator = build_s4_satellite_internet_emulator()
    return create_s4_static_simulator(emulator).get_topology()


@pytest.fixture(scope="module")
def s4_dynamic_progression() -> tuple[
    Simulator,
    TopologySlice,
    TopologySlice,
    TopologyDifference,
    TopologyDifference,
]:
    emulator = build_s4_satellite_internet_emulator()
    simulator = create_s4_dynamic_simulator(emulator)
    slicer = simulator.get_slicer()
    first = slicer.create_next_slice()
    first_difference = slicer.create_difference(0, 1)
    second = slicer.create_next_slice()
    second_difference = slicer.create_difference(1, 2)
    return simulator, first, second, first_difference, second_difference


def test_s4_frozen_inputs_are_deterministic_and_v2_local() -> None:
    generate_inputs._check()
    assert generate_inputs._generated_longitude_counts() == (
        EXPECTED_LONGITUDE_COUNTS
    )
    assert sum(EXPECTED_LONGITUDE_COUNTS) == 732
    frozen = EXAMPLE_ROOT / "ground_stations.yaml"
    assert sha256(frozen.read_bytes()).hexdigest() == (
        generate_inputs.FROZEN_GROUND_STATIONS_SHA256
    )
    assert sha256(GROUND_STATION_EVIDENCE_FILE.read_bytes()).hexdigest() == (
        generate_inputs.REVIEWED_GROUND_STATION_EVIDENCE_SHA256
    )
    assert len(
        generate_inputs._load_reviewed_ground_station_sites(
            GROUND_STATION_EVIDENCE_FILE
        )
    ) == 355
    assert generate_inputs._render_ground_stations(
        GROUND_STATION_EVIDENCE_FILE
    ) == frozen.read_text(encoding="utf-8")

    for runtime_source in ("emu.py", "simu.py", "main.py"):
        source = (EXAMPLE_ROOT / runtime_source).read_text(encoding="utf-8")
        assert "skyseed_v1" not in source
        assert "research/" not in source


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
    assert all(not station.ix_attachments for station in autonomous_system.ground_stations)

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
    topology = create_s4_static_simulator(
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
    assert timeline.end_time == DYNAMIC_TIMELINE_END
    assert timeline.slice_interval == DYNAMIC_SLICE_INTERVAL == timedelta(
        seconds=10
    )
    assert len(tuple(timeline.iter_timestamps())) == DYNAMIC_REAL_SLICE_COUNT == 181
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
    context = _slice_tool_context()

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


def test_s4_static_mode_does_not_create_a_slicer() -> None:
    simulator = create_s4_static_simulator(
        build_s4_satellite_internet_emulator()
    )
    assert simulator.get_topology() is not None
    with pytest.raises(RuntimeError, match="Slicer has not been created"):
        simulator.get_slicer()


def test_s4_dynamic_entry_does_not_import_later_pipeline_stages() -> None:
    imported_modules = set()
    for name in ("main.py", "simu.py", "qualification.py"):
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
    assert not any(
        component in module
        for module in imported_modules
        for component in ("planner", "compiler", "runner")
    )
