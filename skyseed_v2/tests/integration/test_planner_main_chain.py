from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from skyseed_v2.shared import NodeId
from skyseed_v2.simulator import (
    AccumulatedDeadbandParameterPlanningModel,
    Connection,
    ConnectionId,
    ConnectionKind,
    ConnectionSlice,
    ConnectionTransitionOperation,
    DefaultCoordinationPolicy,
    DependencyAwareForwardingPlanningModel,
    DifferenceOperation,
    DirectionalConnectionParameters,
    EarthFixedPosition,
    GroundStationRouteTarget,
    ForwardingTransition,
    MidpointConnectionPlanningModel,
    ParameterTransition,
    ConnectionDirection,
    RouteKey,
    RouteSlice,
    SampleBoundaryConnectionPlanningModel,
    SimulationDefinition,
    SimulationTimeline,
    Simulator,
)
from skyseed_v2.simulator.input import (
    GroundStationSimulationInput,
    SatelliteSimulationInput,
    SimulatorStaticInput,
)


START = datetime(2026, 1, 1, tzinfo=timezone.utc)
SATELLITE = NodeId(65001, "satellite", "sat-1")
SATELLITE_2 = NodeId(65001, "satellite", "sat-2")
SATELLITE_3 = NodeId(65001, "satellite", "sat-3")
SATELLITE_4 = NodeId(65001, "satellite", "sat-4")
GROUND_STATION = NodeId(65001, "ground-station", "gs-1")
TARGET = GroundStationRouteTarget(GROUND_STATION)
ROUTE_KEY = RouteKey(SATELLITE, TARGET)
CONNECTION_ID = ConnectionId(
    ConnectionKind.GROUND_STATION_ACCESS,
    SATELLITE,
    GROUND_STATION,
)
ISL_ID = ConnectionId(
    ConnectionKind.INTER_SATELLITE,
    SATELLITE,
    SATELLITE_2,
)
SECOND_ACCESS_ID = ConnectionId(
    ConnectionKind.GROUND_STATION_ACCESS,
    SATELLITE_2,
    GROUND_STATION,
)


class _PositionModel:
    def __init__(self, extra_satellites=()):
        self._extra_satellites = tuple(extra_satellites)

    def compute_positions(self, topology, timestamp):
        positions = {
            SATELLITE: EarthFixedPosition(7_000_000.0, 0.0, 0.0),
            SATELLITE_2: EarthFixedPosition(7_000_000.0, 10.0, 0.0),
            GROUND_STATION: EarthFixedPosition(6_378_137.0, 0.0, 0.0),
        }
        positions.update(
            {
                node_id: EarthFixedPosition(
                    7_000_000.0, float(index * 10), 0.0
                )
                for index, node_id in enumerate(
                    self._extra_satellites, start=2
                )
            }
        )
        return positions


class _ConnectionSequenceModel:
    def __init__(self, slices):
        self._slices = tuple(slices)
        self.calls = 0

    def compute_connection_slice(
        self, topology, timestamp, positions, previous_connection_slice
    ):
        value = self._slices[self.calls]
        self.calls += 1
        return value


class _RouteSequenceModel:
    def __init__(self, slices):
        self._slices = tuple(slices)
        self.calls = 0

    def compute_route_slice(
        self, topology, timestamp, connection_slice, previous_route_slice
    ):
        value = self._slices[self.calls]
        self.calls += 1
        return value


class _CountingConnectionPlanningModel:
    def __init__(self, minimum_dwell=None):
        self.delegate = MidpointConnectionPlanningModel(
            minimum_dwell=minimum_dwell
        )
        self.calls = []

    def plan_connection_transitions(self, **kwargs):
        self.calls.append(
            (
                tuple(
                    value.current_slice_id
                    for value in kwargs["differences"]
                ),
                tuple(
                    value.current_slice_id
                    for value in kwargs["affected_differences"]
                ),
                len(kwargs["previous_transitions"]),
            )
        )
        return self.delegate.plan_connection_transitions(**kwargs)

    def is_suppression_candidate(self, establish, withdraw):
        return self.delegate.is_suppression_candidate(establish, withdraw)


class _CountingParameterPlanningModel:
    def __init__(self, threshold):
        self.delegate = AccumulatedDeadbandParameterPlanningModel(
            delay_us_threshold=threshold
        )
        self.calls = []

    def plan_parameter_transitions(self, **kwargs):
        self.calls.append(
            (
                tuple(
                    value.current_slice_id
                    for value in kwargs["differences"]
                ),
                tuple(
                    value.current_slice_id
                    for value in kwargs["affected_differences"]
                ),
                len(kwargs["previous_transitions"]),
            )
        )
        return self.delegate.plan_parameter_transitions(**kwargs)


class _CountingForwardingPlanningModel:
    def __init__(self):
        self.delegate = DependencyAwareForwardingPlanningModel()
        self.calls = []

    def plan_forwarding_transitions(self, **kwargs):
        assert "slicer" not in kwargs
        self.calls.append(
            (
                tuple(
                    value.current_slice_id
                    for value in kwargs["differences"]
                ),
                tuple(
                    value.current_slice_id
                    for value in kwargs["affected_differences"]
                ),
                len(kwargs["previous_transitions"]),
            )
        )
        return self.delegate.plan_forwarding_transitions(**kwargs)

    def plan_forwarding_precedences(self, **kwargs):
        assert "slicer" not in kwargs
        return self.delegate.plan_forwarding_precedences(**kwargs)


class _LateForwardingPlanningModel:
    def plan_forwarding_transitions(
        self,
        *,
        differences,
        affected_differences,
        previous_transitions,
        connection_transitions,
        route_path_connection_ids,
    ):
        return (
            *previous_transitions,
            *(
                ForwardingTransition(
                    difference.current_timestamp,
                    patch.route_key,
                    patch.operation,
                    patch.next_hop,
                )
                for difference in affected_differences
                for patch in difference.route_patches
            )
        )

    def plan_forwarding_precedences(self, **kwargs):
        return ()


class _PublishOneDifferenceWindowPolicy:
    def select_planning_differences(self, available_differences):
        return tuple(available_differences)

    def select_publication_differences(self, planning_differences):
        return tuple(planning_differences[:1])


