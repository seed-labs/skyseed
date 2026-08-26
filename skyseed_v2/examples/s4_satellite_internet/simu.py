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
    AccumulatedDeadbandParameterPlanningModel,
    AllAvailablePlanningWindowPolicy,
    CircularOrbitPositionModel,
    ConnectionEpisodeConfiguration,
    ConnectionKind,
    DefaultCoordinationPolicy,
    DependencyAwareForwardingPlanningModel,
    ForwardingStabilityConfiguration,
    GatewayHopCountRouteModel,
    GeometricConnectionModel,
    MidpointConnectionPlanningModel,
    ParameterFieldConfiguration,
    ParameterPlanningConfiguration,
    Planner,
    SampleBoundaryConnectionPlanningModel,
    SampleBoundaryParameterPlanningModel,
    SimulationDefinition,
    SimulationTimeline,
    Simulator,
    StableForwardingPlanningModel,
    TimeHorizonPlanningWindowPolicy,
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
REFERENCE_GLOBAL_PROFILE = "reference-global"
REFERENCE_BOUNDED_PROFILE = "reference-bounded"
EXPERIMENTAL_STABLE_GLOBAL_PROFILE = "experimental-stable-global"
STABLE_BOUNDED_PROFILE = "stable-bounded"
S4_PLANNER_PROFILES = (
    REFERENCE_GLOBAL_PROFILE,
    REFERENCE_BOUNDED_PROFILE,
    EXPERIMENTAL_STABLE_GLOBAL_PROFILE,
    STABLE_BOUNDED_PROFILE,
)

# These S4 scenario values remain an evidence-derived candidate, not generic
# library defaults. Numeric tuning remains outside the canonical run.
S4_PUBLICATION_HORIZON = timedelta(seconds=20)
S4_BOUNDED_PLANNING_LOOKAHEAD = timedelta(seconds=50)
S4_CONNECTION_CONFIGURATION = ConnectionEpisodeConfiguration(
    {
        # The measured minimum completed GSA midpoint episode was 5 seconds.
        ConnectionKind.GROUND_STATION_ACCESS: timedelta(seconds=5),
    }
)
S4_PARAMETER_CONFIGURATION = ParameterPlanningConfiguration(
    delay_us=ParameterFieldConfiguration(
        # Measured reference medians: 191 us absolute and 2.406789% relative.
        absolute_deadband=191,
        relative_deadband=0.024067890929326655,
        persistence=timedelta(seconds=10),
        max_staleness=timedelta(seconds=30),
    ),
    bundle_drifted_fields=True,
)
S4_FORWARDING_CONFIGURATION = ForwardingStabilityConfiguration(
    # Measured medians for supported prior paths.
    minimum_hop_gain=1,
    minimum_delay_gain_us=2_477,
    persistence=timedelta(seconds=10),
    # Measured p95 benefits bypass persistence; HOLD remains capped at 30 s.
    immediate_hop_gain=11,
    immediate_delay_gain_us=92_595,
    maximum_hold=timedelta(seconds=30),
)


class _IdentityCoordinationPolicy:
    """Keep the normal example's three Model results unchanged."""

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


