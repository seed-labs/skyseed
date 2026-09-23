from __future__ import annotations

import json

import pytest

from examples.Sky00_tiny_network.tiny_network import build_emulator
from examples.Sky00_tiny_network.tiny_network import create_simulator as create_complete_static_simulator
from tests._support import drain_simulator as run_complete_static_chain
from skyseed.backend.execution_materials.sealing import _write_runner_artifact, _RunnerArtifactWriter
from skyseed.experiment.execution import (
    _read_and_verify_execution_definition,
    _write_execution_definition,
    _write_generated_run_script,
)


@pytest.fixture(scope="module")
def compiled_chain():
    simulator = create_complete_static_simulator(build_emulator())
    plans, realizations, _ = run_complete_static_chain(simulator)
    return simulator, plans, realizations


def _generated_output(tmp_path, compiled_chain):
    simulator, _, realizations = compiled_chain
    project = tmp_path / "project"
    example = project / "examples/demo"
    for path in (
        project / "src/skyseed/runtime.py",
        example / "network.py",
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("VALUE = 1\n", encoding="utf-8")
    output = tmp_path / "output"
    (output / "inputs").mkdir(parents=True)
    (output / "inputs/scenario.yaml").write_text(
        "scenario: test\n", encoding="utf-8"
    )
    (output / "deployment").mkdir(parents=True)
    (output / "deployment/docker-compose.yml").write_text(
        "services: {}\n", encoding="utf-8"
    )
    (output / "deployment/compose-project-name").write_text(
        "round-b-bootstrap\n", encoding="utf-8"
    )
    writer = _RunnerArtifactWriter(output / "results/compiler/runner-artifact")
    for result in realizations:
        writer.append(result)
    writer.finalize()
    sealed_metadata = writer.sealed_metadata()
    writer.close()
    timeline = simulator.get_timeline()
    definition = _write_execution_definition(
        output,
        generated_output_identity="round-b-bootstrap",
        timeline_start=timeline.start_time,
        timeline_end=timeline.end_time,
        observation_interval=timeline.observation_interval,
        runner_artifact="results/compiler/runner-artifact/manifest.json",
        realization_producer="common-incremental-realization-producer-v1",
        sealed_metadata=sealed_metadata,
    )
    return output, project, example, definition


def test_generated_definition_is_persisted_only(tmp_path, compiled_chain) -> None:
    output, _, _, path = _generated_output(tmp_path, compiled_chain)
    definition = _read_and_verify_execution_definition(output)
    serialized = json.loads(path.read_text(encoding="utf-8"))
    assert definition == serialized
    assert definition["format_version"] == 6
    assert definition["progression"] == {
        "strategy": "sealed-precomputed-runner-artifact",
        "producer": "common-incremental-realization-producer-v1",
    }
    handoff = definition["runner_artifact"]
    assert handoff["manifest"] == "results/compiler/runner-artifact/manifest.json"
    assert handoff["format_version"] == 8
    assert handoff["encoding"] == "prepared-forwarding-v8"
    assert handoff["realization_count"] == 1
    assert len(handoff["sequence_sha256"]) == 64
    assert len(handoff["manifest_sha256"]) == 64
    assert definition["simulation"]["bounded"] is True
    assert "builder" not in json.dumps(definition).lower()
    assert "pickle" not in json.dumps(definition).lower()


def test_source_comments_archived_inputs_and_environment_are_provenance(
    tmp_path, compiled_chain, monkeypatch
) -> None:
    output, project, example, _ = _generated_output(tmp_path, compiled_chain)
    assert _read_and_verify_execution_definition(output)["backend"] == "bpf"

    source = example / "network.py"
    source.write_text("VALUE = 2\n", encoding="utf-8")
    assert _read_and_verify_execution_definition(output)["backend"] == "bpf"
    source.write_text("VALUE = 1\n", encoding="utf-8")

    scenario = output / "inputs/scenario.yaml"
    scenario.write_text("scenario: changed\n", encoding="utf-8")
    assert _read_and_verify_execution_definition(output)["backend"] == "bpf"
    compose = output / 'deployment/docker-compose.yml'
    compose.write_text(compose.read_text() + '\n# unrelated formatting change\n')
    import skyseed.experiment.execution as execution
    monkeypatch.setattr(execution.platform, 'python_version', lambda: 'different interpreter version')
    monkeypatch.setattr(execution.metadata, 'version', lambda name: 'different SEED version')
    assert _read_and_verify_execution_definition(output)["backend"] == "bpf"


def test_generated_definition_rejects_old_handoff_format(
    tmp_path, compiled_chain
) -> None:
    output, _, _, _ = _generated_output(tmp_path, compiled_chain)
    manifest = output / "results/compiler/runner-artifact/manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "artifact_type": "bpf-compiled-realization-sequence",
                "format_version": 1,
                "encoding": "gzip-pickle-v1",
                "realizations": [],
            }
        ),
        encoding="utf-8",
    )
    from skyseed.backend.execution_materials.codec import _open_runner_artifact
    with pytest.raises(ValueError, match="schema differs"):
        _open_runner_artifact(manifest)


