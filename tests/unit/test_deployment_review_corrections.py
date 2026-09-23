"""Review regressions through real owners with fake Docker process boundaries."""
from copy import deepcopy
import importlib
import json
from pathlib import Path
from subprocess import CompletedProcess
from types import SimpleNamespace

import pytest

from skyseed.deployment import deployer
from tests.tools.test_runner_tool import _outcome, _snapshot
from tools.observation.runner import write_runner_tool


@pytest.fixture
def docker(tmp_path, monkeypatch):
    runtime = tmp_path / 'deployment'
    runtime.mkdir()
    services = {name: {'image': 'node'} for name in ('node', 'other')}
    (runtime / 'docker-compose.yml').write_text(deployer.safe_dump({'services': services}))
    (runtime / 'compose-project-name').write_text('project\n')
    (runtime / 'skyseed-build-plan.txt').write_text('')
    fake = SimpleNamespace(output=tmp_path, runtime=runtime, services=services, alive={},
                           calls=[], polls=[], clock=100.0, exit_code=0,
                           states={'node': 'running', 'other': 'running'},
                           cancel=False, waits=0, delayed=0, observation_error=None)

    def query(command, **kwargs):
        fake.calls.append(command)
        if command[1:3] == ['image', 'inspect']:
            value = json.dumps([{'Id': 'image-id'}])
        elif command[1:3] == ['container', 'inspect']:
            if fake.alive and fake.observation_error:
                raise fake.observation_error
            if fake.delayed and fake.alive:
                fake.delayed -= 1
                if not fake.delayed:
                    for c in fake.alive.values():
                        c['State'] = {'Status': 'running', 'Running': True}
            value = '\n'.join(json.dumps(c) for n, c in fake.alive.items()
                              if 'project-' + n + '-1' in command)
        elif command[1] == 'ps':
            value = '\n'.join(f'"{c["Id"]}" "project" "{n}"' for n, c in fake.alive.items())
        elif command[1:3] == ['network', 'ls']:
            value = ''
        else:
            raise AssertionError(command)
        return CompletedProcess(command, 0, value, '')

    class Client:
        def __init__(self, command, **kwargs):
            fake.calls.append(command)
            self.returncode = 0
            self.cancelled = False
            if command[1] == 'compose':
                assert '--no-recreate' in command
                fake.clock += 12.5
                self.returncode = fake.exit_code
                for name in command[command.index('--no-recreate') + 1:]:
                    state = fake.states[name]
                    fake.alive[name] = {
                        'Id': 'id-' + name, 'Image': 'image-id', 'RestartCount': 0,
                        'Config': {'Labels': {'com.docker.compose.project': 'project',
                            'com.docker.compose.service': name,
                            deployer._DEPLOYMENT_CONFIG_LABEL:
                                deployer._deployment_service_digest(services[name], 'image-id')}},
                        'State': {'Status': state, 'Running': state == 'running', 'ExitCode': 1},
                    }
            elif command[1] == 'stop':
                for c in fake.alive.values():
                    if c['Id'] in command:
                        c['State'] = {'Status': 'exited', 'Running': False, 'ExitCode': 0}
            elif command[1] == 'rm':
                for name in list(fake.alive):
                    if fake.alive[name]['Id'] in command:
                        del fake.alive[name]
            else:
                raise AssertionError(command)

        def communicate(self):
            fake.waits += 1
            if fake.cancel and not self.cancelled:
                self.cancelled = True
                raise KeyboardInterrupt()
            return '', 'ordinary compose failure' if self.returncode > 0 else ''

    def sleep(seconds):
        fake.polls.append(seconds)
        if not fake.delayed:
            raise AssertionError('known failure concealed by readiness polling')
        fake.clock += seconds

    monkeypatch.setattr(deployer, 'run', query)
    monkeypatch.setattr(deployer, 'Popen', Client)
    monkeypatch.setattr(deployer, 'sleep', sleep)
    monkeypatch.setattr(deployer, 'monotonic', lambda: fake.clock)
    monkeypatch.setattr(deployer, '_host_startup_sample',
                        lambda: {'memory': {'MemAvailable': 1000}})
    fake.owner = deployer.Deployer(tmp_path)
    return fake


