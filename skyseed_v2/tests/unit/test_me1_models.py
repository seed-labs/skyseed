"""ME1 contracts through the actual separately copyable, independent templates."""
from asyncio import CancelledError
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path

import pytest

from templates.models.position import StaticPositionModel
from templates.models.zone_membership import FirstMatchMembershipModel
from templates.models.physical_link import StaticPhysicalLinkModel
from templates.models.inter_satellite import SimpleInterSatelliteModel
from templates.models.ground_station import SimpleGroundStationModel
from templates.models.zone_service import FirstCandidateServiceModel
from templates.models.parameters import FixedParameterModel
from templates.models.routing import SimpleRouteModel
from skyseed_v2 import simulator
from skyseed_v2.simulator import physical, planner
from skyseed_v2.simulator.input import SimulatorStaticInput
from skyseed_v2.simulator.geometry import EarthFixedPosition
from skyseed_v2.configuration.simulator import (
    ZoneLayoutDefinition, ZoneDefinition, LatitudeLongitudeRectangleDefinition,
)
from skyseed_v2.backend.bpf import compile_bpf
from skyseed_v2.experiment import Producer, ExperimentController
from skyseed_v2.experiment.output.physical_links import _PhysicalLinkInspectionWriter
from skyseed_v2.experiment.output.observation import _ExecutionObserver
from tests.unit.test_network_planner_semantic_core import EPOCH, _satellite, _station


def assembly(*, interval=5, metrics=False, route=None, demands='explicit', configuration=None, create=True):
    sats, station = (_satellite(0), _satellite(1)), _station()
    a, b, g = sats[0].node_id, sats[1].node_id, station.node_id
    # Deliberately through-Earth coordinates, not a circular-orbit implementation.
    position = StaticPositionModel({a: EarthFixedPosition(7e6, 0, 0),
        b: EarthFixedPosition(-7e6, 0, 0), g: EarthFixedPosition(6e6, 0, 0)})
    isl = physical.InterSatelliteLinkCandidate(a, b)
    gs = physical.GroundStationAccessCandidate(g, b)
    zone = physical.ZoneServiceCandidate('zone-a', b)
    if metrics:
        isl = replace(isl, same_plane=False, cyclic_plane_separation=0, distance_m=1e16, propagation_delay_us=-7)
        gs = replace(gs, distance_m=1e16, elevation_deg=-70, propagation_delay_us=-9)
        zone = replace(zone, distance_m=1e16, elevation_deg=-70, propagation_delay_us=-11)
    models = dict(position_model=position, zone_membership_model=FirstMatchMembershipModel(),
        physical_link_model=StaticPhysicalLinkModel(inter_satellite=(isl,), ground_station=(gs,), zone_service=(zone,)),
        inter_satellite_model=SimpleInterSatelliteModel(((a, b),)), ground_station_model=SimpleGroundStationModel(),
        zone_service_model=FirstCandidateServiceModel(), parameter_model=FixedParameterModel(),
        route_model=route if route is not None else SimpleRouteModel())
    definition = simulator.SimulationDefinition(SimulatorStaticInput(sats, (station,)),
        simulator.SimulationTimeline(EPOCH, EPOCH + timedelta(seconds=10), timedelta(seconds=interval)))
    zones = ZoneLayoutDefinition(zones=(ZoneDefinition(zone_id='zone-a',
        geometry=LatitudeLongitudeRectangleDefinition(-10, 10, -10, 10)),), allow_overlap=False)
    explicit = (planner.RouteDemand(a, planner.GroundStationRouteDemandTarget(g)),
                planner.RouteDemand(g, planner.ZoneRouteDemandTarget('zone-a')))
    values = explicit if demands == 'explicit' else demands
    if not create:
        return definition, zones, models
    sim = simulator.create_default_simulator(definition,
        configuration=simulator.DefaultSimulatorConfiguration(zone_layout_definition=zones),
        planner_configuration=configuration or planner.PlannerConfiguration(frames_per_batch=1),
        route_demands=values, **models)
    return sim, models, (a, b, g), explicit


def observe(sim, seconds, observation=None):
    world, plan = sim.get_physical_world(), sim.get_planner()
    if plan._published_evidence:
        plan.release_published_through(plan._published_evidence[-1].plan.plan_id)
    frame = world.frame_at(EPOCH + timedelta(seconds=seconds))
    facts = observation or sim.get_physical_link_model().compute_observation(world, frame, plan.create_physical_link_request())
    return frame, facts, plan.observe(frame, facts)


