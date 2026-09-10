from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

import skyseed_v2.simulator._route_computation as route_computation
from skyseed_v2.configuration.simulator import (
    LatitudeLongitudeRectangleDefinition,
    ZoneDefinition,
    ZoneLayoutDefinition,
)
from skyseed_v2.shared import NodeId
from skyseed_v2.simulator import (
    AccumulatedDeadbandParameterPlanningModel,
    CircularOrbitPositionModel,
    ConnectionParameterObservation,
    DefaultCoordinationPolicy,
    EffectiveConnectionParameters,
    GatewayHopCountRoutePlanningModel,
    GroundStationRouteDemandTarget,
    OperationalConnection,
    OperationalConnectionId,
    OperationalConnectionKind,
    ParameterKindConfiguration,
    PhysicalWorld,
    PlanTransition,
    ReachableRouteOutcome,
    RouteDemand,
    RouteUnreachableReason,
    SemanticEventKind,
    SemanticEventPrecedence,
    SimulationDefinition,
    SimulationTimeline,
    Simulator,
    FrameBatchPlanningWindowPolicy,
    UnreachableRouteOutcome,
    ZoneRouteDemandTarget,
    ZoneServiceAssignment,
    semantic_event_references,
)
from skyseed_v2.simulator.input import (
    GroundStationSimulationInput,
    SatelliteSimulationInput,
    SimulatorStaticInput,
)


START = datetime(2026, 1, 1, tzinfo=timezone.utc)
EFFECTIVE_PARAMETERS = EffectiveConnectionParameters(1_000_000, 10, 20)


@dataclass(frozen=True, slots=True)
class _RouteScenario:
    physical_world: PhysicalWorld
    satellites: tuple[NodeId, NodeId]
    ground_stations: tuple[NodeId, NodeId, NodeId]


def _satellite(identifier: str, index: int) -> SatelliteSimulationInput:
    return SatelliteSimulationInput(
        node_id=NodeId(65090, "satellite", identifier),
        satellite_id=identifier,
        shell_id="shell",
        orbit_id="orbit",
        orbit_index=index,
        altitude_km=550.0,
        inclination_deg=53.0,
        right_ascension_deg=0.0,
        mean_anomaly_deg=index * 180.0,
        epoch_utc=START,
        maximum_simultaneous_inter_satellite_links=2,
    )


def _ground_station(
    identifier: str,
    longitude_deg: float,
) -> GroundStationSimulationInput:
    return GroundStationSimulationInput(
        node_id=NodeId(65090, "ground-station", identifier),
        ground_station_id=identifier,
        latitude_deg=0.0,
        longitude_deg=longitude_deg,
        elevation_m=0.0,
        station_type="gateway",
        operator=None,
    )


def _route_scenario(
    zone_ids: tuple[str, ...] = ("zone",),
    *,
    zone_center_longitude_deg: float = 0.0,
) -> _RouteScenario:
    satellites = (_satellite("sat-a", 0), _satellite("sat-b", 1))
    ground_stations = (
        _ground_station("a-near", 0.0),
        _ground_station("b-middle", 10.0),
        _ground_station("c-far", 20.0),
    )
    simulator = Simulator(
        SimulationDefinition(
            SimulatorStaticInput(satellites, ground_stations),
            SimulationTimeline(START, START, timedelta(seconds=10)),
        )
    )
    physical_world = simulator.create_physical_world(
        position_model=CircularOrbitPositionModel(),
        zone_layout_definition=ZoneLayoutDefinition(
            zones=tuple(
                ZoneDefinition(
                    zone_id=zone_id,
                    priority=1,
                    geometry=LatitudeLongitudeRectangleDefinition(
                        south_latitude_deg=-1.0 + index * 3.0,
                        north_latitude_deg=1.0 + index * 3.0,
                        west_longitude_deg=zone_center_longitude_deg - 1.0,
                        east_longitude_deg=zone_center_longitude_deg + 1.0,
                    ),
                )
                for index, zone_id in enumerate(zone_ids)
            ),
            allow_overlap=False,
        ),
    )
    return _RouteScenario(
        physical_world,
        tuple(value.node_id for value in satellites),
        tuple(value.node_id for value in ground_stations),
    )


