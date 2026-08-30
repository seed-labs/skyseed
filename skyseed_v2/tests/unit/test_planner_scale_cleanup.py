from __future__ import annotations

from collections import Counter
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from skyseed_v2.shared import NodeId
from skyseed_v2.simulator import (
    Connection,
    ConnectionDirection,
    ConnectionId,
    ConnectionKind,
    ConnectionSlice,
    ConnectionTransition,
    ConnectionTransitionOperation,
    DifferenceOperation,
    DirectionalConnectionParameters,
    ForwardingTransition,
    Plan,
    GroundStationRouteTarget,
    MidpointConnectionPlanningModel,
    ParameterTransition,
    RouteKey,
    RoutePatch,
    TopologyDifference,
)
from skyseed_v2.simulator.planning import _difference_time
from skyseed_v2.simulator.planning import planner as planner_module
from skyseed_v2.simulator.planning.algorithms import (
    _connection_id_for_hop,
    _resolve_planned_path,
    _resolve_planned_paths,
)
from skyseed_v2.simulator.planning._difference_time import (
    _DifferenceTimeResolver,
)
from skyseed_v2.simulator.planning.planner import (
    _is_faithful_connection_translation,
    _partition_transitions,
    _suppression_candidates,
    _validate_connection_model_output,
    _validate_coordination_output,
    _validate_forwarding_model_output,
    _validate_plan_region,
)


START = datetime(2026, 1, 1, tzinfo=timezone.utc)
SATELLITE = NodeId(65001, "satellite", "sat-1")
SATELLITE_2 = NodeId(65001, "satellite", "sat-2")
GROUND_STATION = NodeId(65001, "ground-station", "gs-1")
CONNECTION_ID = ConnectionId(
    ConnectionKind.GROUND_STATION_ACCESS,
    SATELLITE,
    GROUND_STATION,
)
SECOND_CONNECTION_ID = ConnectionId(
    ConnectionKind.INTER_SATELLITE,
    SATELLITE,
    SATELLITE_2,
)
PARAMETERS = DirectionalConnectionParameters(1_000_000, 100, 10)
CONNECTION = Connection(CONNECTION_ID, PARAMETERS, PARAMETERS)
SECOND_CONNECTION = Connection(SECOND_CONNECTION_ID, PARAMETERS, PARAMETERS)
ROUTE_KEY = RouteKey(SATELLITE, GroundStationRouteTarget(GROUND_STATION))


def _difference(
    previous_slice_id: int,
    previous_timestamp: datetime | None,
    current_timestamp: datetime,
    **values,
) -> TopologyDifference:
    return TopologyDifference(
        previous_slice_id=previous_slice_id,
        previous_timestamp=previous_timestamp,
        current_slice_id=previous_slice_id + 1,
        current_timestamp=current_timestamp,
        **values,
    )


def _legacy_difference_for_time(
    differences: tuple[TopologyDifference, ...],
    nominal_time: datetime,
) -> TopologyDifference:
    for difference in differences:
        if difference.previous_timestamp is None:
            if nominal_time == difference.current_timestamp:
                return difference
        elif (
            difference.previous_timestamp
            < nominal_time
            <= difference.current_timestamp
        ):
            return difference
    raise ValueError("transition nominal time is outside planning window")


def _assert_same_call(legacy, current) -> None:
    try:
        expected = legacy()
    except Exception as expected_error:
        with pytest.raises(type(expected_error)) as actual:
            current()
        assert str(actual.value) == str(expected_error)
        return
    actual = current()
    assert actual == expected
    if isinstance(actual, TopologyDifference):
        assert actual is expected


def _standard_differences() -> tuple[TopologyDifference, ...]:
    return (
        _difference(0, None, START),
        _difference(1, START, START + timedelta(seconds=10)),
        _difference(
            2,
            START + timedelta(seconds=10),
            START + timedelta(seconds=20),
        ),
    )


