"""Assemble the one formal Starlink Gen1 Simulator, Slicer, and Planner composition."""

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
    ConnectionKind,
    DependencyAwareForwardingPlanningModel,
    GatewayHopCountRouteModel,
    GeometricConnectionModel,
    ParameterFieldConfiguration,
    ParameterPlanningConfiguration,
    Planner,
    SampleBoundaryConnectionPlanningModel,
    SimulationDefinition,
    SimulationTimeline,
    Simulator,
    TimeHorizonPlanningWindowPolicy,
)


EXAMPLE_ROOT = Path(__file__).resolve().parent
ZONE_LAYOUT_FILE = EXAMPLE_ROOT / "zones.yaml"
REFERENCE_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)
STARLINK_GEN1_SLICE_INTERVAL = timedelta(seconds=5)
STARLINK_GEN1_DURATION = timedelta(minutes=10)
STARLINK_GEN1_TIMELINE_END = REFERENCE_EPOCH + STARLINK_GEN1_DURATION
STARLINK_GEN1_REAL_SLICE_COUNT = 121

# These are explicit Starlink Gen1 scenario policies, not generic library defaults or
# claims that every real Gateway site uses identical hardware.
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

# The accepted normal Starlink Gen1 Planner composition uses one 50-second planning
# lookahead and publishes one continuous prefix of at most 20 seconds.
STARLINK_GEN1_PLANNING_LOOKAHEAD = timedelta(seconds=50)
STARLINK_GEN1_PUBLICATION_HORIZON = timedelta(seconds=20)
STARLINK_GEN1_PARAMETER_CONFIGURATION = ParameterPlanningConfiguration(
    delay_us=ParameterFieldConfiguration(
        absolute_deadband=191,
        relative_deadband=0.024067890929326655,
        persistence=timedelta(seconds=10),
        max_staleness=timedelta(seconds=30),
    ),
    bundle_drifted_fields=True,
)


class _IdentityCoordinationPolicy:
    """Keep the reference example's three Model results unchanged."""

    __slots__ = ()

    def coordinate_transitions(
        self,
        *,
        connection_transitions,
        parameter_transitions,
        forwarding_transitions,
        **_unused,
    ):
        return (
            connection_transitions,
            parameter_transitions,
            forwarding_transitions,
            (),
        )