class _RetainOnePlanningDifferenceWindowPolicy:
    def select_planning_differences(self, available_differences):
        return tuple(available_differences)

    def select_publication_differences(self, planning_differences):
        if len(planning_differences) == 1:
            return tuple(planning_differences)
        return tuple(planning_differences[:-1])


class _PublishTwoDifferencesWindowPolicy:
    def select_planning_differences(self, available_differences):
        return tuple(available_differences)

    def select_publication_differences(self, planning_differences):
        return tuple(planning_differences[:2])


class _SubSliceParameterPlanningModel:
    def __init__(self, *, outside_interval=False):
        self.outside_interval = outside_interval

    def plan_parameter_transitions(
        self,
        *,
        differences,
        affected_differences,
        previous_transitions,
        observed_parameters,
        planned_parameters,
    ):
        result = list(previous_transitions)
        for difference in affected_differences:
            for patch in difference.connection_parameter_patches:
                nominal_time = difference.previous_timestamp + (
                    difference.current_timestamp - difference.previous_timestamp
                ) / 2
                if self.outside_interval:
                    nominal_time = difference.previous_timestamp - timedelta(
                        microseconds=1
                    )
                result.append(
                    ParameterTransition(
                        nominal_time=nominal_time,
                        connection_id=patch.connection_id,
                        direction=patch.direction,
                        bandwidth_bps=patch.bandwidth_bps,
                        delay_us=patch.delay_us,
                        packet_loss_ppm=patch.packet_loss_ppm,
                    )
                )
        return tuple(result)


class _RetainCoordinationPolicy:
    def __init__(self):
        self.calls = []

    def coordinate_transitions(self, **kwargs):
        self.calls.append(
            (
                len(kwargs["suppression_candidates"]),
                len(kwargs["parameter_transitions"]),
                len(kwargs["forwarding_transitions"]),
            )
        )
        return (
            kwargs["connection_transitions"],
            kwargs["parameter_transitions"],
            kwargs["forwarding_transitions"],
            (),
        )


class _FailSecondCoordinationPolicy:
    def __init__(self):
        self.calls = 0
        self.delegate = DefaultCoordinationPolicy()

    def coordinate_transitions(self, **kwargs):
        self.calls += 1
        result = self.delegate.coordinate_transitions(**kwargs)
        if self.calls == 1:
            return result
        connections, parameters, forwarding, decisions = result
        return connections, parameters, forwarding[:-1], decisions


class _RecordingWindowPolicy:
    def __init__(self, events):
        self.events = events

    def select_planning_differences(self, available_differences):
        self.events.append(
            (
                "planning_window",
                tuple(d.current_slice_id for d in available_differences),
            )
        )
        return tuple(available_differences)

    def select_publication_differences(self, planning_differences):
        self.events.append(
            (
                "publication_window",
                tuple(d.current_slice_id for d in planning_differences),
            )
        )
        return tuple(planning_differences)


class _PlanningWindowSensitiveConnectionModel:
    def __init__(self, events):
        self.events = events
        self.delegate = MidpointConnectionPlanningModel()

    def plan_connection_transitions(self, **kwargs):
        self.events.append(
            ("connection", tuple(d.current_slice_id for d in kwargs["differences"]))
        )
        transitions = self.delegate.plan_connection_transitions(**kwargs)
        return tuple(
            replace(
                transition,
                nominal_time=START
                + timedelta(seconds=len(kwargs["differences"])),
            )
            if transition.connection_id == ISL_ID
            and transition.operation
            is ConnectionTransitionOperation.ESTABLISH
            else transition
            for transition in transitions
        )

    def is_suppression_candidate(self, establish, withdraw):
        return False


class _PlanningWindowSensitiveParameterModel:
    def __init__(self, events):
        self.events = events

    def plan_parameter_transitions(self, **kwargs):
        self.events.append(
            ("parameter", tuple(d.current_slice_id for d in kwargs["differences"]))
        )
        if len(kwargs["differences"]) < 3:
            return kwargs["previous_transitions"]
        return AccumulatedDeadbandParameterPlanningModel(
            delay_us_threshold=1
        ).plan_parameter_transitions(**kwargs)


class _PlanningWindowSensitiveForwardingModel:
    def __init__(self, events):
        self.events = events
        self.delegate = DependencyAwareForwardingPlanningModel()

    def plan_forwarding_transitions(self, **kwargs):
        assert "slicer" not in kwargs
        self.events.append(
            ("forwarding", tuple(d.current_slice_id for d in kwargs["differences"]))
        )
        transitions = self.delegate.plan_forwarding_transitions(**kwargs)
        return tuple(
            replace(
                transition,
                nominal_time=START
                + timedelta(seconds=len(kwargs["differences"])),
            )
            if transition.route_key == ROUTE_KEY
            else transition
            for transition in transitions
        )

    def plan_forwarding_precedences(self, **kwargs):
        return self.delegate.plan_forwarding_precedences(**kwargs)


class _SharedAlgorithms:
    def __init__(self):
        self.connection = MidpointConnectionPlanningModel()
        self.parameter = AccumulatedDeadbandParameterPlanningModel()
        self.forwarding = DependencyAwareForwardingPlanningModel()

    def select_planning_differences(self, available_differences):
        return tuple(available_differences)

    def select_publication_differences(self, planning_differences):
        return tuple(planning_differences)

    def plan_connection_transitions(self, **kwargs):
        return self.connection.plan_connection_transitions(**kwargs)

    def is_suppression_candidate(self, establish, withdraw):
        return False

    def plan_parameter_transitions(self, **kwargs):
        return self.parameter.plan_parameter_transitions(**kwargs)

    def plan_forwarding_transitions(self, **kwargs):
        return self.forwarding.plan_forwarding_transitions(**kwargs)

    def plan_forwarding_precedences(self, **kwargs):
        return self.forwarding.plan_forwarding_precedences(**kwargs)

    def coordinate_transitions(self, **kwargs):
        return (
            kwargs["connection_transitions"],
            kwargs["parameter_transitions"],
            kwargs["forwarding_transitions"],
            (),
        )


def _connection(delay_us: int) -> Connection:
    parameters = DirectionalConnectionParameters(1_000_000, delay_us, 0)
    return Connection(CONNECTION_ID, parameters, parameters)


def _connection_with_id(
    connection_id: ConnectionId, delay_us: int = 10
) -> Connection:
    parameters = DirectionalConnectionParameters(1_000_000, delay_us, 0)
    return Connection(connection_id, parameters, parameters)


