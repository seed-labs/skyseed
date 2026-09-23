"""Real control/owner lifecycle with scoped offline resource failure injection."""
from threading import Event, Thread
import json
import pytest
from skyseed.experiment import execution
from skyseed.experiment.interaction import InteractionClient
from tests.integration.test_experiment_workflow import configured
from tests.integration.test_retained_workflow import await_state


@pytest.mark.parametrize('fault,command', [(None, 'cancel-runtime'), ('runner', 'cancel-runtime'),
    ('traffic', 'cancel-runtime'), ('both', 'cancel-runtime'), ('runner', 'reset'), ('traffic', 'shutdown')])
def test_runtime_results_wait_for_real_owner_drain(tmp_path, monkeypatch, fault, command):
    _, workflow = configured(tmp_path / 'runtime')
    output = workflow.generate()
    started, cancelled, cleaning, release = (Event() for _ in range(4))
    errors, order = [], []
    class Runner:
        def __init__(self, **kw):self.state = 'new'
        def _bind_execution_input(self, **kw):pass
        def _enable_event_history(self, path):pass
        def _enable_traffic_telemetry(self, path):pass
        def _record(self, *args, **kw):pass
        def prepare(self):self.state = 'prepared'
        def run(self):started.set(); assert cancelled.wait(20)
        def cancel_runtime(self):cancelled.set()
        def status(self):return {'lifecycle_state': self.state, 'accepts_batch': False}
        def inspection_snapshot(self):return {'runner': self.status(), 'events': []}
        def cleanup(self):
            cleaning.set(); assert release.wait(20); order.append('runner')
            if fault in {'runner', 'both'}:
                self.state = 'teardown-failed'; raise OSError('Runner drain failed')
            self.state = 'cleaned'
    class Traffic:
        def __init__(self, **kw):pass
        def start(self, runner):pass
        def snapshot(self):return {'running': True}
        def stop(self):
            order.append('traffic')
            if fault in {'traffic', 'both'}:raise OSError('Traffic drain failed')
    monkeypatch.setattr(execution, 'Runner', Runner)
    monkeypatch.setattr('tools.generation.traffic.probe_execution.TrafficExecutor', Traffic)
    monkeypatch.setattr(execution, '_record_container_checkpoint', lambda *a: None)
    def run():
        try:execution._run_generated_bpf_experiment(output)
        except BaseException as error:errors.append(error)
    thread = Thread(target=run); thread.start()
    client = InteractionClient.from_output(output)
    try:
        assert started.wait(20)
        await_state(lambda: client.state('runtime').get('invocation_id') is not None)
        if command == 'shutdown':receipt = client.shutdown(request_id='shutdown')['runtime']
        else:receipt = client.submit('runtime', command)
        assert cleaning.wait(20)
        assert client.result(receipt)['status'] not in {'SUCCEEDED', 'FAILED'}
        assert client.state('runtime')['live']
    finally:
        release.set(); cancelled.set(); thread.join()
    state = client.state('runtime');result = client.wait(receipt, timeout=10)
    assert not state['live'] and order == ['traffic', 'runner']
    assert result['status'] == ('SUCCEEDED' if fault is None else 'FAILED'), result
    assert result['result']['state']['outcome'] == state['outcome']
    assert state['outcome']['cleanup']['status'] == ('failed' if fault in {'runner', 'both'} else 'completed')
    assert state['outcome']['traffic']['status'] == ('failed' if fault in {'traffic', 'both'} else 'completed')
    if fault:
        assert len(errors) == 1
        assert state['error']['message'] == str(errors[0])
        if fault == 'both':
            assert str(errors[0]) == 'Traffic drain failed'
            assert any('Runner drain failed' in n for n in state['outcome']['primary_error']['notes'])
        handoff = next((output/'runs').glob('*/runner/runner-invocation.json'))
        assert json.loads(handoff.read_text())['runtime_outcome'] == state['outcome']
    else:assert not errors
    # Stopping the separately owned store certifies only service closure; failure
    # records remain on disk and it does not claim deployment/kernel repair.
    from skyseed.experiment.interaction_service import stop_interaction, start_interaction
    stop_interaction(output)
    client.close()
    restarted = start_interaction(output)
    assert restarted.result(receipt)['status'] == result['status']
    assert restarted.state('runtime')['outcome'] == state['outcome']
    restarted.close()


