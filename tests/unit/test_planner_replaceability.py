"""Small independent Models exercise the Phase 4a-2 owner boundaries."""
from tests._support import MODEL_TIMESTAMP
from dataclasses import dataclass, fields, replace
from datetime import timedelta
import json
import subprocess
import sys

import pytest

from examples.Sky03_s3_ospf.ospf_model import OspfInterfaceConfiguration, OspfRoutePlanningModel
from skyseed.backend.bpf import compile_bpf
from skyseed.experiment.output.provenance import _model_description
from skyseed.simulator.planner import (
    CandidateDecision, CandidateDecisionKind, EffectiveConnectionParameters,
    GroundStationCandidateSearch, InterSatelliteSelection, OperationalConnection, OperationalConnectionId,
    OperationalConnectionKind,
    ParameterPlanningResult, Plan, Planner, PlannerConfiguration, PlanTransition,
    RouteDemand, GroundStationRouteDemandTarget, GatewayHopCountRoutePlanningModel,
    RoutePlanningResult, RouteUnreachableReason, UnreachableRouteOutcome,
    ZoneServiceAssignment, ZoneServicePlanningResult, apply_plan_transition,
    plan_from_data, plan_to_data, plan_transition_fingerprint,
    plan_transition_from_data, plan_transition_to_data,
    published_planning_evidence_from_data, published_planning_evidence_to_data,
)
from tests.unit.test_network_planner_semantic_core import (
    EPOCH, _world, _isl_candidate, _ground_candidate, _observation,
)


class SelectIsl:

    def __init__(self, pair):
        self.pair = pair

    def create_inter_satellite_candidate_pairs(self, world):
        return (self.pair,)

    def select_inter_satellite_links(self, world, candidates, retained):
        return InterSatelliteSelection(candidates)


class SelectGround:

    def select_ground_station_accesses(self, world, candidates, retained):
        return candidates


class UnservedZone:
    def assign_zone_service(self, world, candidates, previous, *, timestamp):
        return ZoneServicePlanningResult((ZoneServiceAssignment('zone-a', None),))


class NoEligibleRoute:
    def can_reuse_unchanged_inputs(self):
        return True

    def plan_route_outcomes(self, world, connections, assignments, demands, previous, *, timestamp):
        return RoutePlanningResult(tuple(UnreachableRouteOutcome(d, RouteUnreachableReason.NO_ELIGIBLE_PATH) for d in demands))

    def shutdown(self, *, wait=True):
        pass


@dataclass(frozen=True)
class IndependentMemory:
    successful_calls: int


class DirectionalParameters:
    def __init__(self, *, stateless=False):
        self.stateless = stateless
        self.received = []
        self.returned = []

    def plan_effective_parameters(self, timestamp, observations, previous_memory):
        self.received.append(previous_memory)
        memory = None if self.stateless else IndependentMemory(1 if previous_memory is None else previous_memory.successful_calls + 1)
        self.returned.append(memory)
        return ParameterPlanningResult(tuple(OperationalConnection(
            o.connection_id, EffectiveConnectionParameters(1_000_000, 100, 0),
            EffectiveConnectionParameters(1_000_000, 200, 0)) for o in observations), memory)


def setup_planner(*, route=None, parameters=None, batch=1, ground=None):
    world, sats, stations = _world()
    pair = sats[0].node_id, sats[1].node_id
    demand = RouteDemand(pair[0], GroundStationRouteDemandTarget(stations[0].node_id))
    planner = Planner(physical_world=world, observation_interval=timedelta(seconds=5),
        inter_satellite_model=SelectIsl(pair), ground_station_model=ground or SelectGround(),
        zone_service_model=UnservedZone(), parameter_model=parameters or DirectionalParameters(),
        route_model=route or NoEligibleRoute(),
        configuration=PlannerConfiguration(batch, inter_satellite_acquisition_duration=timedelta(0), ground_station_acquisition_duration=timedelta(0)), route_demands=(demand,))
    candidates = _isl_candidate(*sats), _ground_candidate(stations[0], sats[1])
    return world, planner, candidates, demand