def test_planned_path_connection_index_preserves_legacy_lookup_semantics() -> None:
    route_key = RouteKey(SATELLITE, GroundStationRouteTarget(GROUND_STATION))
    unique = {SECOND_CONNECTION_ID: SECOND_CONNECTION}
    resolved = _resolve_planned_paths(
        {route_key: SATELLITE_2}, unique
    )[route_key]
    assert resolved == (SECOND_CONNECTION_ID,)
    assert resolved[0] is SECOND_CONNECTION_ID

    with pytest.raises(
        ValueError,
        match="planned forwarding hop must map to exactly one current Connection",
    ) as missing:
        _resolve_planned_paths({route_key: SATELLITE_2}, {})
    with pytest.raises(ValueError) as legacy_missing:
        _connection_id_for_hop(SATELLITE, SATELLITE_2, {})
    assert str(missing.value) == str(legacy_missing.value)

    ambiguous_id = ConnectionId(
        ConnectionKind.GROUND_STATION_ACCESS,
        SATELLITE,
        SATELLITE_2,
    )
    ambiguous = {
        SECOND_CONNECTION_ID: SECOND_CONNECTION,
        ambiguous_id: Connection(ambiguous_id, PARAMETERS, PARAMETERS),
    }
    with pytest.raises(ValueError) as indexed_ambiguous:
        _resolve_planned_paths({route_key: SATELLITE_2}, ambiguous)
    with pytest.raises(ValueError) as legacy_ambiguous:
        _connection_id_for_hop(SATELLITE, SATELLITE_2, ambiguous)
    assert str(indexed_ambiguous.value) == str(legacy_ambiguous.value)


def test_planned_path_suffix_cache_preserves_exact_legacy_paths_and_order() -> None:
    target = GroundStationRouteTarget(GROUND_STATION)
    upstream = RouteKey(SATELLITE, target)
    downstream = RouteKey(SATELLITE_2, target)
    access_id = ConnectionId(
        ConnectionKind.GROUND_STATION_ACCESS,
        SATELLITE_2,
        GROUND_STATION,
    )
    access = Connection(access_id, PARAMETERS, PARAMETERS)
    next_hops = {
        upstream: SATELLITE_2,
        downstream: GROUND_STATION,
    }
    connections = {
        SECOND_CONNECTION_ID: SECOND_CONNECTION,
        access_id: access,
    }

    expected = {
        route_key: _resolve_planned_path(route_key, next_hops, connections)
        for route_key in next_hops
    }
    actual = _resolve_planned_paths(next_hops, connections)

    assert tuple(actual) == tuple(next_hops)
    assert actual == expected
    assert actual[upstream] == (SECOND_CONNECTION_ID, access_id)
    assert actual[downstream] == (access_id,)
    assert actual[upstream][0] is SECOND_CONNECTION_ID
    assert actual[upstream][1] is access_id

    loop = {upstream: SATELLITE_2, downstream: SATELLITE}
    with pytest.raises(ValueError) as legacy_loop:
        _resolve_planned_path(upstream, loop, connections)
    with pytest.raises(ValueError) as cached_loop:
        _resolve_planned_paths(loop, connections)
    assert str(cached_loop.value) == str(legacy_loop.value)


@pytest.mark.parametrize(
    "nominal_time",
    (
        START,
        START + timedelta(seconds=5),
        START + timedelta(seconds=10),
        START + timedelta(seconds=10, microseconds=1),
        START + timedelta(seconds=20),
        START - timedelta(microseconds=1),
        START + timedelta(seconds=20, microseconds=1),
    ),
)
def test_difference_time_resolver_is_legacy_equivalent_at_boundaries(
    nominal_time,
) -> None:
    differences = _standard_differences()
    resolver = _DifferenceTimeResolver(differences)

    _assert_same_call(
        lambda: _legacy_difference_for_time(differences, nominal_time),
        lambda: resolver.difference_for_time(nominal_time),
    )


def test_difference_time_resolver_falls_back_for_overlap_and_non_monotone() -> None:
    first = _difference(
        1,
        START,
        START + timedelta(seconds=10),
    )
    overlapping = _difference(
        2,
        START + timedelta(seconds=5),
        START + timedelta(seconds=15),
    )
    overlap = (first, overlapping)
    overlap_resolver = _DifferenceTimeResolver(overlap)
    assert overlap_resolver.difference_for_time(
        START + timedelta(seconds=7)
    ) is first

    non_monotone = (overlapping, first)
    resolver = _DifferenceTimeResolver(non_monotone)
    for nominal_time in (
        START + timedelta(seconds=7),
        START + timedelta(seconds=12),
        START + timedelta(seconds=16),
    ):
        _assert_same_call(
            lambda value=nominal_time: _legacy_difference_for_time(
                non_monotone, value
            ),
            lambda value=nominal_time: resolver.difference_for_time(value),
        )