def test_manifest_whitespace_is_not_execution_incompatibility(
    tmp_path, compiled_chain
) -> None:
    output, _, _, _ = _generated_output(tmp_path, compiled_chain)
    manifest = output / "results/compiler/runner-artifact/manifest.json"
    value = json.loads(manifest.read_text(encoding="utf-8"))
    manifest.write_text(json.dumps(value, indent=4) + "\n", encoding="utf-8")
    from skyseed.backend.execution_materials.codec import _open_runner_artifact
    artifact = _open_runner_artifact(manifest)
    artifact.close()
    assert _read_and_verify_execution_definition(output)["backend"] == "bpf"


def test_scene_binding_fails_at_actual_reader_before_deployment(tmp_path, compiled_chain, monkeypatch):
    from skyseed.backend.runner.lifecycle import Runner
    from skyseed.backend.runner import deployment
    output, _, _, _ = _generated_output(tmp_path, compiled_chain)
    def unexpected(*args, **kwargs):
        raise AssertionError('deployment touched before scene validation')
    monkeypatch.setattr(deployment, '_execute', unexpected)
    runner = Runner(compose_project_name='test', runtime_artifact=output / 'results/compiler/runner-artifact/manifest.json')
    runner._bind_execution_input(physical_world_fingerprint='different-scene', start_time='different', end_time='different')
    try:
        with pytest.raises(ValueError, match='bound scene or Timeline'):
            runner.prepare()
    finally:
        runner.cleanup()


def test_generated_run_script_consumes_only_persisted_domain_history(
    tmp_path, compiled_chain
) -> None:
    output, project, _, _ = _generated_output(tmp_path, compiled_chain)
    (project / "tools/__init__.py").parent.mkdir(parents=True, exist_ok=True)
    (project / "tools/__init__.py").write_text("", encoding="utf-8")
    path = _write_generated_run_script(
        output,
        python_executable="python",
        tool_package_parent=project,
    )
    script = path.read_text(encoding="utf-8")
    assert "results/compiler/runner-artifact/manifest.json" in script
    assert "results/compiler/runner-artifact/runner-runtime.bin" in script
    assert "runs/$INVOCATION_ID" in script
    assert '--invocation-directory "$INVOCATION_DIR"' in script
    assert '"$INVOCATION_DIR/runner/runner-invocation.json"' in script
    assert '"$INVOCATION_DIR/runner/runner-events.jsonl"' in script
    assert '"$INVOCATION_DIR/traffic/traffic-telemetry.jsonl"' in script
    assert '"$INVOCATION_DIR/traffic/probe-execution.jsonl"' in script
    assert '"$INVOCATION_DIR/performance/execution-observations.jsonl"' in script
    assert "results/physical-world/physical-frames.jsonl" in script
    # Traffic consumes the fixed workload and actual invocation evidence.
    assert "results/traffic/workload.json" in script
    assert "--probe-plan" not in script
    assert "results/planner/planning.jsonl.gz" not in script
    assert "performance/execution-observations.jsonl" in script
    assert "-m tools.observation.performance" in script
    assert '--generated "$SCRIPT_DIR"' in script
    assert 'exit "$traffic_tool_status"' not in script
    assert 'exit "$runner_tool_status"' not in script
    assert "detached inspection Tools failed" in script
    assert "progression_builder" not in script
    assert "slicer" not in script.lower()