def observe(world, planner, candidates, seconds=0):
    stamp = EPOCH + timedelta(seconds=seconds)
    return planner.observe(world.frame_at(stamp), _observation(stamp, isls=(candidates[0],), ground=(candidates[1],)))


@pytest.mark.parametrize('stateless', [False, True])
def test_independent_directional_model_memory_roundtrip_compiler_and_empty_reuse(stateless):
    parameters = DirectionalParameters(stateless=stateless)
    world, planner, candidates, _ = setup_planner(parameters=parameters)
    request = planner._candidate_search_request(planner.create_physical_link_request(), supports_ground_station_limits=True)
    assert request.cross_plane_neighbor_count is None and request.ground_station_candidate_limit is None
    first, = observe(world, planner, candidates)
    state = planner.get_state()
    detached = plan_from_data(json.loads(json.dumps(plan_to_data(first))))
    compiled = compile_bpf(detached, world, None)
    assert {p.delay_us for p in compiled.parameter_realizations} == {100, 200}
    assert all(c.endpoint_a_to_b.delay_us == 100 and c.endpoint_b_to_a.delay_us == 200 for c in state.operational_connections)
    assert _model_description(parameters)['configuration_source'].startswith('unavailable:')
    assert 'configuration' not in _model_description(parameters)
    planner.release_published_through(first.plan_id)
    second, = observe(world, planner, candidates, 5)
    assert parameters.received == [None, parameters.returned[0]]
    assert parameters.received[1] is parameters.returned[0]
    current = planner.get_state()
    replayed = apply_plan_transition(state, second.transitions[0])
    for name in ('operational_connections', 'zone_service_assignments', 'route_outcomes'):
        assert getattr(current, name) is getattr(state, name)
        assert getattr(replayed, name) is getattr(state, name)
    assert current.timestamp == EPOCH + timedelta(seconds=5)
    assert compiled.output_planned_state == state
    assert compile_bpf(plan_from_data(plan_to_data(second)), world, compiled).output_planned_state == current
    planner.shutdown()


def test_ospf_excluded_operational_isl_is_legitimately_unreachable_through_compiler():
    world, sats, _ = _world()
    identifier = OperationalConnectionId(OperationalConnectionKind.INTER_SATELLITE, sats[0].node_id, sats[1].node_id)
    route = OspfRoutePlanningModel(interfaces=(OspfInterfaceConfiguration(identifier, area_id=None),))
    world, planner, candidates, demand = setup_planner(route=route)
    plan, = observe(world, planner, candidates)
    assert len(planner.get_state().operational_connections) == 2
    expected = UnreachableRouteOutcome(demand, RouteUnreachableReason.NO_ELIGIBLE_PATH)
    assert planner.get_state().route_outcomes == (expected,)
    assert route.plan_route_outcomes(world, planner.get_state().operational_connections,
        planner.get_state().zone_service_assignments, (demand,), (), timestamp=MODEL_TIMESTAMP).outcomes == (expected,)
    decoded = plan_from_data(plan_to_data(plan))
    assert compile_bpf(decoded, world, None).output_planned_state.route_outcomes == (expected,)
    planner.shutdown()


def test_failed_routing_does_not_adopt_model_memory_or_state():
    class FailOnce(NoEligibleRoute):
        fail = False

        def can_reuse_unchanged_inputs(self):
            return False

        def plan_route_outcomes(self, *args, **kwargs):
            if self.fail:
                self.fail = False
                raise ValueError('planned test failure')
            return super().plan_route_outcomes(*args, **kwargs)

    route, parameters = FailOnce(), DirectionalParameters()
    world, planner, candidates, _ = setup_planner(parameters=parameters, route=route, batch=2)
    observe(world, planner, candidates)
    previous, memory = planner.get_state(), parameters.returned[-1]
    frontiers = planner.get_frontiers()
    route.fail = True
    with pytest.raises(ValueError, match='planned test failure'):
        observe(world, planner, candidates, 5)
    assert planner.get_state() is previous and planner.get_frontiers() == frontiers
    plan, = observe(world, planner, candidates, 5)
    assert parameters.received[-2] is memory and parameters.received[-1] is memory
    assert len(plan.transitions) == 2
    planner.shutdown()


