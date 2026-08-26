from __future__ import annotations

from datetime import datetime, timedelta, timezone
from inspect import signature

import pytest

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
    ForwardingTransitionPrecedence,
    GroundStationRouteTarget,
    MidpointConnectionPlanningModel,
    ParameterFieldConfiguration,
    ParameterPlanningConfiguration,
    PlanningWindowPolicy,
    SampleBoundaryConnectionPlanningModel,
    RouteKey,
    RoutePatch,
    TopologyDifference,
    TimeHorizonPlanningWindowPolicy,
    ZoneRouteTarget,
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


def _legacy_forwarding_precedences(
    differences: tuple[TopologyDifference, ...],
    transitions: tuple[ForwardingTransition, ...],
    paths: dict[int, dict[RouteKey, tuple[ConnectionId, ...]]],
) -> tuple[ForwardingTransitionPrecedence, ...]:
    def source_for(transition: ForwardingTransition) -> TopologyDifference:
        for difference in differences:
            if difference.previous_timestamp is None:
                if transition.nominal_time == difference.current_timestamp:
                    return difference
            elif (
                difference.previous_timestamp
                < transition.nominal_time
                <= difference.current_timestamp
            ):
                return difference
        raise ValueError("transition nominal time is outside planning window")

    def states(transition: ForwardingTransition):
        source = source_for(transition)
        return (
            tuple(paths.get(source.previous_slice_id, {}).get(transition.route_key, ())),
            tuple(paths.get(source.current_slice_id, {}).get(transition.route_key, ())),
        )

    def direct_upstream(upstream, downstream) -> bool:
        return len(upstream) == len(downstream) + 1 and upstream[1:] == downstream

    path_states = {id(value): states(value) for value in transitions}
    result = []
    for predecessor in transitions:
        predecessor_old_path, predecessor_new_path = path_states[id(predecessor)]
        for successor in transitions:
            if predecessor is successor:
                continue
            if predecessor.nominal_time != successor.nominal_time:
                continue
            if predecessor.route_key.target != successor.route_key.target:
                continue
            successor_old_path, successor_new_path = path_states[id(successor)]
            prepares_direct_upstream = (
                predecessor.operation is not DifferenceOperation.REMOVED
                and successor.operation is not DifferenceOperation.REMOVED
                and direct_upstream(successor_new_path, predecessor_new_path)
            )
            removes_direct_downstream = (
                predecessor.operation is not DifferenceOperation.ADDED
                and successor.operation is DifferenceOperation.REMOVED
                and direct_upstream(predecessor_old_path, successor_old_path)
            )
            if prepares_direct_upstream or removes_direct_downstream:
                result.append(
                    ForwardingTransitionPrecedence(predecessor, successor)
                )
    return tuple(result)


def _assert_precedence_equivalent(
    differences: tuple[TopologyDifference, ...],
    transitions: tuple[ForwardingTransition, ...],
    paths: dict[int, dict[RouteKey, tuple[ConnectionId, ...]]],
) -> None:
    expected = _legacy_forwarding_precedences(differences, transitions, paths)
    actual = DependencyAwareForwardingPlanningModel().plan_forwarding_precedences(
        differences=differences,
        forwarding_transitions=transitions,
        route_path_connection_ids=paths,
    )
    assert len(actual) == len(expected)
    assert all(
        current.predecessor is previous.predecessor
        and current.successor is previous.successor
        for current, previous in zip(actual, expected, strict=True)
    )


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


def test_window_policy_separates_planning_from_publication_window() -> None:
    planning_parameters = signature(
        PlanningWindowPolicy.select_planning_differences
    ).parameters
    publication_parameters = signature(
        PlanningWindowPolicy.select_publication_differences
    ).parameters

    assert tuple(planning_parameters) == ("self", "available_differences")
    assert tuple(publication_parameters) == (
        "self",
        "planning_differences",
    )