@pytest.mark.parametrize('metrics', [False, True])
def test_all_minimum_templates_nonempty_compile_and_optional_free(metrics):
    sim, models, ids, demands = assembly(metrics=metrics)
    contracts = (physical.PositionModel, physical.ZoneMembershipModel, physical.PhysicalLinkModel,
        planner.InterSatelliteConnectionPlanningModel, planner.GroundStationAccessPlanningModel,
        planner.ZoneServicePlanningModel, planner.ParameterPlanningModel, planner.RoutePlanningModel)
    for model, contract in zip(models.values(), contracts, strict=True):
        assert isinstance(model, contract)
        assert type(model).__bases__ == (object,)
        assert not any(hasattr(model, hook) for hook in ('shutdown', 'required_contact_duration',
            'selection_diagnostics', 'selection_statistics', 'allocation_statistics', 'predict_contacts',
            'route_work_statistics', 'route_resource_statistics', 'can_reuse_unchanged_inputs'))
    world, plan = sim.get_physical_world(), sim.get_planner()
    assert world.resolve_zone(0, 0).zone_id == 'zone-a'
    frame, facts, first = observe(sim, 0)
    assert len(frame.positions) == 3 and facts.work_counts is None
    assert not plan.get_state().operational_connections
    assert plan.get_state().zone_service_assignments[0].satellite_id == ids[1]
    evidence = plan.get_published_evidence(first[0].plan_id)
    assert evidence.inspections[0].underfilled_isl_slots is None
    assert evidence.frames[0].selected_inter_satellite_links[0].selected_since == EPOCH
    result = compile_bpf(first[0], world, None)
    _, _, second = observe(sim, 5)
    assert len(plan.get_state().operational_connections) == 2
    assert all(isinstance(o, planner.ReachableRouteOutcome) for o in plan.get_state().route_outcomes)
    result = compile_bpf(second[0], world, result)
    assert result.output_planned_state == plan.get_state()
    assert all(c.endpoint_a_to_b.delay_us == 100 for c in plan.get_state().operational_connections)
    decoded = planner.published_planning_evidence_from_data(json.loads(json.dumps(
        planner.published_planning_evidence_to_data(plan.get_published_evidence(second[0].plan_id)))))
    assert decoded.plan == second[0]
    sim.shutdown()
    assert world.autonomous_systems and world.get_zone_layout().zones and plan.get_state()
    with pytest.raises(RuntimeError, match='shut down'):
        world.frame_at(EPOCH)
    with pytest.raises(RuntimeError, match='shut down'):
        world.resolve_zone(0, 0)


def test_preparation_projection_original_start_cancellation_and_capacity():
    sim, models, ids, _ = assembly(interval=1)
    captured = []
    original = models['inter_satellite_model'].select_inter_satellite_links
    def select(world, candidates, state):
        captured.append(state)
        with pytest.raises(TypeError):
            state.selected_since['foreign'] = EPOCH
        return original(world, candidates, state)
    models['inter_satellite_model'].select_inter_satellite_links = select
    for seconds in range(9):
        if seconds == 2:
            obs = physical.PhysicalLinkQueryResult(EPOCH + timedelta(seconds=seconds),
                ground_station_access_candidates=models['physical_link_model'].ground_station,
                zone_service_candidates=models['physical_link_model'].zone_service)
        else:
            obs = None
        observe(sim, seconds, obs)
    key = planner.OperationalConnectionId(planner.OperationalConnectionKind.INTER_SATELLITE, *ids[:2])
    assert captured[1].selected_since[key] == EPOCH and not captured[1].operational_connections
    assert key not in captured[3].selected_since
    assert captured[8].selected_since[key] == EPOCH + timedelta(seconds=3)
    assert key not in captured[8].operational_connections  # Operational only after this selection step.
    assert len(sim.get_planner().get_state().operational_connections) == 2
    sim.shutdown()