@pytest.mark.parametrize('followup', ['retry', 'cleanup'])
def test_completed_command_failure_does_not_wait_for_residual_created(docker, followup):
    docker.exit_code = 1
    docker.states['node'] = 'created'
    with pytest.raises(deployer.DeploymentError, match='ordinary compose failure'):
        docker.owner.start()
    assert docker.polls == []
    assert not (docker.runtime / 'deployment-pending.json').exists()
    healthy = deepcopy(docker.alive['other'])
    docker.exit_code = 0
    docker.states['node'] = 'running'
    if followup == 'retry':
        docker.owner.start()
        assert docker.alive['other'] == healthy
        admissions = [c for c in docker.calls if c[1] == 'compose']
        assert admissions[-1][-1] == 'node' and 'other' not in admissions[-1]
    else:
        docker.owner.cleanup()
        assert docker.alive == {}
    assert docker.polls == []


def test_failed_command_keeps_original_error_when_observation_fails(docker):
    docker.exit_code = 1
    docker.observation_error = OSError('inspection unavailable')
    with pytest.raises(deployer.DeploymentError, match='ordinary compose failure') as caught:
        docker.owner.start()
    assert any('inspection unavailable' in n for n in caught.value.__notes__)
    assert not (docker.runtime / 'deployment-pending.json').exists()


def test_completed_failure_record_survives_unlink_error_without_waiting_on_retry(docker, monkeypatch):
    journal = docker.runtime / 'deployment-pending.json'
    original = Path.unlink
    def unlink(path, *args, **kwargs):
        if path == journal:
            raise OSError('journal removal unavailable')
        return original(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'unlink', unlink)
        docker.exit_code = 1
        docker.states['node'] = 'created'
        with pytest.raises(deployer.DeploymentError, match='ordinary compose failure') as caught:
            docker.owner.start()
    assert any('journal removal unavailable' in n for n in caught.value.__notes__)
    assert json.loads(journal.read_text())['command_failed'] is True
    docker.owner.cleanup()
    assert not docker.alive and not journal.exists() and not docker.polls


@pytest.mark.parametrize('exit_code', [0, -9])
def test_known_failure_preserves_other_unresolved_work_on_reentry(docker, exit_code):
    docker.exit_code = exit_code
    docker.states = {'node': 'exited', 'other': 'created'}
    with pytest.raises(deployer.DeploymentError):
        docker.owner.start()
    journal = docker.runtime / 'deployment-pending.json'
    original = journal.read_bytes()
    assert docker.polls == []
    admitted = [c for c in docker.calls if c[1] in ('compose', 'stop', 'rm')]
    for operation in (docker.owner.start, docker.owner.cleanup):
        with pytest.raises(deployer.DeploymentError, match='other accepted daemon work remains unresolved'):
            operation()
        assert journal.read_bytes() == original
    assert [c for c in docker.calls if c[1] in ('compose', 'stop', 'rm')] == admitted
    docker.alive['other']['State'] = {'Status': 'running', 'Running': True}
    docker.owner.cleanup()
    assert not journal.exists() and not docker.alive


def test_lost_client_with_created_container_reports_uncertainty_without_releasing(docker):
    docker.exit_code = -9
    docker.states['node'] = 'created'
    with pytest.raises(deployer._ClientLost, match='unresolved'):
        docker.owner.start()
    assert docker.polls == []
    assert (docker.runtime / 'deployment-pending.json').exists()


@pytest.mark.parametrize('cancel', [False, True])
def test_slow_startup_and_cancelled_client_drain_without_cutoff(docker, cancel, capsys):
    docker.states = {'node': 'created', 'other': 'created'}
    docker.delayed = 46
    docker.cancel = cancel
    if cancel:
        with pytest.raises(KeyboardInterrupt):
            docker.owner.start()
    else:
        docker.owner.start()
    assert len(docker.polls) == 45
    assert docker.waits == (2 if cancel else 1)
    assert not (docker.runtime / 'deployment-pending.json').exists()
    assert all(c['State']['Status'] == 'running' for c in docker.alive.values())
    feedback = capsys.readouterr().err
    assert ('Deployment startup succeeded' in feedback) is (not cancel)
    assert ('admitted Docker client to drain' in feedback) is cancel
    if not cancel:
        assert feedback.index('Container startup') < feedback.index('Deployment startup succeeded')