def test_transition_dependencies_are_the_only_authority_and_validated_once(monkeypatch):
    from skyseed.simulator.planner import values
    world, planner, candidates, _ = setup_planner()
    count, original = [], values._validate_semantic_precedences
    def validate(*args):
        count.append(1)
        return original(*args)
    monkeypatch.setattr(values, '_validate_semantic_precedences', validate)
    plan, = observe(world, planner, candidates)
    assert len(count) == 1  # Publication adds no dependency/DAG validation pass.
    transition, = plan.transitions
    assert transition.semantic_event_precedences
    assert 'semantic_event_precedences' not in {f.name for f in fields(Plan)}
    assert not hasattr(plan, 'semantic_event_precedences')
    assert plan_transition_from_data(plan_transition_to_data(transition)) == transition
    assert plan_transition_fingerprint(transition) == transition._fingerprint
    edge = transition.semantic_event_precedences[0]
    with pytest.raises(ValueError, match='duplicate'):
        replace(transition, semantic_event_precedences=(edge, edge))
    with pytest.raises(ValueError, match='required semantic'):
        replace(transition, semantic_event_precedences=())
    planner.shutdown()


@pytest.mark.parametrize('model_type', [GatewayHopCountRoutePlanningModel, OspfRoutePlanningModel])
def test_explicit_demands_never_rank_geographical_gateways(model_type, monkeypatch):
    from skyseed.simulator.planner.routing import _demands
    def forbidden(*a, **kw):
        raise AssertionError('explicit demands must not rank Zone × GS geography')
    monkeypatch.setattr(_demands, '_rank_gateway_candidates', forbidden)
    world, planner, candidates, _ = setup_planner(route=model_type())
    observe(world, planner, candidates)
    planner.shutdown()


def test_current_evidence_rejects_retired_envelope_missing_diagnostics_and_embedded_demand():
    world, planner, candidates, _ = setup_planner()
    plan, = observe(world, planner, candidates)
    data = published_planning_evidence_to_data(planner.get_published_evidence(plan.plan_id))
    assert published_planning_evidence_from_data(data).plan == plan
    with pytest.raises(ValueError, match='schema'):
        published_planning_evidence_from_data({**data, 'schema': 'skyseed-v2-published-planning-evidence-v2'})
    for field in ('ground_station_preparations', 'physical_link_work_counts'):
        bad = json.loads(json.dumps(data));del bad['frames'][0][field]
        with pytest.raises(ValueError, match='missing or unknown'):
            published_planning_evidence_from_data(bad)
    bad = plan_to_data(plan)
    change = bad['transitions'][0]['route_outcome_transitions'][0]
    change['current']['demand'] = change['demand']
    with pytest.raises(ValueError, match='missing or unknown'):
        plan_from_data(bad)
    for schema in ('skyseed-v2-plan-evidence-v6', 'skyseed-v2-plan-evidence-v4', 'skyseed-v2-plan-evidence-v3', 'skyseed-v2-plan-evidence-v2'):
        with pytest.raises(ValueError, match='schema'):
            plan_from_data({**plan_to_data(plan), 'schema': schema})
    planner.shutdown()


