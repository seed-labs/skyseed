"""Real generated inputs and cooperative subprocesses at the prepared handoff."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from threading import Event, Thread

import pytest
from seedemu.compiler import Docker
from examples.Sky00_tiny_network import tiny_network as tiny
from skyseed_v2.experiment import ExperimentController, execution
from skyseed_v2.experiment._control import _request
from tests.integration.test_retained_workflow import await_state


ARGUMENTS = '''import argparse, json, pathlib, sys
p = argparse.ArgumentParser()
p.add_argument('--output', type=pathlib.Path, required=True)
p.add_argument('--invocation-directory', type=pathlib.Path, required=True)
a = p.parse_args()
i = a.invocation_directory
'''


def generate(tmp_path, body=None, *, traffic=True):
    script = tmp_path / 'readiness.py'
    script.write_text(ARGUMENTS + (body or ''))
    network = tiny.build_emulator()
    simulation = tiny.create_simulator(network)
    workflow = ExperimentController(network, simulation, tmp_path / 'output', entry_path=script,
        initial_readiness_command=None if body is None else (sys.executable, str(script)),
        traffic_enabled=traffic, internet_map_enabled=False, docker_compiler=Docker(internetMapEnabled=False))
    return workflow.generate(), script


def runtime(monkeypatch, *, block=False, cleanup_error=False):
    order, runners = [], []
    class Runner:
        def __init__(self, **kwargs):
            self.index = len(runners)
            self.done = Event()
            self.state = 'new'
            self.path = None
            runners.append(self)
        def _bind_execution_input(self, **facts):
            self.start = facts['start_time']
        def _enable_event_history(self, path):
            self.path = path
        def _enable_traffic_telemetry(self, path):
            pass
        def _record(self, *args, **kwargs):
            pass
        def prepare(self):
            self.state = 'prepared'
            order.append((self.index, 'prepare'))
        def run(self):
            order.append((self.index, 'run'))
            if block:
                self.done.wait()
        def cancel_runtime(self):
            self.done.set()
        def status(self):
            return {'lifecycle_state': self.state, 'accepts_batch': False}
        def inspection_snapshot(self):
            return {'runner': {'activation_count': 1, 'frontiers': {'last_active': self.start},
                'runtime_foundation': {'bindings': ()}}, 'events': ()}
        def cleanup(self):
            self.state = 'cleaned'
            order.append((self.index, 'cleanup'))
            if cleanup_error:
                raise RuntimeError('cleanup evidence failure')
    class Traffic:
        def __init__(self, **kwargs):
            self.index = len(runners) - 1
        def start(self, runner):
            order.append((self.index, 'traffic'))
        def stop(self):
            order.append((self.index, 'traffic-stop'))
        def snapshot(self):
            return {}
    monkeypatch.setattr(execution, 'Runner', Runner)
    monkeypatch.setattr(execution, '_record_container_checkpoint', lambda *a: None)
    monkeypatch.setattr('tools.generation.traffic.probe_execution.TrafficProbeExecutor', Traffic)
    return order, runners


def test_captured_readiness_runs_before_traffic_and_formal_time(tmp_path, monkeypatch, capsys):
    body = '''assert pathlib.Path.cwd() == a.output
s = json.loads((i/'runner/prepared.json').read_text())
assert s['runner_snapshot']['runner']['activation_count'] == 1
print('captured readiness', flush=True)
print('captured stderr', file=sys.stderr, flush=True)
(i/'readiness/released').write_text('yes')
'''
    output, script = generate(tmp_path, body)
    definition = execution._read_and_verify_execution_definition(output)
    assert definition['format_version'] == 6
    assert definition['initial_readiness_command'][1] == 'inputs/sources/readiness.py'
    script.write_text('raise RuntimeError("edited source must not run")')
    order, runners = runtime(monkeypatch)
    invocation = output / 'runs/test'
    original = execution.Runner.run
    def run(runner):
        assert (invocation / 'readiness/released').exists()
        original(runner)
    monkeypatch.setattr(execution.Runner, 'run', run)
    execution._run_generated_bpf_experiment(output, invocation_directory=invocation)
    row = json.loads((invocation / 'readiness/command.json').read_text())
    assert row['status'] == 'completed' and row['exit_code'] == 0 and row['elapsed_seconds'] > 0
    assert row['argv'][0] == sys.executable
    assert row['argv'][-4:] == ['--output', str(output), '--invocation-directory', str(invocation)]
    assert (invocation / 'readiness/stdout.log').read_text() == 'captured readiness\n'
    assert (invocation / 'readiness/stderr.log').read_text() == 'captured stderr\n'
    assert order == [(0, 'prepare'), (0, 'traffic'), (0, 'run'), (0, 'traffic-stop'), (0, 'cleanup')]
    feedback = capsys.readouterr().err
    assert feedback.index('User preparation starting') < feedback.index('User preparation succeeded') < feedback.index('Runtime cleanup starting')
    assert feedback.index('Runtime cleanup starting') < feedback.index('Runtime resource cleanup succeeded')
    assert 'Runtime resource cleanup failed' not in feedback


@pytest.mark.parametrize('version', [5, 6])
def test_no_command_has_no_prepared_handoff_or_wait(tmp_path, monkeypatch, version):
    output, _ = generate(tmp_path)
    path = output / 'results/compiler/execution.json'
    data = json.loads(path.read_text())
    data['format_version'] = version
    if version == 5:
        data.pop('initial_readiness_command')
        data['environment'].pop('python_executable')
    path.write_text(json.dumps(data))
    order, _ = runtime(monkeypatch)
    def unexpected(*a):
        pytest.fail('no-hook execution entered readiness')
    monkeypatch.setattr(execution, '_run_initial_readiness', unexpected)
    invocation = output / 'runs/test'
    execution._run_generated_bpf_experiment(output, invocation_directory=invocation)
    assert (0, 'traffic') in order and (0, 'run') in order
    assert not (invocation / 'runner/prepared.json').exists()


@pytest.mark.parametrize('cleanup_error', [False, True])
def test_command_failure_stays_primary_through_cleanup(tmp_path, monkeypatch, capsys, cleanup_error):
    output, _ = generate(tmp_path, "print('diagnostic', file=sys.stderr)\nsys.exit(7)\n")
    order, _ = runtime(monkeypatch, cleanup_error=cleanup_error)
    invocation = output / 'runs/test'
    with pytest.raises(subprocess.CalledProcessError) as caught:
        execution._run_generated_bpf_experiment(output, invocation_directory=invocation)
    assert caught.value.returncode == 7
    assert any('cleanup evidence failure' in n for n in getattr(caught.value, '__notes__', ())) is cleanup_error
    assert order == [(0, 'prepare'), (0, 'cleanup')]
    row = json.loads((invocation / 'readiness/command.json').read_text())
    assert row['status'] == 'failed' and row['exit_code'] == 7
    feedback = capsys.readouterr().err
    assert feedback.index('User preparation failed') < feedback.index('Runtime cleanup starting')
    assert 'User preparation succeeded' not in feedback
    assert 'Runtime cleanup starting' in feedback
    assert ('Runtime resource cleanup failed' in feedback) is cleanup_error
    assert ('Runtime resource cleanup succeeded' in feedback) is (not cleanup_error)


def test_prepared_file_is_required_before_command_start(tmp_path, monkeypatch):
    output, _ = generate(tmp_path, "(i/'unexpected').touch()\n")
    order, _ = runtime(monkeypatch)
    original = execution._write_runner_inspection_handoff
    def write(path, *args, **kwargs):
        if path.name == 'prepared.json':
            raise OSError('prepared input unavailable')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(execution, '_write_runner_inspection_handoff', write)
    invocation = output / 'runs/test'
    with pytest.raises(OSError, match='prepared input unavailable'):
        execution._run_generated_bpf_experiment(output, invocation_directory=invocation)
    assert order == [(0, 'prepare'), (0, 'cleanup')]
    assert not (invocation / 'unexpected').exists()


WAIT_FOR_CHILD = '''import subprocess, signal, time
from threading import Event
if i.name.startswith('reset-'):
    sys.exit(0)
stop = Event()
signal.signal(signal.SIGTERM, lambda *_: stop.set())
child = subprocess.Popen([sys.executable, '-c', ''' + repr('''import pathlib,signal,sys,time
p=pathlib.Path(sys.argv[1])
def end(*a):
    (p/'readiness/child-drained').touch()
    sys.exit(0)
signal.signal(signal.SIGTERM,end)
(p/'readiness/child-ready').write_text(str(__import__('os').getpid()))
while True: time.sleep(.05)
''') + ''', str(i)])
stop.wait()
child.terminate()
child.wait()
(i/'readiness/parent-drained').touch()
'''


@pytest.mark.parametrize('control', ['cancel-runtime', 'reset'])
def test_control_during_real_child_query_drains_before_cleanup(tmp_path, monkeypatch, control):
    output, _ = generate(tmp_path, WAIT_FOR_CHILD)
    order, runners = runtime(monkeypatch, block=True)
    invocation = output / 'runs/test'
    original = execution.Runner.cleanup
    def cleanup(runner):
        if runner.index == 0:
            assert (invocation / 'readiness/child-drained').exists()
            assert (invocation / 'readiness/parent-drained').exists()
        original(runner)
    monkeypatch.setattr(execution.Runner, 'cleanup', cleanup)
    errors = []
    def run():
        try:
            execution._run_generated_bpf_experiment(output, invocation_directory=invocation)
        except BaseException as error:
            errors.append(error)
    thread = Thread(target=run)
    thread.start()
    try:
        await_state(lambda: errors or (invocation / 'readiness/child-ready').exists())
        assert not errors and order == [(0, 'prepare')]
        status = _request(output, 'runtime', 'status')
        assert status['initial_readiness']['status'] == 'waiting'
        _request(output, 'runtime', control)
        if control == 'reset':
            await_state(lambda: errors or (1, 'run') in order)
            assert not errors
            assert order.index((0, 'cleanup')) < order.index((1, 'prepare'))
            assert (0, 'run') not in order and (0, 'traffic') not in order
            _request(output, 'runtime', 'cancel-runtime')
    finally:
        if thread.is_alive():
            _request(output, 'runtime', 'cancel-runtime')
        thread.join()
    assert not errors
    row = json.loads((invocation / 'readiness/command.json').read_text())
    assert row['status'] == 'cancelled'
    pid = int((invocation / 'readiness/child-ready').read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.parametrize('point', ['before-launch', 'after-exit'])
def test_cancel_at_handoff_races_never_starts_traffic(tmp_path, monkeypatch, point):
    output, _ = generate(tmp_path, "(i/'readiness/launched').touch()\n")
    order, _ = runtime(monkeypatch)
    invocation = output / 'runs/test'
    if point == 'before-launch':
        original = execution._write_runner_inspection_handoff
        def write(*args, **kwargs):
            result = original(*args, **kwargs)
            if args[0].name == 'prepared.json':
                _request(output, 'runtime', 'cancel-runtime')
            return result
        monkeypatch.setattr(execution, '_write_runner_inspection_handoff', write)
    else:
        original = execution._drain_readiness_process
        def drain(process):
            original(process)
            _request(output, 'runtime', 'cancel-runtime')
        monkeypatch.setattr(execution, '_drain_readiness_process', drain)
    execution._run_generated_bpf_experiment(output, invocation_directory=invocation)
    assert order == [(0, 'prepare'), (0, 'cleanup')]
    assert (invocation / 'readiness/launched').exists() is (point == 'after-exit')
    assert json.loads((invocation / 'readiness/command.json').read_text())['status'] == 'cancelled'


def test_real_ctrl_c_drains_command_children_before_cleanup(tmp_path):
    output, _ = generate(tmp_path, WAIT_FOR_CHILD)
    invocation = output / 'runs/test'
    code = '''import json,sys
from pathlib import Path
from pytest import MonkeyPatch
from tests.integration.test_initial_readiness import runtime
from skyseed_v2.experiment import execution
output=Path(sys.argv[1]); invocation=output/'runs/test'
patch=MonkeyPatch(); order,_=runtime(patch)
original=execution.Runner.cleanup
def cleanup(runner):
    assert (invocation/'readiness/parent-drained').exists()
    assert (invocation/'readiness/child-drained').exists()
    original(runner)
execution.Runner.cleanup=cleanup
try:
    execution._run_generated_bpf_experiment(output, invocation_directory=invocation)
except KeyboardInterrupt:
    (invocation/'interrupted').touch()
finally:
    (invocation/'order.json').write_text(json.dumps(order))
'''
    with (tmp_path / 'harness.log').open('w') as log:
        process = subprocess.Popen([sys.executable, '-c', code, str(output)], stdout=log, stderr=log,
            start_new_session=True, env={**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[2])})
        try:
            await_state(lambda: process.poll() is not None or (invocation / 'readiness/child-ready').exists())
            assert process.poll() is None, (tmp_path / 'harness.log').read_text()
            os.kill(process.pid, signal.SIGINT)
            assert process.wait() == 0, (tmp_path / 'harness.log').read_text()
        finally:
            if process.poll() is None:
                os.kill(process.pid, signal.SIGINT)
                process.wait()
    assert (invocation / 'interrupted').exists()
    assert json.loads((invocation / 'order.json').read_text()) == [[0, 'prepare'], [0, 'cleanup']]
    assert json.loads((invocation / 'readiness/command.json').read_text())['status'] == 'interrupted'


@pytest.mark.parametrize('command', ['', 'true', [], [1], [''], ['a\0b']])
def test_readiness_rejects_invalid_argv(command):
    with pytest.raises(ValueError, match='argv sequence'):
        execution._validate_readiness_command(command)


def test_future_delivery_starts_only_after_readiness_release(tmp_path, monkeypatch):
    output, _ = generate(tmp_path, "(i/'readiness/released').touch()\n")
    path = output / 'results/compiler/execution.json'
    data = json.loads(path.read_text())
    data['runner_batches'] = [{'manifest': 'results/compiler/next.json'}]
    path.write_text(json.dumps(data))
    order, _ = runtime(monkeypatch)
    invocation = output / 'runs/test'
    admitted = Event()
    monkeypatch.setattr(execution.Runner, 'status', lambda self: {'accepts_batch': True})
    def append(self, path):
        assert (invocation / 'readiness/released').exists()
        order.append((self.index, 'append'))
        admitted.set()
        return True
    def run(self):
        assert admitted.wait(10), 'delivery never reached prepared Runner'
        order.append((self.index, 'run'))
    monkeypatch.setattr(execution.Runner, 'append_batch', append, raising=False)
    monkeypatch.setattr(execution.Runner, 'run', run)
    execution._run_generated_bpf_experiment(output, invocation_directory=invocation)
    assert order == [(0, 'prepare'), (0, 'traffic'), (0, 'append'), (0, 'run'), (0, 'traffic-stop'), (0, 'cleanup')]


def test_cancel_during_process_launch_is_not_lost(tmp_path, monkeypatch):
    output, _ = generate(tmp_path, 'import time\ntime.sleep(1000)\n')
    order, _ = runtime(monkeypatch)
    original = execution.subprocess.Popen
    def launch(*args, **kwargs):
        process = original(*args, **kwargs)
        _request(output, 'runtime', 'cancel-runtime')
        return process
    monkeypatch.setattr(execution.subprocess, 'Popen', launch)
    invocation = output / 'runs/test'
    execution._run_generated_bpf_experiment(output, invocation_directory=invocation)
    assert order == [(0, 'prepare'), (0, 'cleanup')]
    row = json.loads((invocation / 'readiness/command.json').read_text())
    assert row['status'] == 'cancelled'
    with pytest.raises(ProcessLookupError):
        os.kill(row['pid'], 0)


def test_real_failure_wins_over_reset_arriving_during_drain(tmp_path, monkeypatch):
    output, _ = generate(tmp_path, 'sys.exit(9)\n')
    order, _ = runtime(monkeypatch)
    original = execution._drain_readiness_process
    def drain(process):
        original(process)
        _request(output, 'runtime', 'reset')
    monkeypatch.setattr(execution, '_drain_readiness_process', drain)
    invocation = output / 'runs/test'
    with pytest.raises(subprocess.CalledProcessError) as caught:
        execution._run_generated_bpf_experiment(output, invocation_directory=invocation)
    assert caught.value.returncode == 9
    assert order == [(0, 'prepare'), (0, 'cleanup')]
    assert json.loads((invocation / 'readiness/command.json').read_text())['status'] == 'failed'


@pytest.mark.parametrize('primary_fails', [False, True])
def test_observer_cleanup_failure_is_reported_without_replacing_primary(tmp_path, monkeypatch, capsys, primary_fails):
    output, _ = generate(tmp_path, 'sys.exit(7)' if primary_fails else None)
    runtime(monkeypatch)
    (output / '.control/internet-map').mkdir(parents=True)
    class Observer:
        def __init__(self, *args, **kwargs): pass
        def close(self): raise RuntimeError('observer cleanup failure')
    monkeypatch.setattr('skyseed_v2.backend.runner.connection_observer._ConnectionObserver', Observer)
    with pytest.raises(subprocess.CalledProcessError if primary_fails else RuntimeError) as caught:
        execution._run_generated_bpf_experiment(output, invocation_directory=output/'runs/test')
    if primary_fails:
        assert caught.value.returncode == 7
        assert any('observer cleanup failure' in n for n in caught.value.__notes__)
    feedback = capsys.readouterr().err
    assert 'Runtime resource cleanup failed: RuntimeError: observer cleanup failure' in feedback
    assert 'Runtime resource cleanup succeeded' not in feedback
