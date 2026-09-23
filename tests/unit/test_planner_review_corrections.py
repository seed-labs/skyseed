"""Small independent checks for the three Phase 4a-2 review corrections."""
from dataclasses import replace
from datetime import timedelta
from itertools import combinations
import json
from types import MappingProxyType

import pytest

from examples.Sky03_s3_ospf.ospf_model import OspfInterfaceConfiguration, OspfRoutePlanningModel
from skyseed.backend.bpf import compile_bpf
from skyseed.experiment.output.observation import _ExecutionObserver
from skyseed.simulator.planner import (
    EffectiveConnectionParameters,
    GatewayHopCountRoutePlanningModel, GroundStationRouteDemandTarget,
    OperationalConnection, OperationalConnectionId, OperationalConnectionKind,
    ParameterPlanningResult, Planner, PlannerConfiguration,
    ReachableRouteOutcome, RouteDemand, RoutePlanningResult,
    plan_from_data, plan_to_data, plan_transition_from_data, plan_transition_to_data,
    plan_transition_fingerprint, semantic_event_references,
)
from tests.unit.test_network_planner_semantic_core import (
    EPOCH, _world, _satellite, _isl_candidate, _ground_candidate, _observation,
)
from tests.unit.test_planner_replaceability import (
    SelectIsl, SelectGround, UnservedZone, NoEligibleRoute, DirectionalParameters,
    setup_planner, observe,
)


class ImmediateParameters:
    def plan_effective_parameters(self, timestamp, observations, previous_memory):
        return ParameterPlanningResult(tuple(OperationalConnection(
            item.connection_id,
            EffectiveConnectionParameters(1_000_000, item.physical_delay_us, 0),
            EffectiveConnectionParameters(1_000_000, 77, 0),
        ) for item in observations))


class TriangleIsl(SelectIsl):
    def create_inter_satellite_candidate_pairs(self, world):
        return self.pair


class ParameterDependentRoutes:
    def can_reuse_unchanged_inputs(self):
        return True

    """An independent tiny policy with no optional dependency declaration."""
    def __init__(self, satellites):
        self.satellites = satellites
        self.calls = 0

    def plan_route_outcomes(self, world, connections, assignments, demands, previous, *, timestamp):
        self.calls += 1
        a, b, c = self.satellites
        direct = next(v for v in connections if v.connection_id == OperationalConnectionId(
            OperationalConnectionKind.INTER_SATELLITE, a, c))
        prefix = (a, c) if direct.endpoint_a_to_b.delay_us < 10 else (a, b, c)
        return RoutePlanningResult(tuple(ReachableRouteOutcome(
            demand, prefix[1], (*prefix, demand.target.ground_station_id)) for demand in demands))

    def shutdown(self, *, wait=True):
        pass


def triangle(route_factory):
    world, satellites, stations = _world(satellites=tuple(_satellite(i) for i in range(3)))
    nodes = tuple(s.node_id for s in satellites)
    model = route_factory(nodes)
    demand = RouteDemand(nodes[0], GroundStationRouteDemandTarget(stations[0].node_id))
    planner = Planner(physical_world=world, observation_interval=timedelta(seconds=5),
        inter_satellite_model=TriangleIsl(tuple(combinations(nodes, 2))),
        ground_station_model=SelectGround(), zone_service_model=UnservedZone(),
        parameter_model=ImmediateParameters(), route_model=model,
        configuration=PlannerConfiguration(1, inter_satellite_acquisition_duration=timedelta(0), ground_station_acquisition_duration=timedelta(0)),
        route_demands=(demand,))

    def advance(seconds, *, omit_direct=False):
        timestamp = EPOCH + timedelta(seconds=seconds)
        candidates = tuple(_isl_candidate(a, b, delay_us=(100 if seconds == 0 else 1)
            if (a, b) == (satellites[0], satellites[2]) else 10)
            for a, b in combinations(satellites, 2)
            if not omit_direct or (a, b) != (satellites[0], satellites[2]))
        plan, = planner.observe(world.frame_at(timestamp), _observation(timestamp,
            isls=candidates, ground=(_ground_candidate(stations[0], satellites[2]),)))
        planner.release_published_through(plan.plan_id)
        return plan
    return world, planner, model, nodes, stations[0].node_id, advance