@pytest.mark.parametrize('feedback,capability,expected', [
    (True, True, [1]), (False, True, [1, 2, None]),
    (None, True, [1, None]), (None, False, [None]),
])
def test_search_feedback_without_statistics_and_no_limited_probe_for_minimal(feedback, capability, expected, capsys):
    sim, models, _, _ = assembly()
    world = sim.get_physical_world()
    # A policy can require expansion even when the returned subset is small.
    ground, link = models['ground_station_model'], models['physical_link_model']
    ground.initial_candidate_search = lambda world, **kw: planner.GroundStationCandidateSearch(1)
    states = []
    def expand(world, *, search, minimum_contact_duration, state, returned_candidates):
        states.append(state)
        assert returned_candidates and state.acquisition_duration == timedelta(seconds=5)
        return planner.GroundStationCandidateSearch(2 if search.candidate_limit == 1 else None)
    ground.expand_candidate_search = expand
    def broken_statistics():
        raise RuntimeError('optional allocation counts failed')
    ground.allocation_statistics = broken_statistics
    if capability:
        link.supports_ground_station_candidate_limits = lambda: True
    queried, original = [], link.compute_observation
    def compute(world, frame, request):
        queried.append(request.ground_station_candidate_limit)
        return replace(original(world, frame, request), ground_station_search_complete=feedback)
    link.compute_observation = compute
    sim._set_execution_observer(_ExecutionObserver())
    producer = Producer(sim.get_timeline(), world, link, sim.get_planner())
    producer._ensure_prediction()
    frame = world.frame_at(EPOCH)
    facts = producer._compute_physical_observation(frame)
    assert queried == expected
    sim.get_planner().observe(frame, facts)
    assert 'optional allocation counts failed' in capsys.readouterr().err
    producer.close()


@pytest.mark.parametrize('reusable', [False, True])
def test_time_state_and_automatic_demands_are_evaluated_before_reuse(reusable):
    class TimedRoutes:
        def __init__(self):
            self.times, self.factory_times = [], []
            self.algorithm = SimpleRouteModel()
        def create_route_demands(self, world, assignments, *, timestamp):
            self.factory_times.append(timestamp)
            if timestamp < EPOCH + timedelta(seconds=10):
                return ()
            stations = world.autonomous_systems[0].ground_stations
            return (planner.RouteDemand(stations[0].node_id, planner.ZoneRouteDemandTarget('zone-a')),)
        def plan_route_outcomes(self, *args, timestamp):
            self.times.append(timestamp)
            return self.algorithm.plan_route_outcomes(*args, timestamp=timestamp)
    route = TimedRoutes()
    if reusable:
        route.can_reuse_unchanged_inputs = lambda: True
    sim, _, _, _ = assembly(route=route, demands=None,
        configuration=planner.PlannerConfiguration(1, timedelta(0), timedelta(0)))
    for seconds in (0, 5, 10):
        observe(sim, seconds)
    assert route.factory_times == [EPOCH + timedelta(seconds=s) for s in (0, 5, 10)]
    assert route.times == [EPOCH + timedelta(seconds=s) for s in ((0, 10) if reusable else (0, 5, 10))]
    assert len(sim.get_planner().get_state().route_outcomes) == 1
    sim.shutdown()


@pytest.mark.parametrize('demands', [(), 'explicit'])
def test_explicit_demands_including_empty_never_invoke_factory(demands):
    route = SimpleRouteModel()
    route.create_route_demands = lambda *a, **kw: pytest.fail('explicit demands invoked factory')
    sim, _, _, _ = assembly(route=route, demands=demands)
    observe(sim, 0)
    observe(sim, 5)
    assert len(sim.get_planner().get_state().route_outcomes) == (0 if demands == () else 2)
    sim.shutdown()


def test_stateful_route_changes_without_input_changes_compile():
    class StatefulRoutes:
        def __init__(self):
            self.calls = 0
            self.algorithm = SimpleRouteModel()
        def plan_route_outcomes(self, world, connections, services, demands, previous, *, timestamp):
            self.calls += 1
            if self.calls % 2 == 0:
                return planner.RoutePlanningResult(tuple(planner.UnreachableRouteOutcome(
                    d, planner.RouteUnreachableReason.NO_ELIGIBLE_PATH) for d in demands))
            return self.algorithm.plan_route_outcomes(world, connections, services, demands, previous, timestamp=timestamp)
    route = StatefulRoutes()
    sim, _, _, _ = assembly(route=route, configuration=planner.PlannerConfiguration(1, timedelta(0), timedelta(0)))
    previous, outcomes = None, []
    for seconds in (0, 5, 10):
        _, _, plans = observe(sim, seconds)
        previous = compile_bpf(plans[0], sim.get_physical_world(), previous)
        outcomes.append(type(sim.get_planner().get_state().route_outcomes[0]))
    assert route.calls == 3
    assert outcomes == [planner.ReachableRouteOutcome, planner.UnreachableRouteOutcome, planner.ReachableRouteOutcome]
    sim.shutdown()


def resource_hooks(models, failing=None):
    events = []
    for name, model in models.items():
        def make_close(name):
            closed = False
            def close(*, wait=True):
                nonlocal closed
                assert wait
                if closed:
                    return
                closed = True
                events.append(name)
                if name == failing:
                    raise RuntimeError('resource failure: ' + name)
            return close
        model.shutdown = make_close(name)
    return events