def test_generated_scripts_execute_the_frozen_division_of_responsibility(tmp_path, compiled_chain, monkeypatch):
    """Run generated shell controls with recording executables, never Docker."""
    from hashlib import sha256
    import os
    from pathlib import Path
    import subprocess
    import sys
    from skyseed.deployment.deployer import _write_deployment_scripts

    output, project, _, _ = _generated_output(tmp_path, compiled_chain)
    (project / "tools").mkdir()
    (project / "tools/__init__.py").write_text("")
    (output / 'results/experiment').mkdir(parents=True, exist_ok=True)
    (output / 'results/experiment/domain-history-manifest.json').write_text('{}')
    executables = tmp_path / "recording-executables"
    executables.mkdir()
    log = tmp_path / "commands.jsonl"
    recorder = f'''#!{sys.executable}
import json, os, sys
with open(os.environ["RUNNER_SCRIPT_TEST_LOG"], "a") as stream:
    stream.write(json.dumps([os.path.basename(sys.argv[0]), sys.argv[1:]]) + "\\n")
'''
    for name in ("docker", "record-python"):
        path = executables / name
        path.write_text(recorder)
        path.chmod(0o755)
    monkeypatch.setenv("PATH", str(executables) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("RUNNER_SCRIPT_TEST_LOG", str(log))
    _write_deployment_scripts(output, str(executables / "record-python"), None)
    _write_generated_run_script(output, python_executable=str(executables / "record-python"),
                               tool_package_parent=project,
                               )
    artifact_files = tuple((output / "results/compiler/runner-artifact").iterdir())
    hashes = {path: sha256(path.read_bytes()).hexdigest() for path in artifact_files}

    def invoke(script):
        log.write_text("")
        subprocess.run(("bash", str(output / script)), check=True, capture_output=True, text=True)
        assert hashes == {path: sha256(path.read_bytes()).hexdigest() for path in artifact_files}
        import re
        return [[executable, [re.sub(r"/runs/[^/]+", "/runs/INVOCATION", arg) for arg in args]]
                for executable, args in (json.loads(line) for line in log.read_text().splitlines())]

    start = invoke("deploy.sh")
    assert start == [["record-python", ["-m", "skyseed.deployment", str(output), "start"]]]
    # A material batch may be executable before any whole request is published.
    assert len(invoke("run.sh")) == 1
    (output / 'results/experiment/completed.json').write_text('{}')
    from tools.generation.traffic.workload import select_workload, write_workload
    simulator = create_complete_static_simulator(build_emulator())
    try:
        write_workload(select_workload(simulator.get_physical_world()),
                       output / 'results/traffic/workload.json')
    finally:
        simulator.shutdown()
    execution = invoke("run.sh")
    assert all(executable == "record-python" for executable, _ in execution)
    assert execution[0][1][:2] == ["-m", "skyseed.experiment.execution"]
    assert not (output / 'deployment/deployment-startup.json').exists()
    runner_arguments = next(args for _, args in execution if args[:2] == ['-m', 'tools.observation.runner'])
    assert runner_arguments[runner_arguments.index('--deployment-startup') + 1] == str(
        output / 'deployment/deployment-startup.json')
    traffic_arguments = next(args for _, args in execution if args[:2] == ['-m', 'tools.observation.traffic'])
    assert traffic_arguments[traffic_arguments.index('--workload') + 1] == str(output / 'results/traffic/workload.json')
    assert traffic_arguments[traffic_arguments.index('--probe-execution') + 1] == str(output / 'runs/INVOCATION/traffic/probe-execution.jsonl')
    assert '--probe-plan' not in traffic_arguments
    assert all("main.py" not in " ".join(args) for _, args in execution)
    assert not (output / "launch.sh").exists() and not (output / "start.sh").exists()
    monkeypatch.setenv("SKYSEED_NO_TOOLS", "1")
    without_reports = invoke("run.sh")
    assert len(without_reports) == 1
    assert without_reports[0][1][:2] == ["-m", "skyseed.experiment.execution"]
    cleanup = invoke("cleanup.sh")
    assert cleanup == [["record-python", ["-m", "skyseed.deployment", str(output), "cleanup"]]]


@pytest.mark.parametrize("execution_fails", [False, True])
@pytest.mark.parametrize("traffic_fails", [False, True])
def test_checkpoint_write_failure_never_skips_runner_cleanup(tmp_path, monkeypatch, execution_fails, traffic_fails):
    from types import SimpleNamespace
    from skyseed.experiment import execution as module
    output = tmp_path / 'output'
    (output / 'deployment').mkdir(parents=True)
    from skyseed.experiment import interaction_service
    interaction_service.start_interaction(output)
    (output / 'results/compiler').mkdir(parents=True)
    (output / 'results/compiler/materials.jsonl').write_text(json.dumps({'index': 0, 'manifest': 'artifact'}) + '\n')
    probe = output / 'results/traffic/workload.json'
    probe.parent.mkdir(parents=True); probe.write_text('{}')
    calls = []
    primary = RuntimeError('execution failed')
    traffic_error = RuntimeError('requested Traffic failed')
    class Runner:
        def __init__(self, **kwargs): pass
        def _enable_event_history(self, path): pass
        def _enable_traffic_telemetry(self, path): pass
        def prepare(self): calls.append('prepare')
        def run(self):
            calls.append('run')
            if execution_fails: raise primary
        def status(self):
            # One final material is already loaded; no delivery append is legal.
            return {'accepts_batch': False}
        def cleanup(self): calls.append('cleanup')
        def inspection_snapshot(self): return {}
    class Traffic:
        def snapshot(self): return {}
        def __init__(self, **kwargs): pass
        def start(self, runner): pass
        def stop(self):
            calls.append('traffic-stop')
            if traffic_fails: raise traffic_error
    def checkpoint(output, destination, project):
        if 'before-cleanup' in destination.name:
            raise OSError('checkpoint write failed')
    monkeypatch.setattr(module, 'Runner', Runner)
    monkeypatch.setattr('tools.generation.traffic.probe_execution.TrafficExecutor', Traffic)
    monkeypatch.setattr('tools.generation.traffic.workload.read_workload', lambda path: SimpleNamespace(communications=()))
    monkeypatch.setattr(module, '_read_and_verify_execution', lambda output: (
        {'format_version': 3, 'backend': 'bpf', 'generated_output_identity':'test', 'runner':{}}, tmp_path/'artifact',
        {'realization_count':1, 'boundary_count':1}))
    monkeypatch.setattr(module, '_record_container_checkpoint', checkpoint)
    for name in ('_write_runtime_invocation_index', '_write_runtime_execution_observations', '_write_runner_inspection_handoff'):
        monkeypatch.setattr(module, name, lambda *a, **kw: None)
    if execution_fails or traffic_fails:
        with pytest.raises(RuntimeError) as caught:
            module._run_generated_bpf_experiment(output, invocation_directory=output/'runs/test')
        assert caught.value is (primary if execution_fails else traffic_error)
        assert any('checkpoint' in note for note in caught.value.__notes__)
        if execution_fails and traffic_fails:
            assert any('secondary Traffic' in note for note in primary.__notes__)
    else:
        module._run_generated_bpf_experiment(output, invocation_directory=output/'runs/test')
    assert calls == ['prepare', 'run', 'traffic-stop', 'cleanup']


@pytest.mark.parametrize('runtime_status,failed_tool', [
    (0, None), (0, 'runner'), (7, 'traffic'), (130, 'performance'), (0, 'index'),
])
def test_generated_tool_feedback_preserves_runtime_exit(tmp_path, compiled_chain, runtime_status, failed_tool):
    import subprocess
    import sys
    output, project, _, _ = _generated_output(tmp_path, compiled_chain)
    (project / 'tools').mkdir()
    (project / 'tools/__init__.py').write_text('')
    (output / 'results/experiment').mkdir(exist_ok=True)
    (output / 'results/experiment/completed.json').write_text('{}')
    (output / 'results/traffic').mkdir()
    (output / 'results/traffic/workload.json').write_text('{}')
    executable = tmp_path / 'fake-python'
    executable.write_text(f'''#!{sys.executable}
import sys
module = sys.argv[2] if sys.argv[1] == '-m' else 'index'
if module == 'skyseed.experiment.execution': sys.exit({runtime_status})
if module.rsplit('.', 1)[-1] == {failed_tool!r}: sys.exit(9)
''')
    executable.chmod(0o755)
    script = _write_generated_run_script(output, python_executable=str(executable), tool_package_parent=project)
    completed = subprocess.run(['bash', str(script)], capture_output=True, text=True)
    assert completed.returncode == runtime_status
    assert not completed.stdout
    assert 'Observation Tool generation starting' in completed.stderr
    # An execution subprocess returning 0 alone proves no resource cleanup.
    assert 'Runtime resource cleanup succeeded' not in completed.stderr
    if failed_tool is None:
        assert 'Observation Tool generation succeeded' in completed.stderr
        assert 'Observation Tool generation failed' not in completed.stderr
    else:
        label = ('Observation index generation' if failed_tool == 'index'
                 else failed_tool.title() + ' observation Tool')
        assert label + ' failed (exit 9)' in completed.stderr
        assert 'Observation Tool generation failed' in completed.stderr
        assert 'Observation Tool generation succeeded' not in completed.stderr