@pytest.mark.parametrize('custom', [False, True])
def test_parameter_only_change_updates_routes_through_publication_codec_and_compiler(custom):
    factory = ParameterDependentRoutes if custom else lambda nodes: OspfRoutePlanningModel(default_area=0, cost_metric='delay_us')
    world, planner, model, (a, b, c), ground, advance = triangle(factory)
    try:
        first = advance(0)
        initial = compile_bpf(plan_from_data(plan_to_data(first)), world, None)
        assert initial.output_planned_state.route_outcomes[0].path == (a, b, c, ground)
        second = advance(5)
        change, = second.transitions
        assert len(change.parameter_updated_connections) == 1
        route_change, = change.route_outcome_transitions
        assert route_change.previous.path == (a, b, c, ground)
        assert route_change.current.path == (a, c, ground)
        decoded = plan_from_data(json.loads(json.dumps(plan_to_data(second))))
        compiled = compile_bpf(decoded, world, initial)
        assert compiled.output_planned_state.route_outcomes[0].path == (a, c, ground)
        forwarding = next(v for v in compiled.forwarding_realizations if v.route_key.source == a)
        assert forwarding.previous.next_hop == b and forwarding.current.next_hop == c
        connection = change.parameter_updated_connections[0]
        assert (connection.endpoint_a_to_b.delay_us, connection.endpoint_b_to_a.delay_us) == (1, 77)
        assert planner.get_route_recomputation_count() == 2
        assert not advance(10).transitions[0].route_outcome_transitions
        assert planner.get_route_recomputation_count() == 2
        if custom:
            assert model.calls == 2
            assert not hasattr(model, 'depends_on_connection_parameters')
    finally:
        planner.shutdown()


@pytest.mark.parametrize('model_type', [GatewayHopCountRoutePlanningModel, OspfRoutePlanningModel])
def test_explicit_parameter_independence_preserves_skipping_and_other_triggers(model_type):
    class Counted(model_type):
        evaluations = dependency_queries = 0
        def plan_route_outcomes(self, *args, timestamp):
            self.evaluations += 1
            return super().plan_route_outcomes(*args, timestamp=timestamp)
        def depends_on_connection_parameters(self):
            self.dependency_queries += 1
            return super().depends_on_connection_parameters()

    world, planner, model, (a, b, c), ground, advance = triangle(lambda nodes: Counted())
    try:
        advance(0)
        assert (model.evaluations, model.dependency_queries) == (1, 0)
        assert len(advance(5).transitions[0].parameter_updated_connections) == 1
        assert (model.evaluations, model.dependency_queries) == (1, 1)
        advance(10)
        assert (model.evaluations, model.dependency_queries) == (1, 1)
        planner.set_route_demands((RouteDemand(b, GroundStationRouteDemandTarget(ground)),))
        advance(15)
        assert (model.evaluations, model.dependency_queries) == (2, 1)
        advance(20, omit_direct=True)
        assert (model.evaluations, model.dependency_queries) == (3, 1)
    finally:
        planner.shutdown()


class PrivateSentinels:
    def __getattribute__(self, name):
        if name in {'_selection_work', '_allocation_work', '_route_work_counts', '_route_resource_counts'}:
            raise AssertionError('Planner touched unrelated private Model state: ' + name)
        return super().__getattribute__(name)


@pytest.mark.parametrize('recording', [False, True])
@pytest.mark.parametrize('supported', [False, True])
def test_statistics_are_optional_public_and_never_queried_with_recording_disabled(recording, supported):
    calls = []
    class Isl(PrivateSentinels, SelectIsl):
        pass
    class Ground(PrivateSentinels, SelectGround):
        pass
    class Route(PrivateSentinels, NoEligibleRoute):
        pass
    def statistic(label, key, value):
        def read(self):
            assert recording, 'disabled recording called a statistics capability'
            calls.append(label)
            return MappingProxyType({key: value})
        return read
    if supported:
        Isl.selection_statistics = statistic('isl', 'isl_custom_work', 11)
        Ground.allocation_statistics = statistic('ground', 'ground_custom_work', 12)
        Route.route_work_statistics = statistic('route', 'route_custom_work', 13)
        Route.route_resource_statistics = statistic('resources', 'route_custom_resources', 14)
    world, sats, stations = _world()
    pair = tuple(s.node_id for s in sats)
    observer = _ExecutionObserver() if recording else None
    planner = Planner(physical_world=world, observation_interval=timedelta(seconds=5),
        inter_satellite_model=Isl(pair), ground_station_model=Ground(),
        zone_service_model=UnservedZone(), parameter_model=DirectionalParameters(), route_model=Route(),
        configuration=PlannerConfiguration(1, inter_satellite_acquisition_duration=timedelta(0), ground_station_acquisition_duration=timedelta(0)),
        route_demands=(RouteDemand(pair[0], GroundStationRouteDemandTarget(stations[0].node_id)),),
        execution_observer=observer)
    candidates = _isl_candidate(*sats), _ground_candidate(stations[0], sats[1])
    try:
        observe(world, planner, candidates)
        observe(world, planner, candidates, 5)
        if recording and supported:
            assert calls == ['isl', 'ground', 'route', 'resources', 'isl', 'ground', 'resources']
            records = observer.snapshot()['records']
            expected = [('isl-candidate-selection', 'work', 'isl_custom_work', 11),
                        ('ground-station-selection-and-ranking', 'work', 'ground_custom_work', 12),
                        ('route-planning', 'work', 'route_custom_work', 13),
                        ('route-planning', 'resources', 'route_custom_resources', 14)]
            for stage, section, key, value in expected:
                assert next(row for row in records if row['stage'] == stage)[section][key] == value
            assert observer._get_recording_failure() is None
        else:
            assert calls == []
    finally:
        planner.shutdown()


