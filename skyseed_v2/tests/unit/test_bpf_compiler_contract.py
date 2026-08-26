from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone

import pytest

from skyseed_v2.backend import bpf
from skyseed_v2.backend.bpf import BpfCompiledRealization, compile_bpf
from skyseed_v2.backend.bpf.compiler import (
    _IntentReference,
    _OrderingConstraint,
    _constraint_key,
)
from skyseed_v2.configuration.simulator import (
    LatitudeLongitudeRectangleDefinition,
    ZoneDefinition,
    ZoneLayoutDefinition,
)
from skyseed_v2.shared import NodeId
from skyseed_v2.simulator import (
    AccumulatedDeadbandParameterPlanningModel,
    Connection,
    ConnectionDirection,
    ConnectionId,
    ConnectionKind,
    ConnectionSlice,
    ConnectionTransition,
    ConnectionTransitionOperation,
    DefaultCoordinationPolicy,
    DependencyAwareForwardingPlanningModel,
    DifferenceOperation,
    DirectionalConnectionParameters,
    EarthFixedPosition,
    ForwardingTransition,
    ForwardingTransitionPrecedence,
    GroundStationRouteTarget,
    MidpointConnectionPlanningModel,
    Plan,
    RouteKey,
    RouteSlice,
    SimulationDefinition,
    SimulationTimeline,
    Simulator,
    ZoneRouteTarget,
)
from skyseed_v2.simulator.input import (
    GroundStationSimulationInput,
    SatelliteSimulationInput,
    SimulatorStaticInput,
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
TRANSIENT_CONNECTION_ID = ConnectionId(
    ConnectionKind.INTER_SATELLITE,
    SATELLITE,
    SATELLITE_2,
)
ROUTE_KEY = RouteKey(
    SATELLITE,
    GroundStationRouteTarget(GROUND_STATION),
)
TRANSIENT_ROUTE_KEY = RouteKey(
    SATELLITE_2,
    GroundStationRouteTarget(GROUND_STATION),
)


class _PositionModel:
    def compute_positions(self, topology, timestamp):
        return {
            SATELLITE: EarthFixedPosition(7_000_000.0, 0.0, 0.0),
            SATELLITE_2: EarthFixedPosition(7_000_000.0, 10.0, 0.0),
            GROUND_STATION: EarthFixedPosition(6_378_137.0, 0.0, 0.0),
        }


class _SequenceConnectionModel:
    def __init__(self, values):
        self._values = iter(values)

    def compute_connection_slice(
        self, topology, timestamp, positions, previous_connection_slice
    ):
        return next(self._values)


class _SequenceRouteModel:
    def __init__(self, values):
        self._values = iter(values)

    def compute_route_slice(
        self, topology, timestamp, connection_slice, previous_route_slice
    ):
        return next(self._values)


class _OneDifferenceAtATime:
    def select_planning_differences(self, available_differences):
        return tuple(available_differences)

    def select_publication_differences(self, planning_differences):
        return tuple(planning_differences[:1])


class _PublishAllDifferences:
    def select_planning_differences(self, available_differences):
        return tuple(available_differences)

    def select_publication_differences(self, planning_differences):
        return tuple(planning_differences)


@pytest.fixture(scope="module")
def compiled_chain():
    first_parameters = DirectionalConnectionParameters(1_000_000, 10, 20)
    second_parameters = DirectionalConnectionParameters(1_000_000, 15, 20)
    transient_parameters = DirectionalConnectionParameters(
        2_000_000, 5, 10
    )
    first_connection = Connection(
        CONNECTION_ID, first_parameters, first_parameters
    )
    second_connection = Connection(
        CONNECTION_ID, second_parameters, second_parameters
    )
    transient_connection = Connection(
        TRANSIENT_CONNECTION_ID,
        transient_parameters,
        transient_parameters,
    )
    connection_slices = (
        ConnectionSlice((first_connection,)),
        ConnectionSlice((first_connection, transient_connection)),
        ConnectionSlice((first_connection,)),
        ConnectionSlice((second_connection,)),
        ConnectionSlice(),
    )
    route_slices = (
        RouteSlice(((ROUTE_KEY, GROUND_STATION),)),
        RouteSlice(
            (
                (ROUTE_KEY, GROUND_STATION),
                (TRANSIENT_ROUTE_KEY, SATELLITE),
            )
        ),
        RouteSlice(((ROUTE_KEY, GROUND_STATION),)),
        RouteSlice(((ROUTE_KEY, GROUND_STATION),)),
        RouteSlice(),
    )
    static_input = SimulatorStaticInput(
        satellites=(
            SatelliteSimulationInput(
                SATELLITE,
                "sat-1",
                "shell-1",
                "orbit-1",
                0,
                550.0,
                53.0,
                0.0,
                0.0,
                START,
            ),
            SatelliteSimulationInput(
                SATELLITE_2,
                "sat-2",
                "shell-1",
                "orbit-1",
                1,
                550.0,
                53.0,
                0.0,
                10.0,
                START,
            ),
        ),
        ground_stations=(
            GroundStationSimulationInput(
                GROUND_STATION,
                "gs-1",
                0.0,
                0.0,
                0.0,
                "gateway",
                "Skyseed",
            ),
        ),
    )
    simulator = Simulator(
        SimulationDefinition(
            static_input,
            SimulationTimeline(
                START,
                START + timedelta(seconds=40),
                timedelta(seconds=10),
            ),
        )
    )
    simulator.create_topology()
    slicer = simulator.create_slicer(
        position_model=_PositionModel(),
        connection_model=_SequenceConnectionModel(connection_slices),
        route_model=_SequenceRouteModel(route_slices),
    )
    slicer.create_all_slices()
    planner = simulator.create_planner(
        planning_window_policy=_OneDifferenceAtATime(),
        connection_model=MidpointConnectionPlanningModel(
            minimum_dwell=timedelta(seconds=20)
        ),
        parameter_model=AccumulatedDeadbandParameterPlanningModel(),
        forwarding_model=DependencyAwareForwardingPlanningModel(),
        coordination_policy=DefaultCoordinationPolicy(),
    )
    plans = [planner.get_plan()]
    plans.extend(planner.synchronize() for _ in range(4))
    results = [compile_bpf(plans[0], slicer=slicer)]
    for plan in plans[1:]:
        results.append(
            compile_bpf(plan, slicer=slicer, previous=results[-1])
        )
    return slicer, tuple(plans), tuple(results)


def test_compiler_surface_and_separate_runner_owner_are_exact() -> None:
    assert bpf.__all__ == [
        "BpfCompiledRealization",
        "BpfRunner",
        "compile_bpf",
    ]
    assert bpf.compile_bpf is compile_bpf
    assert bpf.BpfCompiledRealization is BpfCompiledRealization
    with pytest.raises(TypeError, match="created by compile_bpf"):
        BpfCompiledRealization()


def test_bootstrap_and_consecutive_baseline_fold_all_domains(
    compiled_chain,
) -> None:
    slicer, plans, results = compiled_chain
    first, materialized_add, materialized_remove, parameter, withdraw = results

    assert (first.start_slice_id, first.end_slice_id) == (0, 1)
    assert first.resource_realizations[0].operation is (
        ConnectionTransitionOperation.ESTABLISH
    )
    assert len(first.resource_realizations[0].attachments) == 2
    assert {value.reason for value in first.parameter_realizations} == {
        "connection-establish"
    }
    assert first.forwarding_realizations[0].operation is DifferenceOperation.ADDED
    assert first.forwarding_realizations[0].current.connection_id == CONNECTION_ID
    expected_direction = (
        ConnectionDirection.ENDPOINT_A_TO_B
        if CONNECTION_ID.endpoint_a == SATELLITE
        else ConnectionDirection.ENDPOINT_B_TO_A
    )
    assert first.forwarding_realizations[0].current.direction is expected_direction

    assert (materialized_add.start_slice_id, materialized_add.end_slice_id) == (
        1,
        2,
    )
    assert materialized_add.resource_realizations[0].operation is (
        ConnectionTransitionOperation.ESTABLISH
    )
    assert materialized_add.forwarding_realizations[0].operation is (
        DifferenceOperation.ADDED
    )
    assert len(materialized_add.intended_resources) == 2
    assert len(slicer.get_slice(2).connection_slice.connections) == 2
    assert materialized_remove.resource_realizations[0].operation is (
        ConnectionTransitionOperation.WITHDRAW
    )
    assert materialized_remove.forwarding_realizations[0].operation is (
        DifferenceOperation.REMOVED
    )
    assert len(materialized_remove.intended_resources) == 1

    sparse = parameter.parameter_realizations
    assert len(sparse) == 2
    assert {value.delay_us for value in sparse} == {15}
    assert all(value.bandwidth_bps is None for value in sparse)
    assert all(value.packet_loss_ppm is None for value in sparse)

    assert withdraw.resource_realizations[0].operation is (
        ConnectionTransitionOperation.WITHDRAW
    )
    assert withdraw.forwarding_realizations[0].operation is (
        DifferenceOperation.REMOVED
    )
    assert (
        withdraw.forwarding_realizations[0].previous.connection_id
        == CONNECTION_ID
    )
    assert withdraw.forwarding_realizations[0].current is None
    assert withdraw.intended_resources == ()
    assert withdraw.intended_parameters == ()
    assert withdraw.intended_forwarding == ()
    assert all(
        previous.end_slice_id == current.start_slice_id
        for previous, current in zip(plans, plans[1:])
    )


def test_compilation_is_deterministic_and_deeply_immutable(compiled_chain) -> None:
    slicer, plans, results = compiled_chain
    repeated = compile_bpf(plans[0], slicer=slicer)

    assert repeated == results[0]
    with pytest.raises(FrozenInstanceError):
        repeated.end_slice_id = 99
    with pytest.raises(FrozenInstanceError):
        repeated.resource_realizations[0].nominal_time = START


def test_invalid_lineage_and_plan_contradiction_fail_closed(compiled_chain) -> None:
    slicer, plans, results = compiled_chain
    with pytest.raises(ValueError, match="must not have previous"):
        compile_bpf(plans[0], slicer=slicer, previous=results[0])
    with pytest.raises(ValueError, match="requires previous"):
        compile_bpf(plans[1], slicer=slicer)
    with pytest.raises(ValueError, match="not continuous"):
        compile_bpf(plans[-1], slicer=slicer, previous=results[0])

    original = plans[0].forwarding_transitions[0]
    contradictory = replace(
        original,
        next_hop=SATELLITE_2,
    )
    invalid_plan = Plan(
        start_slice_id=plans[0].start_slice_id,
        start_timestamp=plans[0].start_timestamp,
        end_slice_id=plans[0].end_slice_id,
        end_timestamp=plans[0].end_timestamp,
        connection_transitions=plans[0].connection_transitions,
        parameter_transitions=plans[0].parameter_transitions,
        forwarding_transitions=(contradictory,),
        forwarding_precedences=(),
    )
    with pytest.raises(ValueError, match="contradicts Slice facts"):
        compile_bpf(invalid_plan, slicer=slicer)

    missing_resource = Plan(
        start_slice_id=plans[0].start_slice_id,
        start_timestamp=plans[0].start_timestamp,
        end_slice_id=plans[0].end_slice_id,
        end_timestamp=plans[0].end_timestamp,
        forwarding_transitions=plans[0].forwarding_transitions,
    )
    with pytest.raises(ValueError, match="inactive planned Connection"):
        compile_bpf(missing_resource, slicer=slicer)


def test_same_next_hop_changed_is_lowered_with_formal_precedence(
    compiled_chain,
) -> None:
    slicer, _, results = compiled_chain
    current_slice = slicer.get_slice(2)
    transient_connection = current_slice.connection_slice.get_connection(
        TRANSIENT_CONNECTION_ID
    )
    nominal_time = current_slice.timestamp
    assert nominal_time is not None
    assert (
        slicer.get_slice(1).route_slice.get_next_hop(
            ROUTE_KEY.source, ROUTE_KEY.target
        )
        == current_slice.route_slice.get_next_hop(
            ROUTE_KEY.source, ROUTE_KEY.target
        )
        == GROUND_STATION
    )
    same_next_hop = ForwardingTransition(
        nominal_time,
        ROUTE_KEY,
        DifferenceOperation.CHANGED,
        GROUND_STATION,
    )
    upstream_add = ForwardingTransition(
        nominal_time,
        TRANSIENT_ROUTE_KEY,
        DifferenceOperation.ADDED,
        SATELLITE,
    )
    precedence = ForwardingTransitionPrecedence(
        same_next_hop,
        upstream_add,
    )
    plan = Plan(
        start_slice_id=1,
        start_timestamp=slicer.get_slice(1).timestamp,
        end_slice_id=2,
        end_timestamp=nominal_time,
        connection_transitions=(
            ConnectionTransition(
                START + timedelta(seconds=5),
                ConnectionTransitionOperation.ESTABLISH,
                TRANSIENT_CONNECTION_ID,
                transient_connection,
            ),
        ),
        forwarding_transitions=(same_next_hop, upstream_add),
        forwarding_precedences=(precedence,),
    )

    result = compile_bpf(plan, slicer=slicer, previous=results[0])

    assert [
        value.operation for value in result.forwarding_realizations
    ] == [DifferenceOperation.CHANGED, DifferenceOperation.ADDED]
    changed = result.forwarding_realizations[0]
    assert changed.previous is not None
    assert changed.current is not None
    assert changed.previous.next_hop == changed.current.next_hop
    assert changed.route_key == ROUTE_KEY
    plan_constraints = [
        value
        for value in result.constraints
        if value.reason == "plan-forwarding-precedence"
    ]
    assert len(plan_constraints) == 1
    assert plan_constraints[0].predecessor.domain == "forwarding"
    assert plan_constraints[0].predecessor.index == 0
    assert plan_constraints[0].successor.domain == "forwarding"
    assert plan_constraints[0].successor.index == 1


def test_zone_terminal_change_materializes_required_transient_state() -> None:
    access_2_id = ConnectionId(
        ConnectionKind.GROUND_STATION_ACCESS,
        SATELLITE_2,
        GROUND_STATION,
    )
    parameters = DirectionalConnectionParameters(1_000_000, 10, 20)
    access_1 = Connection(CONNECTION_ID, parameters, parameters)
    access_2 = Connection(access_2_id, parameters, parameters)
    zone_target = ZoneRouteTarget("zone-a")
    zone_route_key = RouteKey(GROUND_STATION, zone_target)
    ground_target = GroundStationRouteTarget(GROUND_STATION)
    connection_slices = (
        ConnectionSlice((access_1,), {"zone-a": SATELLITE}),
        ConnectionSlice(
            (access_1, access_2), {"zone-a": SATELLITE_2}
        ),
        ConnectionSlice((access_1,), {"zone-a": SATELLITE}),
    )
    route_slices = (
        RouteSlice(
            (
                (RouteKey(SATELLITE, ground_target), GROUND_STATION),
                (zone_route_key, SATELLITE),
            )
        ),
        RouteSlice(
            (
                (RouteKey(SATELLITE, ground_target), GROUND_STATION),
                (RouteKey(SATELLITE_2, ground_target), GROUND_STATION),
                (zone_route_key, SATELLITE_2),
            )
        ),
        RouteSlice(
            (
                (RouteKey(SATELLITE, ground_target), GROUND_STATION),
                (zone_route_key, SATELLITE),
            )
        ),
    )
    static_input = SimulatorStaticInput(
        satellites=(
            SatelliteSimulationInput(
                SATELLITE,
                "sat-1",
                "shell-1",
                "orbit-1",
                0,
                550.0,
                53.0,
                0.0,
                0.0,
                START,
            ),
            SatelliteSimulationInput(
                SATELLITE_2,
                "sat-2",
                "shell-1",
                "orbit-1",
                1,
                550.0,
                53.0,
                0.0,
                10.0,
                START,
            ),
        ),
        ground_stations=(
            GroundStationSimulationInput(
                GROUND_STATION,
                "gs-1",
                0.0,
                0.0,
                0.0,
                "gateway",
                "Skyseed",
            ),
        ),
    )
    simulator = Simulator(
        SimulationDefinition(
            static_input,
            SimulationTimeline(
                START,
                START + timedelta(seconds=20),
                timedelta(seconds=10),
            ),
        )
    )
    simulator.create_topology(
        zone_layout_definition=ZoneLayoutDefinition(
            zones=(
                ZoneDefinition(
                    zone_id="zone-a",
                    geometry=LatitudeLongitudeRectangleDefinition(
                        -10.0, 10.0, -20.0, 20.0
                    ),
                ),
            ),
            allow_overlap=True,
        )
    )
    slicer = simulator.create_slicer(
        position_model=_PositionModel(),
        connection_model=_SequenceConnectionModel(connection_slices),
        route_model=_SequenceRouteModel(route_slices),
    )
    slicer.create_all_slices()
    planner = simulator.create_planner(
        planning_window_policy=_PublishAllDifferences(),
        connection_model=MidpointConnectionPlanningModel(
            minimum_dwell=timedelta(seconds=20)
        ),
        parameter_model=AccumulatedDeadbandParameterPlanningModel(),
        forwarding_model=DependencyAwareForwardingPlanningModel(),
        coordination_policy=DefaultCoordinationPolicy(),
    )
    plan = planner.get_plan()

    assert [
        dict(slicer.get_slice(slice_id).connection_slice.zone_service_assignments)[
            "zone-a"
        ]
        for slice_id in (1, 2, 3)
    ] == [SATELLITE, SATELLITE_2, SATELLITE]
    assert tuple(
        value.operation
        for value in plan.connection_transitions
        if value.connection_id == access_2_id
    ) == (
        ConnectionTransitionOperation.ESTABLISH,
        ConnectionTransitionOperation.WITHDRAW,
    )
    zone_transitions = [
        value
        for value in plan.forwarding_transitions
        if value.route_key == zone_route_key
    ]
    assert tuple(value.operation for value in zone_transitions) == (
        DifferenceOperation.ADDED,
        DifferenceOperation.CHANGED,
        DifferenceOperation.CHANGED,
    )
    assert tuple(value.next_hop for value in zone_transitions) == (
        SATELLITE,
        SATELLITE_2,
        SATELLITE,
    )
    assert not any(
        value.decision == "suppressed"
        for value in planner.get_planning_decisions()
    )

    result = compile_bpf(plan, slicer=slicer)

    assert [
        value.service_satellite_id for value in result.zone_edge_realizations
    ] == [SATELLITE, SATELLITE_2, SATELLITE]
    assert result.intended_zone_edges[0].service_satellite_id == SATELLITE
    assert result.intended_forwarding[0].route_key == zone_route_key
    assert result.intended_forwarding[0].next_hop == SATELLITE


def test_backend_technical_constraints_cover_ready_and_retire_edges(
    compiled_chain,
) -> None:
    _, _, results = compiled_chain
    reasons = {
        constraint.reason
        for result in results
        for constraint in result.constraints
    }
    assert {
        "resource-ready-before-qdisc",
        "resource-ready-before-forwarding",
        "qdisc-ready-before-forwarding",
        "stop-use-before-resource-retirement",
    } <= reasons


def test_indexed_constraint_compilation_matches_ordered_nested_oracle(
    compiled_chain,
) -> None:
    slicer, plans, results = compiled_chain
    previous = None
    for plan, result in zip(plans, results, strict=True):
        assert result.constraints == _constraint_oracle(
            slicer,
            plan,
            result,
            previous,
        )
        previous = result


def _constraint_oracle(slicer, plan, result, previous):
    resources = result.resource_realizations
    qdiscs = result.parameter_realizations
    forwarding = result.forwarding_realizations
    zone_edges = result.zone_edge_realizations
    baseline_forwarding = (
        {}
        if previous is None
        else {value.route_key: value for value in previous.intended_forwarding}
    )
    constraints = set()
    forwarding_positions = {
        id(value): index
        for index, value in enumerate(plan.forwarding_transitions)
    }
    for precedence in plan.forwarding_precedences:
        constraints.add(
            _OrderingConstraint(
                _IntentReference(
                    "forwarding",
                    forwarding_positions[id(precedence.predecessor)],
                ),
                _IntentReference(
                    "forwarding",
                    forwarding_positions[id(precedence.successor)],
                ),
                "plan-forwarding-precedence",
            )
        )
    for resource_index, resource in enumerate(resources):
        if resource.operation is not ConnectionTransitionOperation.ESTABLISH:
            continue
        for qdisc_index, qdisc in enumerate(qdiscs):
            if (
                qdisc.connection_id == resource.connection_id
                and qdisc.nominal_time == resource.nominal_time
                and qdisc.reason == "connection-establish"
            ):
                constraints.add(
                    _OrderingConstraint(
                        _IntentReference("resource", resource_index),
                        _IntentReference("parameter", qdisc_index),
                        "resource-ready-before-qdisc",
                    )
                )
    for forwarding_index, intent in enumerate(forwarding):
        if intent.current is None:
            continue
        establishments = [
            (index, value)
            for index, value in enumerate(resources)
            if value.connection_id == intent.current.connection_id
            and value.operation is ConnectionTransitionOperation.ESTABLISH
            and value.nominal_time <= intent.nominal_time
        ]
        if establishments:
            index, _ = max(
                establishments,
                key=lambda value: (value[1].nominal_time, value[0]),
            )
            constraints.add(
                _OrderingConstraint(
                    _IntentReference("resource", index),
                    _IntentReference("forwarding", forwarding_index),
                    "resource-ready-before-forwarding",
                )
            )
        parameters = [
            (index, value)
            for index, value in enumerate(qdiscs)
            if value.connection_id == intent.current.connection_id
            and value.direction is intent.current.direction
            and value.nominal_time <= intent.nominal_time
        ]
        if parameters:
            index, _ = max(
                parameters,
                key=lambda value: (value[1].nominal_time, value[0]),
            )
            constraints.add(
                _OrderingConstraint(
                    _IntentReference("parameter", index),
                    _IntentReference("forwarding", forwarding_index),
                    "qdisc-ready-before-forwarding",
                )
            )
    for resource_index, resource in enumerate(resources):
        if resource.operation is not ConnectionTransitionOperation.WITHDRAW:
            continue
        projected = dict(baseline_forwarding)
        for intent in forwarding:
            if intent.nominal_time > resource.nominal_time:
                break
            if intent.current is None:
                projected.pop(intent.route_key, None)
            else:
                projected[intent.route_key] = intent.current
        assert all(
            value.connection_id != resource.connection_id
            for value in projected.values()
        )
        for forwarding_index, intent in enumerate(forwarding):
            if (
                intent.previous is not None
                and intent.previous.connection_id == resource.connection_id
                and (
                    intent.current is None
                    or intent.current.connection_id != resource.connection_id
                )
                and intent.nominal_time <= resource.nominal_time
            ):
                constraints.add(
                    _OrderingConstraint(
                        _IntentReference("forwarding", forwarding_index),
                        _IntentReference("resource", resource_index),
                        "stop-use-before-resource-retirement",
                    )
                )
    for zone_index, zone_edge in enumerate(zone_edges):
        for forwarding_index, intent in enumerate(forwarding):
            target = intent.route_key.target
            if not isinstance(target, ZoneRouteTarget):
                continue
            source_slice = next(
                slicer.get_slice(slice_id)
                for slice_id in range(
                    plan.start_slice_id + 1,
                    plan.end_slice_id + 1,
                )
                if (
                    (
                        slicer.get_slice(slice_id - 1).timestamp is None
                        and intent.nominal_time
                        == slicer.get_slice(slice_id).timestamp
                    )
                    or (
                        slicer.get_slice(slice_id - 1).timestamp is not None
                        and slicer.get_slice(slice_id - 1).timestamp
                        < intent.nominal_time
                        <= slicer.get_slice(slice_id).timestamp
                    )
                )
            )
            if (
                target.zone_id != zone_edge.zone_id
                or source_slice.timestamp != zone_edge.nominal_time
            ):
                continue
            reason = (
                "forwarding-stopped-before-zone-retirement"
                if zone_edge.operation is DifferenceOperation.REMOVED
                else "forwarding-ready-before-zone-activation"
            )
            constraints.add(
                _OrderingConstraint(
                    _IntentReference("forwarding", forwarding_index),
                    _IntentReference("zone-edge", zone_index),
                    reason,
                )
            )
    return tuple(sorted(constraints, key=_constraint_key))