@pytest.mark.parametrize('failure_action', [None, 'stop', 'rm'])
def test_cleanup_stage_feedback_follows_successful_operations(docker, monkeypatch, capsys, failure_action):
    docker.owner.start()
    capsys.readouterr()
    original = deployer._accepted_mutation
    primary = deployer.DeploymentError('stage failed')
    def mutation(command, output):
        if command[1] == failure_action:
            raise primary
        return original(command, output)
    monkeypatch.setattr(deployer, '_accepted_mutation', mutation)
    if failure_action:
        with pytest.raises(deployer.DeploymentError) as caught:
            docker.owner.cleanup()
        assert caught.value is primary
    else:
        docker.owner.cleanup()
    feedback = capsys.readouterr().err
    assert 'stopping 2 containers gracefully' in feedback
    assert ('graceful stop succeeded' in feedback) is (failure_action != 'stop')
    assert ('removal succeeded' in feedback) is (failure_action is None)
    assert ('Deployment cleanup verified' in feedback) is (failure_action is None)


@pytest.mark.parametrize('failed', [False, True])
@pytest.mark.parametrize('write_fails', [False, True])
def test_real_startup_writer_to_runner_tool(docker, monkeypatch, capsys, failed, write_fails):
    summary = docker.runtime / 'deployment-startup.json'
    if write_fails:
        # Also exercise replacement of a preceding successful operation's evidence.
        docker.owner.start()
        docker.alive.clear()
        original = Path.write_text
        def write(path, *args, **kwargs):
            if path == summary:
                raise OSError('summary disk unavailable')
            return original(path, *args, **kwargs)
        monkeypatch.setattr(Path, 'write_text', write)
    if failed:
        docker.exit_code = 1
        with pytest.raises(deployer.DeploymentError, match='ordinary compose failure'):
            docker.owner.start()
    else:
        docker.owner.start()
    outcome = _outcome()
    write_runner_tool(_snapshot(), outcome, docker.output / 'tool', deployment_startup_path=summary)
    data = json.loads((docker.output / 'tool/runner-inspection.json').read_text())
    assert data['runtime_outcome']['status'] == outcome['status']
    if write_fails:
        assert not summary.exists()
        assert data['scale_safety']['deployment_startup'] is None
        assert 'unavailable' in data['scale_safety']['deployment_startup_recording']['status']
        assert 'summary disk unavailable' in capsys.readouterr().err
    else:
        actual = json.loads(summary.read_text())
        assert data['scale_safety']['deployment_startup'] == actual
        assert actual['status'] == ('failed' if failed else 'completed')
        assert actual['exit_code'] == (1 if failed else 0)
        assert actual['duration_seconds'] == 12.5
        if failed:
            assert actual['error'] == 'ordinary compose failure'


@pytest.mark.parametrize('runtime_status', ['completed', 'failed'])
def test_missing_optional_startup_does_not_bypass_required_runner_history(docker, runtime_status):
    optional = docker.runtime / 'deployment-startup.json'
    output = docker.output / 'tool'
    outcome = _outcome(runtime_status, primary_error=(
        {'type': 'RuntimeError', 'message': 'required execution failed'} if runtime_status == 'failed' else None))
    write_runner_tool(_snapshot(), outcome, output, deployment_startup_path=optional)
    data = json.loads((output / 'runner-inspection.json').read_text())
    assert data['scale_safety']['deployment_startup'] is None
    assert data['scale_safety']['deployment_startup_recording']['status'] == 'unavailable'
    assert data['runtime_outcome'] == outcome
    with pytest.raises(FileNotFoundError):
        write_runner_tool(_snapshot(), _outcome(), output,
                          deployment_startup_path=optional,
                          event_history_path=docker.output / 'required-missing.jsonl')


@pytest.mark.parametrize('malformed', ['{', '{"status":"completed"}',
                                      '{"artifact_type":"compose-startup-evidence","format_version":1,'
                                      '"status":"completed","exit_code":0,"duration_seconds":NaN}'])
def test_invalid_optional_startup_is_reported_without_fabricating_evidence(docker, malformed):
    summary = docker.runtime / 'deployment-startup.json'
    summary.write_text(malformed)
    output = docker.output / 'tool'
    write_runner_tool(_snapshot(), _outcome(), output, deployment_startup_path=summary)
    data = json.loads((output / 'runner-inspection.json').read_text())
    assert data['scale_safety']['deployment_startup'] is None
    assert data['scale_safety']['deployment_startup_recording']['status'] == 'unavailable'


