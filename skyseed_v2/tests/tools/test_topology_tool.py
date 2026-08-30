from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from examples.complete_static_emulator.emu import build_emulator
from examples.complete_static_emulator.main import _create_simulator_topology
from skyseed_v2.shared import NodeId
from skyseed_v2.simulator import (
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
from tools.topology import collect_topology_data, write_topology_tool


class _UnusedPositionModel:
    def compute_positions(self, topology, timestamp):
        raise AssertionError("Topology Tool tests do not project positions")


@pytest.fixture
def topology():
    return _create_simulator_topology(build_emulator())


def topology_from_input(static_input: SimulatorStaticInput):
    definition = SimulationDefinition(
        static_input,
        SimulationTimeline(
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 1, 0, 0, 10, tzinfo=timezone.utc),
            timedelta(seconds=10),
        ),
    )
    return Simulator(definition).create_topology(
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


def test_collector_counts_public_tuples_and_checks_owner_facade_identity(
    topology,
) -> None:
    data = collect_topology_data(topology)

    expected = {
        "autonomous_systems": len(topology.autonomous_systems),
        "shells": sum(len(value.shells) for value in topology.autonomous_systems),
        "orbits": sum(
            len(shell.orbits)
            for value in topology.autonomous_systems
            for shell in value.shells
        ),
        "satellites": sum(
            len(orbit.satellites)
            for value in topology.autonomous_systems
            for shell in value.shells
            for orbit in shell.orbits
        ),
        "ground_stations": sum(
            len(value.ground_stations) for value in topology.autonomous_systems
        ),
        "zones": len(topology.get_zone_layout().zones),
    }
    assert data["counts"] == expected
    assert data["status"] == "PASS"
    assert data["consistency"]["failed"] == 0
    assert data["consistency"]["checks"] == []


def test_collector_handles_empty_and_multi_owner_topologies() -> None:
    empty = collect_topology_data(topology_from_input(SimulatorStaticInput()))
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
    data = collect_topology_data(topology_from_input(static_input))

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


def test_orbit_ring_uses_only_public_static_plane_parameters(topology) -> None:
    data = collect_topology_data(topology)
    ring = data["orbit_rings"][0]

    assert set(ring) == {
        "asn",
        "shell_id",
        "orbit_id",
        "altitude_km",
        "inclination_deg",
        "right_ascension_deg",
        "satellite_count",
        "satellite_ids",
        "satellites",
    }
    orbit = topology.autonomous_systems[0].shells[0].orbits[0]
    assert ring["altitude_km"] == orbit.altitude_km
    assert ring["inclination_deg"] == orbit.inclination_deg
    assert ring["right_ascension_deg"] == orbit.right_ascension_deg
    assert ring["satellites"] == [
        {
            "node_id": satellite.node_id.value,
            "satellite_id": satellite.satellite_id,
            "orbit_index": satellite.orbit_index,
            "max_simultaneous_ground_station_accesses": (
                satellite.max_simultaneous_ground_station_accesses
            ),
        }
        for satellite in orbit.satellites
    ]
    assert "mean_anomaly_deg" not in json.dumps(data)
    assert "timestamp" not in json.dumps(data).lower()


def test_ground_station_records_are_the_public_topology_values(topology) -> None:
    data = collect_topology_data(topology)
    station = topology.autonomous_systems[0].ground_stations[0]

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

    nullable = collect_topology_data(
        topology_from_input(
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


def test_topology_renderer_writes_embedded_offline_artifacts(
    topology,
    tmp_path: Path,
) -> None:
    result = write_topology_tool(topology, tmp_path)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    payload = json.loads(
        (tmp_path / "topology.json").read_text(encoding="utf-8")
    )
    summary = (tmp_path / "summary.txt").read_text(encoding="utf-8")

    assert result["status"] == "PASS"
    assert payload["format_version"] == 1
    assert "const DATA=" in html
    assert "const COASTLINES=" in html
    assert 'href="../index.html"' in html
    assert "静态轨道平面" in html
    assert "不表示任一时刻的卫星位置" in html
    assert "GroundStation" in html
    assert "spherePoint(gs.latitude_deg,gs.longitude_deg" in html
    assert "const orbitPoint=(ring,angle)" in html
    assert "const DISPLAY_GEOMETRY=" in html
    assert "ring.inclination_deg" in html
    assert "ring.right_ascension_deg" in html
    assert "ring.altitude_km" in html
    assert (
        "Number(ring.altitude_km)*1000/"
        "DISPLAY_GEOMETRY.earth_reference_radius_m" in html
    )
    assert "Math.min(.24" not in html
    assert "canvas.dataset.firstOrbitDisplayRadius" in html
    assert "canvas.dataset.firstOrbitAltitudeKm" in html
    assert "canvas.dataset.orbitSegments" in html
    assert "canvas.dataset.coastlineSegments" in html
    assert "window.devicePixelRatio" in html
    assert "backingWidth/logicalWidth" in html
    assert "x=clientX-box.left" in html
    assert 'id="selected-object"' in html
    assert "Selected static object" in html
    assert "orbitHitRegions" in html
    assert "groundStationHitRegions" in html
    assert "function showSelection(kind,value)" in html
    assert "function selectAtCanvas(clientX,clientY)" in html
    assert "showSelection(hit.kind,hit.value)" in html
    assert "value.satellite_count" in html
    assert "value.satellites.map" in html
    assert "satellite.max_simultaneous_ground_station_accesses" in html
    assert "value.ground_station_id" in html
    assert "value.station_type" in html
    assert "value.max_simultaneous_satellite_accesses" in html
    assert "canvas.dataset.selectedLabel=selectedLabelText" in html
    assert "canvas.dataset.hoverLabel=hoverLabelText" in html
    assert 'selectedLabelText=ring.orbit_id' in html
    assert 'selectedLabelText=gs.ground_station_id' in html
    assert "function clearSelection()" in html
    assert "selected?.kind===hit.kind&&selected.id===hit.id" in html
    assert 'event.key==="Escape"' in html
    assert "hsla(" not in html
    assert "(index*47)%360" not in html
    assert 'canvas.addEventListener("pointermove"' in html
    assert "if(total>5)pointer.moved=true" in html
    assert "const wasClick=pointer&&!pointer.moved" in html
    assert "orbit-reset" in html
    assert "单位半径" in html
    assert "不执行 Satellite propagation" in html
    assert "诊断" in html
    assert "无一致性异常" in html
    assert "<table" not in html
    assert "mean_anomaly_deg" not in html
    assert "Static object tree" not in html
    assert "Object details" not in html
    assert "JSON.stringify" not in html
    assert "http://" not in html
    assert "https://" not in html
    assert "fetch(" not in html
    assert "<script src=" not in html
    assert "<link " not in html
    assert "status=PASS" in summary
    assert "satellites=2" in summary
    (tmp_path / "topology.json").unlink()
    assert (tmp_path / "index.html").read_text(encoding="utf-8") == html


def test_failed_page_is_conspicuous_and_contains_no_success_claim(
    tmp_path: Path,
) -> None:
    write_failed_tool(tmp_path, "topology", RuntimeError("formal API broke"))

    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    summary = (tmp_path / "summary.txt").read_text(encoding="utf-8")
    assert "FAILED" in html
    assert "formal API broke" in html
    assert "没有产生成功结果" in html
    assert "status=FAILED" in summary
