"""Completed request publication, suffix state and independent presentation."""
from datetime import timedelta
import json
from pathlib import Path
from threading import Event, Thread

import pytest
from examples.Sky00_tiny_network import tiny_network as tiny
from skyseed_v2.experiment import ExperimentController
from skyseed_v2.experiment._control import _request
from skyseed_v2.experiment.output.history import _reading_completed_range
from skyseed_v2.simulator import planner
from tools import write_experiment_tools
from tools._history import read_sealed_physical_frames
from tools.planner import read_planner_history
from tests.integration.test_retained_workflow import await_state


class AlternatingRoutes:
    """Opaque author state changes routing even when physical inputs are unchanged."""
    def __init__(self, algorithm):
        self.algorithm, self.timestamps = algorithm, []
    def create_route_demands(self, *args, **kwargs):
        return self.algorithm.create_route_demands(*args, **kwargs)
    def plan_route_outcomes(self, world, connections, services, demands, previous, *, timestamp):
        self.timestamps.append(timestamp)
        result = self.algorithm.plan_route_outcomes(world, connections, services, demands, previous, timestamp=timestamp)
        if len(self.timestamps) % 2 == 0:
            return planner.RoutePlanningResult(tuple(planner.UnreachableRouteOutcome(
                outcome.demand, planner.RouteUnreachableReason.NO_ELIGIBLE_PATH)
                if isinstance(outcome, planner.ReachableRouteOutcome) else outcome
                for outcome in result.outcomes))
        return result


def configured(path, through, duration=20):
    network = tiny.build_emulator()
    simulation = tiny.create_simulator(network, duration=timedelta(seconds=duration))
    route = AlternatingRoutes(simulation.get_planner().get_route_model())
    simulation.get_planner()._route_model = route
    return simulation, route, ExperimentController(network, simulation, path,
        entry_path=Path(tiny.__file__), input_files=(tiny.ZONE_LAYOUT_FILE,), through=through,
        traffic_enabled=False, internet_map_enabled=False)


@pytest.mark.parametrize('through', ['physical-world', 'planner', 'compiler'])
def test_completed_range_is_browseable_before_exit_and_reuses_state(tmp_path, through, monkeypatch):
    simulation, route, controller = configured(tmp_path/'retained', through)
    def prohibited(*args, **kwargs):
        pytest.fail('partial cutoff executed a downstream stage')
    if through == 'physical-world':
        for name in ('prime', 'observe', 'flush', 'finish', 'create_physical_link_request',
                     '_physical_prediction_requirements', '_candidate_search_request',
                     '_expanded_candidate_request', 'get_frontiers'):
            monkeypatch.setattr(type(simulation.get_planner()), name, prohibited)
    if through != 'compiler':
        monkeypatch.setattr(controller, '_prepare_static', prohibited)
        from skyseed_v2.experiment import producer
        monkeypatch.setattr(producer, 'compile_bpf', prohibited)
        monkeypatch.setattr(producer, '_compile_bpf_observed', prohibited)
    publications, failures = [], []
    def present(output, publication):
        if through == 'compiler':
            # An independently active runtime has published its identity, but
            # neither its performance nor Runner handoff is a completed record.
            raw = output/'runs/active/performance'
            raw.mkdir(parents=True, exist_ok=True)
            (raw/'execution-observations.jsonl').write_text('{in-progress')
            (output/'runs/latest.json').write_text(json.dumps({'invocation_id':'active','status':'running'}))
        results = write_experiment_tools(output, physical_world=simulation.get_physical_world(), publication=publication)
        assert all(item['status'] in {'PASS','COMPLETE','COMPLETED','WARN'} for item in results.values()), results
        publications.append(publication)
        return results
    def serve():
        try:
            controller.serve(delivery_duration=timedelta(seconds=20), on_completed=present)
        except BaseException as error:
            failures.append(error)
    thread = Thread(target=serve); thread.start()
    output = tmp_path/'retained'
    try:
        await_state(lambda: failures or len(publications) == 1)
        assert not failures
        await_state(lambda: controller.status()['state'] == 'IDLE')
        assert thread.is_alive()
        assert (output/'visualizations/physical-world/index.html').is_file()
        assert publications[0]['history']['terminal'] is False
        assert publications[0]['session_terminated'] is False
        _request(output, 'computation', 'continue', seconds=20, batch_seconds=20)
        await_state(lambda: failures or len(publications) == 2)
        assert not failures
        assert publications[1]['history']['counts']['formal_frames'] == 5
        assert controller.status()['completed_end'].endswith('00:00:40+00:00')
        # An older publication still reads exactly its completed byte prefix.
        with _reading_completed_range(output, publications[0]):
            assert len(read_sealed_physical_frames(output/'results/physical-world/physical-frames.jsonl')) == 3
        assert len(read_sealed_physical_frames(output/'results/physical-world/physical-frames.jsonl')) == 5
        if through != 'physical-world':
            assert route.timestamps == [tiny.DEFAULT_START_TIME+timedelta(seconds=n) for n in (0,10,20,30,40)]
            assert simulation.get_planner().get_route_model() is route
            assert not controller.status()['producer']['planner_finished']
        else:
            assert route.timestamps == []
        assert not (output/'start.sh').exists() and not (output/'launch.sh').exists()
    finally:
        controller.cancel_computation(); thread.join()
    assert not failures
    status = json.loads((output/'results/experiment/generation-status.json').read_text())
    assert status['session_terminated'] is True
    if through != 'physical-world':
        finite, finite_route, finite_controller = configured(tmp_path/'finite', through, duration=40)
        finite_controller.generate()
        def transitions(root):
            return [t for plan in read_planner_history(root/'results/planner/planning.jsonl.gz') for t in plan.plan.transitions]
        assert transitions(output) == transitions(tmp_path/'finite')
        assert route.timestamps == finite_route.timestamps


def test_slow_and_failed_presentation_does_not_block_domain_progress(tmp_path):
    simulation, route, controller = configured(tmp_path/'output', 'planner')
    entered, release = Event(), Event()
    def presentation(output, publication):
        entered.set(); release.wait()
        raise RuntimeError('intentional presentation failure')
    thread = Thread(target=lambda: controller.serve(on_completed=presentation)); thread.start()
    try:
        assert entered.wait(20)
        controller.continue_calculation(additional_duration=timedelta(seconds=20))
        await_state(lambda: controller.status()['state'] == 'IDLE' and len(route.timestamps) == 5)
        assert controller.status()['presentation'] == 'BUILDING'
        assert controller.status()['completed_end'].endswith('00:00:40+00:00')
    finally:
        release.set(); controller.cancel_computation(); thread.join()
    assert controller.status()['presentation'] == 'DEGRADED'
    assert json.loads((tmp_path/'output/results/experiment/generation-status.json').read_text())['status'] == 'MATERIALS_READY'