def test_startup_summary_includes_pre_admission_failure_without_measurement_replay(docker, monkeypatch):
    original = Path.read_text
    def read(path, *args, **kwargs):
        assert path.name != 'deployment-measurements.jsonl', 'summary must not replay measurements'
        if path.name == 'skyseed-build-plan.txt':
            docker.clock += 2.75
            raise OSError('build plan unavailable')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', read)
    with pytest.raises(OSError, match='build plan unavailable'):
        docker.owner.start()
    value = json.loads((docker.runtime / 'deployment-startup.json').read_text())
    assert value['status'] == 'failed' and value['duration_seconds'] == 2.75
    assert value['error'] == 'build plan unavailable'
    assert not any(c[1] == 'compose' for c in docker.calls)


@pytest.mark.parametrize('entry', ['aggregate', 'cli'])
@pytest.mark.parametrize('available', [False, True])
def test_both_tool_entries_consume_real_startup_or_report_missing(docker, monkeypatch, entry, available):
    from tools.observation import experiment as reporting
    from tools.observation.runner.__main__ import main
    if available:
        docker.owner.start()
    inspection = docker.output / 'visualizations'
    invocation = docker.output / 'runs/fixture/runner'
    invocation.mkdir(parents=True)
    (invocation.parent.parent / 'latest.json').write_text(json.dumps({'invocation_id': 'fixture'}))
    handoff = invocation / 'runner-invocation.json'
    handoff.write_text(json.dumps({'format_version': 1, 'artifact_type': 'runner-inspection-handoff',
                                  'runner_snapshot': _snapshot(), 'runtime_outcome': _outcome()}))
    events = invocation / 'runner-events.jsonl'
    events.write_text('\n'.join(json.dumps(v) for v in [
        {'record_type': 'history_header', 'format_version': 1, 'owner': 'Runner'},
        *({'record_type': 'runner_event', **event} for event in _snapshot()['events'])]) + '\n')
    if entry == 'aggregate':
        (docker.output / 'results/experiment').mkdir(parents=True)
        (docker.output / 'results/experiment/completed.json').write_text(json.dumps({
            'status': 'MATERIALS_READY', 'configuration': {'zones': None}, 'producer': {},
            'through': 'physical-world', 'total_elapsed_seconds': 1.0}))
        monkeypatch.setattr(reporting, 'write_physical_world_tool', lambda *a, **kw: {'status': 'PASS'})
        results = reporting.write_experiment_tools(docker.output, physical_world=None)
        assert results['runner']['status'] == 'COMPLETED', results
    else:
        monkeypatch.setattr('sys.argv', ['tools.observation.runner', '--handoff', str(handoff), '--events', str(events),
            '--output', str(inspection / 'runner'), '--deployment-startup',
            str(docker.runtime / 'deployment-startup.json')])
        assert main() == 0
    data = json.loads((inspection / 'runner/runner-inspection.json').read_text())
    if available:
        assert data['scale_safety']['deployment_startup']['duration_seconds'] == 12.5
    else:
        assert data['scale_safety']['deployment_startup'] is None
        assert data['scale_safety']['deployment_startup_recording']['status'] == 'unavailable'


@pytest.mark.parametrize('document', ['history/paper-discussion.md',
                                     'history/optimization-decision-history.md',
                                     'user-guides/ordinary.md'])
@pytest.mark.parametrize('retired,replacement', [
    ('compose/launcher.py', 'deployment/deployer.py'),
    ('experiment/_realization_producer.py', 'experiment/producer.py'),
    ('experiment/workflow.py', 'experiment/controller.py'),
])
def test_link_validation_never_substitutes_relocated_sources(tmp_path, monkeypatch, document,
                                                            retired, replacement):
    root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(root / 'scripts'))
    checker = importlib.import_module('check_documentation')
    monkeypatch.setattr(checker, 'ROOT', tmp_path)
    monkeypatch.setattr(checker, 'PRODUCTION_ROOT', tmp_path / 'src/skyseed')
    available = tmp_path / 'src/skyseed' / replacement
    available.parent.mkdir(parents=True, exist_ok=True)
    available.write_text('')
    doc = tmp_path / 'docs' / document
    doc.parent.mkdir(parents=True)
    doc.write_text(f'[historical source](../../src/skyseed/{retired})')
    with pytest.raises(AssertionError, match='broken documentation links'):
        checker._validate_links((doc,))