def _simulator(connection_slices, route_slices, *, extra_satellites=()):
    extra_satellites = tuple(extra_satellites)
    if len(connection_slices) != len(route_slices):
        raise ValueError("connection_slices and route_slices must align")
    if not connection_slices:
        raise ValueError("at least one real Slice is required")
    static_input = SimulatorStaticInput(
        satellites=(
            SatelliteSimulationInput(
                node_id=SATELLITE,
                satellite_id="sat-1",
                shell_id="shell-1",
                orbit_id="orbit-1",
                orbit_index=0,
                altitude_km=550.0,
                inclination_deg=53.0,
                right_ascension_deg=0.0,
                mean_anomaly_deg=0.0,
                epoch_utc=START,
            ),
            SatelliteSimulationInput(
                node_id=SATELLITE_2,
                satellite_id="sat-2",
                shell_id="shell-1",
                orbit_id="orbit-1",
                orbit_index=1,
                altitude_km=550.0,
                inclination_deg=53.0,
                right_ascension_deg=0.0,
                mean_anomaly_deg=10.0,
                epoch_utc=START,
            ),
            *(
                SatelliteSimulationInput(
                    node_id=node_id,
                    satellite_id=node_id.entity_id,
                    shell_id="shell-1",
                    orbit_id="orbit-1",
                    orbit_index=index,
                    altitude_km=550.0,
                    inclination_deg=53.0,
                    right_ascension_deg=0.0,
                    mean_anomaly_deg=float(index * 10),
                    epoch_utc=START,
                )
                for index, node_id in enumerate(
                    extra_satellites, start=2
                )
            ),
        ),
        ground_stations=(
            GroundStationSimulationInput(
                node_id=GROUND_STATION,
                ground_station_id="gs-1",
                latitude_deg=0.0,
                longitude_deg=0.0,
                elevation_m=0.0,
                station_type="gateway",
                operator="Skyseed",
            ),
        ),
    )
    simulator = Simulator(
        SimulationDefinition(
            static_input,
            SimulationTimeline(
                START,
                START
                + timedelta(seconds=10 * (len(connection_slices) - 1)),
                timedelta(seconds=10),
            ),
        )
    )
    simulator.create_topology()
    slicer = simulator.create_slicer(
        position_model=_PositionModel(extra_satellites),
        connection_model=_ConnectionSequenceModel(connection_slices),
        route_model=_RouteSequenceModel(route_slices),
    )
    return simulator, slicer


def _short_connection_fixture():
    second_route = RouteKey(SATELLITE_2, TARGET)
    connection_slices = (
        ConnectionSlice((_connection_with_id(SECOND_ACCESS_ID),)),
        ConnectionSlice(
            (
                _connection_with_id(SECOND_ACCESS_ID),
                _connection_with_id(ISL_ID, 10),
            )
        ),
        ConnectionSlice(
            (
                _connection_with_id(SECOND_ACCESS_ID),
                _connection_with_id(ISL_ID, 20),
            )
        ),
        ConnectionSlice(
            (
                _connection_with_id(SECOND_ACCESS_ID),
                _connection_with_id(CONNECTION_ID),
            )
        ),
    )
    route_slices = (
        RouteSlice(((second_route, GROUND_STATION),)),
        RouteSlice(
            (
                (ROUTE_KEY, SATELLITE_2),
                (second_route, GROUND_STATION),
            )
        ),
        RouteSlice(
            (
                (ROUTE_KEY, SATELLITE_2),
                (second_route, GROUND_STATION),
            )
        ),
        RouteSlice(
            (
                (ROUTE_KEY, GROUND_STATION),
                (second_route, GROUND_STATION),
            )
        ),
    )
    simulator, slicer = _simulator(connection_slices, route_slices)
    return simulator, slicer, second_route


def test_planner_owns_exact_sibling_and_processes_only_new_difference_tail() -> None:
    connection_slices = (
        ConnectionSlice((_connection(10),)),
        ConnectionSlice((_connection(12),)),
        ConnectionSlice((_connection(16),)),
    )
    route_slices = (
        RouteSlice(((ROUTE_KEY, GROUND_STATION),)),
        RouteSlice(((ROUTE_KEY, GROUND_STATION),)),
        RouteSlice(((ROUTE_KEY, GROUND_STATION),)),
    )
    simulator, slicer = _simulator(connection_slices, route_slices)
    slicer.create_slices(2)
    connection_model = _CountingConnectionPlanningModel()
    parameter_model = _CountingParameterPlanningModel(5)
    forwarding_model = _CountingForwardingPlanningModel()

    planner = simulator.create_planner(
        connection_model=connection_model,
        parameter_model=parameter_model,
        forwarding_model=forwarding_model,
        coordination_policy=DefaultCoordinationPolicy(),
    )
    first_plan = planner.get_plan()
    first_route_context = planner._route_path_connection_ids[1]

    assert simulator.get_planner() is planner
    assert planner._slicer is slicer
    assert connection_model.calls == [((1, 2), (1, 2), 0)]
    assert parameter_model.calls == [((1, 2), (1, 2), 0)]
    assert forwarding_model.calls == [((1, 2), (1, 2), 0)]
    assert planner._publication_count == 1
    assert first_plan.connection_transitions[0].nominal_time == START
    with pytest.raises(RuntimeError, match="already"):
        simulator.create_planner(
            connection_model=_CountingConnectionPlanningModel(),
            parameter_model=_CountingParameterPlanningModel(5),
            forwarding_model=_CountingForwardingPlanningModel(),
            coordination_policy=DefaultCoordinationPolicy(),
        )

    slicer.create_next_slice()
    second_plan = planner.synchronize()

    assert connection_model.calls == [
        ((1, 2), (1, 2), 0),
        ((3,), (3,), 0),
    ]
    assert parameter_model.calls == [
        ((1, 2), (1, 2), 0),
        ((3,), (3,), 0),
    ]
    assert forwarding_model.calls == [
        ((1, 2), (1, 2), 0),
        ((3,), (3,), 0),
    ]
    assert (second_plan.start_slice_id, second_plan.end_slice_id) == (2, 3)
    assert planner._publication_count == 2
    assert second_plan.connection_transitions == ()
    assert planner._route_path_connection_ids[1] is first_route_context
    assert len(second_plan.parameter_transitions) == 2
    assert {
        transition.delay_us
        for transition in second_plan.parameter_transitions
    } == {16}
    assert planner.synchronize() is second_plan
    assert len(connection_model.calls) == 2
    assert planner._publication_count == 2
    merged = tuple(
        decision
        for decision in planner.get_planning_decisions()
        if decision.decision == "merged"
    )
    assert len(merged) == 1
    assert merged[0].source_difference_ids == (2, 3)
    assert all(
        "below" not in decision.reason
        for decision in planner.get_planning_decisions()
    )