def _operational_connection(
    kind: OperationalConnectionKind,
    first: NodeId,
    second: NodeId,
) -> OperationalConnection:
    return OperationalConnection(
        OperationalConnectionId(kind, first, second),
        EFFECTIVE_PARAMETERS,
        EFFECTIVE_PARAMETERS,
    )


def _connected_operational_graph(
    scenario: _RouteScenario,
) -> tuple[OperationalConnection, ...]:
    first_satellite, second_satellite = scenario.satellites
    first_station, second_station, third_station = scenario.ground_stations
    return (
        _operational_connection(
            OperationalConnectionKind.INTER_SATELLITE,
            first_satellite,
            second_satellite,
        ),
        _operational_connection(
            OperationalConnectionKind.GROUND_STATION_ACCESS,
            first_station,
            first_satellite,
        ),
        _operational_connection(
            OperationalConnectionKind.GROUND_STATION_ACCESS,
            second_station,
            second_satellite,
        ),
        _operational_connection(
            OperationalConnectionKind.GROUND_STATION_ACCESS,
            third_station,
            second_satellite,
        ),
    )


@pytest.mark.parametrize(
    ("keyword", "value"),
    (
        ("gateway_candidate_count", 0),
        ("gateway_candidate_count", -1),
        ("gateway_candidate_count", True),
        ("gateway_candidate_count", 1.5),
        ("gateway_candidate_max_distance_m", 0.0),
        ("gateway_candidate_max_distance_m", -1.0),
        ("gateway_candidate_max_distance_m", float("inf")),
        ("gateway_candidate_max_distance_m", float("nan")),
        ("gateway_candidate_max_distance_m", True),
    ),
)
def test_gateway_route_configuration_fails_closed(keyword, value) -> None:
    arguments = {
        "gateway_candidate_count": 1,
        "gateway_candidate_max_distance_m": None,
    }
    arguments[keyword] = value
    with pytest.raises(ValueError):
        GatewayHopCountRoutePlanningModel(**arguments)


