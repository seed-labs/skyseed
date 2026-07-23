from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from examples.complete_static_emulator.emu import build_emulator
from examples.complete_static_emulator.main import _create_simulator_topology
from skyseed_v2.node_id import NodeId
from skyseed_v2.simulator import (
    SimulationDefinition,
    SimulationTimeline,
    Simulator,
)
from skyseed_v2.simulator.static_input import (
    GroundStationSimulationInput,
    SatelliteSimulationInput,
    SimulatorStaticInput,
)
from tools._html import write_failed_tool
from tools.topology import collect_topology_data, write_topology_tool


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
    return Simulator(definition).create_topology()


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
) -> GroundStationSimulationInput:
    return GroundStationSimulationInput(
        node_id=NodeId(asn, "ground-station", station_id),
        ground_station_id=station_id,
        latitude_deg=latitude,
        longitude_deg=longitude,
        elevation_m=25.0,
        station_type="gateway",
        operator="fixture",
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
    assert all(
        check["status"] == "PASS"
        for check in data["consistency"]["checks"]
    )


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
    assert len(data["hierarchy"]) == 3
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
    }
    orbit = topology.autonomous_systems[0].shells[0].orbits[0]
    assert ring["altitude_km"] == orbit.altitude_km
    assert ring["inclination_deg"] == orbit.inclination_deg
    assert ring["right_ascension_deg"] == orbit.right_ascension_deg
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
        }
    ]


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
    assert "静态轨道平面" in html
    assert "不表示任一时刻的卫星位置" in html
    assert "GroundStation" in html
    assert "spherePoint(gs.latitude_deg,gs.longitude_deg" in html
    assert "orbit-reset" in html
    assert "单位半径" in html
    assert "没有执行 Satellite propagation" in html
    assert "一致性摘要" in html
    assert "mean_anomaly_deg" not in html
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