def test_planning_window_is_selected_before_models_and_seen_in_full() -> None:
    events = []
    second_route = RouteKey(SATELLITE_2, TARGET)
    connection_slices = (
        ConnectionSlice((_connection_with_id(SECOND_ACCESS_ID),)),
        ConnectionSlice(
            (
                _connection_with_id(SECOND_ACCESS_ID),
                _connection_with_id(ISL_ID, 10),
            )
        ),
        ConnectionSlice(
            (
                _connection_with_id(SECOND_ACCESS_ID),
                _connection_with_id(ISL_ID, 20),
            )
        ),
    )
    route_slices = (
        RouteSlice(((second_route, GROUND_STATION),)),
        RouteSlice(
            (
                (ROUTE_KEY, SATELLITE_2),
                (second_route, GROUND_STATION),
            )
        ),
        RouteSlice(
            (
                (ROUTE_KEY, SATELLITE_2),
                (second_route, GROUND_STATION),
            )
        ),
    )
    simulator, slicer = _simulator(connection_slices, route_slices)
    slicer.create_slices(3)
    route_model_calls = slicer.get_route_model().calls

    planner = simulator.create_planner(
        planning_window_policy=_RecordingWindowPolicy(events),
        connection_model=_PlanningWindowSensitiveConnectionModel(events),
        parameter_model=_PlanningWindowSensitiveParameterModel(events),
        forwarding_model=_PlanningWindowSensitiveForwardingModel(events),
        coordination_policy=_RetainCoordinationPolicy(),
    )
    plan = planner.get_plan()

    assert events == [
        ("planning_window", (1, 2, 3)),
        ("publication_window", (1, 2, 3)),
        ("connection", (1, 2, 3)),
        ("parameter", (1, 2, 3)),
        ("forwarding", (1, 2, 3)),
    ]
    assert planner._publication_count == 1
    assert next(
        transition
        for transition in plan.connection_transitions
        if transition.connection_id == ISL_ID
    ).nominal_time == START + timedelta(seconds=3)
    assert {
        transition.delay_us for transition in plan.parameter_transitions
    } == {20}
    assert next(
        transition
        for transition in plan.forwarding_transitions
        if transition.route_key == ROUTE_KEY
    ).nominal_time == START + timedelta(seconds=3)
    assert slicer.get_route_model().calls == route_model_calls


def test_unpublished_planning_window_retains_cross_boundary_suppression() -> None:
    simulator, slicer, _ = _short_connection_fixture()
    slicer.create_all_slices()
    connection_model = _CountingConnectionPlanningModel(
        minimum_dwell=timedelta(seconds=30)
    )
    parameter_model = _CountingParameterPlanningModel(1)
    forwarding_model = _CountingForwardingPlanningModel()
    planner = simulator.create_planner(
        planning_window_policy=_RetainOnePlanningDifferenceWindowPolicy(),
        connection_model=connection_model,
        parameter_model=parameter_model,
        forwarding_model=forwarding_model,
        coordination_policy=DefaultCoordinationPolicy(),
    )

    first = planner.get_plan()
    first_content = (
        first.connection_transitions,
        first.parameter_transitions,
        first.forwarding_transitions,
        first.forwarding_precedences,
    )
    assert (first.start_slice_id, first.end_slice_id) == (0, 3)
    assert connection_model.calls == [((1, 2, 3, 4), (1, 2, 3, 4), 0)]
    assert ISL_ID in {
        transition.connection_id
        for transition in first.connection_transitions
    }
    assert ISL_ID in planner._published_active_establishments
    assert tuple(
        difference.current_slice_id
        for difference in planner._planning_differences
    ) == (4,)

    second = planner.synchronize()

    assert (second.start_slice_id, second.end_slice_id) == (3, 4)
    assert first_content == (
        first.connection_transitions,
        first.parameter_transitions,
        first.forwarding_transitions,
        first.forwarding_precedences,
    )
    assert planner.get_plan() is second
    assert first is not second
    assert ISL_ID in {
        transition.connection_id
        for transition in second.connection_transitions
    }
    assert not any(
        decision.decision == "suppressed"
        for decision in planner.get_planning_decisions()
    )
    assert connection_model.calls == [((1, 2, 3, 4), (1, 2, 3, 4), 0)]
    assert parameter_model.calls == [((1, 2, 3, 4), (1, 2, 3, 4), 0)]
    assert forwarding_model.calls == [((1, 2, 3, 4), (1, 2, 3, 4), 0)]


def test_parameter_planned_target_continues_across_publication_boundary() -> None:
    connection_slices = tuple(
        ConnectionSlice((_connection(delay),))
        for delay in (10, 12, 14, 16)
    )
    route = RouteSlice(((ROUTE_KEY, GROUND_STATION),))
    simulator, slicer = _simulator(
        connection_slices, (route, route, route, route)
    )
    slicer.create_all_slices()
    parameter_model = _CountingParameterPlanningModel(5)
    planner = simulator.create_planner(
        planning_window_policy=_PublishTwoDifferencesWindowPolicy(),
        connection_model=_CountingConnectionPlanningModel(),
        parameter_model=parameter_model,
        forwarding_model=_CountingForwardingPlanningModel(),
        coordination_policy=DefaultCoordinationPolicy(),
    )

    first = planner.get_plan()
    key = (CONNECTION_ID, ConnectionDirection.ENDPOINT_A_TO_B)
    assert (first.start_slice_id, first.end_slice_id) == (0, 2)
    assert first.parameter_transitions == ()
    assert planner._published_observed_parameters[key].delay_us == 12
    assert planner._published_planned_parameters[key].delay_us == 10
    assert parameter_model.calls == [((1, 2, 3, 4), (1, 2, 3, 4), 0)]

    second = planner.synchronize()

    assert (second.start_slice_id, second.end_slice_id) == (2, 4)
    assert {
        transition.delay_us for transition in second.parameter_transitions
    } == {16}
    assert planner._published_planned_parameters[key].delay_us == 16
    assert parameter_model.calls == [((1, 2, 3, 4), (1, 2, 3, 4), 0)]
    assert first.parameter_transitions == ()


