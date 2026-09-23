"""Captured Python preparation across the generation/execution process boundary."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Thread

import pytest
from seedemu.compiler import Docker
from examples.Sky00_tiny_network import tiny_network as tiny
from skyseed.experiment import ExperimentController, execution
from skyseed.experiment._control import _request
from tests.integration.test_initial_readiness import runtime
from tests.integration.test_retained_workflow import await_state


def generate(tmp_path, body, *, package=False, capture_initializers=True):
    directory = tmp_path / 'author_package/nested' if package else tmp_path
    directory.mkdir(parents=True, exist_ok=True)
    initializers = (directory.parent / '__init__.py', directory / '__init__.py') if package else ()
    for initializer in initializers:
        initializer.write_text('CAPTURED = True\n')
    source = directory / 'author.py'
    source.write_text('import time\nfrom ' + ('.helper' if package else 'helper') + ' import VALUE\ndef prepare(context):\n' +
                      '\n'.join('    '+line for line in body.splitlines()) + '\n')
    helper = directory / 'helper.py'; helper.write_text('VALUE = "captured dependency"\n')
    sys.path.insert(0, str(tmp_path))
    try:
        sys.modules.pop('helper', None)
        spec = importlib.util.spec_from_file_location('author_package.nested.author' if package else 'author_preparation', source)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(tmp_path)); sys.modules.pop('helper', None)
        for name in tuple(sys.modules):
            if name == 'author_package' or name.startswith('author_package.'):
                del sys.modules[name]
    network = tiny.build_emulator()
    controller = ExperimentController(network, tiny.create_simulator(network), tmp_path / 'output',
        entry_path=source, input_files=(helper, *(initializers if capture_initializers else ())), preparation=module.prepare,
        preparation_inputs={'value': 42}, traffic_enabled=True,
        internet_map_enabled=False, docker_compiler=Docker(internetMapEnabled=False))
    output = controller.generate()
    source.write_text('raise AssertionError("original source executed")')
    helper.write_text('raise AssertionError("original dependency executed")')
    for initializer in initializers:
        initializer.write_text('raise AssertionError("original package executed")')
    return output


@pytest.mark.parametrize('capture_initializers', [True, False])
def test_captured_package_relative_imports_ignore_modified_originals(tmp_path, monkeypatch, capture_initializers):
    initializer_check = ("from . import CAPTURED\nassert CAPTURED is True\n"
                         "from .. import CAPTURED as PARENT\nassert PARENT is True\n") if capture_initializers else ''
    output = generate(tmp_path, initializer_check + "assert VALUE == 'captured dependency'\n"
        "(context.invocation_directory / 'package-prepared').touch()",
        package=True, capture_initializers=capture_initializers)
    # Keep the now-broken original package importable in the real child process.
    monkeypatch.setenv('PYTHONPATH', str(tmp_path) + os.pathsep + os.environ.get('PYTHONPATH', ''))
    order, _ = runtime(monkeypatch)
    invocation = output / 'runs/package'
    execution._run_generated_bpf_experiment(output, invocation_directory=invocation)
    assert (invocation / 'package-prepared').exists()
    assert order == [(0, 'prepare'), (0, 'traffic'), (0, 'run'), (0, 'traffic-stop'), (0, 'cleanup')]


def test_captured_function_context_and_wait_before_traffic(tmp_path, monkeypatch):
    output = generate(tmp_path, '''assert context.inputs == {'value': 42}
assert VALUE == 'captured dependency'
assert context.initial_state.timestamp.isoformat() == context.runtime_manifest['timeline']['start']
assert context.runner_snapshot['runner']['activation_count'] == 1
assert context.source_files
assert context.output_directory == __import__('pathlib').Path.cwd()
time.sleep(.1)
(context.invocation_directory / 'prepared-by-user').touch()''')
    order, _ = runtime(monkeypatch)
    invocation = output / 'runs/test'
    original = execution.Runner.run
    def run(runner):
        assert (invocation/'prepared-by-user').exists()
        original(runner)
    monkeypatch.setattr(execution.Runner, 'run', run)
    execution._run_generated_bpf_experiment(output, invocation_directory=invocation)
    assert order == [(0, 'prepare'), (0, 'traffic'), (0, 'run'), (0, 'traffic-stop'), (0, 'cleanup')]
    assert json.loads((invocation/'readiness/command.json').read_text())['elapsed_seconds'] >= .1


def test_waiting_function_cancels_without_formal_time(tmp_path, monkeypatch, capsys):
    output = generate(tmp_path, "(context.invocation_directory / 'waiting').touch()\ntime.sleep(600)")
    order, _ = runtime(monkeypatch)
    invocation = output/'runs/test'; errors = []
    def run():
        try: execution._run_generated_bpf_experiment(output, invocation_directory=invocation)
        except BaseException as e: errors.append(e)
    thread = Thread(target=run); thread.start()
    try:
        await_state(lambda: (invocation/'waiting').exists() or errors)
        assert 'Runtime resource cleanup succeeded' not in capsys.readouterr().err
        _request(output, 'runtime', 'cancel-runtime')
    finally:
        if thread.is_alive(): _request(output, 'runtime', 'cancel-runtime')
        thread.join()
    assert not errors
    assert order == [(0, 'prepare'), (0, 'cleanup')]
    assert json.loads((invocation/'readiness/command.json').read_text())['status'] == 'cancelled'
    feedback = capsys.readouterr().err
    assert feedback.index('Runtime cleanup starting') < feedback.index('Runtime resource cleanup succeeded')


def test_function_error_preserves_failure_and_cleanup(tmp_path, monkeypatch):
    output = generate(tmp_path, 'raise ValueError("user preparation failure")')
    order, _ = runtime(monkeypatch, cleanup_error=True)
    invocation = output/'runs/test'
    with pytest.raises(subprocess.CalledProcessError) as caught:
        execution._run_generated_bpf_experiment(output, invocation_directory=invocation)
    assert 'user preparation failure' in (invocation/'readiness/stderr.log').read_text()
    assert any('cleanup evidence failure' in note for note in caught.value.__notes__)
    assert order == [(0, 'prepare'), (0, 'cleanup')]


def test_sleep_only_function_returns_without_report(tmp_path, monkeypatch):
    output = generate(tmp_path, 'time.sleep(.1)')
    order, _ = runtime(monkeypatch)
    invocation = output/'runs/test'
    execution._run_generated_bpf_experiment(output, invocation_directory=invocation)
    row = json.loads((invocation/'readiness/command.json').read_text())
    assert row['status'] == 'completed' and row['elapsed_seconds'] >= .1
    assert order == [(0, 'prepare'), (0, 'traffic'), (0, 'run'), (0, 'traffic-stop'), (0, 'cleanup')]
