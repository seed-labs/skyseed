"""Optional measurements must not change valid planning or hide real failures."""
from asyncio import CancelledError
from collections.abc import Mapping
from datetime import timedelta
from types import MappingProxyType, SimpleNamespace

import pytest

from skyseed.experiment.output.observation import _ExecutionObserver
from skyseed.simulator.planner import (
    GroundStationRouteDemandTarget, Planner,
    PlannerConfiguration, RouteDemand,
)
from tests.unit.test_network_planner_semantic_core import _world, _isl_candidate, _ground_candidate
from tests.unit.test_planner_replaceability import (
    SelectIsl, SelectGround, UnservedZone, NoEligibleRoute, DirectionalParameters, observe,
)

HOOKS = ('selection_statistics', 'allocation_statistics',
         'route_work_statistics', 'route_resource_statistics')


class BrokenMapping(Mapping):
    def __iter__(self):
        return iter(('unavailable',))

    def __len__(self):
        return 1

    def __getitem__(self, key):
        raise ValueError('statistics consumption failed')


def planner_case(*, recording, failing=None, mode='invoke'):
    calls = []

    class Statistics:
        failure = None

        def __getattribute__(self, name):
            if name in HOOKS:
                calls.append(('get', name))
                assert recording, 'statistics retrieved with recording disabled'
                if name == failing and mode == 'get':
                    raise ValueError('statistics retrieval failed')
                if name == failing and mode == 'noncallable':
                    return 5

                def read():
                    calls.append(('call', name))
                    if name == failing:
                        if mode == 'invoke':
                            raise ValueError('statistics invocation failed')
                        if mode == 'consume':
                            return BrokenMapping()
                        if mode == 'not-mapping':
                            return None
                        if mode == 'non-numeric':
                            return {'unavailable': object()}
                        if mode == 'non-finite':
                            return {'unavailable': float('nan')}
                        if mode == 'invalid-key':
                            return {1: 42}
                        if mode == 'cancel':
                            raise CancelledError('cancel statistics')
                        if mode == 'interrupt':
                            raise KeyboardInterrupt('interrupt statistics')
                        if mode == 'collision':
                            return {'feasible_candidates': 999, 'route_demands': 999,
                                    'route_outcome_entries': 999, name: 42}
                    return MappingProxyType({name: 42})
                return read
            return super().__getattribute__(name)

    class Isl(Statistics, SelectIsl):
        def select_inter_satellite_links(self, *args):
            if self.failure:
                raise self.failure
            return super().select_inter_satellite_links(*args)

    class Ground(Statistics, SelectGround):
        def select_ground_station_accesses(self, *args):
            if self.failure:
                raise self.failure
            return super().select_ground_station_accesses(*args)

    class Route(Statistics, NoEligibleRoute):
        def plan_route_outcomes(self, *args, timestamp):
            if self.failure:
                raise self.failure
            return super().plan_route_outcomes(*args, timestamp=timestamp)

    world, satellites, stations = _world()
    pair = tuple(s.node_id for s in satellites)
    models = Isl(pair), Ground(), Route()
    observer = _ExecutionObserver() if recording else None
    parameters = DirectionalParameters()
    planner = Planner(physical_world=world, observation_interval=timedelta(seconds=5),
        inter_satellite_model=models[0], ground_station_model=models[1],
        zone_service_model=UnservedZone(), parameter_model=parameters, route_model=models[2],
        configuration=PlannerConfiguration(1, inter_satellite_acquisition_duration=timedelta(0), ground_station_acquisition_duration=timedelta(0)),
        route_demands=(RouteDemand(pair[0], GroundStationRouteDemandTarget(stations[0].node_id)),),
        execution_observer=observer)
    candidates = _isl_candidate(*satellites), _ground_candidate(stations[0], satellites[1])
    return world, planner, candidates, observer, models, calls


def progress(case):
    world, planner, candidates, *_ = case
    results = []
    try:
        for seconds in (0, 5, 10):
            if seconds == 10:
                planner.set_route_demands(())  # Exercise a subsequent real routing change.
            plan, = observe(world, planner, candidates, seconds)
            results.append((plan, planner.get_state(), planner.get_frontiers()))
            planner.release_published_through(plan.plan_id)
        return results
    finally:
        planner.shutdown()


def test_recording_disabled_does_not_retrieve_any_statistics():
    case = planner_case(recording=False)
    progress(case)
    assert case[-1] == []