@pytest.mark.parametrize('failure_role', [None, 'position_model', 'zone_membership_model', 'physical_link_model',
    'inter_satellite_model', 'ground_station_model', 'zone_service_model', 'parameter_model', 'route_model'])
def test_every_role_closes_and_remaining_cleanup_is_attempted(failure_role):
    sim, models, _, _ = assembly()
    events = resource_hooks(models, failure_role)
    if failure_role:
        with pytest.raises(RuntimeError, match='resource failure'):
            sim.shutdown()
    else:
        sim.shutdown()
    assert set(events) == set(models) and len(events) == 8
    sim.shutdown()
    assert len(events) == 8
    with pytest.raises(RuntimeError, match='shut down'):
        sim.get_physical_world().frame_at(EPOCH)


@pytest.mark.parametrize('mode', ['normal', 'cancel', 'failure', 'retained'])
def test_producer_drains_resources_and_retained_idle_keeps_them_open(mode, tmp_path):
    sim, models, _, _ = assembly()
    events = resource_hooks(models)
    producer = Producer(sim.get_timeline(), sim.get_physical_world(), models['physical_link_model'],
                        sim.get_planner(), retain=mode == 'retained')
    if mode == 'failure':
        models['physical_link_model'].compute_observation = lambda *args: (_ for _ in ()).throw(ValueError('calculation failed'))
        with pytest.raises(ValueError, match='calculation failed'):
            with producer:
                producer.produce_next_realization()
    elif mode == 'cancel':
        result = producer.produce(tmp_path, through='compiler',
            delivery_ends=(EPOCH, EPOCH + timedelta(seconds=10)),
            publish=lambda *args: producer.cancel_computation())
        assert result['cancellation_requested'] and result['sealed_batches'] == 1
        producer.close()
    else:
        while producer.produce_next_realization() is not None:
            pass
        if mode == 'retained':
            assert not events and producer.snapshot()['formal_input_exhausted']
            producer.extend(EPOCH + timedelta(seconds=15))
            assert producer.produce_next_realization() is not None
            while producer.produce_next_realization() is not None:
                pass
            producer.finish()
        producer.close()
    assert len(events) == 8
    producer.close()
    assert len(events) == 8


@pytest.mark.parametrize('cutoff', ['physical-world', 'planner'])
def test_controller_cutoff_and_failure_before_producer_close_all_roles(tmp_path, cutoff):
    import seedemu as seed
    sim, models, _, _ = assembly()
    events = resource_hooks(models)
    controller = ExperimentController(seed.Emulator(), sim, tmp_path / cutoff,
        entry_path=Path(__file__), through=cutoff, traffic_enabled=False)
    controller.generate()
    assert len(events) == 8
    broken, owned, _, _ = assembly()
    closed = resource_hooks(owned)
    controller = ExperimentController(seed.Emulator(), broken, tmp_path / 'bad',
        entry_path=tmp_path / 'missing-source.py', through=cutoff, traffic_enabled=False)
    with pytest.raises(FileNotFoundError):
        controller.generate()
    assert len(closed) == 8


def test_partial_default_assembly_cleans_supplied_resources_and_preserves_original(monkeypatch):
    definition, zones, supplied = assembly(create=False)
    events = resource_hooks(supplied, 'route_model')
    from skyseed_v2.simulator import defaults
    def fail(*args, **kwargs):
        raise ValueError('physical construction failed')
    monkeypatch.setattr(defaults, 'GeometricPhysicalLinkModel', fail)
    supplied.pop('physical_link_model')
    with pytest.raises(ValueError, match='physical construction failed') as caught:
        simulator.create_default_simulator(definition,
            configuration=simulator.DefaultSimulatorConfiguration(zone_layout_definition=zones), **supplied)
    assert set(events) == set(supplied)
    assert any('resource failure' in note for note in caught.value.__notes__)


@pytest.mark.parametrize('mode', ['get', 'invoke', 'consume'])
def test_optional_diagnostics_failures_do_not_change_plans(mode, capsys):
    baseline, _, _, _ = assembly()
    expected = observe(baseline, 0)[2]
    baseline.shutdown()
    sim, models, _, _ = assembly()
    model = models['inter_satellite_model']
    class BrokenDiagnostic(planner.InterSatelliteSelectionDiagnostics):
        def __getattribute__(self, name):
            if name == 'decisions':
                raise ValueError('diagnostic consumption failed')
            return super().__getattribute__(name)
    if mode == 'get':
        def fail(self):
            raise ValueError('diagnostic retrieval failed')
        model.__class__ = type('BrokenRead', (SimpleInterSatelliteModel,), {'selection_diagnostics': property(fail)})
    else:
        def read():
            if mode == 'invoke':
                raise ValueError('diagnostic invocation failed')
            result = object.__new__(BrokenDiagnostic)
            object.__setattr__(result, 'underfilled_slots', 1)
            return result
        model.selection_diagnostics = read
    actual = observe(sim, 0)[2]
    assert actual == expected
    assert 'planning diagnostics degraded' in capsys.readouterr().err
    sim.shutdown()