def test_gateway_candidates_are_static_ordered_bounded_and_two_way() -> None:
    scenario = _route_scenario()
    first_satellite, second_satellite = scenario.satellites
    first_station, second_station, third_station = scenario.ground_stations
    assignment = ZoneServiceAssignment("zone", second_satellite)
    model = GatewayHopCountRoutePlanningModel(gateway_candidate_count=2)
    try:
        demands = model.create_route_demands(
            scenario.physical_world,
            (assignment,),
        )
        assert demands is model.create_route_demands(
            scenario.physical_world,
            (assignment,),
        )
        assert set(demands) == {
            RouteDemand(first_station, ZoneRouteDemandTarget("zone")),
            RouteDemand(second_station, ZoneRouteDemandTarget("zone")),
            RouteDemand(
                second_satellite,
                GroundStationRouteDemandTarget(first_station),
            ),
            RouteDemand(
                second_satellite,
                GroundStationRouteDemandTarget(second_station),
            ),
        }
        assert all(
            demand.source != third_station
            and not (
                isinstance(demand.target, GroundStationRouteDemandTarget)
                and demand.target.ground_station_id == third_station
            )
            for demand in demands
        )

        outcomes = model.plan_route_outcomes(
            scenario.physical_world,
            _connected_operational_graph(scenario),
            (assignment,),
            demands,
            (),
        )
        by_demand = {value.demand: value for value in outcomes.outcomes}
        assert by_demand[
            RouteDemand(first_station, ZoneRouteDemandTarget("zone"))
        ].path == (first_station, first_satellite, second_satellite)
        assert by_demand[
            RouteDemand(second_station, ZoneRouteDemandTarget("zone"))
        ].path == (second_station, second_satellite)
        assert by_demand[
            RouteDemand(
                second_satellite,
                GroundStationRouteDemandTarget(first_station),
            )
        ].path == (second_satellite, first_satellite, first_station)
        assert by_demand[
            RouteDemand(
                second_satellite,
                GroundStationRouteDemandTarget(second_station),
            )
        ].path == (second_satellite, second_station)
        assert len(outcomes.transitions) == len(outcomes.outcomes) == 4
        repeated = model.plan_route_outcomes(
            scenario.physical_world,
            _connected_operational_graph(scenario),
            (assignment,),
            demands,
            outcomes.outcomes,
        )
        assert repeated.outcomes == outcomes.outcomes
        assert all(a is b for a, b in zip(repeated.outcomes, outcomes.outcomes))
        assert repeated.transitions == ()
        assert model._construction_work["route_request_objects_reused"] == 4

        changed_assignment_demands = model.create_route_demands(
            scenario.physical_world,
            (ZoneServiceAssignment("zone", first_satellite),),
        )
        assert changed_assignment_demands is not demands
        changed = model.plan_route_outcomes(scenario.physical_world,
            _connected_operational_graph(scenario),
            (ZoneServiceAssignment("zone", first_satellite),), changed_assignment_demands, outcomes.outcomes)
        reference = GatewayHopCountRoutePlanningModel(gateway_candidate_count=2)
        try:
            expected = reference.plan_route_outcomes(scenario.physical_world,
                _connected_operational_graph(scenario),
                (ZoneServiceAssignment("zone", first_satellite),), changed_assignment_demands, outcomes.outcomes)
            assert changed == expected
            removed = model.plan_route_outcomes(scenario.physical_world, (),
                (ZoneServiceAssignment("zone", first_satellite),), changed_assignment_demands, changed.outcomes)
            assert removed == reference.plan_route_outcomes(scenario.physical_world, (),
                (ZoneServiceAssignment("zone", first_satellite),), changed_assignment_demands, expected.outcomes)
        finally:
            reference.shutdown()
        assert {
            demand.target.ground_station_id
            for demand in changed_assignment_demands
            if isinstance(demand.target, GroundStationRouteDemandTarget)
        } == {first_station, second_station}

        foreign = _route_scenario()
        with pytest.raises(ValueError, match="another PhysicalWorld"):
            model.create_route_demands(
                foreign.physical_world,
                (ZoneServiceAssignment("zone", foreign.satellites[0]),),
            )
    finally:
        model.shutdown()


def test_gateway_count_distance_and_explicit_unreachable_outcomes() -> None:
    tied = _route_scenario(zone_center_longitude_deg=5.0)
    first_station, _second_station, _third_station = tied.ground_stations
    tie_model = GatewayHopCountRoutePlanningModel(gateway_candidate_count=1)
    distance_model = GatewayHopCountRoutePlanningModel(
        gateway_candidate_count=3,
        gateway_candidate_max_distance_m=1.0,
    )
    try:
        tied_demands = tie_model.create_route_demands(
            tied.physical_world,
            (ZoneServiceAssignment("zone", tied.satellites[1]),),
        )
        assert {
            demand.source
            for demand in tied_demands
            if isinstance(demand.target, ZoneRouteDemandTarget)
        } == {first_station}
        assert distance_model.create_route_demands(
            tied.physical_world,
            (ZoneServiceAssignment("zone", tied.satellites[1]),),
        ) == ()
    finally:
        tie_model.shutdown()
        distance_model.shutdown()

    scenario = _route_scenario()
    first_satellite, second_satellite = scenario.satellites
    first_station, second_station, _third_station = scenario.ground_stations
    model = GatewayHopCountRoutePlanningModel(gateway_candidate_count=1)
    try:
        assignment = ZoneServiceAssignment("zone", second_satellite)
        demands = model.create_route_demands(
            scenario.physical_world,
            (assignment,),
        )
        disconnected = (
            _operational_connection(
                OperationalConnectionKind.INTER_SATELLITE,
                first_satellite,
                second_satellite,
            ),
            _operational_connection(
                OperationalConnectionKind.GROUND_STATION_ACCESS,
                second_station,
                second_satellite,
            ),
        )
        outcomes = model.plan_route_outcomes(
            scenario.physical_world,
            disconnected,
            (assignment,),
            demands,
            (),
        )
        assert len(outcomes.outcomes) == 2
        assert all(
            isinstance(value, UnreachableRouteOutcome)
            and value.reason is RouteUnreachableReason.NO_OPERATIONAL_PATH
            for value in outcomes.outcomes
        )

        unserved_assignments = (ZoneServiceAssignment("zone", None),)
        unserved_demands = model.create_route_demands(
            scenario.physical_world,
            unserved_assignments,
        )
        assert unserved_demands == (
            RouteDemand(first_station, ZoneRouteDemandTarget("zone")),
        )
        unserved = model.plan_route_outcomes(
            scenario.physical_world,
            disconnected,
            unserved_assignments,
            unserved_demands,
            (),
        )
        assert unserved.outcomes == (
            UnreachableRouteOutcome(
                unserved_demands[0],
                RouteUnreachableReason.ZONE_UNSERVED,
            ),
        )
    finally:
        model.shutdown()