def test_difference_time_resolver_has_index_plus_query_growth(monkeypatch) -> None:
    differences = tuple(
        _difference(
            index,
            None if index == 0 else START + timedelta(seconds=index - 1),
            START + timedelta(seconds=index),
        )
        for index in range(100)
    )
    calls = 0
    legacy = _difference_time._time_belongs_to_difference

    def counted(difference, nominal_time):
        nonlocal calls
        calls += 1
        return legacy(difference, nominal_time)

    monkeypatch.setattr(_difference_time, "_time_belongs_to_difference", counted)
    resolver = _DifferenceTimeResolver(differences)
    for difference in differences:
        assert resolver.difference_for_time(
            difference.current_timestamp
        ) is difference
    assert calls == len(differences)
    for difference in differences:
        resolver.difference_for_time(difference.current_timestamp)
    assert calls == len(differences)


def _legacy_validate_connection_output(differences, transitions) -> None:
    if any(not isinstance(value, ConnectionTransition) for value in transitions):
        raise TypeError(
            "ConnectionPlanningModel must return ConnectionTransition values"
        )
    expected = Counter()
    for difference in differences:
        expected.update(
            (
                difference.current_slice_id,
                ConnectionTransitionOperation.ESTABLISH,
                connection.connection_id,
                connection,
            )
            for connection in difference.added_connections
        )
        expected.update(
            (
                difference.current_slice_id,
                ConnectionTransitionOperation.WITHDRAW,
                connection_id,
                None,
            )
            for connection_id in difference.removed_connection_ids
        )
    actual = Counter()
    for transition in transitions:
        source = _legacy_difference_for_time(
            differences, transition.nominal_time
        )
        actual[
            (
                source.current_slice_id,
                transition.operation,
                transition.connection_id,
                transition.connection,
            )
        ] += 1
    if actual != expected:
        raise ValueError(
            "ConnectionPlanningModel must preserve exact planning-window "
            "Difference evolution"
        )


def test_connection_validation_matches_legacy_oracle() -> None:
    first = _difference(
        0,
        None,
        START,
        added_connections=(CONNECTION,),
        route_patches=(
            RoutePatch(
                ROUTE_KEY,
                DifferenceOperation.ADDED,
                GROUND_STATION,
            ),
        ),
    )
    second = _difference(
        1,
        START,
        START + timedelta(seconds=10),
        removed_connection_ids=(CONNECTION_ID,),
        route_patches=(
            RoutePatch(ROUTE_KEY, DifferenceOperation.REMOVED, None),
        ),
    )
    differences = (first, second)
    connections = (
        ConnectionTransition(
            START,
            ConnectionTransitionOperation.ESTABLISH,
            CONNECTION_ID,
            CONNECTION,
        ),
        ConnectionTransition(
            START + timedelta(seconds=5),
            ConnectionTransitionOperation.WITHDRAW,
            CONNECTION_ID,
        ),
    )
    for values in (
        connections,
        tuple(reversed(connections)),
        (*connections, connections[0]),
        (replace(connections[1], nominal_time=START + timedelta(seconds=11)),),
    ):
        _assert_same_call(
            lambda current=values: _legacy_validate_connection_output(
                differences, current
            ),
            lambda current=values: _validate_connection_model_output(
                differences, current
            ),
        )


def test_faithful_connection_proof_is_data_based_and_exact() -> None:
    difference = _difference(
        0,
        None,
        START,
        added_connections=(CONNECTION,),
    )
    exact = ConnectionTransition(
        START,
        ConnectionTransitionOperation.ESTABLISH,
        CONNECTION_ID,
        CONNECTION,
    )
    equal_copy = ConnectionTransition(
        START,
        ConnectionTransitionOperation.ESTABLISH,
        CONNECTION_ID,
        replace(CONNECTION),
    )
    midpoint = replace(exact, nominal_time=START + timedelta(microseconds=1))

    assert _is_faithful_connection_translation((difference,), (exact,))
    assert not _is_faithful_connection_translation(
        (difference,), (equal_copy,)
    )
    assert not _is_faithful_connection_translation(
        (difference,), (midpoint,)
    )