def test_diagnostic_cancellation_and_algorithm_failure_propagate():
    sim, models, _, _ = assembly()
    def cancel():
        raise CancelledError('diagnostic cancellation')
    models['inter_satellite_model'].selection_diagnostics = cancel
    before = sim.get_planner()._snapshot_private_state()
    with pytest.raises(CancelledError):
        observe(sim, 0)
    assert sim.get_planner()._snapshot_private_state() == before
    del models['inter_satellite_model'].selection_diagnostics
    def failed_algorithm(*args):
        raise ValueError('selection computation failed')
    original = models['inter_satellite_model'].select_inter_satellite_links
    models['inter_satellite_model'].select_inter_satellite_links = failed_algorithm
    with pytest.raises(ValueError, match='selection computation failed'):
        observe(sim, 0)
    assert sim.get_planner()._snapshot_private_state() == before
    models['inter_satellite_model'].select_inter_satellite_links = original
    assert observe(sim, 0)[2]
    sim.shutdown()


def test_basic_tools_preserve_missing_measures_and_never_recompute(tmp_path):
    from tools._inspection_evidence import iter_physical_link_inspection
    from tools.physical_world import write_physical_world_tool, collect_physical_world_data
    from tools.planner import write_planner_tool, collect_planner_data
    sim, models, _, _ = assembly()
    world, owner = sim.get_physical_world(), sim.get_planner()
    writer = _PhysicalLinkInspectionWriter(tmp_path / 'facts', world)
    evidence = []
    for seconds in (0, 5):
        frame, facts, plans = observe(sim, seconds)
        writer.record(facts)
        evidence.extend(owner.get_published_evidence(p.plan_id) for p in plans)
    path = writer.finalize()
    writer.close()
    # finalize returns the manifest; Tools consume the sealed stream.
    path = tmp_path / 'facts' / 'physical-link-observations.jsonl.gz'
    records = tuple(iter_physical_link_inspection(path))
    assert records[1]['work_counts'] is None and records[1]['inter_satellite'][0][2:] == [None] * 4
    sim.shutdown()
    for model in models.values():
        for method in ('compute_positions', 'compute_observation', 'select_inter_satellite_links',
                       'select_ground_station_accesses', 'plan_route_outcomes'):
            if hasattr(model, method):
                setattr(model, method, lambda *a, **kw: pytest.fail('Tool recomputed a Model'))
    physical_data = collect_physical_world_data(world, physical_frame=frame, physical_link_observation=facts)
    planned = collect_planner_data(tuple(evidence), physical_link_inspection_path=path)
    assert physical_data and planned
    write_physical_world_tool(world, tmp_path / 'physical', physical_frame=frame,
                              physical_link_observation=facts)
    write_planner_tool(tuple(evidence), tmp_path / 'planner', physical_link_inspection_path=path)
    assert (tmp_path / 'physical/index.html').is_file() and (tmp_path / 'planner/index.html').is_file()
    assert 'unavailable' in (tmp_path / 'planner/index.html').read_text()


def test_measures_and_work_are_not_generic_geometry_or_admission_rules():
    sim, models, ids, _ = assembly(metrics=True)
    frame = sim.get_physical_world().frame_at(EPOCH)
    facts = models['physical_link_model'].compute_observation(sim.get_physical_world(), frame,
        sim.get_planner().create_physical_link_request())
    # Internally representable statistics may differ from the actual candidate count.
    counts = physical.PhysicalLinkWorkCounts(0, 0, 0, 0, 0, 0, 0, 0, 0)
    facts = replace(facts, work_counts=counts)
    assert sim.get_planner().observe(frame, facts)
    references = []
    original = models['parameter_model'].plan_effective_parameters
    def parameters(timestamp, observations, memory):
        references.extend(observations)
        return original(timestamp, observations, memory)
    models['parameter_model'].plan_effective_parameters = parameters
    observe(sim, 5)
    by_type = {type(value.physical_candidate): value for value in references}
    assert by_type[physical.InterSatelliteLinkCandidate].physical_candidate is models['physical_link_model'].inter_satellite[0]
    assert by_type[physical.InterSatelliteLinkCandidate].physical_delay_us == -7
    assert by_type[physical.GroundStationAccessCandidate].physical_candidate is models['physical_link_model'].ground_station[0]
    sim.shutdown()


