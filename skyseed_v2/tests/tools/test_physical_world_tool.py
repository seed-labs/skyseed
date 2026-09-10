from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from examples.complete_static_emulator.emu import build_emulator
from examples.complete_static_emulator.simu import create_complete_static_simulator
from skyseed_v2.shared import NodeId
from skyseed_v2.simulator import (
    PhysicalLinkObservation,
    SimulationDefinition,
    SimulationTimeline,
    Simulator,
)
from skyseed_v2.simulator.input import (
    GroundStationSimulationInput,
    SatelliteSimulationInput,
    SimulatorStaticInput,
)
from tools._display_geometry import EARTH_REFERENCE_RADIUS_M, physical_display_radius
from tools._earth_reference import _natural_earth_coastline_lines
from tools._html import write_failed_tool
from tools._inspection_evidence import (
    _PhysicalLinkInspectionWriter,
    iter_physical_link_inspection,
    read_physical_link_inspection_index,
)
from tools.physical_world import collect_physical_world_data, write_physical_world_tool


class _UnusedPositionModel:
    def compute_positions(self, physical_world, timestamp):
        raise AssertionError("Physical World Tool tests do not project positions")


@pytest.fixture
def physical_world():
    return create_complete_static_simulator(build_emulator()).get_physical_world()


def physical_world_from_input(static_input: SimulatorStaticInput):
    definition = SimulationDefinition(
        static_input,
        SimulationTimeline(
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 1, 0, 0, 10, tzinfo=timezone.utc),
            timedelta(seconds=10),
        ),
    )
    return Simulator(definition).create_physical_world(
        position_model=_UnusedPositionModel()
    )