def create_s4_planner(
    simulator: Simulator,
    *,
    profile: str | None = None,
) -> Planner:
    """Create the normal S4 Planner or an explicit diagnostic profile."""

    if not isinstance(simulator, Simulator):
        raise TypeError("simulator must be a Simulator")
    if profile is None:
        return simulator.create_planner(
            planning_window_policy=TimeHorizonPlanningWindowPolicy(
                planning_lookahead=S4_BOUNDED_PLANNING_LOOKAHEAD,
                publication_horizon=S4_PUBLICATION_HORIZON,
            ),
            connection_model=SampleBoundaryConnectionPlanningModel(),
            parameter_model=AccumulatedDeadbandParameterPlanningModel(
                configuration=S4_PARAMETER_CONFIGURATION,
            ),
            forwarding_model=DependencyAwareForwardingPlanningModel(),
            coordination_policy=_IdentityCoordinationPolicy(),
        )
    _require_planner_profile(profile)
    if profile == REFERENCE_GLOBAL_PROFILE:
        return simulator.create_planner(
            planning_window_policy=AllAvailablePlanningWindowPolicy(),
            connection_model=SampleBoundaryConnectionPlanningModel(),
            parameter_model=SampleBoundaryParameterPlanningModel(),
            forwarding_model=DependencyAwareForwardingPlanningModel(),
            coordination_policy=DefaultCoordinationPolicy(),
        )
    if profile == REFERENCE_BOUNDED_PROFILE:
        return simulator.create_planner(
            planning_window_policy=TimeHorizonPlanningWindowPolicy(
                planning_lookahead=(
                    S4_BOUNDED_PLANNING_LOOKAHEAD
                ),
                publication_horizon=S4_PUBLICATION_HORIZON,
            ),
            connection_model=SampleBoundaryConnectionPlanningModel(),
            parameter_model=SampleBoundaryParameterPlanningModel(),
            forwarding_model=DependencyAwareForwardingPlanningModel(),
            coordination_policy=DefaultCoordinationPolicy(),
        )
    planning_lookahead = (
        S4_BOUNDED_PLANNING_LOOKAHEAD
        if profile == STABLE_BOUNDED_PROFILE
        else None
    )
    return simulator.create_planner(
        planning_window_policy=TimeHorizonPlanningWindowPolicy(
            planning_lookahead=planning_lookahead,
            publication_horizon=S4_PUBLICATION_HORIZON,
        ),
        connection_model=MidpointConnectionPlanningModel(
            episode_configuration=S4_CONNECTION_CONFIGURATION,
        ),
        parameter_model=AccumulatedDeadbandParameterPlanningModel(
            configuration=S4_PARAMETER_CONFIGURATION,
        ),
        forwarding_model=StableForwardingPlanningModel(
            configuration=S4_FORWARDING_CONFIGURATION,
        ),
        coordination_policy=DefaultCoordinationPolicy(),
    )