@pytest.mark.parametrize('fault', [None, 'calculation', 'drain', 'both'])
@pytest.mark.parametrize('command', ['cancel-computation', 'shutdown'])
def test_calculation_cancellation_preserves_failure_and_waits_for_owned_work(tmp_path, monkeypatch, fault, command):
    from skyseed.experiment.producer import Producer
    simulator, workflow = configured(tmp_path/'calculation', route_workers=1)
    entered, release_calculation, draining, release_drain, presenting, release_presentation = (Event() for _ in range(6))
    errors = []
    original_compute = simulator.get_physical_link_model().compute_observation
    original_close = Producer.close
    def compute(self, *args, **kw):
        entered.set(); assert release_calculation.wait(20)
        if fault in {'calculation', 'both'}:raise ValueError('calculation failed')
        return original_compute(*args, **kw)
    def close(self):
        original_close(self)
        draining.set(); assert release_drain.wait(20)
        if fault in {'drain', 'both'}:raise OSError('calculation drain failed')
    def present(*args):presenting.set(); assert release_presentation.wait(20)
    monkeypatch.setattr(type(simulator.get_physical_link_model()), 'compute_observation', compute)
    monkeypatch.setattr(Producer, 'close', close)
    def run():
        try:workflow.serve(on_completed=present)
        except BaseException as error:errors.append(error)
    thread = Thread(target=run); thread.start()
    try:
        assert entered.wait(20)
        client = workflow.interaction
        await_state(lambda: client.state('computation')['available'])
        if fault is None:
            release_calculation.set(); assert presenting.wait(20)
        receipt = client.submit('computation', command)
        await_state(lambda: workflow._stop.is_set())
        release_calculation.set(); assert draining.wait(20)
        # A later read request fences a full consumer turn while drain is held.
        assert client.wait(client.submit('computation', 'status'), timeout=10)['status'] == 'SUCCEEDED'
        assert client.result(receipt)['status'] not in {'SUCCEEDED', 'FAILED'}
        release_drain.set()
        if fault is None:
            await_state(lambda: workflow.status()['state'] == 'CLOSED')
            assert client.result(receipt)['status'] not in {'SUCCEEDED', 'FAILED'}
            assert not workflow._serve_finished
    finally:
        release_calculation.set(); release_drain.set(); release_presentation.set()
        workflow._cancel_computation(); thread.join()
    result = client.wait(receipt, timeout=10);state = client.state('computation')
    assert not state['live']
    assert result['status'] == ('SUCCEEDED' if fault is None else 'FAILED'), result
    assert result['result']['state']['outcome'] == state['outcome']
    if fault:
        assert errors and state['state'] == 'FAILED'
        assert state['outcome']['primary_error']['message'] == str(errors[0])
        if fault == 'both':assert any('calculation drain failed' in n for n in state['outcome']['primary_error']['notes'])
    else:assert not errors


@pytest.mark.parametrize('failed', [False, True])
def test_finite_calculation_cancel_reports_its_actual_drain(tmp_path, monkeypatch, failed):
    from skyseed.experiment.producer import Producer
    simulator, workflow = configured(tmp_path/'finite', route_workers=1)
    entered, release = Event(), Event()
    original = type(simulator.get_physical_link_model()).compute_observation
    close = Producer.close
    errors = []
    def observe(self, *args, **kw):
        entered.set(); assert release.wait(20)
        return original(self, *args, **kw)
    def drain(self):
        close(self)
        if failed:raise OSError('finite drain failed')
    monkeypatch.setattr(type(simulator.get_physical_link_model()), 'compute_observation', observe)
    monkeypatch.setattr(Producer, 'close', drain)
    def run():
        try:workflow.generate()
        except BaseException as error:errors.append(error)
    thread = Thread(target=run);thread.start()
    try:
        assert entered.wait(20)
        client = workflow.interaction
        await_state(lambda: client.state('computation')['available'])
        receipt = client.submit('computation', 'cancel-computation')
        await_state(lambda: workflow._stop.is_set())
        assert client.result(receipt)['status'] == 'RUNNING'
    finally:
        release.set();thread.join()
    assert client.wait(receipt, timeout=10)['status'] == ('FAILED' if failed else 'SUCCEEDED')
    assert not client.state('computation')['live']
    assert bool(errors) is failed


@pytest.mark.parametrize('retained', [False, True])
def test_pre_producer_failure_closes_models_before_owner_publication(tmp_path, monkeypatch, retained):
    simulator, workflow = configured(tmp_path/'startup', route_workers=1)
    preparing, fail, draining, release = (Event() for _ in range(4))
    errors = []
    original = type(simulator).shutdown
    def prepare():
        preparing.set(); assert fail.wait(20)
        raise ValueError('startup failed')
    def drain(self, *args, **kw):
        original(self, *args, **kw)
        draining.set(); assert release.wait(20)
        raise OSError('Models drain failed')
    monkeypatch.setattr(workflow, '_prepare_static', prepare)
    monkeypatch.setattr(type(simulator), 'shutdown', drain)
    def run():
        try:workflow.serve() if retained else workflow.generate()
        except BaseException as error:errors.append(error)
    thread = Thread(target=run);thread.start()
    try:
        assert preparing.wait(20)
        client = workflow.interaction
        await_state(lambda: client.state('computation')['available'])
        receipt = client.submit('computation', 'cancel-computation')
        await_state(lambda: workflow._stop.is_set())
        fail.set();assert draining.wait(20)
        assert client.state('computation')['live']
        assert client.result(receipt)['status'] == 'RUNNING'
    finally:
        fail.set();release.set();thread.join()
    result = client.wait(receipt, timeout=10)
    assert result['status'] == 'FAILED' and len(errors) == 1
    state = client.state('computation')
    assert not state['live']
    assert state['state'] == 'FAILED'
    assert state['outcome']['primary_error']['message'] == 'startup failed'
    assert any('Models drain failed' in n for n in state['outcome']['primary_error']['notes'])


