from __future__ import annotations

from datetime import datetime, timedelta, timezone
from inspect import signature

from skyseed_v2.shared import NodeId
from skyseed_v2.simulator import (
    AccumulatedDeadbandParameterPlanningModel,
    Connection,
    ConnectionDirection,
    ConnectionId,
    ConnectionKind,
    ConnectionTransitionOperation,
    DependencyAwareForwardingPlanningModel,
    DifferenceOperation,
    DirectionalConnectionParameters,
    DirectionalConnectionParametersPatch,
    ForwardingPlanningModel,
    ForwardingTransition,
    GroundStationRouteTarget,
    MidpointConnectionPlanningModel,
    PlanningHorizonPolicy,
    SampleBoundaryConnectionPlanningModel,
    RouteKey,
    RoutePatch,
    TopologyDifference,
)


START = datetime(2026, 1, 1, tzinfo=timezone.utc)
SATELLITE = NodeId(65001, "satellite", "sat-1")
GROUND_STATION = NodeId(65001, "ground-station", "gs-1")
CONNECTION_ID = ConnectionId(
    ConnectionKind.GROUND_STATION_ACCESS,
    SATELLITE,
    GROUND_STATION,
)
PARAMETERS = DirectionalConnectionParameters(1_000_000, 10, 20)
CONNECTION = Connection(CONNECTION_ID, PARAMETERS, PARAMETERS)


def test_connection_reference_models_cover_initial_boundary_and_midpoint() -> None:
    initial = TopologyDifference(
        previous_slice_id=0,
        previous_timestamp=None,
        current_slice_id=1,
        current_timestamp=START,
        added_connections=(CONNECTION,),
    )
    removed = TopologyDifference(
        previous_slice_id=1,
        previous_timestamp=START,
        current_slice_id=2,
        current_timestamp=START + timedelta(seconds=10),
        removed_connection_ids=(CONNECTION_ID,),
    )
    midpoint = MidpointConnectionPlanningModel(
        minimum_dwell=timedelta(seconds=10)
    )
    boundary = SampleBoundaryConnectionPlanningModel()

    differences = (initial, removed)
    transitions = midpoint.plan_connection_transitions(
        differences=differences,
        affected_differences=differences,
        previous_transitions=(),
    )
    establish, withdraw = transitions

    assert establish.nominal_time == START
    assert establish.operation is ConnectionTransitionOperation.ESTABLISH
    assert withdraw.nominal_time == START + timedelta(seconds=5)
    assert midpoint.is_suppression_candidate(establish, withdraw)
    assert (
        boundary.plan_connection_transitions(
            differences=(removed,),
            affected_differences=(removed,),
            previous_transitions=(),
        )[0].nominal_time
        == START + timedelta(seconds=10)
    )


def test_connection_model_preserves_repeated_logical_lifetimes() -> None:
    later_parameters = DirectionalConnectionParameters(1_000_000, 100, 20)
    later_connection = Connection(
        CONNECTION_ID,
        later_parameters,
        later_parameters,
    )
    differences = (
        TopologyDifference(
            previous_slice_id=0,
            previous_timestamp=None,
            current_slice_id=1,
            current_timestamp=START,
            added_connections=(CONNECTION,),
        ),
        TopologyDifference(
            previous_slice_id=1,
            previous_timestamp=START,
            current_slice_id=2,
            current_timestamp=START + timedelta(seconds=10),
            removed_connection_ids=(CONNECTION_ID,),
        ),
        TopologyDifference(
            previous_slice_id=2,
            previous_timestamp=START + timedelta(seconds=10),
            current_slice_id=3,
            current_timestamp=START + timedelta(seconds=20),
            added_connections=(later_connection,),
        ),
        TopologyDifference(
            previous_slice_id=3,
            previous_timestamp=START + timedelta(seconds=20),
            current_slice_id=4,
            current_timestamp=START + timedelta(seconds=30),
            removed_connection_ids=(CONNECTION_ID,),
        ),
    )

    transitions = MidpointConnectionPlanningModel().plan_connection_transitions(
        differences=differences,
        affected_differences=differences,
        previous_transitions=(),
    )

    assert tuple(value.operation for value in transitions) == (
        ConnectionTransitionOperation.ESTABLISH,
        ConnectionTransitionOperation.WITHDRAW,
        ConnectionTransitionOperation.ESTABLISH,
        ConnectionTransitionOperation.WITHDRAW,
    )
    assert tuple(value.nominal_time for value in transitions) == (
        START,
        START + timedelta(seconds=5),
        START + timedelta(seconds=15),
        START + timedelta(seconds=25),
    )
    assert transitions[0].connection is CONNECTION
    assert transitions[2].connection is later_connection