def create_starlink_gen1_simulator(
    emulator: Emulator,
    *,
    zone_layout_path: Path = ZONE_LAYOUT_FILE,
) -> Simulator:
    """Create the one Starlink Gen1 Topology/Slicer composition without advancing it."""

    if not isinstance(emulator, Emulator):
        raise TypeError("emulator must be an upstream Emulator")
    base = emulator.getLayer("Base")
    if not isinstance(base, Base):
        raise TypeError("Starlink Gen1 Base layer must be a Base")
    static_input = create_simulator_static_input(
        get_skyseed_autonomous_systems(base)
    )
    simulator = Simulator(
        SimulationDefinition(
            static_input=static_input,
            timeline=SimulationTimeline(
                start_time=REFERENCE_EPOCH,
                end_time=STARLINK_GEN1_TIMELINE_END,
                slice_interval=STARLINK_GEN1_SLICE_INTERVAL,
            ),
        )
    )
    simulator.create_topology(
        position_model=CircularOrbitPositionModel(),
        zone_layout_definition=load_zone_layout_definition(zone_layout_path)
    )
    simulator.create_slicer(
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


def create_starlink_gen1_planner(simulator: Simulator) -> Planner:
    """Attach the one accepted normal Planner composition."""

    if not isinstance(simulator, Simulator):
        raise TypeError("simulator must be a Simulator")
    return simulator.create_planner(
        planning_window_policy=TimeHorizonPlanningWindowPolicy(
            planning_lookahead=STARLINK_GEN1_PLANNING_LOOKAHEAD,
            publication_horizon=STARLINK_GEN1_PUBLICATION_HORIZON,
        ),
        connection_model=SampleBoundaryConnectionPlanningModel(),
        parameter_model=AccumulatedDeadbandParameterPlanningModel(
            configuration=STARLINK_GEN1_PARAMETER_CONFIGURATION,
        ),
        forwarding_model=DependencyAwareForwardingPlanningModel(),
        coordination_policy=_IdentityCoordinationPolicy(),
    )


def create_starlink_gen1_runtime_progression(
    input_directory: Path,
) -> tuple[Simulator, Planner]:
    """Restore the generated Starlink Gen1 progression without advancing it."""

    inputs = Path(input_directory)
    from examples.starlink_gen1_satellite_internet.emu import (
        build_starlink_gen1_satellite_internet_emulator,
    )

    emulator = build_starlink_gen1_satellite_internet_emulator(
        inputs / "scenario.yaml"
    )
    simulator = create_starlink_gen1_simulator(
        emulator,
        zone_layout_path=inputs / "zones.yaml",
    )
    return simulator, create_starlink_gen1_planner(simulator)


def starlink_gen1_slicing_configuration() -> dict[str, object]:
    """Project the temporary legacy Slicer policy context for inspection."""

    return {
        "physical_input": {
            "owner": "Topology",
            "value": "PhysicalFrame",
            "position_model": "CircularOrbitPositionModel",
            "identity": "timestamp",
        },
        "legacy_models": {
            "connection": {
                "selected": "GeometricConnectionModel",
                "role": (
                    "selects effective ISL, GroundStation access, and Zone "
                    "service facts from current formal geometry"
                ),
                "configuration": (
                    "distance/elevation, Earth-clear LOS, and access caps "
                    "shown below"
                ),
            },
            "route": {
                "selected": "GatewayHopCountRouteModel",
                "role": (
                    "publishes sparse next hops for current Gateway/Zone "
                    "demand on the effective Connection graph"
                ),
                "configuration": (
                    f"{GATEWAY_CANDIDATE_COUNT} static Gateway candidates "
                    "per Zone; minimum hop count"
                ),
            },
        },
        "zone_service": {
            "minimum_elevation_deg": ZONE_COVERAGE_MINIMUM_ELEVATION_DEG,
        },
        "ground_station_access": {
            "minimum_elevation_deg": GROUND_STATION_MINIMUM_ELEVATION_DEG,
            "maximum_slant_distance_km": (
                GROUND_STATION_MAXIMUM_DISTANCE_M / 1_000.0
            ),
            "ground_station_capacity": MAXIMUM_GROUND_STATION_ACCESS_COUNT,
        },
        "isl": {
            "same_orbit_policy": "fixed predecessor/successor ring",
            "cross_plane_policy": "RAAN-neighbour fixed cyclic pairing",
            "maximum_distance_km": MAXIMUM_ISL_DISTANCE_M / 1_000.0,
            "earth_clearance": "exact Earth-clear LOS required",
        },
        "routing": {
            "model": "GatewayHopCountRouteModel",
            "gateway_candidates_per_zone": GATEWAY_CANDIDATE_COUNT,
            "metric": "minimum hop count on the published Connection graph",
        },
        "interpretation_notes": [
            (
                "Unserved Zones are expected: this workload maps a global "
                "732-Zone surface onto one selected Starlink Gen1 shell."
            ),
            (
                "The 25-degree Zone-service minimum rejects distant, "
                "low-elevation service merely used to increase nominal "
                "coverage."
            ),
            (
                "Cross-plane ISLs remain subject to the configured neighbouring "
                "plane geometry and exact Earth-clear LOS."
            ),
            (
                "A long formal Route can remain the minimum-hop path under "
                "the published access-capacity and ISL graph."
            ),
        ],
    }


def starlink_gen1_planning_configuration(simulator: Simulator) -> dict[str, object]:
    """Describe the exact accepted normal Planner composition."""

    if not isinstance(simulator, Simulator):
        raise TypeError("simulator must be a Simulator")
    delay_configuration = STARLINK_GEN1_PARAMETER_CONFIGURATION.delay_us
    latest_slice_id = simulator.get_slicer().get_latest_slice_id()
    return {
        "scenario_identity": "starlink_gen1_satellite_internet",
        "scenario_display_name": "Starlink Gen1 Satellite Internet",
        "numeric_defaults_status": (
            "reference oracle; no optimized Planner numeric defaults selected"
        ),
        "slice_interval_seconds": STARLINK_GEN1_SLICE_INTERVAL.total_seconds(),
        "timeline_real_slice_count": STARLINK_GEN1_REAL_SLICE_COUNT,
        "execution_through_slice_id": latest_slice_id,
        "planning_through_slice_id": latest_slice_id,
        "planning_window": "bounded exact time prefix",
        "planning_window_policy": "TimeHorizonPlanningWindowPolicy",
        "planning_lookahead_seconds": STARLINK_GEN1_PLANNING_LOOKAHEAD.total_seconds(),
        "publication_horizon_seconds": STARLINK_GEN1_PUBLICATION_HORIZON.total_seconds(),
        "connection_model": "SampleBoundaryConnectionPlanningModel",
        "connection_timing": "sample boundary",
        "connection_episode_omission": "disabled",
        "parameter_model": "AccumulatedDeadbandParameterPlanningModel",
        "parameter_delay_us": {
            "absolute_deadband": delay_configuration.absolute_deadband,
            "relative_deadband": delay_configuration.relative_deadband,
            "persistence_seconds": delay_configuration.persistence.total_seconds(),
            "max_staleness_seconds": delay_configuration.max_staleness.total_seconds(),
        },
        "parameter_bundle_drifted_fields": True,
        "forwarding_model": "DependencyAwareForwardingPlanningModel",
        "forwarding_stability": "always FOLLOW",
        "coordination_policy": "_IdentityCoordinationPolicy",
        "connection_suppression": "disabled",
    }


__all__ = [
    "create_starlink_gen1_planner",
    "create_starlink_gen1_simulator",
    "starlink_gen1_planning_configuration",
    "starlink_gen1_slicing_configuration",
]