def test_shared_boundary_transition_is_published_exactly_once() -> None:
    connection_slices = (
        ConnectionSlice((_connection(10),)),
        ConnectionSlice((_connection(16),)),
        ConnectionSlice((_connection(16),)),
    )
    route = RouteSlice(((ROUTE_KEY, GROUND_STATION),))
    simulator, slicer = _simulator(
        connection_slices, (route, route, route)
    )
    slicer.create_slices(2)
    planner = simulator.create_planner(
        planning_window_policy=_PublishOneDifferenceWindowPolicy(),
        connection_model=_CountingConnectionPlanningModel(),
        parameter_model=_CountingParameterPlanningModel(5),
        forwarding_model=_CountingForwardingPlanningModel(),
        coordination_policy=DefaultCoordinationPolicy(),
    )

    first = planner.get_plan()
    second = planner.synchronize()
    slicer.create_next_slice()
    third = planner.synchronize()

    assert first.parameter_transitions == ()
    assert len(second.parameter_transitions) == 2
    assert {
        transition.nominal_time for transition in second.parameter_transitions
    } == {second.end_timestamp}
    assert second.end_timestamp == third.start_timestamp
    assert third.parameter_transitions == ()


def test_parameter_model_may_use_valid_sub_slice_time_but_not_outside_interval() -> None:
    connection_slices = (
        ConnectionSlice((_connection(10),)),
        ConnectionSlice((_connection(16),)),
    )
    route = RouteSlice(((ROUTE_KEY, GROUND_STATION),))
    simulator, slicer = _simulator(connection_slices, (route, route))
    slicer.create_slices(2)
    plan = simulator.create_planner(
        connection_model=_CountingConnectionPlanningModel(),
        parameter_model=_SubSliceParameterPlanningModel(),
        forwarding_model=_CountingForwardingPlanningModel(),
        coordination_policy=DefaultCoordinationPolicy(),
    ).get_plan()
    assert {
        transition.nominal_time for transition in plan.parameter_transitions
    } == {START + timedelta(seconds=5)}

    invalid_simulator, invalid_slicer = _simulator(
        connection_slices, (route, route)
    )
    invalid_slicer.create_slices(2)
    with pytest.raises(ValueError, match="outside planning window"):
        invalid_simulator.create_planner(
            connection_model=_CountingConnectionPlanningModel(),
            parameter_model=_SubSliceParameterPlanningModel(
                outside_interval=True
            ),
            forwarding_model=_CountingForwardingPlanningModel(),
            coordination_policy=DefaultCoordinationPolicy(),
        )


def test_unsafe_short_connection_preserves_formal_cross_domain_evolution() -> None:
    simulator, slicer, second_route = _short_connection_fixture()
    slicer.create_all_slices()
    route_model_calls = slicer.get_route_model().calls
    planner = simulator.create_planner(
        connection_model=_CountingConnectionPlanningModel(
            minimum_dwell=timedelta(seconds=30)
        ),
        parameter_model=_CountingParameterPlanningModel(1),
        forwarding_model=_CountingForwardingPlanningModel(),
        coordination_policy=DefaultCoordinationPolicy(),
    )

    plan = planner.get_plan()

    assert plan.end_slice_id == 4
    assert {
        transition.connection_id
        for transition in plan.connection_transitions
    } == {SECOND_ACCESS_ID, ISL_ID, CONNECTION_ID}
    assert len(plan.parameter_transitions) == 2
    assert {
        transition.connection_id
        for transition in plan.parameter_transitions
    } == {ISL_ID}
    assert tuple(
        transition.route_key for transition in plan.forwarding_transitions
    ) == (second_route, ROUTE_KEY, ROUTE_KEY)
    assert tuple(
        transition.operation for transition in plan.forwarding_transitions
    ) == (
        DifferenceOperation.ADDED,
        DifferenceOperation.ADDED,
        DifferenceOperation.CHANGED,
    )
    assert plan.forwarding_transitions[-1].next_hop == GROUND_STATION
    assert slicer.get_route_model().calls == route_model_calls
    assert not any(
        decision.decision == "suppressed"
        for decision in planner.get_planning_decisions()
    )


def test_repeated_connection_suppresses_only_short_lifetime_and_resets_parameters() -> None:
    connection_slices = (
        ConnectionSlice((_connection(10),)),
        ConnectionSlice(()),
        ConnectionSlice((_connection(100),)),
        ConnectionSlice((_connection(103),)),
        ConnectionSlice((_connection(106),)),
        ConnectionSlice(()),
    )
    direct_route = RouteSlice(((ROUTE_KEY, GROUND_STATION),))
    empty_route = RouteSlice(())
    simulator, slicer = _simulator(
        connection_slices,
        (
            direct_route,
            empty_route,
            direct_route,
            direct_route,
            direct_route,
            empty_route,
        ),
    )
    slicer.create_all_slices()

    plan = simulator.create_planner(
        connection_model=MidpointConnectionPlanningModel(
            minimum_dwell=timedelta(seconds=15)
        ),
        parameter_model=AccumulatedDeadbandParameterPlanningModel(
            delay_us_threshold=5
        ),
        forwarding_model=DependencyAwareForwardingPlanningModel(),
        coordination_policy=DefaultCoordinationPolicy(),
    ).get_plan()

    repeated_connections = tuple(
        transition
        for transition in plan.connection_transitions
        if transition.connection_id == CONNECTION_ID
    )
    assert tuple(value.operation for value in repeated_connections) == (
        ConnectionTransitionOperation.ESTABLISH,
        ConnectionTransitionOperation.WITHDRAW,
    )
    assert tuple(value.nominal_time for value in repeated_connections) == (
        START + timedelta(seconds=15),
        START + timedelta(seconds=45),
    )
    assert repeated_connections[0].connection is not None
    assert repeated_connections[0].connection.endpoint_a_to_b.delay_us == 100
    assert {
        (transition.nominal_time, transition.delay_us)
        for transition in plan.parameter_transitions
    } == {(START + timedelta(seconds=40), 106)}
    suppressed = tuple(
        decision
        for decision in simulator.get_planner().get_planning_decisions()
        if decision.decision == "suppressed"
    )
    assert len(suppressed) == 1
    assert suppressed[0].source_difference_ids == (1, 2)