def test_store_outage_does_not_skip_local_runtime_drain(tmp_path, monkeypatch):
    from skyseed.experiment.interaction import InteractionUnavailable, _OwnerPublisher
    from tests.integration.test_initial_readiness import runtime
    _, workflow = configured(tmp_path/'outage')
    output = workflow.generate()
    order, runners = runtime(monkeypatch, block=True)
    outage = Event();errors = []
    original_cleanup, original_call = execution.Runner.cleanup, InteractionClient._call
    def cleanup(self):
        outage.set()
        original_cleanup(self)
    def call(self, *args, **kwargs):
        if outage.is_set():raise InteractionUnavailable('store unavailable during local drain')
        return original_call(self, *args, **kwargs)
    monkeypatch.setattr(execution.Runner, 'cleanup', cleanup)
    monkeypatch.setattr(InteractionClient, '_call', call)
    def run():
        try:execution._run_generated_bpf_experiment(output)
        except BaseException as error:errors.append(error)
    thread = Thread(target=run);thread.start()
    client = InteractionClient.from_output(output)
    try:
        await_state(lambda: (0, 'run') in order)
        await_state(lambda: client.state('runtime').get('invocation_id') is not None)
        receipt = client.submit('runtime', 'cancel-runtime')
        thread.join()
        assert outage.is_set()
    finally:
        for runner in runners:runner.cancel_runtime()
        thread.join();outage.clear()
    try:
        assert order[-2:] == [(0, 'traffic-stop'), (0, 'cleanup')]
        assert len(errors) == 1 and isinstance(errors[0], InteractionUnavailable)
        assert client.state('runtime')['live']  # No closure was acknowledged by the store.
        assert client.result(receipt)['status'] == 'RUNNING'
        handoff = json.loads(next((output/'runs').glob('*/runner/runner-invocation.json')).read_text())
        assert handoff['runtime_outcome']['cleanup']['status'] == 'completed'
    finally:
        # Test-only service teardown after joining the actual owner. Production
        # does not synthesize closure/results when publication is unavailable.
        state = client.state('runtime')
        _OwnerPublisher(client, 'runtime').publish(
            {**state, 'error': 'fixture observed owner exit after store outage'}, live=False)
        client.close()


def test_failed_calculation_cancel_waits_for_presentation_then_reports_failure(tmp_path, monkeypatch):
    from datetime import timedelta
    simulator, workflow = configured(tmp_path/'failed-with-presentation', route_workers=1)
    presenting, release, fail = Event(), Event(), Event()
    original = type(simulator.get_physical_link_model()).compute_observation
    errors = []
    def observe(self, *args, **kwargs):
        if fail.is_set():raise ValueError('continued calculation failed')
        return original(self, *args, **kwargs)
    def present(*args):presenting.set(); assert release.wait(20)
    monkeypatch.setattr(type(simulator.get_physical_link_model()), 'compute_observation', observe)
    def run():
        try:workflow.serve(on_completed=present)
        except BaseException as error:errors.append(error)
    thread = Thread(target=run);thread.start()
    try:
        assert presenting.wait(20)
        client = workflow.interaction
        await_state(lambda: client.state('computation').get('state') == 'IDLE')
        fail.set()
        workflow.continue_calculation(additional_duration=timedelta(seconds=30))
        await_state(lambda: client.state('computation').get('state') == 'FAILED')
        receipt = client.submit('computation', 'cancel-computation')
        # This later receipt settles after the cancellation's completion check.
        assert client.wait(client.submit('computation', 'status'), timeout=10)['status'] == 'SUCCEEDED'
        assert client.result(receipt)['status'] == 'RUNNING'
        assert not workflow._serve_finished
    finally:
        release.set();workflow._cancel_computation();thread.join()
    assert client.wait(receipt, timeout=10)['status'] == 'FAILED'
    assert len(errors) == 1 and str(errors[0]) == 'continued calculation failed'