def dependency_fixture():
    world, planner, candidates, _ = setup_planner(route=GatewayHopCountRoutePlanningModel())
    try:
        plan, = observe(world, planner, candidates)
        return plan.transitions[0]
    finally:
        planner.shutdown()


def test_compact_dependencies_share_canonical_events_and_construct_validate_once(monkeypatch):
    from skyseed.simulator.planner import values
    transition = dependency_fixture()
    data = plan_transition_to_data(transition)
    assert data['schema'] == 'skyseed-v2-plan-transition-evidence-v7'
    assert len(semantic_event_references(transition)) == 5
    pairs = data['semantic_event_precedences']
    assert len(pairs) == 4 and all(len(pair) == 2 and all(type(i) is int for i in pair) for pair in pairs)
    assert len(json.dumps(pairs, separators=(',', ':'))) == 25
    assert 'events' not in data and 'canonical_events' not in data
    # The same fixed fixture occupied 4346 bytes in the reviewed verbose format;
    # the recorded before/after comparison is kept with this correction's evidence.
    assert len(json.dumps(data, separators=(',', ':'))) == 1646
    counts = {'events': 0, 'initialization': 0, 'validation': 0}
    def count(cls, name, label):
        original = getattr(cls, name)
        def wrapped(*args, **kwargs):
            counts[label] += 1
            return original(*args, **kwargs)
        monkeypatch.setattr(cls, name, wrapped)
    count(values.SemanticEventReference, '__post_init__', 'events')
    count(values.PlanTransition, '_initialize', 'initialization')
    count(values, '_validate_semantic_precedences', 'validation')
    decoded = plan_transition_from_data(json.loads(json.dumps(data)))
    assert counts == {'events': 5, 'initialization': 1, 'validation': 1}
    assert decoded == transition and plan_transition_to_data(decoded) == data
    canonical = semantic_event_references(decoded)
    endpoints = [endpoint for edge in decoded.semantic_event_precedences for endpoint in (edge.predecessor, edge.successor)]
    assert all(any(endpoint is event for event in canonical) for endpoint in endpoints)
    assert len({id(endpoint) for endpoint in endpoints}) < len(endpoints)
    assert plan_transition_fingerprint(decoded) == plan_transition_fingerprint(transition)
    assert plan_transition_fingerprint(decoded) == decoded._fingerprint
    material = values._plan_transition_material(decoded)
    assert material['semantic_event_precedences'] == pairs
    assert material['schema'] == 'skyseed-v2-plan-transition-v6'
    assert plan_transition_to_data(replace(transition, semantic_event_precedences=tuple(reversed(transition.semantic_event_precedences)))) == data


def test_compact_dependency_decoder_keeps_reference_required_order_and_cycle_checks():
    data = plan_transition_to_data(dependency_fixture())
    pairs = data['semantic_event_precedences']
    first, second = pairs[0]
    for malformed, error, message in (
        ([[first, 5]], ValueError, 'outside canonical'),
        ([[-1, second]], ValueError, 'outside canonical'),
        ([[True, second]], TypeError, 'integer'),
        ([[first]], ValueError, 'two event indexes'),
        ([], ValueError, 'required semantic'),
        ([*pairs, pairs[0]], ValueError, 'duplicate'),
        ([*pairs, [second, first]], ValueError, 'acyclic'),
    ):
        with pytest.raises(error, match=message):
            plan_transition_from_data({**data, 'semantic_event_precedences': malformed})
    with pytest.raises(ValueError, match='schema'):
        plan_transition_from_data({**data, 'schema': 'skyseed-v2-plan-transition-evidence-v4'})
    with pytest.raises(TypeError, match='list'):
        plan_transition_from_data({**data, 'semantic_event_precedences': [{'predecessor': {}, 'successor': {}}]})