def test_accumulated_deadband_compares_with_last_planned_target() -> None:
    model = AccumulatedDeadbandParameterPlanningModel(
        bandwidth_bps_threshold=100,
        delay_us_threshold=5,
        packet_loss_ppm_threshold=10,
    )
    initial = TopologyDifference(
        previous_slice_id=0,
        previous_timestamp=None,
        current_slice_id=1,
        current_timestamp=START,
        added_connections=(CONNECTION,),
    )
    first_patch = TopologyDifference(
        previous_slice_id=1,
        previous_timestamp=START,
        current_slice_id=2,
        current_timestamp=START + timedelta(seconds=10),
        connection_parameter_patches=(
            DirectionalConnectionParametersPatch(
                CONNECTION_ID,
                ConnectionDirection.ENDPOINT_A_TO_B,
                delay_us=12,
            ),
        ),
    )
    second_patch = TopologyDifference(
        previous_slice_id=2,
        previous_timestamp=START + timedelta(seconds=10),
        current_slice_id=3,
        current_timestamp=START + timedelta(seconds=20),
        connection_parameter_patches=(
            DirectionalConnectionParametersPatch(
                CONNECTION_ID,
                ConnectionDirection.ENDPOINT_A_TO_B,
                delay_us=16,
            ),
        ),
    )
    differences = (initial, first_patch, second_patch)
    transitions = model.plan_parameter_transitions(
        differences=differences,
        affected_differences=differences,
        previous_transitions=(),
        observed_parameters={},
        planned_parameters={},
    )

    assert len(transitions) == 1
    second = transitions[0]
    assert second.delay_us == 16
    assert second.bandwidth_bps is None
    assert second.packet_loss_ppm is None


def test_parameter_deadband_resets_after_repeated_connection_establishment() -> None:
    later_parameters = DirectionalConnectionParameters(1_000_000, 100, 20)
    later_connection = Connection(
        CONNECTION_ID,
        later_parameters,
        later_parameters,
    )
    differences = (
        TopologyDifference(
            previous_slice_id=0,
            previous_timestamp=None,
            current_slice_id=1,
            current_timestamp=START,
            added_connections=(CONNECTION,),
        ),
        TopologyDifference(
            previous_slice_id=1,
            previous_timestamp=START,
            current_slice_id=2,
            current_timestamp=START + timedelta(seconds=10),
            connection_parameter_patches=(
                DirectionalConnectionParametersPatch(
                    CONNECTION_ID,
                    ConnectionDirection.ENDPOINT_A_TO_B,
                    delay_us=16,
                ),
            ),
        ),
        TopologyDifference(
            previous_slice_id=2,
            previous_timestamp=START + timedelta(seconds=10),
            current_slice_id=3,
            current_timestamp=START + timedelta(seconds=20),
            removed_connection_ids=(CONNECTION_ID,),
        ),
        TopologyDifference(
            previous_slice_id=3,
            previous_timestamp=START + timedelta(seconds=20),
            current_slice_id=4,
            current_timestamp=START + timedelta(seconds=30),
            added_connections=(later_connection,),
        ),
        TopologyDifference(
            previous_slice_id=4,
            previous_timestamp=START + timedelta(seconds=30),
            current_slice_id=5,
            current_timestamp=START + timedelta(seconds=40),
            connection_parameter_patches=(
                DirectionalConnectionParametersPatch(
                    CONNECTION_ID,
                    ConnectionDirection.ENDPOINT_A_TO_B,
                    delay_us=103,
                ),
            ),
        ),
        TopologyDifference(
            previous_slice_id=5,
            previous_timestamp=START + timedelta(seconds=40),
            current_slice_id=6,
            current_timestamp=START + timedelta(seconds=50),
            connection_parameter_patches=(
                DirectionalConnectionParametersPatch(
                    CONNECTION_ID,
                    ConnectionDirection.ENDPOINT_A_TO_B,
                    delay_us=106,
                ),
            ),
        ),
    )

    transitions = AccumulatedDeadbandParameterPlanningModel(
        delay_us_threshold=5
    ).plan_parameter_transitions(
        differences=differences,
        affected_differences=differences,
        previous_transitions=(),
        observed_parameters={},
        planned_parameters={},
    )

    assert tuple(value.delay_us for value in transitions) == (16, 106)
    assert tuple(value.nominal_time for value in transitions) == (
        START + timedelta(seconds=10),
        START + timedelta(seconds=50),
    )


def test_forwarding_planning_protocol_has_no_slicer_authority() -> None:
    parameters = signature(
        ForwardingPlanningModel.plan_forwarding_transitions
    ).parameters

    assert "slicer" not in parameters
    assert "route_path_connection_ids" in parameters
    precedence_parameters = signature(
        ForwardingPlanningModel.plan_forwarding_precedences
    ).parameters
    assert "slicer" not in precedence_parameters
    assert "route_path_connection_ids" in precedence_parameters