def test_forwarding_after_reestablishment_uses_later_active_occurrence() -> None:
    repeated_route = RouteKey(SATELLITE_2, TARGET)
    isl = _connection_with_id(ISL_ID)
    repeated = _connection_with_id(CONNECTION_ID)
    alternative = _connection_with_id(SECOND_ACCESS_ID)
    connection_slices = (
        ConnectionSlice((isl, repeated)),
        ConnectionSlice((isl,)),
        ConnectionSlice((isl, repeated)),
        ConnectionSlice((isl, repeated, alternative)),
    )
    indirect_routes = RouteSlice(
        (
            (ROUTE_KEY, GROUND_STATION),
            (repeated_route, SATELLITE),
        )
    )
    route_slices = (
        indirect_routes,
        RouteSlice(()),
        indirect_routes,
        RouteSlice(
            (
                (ROUTE_KEY, GROUND_STATION),
                (repeated_route, GROUND_STATION),
            )
        ),
    )
    simulator, slicer = _simulator(connection_slices, route_slices)
    slicer.create_all_slices()
    route_model_calls = slicer.get_route_model().calls

    plan = simulator.create_planner(
        connection_model=MidpointConnectionPlanningModel(),
        parameter_model=AccumulatedDeadbandParameterPlanningModel(),
        forwarding_model=DependencyAwareForwardingPlanningModel(),
        coordination_policy=DefaultCoordinationPolicy(),
    ).get_plan()

    assert tuple(
        transition.operation
        for transition in plan.connection_transitions
        if transition.connection_id == CONNECTION_ID
    ) == (
        ConnectionTransitionOperation.ESTABLISH,
        ConnectionTransitionOperation.WITHDRAW,
        ConnectionTransitionOperation.ESTABLISH,
    )
    repeated_forwarding = tuple(
        transition
        for transition in plan.forwarding_transitions
        if transition.route_key == repeated_route
    )
    assert tuple(value.operation for value in repeated_forwarding) == (
        DifferenceOperation.ADDED,
        DifferenceOperation.REMOVED,
        DifferenceOperation.ADDED,
        DifferenceOperation.CHANGED,
    )
    assert repeated_forwarding[-1].nominal_time == START + timedelta(seconds=30)
    assert repeated_forwarding[-1].next_hop == GROUND_STATION
    assert slicer.get_route_model().calls == route_model_calls


def test_unsafe_short_suppression_refreshes_path_without_transition() -> None:
    downstream_key = RouteKey(SATELLITE_2, TARGET)
    final_key = RouteKey(SATELLITE_3, TARGET)
    replacement_isl_id = ConnectionId(
        ConnectionKind.INTER_SATELLITE,
        SATELLITE_2,
        SATELLITE_3,
    )
    replacement_access_id = ConnectionId(
        ConnectionKind.GROUND_STATION_ACCESS,
        SATELLITE_3,
        GROUND_STATION,
    )
    connection_slices = (
        ConnectionSlice(
            (
                _connection_with_id(ISL_ID),
                _connection_with_id(replacement_isl_id),
                _connection_with_id(replacement_access_id),
            )
        ),
        ConnectionSlice(
            (
                _connection_with_id(ISL_ID),
                _connection_with_id(replacement_isl_id),
                _connection_with_id(replacement_access_id),
                _connection_with_id(SECOND_ACCESS_ID),
            )
        ),
        ConnectionSlice(
            (
                _connection_with_id(ISL_ID),
                _connection_with_id(replacement_isl_id),
                _connection_with_id(replacement_access_id),
            )
        ),
    )
    route_slices = (
        RouteSlice(
            (
                (ROUTE_KEY, SATELLITE_2),
                (downstream_key, SATELLITE_3),
                (final_key, GROUND_STATION),
            )
        ),
        RouteSlice(
            (
                (ROUTE_KEY, SATELLITE_2),
                (downstream_key, GROUND_STATION),
                (final_key, GROUND_STATION),
            )
        ),
        RouteSlice(
            (
                (ROUTE_KEY, SATELLITE_2),
                (downstream_key, SATELLITE_3),
                (final_key, GROUND_STATION),
            )
        ),
    )
    simulator, slicer = _simulator(
        connection_slices,
        route_slices,
        extra_satellites=(SATELLITE_3,),
    )
    slicer.create_all_slices()

    planner = simulator.create_planner(
        connection_model=MidpointConnectionPlanningModel(
            minimum_dwell=timedelta(seconds=15)
        ),
        parameter_model=AccumulatedDeadbandParameterPlanningModel(),
        forwarding_model=DependencyAwareForwardingPlanningModel(),
        coordination_policy=DefaultCoordinationPolicy(),
    )
    plan = planner.get_plan()

    assert tuple(
        transition.operation
        for transition in plan.forwarding_transitions
        if transition.route_key == ROUTE_KEY
    ) == (DifferenceOperation.ADDED,)
    assert tuple(
        transition.operation
        for transition in plan.connection_transitions
        if transition.connection_id == SECOND_ACCESS_ID
    ) == (
        ConnectionTransitionOperation.ESTABLISH,
        ConnectionTransitionOperation.WITHDRAW,
    )
    assert not any(
        decision.decision == "suppressed"
        for decision in planner.get_planning_decisions()
    )
    assert planner._planned_route_path_connection_ids[ROUTE_KEY] == (
        ISL_ID,
        replacement_isl_id,
        replacement_access_id,
    )
    assert SECOND_ACCESS_ID not in planner._planned_route_path_connection_ids[
        ROUTE_KEY
    ]


def test_coordination_policy_can_retain_same_cross_domain_candidate() -> None:
    simulator, slicer, _ = _short_connection_fixture()
    slicer.create_all_slices()
    policy = _RetainCoordinationPolicy()
    planner = simulator.create_planner(
        connection_model=MidpointConnectionPlanningModel(
            minimum_dwell=timedelta(seconds=30)
        ),
        parameter_model=AccumulatedDeadbandParameterPlanningModel(
            delay_us_threshold=1
        ),
        forwarding_model=DependencyAwareForwardingPlanningModel(),
        coordination_policy=policy,
    )

    plan = planner.get_plan()

    assert policy.calls == [(1, 2, 3)]
    assert sum(
        transition.connection_id == ISL_ID
        for transition in plan.connection_transitions
    ) == 2
    assert len(plan.parameter_transitions) == 2
    assert tuple(
        transition.operation
        for transition in plan.forwarding_transitions
        if transition.route_key == ROUTE_KEY
    ) == (DifferenceOperation.ADDED, DifferenceOperation.CHANGED)
    assert all(
        decision.decision != "suppressed"
        for decision in planner.get_planning_decisions()
    )