def satellite_input(
    asn: int,
    satellite_id: str,
    shell_id: str,
    orbit_id: str,
    orbit_index: int,
    *,
    right_ascension_deg: float,
) -> SatelliteSimulationInput:
    return SatelliteSimulationInput(
        node_id=NodeId(asn, "satellite", satellite_id),
        satellite_id=satellite_id,
        shell_id=shell_id,
        orbit_id=orbit_id,
        orbit_index=orbit_index,
        altitude_km=500.0,
        inclination_deg=40.0,
        right_ascension_deg=right_ascension_deg,
        mean_anomaly_deg=orbit_index * 30.0,
        epoch_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def ground_station_input(
    asn: int,
    station_id: str,
    latitude: float,
    longitude: float,
    *,
    operator: str | None = "fixture",
) -> GroundStationSimulationInput:
    return GroundStationSimulationInput(
        node_id=NodeId(asn, "ground-station", station_id),
        ground_station_id=station_id,
        latitude_deg=latitude,
        longitude_deg=longitude,
        elevation_m=25.0,
        station_type="gateway",
        operator=operator,
    )


def test_public_projection_isolates_input_and_owned_path_retains_frames(physical_world):
    from copy import deepcopy
    from tools.physical_world.reader import read_physical_world
    from tools.physical_world.projection import project_physical_world_data, _project_owned_snapshot
    read = read_physical_world(physical_world)
    before = deepcopy(read.snapshot)
    projected = project_physical_world_data(read)
    projected["counts"]["satellites"] = -1
    assert read.snapshot == before
    frames = read.snapshot["physical_frames"]
    owned = _project_owned_snapshot(read.snapshot)
    assert owned["_physical_frames"] is frames


def test_collector_counts_public_tuples_and_checks_owner_facade_identity(
    physical_world,
) -> None:
    data = collect_physical_world_data(physical_world)

    expected = {
        "autonomous_systems": len(physical_world.autonomous_systems),
        "shells": sum(len(value.shells) for value in physical_world.autonomous_systems),
        "orbits": sum(
            len(shell.orbits)
            for value in physical_world.autonomous_systems
            for shell in value.shells
        ),
        "satellites": sum(
            len(orbit.satellites)
            for value in physical_world.autonomous_systems
            for shell in value.shells
            for orbit in shell.orbits
        ),
        "ground_stations": sum(
            len(value.ground_stations) for value in physical_world.autonomous_systems
        ),
        "zones": len(physical_world.get_zone_layout().zones),
    }
    assert data["counts"] == expected
    assert data["status"] == "PASS"
    assert data["consistency"]["failed"] == 0
    assert data["consistency"]["checks"] == []


def test_collector_handles_empty_and_multi_owner_topologies() -> None:
    empty = collect_physical_world_data(physical_world_from_input(SimulatorStaticInput()))
    assert empty["counts"] == {
        "autonomous_systems": 0,
        "shells": 0,
        "orbits": 0,
        "satellites": 0,
        "ground_stations": 0,
        "zones": 0,
    }
    assert not empty["zone_layout_configured"]
    assert empty["status"] == "PASS"

    static_input = SimulatorStaticInput(
        satellites=(
            satellite_input(65110, "a-0", "alpha", "one", 0, right_ascension_deg=5),
            satellite_input(65110, "a-1", "alpha", "one", 1, right_ascension_deg=5),
            satellite_input(65110, "b-0", "beta", "two", 0, right_ascension_deg=25),
            satellite_input(65220, "c-0", "gamma", "three", 0, right_ascension_deg=45),
        ),
        ground_stations=(
            ground_station_input(65110, "west-gs", 10, -120),
            ground_station_input(65220, "east-gs", 20, 120),
            ground_station_input(65330, "only-gs", -30, 30),
        ),
    )
    data = collect_physical_world_data(physical_world_from_input(static_input))

    assert data["counts"] == {
        "autonomous_systems": 3,
        "shells": 3,
        "orbits": 3,
        "satellites": 4,
        "ground_stations": 3,
        "zones": 0,
    }
    assert "hierarchy" not in data
    assert len(data["orbit_rings"]) == 3
    assert len(data["ground_stations"]) == 3
    assert data["consistency"]["failed"] == 0


def test_orbit_ring_uses_only_public_static_plane_parameters(physical_world) -> None:
    data = collect_physical_world_data(physical_world)
    ring = data["orbit_rings"][0]

    assert set(ring) == {
        "asn",
        "shell_id",
        "orbit_id",
        "altitude_km",
        "inclination_deg",
        "right_ascension_deg",
        "epoch_utc",
        "satellite_count",
        "satellite_ids",
        "satellites",
    }
    orbit = physical_world.autonomous_systems[0].shells[0].orbits[0]
    assert ring["altitude_km"] == orbit.altitude_km
    assert ring["inclination_deg"] == orbit.inclination_deg
    assert ring["right_ascension_deg"] == orbit.right_ascension_deg
    assert ring["satellites"] == [
        {
            "node_id": satellite.node_id.value,
            "satellite_id": satellite.satellite_id,
            "orbit_index": satellite.orbit_index,
            "mean_anomaly_deg": satellite.mean_anomaly_deg,
            "max_simultaneous_ground_station_accesses": (
                satellite.max_simultaneous_ground_station_accesses
            ),
            "inter_satellite_link_capability": (
                None
                if satellite.inter_satellite_link_capability is None
                else {
                    "maximum_simultaneous_links": (
                        satellite.inter_satellite_link_capability
                        .maximum_simultaneous_links
                    )
                }
            ),
        }
        for satellite in orbit.satellites
    ]
    assert {
        satellite["inter_satellite_link_capability"][
            "maximum_simultaneous_links"
        ]
        for satellite in ring["satellites"]
    } == {4}
    assert ring["epoch_utc"] == orbit.epoch_utc.isoformat()
    assert data["temporal_inventory"]["formal_frames"] == 0


def test_ground_station_records_are_the_public_physical_world_values(physical_world) -> None:
    data = collect_physical_world_data(physical_world)
    station = physical_world.autonomous_systems[0].ground_stations[0]

    assert data["ground_stations"] == [
        {
            "asn": station.node_id.asn,
            "ground_station_id": station.ground_station_id,
            "node_id": station.node_id.value,
            "latitude_deg": station.latitude_deg,
            "longitude_deg": station.longitude_deg,
            "elevation_m": station.elevation_m,
            "station_type": station.station_type,
            "operator": station.operator,
            "max_simultaneous_satellite_accesses": (
                station.max_simultaneous_satellite_accesses
            ),
        }
    ]

    nullable = collect_physical_world_data(
        physical_world_from_input(
            SimulatorStaticInput(
                ground_stations=(
                    ground_station_input(
                        65110,
                        "no-known-operator",
                        10,
                        -120,
                        operator=None,
                    ),
                )
            )
        )
    )
    assert nullable["ground_stations"][0]["operator"] is None


def test_frozen_natural_earth_reference_is_minimal_coordinate_data() -> None:
    lines = _natural_earth_coastline_lines()

    assert len(lines) == 134
    assert sum(len(line) for line in lines) == 5_128
    assert all(len(coordinate) == 2 for line in lines for coordinate in line)


def test_shared_tool_private_display_radius_preserves_physical_altitude() -> None:
    assert EARTH_REFERENCE_RADIUS_M == 6_371_008.8
    assert physical_display_radius(0.0) == 1.0
    assert physical_display_radius(560_000.0) == pytest.approx(1.0878981743)


def test_physical_world_renderer_writes_embedded_offline_artifacts(
    physical_world,
    tmp_path: Path,
) -> None:
    frame = physical_world.frame_at(
        datetime(2026, 1, 1, tzinfo=timezone.utc)
    )
    result = write_physical_world_tool(
        physical_world, tmp_path, physical_frame=frame
    )
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    payload = json.loads(
        (tmp_path / "physical-world.json").read_text(encoding="utf-8")
    )
    summary = (tmp_path / "summary.txt").read_text(encoding="utf-8")

    assert result["status"] == "PASS"
    assert payload["format_version"] == 2
    assert payload["temporal_inventory"]["formal_frames"] == 1
    assert payload["frame_index"][0]["chunk"] == "data/frame-000.js"
    assert (tmp_path / "data/frame-000.js").is_file()
    assert "const DATA=" in html
    assert "const DATA=" in html and "COAST=" in html
    assert 'href="../index.html"' in html
    assert "以地图为中心的精确物理证据" in html
    assert "GroundStation" in html
    assert 'id="globe-canvas"' in html
    assert 'id="map-canvas"' in html
    assert 'id="time"' in html
    assert 'data-layer="physical-link"' in html
    assert 'data-layer="zone"' in html
    assert "GEO=" in html
    assert "function loadFrame(index)" in html
    assert "function adjacency()" in html
    assert "Selected exact relation" in html
    assert "globe.dataset.selectedRelation" in html
    assert "map.dataset.selectedRelation" in html
    assert "devicePixelRatio" in html
    assert 'id="detail"' in html
    assert "map.dataset.layer" in html
    assert "map.dataset.timestamp" in html
    assert "map.dataset.selectedId" in html
    assert "globe.onpointermove" in html
    assert "Concrete model descriptor" in html
    assert "feasible input facts only" in html
    assert "mean_anomaly_deg" in html
    assert "http://" not in html
    assert "https://" not in html
    assert "fetch(" not in html
    assert "<link " not in html
    assert "status=PASS" in summary
    assert "satellites=2" in summary
    (tmp_path / "physical-world.json").unlink()
    assert (tmp_path / "index.html").read_text(encoding="utf-8") == html


def test_exact_physical_link_inspection_stream_is_sealed_and_verified(
    physical_world, tmp_path: Path
) -> None:
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    writer = _PhysicalLinkInspectionWriter(tmp_path, physical_world)
    writer.record(PhysicalLinkObservation(timestamp))
    manifest = writer.finalize()

    assert manifest.is_file()
    index = read_physical_link_inspection_index(writer.path)
    assert index.timestamps == (timestamp.isoformat(),)
    assert len(index.nodes) == 3
    records = tuple(iter_physical_link_inspection(writer.path))
    assert records[1]["timestamp"] == timestamp.isoformat()
    assert records[1]["inter_satellite"] == []
    assert records[1]["ground_station"] == []
    assert records[1]["zone_service"] == []


def test_failed_page_is_conspicuous_and_contains_no_success_claim(
    tmp_path: Path,
) -> None:
    write_failed_tool(tmp_path, "physical-world", RuntimeError("formal API broke"))

    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    summary = (tmp_path / "summary.txt").read_text(encoding="utf-8")
    assert "FAILED" in html
    assert "formal API broke" in html
    assert "没有产生成功结果" in html
    assert "status=FAILED" in summary