def test_failed_degradation_diagnostic_does_not_change_planning(monkeypatch):
    from skyseed.simulator.planner import planner as implementation

    class BrokenStderr:
        def write(self, message):
            raise OSError('diagnostic sink failed')

    monkeypatch.setattr(implementation, 'sys', SimpleNamespace(stderr=BrokenStderr()))
    baseline = progress(planner_case(recording=False))
    assert progress(planner_case(recording=True, failing=HOOKS[0])) == baseline


@pytest.mark.parametrize('hook', HOOKS)
@pytest.mark.parametrize('mode', ['get', 'invoke', 'consume', 'not-mapping', 'noncallable',
                                 'non-numeric', 'non-finite', 'invalid-key'])
def test_statistics_degrade_without_changing_plans_state_or_progression(hook, mode, capsys):
    baseline = progress(planner_case(recording=False))
    case = planner_case(recording=True, failing=hook, mode=mode)
    assert progress(case) == baseline
    diagnostic = capsys.readouterr().err
    assert 'performance recording degraded' in diagnostic and hook in diagnostic
    observer, calls = case[3], case[-1]
    assert observer._get_recording_failure() is None
    records = observer.snapshot()['records']
    selection = next(r for r in records if r['stage'] == 'isl-candidate-selection')
    assert selection['work']['selected_candidates'] == 1
    ground = next(r for r in records if r['stage'] == 'ground-station-selection-and-ranking')
    assert ground['work']['proposed_accesses'] == 1
    routes = [r for r in records if r['stage'] == 'route-planning']
    assert [r['work']['route_recomputed'] for r in routes] == [1, 0, 1]
    assert routes[0]['work']['route_demands'] == routes[0]['resources']['route_outcome_entries'] == 1
    # A failed capability has no fake value; independent capabilities remain usable.
    for name, row, section in ((HOOKS[0], selection, 'work'), (HOOKS[1], ground, 'work'),
                               (HOOKS[2], routes[0], 'work'), (HOOKS[3], routes[0], 'resources')):
        if name == hook:
            assert name not in row[section] and 'unavailable' not in row[section]
        else:
            assert row[section][name] == 42
    assert calls.count(('get', HOOKS[2])) == 2
    assert calls.count(('get', HOOKS[3])) == 3
    assert HOOKS[2] not in routes[1]['work']


@pytest.mark.parametrize('hook', HOOKS)
def test_planner_owned_counters_survive_optional_key_collisions(hook):
    case = planner_case(recording=True, failing=hook, mode='collision')
    progress(case)
    records = case[3].snapshot()['records']
    stage, section, key = {
        HOOKS[0]: ('isl-candidate-selection', 'work', 'feasible_candidates'),
        HOOKS[1]: ('ground-station-selection-and-ranking', 'work', 'feasible_candidates'),
        HOOKS[2]: ('route-planning', 'work', 'route_demands'),
        HOOKS[3]: ('route-planning', 'resources', 'route_outcome_entries'),
    }[hook]
    assert next(r for r in records if r['stage'] == stage)[section][key] == 1


@pytest.mark.parametrize('hook', HOOKS)
@pytest.mark.parametrize('mode, error', [('cancel', CancelledError), ('interrupt', KeyboardInterrupt)])
def test_statistics_cancellation_is_not_swallowed(hook, mode, error):
    world, planner, candidates, *_ = planner_case(recording=True, failing=hook, mode=mode)
    try:
        with pytest.raises(error):
            observe(world, planner, candidates)
    finally:
        planner.shutdown()


@pytest.mark.parametrize('role', [0, 1, 2])
def test_primary_model_errors_still_propagate_and_rollback(role):
    world, planner, candidates, _, models, _ = planner_case(
        recording=True, failing=HOOKS[0], mode='invoke')
    try:
        observe(world, planner, candidates)
        planner.set_route_demands(())
        state, frontiers = planner.get_state(), planner.get_frontiers()
        last = planner.get_last_transition()
        count = planner.get_route_recomputation_count()
        error = ValueError('actual Model computation failed')
        models[role].failure = error
        with pytest.raises(ValueError) as caught:
            observe(world, planner, candidates, 5)
        assert caught.value is error
        assert planner.get_state() is state and planner.get_frontiers() == frontiers
        assert planner.get_last_transition() is last
        assert planner.get_route_recomputation_count() == count
        models[role].failure = None
        plan, = observe(world, planner, candidates, 5)
        assert plan.end_timestamp == state.timestamp + timedelta(seconds=5)
        assert planner.get_state().route_outcomes == ()
    finally:
        planner.shutdown()