def test_role_import_identity_retired_paths_and_codec_dependency_direction(tmp_path):
    program = '''
import importlib.util, sys
from skyseed.simulator.planner.evidence import plan_to_data
assert 'skyseed.simulator.planner.planner' not in sys.modules
assert not any(n.startswith('skyseed.simulator.planner.routing') for n in sys.modules)
from skyseed.simulator import PlannerConfiguration, GroundStationCandidateSearch
from skyseed.simulator.planner.configuration import PlannerConfiguration as Exact
from skyseed.simulator.planner.ground_station import GroundStationCandidateSearch as Search
assert Exact is PlannerConfiguration and Search is GroundStationCandidateSearch
for path in ('skyseed.simulator.planner.routing.ospf', 'skyseed.simulator.planning', 'skyseed.simulator._route_computation',
             'skyseed.simulator._routing_core', 'skyseed.simulator._ground_station_allocation',
             'skyseed.simulator._gateway_candidates', 'skyseed.simulator.planner.models'):
    assert importlib.util.find_spec(path) is None, path
import skyseed.simulator.planner as planner
for name in ('OspfRoutePlanningModel', 'OspfInterfaceConfiguration', 'PlanningWindowPolicy', 'FrameBatchPlanningWindowPolicy', 'ParameterPlanningMemory'):
    assert not hasattr(planner, name), name
assert not hasattr(planner.RouteUnreachableReason, 'NO_OPERATIONAL_PATH')
from skyseed.simulator.planner import routing
assert not hasattr(routing, 'OspfRoutePlanningModel')
assert not hasattr(routing, 'OspfInterfaceConfiguration')
'''
    result = subprocess.run([sys.executable, '-B', '-c', program], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_document_links_fail_without_historical_git_dependency(tmp_path, monkeypatch):
    from pathlib import Path
    import inspect
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'scripts'))
    import check_documentation as checker
    monkeypatch.setattr(checker, 'ROOT', tmp_path)
    document = tmp_path / 'current.md'
    document.write_text('[missing](retired.py)')
    with pytest.raises(AssertionError, match='broken documentation links'):
        checker._validate_links((document,))
    (tmp_path / 'retired.py').write_text('')
    checker._validate_links((document,))
    source = inspect.getsource(checker._validate_links)
    assert 'subprocess' not in source and 'cat-file' not in source and '375501' not in source


def test_independent_search_advice_preserves_planner_retention_and_physical_coverage():
    class AdvisedGround(SelectGround):
        def initial_candidate_search(self, world, *, minimum_contact_duration):
            self.duration = minimum_contact_duration
            return GroundStationCandidateSearch(1)

        def expand_candidate_search(self, world, *, search, minimum_contact_duration,
                                    state, returned_candidates):
            assert search == GroundStationCandidateSearch(1)
            assert minimum_contact_duration == self.duration
            assert state.timestamp == EPOCH
            assert state.selected_since and returned_candidates
            return GroundStationCandidateSearch(2)

    world, planner, candidates, _ = setup_planner(ground=AdvisedGround())
    # The custom implementation shares no fields with the built-in Model.
    observe(world, planner, candidates)
    request = planner._candidate_search_request(planner.create_physical_link_request(), supports_ground_station_limits=True)
    assert request.ground_station_candidate_limit == 1
    assert request.retained_ground_station_pairs == ((candidates[1].ground_station_id, candidates[1].satellite_id),)
    assert request.retained_inter_satellite_pairs == ((candidates[0].endpoint_a, candidates[0].endpoint_b),)
    observation = _observation(EPOCH, isls=(candidates[0],), ground=(candidates[1],))
    observation = replace(observation, ground_station_search_complete=False, work_counts=None)
    expanded = planner._expanded_candidate_request(request, observation)
    assert expanded == replace(request, ground_station_candidate_limit=2)
    planner.shutdown()


def test_changed_connection_collection_reuses_assignments_and_outcomes():
    world, planner, candidates, _ = setup_planner()
    observe(world, planner, candidates)
    state = planner.get_state()
    changed = replace(state.operational_connections[0], endpoint_a_to_b=EffectiveConnectionParameters(1_000_000, 300, 0))
    transition = PlanTransition(EPOCH + timedelta(seconds=5), parameter_updated_connections=(changed,))
    result = apply_plan_transition(state, transition)
    assert result.operational_connections is not state.operational_connections
    assert changed in result.operational_connections
    assert result.zone_service_assignments is state.zone_service_assignments
    assert result.route_outcomes is state.route_outcomes
    assert {f.name for f in fields(ParameterPlanningResult)} == {'connections', 'memory'}
    assert {f.name for f in fields(ZoneServicePlanningResult)} == {'assignments'}
    assert {f.name for f in fields(RoutePlanningResult)} == {'outcomes'}
    planner.shutdown()
