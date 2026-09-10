"""Run the complete example through PhysicalWorld, Planner, and Compiler."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys


EXAMPLE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = EXAMPLE_ROOT.parents[1]

if __package__ in (None, ""):
    sys.path.insert(0, str(PROJECT_ROOT))

from examples.complete_static_emulator.emu import build_emulator
from seedemu.core import Emulator
from seedemu.layers import Base
from skyseed_v2.backend.bpf import BpfCompiledRealization
from skyseed_v2.configuration.simulator import load_zone_layout_definition
from skyseed_v2.emulator.seedemu import get_skyseed_autonomous_systems
from skyseed_v2.emulator_to_simulator import create_simulator_static_input
from skyseed_v2.experiment._realization_producer import (
    _IncrementalRealizationProducer,
)
from skyseed_v2.simulator import (
    AccumulatedDeadbandParameterPlanningModel,
    CircularOrbitPositionModel,
    DefaultCoordinationPolicy,
    GatewayHopCountRoutePlanningModel,
    GeometricPhysicalLinkModel,
    HighestElevationZoneServicePlanningModel,
    OperationalConnectionKind,
    ParameterKindConfiguration,
    Plan,
    Planner,
    CoverageFirstGroundStationAccessPlanningModel,
    RetainFirstInterSatelliteConfiguration,
    RetainFirstInterSatelliteConnectionPlanningModel,
    SimulationDefinition,
    SimulationTimeline,
    Simulator,
    FrameBatchPlanningWindowPolicy,
)


DEFAULT_START_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)
DEFAULT_END_TIME = DEFAULT_START_TIME + timedelta(seconds=30)
DEFAULT_OBSERVATION_INTERVAL = timedelta(seconds=10)
ZONE_LAYOUT_FILE = EXAMPLE_ROOT / "zones.yaml"


def create_complete_static_simulator(
    emulator: Emulator,
    *,
    zone_layout_path: Path = ZONE_LAYOUT_FILE,
) -> Simulator:
    """Materialize the example's immutable PhysicalWorld."""

    if not isinstance(emulator, Emulator):
        raise TypeError("emulator must be an upstream Emulator")
    base = emulator.getLayer("Base")
    if not isinstance(base, Base):
        raise TypeError("complete example Base layer must be a Base")
    simulator = Simulator(
        SimulationDefinition(
            static_input=create_simulator_static_input(
                get_skyseed_autonomous_systems(base)
            ),
            timeline=SimulationTimeline(
                start_time=DEFAULT_START_TIME,
                end_time=DEFAULT_END_TIME,
                observation_interval=DEFAULT_OBSERVATION_INTERVAL,
            ),
        )
    )
    simulator.create_physical_world(
        position_model=CircularOrbitPositionModel(),
        zone_layout_definition=load_zone_layout_definition(zone_layout_path),
    )
    return simulator


def create_complete_static_planner(simulator: Simulator) -> Planner:
    """Attach explicit reference planning roles for the small topology."""

    if not isinstance(simulator, Simulator):
        raise TypeError("simulator must be a Simulator")
    parameters = {
        OperationalConnectionKind.INTER_SATELLITE: (
            ParameterKindConfiguration(
                bandwidth_bps=1_000_000_000,
                packet_loss_ppm=100,
                delay_relative_threshold=0.10,
            )
        ),
        OperationalConnectionKind.GROUND_STATION_ACCESS: (
            ParameterKindConfiguration(
                bandwidth_bps=500_000_000,
                packet_loss_ppm=200,
                delay_relative_threshold=0.10,
            )
        ),
    }
    return simulator.create_planner(
        physical_link_model=GeometricPhysicalLinkModel(
            maximum_isl_distance_m=5_000_000.0,
            ground_station_minimum_elevation_deg=0.0,
            ground_station_maximum_distance_m=None,
            zone_coverage_minimum_elevation_deg=30.0,
            propagation_speed_m_per_s=299_792_458.0,
        ),
        inter_satellite_model=(
            RetainFirstInterSatelliteConnectionPlanningModel(
                RetainFirstInterSatelliteConfiguration(
                    desired_same_plane_links=1,
                    desired_cross_plane_links=0,
                    maximum_cross_plane_separation=0,
                    acquisition_duration=timedelta(0),
                )
            )
        ),
        ground_station_model=CoverageFirstGroundStationAccessPlanningModel(
            default_ground_station_limit=1
        ),
        zone_service_model=HighestElevationZoneServicePlanningModel(),
        parameter_model=AccumulatedDeadbandParameterPlanningModel(parameters),
        route_model=GatewayHopCountRoutePlanningModel(
            gateway_candidate_count=1
        ),
        coordination_policy=DefaultCoordinationPolicy(),
        planning_window_policy=FrameBatchPlanningWindowPolicy(
            frames_per_batch=32,
        ),
    )


def run_complete_static_chain(
    simulator: Simulator,
    *,
    evidence_path: Path | None = None,
    planner: Planner | None = None,
) -> tuple[
    tuple[Plan, ...],
    tuple[BpfCompiledRealization, ...],
    dict[str, object],
]:
    """Drain the one common producer for the bounded example Timeline."""

    if not isinstance(simulator, Simulator):
        raise TypeError("simulator must be a Simulator")
    active_planner = (
        create_complete_static_planner(simulator)
        if planner is None
        else planner
    )
    if not isinstance(active_planner, Planner):
        raise TypeError("planner must be a Planner or None")
    plans = []
    realizations = []
    producer = _IncrementalRealizationProducer(
        simulator.get_timeline(),
        simulator.get_physical_world(),
        simulator.get_physical_link_model(),
        active_planner,
        evidence_path=evidence_path,
    )
    try:
        while True:
            realization = producer.produce_next_realization()
            if realization is None:
                break
            plans.append(producer.latest_plan)
            realizations.append(realization)
        snapshot = producer.snapshot()
    finally:
        producer.close()
    return tuple(plans), tuple(realizations), snapshot


def main() -> None:
    """Print a concise proof of the complete public progression."""

    simulator = create_complete_static_simulator(build_emulator())
    plans, realizations, snapshot = run_complete_static_chain(simulator)
    transition_count = sum(len(plan.transitions) for plan in plans)
    activation_count = sum(
        len(realization.activation_timestamps)
        for realization in realizations
    )
    print(
        "PhysicalWorld -> PhysicalLinkObservation -> Planner -> Plan -> "
        "Compiler"
    )
    print(
        f"formal frames={snapshot['formal_frame_count']}, "
        f"Plans={len(plans)}, transitions={transition_count}, "
        f"realizations={len(realizations)}, "
        f"activation boundaries={activation_count}"
    )


if __name__ == "__main__":
    main()


__all__ = [
    "create_complete_static_planner",
    "create_complete_static_simulator",
    "run_complete_static_chain",
]