def test_horizon_policy_separates_visibility_from_publication_prefix() -> None:
    visible_parameters = signature(
        PlanningHorizonPolicy.select_differences
    ).parameters
    publication_parameters = signature(
        PlanningHorizonPolicy.select_publication_differences
    ).parameters

    assert tuple(visible_parameters) == ("self", "available_differences")
    assert tuple(publication_parameters) == (
        "self",
        "visible_differences",
    )


def test_forwarding_add_precedence_constrains_only_direct_downstream_dependency() -> None:
    satellite_2 = NodeId(65001, "satellite", "sat-2")
    satellite_3 = NodeId(65001, "satellite", "sat-3")
    target = GroundStationRouteTarget(GROUND_STATION)
    upstream_key = RouteKey(SATELLITE, target)
    downstream_key = RouteKey(satellite_2, target)
    unrelated_key = RouteKey(satellite_3, target)
    isl = ConnectionId(
        ConnectionKind.INTER_SATELLITE, SATELLITE, satellite_2
    )
    access = ConnectionId(
        ConnectionKind.GROUND_STATION_ACCESS,
        satellite_2,
        GROUND_STATION,
    )
    unrelated_access = ConnectionId(
        ConnectionKind.GROUND_STATION_ACCESS,
        satellite_3,
        GROUND_STATION,
    )
    difference = TopologyDifference(
        previous_slice_id=0,
        previous_timestamp=None,
        current_slice_id=1,
        current_timestamp=START,
        route_patches=(
            RoutePatch(
                upstream_key, DifferenceOperation.ADDED, satellite_2
            ),
            RoutePatch(
                downstream_key, DifferenceOperation.ADDED, GROUND_STATION
            ),
            RoutePatch(
                unrelated_key, DifferenceOperation.ADDED, GROUND_STATION
            ),
        ),
    )
    transitions = tuple(
        ForwardingTransition(
            START, patch.route_key, patch.operation, patch.next_hop
        )
        for patch in difference.route_patches
    )
    by_key = {value.route_key: value for value in transitions}

    precedences = DependencyAwareForwardingPlanningModel().plan_forwarding_precedences(
        differences=(difference,),
        forwarding_transitions=transitions,
        route_path_connection_ids={
            1: {
                upstream_key: (isl, access),
                downstream_key: (access,),
                unrelated_key: (unrelated_access,),
            }
        },
    )

    assert len(precedences) == 1
    assert precedences[0].predecessor is by_key[downstream_key]
    assert precedences[0].successor is by_key[upstream_key]
    assert all(
        precedence.predecessor is not by_key[unrelated_key]
        and precedence.successor is not by_key[unrelated_key]
        for precedence in precedences
    )
    assert {value.nominal_time for value in transitions} == {START}


def test_forwarding_remove_precedence_stops_upstream_before_downstream_teardown() -> None:
    satellite_2 = NodeId(65001, "satellite", "sat-2")
    target = GroundStationRouteTarget(GROUND_STATION)
    upstream_key = RouteKey(SATELLITE, target)
    downstream_key = RouteKey(satellite_2, target)
    isl = ConnectionId(
        ConnectionKind.INTER_SATELLITE, SATELLITE, satellite_2
    )
    access = ConnectionId(
        ConnectionKind.GROUND_STATION_ACCESS,
        satellite_2,
        GROUND_STATION,
    )
    difference = TopologyDifference(
        previous_slice_id=1,
        previous_timestamp=START,
        current_slice_id=2,
        current_timestamp=START + timedelta(seconds=10),
        route_patches=(
            RoutePatch(upstream_key, DifferenceOperation.REMOVED, None),
            RoutePatch(downstream_key, DifferenceOperation.REMOVED, None),
        ),
    )
    transitions = tuple(
        ForwardingTransition(
            difference.current_timestamp,
            patch.route_key,
            patch.operation,
            patch.next_hop,
        )
        for patch in difference.route_patches
    )
    by_key = {value.route_key: value for value in transitions}

    precedences = DependencyAwareForwardingPlanningModel().plan_forwarding_precedences(
        differences=(difference,),
        forwarding_transitions=transitions,
        route_path_connection_ids={
            1: {
                upstream_key: (isl, access),
                downstream_key: (access,),
            },
            2: {},
        },
    )

    assert len(precedences) == 1
    assert precedences[0].predecessor is by_key[upstream_key]
    assert precedences[0].successor is by_key[downstream_key]
    assert not any(
        precedence.predecessor is by_key[downstream_key]
        and precedence.successor is by_key[upstream_key]
        for precedence in precedences
    )
