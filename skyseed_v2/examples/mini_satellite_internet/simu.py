"""Assemble the Mini PhysicalWorld and authoritative Network Planner."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

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
    QualityFirstGroundStationAccessPlanningModel,
    RetainFirstInterSatelliteConfiguration,
    RetainFirstInterSatelliteConnectionPlanningModel,
    SimulationDefinition,
    SimulationTimeline,
    Simulator,
    TimeHorizonPlanningWindowPolicy,
)


EXAMPLE_ROOT = Path(__file__).resolve().parent
DEFAULT_START_TIME = datetime(2026, 1, 1, 0, 0, 30, tzinfo=timezone.utc)
DEFAULT_OBSERVATION_INTERVAL = timedelta(seconds=10)
FORMAL_BOUNDARY_COUNT = 64
DEFAULT_END_TIME = (
    DEFAULT_START_TIME
    + (FORMAL_BOUNDARY_COUNT - 1) * DEFAULT_OBSERVATION_INTERVAL
)
ZONE_LAYOUT_FILE = EXAMPLE_ROOT / "zones.yaml"

MAXIMUM_ISL_DISTANCE_M = 6_000_000.0
GROUND_STATION_MINIMUM_ELEVATION_DEG = 5.0
MAXIMUM_GROUND_STATION_ACCESS_COUNT = 2
GROUND_STATION_MAXIMUM_DISTANCE_M = 3_000_000.0
ZONE_COVERAGE_MINIMUM_ELEVATION_DEG = 10.0
PROPAGATION_SPEED_M_PER_S = 299_792_458.0
PLANNING_HORIZON = timedelta(seconds=50)
PUBLICATION_HORIZON = timedelta(seconds=20)
ACQUISITION_DURATION = timedelta(seconds=20)


def create_mini_satellite_internet_simulator(
    emulator: Emulator,
    *,
    zone_layout_path: Path = ZONE_LAYOUT_FILE,
) -> Simulator:
    """Create the Mini PhysicalWorld without advancing formal time."""

    if not isinstance(emulator, Emulator):
        raise TypeError("emulator must be an upstream Emulator")
    base = emulator.getLayer("Base")
    if not isinstance(base, Base):
        raise TypeError("Mini Satellite Internet Base layer must be a Base")
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


def create_mini_satellite_internet_planner(
    simulator: Simulator,
) -> Planner:
    """Attach the explicit 2+2/K3 reference planning composition."""

    if not isinstance(simulator, Simulator):
        raise TypeError("simulator must be a Simulator")
    parameter_configuration = {
        OperationalConnectionKind.INTER_SATELLITE: (
            ParameterKindConfiguration(
                bandwidth_bps=1_000_000_000,
                packet_loss_ppm=100,
                delay_deadband_us=250,
                persistence=timedelta(0),
            )
        ),
        OperationalConnectionKind.GROUND_STATION_ACCESS: (
            ParameterKindConfiguration(
                bandwidth_bps=500_000_000,
                packet_loss_ppm=200,
                delay_deadband_us=250,
                persistence=timedelta(0),
            )
        ),
    }
    return simulator.create_planner(
        physical_link_model=GeometricPhysicalLinkModel(
            maximum_isl_distance_m=MAXIMUM_ISL_DISTANCE_M,
            ground_station_minimum_elevation_deg=(
                GROUND_STATION_MINIMUM_ELEVATION_DEG
            ),
            ground_station_maximum_distance_m=(
                GROUND_STATION_MAXIMUM_DISTANCE_M
            ),
            zone_coverage_minimum_elevation_deg=(
                ZONE_COVERAGE_MINIMUM_ELEVATION_DEG
            ),
            propagation_speed_m_per_s=PROPAGATION_SPEED_M_PER_S,
        ),
        inter_satellite_model=(
            RetainFirstInterSatelliteConnectionPlanningModel(
                RetainFirstInterSatelliteConfiguration(
                    desired_same_plane_links=2,
                    desired_cross_plane_links=2,
                    maximum_cross_plane_separation=3,
                    acquisition_duration=ACQUISITION_DURATION,
                )
            )
        ),
        ground_station_model=QualityFirstGroundStationAccessPlanningModel(
            default_ground_station_limit=(
                MAXIMUM_GROUND_STATION_ACCESS_COUNT
            )
        ),
        zone_service_model=HighestElevationZoneServicePlanningModel(),
        parameter_model=AccumulatedDeadbandParameterPlanningModel(
            parameter_configuration
        ),
        route_model=GatewayHopCountRoutePlanningModel(
            gateway_candidate_count=1
        ),
        coordination_policy=DefaultCoordinationPolicy(),
        planning_window_policy=TimeHorizonPlanningWindowPolicy(
            planning_horizon=PLANNING_HORIZON,
            publication_horizon=PUBLICATION_HORIZON,
            maximum_prehistory=ACQUISITION_DURATION,
        ),
    )


def create_mini_satellite_internet_producer(
    input_directory: Path,
    *,
    emulator: Emulator | None = None,
    evidence_path: Path | None = None,
) -> tuple[Simulator, _IncrementalRealizationProducer]:
    """Create the one incremental producer used by precomputed execution."""

    inputs = Path(input_directory)
    from examples.mini_satellite_internet.emu import (
        build_mini_satellite_internet_emulator,
    )

    if emulator is None:
        emulator = build_mini_satellite_internet_emulator(
            inputs / "scenario.yaml"
        )
    elif not isinstance(emulator, Emulator):
        raise TypeError("emulator must be an upstream Emulator or None")
    simulator = create_mini_satellite_internet_simulator(
        emulator,
        zone_layout_path=inputs / "zones.yaml",
    )
    planner = create_mini_satellite_internet_planner(simulator)
    producer = _IncrementalRealizationProducer(
        simulator.get_timeline(),
        simulator.get_physical_world(),
        simulator.get_physical_link_model(),
        planner,
        evidence_path=evidence_path,
    )
    return simulator, producer


def drain_mini_satellite_internet(
    simulator: Simulator,
    producer: _IncrementalRealizationProducer,
) -> tuple[
    tuple[Plan, ...],
    tuple[BpfCompiledRealization, ...],
    dict[str, object],
]:
    """Drain the supplied common producer and deterministically close it."""

    if not isinstance(simulator, Simulator):
        raise TypeError("simulator must be a Simulator")
    plans = []
    realizations = []
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


def mini_satellite_internet_configuration() -> dict[str, object]:
    """Return the detached immutable policy facts shown by Tools."""

    return {
        "scenario_identity": "mini_satellite_internet",
        "observation_interval_seconds": (
            DEFAULT_OBSERVATION_INTERVAL.total_seconds()
        ),
        "formal_boundary_count": FORMAL_BOUNDARY_COUNT,
        "planning_horizon_seconds": PLANNING_HORIZON.total_seconds(),
        "publication_horizon_seconds": PUBLICATION_HORIZON.total_seconds(),
        "acquisition_duration_seconds": ACQUISITION_DURATION.total_seconds(),
        "isl_policy": "retain-first 2 same-plane + 2 cross-plane / K3",
        "ground_station_policy": "quality-first allocation",
        "zone_policy": "highest elevation with explicit unserved state",
        "parameter_policy": "accumulated delay deadband",
        "route_policy": "gateway demand-bounded hop count",
    }


__all__ = [
    "create_mini_satellite_internet_planner",
    "create_mini_satellite_internet_producer",
    "create_mini_satellite_internet_simulator",
    "drain_mini_satellite_internet",
    "mini_satellite_internet_configuration",
]