def test_defaults_report_the_metrics_their_algorithms_need():
    sim, models, ids, _ = assembly()
    world, link = sim.get_physical_world(), models['physical_link_model']
    state = planner.ConnectionSelectionState(EPOCH, timedelta(seconds=5), timedelta(seconds=5), {}, frozenset())
    with pytest.raises(ValueError, match='requires'):
        planner.RetainFirstInterSatelliteConnectionPlanningModel().select_inter_satellite_links(world, link.inter_satellite, state)
    with pytest.raises(ValueError, match='requires elevation_deg'):
        planner.CoverageFirstGroundStationAccessPlanningModel(default_ground_station_limit=1).select_ground_station_accesses(world, link.ground_station, state)
    with pytest.raises(ValueError, match='requires elevation_deg'):
        planner.HighestElevationZoneServicePlanningModel().assign_zone_service(world, link.zone_service, (), timestamp=EPOCH)
    default = planner.AccumulatedDeadbandParameterPlanningModel(simulator.DefaultSimulatorConfiguration().parameter_configuration)
    key = planner.OperationalConnectionId(planner.OperationalConnectionKind.INTER_SATELLITE, *ids[:2])
    with pytest.raises(ValueError, match='requires physical_delay_us'):
        default.plan_effective_parameters(EPOCH, (planner.ConnectionParameterObservation(key, None, link.inter_satellite[0]),), None)
    sim.shutdown()


@pytest.mark.parametrize('stage', ['configuration', 'claim', 'serve'])
def test_failures_before_producer_close_all_resources(tmp_path, monkeypatch, stage):
    import seedemu as seed
    if stage == 'configuration':
        definition, zones, models = assembly(create=False)
        events = resource_hooks(models)
        with pytest.raises(TypeError, match='configuration'):
            simulator.create_default_simulator(definition, configuration=object(), **models)
    else:
        sim, models, _, _ = assembly()
        events = resource_hooks(models)
        owner = ExperimentController(seed.Emulator(), sim, tmp_path / 'early', entry_path=Path(__file__), traffic_enabled=False)
        if stage == 'claim':
            from skyseed_v2.experiment import controller
            def fail(*a, **kw):
                raise RuntimeError('claim failed')
            monkeypatch.setattr(controller, 'claim_output', fail)
            with pytest.raises(RuntimeError, match='claim failed'):
                owner.generate()
        else:
            with pytest.raises(ValueError, match='duration'):
                owner.serve(delivery_duration=timedelta(0))
    assert set(events) == set(models)


def test_nested_cleanup_failures_preserve_original_and_report_all_roles():
    sim, models, _, _ = assembly()
    events = []
    for role, model in models.items():
        def close(*, wait=True, role=role):
            events.append(role)
            raise RuntimeError(role)
        model.shutdown = close
    with pytest.raises(RuntimeError) as caught:
        sim.shutdown()
    text = str(caught.value) + '\n'.join(caught.value.__notes__)
    assert set(events) == set(models)
    assert all(role in text for role in models)
    sim.shutdown()
    assert len(events) == 8


@pytest.mark.parametrize('role', ['isl', 'ground'])
@pytest.mark.parametrize('coverage,expected', [('unknown', True), ('short', False), ('pending', True), ('operational', True)])
def test_default_contact_preference_distinguishes_pending_operational_and_unknown(role, coverage, expected):
    from tests.unit.test_network_planner_semantic_core import _world, _isl_candidate, _ground_candidate
    world, satellites, stations = _world()
    now = EPOCH + timedelta(seconds=2)
    if role == 'isl':
        candidate = _isl_candidate(*satellites[:2])
        model = planner.RetainFirstInterSatelliteConnectionPlanningModel(
            planner.RetainFirstInterSatelliteConfiguration(minimum_service_duration=timedelta(seconds=5)))
        kind, endpoints = planner.OperationalConnectionKind.INTER_SATELLITE, (candidate.endpoint_a, candidate.endpoint_b)
        choose = lambda state: model.select_inter_satellite_links(world, (candidate,), state).selected_candidates
    else:
        candidate = _ground_candidate(stations[0], satellites[0])
        model = planner.CoverageFirstGroundStationAccessPlanningModel(default_ground_station_limit=1, minimum_service_duration=timedelta(seconds=5))
        kind, endpoints = planner.OperationalConnectionKind.GROUND_STATION_ACCESS, (candidate.ground_station_id, candidate.satellite_id)
        choose = lambda state: model.select_ground_station_accesses(world, (candidate,), state)
    key = planner.OperationalConnectionId(kind, *endpoints)
    candidate = replace(candidate, contact_window=None if coverage == 'unknown' else
        physical.PhysicalContactWindow(now, EPOCH + timedelta(seconds=10)))
    state = planner.ConnectionSelectionState(now, timedelta(seconds=1), timedelta(seconds=5),
        {key: EPOCH} if coverage in ('pending', 'operational') else {},
        frozenset((key,)) if coverage == 'operational' else frozenset())
    assert bool(choose(state)) is expected
    assert model.required_contact_duration(acquisition_duration=timedelta(seconds=5), observation_interval=timedelta(seconds=1)) == timedelta(seconds=10)
    world.shutdown()