def test_one_object_may_implement_multiple_planning_roles() -> None:
    connection = ConnectionSlice((_connection(10),))
    route = RouteSlice(((ROUTE_KEY, GROUND_STATION),))
    simulator, slicer = _simulator((connection,), (route,))
    slicer.create_next_slice()
    shared = _SharedAlgorithms()

    planner = simulator.create_planner(
        planning_window_policy=shared,
        connection_model=shared,
        parameter_model=shared,
        forwarding_model=shared,
        coordination_policy=shared,
    )

    assert planner.get_plan().end_slice_id == 1


def test_replaceable_window_publishes_complete_selected_coverage() -> None:
    connection_slices = (
        ConnectionSlice((_connection(10),)),
        ConnectionSlice((_connection(10),)),
        ConnectionSlice((_connection(10),)),
    )
    route = RouteSlice(((ROUTE_KEY, GROUND_STATION),))
    simulator, slicer = _simulator(
        connection_slices, (route, route, route)
    )
    slicer.create_slices(2)
    planner = simulator.create_planner(
        connection_model=_CountingConnectionPlanningModel(),
        parameter_model=_CountingParameterPlanningModel(1),
        forwarding_model=_CountingForwardingPlanningModel(),
        coordination_policy=DefaultCoordinationPolicy(),
        planning_window_policy=_PublishOneDifferenceWindowPolicy(),
    )

    first = planner.get_plan()
    assert (first.start_slice_id, first.end_slice_id) == (0, 1)
    assert len(first.connection_transitions) == 1
    assert first.parameter_transitions == ()
    assert len(first.forwarding_transitions) == 1
    assert len(planner.get_consumed_differences()) == 2

    slicer.create_next_slice()
    second = planner.synchronize()
    assert (second.start_slice_id, second.end_slice_id) == (1, 2)
    assert len(planner.get_consumed_differences()) == 3
    third = planner.synchronize()
    assert (third.start_slice_id, third.end_slice_id) == (2, 3)
    assert first.end_slice_id == second.start_slice_id
    assert second.end_slice_id == third.start_slice_id
    assert first.end_timestamp == second.start_timestamp
    assert second.end_timestamp == third.start_timestamp


def test_forwarding_uses_formal_paths_for_downstream_first_order() -> None:
    first_route = RouteKey(SATELLITE, TARGET)
    second_route = RouteKey(SATELLITE_2, TARGET)
    connections = ConnectionSlice(
        (
            _connection_with_id(ISL_ID),
            _connection_with_id(SECOND_ACCESS_ID),
        )
    )
    routes = RouteSlice(
        (
            (first_route, SATELLITE_2),
            (second_route, GROUND_STATION),
        )
    )
    simulator, slicer = _simulator((connections,), (routes,))
    slicer.create_next_slice()
    route_model_calls = slicer.get_route_model().calls

    plan = simulator.create_planner(
        connection_model=_CountingConnectionPlanningModel(),
        parameter_model=_CountingParameterPlanningModel(1),
        forwarding_model=_CountingForwardingPlanningModel(),
        coordination_policy=DefaultCoordinationPolicy(),
    ).get_plan()

    assert tuple(
        transition.route_key for transition in plan.forwarding_transitions
    ) == (second_route, first_route)
    same_time_forwarding = tuple(
        transition
        for transition in plan.temporal_index[START]
        if isinstance(transition, ForwardingTransition)
    )
    assert same_time_forwarding == plan.forwarding_transitions
    assert len(plan.forwarding_precedences) == 1
    precedence = plan.forwarding_precedences[0]
    assert precedence.predecessor.route_key == second_route
    assert precedence.successor.route_key == first_route
    assert precedence.predecessor.nominal_time == precedence.successor.nominal_time
    assert slicer.get_route_model().calls == route_model_calls


def test_mixed_forwarding_change_publishes_minimal_acyclic_precedence() -> None:
    old_downstream_key = RouteKey(SATELLITE_2, TARGET)
    new_downstream_key = RouteKey(SATELLITE_3, TARGET)
    unrelated_key = RouteKey(SATELLITE_4, TARGET)
    new_isl_id = ConnectionId(
        ConnectionKind.INTER_SATELLITE,
        SATELLITE,
        SATELLITE_3,
    )
    new_access_id = ConnectionId(
        ConnectionKind.GROUND_STATION_ACCESS,
        SATELLITE_3,
        GROUND_STATION,
    )
    unrelated_access_id = ConnectionId(
        ConnectionKind.GROUND_STATION_ACCESS,
        SATELLITE_4,
        GROUND_STATION,
    )
    old_connections = ConnectionSlice(
        (
            _connection_with_id(ISL_ID),
            _connection_with_id(SECOND_ACCESS_ID),
        )
    )
    new_connections = ConnectionSlice(
        (
            _connection_with_id(new_isl_id),
            _connection_with_id(new_access_id),
            _connection_with_id(unrelated_access_id),
        )
    )
    old_routes = RouteSlice(
        (
            (ROUTE_KEY, SATELLITE_2),
            (old_downstream_key, GROUND_STATION),
        )
    )
    new_routes = RouteSlice(
        (
            (ROUTE_KEY, SATELLITE_3),
            (new_downstream_key, GROUND_STATION),
            (unrelated_key, GROUND_STATION),
        )
    )
    simulator, slicer = _simulator(
        (old_connections, new_connections),
        (old_routes, new_routes),
        extra_satellites=(SATELLITE_3, SATELLITE_4),
    )
    slicer.create_slices(2)
    formal_change = slicer.create_difference(1, 2)
    route_model_calls = slicer.get_route_model().calls
    planner = simulator.create_planner(
        planning_window_policy=_PublishOneDifferenceWindowPolicy(),
        connection_model=SampleBoundaryConnectionPlanningModel(),
        parameter_model=_CountingParameterPlanningModel(1),
        forwarding_model=_CountingForwardingPlanningModel(),
        coordination_policy=DefaultCoordinationPolicy(),
    )
    assert planner.get_plan().end_slice_id == 1

    plan = planner.synchronize()

    assert (plan.start_slice_id, plan.end_slice_id) == (1, 2)
    formal_states = {
        (patch.route_key, patch.operation, patch.next_hop)
        for patch in formal_change.route_patches
    }
    assert {
        (transition.route_key, transition.operation, transition.next_hop)
        for transition in plan.forwarding_transitions
    } == formal_states
    by_key = {
        transition.route_key: transition
        for transition in plan.forwarding_transitions
    }
    assert by_key[ROUTE_KEY].operation is DifferenceOperation.CHANGED
    assert by_key[old_downstream_key].operation is DifferenceOperation.REMOVED
    assert by_key[new_downstream_key].operation is DifferenceOperation.ADDED
    assert by_key[unrelated_key].operation is DifferenceOperation.ADDED

    edges = {
        (precedence.predecessor.route_key, precedence.successor.route_key)
        for precedence in plan.forwarding_precedences
    }
    assert edges == {
        (new_downstream_key, ROUTE_KEY),
        (ROUTE_KEY, old_downstream_key),
    }
    transition_ids = {id(transition) for transition in plan.forwarding_transitions}
    assert all(
        id(precedence.predecessor) in transition_ids
        and id(precedence.successor) in transition_ids
        and precedence.predecessor.nominal_time
        == precedence.successor.nominal_time
        for precedence in plan.forwarding_precedences
    )
    assert len(edges) == len(plan.forwarding_precedences)
    assert all(
        unrelated_key not in edge
        for edge in edges
    )
    successors = {}
    for predecessor, successor in edges:
        successors.setdefault(predecessor, set()).add(successor)
    for predecessor, successor in edges:
        pending = [successor]
        visited = set()
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            pending.extend(successors.get(current, ()))
        assert predecessor not in visited
    assert slicer.get_route_model().calls == route_model_calls