def test_gateway_routes_reuse_one_tree_for_a_shared_service_satellite(
    monkeypatch,
) -> None:
    zone_ids = ("zone-a", "zone-b", "zone-c")
    scenario = _route_scenario(zone_ids)
    second_satellite = scenario.satellites[1]
    first_station, second_station, _third_station = scenario.ground_stations
    assignments = tuple(
        ZoneServiceAssignment(zone_id, second_satellite)
        for zone_id in zone_ids
    )
    model = GatewayHopCountRoutePlanningModel(gateway_candidate_count=2)
    original_ground = route_computation._required_ground_next_hops
    original_zone = route_computation._required_zone_next_hops
    calls = []

    def _ground(context, terminal, required_sources):
        calls.append(("ground", terminal, required_sources))
        return original_ground(context, terminal, required_sources)

    def _zone(context, terminal, required_sources):
        calls.append(("zone", terminal, required_sources))
        return original_zone(context, terminal, required_sources)

    monkeypatch.setattr(
        route_computation,
        "_required_ground_next_hops",
        _ground,
    )
    monkeypatch.setattr(
        route_computation,
        "_required_zone_next_hops",
        _zone,
    )
    try:
        demands = model.create_route_demands(
            scenario.physical_world,
            assignments,
        )
        result = model.plan_route_outcomes(
            scenario.physical_world,
            _connected_operational_graph(scenario),
            assignments,
            demands,
            (),
        )
    finally:
        model.shutdown()

    assert len(demands) == len(result.outcomes) == 8
    assert all(isinstance(value, ReachableRouteOutcome) for value in result.outcomes)
    context = model._routing_context
    requests = tuple(route_computation._RoutePathRequest(i,
        isinstance(d.target, GroundStationRouteDemandTarget), d.source,
        d.target.ground_station_id if isinstance(d.target, GroundStationRouteDemandTarget)
        else second_satellite) for i, d in enumerate(sorted(demands,
            key=lambda d: (d.source.value, str(d.target)))))
    engine = route_computation._GroupedRouteEngine()
    try:
        reference = engine.compute(context, requests, choice_resolvers=(_ground, _zone))
        compact = engine.compute(context, requests)
        assert compact == reference
        assert len(calls) == 3
    finally:
        engine.shutdown()
    zone_paths = {
        (value.demand.source, value.demand.target.zone_id): value.path
        for value in result.outcomes
        if isinstance(value.demand.target, ZoneRouteDemandTarget)
    }
    assert {
        zone_paths[(first_station, zone_id)] for zone_id in zone_ids
    } == {
        (
            first_station,
            scenario.satellites[0],
            second_satellite,
        )
    }
    assert {
        zone_paths[(second_station, zone_id)] for zone_id in zone_ids
    } == {(second_station, second_satellite)}


PARAMETER_SATELLITE = NodeId(65091, "satellite", "parameter-satellite")
PARAMETER_STATION = NodeId(65091, "ground-station", "parameter-station")
PARAMETER_CONNECTION_ID = OperationalConnectionId(
    OperationalConnectionKind.GROUND_STATION_ACCESS,
    PARAMETER_SATELLITE,
    PARAMETER_STATION,
)


def _parameter_model(*, threshold=0.10, max_staleness=timedelta(seconds=120)):
    configuration = ParameterKindConfiguration(1_000_000, 20,
        delay_relative_threshold=threshold, max_staleness=max_staleness)
    return AccumulatedDeadbandParameterPlanningModel(
        {kind: configuration for kind in OperationalConnectionKind})