def test_time_horizon_policy_selects_irregular_exact_prefixes() -> None:
    differences = (
        TopologyDifference(
            previous_slice_id=0,
            previous_timestamp=None,
            current_slice_id=1,
            current_timestamp=START,
        ),
        TopologyDifference(
            previous_slice_id=1,
            previous_timestamp=START,
            current_slice_id=2,
            current_timestamp=START + timedelta(seconds=7),
        ),
        TopologyDifference(
            previous_slice_id=2,
            previous_timestamp=START + timedelta(seconds=7),
            current_slice_id=3,
            current_timestamp=START + timedelta(seconds=20),
        ),
        TopologyDifference(
            previous_slice_id=3,
            previous_timestamp=START + timedelta(seconds=20),
            current_slice_id=4,
            current_timestamp=START + timedelta(seconds=31),
        ),
    )
    policy = TimeHorizonPlanningWindowPolicy(
        planning_lookahead=timedelta(seconds=20),
        publication_horizon=timedelta(seconds=7),
    )

    planning = policy.select_planning_differences(differences)
    publication = policy.select_publication_differences(planning)

    assert tuple(value.current_slice_id for value in planning) == (1, 2, 3)
    assert tuple(value.current_slice_id for value in publication) == (1, 2)


def test_parameter_effective_deadband_uses_maximum_absolute_and_relative() -> None:
    model = AccumulatedDeadbandParameterPlanningModel(
        configuration=ParameterPlanningConfiguration(
            delay_us=ParameterFieldConfiguration(
                absolute_deadband=5,
                relative_deadband=0.5,
            )
        )
    )
    initial = TopologyDifference(
        previous_slice_id=0,
        previous_timestamp=None,
        current_slice_id=1,
        current_timestamp=START,
        added_connections=(CONNECTION,),
    )
    inside_relative_band = TopologyDifference(
        previous_slice_id=1,
        previous_timestamp=START,
        current_slice_id=2,
        current_timestamp=START + timedelta(seconds=10),
        connection_parameter_patches=(
            DirectionalConnectionParametersPatch(
                CONNECTION_ID,
                ConnectionDirection.ENDPOINT_A_TO_B,
                delay_us=14,
            ),
        ),
    )
    outside_relative_band = TopologyDifference(
        previous_slice_id=2,
        previous_timestamp=START + timedelta(seconds=10),
        current_slice_id=3,
        current_timestamp=START + timedelta(seconds=20),
        connection_parameter_patches=(
            DirectionalConnectionParametersPatch(
                CONNECTION_ID,
                ConnectionDirection.ENDPOINT_A_TO_B,
                delay_us=15,
            ),
        ),
    )

    transitions = model.plan_parameter_transitions(
        differences=(initial, inside_relative_band, outside_relative_band),
        affected_differences=(
            initial,
            inside_relative_band,
            outside_relative_band,
        ),
        previous_transitions=(),
        observed_parameters={},
        planned_parameters={},
    )

    assert tuple(value.delay_us for value in transitions) == (15,)


def test_parameter_timer_does_not_emit_at_removed_connection_boundary() -> None:
    model = AccumulatedDeadbandParameterPlanningModel(
        configuration=ParameterPlanningConfiguration(
            delay_us=ParameterFieldConfiguration(
                absolute_deadband=5,
                persistence=timedelta(seconds=10),
            )
        )
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
    )

    transitions = model.plan_parameter_transitions(
        differences=differences,
        affected_differences=differences,
        previous_transitions=(),
        observed_parameters={},
        planned_parameters={},
    )

    assert transitions == ()


def test_zero_parameter_max_staleness_is_an_immediate_bound() -> None:
    model = AccumulatedDeadbandParameterPlanningModel(
        configuration=ParameterPlanningConfiguration(
            delay_us=ParameterFieldConfiguration(
                absolute_deadband=1_000,
                max_staleness=timedelta(0),
            )
        )
    )
    initial = TopologyDifference(
        previous_slice_id=0,
        previous_timestamp=None,
        current_slice_id=1,
        current_timestamp=START,
        added_connections=(CONNECTION,),
    )
    patch = TopologyDifference(
        previous_slice_id=1,
        previous_timestamp=START,
        current_slice_id=2,
        current_timestamp=START + timedelta(seconds=10),
        connection_parameter_patches=(
            DirectionalConnectionParametersPatch(
                CONNECTION_ID,
                ConnectionDirection.ENDPOINT_A_TO_B,
                delay_us=11,
            ),
        ),
    )

    transitions = model.plan_parameter_transitions(
        differences=(initial, patch),
        affected_differences=(initial, patch),
        previous_transitions=(),
        observed_parameters={},
        planned_parameters={},
    )

    assert tuple(value.delay_us for value in transitions) == (11,)


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