def test_fixed_invariant_rejects_forwarding_after_connection_withdrawal() -> None:
    connection_slices = (
        ConnectionSlice(),
        ConnectionSlice((_connection(10),)),
        ConnectionSlice(),
    )
    route_slices = (
        RouteSlice(),
        RouteSlice(((ROUTE_KEY, GROUND_STATION),)),
        RouteSlice(),
    )
    simulator, slicer = _simulator(connection_slices, route_slices)
    slicer.create_slices(2)
    planner = simulator.create_planner(
        connection_model=_CountingConnectionPlanningModel(),
        parameter_model=_CountingParameterPlanningModel(1),
        forwarding_model=_LateForwardingPlanningModel(),
        coordination_policy=DefaultCoordinationPolicy(),
    )
    previous_plan = planner.get_plan()
    previous_differences = planner.get_consumed_differences()
    slicer.create_next_slice()

    with pytest.raises(
        ValueError, match="forwarding state depends on withdrawn Connection"
    ):
        planner.synchronize()

    assert planner.get_plan() is previous_plan
    assert planner.get_consumed_differences() == previous_differences
    assert simulator.get_planner() is planner


def test_failed_update_keeps_published_and_built_in_planning_state() -> None:
    connection_slices = (
        ConnectionSlice(),
        ConnectionSlice((_connection(10),)),
        ConnectionSlice(),
    )
    route_slices = (
        RouteSlice(),
        RouteSlice(((ROUTE_KEY, GROUND_STATION),)),
        RouteSlice(),
    )
    simulator, slicer = _simulator(connection_slices, route_slices)
    slicer.create_slices(2)
    policy = _FailSecondCoordinationPolicy()
    planner = simulator.create_planner(
        connection_model=MidpointConnectionPlanningModel(),
        parameter_model=AccumulatedDeadbandParameterPlanningModel(
            delay_us_threshold=1
        ),
        forwarding_model=DependencyAwareForwardingPlanningModel(),
        coordination_policy=policy,
    )
    previous_plan = planner.get_plan()
    previous_differences = planner.get_consumed_differences()
    previous_observed = dict(planner._observed_parameters)
    previous_planned = dict(planner._planned_parameters)
    previous_pending = dict(planner._pending_parameter_ranges)
    previous_active = dict(planner._active_establishments)
    previous_published_observed = dict(
        planner._published_observed_parameters
    )
    previous_published_planned = dict(planner._published_planned_parameters)
    previous_published_pending = dict(
        planner._published_pending_parameter_ranges
    )
    previous_published_active = dict(
        planner._published_active_establishments
    )
    previous_planned_routes = dict(planner._planned_route_next_hops)
    previous_planned_paths = dict(
        planner._planned_route_path_connection_ids
    )
    previous_published_routes = dict(planner._published_route_next_hops)
    previous_published_paths = dict(
        planner._published_route_path_connection_ids
    )
    previous_published_through = planner._published_through_slice_id
    previous_decisions = planner.get_planning_decisions()
    previous_publications = planner._publication_count
    previous_route_slices = set(planner._route_next_hops)
    previous_route_paths = set(planner._route_path_connection_ids)
    slicer.create_next_slice()

    with pytest.raises(
        ValueError,
        match="forwarding state depends on withdrawn Connection",
    ):
        planner.synchronize()

    assert planner.get_plan() is previous_plan
    assert planner.get_consumed_differences() == previous_differences
    assert planner._observed_parameters == previous_observed
    assert planner._planned_parameters == previous_planned
    assert planner._pending_parameter_ranges == previous_pending
    assert planner._active_establishments == previous_active
    assert planner._published_observed_parameters == previous_published_observed
    assert planner._published_planned_parameters == previous_published_planned
    assert planner._published_pending_parameter_ranges == previous_published_pending
    assert planner._published_active_establishments == previous_published_active
    assert planner._planned_route_next_hops == previous_planned_routes
    assert planner._planned_route_path_connection_ids == previous_planned_paths
    assert planner._published_route_next_hops == previous_published_routes
    assert planner._published_route_path_connection_ids == previous_published_paths
    assert planner._published_through_slice_id == previous_published_through
    assert planner.get_planning_decisions() == previous_decisions
    assert planner._publication_count == previous_publications
    assert set(planner._route_next_hops) == previous_route_slices
    assert set(planner._route_path_connection_ids) == previous_route_paths
