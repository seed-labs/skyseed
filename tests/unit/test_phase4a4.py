"""Phase 4a-4 owner handoffs, optional facilities and fake lifecycle boundaries."""
from datetime import timedelta
import json
from pathlib import Path
from subprocess import CompletedProcess, run
import sys
from threading import Event, Thread

import pytest

from tests.integration.test_experiment_workflow import configured
from tests.integration.test_retained_workflow import await_state
from skyseed.experiment import Producer
from skyseed.experiment.output.occupancy import claim_output
from skyseed.experiment.output.observation import _ExecutionObserver
from skyseed.experiment import execution
from skyseed.deployment import deployer


def start_generation(controller):
    failures = []
    def generate():
        try:
            controller.generate()
        except BaseException as error:
            failures.append(error)
    thread = Thread(target=generate)
    thread.start()
    return thread, failures


def test_runner_project_label_uses_one_normalized_value(monkeypatch):
    from skyseed.backend.runner.lifecycle import Runner
    from skyseed.backend.runner import deployment
    from skyseed.backend.execution_materials.actions import _PhysicalWorldProjection
    commands = []
    def execute(command):
        commands.append(command)
        return CompletedProcess(command, 0, '', '')
    monkeypatch.setattr(deployment, '_execute', execute)
    runner = Runner(compose_project_name='  canonical-project \t', runtime_artifact=Path('/unused'))
    with pytest.raises(RuntimeError, match='no containers'):
        runner._deployment.discover(_PhysicalWorldProjection((__import__("skyseed.shared", fromlist=["NodeId"]).NodeId(65001, "satellite", "sat-0"),), ()))
    assert commands[0][-1] == 'label=com.docker.compose.project=canonical-project'


def test_compute_claim_blocks_replace_but_allows_same_experiment_execution(tmp_path):
    marker = tmp_path / 'keep'; marker.write_text('unchanged')
    with claim_output(tmp_path, replace=True, computation=True) as allow_execution:
        with pytest.raises(RuntimeError, match='actively owned'):
            with claim_output(tmp_path, replace=True, computation=True):
                marker.unlink()
        allow_execution()
        with claim_output(tmp_path):
            assert marker.read_text() == 'unchanged'
        with pytest.raises(RuntimeError, match='actively owned'):
            with claim_output(tmp_path, replace=True):
                pass
    with claim_output(tmp_path, replace=True):
        assert marker.read_text() == 'unchanged'


def test_static_preparation_precedes_production_and_sealed_publication(tmp_path, monkeypatch):
    from skyseed.deployment import Deployer
    from skyseed.backend.execution_materials.sealing import _RunnerArtifactWriter
    from skyseed.experiment.controller import ExperimentController
    order = []
    for owner, name in ((Deployer, 'prepare'), (Producer, 'produce'), (_RunnerArtifactWriter, 'finalize'),
                        (ExperimentController, '_publish_batch')):
        original = getattr(owner, name)
        def record(*args, _name=name, _original=original, **kwargs):
            order.append(_name)
            return _original(*args, **kwargs)
        monkeypatch.setattr(owner, name, record)
    sim, controller = configured(tmp_path / 'output', traffic_enabled=False)
    controller.generate()
    assert order == ['prepare', 'produce', 'finalize', '_publish_batch']
    assert controller.status()['producer']['packaging_peak'] <= 2
    assert controller.status()['published_end'] == sim.get_timeline().end_time.isoformat()