def test_faithful_forwarding_proof_skips_full_formal_path_reproof(
    monkeypatch,
) -> None:
    difference = _difference(
        0,
        None,
        START,
        added_connections=(CONNECTION,),
        route_patches=(
            RoutePatch(
                ROUTE_KEY,
                DifferenceOperation.ADDED,
                GROUND_STATION,
            ),
        ),
    )
    exact = ForwardingTransition(
        START,
        ROUTE_KEY,
        DifferenceOperation.ADDED,
        GROUND_STATION,
    )
    route_next_hops = {0: {}, 1: {ROUTE_KEY: GROUND_STATION}}
    route_paths = {0: {}, 1: {ROUTE_KEY: (CONNECTION_ID,)}}
    connection_values = {0: {}, 1: {CONNECTION_ID: CONNECTION}}

    def full_reproof_must_not_run(*_args, **_kwargs):
        raise AssertionError("faithful translation entered full path validation")

    monkeypatch.setattr(
        planner_module,
        "_planned_paths_by_slice",
        full_reproof_must_not_run,
    )
    paths, faithful, work = _validate_forwarding_model_output(
        (difference,),
        (exact,),
        route_next_hops=route_next_hops,
        route_path_connection_ids=route_paths,
        connection_values_by_slice_id=connection_values,
        published_route_next_hops={},
        published_route_path_connection_ids={},
    )

    assert faithful is True
    assert paths[1] is route_paths[1]
    assert work["formal_route_patches_compared"] == 1
    assert work["validated_route_states"] == 0


