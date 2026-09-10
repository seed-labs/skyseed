"""Assemble the canonical Starlink Gen1 PhysicalWorld and Planner roles."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from seedemu.core import Emulator
from seedemu.layers import Base

from skyseed_v2.configuration.simulator import load_zone_layout_definition
from skyseed_v2.emulator.seedemu import get_skyseed_autonomous_systems
from skyseed_v2.emulator_to_simulator import create_simulator_static_input
from skyseed_v2.simulator import (
    AccumulatedDeadbandParameterPlanningModel,
    CircularOrbitPositionModel,
    DefaultZoneMembershipModel,
    DefaultCoordinationPolicy,
    GatewayHopCountRoutePlanningModel,
    GeometricPhysicalLinkModel,
    HighestElevationZoneServicePlanningModel,
    OperationalConnectionKind,
    ParameterKindConfiguration,
    Planner,
    CoverageFirstGroundStationAccessPlanningModel,
    RetainFirstInterSatelliteConfiguration,
    RetainFirstInterSatelliteConnectionPlanningModel,
    SimulationDefinition,
    SimulationTimeline,
    Simulator,
    FrameBatchPlanningWindowPolicy,
)


EXAMPLE_ROOT = Path(__file__).resolve().parent
ZONE_LAYOUT_FILE = EXAMPLE_ROOT / "zones.yaml"
REFERENCE_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)
STARLINK_GEN1_OBSERVATION_INTERVAL = timedelta(seconds=5)
STARLINK_GEN1_DURATION = timedelta(minutes=10)
STARLINK_GEN1_TIMELINE_END = REFERENCE_EPOCH + STARLINK_GEN1_DURATION
STARLINK_GEN1_FORMAL_BOUNDARY_COUNT = (
    STARLINK_GEN1_DURATION // STARLINK_GEN1_OBSERVATION_INTERVAL + 1
)

# Explicit scenario policy and physical parameters; no library default hides them.
MAXIMUM_ISL_DISTANCE_M = 6_000_000.0
GROUND_STATION_MINIMUM_ELEVATION_DEG = 5.0
MAXIMUM_GROUND_STATION_ACCESS_COUNT = 4
GROUND_STATION_MAXIMUM_DISTANCE_M = 3_000_000.0
ZONE_COVERAGE_MINIMUM_ELEVATION_DEG = 25.0
PROPAGATION_SPEED_M_PER_S = 299_792_458.0
BANDWIDTH_BPS_BY_KIND = {
    OperationalConnectionKind.INTER_SATELLITE: 1_000_000_000,
    OperationalConnectionKind.GROUND_STATION_ACCESS: 500_000_000,
}
PACKET_LOSS_PPM_BY_KIND = {
    OperationalConnectionKind.INTER_SATELLITE: 100,
    OperationalConnectionKind.GROUND_STATION_ACCESS: 200,
}
GATEWAY_CANDIDATE_COUNT = 3
GATEWAY_CANDIDATE_MAX_DISTANCE_M = None
STARLINK_GEN1_PLAN_BATCH_FRAMES = 32
STARLINK_GEN1_ACQUISITION_DURATION = timedelta(seconds=5)
STARLINK_GEN1_ISL_MINIMUM_SERVICE = timedelta(seconds=180)
STARLINK_GEN1_GS_MINIMUM_SERVICE = timedelta(seconds=60)
STARLINK_GEN1_DELAY_RELATIVE_THRESHOLD = 0.10
STARLINK_GEN1_PARAMETER_MAX_STALENESS = timedelta(seconds=120)


def create_starlink_gen1_simulator(
    emulator: Emulator,
    *,
    zone_layout_path: Path = ZONE_LAYOUT_FILE,
    observation_interval: timedelta = STARLINK_GEN1_OBSERVATION_INTERVAL,
) -> Simulator:
    """Create the canonical static physical aggregate without advancing time."""

    if not isinstance(emulator, Emulator):
        raise TypeError("emulator must be an upstream Emulator")
    base = emulator.getLayer("Base")
    if not isinstance(base, Base):
        raise TypeError("Starlink Gen1 Base layer must be a Base")
    simulator = Simulator(
        SimulationDefinition(
            static_input=create_simulator_static_input(
                get_skyseed_autonomous_systems(base)
            ),
            timeline=SimulationTimeline(
                start_time=REFERENCE_EPOCH,
                end_time=STARLINK_GEN1_TIMELINE_END,
                observation_interval=observation_interval,
            ),
        )
    )
    simulator.create_physical_world(
        position_model=CircularOrbitPositionModel(),
        zone_layout_definition=load_zone_layout_definition(zone_layout_path),
    )
    return simulator


def create_starlink_gen1_planner(simulator: Simulator) -> Planner:
    """Attach the accepted 2+2/K1 with forward service admission semantic Planner composition."""

    if not isinstance(simulator, Simulator):
        raise TypeError("simulator must be a Simulator")
    parameter_configuration = {
        kind: ParameterKindConfiguration(
            bandwidth_bps=BANDWIDTH_BPS_BY_KIND[kind],
            packet_loss_ppm=PACKET_LOSS_PPM_BY_KIND[kind],
            delay_relative_threshold=STARLINK_GEN1_DELAY_RELATIVE_THRESHOLD,
            max_staleness=STARLINK_GEN1_PARAMETER_MAX_STALENESS,
        )
        for kind in OperationalConnectionKind
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
                    maximum_cross_plane_separation=1,
                    same_plane_neighbor_span=1,
                    cross_plane_neighbor_count=4,
                    acquisition_duration=STARLINK_GEN1_ACQUISITION_DURATION,
                    minimum_service_duration=STARLINK_GEN1_ISL_MINIMUM_SERVICE,
                )
            )
        ),
        ground_station_model=CoverageFirstGroundStationAccessPlanningModel(
            default_ground_station_limit=MAXIMUM_GROUND_STATION_ACCESS_COUNT,
            initial_candidate_count=32,
            acquisition_duration=STARLINK_GEN1_ACQUISITION_DURATION,
            minimum_service_duration=STARLINK_GEN1_GS_MINIMUM_SERVICE,
        ),
        zone_service_model=HighestElevationZoneServicePlanningModel(),
        parameter_model=AccumulatedDeadbandParameterPlanningModel(
            parameter_configuration
        ),
        route_model=GatewayHopCountRoutePlanningModel(
            gateway_candidate_count=GATEWAY_CANDIDATE_COUNT,
            gateway_candidate_max_distance_m=(
                GATEWAY_CANDIDATE_MAX_DISTANCE_M
            ),
        ),
        coordination_policy=DefaultCoordinationPolicy(),
        planning_window_policy=FrameBatchPlanningWindowPolicy(
            frames_per_batch=STARLINK_GEN1_PLAN_BATCH_FRAMES,
        ),
    )


def starlink_gen1_physical_configuration() -> dict[str, object]:
    """Return detached display facts for the exact physical policy."""

    return {
        "physical_input": {
            "owner": "PhysicalWorld",
            "frame": "PhysicalFrame",
            "physical_link_model": "GeometricPhysicalLinkModel",
        },
        "inter_satellite": {
            "maximum_distance_km": MAXIMUM_ISL_DISTANCE_M / 1_000.0,
            "earth_clearance": "exact Earth-clear LOS",
            "candidate_scope": "cyclic same-plane predecessors/successors; K1 planes, four current nearest cross-plane neighbors per endpoint plus incumbents",
        },
        "ground_station_access": {
            "minimum_elevation_deg": GROUND_STATION_MINIMUM_ELEVATION_DEG,
            "maximum_slant_distance_km": (
                GROUND_STATION_MAXIMUM_DISTANCE_M / 1_000.0
            ),
            "default_site_capacity": MAXIMUM_GROUND_STATION_ACCESS_COUNT,
            "capability_basis": "engineering assumption; explicit station capability overrides fallback",
            "initial_candidate_count": 32,
            "search": "nearest physical candidates plus retained/pending; expands under admission/capacity shortage",
        },
        "zone_service": {
            "minimum_elevation_deg": ZONE_COVERAGE_MINIMUM_ELEVATION_DEG,
        },
    }


def starlink_gen1_model_descriptors(
    *, observation_interval: timedelta = STARLINK_GEN1_OBSERVATION_INTERVAL,
) -> dict[str, object]:
    """Describe the exact configured Model roles for detached inspection."""

    parameter_configuration = {
        kind.value: {
            "bandwidth_bps": BANDWIDTH_BPS_BY_KIND[kind],
            "packet_loss_ppm": PACKET_LOSS_PPM_BY_KIND[kind],
            "delay_relative_threshold": STARLINK_GEN1_DELAY_RELATIVE_THRESHOLD,
            "max_staleness_seconds": (
                STARLINK_GEN1_PARAMETER_MAX_STALENESS.total_seconds()
            ),
        }
        for kind in OperationalConnectionKind
    }
    return {
        "source": (
            "examples/starlink_gen1_satellite_internet/simu.py constants and "
            "the same construction arguments used by create_*"
        ),
        "position": {
            "implementation": _type_identity(CircularOrbitPositionModel),
            "configuration": {
                "orbit_source": "inputs/shells.yaml → PhysicalWorld orbit rings",
                "epoch": REFERENCE_EPOCH.isoformat(),
                "coordinate_system": "WGS84 ECEF metres",
            },
        },
        "physical_link": {
            "implementation": _type_identity(GeometricPhysicalLinkModel),
            "configuration": {
                "maximum_isl_distance_m": MAXIMUM_ISL_DISTANCE_M,
                "earth_clearance": "exact Earth-clear line of sight",
                "ground_station_minimum_elevation_deg": (
                    GROUND_STATION_MINIMUM_ELEVATION_DEG
                ),
                "ground_station_maximum_distance_m": (
                    GROUND_STATION_MAXIMUM_DISTANCE_M
                ),
                "zone_coverage_minimum_elevation_deg": (
                    ZONE_COVERAGE_MINIMUM_ELEVATION_DEG
                ),
                "propagation_speed_m_per_s": PROPAGATION_SPEED_M_PER_S,
            },
        },
        "zone_membership": {
            "implementation": _type_identity(DefaultZoneMembershipModel),
            "configuration": {
                "geometry_source": "inputs/zones.yaml rectangles",
                "physical_matches": "all containing Zones ordered by Zone ID",
                "authoritative_overlap_resolution": (
                    "highest priority, nearest representative center, Zone ID tie"
                ),
            },
        },
        "isl_selection": {
            "implementation": _type_identity(
                RetainFirstInterSatelliteConnectionPlanningModel
            ),
            "configuration": {
                "candidate_scope": "cyclic same-plane predecessors/successors; K1 planes, four current nearest cross-plane neighbors per endpoint plus incumbents",
                "maximum_cross_plane_separation": 1,
                "same_plane_neighbor_span": 1,
                "cross_plane_neighbor_count": 4,
                "desired_same_plane_links": 2,
                "desired_cross_plane_links": 2,
                "acquisition_duration_seconds": (
                    STARLINK_GEN1_ACQUISITION_DURATION.total_seconds()
                ),
                "capacity_source": (
                    "Satellite InterSatelliteLinkCapability; maximum 4"
                ),
                "selection_order": "retain-first deterministic policy order",
                "minimum_service_seconds": STARLINK_GEN1_ISL_MINIMUM_SERVICE.total_seconds(),
            },
        },
        "ground_station": {
            "implementation": _type_identity(
                CoverageFirstGroundStationAccessPlanningModel
            ),
            "configuration": {
                "objective": (
                    "maximum GS coverage, distinct satellites, total connections, then continuity and "
                    "deterministic elevation/rank quality"
                ),
                "default_ground_station_limit": (
                    MAXIMUM_GROUND_STATION_ACCESS_COUNT
                ),
                "station_capacity": (
                    "per-GroundStation configured maximum or default limit"
                ),
                "satellite_capacity": (
                    "Satellite.max_simultaneous_ground_station_accesses"
                ),
                "tie_semantics": "canonical station/satellite order",
                "preparation_seconds": STARLINK_GEN1_ACQUISITION_DURATION.total_seconds(),
                "minimum_service_seconds": STARLINK_GEN1_GS_MINIMUM_SERVICE.total_seconds(),
            },
        },
        "zone_service": {
            "implementation": _type_identity(
                HighestElevationZoneServicePlanningModel
            ),
            "configuration": {
                "selection": "highest elevation, deterministic Satellite tie",
                "unserved": "explicit unserved assignment",
            },
        },
        "parameter": {
            "implementation": _type_identity(
                AccumulatedDeadbandParameterPlanningModel
            ),
            "configuration": parameter_configuration,
        },
        "route": {
            "implementation": _type_identity(GatewayHopCountRoutePlanningModel),
            "configuration": {
                "objective": "deterministic demand-bounded hop count",
                "gateway_candidate_count": GATEWAY_CANDIDATE_COUNT,
                "gateway_candidate_max_distance_m": (
                    GATEWAY_CANDIDATE_MAX_DISTANCE_M
                ),
            },
        },
        "coordination": {
            "implementation": _type_identity(DefaultCoordinationPolicy),
            "configuration": {
                "ownership": "precedence relationships only",
            },
        },
        "planning_window": {
            "implementation": _type_identity(
                FrameBatchPlanningWindowPolicy
            ),
            "configuration": {
                "observation_interval_seconds": (
                    observation_interval.total_seconds()
                ),
                "frames_per_batch": STARLINK_GEN1_PLAN_BATCH_FRAMES,
                "derived_prehistory_seconds": (
                    STARLINK_GEN1_ACQUISITION_DURATION.total_seconds()
                ),
            },
        },
    }


def starlink_gen1_planning_configuration(
    simulator: Simulator,
) -> dict[str, object]:
    """Describe the final public Planner policy without advancing it."""

    if not isinstance(simulator, Simulator):
        raise TypeError("simulator must be a Simulator")
    frontiers = simulator.get_planner().get_frontiers()
    timeline = simulator.get_timeline()
    return {
        "scenario_identity": "starlink_gen1_satellite_internet",
        "observation_interval_seconds": (
            simulator.get_timeline().observation_interval.total_seconds()
        ),
        "formal_boundary_count": (timeline.end_time - timeline.start_time) // timeline.observation_interval + 1,
        "frames_per_batch": STARLINK_GEN1_PLAN_BATCH_FRAMES,
        "acquisition_duration_seconds": (
            STARLINK_GEN1_ACQUISITION_DURATION.total_seconds()
        ),
        "isl_policy": "retain-first 2 same-plane + 2 cross-plane / K1",
        "ground_station_policy": "GS coverage / distinct satellites / connections / continuity / quality",
        "zone_policy": "highest elevation with explicit unserved state",
        "parameter_policy": "accumulated delay deadband",
        "route_policy": "gateway demand-bounded hop count",
        "coordination_policy": "DefaultCoordinationPolicy",
        "frontiers": {
            "physical_observation": _optional_timestamp(
                frontiers.physical_observation
            ),
            "planning": _optional_timestamp(frontiers.planning),
            "publication": _optional_timestamp(frontiers.publication),
        },
    }


def _optional_timestamp(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _type_identity(value: type) -> str:
    return f"{value.__module__}.{value.__qualname__}"


__all__ = [
    "create_starlink_gen1_planner",
    "create_starlink_gen1_simulator",
    "starlink_gen1_physical_configuration",
    "starlink_gen1_model_descriptors",
    "starlink_gen1_planning_configuration",
]