def test_disabled_traffic_execution_imports_no_tools_or_computation(tmp_path):
    _, controller = configured(tmp_path / 'output', traffic_enabled=False)
    output = controller.generate()
    assert not (output / 'visualizations/traffic').exists()
    assert 'traffic' not in json.loads((output / 'results/compiler/materials.jsonl').read_text())
    script = '''
import importlib.abc, sys
from pathlib import Path
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname == 'tools' or fullname.startswith('tools.') or fullname in {
            'skyseed.experiment.controller', 'skyseed.experiment.producer'}:
            raise AssertionError('forbidden execution dependency: ' + fullname)
sys.meta_path.insert(0, Block())
from skyseed.experiment import execution
class Runner:
    def __init__(self, **kwargs): pass
    def _bind_execution_input(self, **kwargs): pass
    def _enable_event_history(self, path): pass
    def _enable_traffic_telemetry(self, path): pass
    def prepare(self): pass
    def run(self): pass
    def cleanup(self): pass
    def status(self): return {'accepts_batch': False}
    def inspection_snapshot(self): return {'runner': {}, 'events': ()}
execution.Runner = Runner
execution._record_container_checkpoint = lambda *a: None
execution._run_generated_bpf_experiment(Path(sys.argv[1]))
assert not any(m == 'tools' or m.startswith('tools.') for m in sys.modules)
'''
    result = run([sys.executable, '-B', '-c', script, str(output)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_optional_recording_failure_does_not_revoke_materials(tmp_path, monkeypatch):
    def fail(*a, **kw):
        raise OSError('measurement disk unavailable')
    monkeypatch.setattr(_ExecutionObserver, 'write_jsonl', fail)
    _, controller = configured(tmp_path / 'output', traffic_enabled=False)
    output = controller.generate()
    assert json.loads((output / 'results/experiment/generation-status.json').read_text())['status'] == 'MATERIALS_READY'
    assert (output / 'results/compiler/runner-artifact/manifest.json').is_file()


def test_reset_records_reference_generation_without_reading_it(tmp_path, monkeypatch):
    output = tmp_path / 'output'
    generation = output / 'results/experiment/generation-performance.jsonl'
    generation.parent.mkdir(parents=True)
    generation.write_text('generation must not be parsed or copied')
    observer = _ExecutionObserver()
    observer.record(component='runner', stage='total', phase='one-shot', duration_seconds=1)
    destination = output / 'runs/reset/performance/execution-observations.jsonl'
    original = Path.read_text
    def read(path, *a, **kw):
        if path == generation:
            raise AssertionError('generation history was read on reset')
        return original(path, *a, **kw)
    monkeypatch.setattr(Path, 'read_text', read)
    execution._write_runtime_execution_observations(output, destination, observer)
    records = [json.loads(line) for line in destination.read_text().splitlines()]
    assert len(records) == 2
    assert (destination.parent / records[0]['metadata']['generation_reference']).resolve() == generation


def test_neutral_receiver_failure_leaves_plans_unchanged():
    from examples.Sky00_tiny_network import tiny_network as tiny
    from tests._support import drain_simulator
    class FailingReceiver:
        def record(self, **facts):
            raise OSError('optional receiver failed')
    sim = tiny.create_simulator(tiny.build_emulator())
    sim._set_execution_observer(FailingReceiver())
    _, results, state = drain_simulator(sim)
    assert len(results) == 1 and state['formal_frame_count'] == 4


def test_client_cancellation_drains_accepted_work_without_termination(monkeypatch):
    calls = []
    class Client:
        returncode = 0
        def communicate(self):
            calls.append('wait')
            if len(calls) == 1:
                raise KeyboardInterrupt()
            return '', ''
    monkeypatch.setattr(deployer, 'Popen', lambda *a, **kw: Client())
    with pytest.raises(KeyboardInterrupt):
        deployer._mutation(['docker', 'compose', 'up'])
    assert calls == ['wait', 'wait']


def test_lost_client_is_not_reported_as_daemon_completion(monkeypatch):
    class Client:
        returncode = -9
        def communicate(self): return '', ''
    monkeypatch.setattr(deployer, 'Popen', lambda *a, **kw: Client())
    with pytest.raises(deployer._ClientLost, match='unresolved'):
        deployer._mutation(['docker', 'compose', 'up'])


def test_history_import_does_not_load_orchestration():
    script = '''
import sys
import skyseed.experiment.output.history
assert 'skyseed.experiment.controller' not in sys.modules
assert 'skyseed.experiment.producer' not in sys.modules
assert 'skyseed.experiment.execution' not in sys.modules
'''
    result = run([sys.executable, '-B', '-c', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_independent_status_reads_owner_publication_without_constructing_runtime(tmp_path):
    from skyseed.experiment._control import _serve_control
    from skyseed.experiment import interaction_service
    client = interaction_service.start_interaction(tmp_path)
    with _serve_control(tmp_path, 'runtime', lambda request: {'runner': {'state': 'paused'}}):
        state = client.state('runtime')
        assert state['available'] and state['runner'] == {'state': 'paused'}
        assert state['version'] and state['session_id']
    assert not client.state('runtime')['available']


def test_computed_plan_is_not_published_until_packaging_seals(tmp_path, monkeypatch):
    from skyseed.backend.execution_materials.sealing import _RunnerArtifactWriter
    entered, release = Event(), Event()
    original = _RunnerArtifactWriter.finalize
    def seal(writer):
        entered.set()
        assert release.wait(10)
        return original(writer)
    monkeypatch.setattr(_RunnerArtifactWriter, 'finalize', seal)
    _, controller = configured(tmp_path / 'output', traffic_enabled=False)
    thread, failures = start_generation(controller)
    try:
        assert entered.wait(10)
        status = controller.status()
        assert status['producer']['formal_frame_count'] > 0
        assert status['producer']['plan_count'] > 0
        assert status['producer']['sealed_material_end'] is None
        assert status['published_batches'] == 0
        assert not (tmp_path / 'output/results/compiler/materials.jsonl').exists()
    finally:
        release.set()
        thread.join(10)
    assert not thread.is_alive() and not failures
    assert controller.status()['published_batches'] == 1


def test_computation_cancel_drains_accepted_packaging_without_publishing_partial_batch(tmp_path, monkeypatch):
    from skyseed.backend.execution_materials.sealing import _RunnerArtifactWriter
    entered, release, completed = Event(), Event(), Event()
    original = _RunnerArtifactWriter.append
    def append(writer, result):
        entered.set()
        assert release.wait(10)
        original(writer, result)
        completed.set()
    monkeypatch.setattr(_RunnerArtifactWriter, 'append', append)
    _, controller = configured(tmp_path / 'output', traffic_enabled=False)
    thread, failures = start_generation(controller)
    try:
        assert entered.wait(10)
        client = controller.interaction
        await_state(lambda: client.state('computation')['available'])
        client.close()
        controller.cancel_computation()
        await_state(lambda: controller._stop.is_set())
        assert thread.is_alive() and not completed.is_set()
    finally:
        release.set()
        thread.join(10)
    assert completed.is_set() and not thread.is_alive() and not failures
    assert controller.status()['published_batches'] == 0
    assert controller.status()['producer']['packaging_peak'] <= 2
    assert not (tmp_path / 'output/results/compiler/materials.jsonl').exists()
    assert json.loads((tmp_path / 'output/results/experiment/generation-status.json').read_text())['status'] == 'CANCELLED'


def test_runtime_completion_leaves_retained_computation_available(tmp_path, monkeypatch):
    returned = Event()
    monkeypatch.setattr(execution, 'run_generated_experiment', lambda *a, **kw: returned.set())
    _, controller = configured(tmp_path / 'output', traffic_enabled=False)
    failures = []
    def serve():
        try:
            controller.serve(delivery_duration=timedelta(seconds=30), run=True)
        except BaseException as error:
            failures.append(error)
    thread = Thread(target=serve); thread.start()
    try:
        assert returned.wait(10)
        await_state(lambda: failures or controller.status()['state'] == 'IDLE')
        assert not failures and thread.is_alive()
        previous = controller.status()['producer']['formal_frame_count']
        controller.continue_calculation(additional_duration=timedelta(seconds=30))
        await_state(lambda: failures or controller.status()['state'] == 'IDLE'
            and controller.status()['producer']['formal_frame_count'] > previous)
        assert not failures and thread.is_alive()
    finally:
        controller.cancel_computation(); thread.join(10)
    assert not failures and not thread.is_alive()


def test_requested_traffic_missing_fails_clearly_before_output_replacement(tmp_path, monkeypatch):
    import builtins
    original = builtins.__import__
    def import_module(name, *args, **kwargs):
        if name == 'tools.generation.traffic.workload':
            raise ImportError('Tool intentionally absent')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', import_module)
    _, controller = configured(tmp_path / 'output', traffic_enabled=True)
    with pytest.raises(RuntimeError, match='Traffic is enabled but its Tool is unavailable'):
        controller.generate()
    assert not (tmp_path / 'output').exists()


def test_generation_history_seals_without_reading_record_streams(tmp_path, monkeypatch):
    original = Path.open
    def open_path(path, mode='r', *args, **kwargs):
        if 'domain-history' in path.parts and path.suffix in {'.jsonl', '.gz'} and 'r' in mode:
            raise AssertionError('sealing rescanned generation records')
        return original(path, mode, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', open_path)
    _, controller = configured(tmp_path / 'output', traffic_enabled=False)
    controller.generate()
    manifest = json.loads((tmp_path / 'output/results/experiment/domain-history-manifest.json').read_text())
    assert manifest['counts']['formal_frames'] > 0


def test_pending_build_cannot_be_completed_by_a_preexisting_image(tmp_path, monkeypatch):
    runtime = tmp_path / 'deployment'; runtime.mkdir()
    journal = runtime / 'deployment-build-pending.json'
    journal.write_text(json.dumps({'image': 'image', 'previous_image_id': 'old'}))
    monkeypatch.setattr(deployer, 'run', lambda *a, **kw: CompletedProcess([], 0,
        json.dumps([{'Id': 'old', 'Config': {'Labels': {'com.docker.compose.project': 'p'}}}]), ''))
    with pytest.raises(deployer.DeploymentError, match='does not establish completion'):
        deployer._reconcile_build(tmp_path, 'p')
    assert journal.exists()


def test_lost_stop_client_is_reconciled_before_another_mutation(tmp_path, monkeypatch):
    runtime = tmp_path / 'deployment'; runtime.mkdir()
    journal = runtime / 'deployment-mutation.json'
    journal.write_text(json.dumps({'command': ['docker', 'stop', 'id-node']}))
    states = iter([True, True, False])
    queries, mutations = [], []
    def inspect(command, **kwargs):
        queries.append(command)
        return CompletedProcess(command, 0, json.dumps({'State': {'Running': next(states)}}), '')
    monkeypatch.setattr(deployer, 'run', inspect)
    monkeypatch.setattr(deployer, 'sleep', lambda seconds: None)
    monkeypatch.setattr(deployer, '_mutation', lambda command: mutations.append(command))
    deployer._accepted_mutation(['docker', 'rm', '--force', 'id-node'], tmp_path)
    assert len(queries) == 3 and len(mutations) == 1 and not journal.exists()


def test_failed_daemon_inventory_keeps_pending_work_and_original_error(tmp_path, monkeypatch):
    runtime = tmp_path / 'deployment'; runtime.mkdir()
    journal = runtime / 'deployment-pending.json'
    journal.write_text(json.dumps({'services': ['node']}))
    primary = deployer.DeploymentError('daemon unavailable')
    def inspect(*args, **kwargs): raise primary
    monkeypatch.setattr(deployer, '_inspect_startup_services', inspect)
    with pytest.raises(deployer.DeploymentError) as caught:
        deployer._reconcile_pending(tmp_path, 'p', {'node': {}})
    assert caught.value is primary and journal.exists()


def test_terminal_failure_is_visible_while_other_daemon_work_remains_unresolved(tmp_path, monkeypatch):
    runtime = tmp_path / 'deployment'; runtime.mkdir()
    journal = runtime / 'deployment-pending.json'
    journal.write_text(json.dumps({'services': ['failed', 'pending']}))
    containers = [{'Config': {'Labels': {'com.docker.compose.service': name}},
                   'State': {'Status': status, 'ExitCode': 1}}
                  for name, status in [('failed', 'exited'), ('pending', 'created')]]
    monkeypatch.setattr(deployer, '_inspect_startup_services', lambda *a, **kw: containers)
    def unexpected_sleep(*args): raise AssertionError('known failure was hidden by polling')
    monkeypatch.setattr(deployer, 'sleep', unexpected_sleep)
    with pytest.raises(deployer.DeploymentError, match='other accepted daemon work remains unresolved'):
        deployer._reconcile_pending(tmp_path, 'p', {'failed': {}, 'pending': {}})
    assert journal.exists()
    containers[1]['State']['Status'] = 'running'
    deployer._reconcile_pending(tmp_path, 'p', {'failed': {}, 'pending': {}})
    assert not journal.exists()  # Now explicit retry may replace the failed owner.


def test_deployer_builds_each_content_group_once_and_reuses_completed_images(tmp_path, monkeypatch):
    from tests.unit.test_deployment_images import DockerImages
    docker = DockerImages(monkeypatch)
    docker.add('handsonsecurity/seedemu-base:2.0', 'seed-base')
    docker.add('handsonsecurity/seedemu-router:2.0', 'seed-router')
    _, controller = configured(tmp_path / 'output', traffic_enabled=False)
    output = controller.generate()
    owner = deployer.Deployer(output)
    plan = [line.split()[1] for line in (output / 'deployment/skyseed-build-plan.txt').read_text().splitlines()
            if line and not line.startswith('#')]
    owner.build()
    assert [name for batch in docker.batches for name in batch] == plan
    count = len(docker.batches)
    owner.build()
    assert len(docker.batches) == count


def test_history_failure_drains_other_work_and_closes_all_streams():
    from skyseed.shared._durable_evidence import _OrderedEvidenceWriter
    from types import SimpleNamespace
    writer = _OrderedEvidenceWriter()
    release, entered = Event(), Event()
    primary = OSError('required record write failed')
    settled = []
    def fail():
        entered.set()
        assert release.wait(10)
        raise primary
    def close_failed():
        raise OSError('stream flush failed')
    writer._streams['first'] = SimpleNamespace(close=close_failed)
    writer._streams['second'] = SimpleNamespace(close=lambda: settled.append('second closed'))
    writer.submit(fail)
    assert entered.wait(10)
    writer.submit(lambda: settled.append('accepted work drained'))
    release.set()
    with pytest.raises(OSError) as caught:
        writer.finish()
    assert caught.value is primary
    assert settled == ['accepted work drained', 'second closed']
    assert any('stream flush failed' in note for note in primary.__notes__)