def test_bucketed_forwarding_precedence_is_ordered_legacy_equivalent() -> None:
    satellites = tuple(
        NodeId(65001, "satellite", f"bucket-sat-{index}")
        for index in range(1, 9)
    )
    target_a = GroundStationRouteTarget(GROUND_STATION)
    target_b = ZoneRouteTarget("bucket-zone")
    a_up = RouteKey(satellites[0], target_a)
    a_down = RouteKey(satellites[1], target_a)
    a_equal = RouteKey(satellites[2], target_a)
    a_unrelated = RouteKey(satellites[3], target_a)
    b_up = RouteKey(satellites[4], target_b)
    b_down = RouteKey(satellites[5], target_b)
    b_empty = RouteKey(satellites[6], target_b)
    connections = tuple(
        ConnectionId(
            ConnectionKind.INTER_SATELLITE,
            satellites[index],
            satellites[index + 1],
        )
        for index in range(7)
    )
    a_prefix, a_tail, unrelated, b_prefix, b_tail, extra, _ = connections
    first = TopologyDifference(
        previous_slice_id=0,
        previous_timestamp=None,
        current_slice_id=1,
        current_timestamp=START,
    )
    second = TopologyDifference(
        previous_slice_id=1,
        previous_timestamp=START,
        current_slice_id=2,
        current_timestamp=START + timedelta(seconds=10),
    )
    transitions = (
        ForwardingTransition(START, a_up, DifferenceOperation.ADDED, satellites[1]),
        ForwardingTransition(START, b_down, DifferenceOperation.ADDED, satellites[7]),
        ForwardingTransition(START, a_down, DifferenceOperation.ADDED, satellites[7]),
        ForwardingTransition(START, b_up, DifferenceOperation.CHANGED, satellites[5]),
        ForwardingTransition(START, a_equal, DifferenceOperation.CHANGED, satellites[7]),
        ForwardingTransition(START, a_unrelated, DifferenceOperation.ADDED, satellites[7]),
        ForwardingTransition(START, b_empty, DifferenceOperation.ADDED, satellites[7]),
        ForwardingTransition(
            second.current_timestamp, a_up, DifferenceOperation.REMOVED, None
        ),
        ForwardingTransition(
            second.current_timestamp, b_down, DifferenceOperation.REMOVED, None
        ),
        ForwardingTransition(
            second.current_timestamp, a_down, DifferenceOperation.REMOVED, None
        ),
        ForwardingTransition(
            second.current_timestamp, b_up, DifferenceOperation.CHANGED, satellites[5]
        ),
    )
    paths = {
        0: {},
        1: {
            a_up: (a_prefix, a_tail),
            a_down: (a_tail,),
            a_equal: (a_tail,),
            a_unrelated: (unrelated, extra),
            b_up: (b_prefix, b_tail),
            b_down: (b_tail,),
            b_empty: (),
        },
        2: {
            b_up: (b_prefix, b_tail),
        },
    }

    _assert_precedence_equivalent((first, second), transitions, paths)
    actual = DependencyAwareForwardingPlanningModel().plan_forwarding_precedences(
        differences=(first, second),
        forwarding_transitions=transitions,
        route_path_connection_ids=paths,
    )
    assert len(actual) >= 4
    assert actual[0].predecessor is transitions[1]
    assert actual[0].successor is transitions[3]
    assert actual[1].predecessor is transitions[2]
    assert actual[1].successor is transitions[0]


def test_bucketed_forwarding_precedence_keeps_mapping_validation_first() -> None:
    target = GroundStationRouteTarget(GROUND_STATION)
    route_key = RouteKey(SATELLITE, target)
    difference = TopologyDifference(
        previous_slice_id=0,
        previous_timestamp=None,
        current_slice_id=1,
        current_timestamp=START,
    )
    transition = ForwardingTransition(
        START, route_key, DifferenceOperation.ADDED, GROUND_STATION
    )

    with pytest.raises(
        TypeError,
        match="route_path_connection_ids contains invalid formal path state",
    ):
        DependencyAwareForwardingPlanningModel().plan_forwarding_precedences(
            differences=(difference,),
            forwarding_transitions=(transition,),
            route_path_connection_ids={1: {route_key: [CONNECTION_ID]}},  # type: ignore[dict-item]
        )
