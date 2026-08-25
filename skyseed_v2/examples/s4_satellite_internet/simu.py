"""Assemble the static or dynamic S4 Simulator branch."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from seedemu.core import Emulator
from seedemu.layers import Base

from skyseed_v2.configuration.simulator import load_zone_layout_definition
from skyseed_v2.emulator.seedemu import get_skyseed_autonomous_systems
from skyseed_v2.emulator_to_simulator import create_simulator_static_input
from skyseed_v2.simulator import (
    CircularOrbitPositionModel,
    ConnectionKind,
    GatewayHopCountRouteModel,
    GeometricConnectionModel,
    SimulationDefinition,
    SimulationTimeline,
    Simulator,
)


EXAMPLE_ROOT = Path(__file__).resolve().parent
ZONE_LAYOUT_FILE = EXAMPLE_ROOT / "zones.yaml"
REFERENCE_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)
STATIC_TIMELINE_END = REFERENCE_EPOCH + timedelta(seconds=10)
DYNAMIC_SLICE_INTERVAL = timedelta(seconds=10)
DYNAMIC_DURATION = timedelta(minutes=30)
DYNAMIC_TIMELINE_END = REFERENCE_EPOCH + DYNAMIC_DURATION
DYNAMIC_REAL_SLICE_COUNT = 181

# S4 qualification policies are evidence-informed abstractions documented by
# the example; they do not claim identical hardware at every Gateway site.
MAXIMUM_ISL_DISTANCE_M = 6_000_000.0
GROUND_STATION_MINIMUM_ELEVATION_DEG = 5.0
MAXIMUM_GROUND_STATION_ACCESS_COUNT = 8
GROUND_STATION_MAXIMUM_DISTANCE_M = 3_000_000.0
ZONE_COVERAGE_MINIMUM_ELEVATION_DEG = 25.0
PROPAGATION_SPEED_M_PER_S = 299_792_458.0
BANDWIDTH_BPS_BY_KIND = {
    ConnectionKind.INTER_SATELLITE: 1_000_000_000,
    ConnectionKind.GROUND_STATION_ACCESS: 500_000_000,
}
PACKET_LOSS_PPM_BY_KIND = {
    ConnectionKind.INTER_SATELLITE: 100,
    ConnectionKind.GROUND_STATION_ACCESS: 200,
}
GATEWAY_CANDIDATE_COUNT = 3
GATEWAY_CANDIDATE_MAX_DISTANCE_M = None


def create_s4_static_simulator(
    emulator: Emulator,
    *,
    zone_layout_path: Path = ZONE_LAYOUT_FILE,
) -> Simulator:
    """Project one S4 Emulator and materialize its static Topology."""

    return _create_s4_simulator(
        emulator,
        zone_layout_path=zone_layout_path,
        timeline=SimulationTimeline(
            start_time=REFERENCE_EPOCH,
            end_time=STATIC_TIMELINE_END,
            slice_interval=DYNAMIC_SLICE_INTERVAL,
        ),
    )


def create_s4_dynamic_simulator(
    emulator: Emulator,
    *,
    zone_layout_path: Path = ZONE_LAYOUT_FILE,
) -> Simulator:
    """Create the S4 qualification Slicer without advancing its Timeline."""

    simulator = _create_s4_simulator(
        emulator,
        zone_layout_path=zone_layout_path,
        timeline=SimulationTimeline(
            start_time=REFERENCE_EPOCH,
            end_time=DYNAMIC_TIMELINE_END,
            slice_interval=DYNAMIC_SLICE_INTERVAL,
        ),
    )
    simulator.create_slicer(
        position_model=CircularOrbitPositionModel(),
        connection_model=GeometricConnectionModel(
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
        ),
        route_model=GatewayHopCountRouteModel(
            gateway_candidate_count=GATEWAY_CANDIDATE_COUNT,
            gateway_candidate_max_distance_m=(
                GATEWAY_CANDIDATE_MAX_DISTANCE_M
            ),
        ),
    )
    return simulator


def _create_s4_simulator(
    emulator: Emulator,
    *,
    zone_layout_path: Path,
    timeline: SimulationTimeline,
) -> Simulator:
    if not isinstance(emulator, Emulator):
        raise TypeError("emulator must be an upstream Emulator")
    base = emulator.getLayer("Base")
    if not isinstance(base, Base):
        raise TypeError("S4 Base layer must be a Base")
    static_input = create_simulator_static_input(
        get_skyseed_autonomous_systems(base)
    )
    simulator = Simulator(
        SimulationDefinition(static_input=static_input, timeline=timeline)
    )
    simulator.create_topology(
        zone_layout_definition=load_zone_layout_definition(zone_layout_path)
    )
    return simulator


__all__ = [
    "create_s4_dynamic_simulator",
    "create_s4_static_simulator",
]
