"""Project an existing Emulator into the richer A-F Dynamic Slice sequence."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from seedemu.core import Emulator
from seedemu.layers import Base

from skyseed_v2.configuration.simulator import load_zone_layout_definition
from skyseed_v2.emulator.seedemu import get_skyseed_autonomous_systems
from skyseed_v2.simulator import (
    CircularOrbitPositionModel,
    ConnectionKind,
    GeometricConnectionModel,
    HopCountRouteModel,
    SimulationDefinition,
    SimulationTimeline,
    Simulator,
    Slicer,
)
from skyseed_v2.emulator_to_simulator import create_simulator_static_input


EXAMPLE_ROOT = Path(__file__).resolve().parent
DEFAULT_START_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)
DEFAULT_SLICE_INTERVAL = timedelta(seconds=120)
REAL_SLICE_COUNT = 20
DEFAULT_END_TIME = (
    DEFAULT_START_TIME + (REAL_SLICE_COUNT - 1) * DEFAULT_SLICE_INTERVAL
)
ZONE_LAYOUT_FILE = EXAMPLE_ROOT / "zones.yaml"

# Every algorithm parameter is explicit and unit-bearing.
MAXIMUM_ISL_DISTANCE_M = 6_000_000.0
GROUND_STATION_MINIMUM_ELEVATION_DEG = 5.0
MAXIMUM_GROUND_STATION_ACCESS_COUNT = 2
GROUND_STATION_MAXIMUM_DISTANCE_M = 3_000_000.0
ZONE_COVERAGE_MINIMUM_ELEVATION_DEG = 10.0
PROPAGATION_SPEED_M_PER_S = 299_792_458.0
BANDWIDTH_BPS_BY_KIND = {
    ConnectionKind.INTER_SATELLITE: 1_000_000_000,
    ConnectionKind.GROUND_STATION_ACCESS: 500_000_000,
}
PACKET_LOSS_PPM_BY_KIND = {
    ConnectionKind.INTER_SATELLITE: 100,
    ConnectionKind.GROUND_STATION_ACCESS: 200,
}


def create_dynamic_reference_slicer(
    emulator: Emulator,
    *,
    zone_layout_path: Path = ZONE_LAYOUT_FILE,
) -> Slicer:
    """Project one Emulator into the formal bounded A-F chain."""

    if not isinstance(emulator, Emulator):
        raise TypeError("emulator must be an upstream Emulator")
    base = emulator.getLayer("Base")
    if not isinstance(base, Base):
        raise TypeError("dynamic reference Base layer must be a Base")
    skyseed_autonomous_systems = get_skyseed_autonomous_systems(base)
    simulator_input = create_simulator_static_input(
        skyseed_autonomous_systems
    )
    timeline = SimulationTimeline(
        start_time=DEFAULT_START_TIME,
        end_time=DEFAULT_END_TIME,
        slice_interval=DEFAULT_SLICE_INTERVAL,
    )
    simulator = Simulator(
        SimulationDefinition(static_input=simulator_input, timeline=timeline)
    )
    simulator.create_topology(
        zone_layout_definition=load_zone_layout_definition(zone_layout_path)
    )
    slicer = simulator.create_slicer(
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
        route_model=HopCountRouteModel(),
    )
    created = slicer.create_all_slices()
    if len(created) != REAL_SLICE_COUNT:
        raise AssertionError("bounded reference Timeline produced wrong count")
    return slicer
