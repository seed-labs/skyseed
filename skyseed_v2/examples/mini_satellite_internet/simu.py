"""Assemble the canonical Mini Satellite Internet Simulator and Planner."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from seedemu.core import Emulator
from seedemu.layers import Base

from skyseed_v2.configuration.simulator import load_zone_layout_definition
from skyseed_v2.emulator.seedemu import get_skyseed_autonomous_systems
from skyseed_v2.simulator import (
    AccumulatedDeadbandParameterPlanningModel,
    CircularOrbitPositionModel,
    ConnectionKind,
    DefaultCoordinationPolicy,
    DependencyAwareForwardingPlanningModel,
    GeometricConnectionModel,
    HopCountRouteModel,
    MidpointConnectionPlanningModel,
    Plan,
    Planner,
    SimulationDefinition,
    SimulationTimeline,
    Simulator,
    Slicer,
)
from skyseed_v2.emulator_to_simulator import create_simulator_static_input


EXAMPLE_ROOT = Path(__file__).resolve().parent
DEFAULT_START_TIME = datetime(2026, 1, 1, 0, 0, 30, tzinfo=timezone.utc)
DEFAULT_SLICE_INTERVAL = timedelta(seconds=10)
REAL_SLICE_COUNT = 64
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
PUBLICATION_DIFFERENCE_COUNT = 4
MINIMUM_DWELL_SECONDS = 240
PARAMETER_THRESHOLDS = {
    "bandwidth_bps": 10_000_000,
    "delay_us": 250,
    "packet_loss_ppm": 50,
}


class _PlanAllAvailablePublishFour:
    """Plan all available Differences and publish four Differences."""

    __slots__ = ()

    def select_planning_differences(self, available_differences):
        return tuple(available_differences)

    def select_publication_differences(self, planning_differences):
        return tuple(planning_differences[:PUBLICATION_DIFFERENCE_COUNT])


def create_mini_satellite_internet_simulator(
    emulator: Emulator,
    *,
    zone_layout_path: Path = ZONE_LAYOUT_FILE,
) -> Simulator:
    """Create and configure the Mini Simulator without advancing its Slicer."""

    if not isinstance(emulator, Emulator):
        raise TypeError("emulator must be an upstream Emulator")
    base = emulator.getLayer("Base")
    if not isinstance(base, Base):
        raise TypeError("mini satellite Internet Base layer must be a Base")
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
    return simulator


def create_mini_satellite_internet_slicer(
    emulator: Emulator,
    *,
    zone_layout_path: Path = ZONE_LAYOUT_FILE,
) -> Slicer:
    """Create the configured Mini Slicer without advancing its Timeline."""

    return create_mini_satellite_internet_simulator(
        emulator,
        zone_layout_path=zone_layout_path,
    ).get_slicer()


def prepare_mini_satellite_internet_planning(
    simulator: Simulator,
    planner: Planner | None = None,
) -> tuple[Planner, tuple[Plan, ...], dict[str, object]]:
    """Create, configure, and fully progress the reference Planner branch."""

    if not isinstance(simulator, Simulator):
        raise TypeError("simulator must be a Simulator")
    slicer = simulator.get_slicer()
    created_planner = planner is None
    if created_planner:
        planner = _create_mini_satellite_internet_planner(simulator)
    elif not isinstance(planner, Planner):
        raise TypeError("planner must be a Planner or None")
    plans = [planner.get_plan() if created_planner else planner.synchronize()]
    while plans[-1].end_slice_id < slicer.get_latest_slice_id():
        plans.append(planner.synchronize())
    configuration = {
        "scenario_identity": "mini_satellite_internet",
        "scenario_display_name": "Mini Satellite Internet",
        "slice_interval_seconds": DEFAULT_SLICE_INTERVAL.total_seconds(),
        "real_slice_count": REAL_SLICE_COUNT,
        "planning_window": "all generated unpublished Differences",
        "planning_through_slice_id": slicer.get_latest_slice_id(),
        "publication_difference_count": PUBLICATION_DIFFERENCE_COUNT,
        "connection_timing": "midpoint",
        "minimum_dwell_seconds": MINIMUM_DWELL_SECONDS,
        "parameter_thresholds": PARAMETER_THRESHOLDS,
        "forwarding_model": "DependencyAwareForwardingPlanningModel",
        "coordination_policy": "DefaultCoordinationPolicy",
    }
    return planner, tuple(plans), configuration


def create_mini_satellite_internet_progression(
    input_directory: Path,
    *,
    emulator: Emulator | None = None,
) -> tuple[Simulator, Planner]:
    """Restore the one shared Mini simulation/progression definition."""

    inputs = Path(input_directory)
    from examples.mini_satellite_internet.emu import (
        build_mini_satellite_internet_emulator,
    )

    if emulator is None:
        emulator = build_mini_satellite_internet_emulator(inputs / "scenario.yaml")
    elif not isinstance(emulator, Emulator):
        raise TypeError("emulator must be an upstream Emulator or None")
    simulator = create_mini_satellite_internet_simulator(
        emulator,
        zone_layout_path=inputs / "zones.yaml",
    )
    return simulator, _create_mini_satellite_internet_planner(simulator)


def _create_mini_satellite_internet_planner(simulator: Simulator) -> Planner:
    """Bind the canonical Planner models without advancing the Slicer."""

    if not isinstance(simulator, Simulator):
        raise TypeError("simulator must be a Simulator")
    return simulator.create_planner(
        planning_window_policy=_PlanAllAvailablePublishFour(),
        connection_model=MidpointConnectionPlanningModel(
            minimum_dwell=timedelta(seconds=MINIMUM_DWELL_SECONDS),
        ),
        parameter_model=AccumulatedDeadbandParameterPlanningModel(
            bandwidth_bps_threshold=PARAMETER_THRESHOLDS["bandwidth_bps"],
            delay_us_threshold=PARAMETER_THRESHOLDS["delay_us"],
            packet_loss_ppm_threshold=PARAMETER_THRESHOLDS[
                "packet_loss_ppm"
            ],
        ),
        forwarding_model=DependencyAwareForwardingPlanningModel(),
        coordination_policy=DefaultCoordinationPolicy(),
    )


__all__ = [
    "create_mini_satellite_internet_simulator",
    "create_mini_satellite_internet_slicer",
    "create_mini_satellite_internet_progression",
]
