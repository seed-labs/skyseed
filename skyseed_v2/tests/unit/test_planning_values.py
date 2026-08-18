from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timedelta, timezone
from types import MappingProxyType

import pytest

from skyseed_v2.shared import NodeId
from skyseed_v2.simulator import (
    Connection,
    ConnectionDirection,
    ConnectionId,
    ConnectionKind,
    ConnectionTransition,
    ConnectionTransitionOperation,
    DifferenceOperation,
    DirectionalConnectionParameters,
    ForwardingTransition,
    ForwardingTransitionPrecedence,
    GroundStationRouteTarget,
    ParameterTransition,
    Plan,
    RouteKey,
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
ROUTE_KEY = RouteKey(
    SATELLITE, GroundStationRouteTarget(GROUND_STATION)
)


def test_plan_is_immutable_and_temporal_index_references_typed_values() -> None:
    establish = ConnectionTransition(
        START + timedelta(seconds=5),
        ConnectionTransitionOperation.ESTABLISH,
        CONNECTION_ID,
        CONNECTION,
    )
    forwarding = ForwardingTransition(
        START + timedelta(seconds=5),
        ROUTE_KEY,
        DifferenceOperation.ADDED,
        GROUND_STATION,
    )
    parameter = ParameterTransition(
        START + timedelta(seconds=5),
        CONNECTION_ID,
        ConnectionDirection.ENDPOINT_A_TO_B,
        delay_us=15,
    )
    withdraw = ConnectionTransition(
        START + timedelta(seconds=15),
        ConnectionTransitionOperation.WITHDRAW,
        CONNECTION_ID,
    )
    successor = ForwardingTransition(
        START + timedelta(seconds=5),
        RouteKey(GROUND_STATION, GroundStationRouteTarget(GROUND_STATION)),
        DifferenceOperation.ADDED,
        GROUND_STATION,
    )
    precedence = ForwardingTransitionPrecedence(forwarding, successor)

    plan = Plan(
        start_slice_id=1,
        start_timestamp=START,
        end_slice_id=3,
        end_timestamp=START + timedelta(seconds=20),
        connection_transitions=(withdraw, establish),
        parameter_transitions=(parameter,),
        forwarding_transitions=(successor, forwarding),
        forwarding_precedences=(precedence,),
    )

    assert plan.connection_transitions == (establish, withdraw)
    assert isinstance(plan.temporal_index, MappingProxyType)
    first_group = plan.temporal_index[START + timedelta(seconds=5)]
    assert first_group == (establish, successor, forwarding, parameter)
    assert first_group[0] is establish
    assert first_group[1] is successor
    assert first_group[2] is forwarding
    assert first_group[3] is parameter
    assert {value.nominal_time for value in first_group} == {
        START + timedelta(seconds=5)
    }
    assert tuple(field.name for field in fields(Plan)) == (
        "start_slice_id",
        "start_timestamp",
        "end_slice_id",
        "end_timestamp",
        "connection_transitions",
        "parameter_transitions",
        "forwarding_transitions",
        "forwarding_precedences",
        "temporal_index",
    )
    assert plan.forwarding_precedences == (precedence,)
    assert tuple(
        field.name for field in fields(ForwardingTransitionPrecedence)
    ) == ("predecessor", "successor")
    assert not hasattr(plan, "previous_slice_id")
    assert not hasattr(plan, "current_slice_id")
    with pytest.raises(TypeError):
        plan.temporal_index[START] = ()
    with pytest.raises(FrozenInstanceError):
        plan.end_slice_id = 4


def test_transition_contracts_reject_incomplete_or_out_of_coverage_values() -> None:
    with pytest.raises(TypeError, match="complete Connection"):
        ConnectionTransition(
            START,
            ConnectionTransitionOperation.ESTABLISH,
            CONNECTION_ID,
        )
    with pytest.raises(ValueError, match="must not retain"):
        ConnectionTransition(
            START,
            ConnectionTransitionOperation.WITHDRAW,
            CONNECTION_ID,
            CONNECTION,
        )
    with pytest.raises(ValueError, match="target value"):
        ParameterTransition(
            START,
            CONNECTION_ID,
            ConnectionDirection.ENDPOINT_A_TO_B,
        )
    transition = ConnectionTransition(
        START - timedelta(seconds=1),
        ConnectionTransitionOperation.ESTABLISH,
        CONNECTION_ID,
        CONNECTION,
    )
    with pytest.raises(ValueError, match="start boundary"):
        Plan(
            start_slice_id=1,
            start_timestamp=START,
            end_slice_id=2,
            end_timestamp=START + timedelta(seconds=10),
            connection_transitions=(transition,),
        )
    boundary_transition = ConnectionTransition(
        START,
        ConnectionTransitionOperation.ESTABLISH,
        CONNECTION_ID,
        CONNECTION,
    )
    with pytest.raises(ValueError, match="start boundary"):
        Plan(
            start_slice_id=1,
            start_timestamp=START,
            end_slice_id=2,
            end_timestamp=START + timedelta(seconds=10),
            connection_transitions=(boundary_transition,),
        )