def test_equal_but_non_provenance_forwarding_uses_full_semantic_oracle(
    monkeypatch,
) -> None:
    difference = _difference(
        0,
        None,
        START,
        added_connections=(CONNECTION,),
        route_patches=(
            RoutePatch(
                ROUTE_KEY,
                DifferenceOperation.ADDED,
                GROUND_STATION,
            ),
        ),
    )
    copied_route_key = RouteKey(
        ROUTE_KEY.source,
        GroundStationRouteTarget(GROUND_STATION),
    )
    copied = ForwardingTransition(
        START,
        copied_route_key,
        DifferenceOperation.ADDED,
        GROUND_STATION,
    )
    route_next_hops = {0: {}, 1: {ROUTE_KEY: GROUND_STATION}}
    route_paths = {0: {}, 1: {ROUTE_KEY: (CONNECTION_ID,)}}
    connection_values = {0: {}, 1: {CONNECTION_ID: CONNECTION}}
    original = planner_module._planned_paths_by_slice
    calls = []

    def counted(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(planner_module, "_planned_paths_by_slice", counted)
    _paths, faithful, work = _validate_forwarding_model_output(
        (difference,),
        (copied,),
        route_next_hops=route_next_hops,
        route_path_connection_ids=route_paths,
        connection_values_by_slice_id=connection_values,
        published_route_next_hops={},
        published_route_path_connection_ids={},
    )

    assert faithful is False
    assert calls == [True]
    assert work["validated_route_states"] == 1


def test_faithful_final_region_validates_delta_without_path_reconstruction(
    monkeypatch,
) -> None:
    difference = _difference(
        0,
        None,
        START,
        added_connections=(CONNECTION,),
        route_patches=(
            RoutePatch(
                ROUTE_KEY,
                DifferenceOperation.ADDED,
                GROUND_STATION,
            ),
        ),
    )
    connection = ConnectionTransition(
        START,
        ConnectionTransitionOperation.ESTABLISH,
        CONNECTION_ID,
        CONNECTION,
    )
    forwarding = ForwardingTransition(
        START,
        ROUTE_KEY,
        DifferenceOperation.ADDED,
        GROUND_STATION,
    )

    def path_reconstruction_must_not_run(*_args, **_kwargs):
        raise AssertionError("faithful final region reconstructed formal paths")

    monkeypatch.setattr(
        planner_module,
        "_resolve_planned_paths",
        path_reconstruction_must_not_run,
    )
    work = _validate_plan_region(
        (difference,),
        (connection,),
        (),
        (forwarding,),
        {0: {}, 1: {ROUTE_KEY: GROUND_STATION}},
        {0: {}, 1: {ROUTE_KEY: (CONNECTION_ID,)}},
        {0: {}, 1: {CONNECTION_ID: CONNECTION}},
        {},
        {},
        {},
        faithful_formal_translation=True,
    )

    assert work["faithful_formal_translation"] == 1
    assert work["forwarding_path_reconstructions"] == 0
    assert work["reused_forwarding_path_boundaries"] == 1


def test_full_final_region_counts_validated_forwarding_dependency_ids() -> None:
    difference = _difference(
        0,
        None,
        START,
        added_connections=(CONNECTION,),
        route_patches=(
            RoutePatch(
                ROUTE_KEY,
                DifferenceOperation.ADDED,
                GROUND_STATION,
            ),
        ),
    )
    connection = ConnectionTransition(
        START,
        ConnectionTransitionOperation.ESTABLISH,
        CONNECTION_ID,
        CONNECTION,
    )
    forwarding = ForwardingTransition(
        START,
        ROUTE_KEY,
        DifferenceOperation.ADDED,
        GROUND_STATION,
    )

    work = _validate_plan_region(
        (difference,),
        (connection,),
        (),
        (forwarding,),
        {0: {}, 1: {ROUTE_KEY: GROUND_STATION}},
        {0: {}, 1: {ROUTE_KEY: (CONNECTION_ID,)}},
        {0: {}, 1: {CONNECTION_ID: CONNECTION}},
        {},
        {},
        {},
        faithful_formal_translation=False,
    )

    assert work["validated_forwarding_dependency_ids"] == 2


def _legacy_suppression_candidates(
    differences,
    transitions,
    active_before,
    model,
):
    active = dict(active_before)
    result = []
    for transition in sorted(
        transitions,
        key=lambda value: (
            value.nominal_time,
            0
            if value.operation is ConnectionTransitionOperation.ESTABLISH
            else 1,
            repr(value.connection_id),
        ),
    ):
        if transition.operation is ConnectionTransitionOperation.ESTABLISH:
            active[transition.connection_id] = transition
            continue
        establish = active.pop(transition.connection_id, None)
        if establish is None:
            continue
        _legacy_difference_for_time(differences, transition.nominal_time)
        if model.is_suppression_candidate(establish, transition):
            result.append((establish, transition))
    return tuple(result)


def test_suppression_candidates_preserve_legacy_pairing_order_and_errors() -> None:
    differences = tuple(
        _difference(
            index,
            None if index == 0 else START + timedelta(seconds=(index - 1) * 10),
            START + timedelta(seconds=index * 10),
        )
        for index in range(26)
    )
    establish = ConnectionTransition(
        START,
        ConnectionTransitionOperation.ESTABLISH,
        CONNECTION_ID,
        CONNECTION,
    )
    below = ConnectionTransition(
        START + timedelta(seconds=239),
        ConnectionTransitionOperation.WITHDRAW,
        CONNECTION_ID,
    )
    equal = replace(below, nominal_time=START + timedelta(seconds=240))
    above = replace(below, nominal_time=START + timedelta(seconds=250))
    later_establish = replace(
        establish, nominal_time=START + timedelta(seconds=100)
    )
    first_withdraw = replace(
        below, nominal_time=START + timedelta(seconds=50)
    )
    later_withdraw = replace(
        below, nominal_time=START + timedelta(seconds=200)
    )
    unmatched = ConnectionTransition(
        START + timedelta(seconds=5),
        ConnectionTransitionOperation.WITHDRAW,
        SECOND_CONNECTION_ID,
    )
    model = MidpointConnectionPlanningModel(
        minimum_dwell=timedelta(seconds=240)
    )
    for transitions, active_before in (
        ((unmatched, establish, below), {}),
        ((below,), {CONNECTION_ID: establish}),
        ((establish, equal), {}),
        ((establish, above), {}),
        (
            (establish, first_withdraw, later_establish, later_withdraw),
            {},
        ),
    ):
        expected = _legacy_suppression_candidates(
            differences, transitions, active_before, model
        )
        actual = _suppression_candidates(
            differences, transitions, active_before, model
        )
        assert len(actual) == len(expected)
        assert all(
            left[0] is right[0] and left[1] is right[1]
            for left, right in zip(actual, expected, strict=True)
        )

    same_time_withdraw = replace(below, nominal_time=START)
    _assert_same_call(
        lambda: _legacy_suppression_candidates(
            differences, (same_time_withdraw, establish), {}, model
        ),
        lambda: _suppression_candidates(
            differences, (same_time_withdraw, establish), {}, model
        ),
    )


def test_coordination_output_identity_index_preserves_current_contract() -> None:
    connection = ConnectionTransition(
        START,
        ConnectionTransitionOperation.ESTABLISH,
        CONNECTION_ID,
        CONNECTION,
    )
    parameter = ParameterTransition(
        START,
        CONNECTION_ID,
        ConnectionDirection.ENDPOINT_A_TO_B,
        delay_us=100,
    )
    forwarding = ForwardingTransition(
        START,
        ROUTE_KEY,
        DifferenceOperation.ADDED,
        GROUND_STATION,
    )
    original = ((connection,), (parameter,), (forwarding,))
    fast = _validate_coordination_output(
        (*original, ()),
        *original,
    )
    assert fast[0] is original[0]
    assert fast[1] is original[1]
    assert fast[2] is original[2]

    assert _validate_coordination_output(
        ((), (), (), ()), *original
    ) == ((), (), (), ())
    with pytest.raises(ValueError, match="invented a Connection"):
        _validate_coordination_output(
            ((replace(connection),), (), (), ()), *original
        )
    with pytest.raises(ValueError, match="invented a Parameter"):
        _validate_coordination_output(
            ((), (replace(parameter),), (), ()), *original
        )
    with pytest.raises(ValueError, match="duplicated a Connection"):
        _validate_coordination_output(
            ((connection, connection), (), (), ()), *original
        )
    invented_forwarding = replace(forwarding, next_hop=SATELLITE_2)
    accepted = _validate_coordination_output(
        ((), (), (invented_forwarding,), ()), *original
    )
    assert accepted[2] == (invented_forwarding,)


def test_ordered_plan_inputs_keep_identity_and_exact_temporal_family_order() -> None:
    connection = ConnectionTransition(
        START,
        ConnectionTransitionOperation.ESTABLISH,
        CONNECTION_ID,
        CONNECTION,
    )
    parameter = ParameterTransition(
        START,
        CONNECTION_ID,
        ConnectionDirection.ENDPOINT_A_TO_B,
        delay_us=100,
    )
    forwarding = ForwardingTransition(
        START,
        ROUTE_KEY,
        DifferenceOperation.ADDED,
        GROUND_STATION,
    )
    connections = (connection,)
    parameters = (parameter,)
    forwarding_values = (forwarding,)

    plan = Plan(
        start_slice_id=0,
        start_timestamp=None,
        end_slice_id=1,
        end_timestamp=START,
        connection_transitions=connections,
        parameter_transitions=parameters,
        forwarding_transitions=forwarding_values,
    )

    assert plan.connection_transitions is connections
    assert plan.parameter_transitions is parameters
    assert plan.forwarding_transitions is forwarding_values
    assert plan.temporal_index[START] == (
        connection,
        forwarding,
        parameter,
    )


def test_transition_partition_is_single_pass_identity_equivalent() -> None:
    transitions = (
        ForwardingTransition(
            START,
            ROUTE_KEY,
            DifferenceOperation.ADDED,
            GROUND_STATION,
        ),
        ForwardingTransition(
            START + timedelta(seconds=10),
            replace(ROUTE_KEY, source=SATELLITE_2),
            DifferenceOperation.ADDED,
            GROUND_STATION,
        ),
    )
    through, after = _partition_transitions(transitions, START)

    assert through == transitions[:1]
    assert after == transitions[1:]
    assert through[0] is transitions[0]
    assert after[0] is transitions[1]

    non_monotone = tuple(reversed(transitions))
    through, after = _partition_transitions(non_monotone, START)
    assert through == (transitions[0],)
    assert after == (transitions[1],)
    assert through[0] is transitions[0]
    assert after[0] is transitions[1]