def test_default_physical_limit_pruning_keeps_unknown_windows_and_reports_budget():
    from tests.unit.test_physical_link_facts import _physical_world_fixture, _model
    world, satellites, station = _physical_world_fixture()
    model = _model()
    frame = world.frame_at(EPOCH)
    pairs = ((satellites[0].node_id, satellites[2].node_id),)
    request = physical.PhysicalLinkRequest(inter_satellite_pairs=pairs,
        cross_plane_neighbor_count=1, ground_station_candidate_limit=1,
        inter_satellite_minimum_contact_duration=timedelta(seconds=185),
        ground_station_minimum_contact_duration=timedelta(seconds=65))
    limited = model.compute_observation(world, frame, request)
    assert limited.inter_satellite_candidates and limited.ground_station_access_candidates
    assert all(c.contact_window is None for c in (*limited.inter_satellite_candidates, *limited.ground_station_access_candidates))
    assert limited.ground_station_search_complete is False
    full = model.compute_observation(world, frame, replace(request, ground_station_candidate_limit=None))
    assert full.ground_station_search_complete is True
    assert len(full.ground_station_access_candidates) > len(limited.ground_station_access_candidates)
    model.shutdown()
    world.shutdown()


def test_missing_measure_tools_relationship_selection_executes_actual_javascript(tmp_path):
    import os
    import shutil
    import subprocess
    node = os.environ.get('SKYSEED_TEST_NODE') or shutil.which('node')
    if not node:
        pytest.skip('Node required for nonvisual interaction checks')
    test_basic_tools_preserve_missing_measures_and_never_recompute(tmp_path)
    # Execute the generated page's actual functions against its generated frame.
    # No browser/visual acceptance claim: this checks display text and click state.
    harness = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),root=process.argv[1];
