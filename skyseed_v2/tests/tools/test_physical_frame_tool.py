from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from skyseed_v2.shared import NodeId
from skyseed_v2.simulator import (
    EarthFixedPosition,
    SimulationDefinition,
    SimulationTimeline,
    Simulator,
)
from skyseed_v2.simulator.input import (
    GroundStationSimulationInput,
    SatelliteSimulationInput,
    SimulatorStaticInput,
)
from tools.physical_frame import (
    collect_physical_frame_data,
    write_physical_frame_tool,
)


EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)
SATELLITE_ID = NodeId(65110, "satellite", "sat-0")
GROUND_STATION_ID = NodeId(65110, "ground-station", "gs-0")


class _FixedPositionModel:
    def __init__(self, positions):
        self.positions = positions

    def compute_positions(self, topology, timestamp):
        return self.positions


def _physical_input() -> SimulatorStaticInput:
    return SimulatorStaticInput(
        satellites=(
            SatelliteSimulationInput(
                node_id=SATELLITE_ID,
                satellite_id="sat-0",
                shell_id="shell-a",
                orbit_id="orbit-a",
                orbit_index=0,
                altitude_km=550.0,
                inclination_deg=53.0,
                right_ascension_deg=15.0,
                mean_anomaly_deg=25.0,
                epoch_utc=EPOCH,
                max_simultaneous_ground_station_accesses=2,
            ),
        ),
        ground_stations=(
            GroundStationSimulationInput(
                node_id=GROUND_STATION_ID,
                ground_station_id="gs-0",
                latitude_deg=20.0,
                longitude_deg=30.0,
                elevation_m=100.0,
                station_type="gateway",
                operator="fixture",
                max_simultaneous_satellite_accesses=3,
            ),
        ),
    )


def _topology_and_frame():
    positions = {
        SATELLITE_ID: EarthFixedPosition(6_900_000.0, 120_000.0, 80_000.0),
        GROUND_STATION_ID: EarthFixedPosition(
            5_193_000.0,
            2_998_000.0,
            2_168_000.0,
        ),
    }
    timeline = SimulationTimeline(
        EPOCH,
        EPOCH + timedelta(seconds=10),
        timedelta(seconds=5),
    )
    simulator = Simulator(
        SimulationDefinition(_physical_input(), timeline)
    )
    topology = simulator.create_topology(
        position_model=_FixedPositionModel(positions)
    )
    return topology, topology.frame_at(EPOCH)


def _all_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | set().union(
            *(_all_keys(item) for item in value.values()),
            set(),
        )
    if isinstance(value, (tuple, list)):
        return set().union(*(_all_keys(item) for item in value), set())
    return set()


def test_collector_reads_one_exact_physical_frame_without_operational_fields() -> None:
    topology, frame = _topology_and_frame()

    data = collect_physical_frame_data(topology, frame)

    assert set(data) == {
        "format_version",
        "tool",
        "authority",
        "timestamp",
        "nodes",
        "orbits",
        "inventory",
        "display_notes",
    }
    assert data["tool"] == "physical-frame"
    assert data["timestamp"] == EPOCH.isoformat()
    assert data["inventory"] == {
        "positions": 2,
        "satellites": 1,
        "ground_stations": 1,
        "orbits": 1,
    }
    nodes = {value["node_id"]: value for value in data["nodes"]}
    assert set(nodes) == {SATELLITE_ID.value, GROUND_STATION_ID.value}
    assert nodes[SATELLITE_ID.value][
        "max_simultaneous_ground_station_accesses"
    ] == 2
    assert nodes[GROUND_STATION_ID.value][
        "max_simultaneous_satellite_accesses"
    ] == 3
    for node_id, position in frame.positions.items():
        projected = nodes[node_id.value]["position"]
        assert projected["ecef_m"] == {
            "x": position.x_m,
            "y": position.y_m,
            "z": position.z_m,
        }
        assert projected["geodetic"] == {
            "latitude_deg": position.latitude_deg,
            "longitude_deg": position.longitude_deg,
            "ellipsoidal_height_m": position.ellipsoidal_height_m,
        }

    forbidden = {
        "slice_id",
        "frame_id",
        "connections",
        "connection_slice",
        "routes",
        "route_slice",
        "zone_service_assignments",
        "bandwidth_bps",
        "packet_loss_ppm",
        "difference",
        "plan",
    }
    assert not (_all_keys(data) & forbidden)


def test_collector_rejects_a_frame_with_a_different_topology_node_set() -> None:
    _, frame = _topology_and_frame()
    simulator = Simulator(
        SimulationDefinition(
            SimulatorStaticInput(),
            SimulationTimeline(EPOCH, EPOCH, timedelta(seconds=5)),
        )
    )
    other = simulator.create_topology(position_model=_FixedPositionModel({}))

    with pytest.raises(ValueError, match="supplied Topology node set"):
        collect_physical_frame_data(other, frame)


def test_tool_facade_never_owns_physical_projection() -> None:
    root = Path(__file__).resolve().parents[2] / "tools/physical_frame"
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(root.glob("*.py"))
    )

    for forbidden in (
        ".frame_at(",
        "compute_positions(",
        "PositionModel",
        "Slicer",
        "TopologySlice",
        "ConnectionSlice",
        "RouteSlice",
        "TopologyDifference",
    ):
        assert forbidden not in source


def test_writer_emits_offline_physical_only_artifacts_without_mutation(
    tmp_path: Path,
) -> None:
    topology, frame = _topology_and_frame()
    positions_before = tuple(frame.positions.items())

    result = write_physical_frame_tool(topology, frame, tmp_path)

    payload = json.loads(
        (tmp_path / "physical-frame.json").read_text(encoding="utf-8")
    )
    page = (tmp_path / "index.html").read_text(encoding="utf-8")
    summary = (tmp_path / "summary.txt").read_text(encoding="utf-8")
    assert result["status"] == "PASS"
    assert result["timestamp"] == EPOCH.isoformat()
    assert tuple(frame.positions.items()) == positions_before
    assert payload["timestamp"] == EPOCH.isoformat()
    assert len(payload["nodes"]) == len(frame.positions)
    assert not (_all_keys(payload) & {"slice_id", "frame_id", "connections", "routes"})
    assert "const DATA=" in page
    assert "const COASTLINES=" in page
    assert "PhysicalFrame observed" in page
    assert "WGS84 node subpoints" in page
    assert "canvas.dataset.positionMarkers" in page
    assert "data-selected-node-id" in page
    assert "fetch(" not in page
    assert "http://" not in page
    assert "https://" not in page
    assert "status=PASS" in summary
    assert "tool=physical-frame" in summary
    assert "positions=2" in summary