def _parameter_observation(delay_us):
    return (ConnectionParameterObservation(PARAMETER_CONNECTION_ID, delay_us),)


@pytest.mark.parametrize('values, expected', [
    ([100, 104, 109, 110, 120, 121], [100, 100, 100, 110, 110, 121]),
    ([100, 96, 91, 90, 81], [100, 100, 100, 90, 81]),
    ([0, 0, 1, 0, 5], [0, 0, 1, 0, 5]),
])
def test_relative_delay_accumulates_equals_rebases_and_handles_zero(values, expected):
    model, memory, actual = _parameter_model(), (), []
    for i, candidate in enumerate(values):
        result = model.plan_effective_parameters(START+timedelta(seconds=5*i),
            _parameter_observation(candidate), memory)
        actual.append(result.memory[0].effective.delay_us)
        if i:
            assert bool(result.parameter_updated_connections) == (actual[-1] != actual[-2])
        memory = result.memory
    assert actual == expected


def test_maximum_refresh_is_first_formal_sample_and_noop_updates_memory():
    model = _parameter_model()
    initial = model.plan_effective_parameters(START, _parameter_observation(100), ())
    before = model.plan_effective_parameters(START+timedelta(seconds=119),
        _parameter_observation(109), initial.memory)
    assert before.memory == initial.memory
    refreshed = model.plan_effective_parameters(START+timedelta(seconds=120),
        _parameter_observation(109), before.memory)
    assert refreshed.memory[0].effective.delay_us == 109
    assert refreshed.parameter_updated_connections == refreshed.connections
    noop = model.plan_effective_parameters(START+timedelta(seconds=240),
        _parameter_observation(109), refreshed.memory)
    assert not noop.parameter_updated_connections
    assert noop.memory[0].last_update == START+timedelta(seconds=240)
    assert noop.memory[0].effective.bandwidth_bps == 1_000_000
    assert noop.memory[0].effective.packet_loss_ppm == 20


def test_parameter_removal_and_reestablishment_reset_baseline():
    model = _parameter_model()
    first = model.plan_effective_parameters(START, _parameter_observation(100), ())
    removed = model.plan_effective_parameters(START+timedelta(seconds=5), (), first.memory)
    assert removed.withdrawn_connection_ids == (PARAMETER_CONNECTION_ID,)
    assert removed.memory == ()
    new = model.plan_effective_parameters(START+timedelta(seconds=10),
        _parameter_observation(103), removed.memory)
    assert new.established_connections == new.connections
    assert new.memory[0].effective.delay_us == 103


@pytest.mark.parametrize('value', [-1, float('inf'), float('nan'), True])
def test_relative_threshold_rejects_invalid_configuration(value):
    with pytest.raises(ValueError):
        _parameter_model(threshold=value)


def test_frame_batch_policy_is_independent_of_sampling_cadence():
    policy = FrameBatchPlanningWindowPolicy(frames_per_batch=32)
    assert policy.validate_observation_interval(timedelta(seconds=7)) is None
    for invalid in (0, -1, True, 1.5):
        with pytest.raises(ValueError):
            FrameBatchPlanningWindowPolicy(frames_per_batch=invalid)
    with pytest.raises(ValueError):
        policy.validate_observation_interval(timedelta(0))


def test_default_coordination_returns_only_caller_required_precedence() -> None:
    connection = _operational_connection(
        OperationalConnectionKind.GROUND_STATION_ACCESS,
        PARAMETER_SATELLITE,
        PARAMETER_STATION,
    )
    transition = PlanTransition(
        START,
        established_connections=(connection,),
    )
    events = semantic_event_references(transition)
    by_kind = {value.kind: value for value in events}
    required = (
        SemanticEventPrecedence(
            by_kind[SemanticEventKind.CONNECTION_ESTABLISH],
            by_kind[SemanticEventKind.PARAMETER_READY],
        ),
    )
    policy = DefaultCoordinationPolicy()

    assert policy.coordinate(transition, events, required) is required
    assert policy.coordinate(transition, events, ()) == ()