def s4_planning_configuration(
    simulator: Simulator,
    *,
    profile: str | None = None,
) -> dict[str, object]:
    """Describe the exact normal composition or diagnostic profile."""

    if not isinstance(simulator, Simulator):
        raise TypeError("simulator must be a Simulator")
    common = {
        "scenario_identity": "s4_satellite_internet",
        "scenario_display_name": "S4 Satellite Internet",
        "numeric_defaults_status": (
            "S4 scenario candidate; no generic Planner defaults selected"
        ),
        "slice_interval_seconds": DYNAMIC_SLICE_INTERVAL.total_seconds(),
        "timeline_real_slice_count": DYNAMIC_REAL_SLICE_COUNT,
        "qualification_target_slice_id": (
            simulator.get_slicer().get_latest_slice_id()
        ),
        "planning_window": "all generated unpublished Differences",
        "planning_through_slice_id": (
            simulator.get_slicer().get_latest_slice_id()
        ),
    }
    if profile is None:
        delay_configuration = S4_PARAMETER_CONFIGURATION.delay_us
        return {
            **common,
            "planning_window": "bounded exact time prefix",
            "planning_window_policy": "TimeHorizonPlanningWindowPolicy",
            "planning_lookahead_seconds": (
                S4_BOUNDED_PLANNING_LOOKAHEAD.total_seconds()
            ),
            "publication_horizon_seconds": (
                S4_PUBLICATION_HORIZON.total_seconds()
            ),
            "connection_model": "SampleBoundaryConnectionPlanningModel",
            "connection_timing": "sample boundary",
            "connection_episode_omission": "disabled",
            "parameter_model": "AccumulatedDeadbandParameterPlanningModel",
            "parameter_delay_us": {
                "absolute_deadband": delay_configuration.absolute_deadband,
                "relative_deadband": delay_configuration.relative_deadband,
                "persistence_seconds": (
                    delay_configuration.persistence.total_seconds()
                ),
                "max_staleness_seconds": (
                    delay_configuration.max_staleness.total_seconds()
                ),
            },
            "parameter_bundle_drifted_fields": True,
            "forwarding_model": "DependencyAwareForwardingPlanningModel",
            "forwarding_stability": "always FOLLOW",
            "coordination_policy": "_IdentityCoordinationPolicy",
            "connection_suppression": "disabled",
        }
    _require_planner_profile(profile)
    common["profile"] = profile
    if profile in (REFERENCE_GLOBAL_PROFILE, REFERENCE_BOUNDED_PROFILE):
        bounded = profile == REFERENCE_BOUNDED_PROFILE
        return {
            **common,
            "profile_role": (
                "reference-bounded-control" if bounded else "reference"
            ),
            "planning_window": (
                "bounded exact time prefix"
                if bounded
                else "all generated unpublished Differences"
            ),
            "planning_window_policy": (
                "TimeHorizonPlanningWindowPolicy"
                if bounded
                else "AllAvailablePlanningWindowPolicy"
            ),
            "planning_lookahead_seconds": (
                S4_BOUNDED_PLANNING_LOOKAHEAD.total_seconds()
                if bounded
                else None
            ),
            "publication_horizon_seconds": (
                S4_PUBLICATION_HORIZON.total_seconds()
                if bounded
                else None
            ),
            "connection_model": "SampleBoundaryConnectionPlanningModel",
            "connection_timing": "sample boundary",
            "connection_episode_omission": "disabled",
            "parameter_model": "SampleBoundaryParameterPlanningModel",
            "parameter_stability": "disabled",
            "forwarding_model": "DependencyAwareForwardingPlanningModel",
            "forwarding_stability": "always FOLLOW",
            "coordination_policy": "DefaultCoordinationPolicy",
        }
    planning_lookahead = (
        S4_BOUNDED_PLANNING_LOOKAHEAD
        if profile == STABLE_BOUNDED_PROFILE
        else None
    )
    delay_configuration = S4_PARAMETER_CONFIGURATION.delay_us
    forwarding = S4_FORWARDING_CONFIGURATION
    return {
        **common,
        "profile_role": (
            "current-s4-candidate"
            if profile == STABLE_BOUNDED_PROFILE
            else "experimental-control"
        ),
        "planning_window": (
            "bounded exact time prefix"
            if planning_lookahead is not None
            else "all generated unpublished Differences"
        ),
        "planning_window_policy": "TimeHorizonPlanningWindowPolicy",
        "planning_lookahead_seconds": (
            None
            if planning_lookahead is None
            else planning_lookahead.total_seconds()
        ),
        "publication_horizon_seconds": (
            S4_PUBLICATION_HORIZON.total_seconds()
        ),
        "connection_model": "MidpointConnectionPlanningModel",
        "connection_timing": "interval midpoint",
        "connection_episode_candidate_max_seconds_by_kind": {
            kind.value: duration.total_seconds()
            for kind, duration in (
                S4_CONNECTION_CONFIGURATION
                .omission_candidate_max_duration_by_kind.items()
            )
        },
        "parameter_model": "AccumulatedDeadbandParameterPlanningModel",
        "parameter_delay_us": {
            "absolute_deadband": delay_configuration.absolute_deadband,
            "relative_deadband": delay_configuration.relative_deadband,
            "persistence_seconds": (
                delay_configuration.persistence.total_seconds()
            ),
            "max_staleness_seconds": (
                delay_configuration.max_staleness.total_seconds()
            ),
        },
        "parameter_bundle_drifted_fields": True,
        "forwarding_model": "StableForwardingPlanningModel",
        "forwarding_stability": {
            "minimum_hop_gain": forwarding.minimum_hop_gain,
            "minimum_delay_gain_us": forwarding.minimum_delay_gain_us,
            "persistence_seconds": forwarding.persistence.total_seconds(),
            "immediate_hop_gain": forwarding.immediate_hop_gain,
            "immediate_delay_gain_us": forwarding.immediate_delay_gain_us,
            "maximum_hold_seconds": forwarding.maximum_hold.total_seconds(),
        },
        "coordination_policy": "DefaultCoordinationPolicy",
    }


def _require_planner_profile(profile: str) -> None:
    if not isinstance(profile, str):
        raise TypeError("profile must be a str")
    if profile not in S4_PLANNER_PROFILES:
        raise ValueError(
            f"unknown S4 Planner profile {profile!r}; expected one of "
            f"{S4_PLANNER_PROFILES!r}"
        )


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
    "EXPERIMENTAL_STABLE_GLOBAL_PROFILE",
    "REFERENCE_BOUNDED_PROFILE",
    "REFERENCE_GLOBAL_PROFILE",
    "S4_PLANNER_PROFILES",
    "STABLE_BOUNDED_PROFILE",
    "create_s4_dynamic_simulator",
    "create_s4_planner",
    "create_s4_static_simulator",
    "s4_planning_configuration",
]