const page=fs.readFileSync(root+'/planner/index.html','utf8');
const DATA=JSON.parse(fs.readFileSync(root+'/planner/planner-inspection.json','utf8'));
const window={};vm.runInNewContext(fs.readFileSync(root+'/planner/data/frame-001.js','utf8'),{window});
let frame=window.SKYSEED_PLANNER_FRAME_CHUNK,stage='isl',gsStep='allocation',focus={kind:'node',index:frame.candidates.inter_satellite[0][0]},relationFocus=null;
const nodes=DATA.spatial_index.nodes,zones=DATA.spatial_index.zones,node=i=>nodes[i],visibility={nodeVisible(){return true},shellVisible(){return true}};
let rendered=0,buttons=[];function render(){rendered++}function sameRelation(a,b){return !!a&&a.kind===b.kind&&a.a===b.a&&a.b===b.b}
const special={innerHTML:'',querySelectorAll(selector){if(selector==='[data-gs-step]')return[];if(selector.includes('-relation')){buttons=[{dataset:{islRelation:'0',gsRelation:'0',zoneRelation:'0'}}];return buttons}return[]}},focusDetail={innerHTML:''};
const extract=(a,b)=>page.slice(page.indexOf(a),page.indexOf(b,page.indexOf(a)));
eval(extract('function metric(','function stageRecord('));
eval(extract('function parameterFacts(','let relationPage='));
eval(extract('function relationButtonState(','function loadRoute('));
assert.equal(metric(null),'unavailable');assert.equal(metric(0),'0');
showSpecial();assert.match(special.innerHTML,/unavailable/);assert.doesNotMatch(special.innerHTML,/cross-plane Δnull|distance 0 m/);
buttons[0].onclick();assert.equal(rendered,1);assert.equal(relationFocus.kind,'physical ISL candidate');showFocus();assert.match(focusDetail.innerHTML,/unavailable/);
stage='gs';focus={kind:'node',index:frame.candidates.ground_station[0][0]};showSpecial();assert.match(special.innerHTML,/elevation unavailable/);buttons[0].onclick();assert.equal(relationFocus.rank,null);showFocus();assert.doesNotMatch(focusDetail.innerHTML,/null|NaN/);
stage='zone';focus={kind:'zone',id:zones[0].zone_id};showSpecial();assert.match(special.innerHTML,/distance unavailable/);buttons[0].onclick();assert.equal(relationFocus.kind,'physical Zone candidate');assert.equal(relationFocus.serving,true);
const physical=fs.readFileSync(root+'/physical/index.html','utf8');
const physicalWindow={};vm.runInNewContext(fs.readFileSync(root+'/physical/data/frame-000.js','utf8'),{window:physicalWindow});
const context=vm.createContext({assert,DATA:JSON.parse(fs.readFileSync(root+'/physical/physical-world.json','utf8')),frame:physicalWindow.SKYSEED_PHYSICAL_FRAME_CHUNK});
vm.runInContext(`
const nodes=DATA.spatial_index.nodes,zones=DATA.spatial_index.zones,node=i=>nodes[i],pos=()=>new Map();
let selected={kind:'node',index:frame.candidates.inter_satellite[0][0]},selectedRelation=null,hoveredRelation=null,relationPage=0,pageSize=25,buttons=[];
const visibility={focus(){},edgeVisible(){return true}},detail={dataset:{},innerHTML:''},elements={};
const relations={innerHTML:'',querySelectorAll(){buttons=[{dataset:{relation:'0'}}];return buttons}};
const document={getElementById(id){return elements[id]||(elements[id]={})}};
function relationFocus(){return null}function modelDescriptor(){return {}}function draw(){}
`,context);
for(const name of ['adjacency','selectedId','relationKey','metric','planeRelation','human','show']){
 const start=physical.indexOf('function '+name+'('),end=physical.indexOf('function ',start+9);
 vm.runInContext(physical.slice(start,end),context);
}
vm.runInContext(`
show();assert.match(relations.innerHTML,/distance unavailable/);assert.doesNotMatch(relations.innerHTML,/cross-plane Δnull|distance 0/);
buttons[0].onclick();assert.match(detail.innerHTML,/Selected exact relation/);assert.match(detail.innerHTML,/unavailable/);assert.doesNotMatch(detail.innerHTML,/cross-plane Δnull|0.000 μs/);
selected={kind:'node',index:frame.candidates.ground_station[0][0]};selectedRelation=null;show();buttons[0].onclick();assert.match(relations.innerHTML,/elevation unavailable/);assert.match(detail.innerHTML,/unavailable/);
`,context);
'''
    completed = subprocess.run([node, '-e', harness, str(tmp_path)], text=True, capture_output=True)
    assert completed.returncode == 0, completed.stderr
    # Every emitted inventory link resolves; no model execution is needed.
    data = json.loads((tmp_path / 'planner/inventory.json').read_text())
    assert all((tmp_path / 'planner' / item['path']).is_file() for item in data['files'])
    physical_data = json.loads((tmp_path / 'physical/physical-world.json').read_text())
    assert physical_data['temporal_inventory']['formal_frames'] == 1
    assert (tmp_path / 'physical/data/frame-000.js').is_file()


def test_membership_and_position_require_distinct_role_instances():
    definition, zones, models = assembly(create=False)
    position = models['position_model']
    owner = simulator.Simulator(definition)
    with pytest.raises(ValueError, match='distinct instance'):
        owner.create_physical_world(position_model=position, zone_layout_definition=zones,
            zone_membership_model=position)
    owner.shutdown()


def test_producer_preserves_computation_failure_and_all_cleanup_failures():
    sim, models, _, _ = assembly()
    events = []
    for role, model in models.items():
        def close(*, wait=True, role=role):
            events.append(role)
            raise RuntimeError(role)
        model.shutdown = close
    def compute(*args):
        raise ValueError('primary calculation failure')
    models['physical_link_model'].compute_observation = compute
    producer = Producer(sim.get_timeline(), sim.get_physical_world(), models['physical_link_model'], sim.get_planner())
    with pytest.raises(ValueError, match='primary calculation failure') as caught:
        producer.produce_next_realization()
    assert set(events) == set(models)
    assert all(role in '\n'.join(caught.value.__notes__) for role in models)
    producer.close()
    assert len(events) == 8
