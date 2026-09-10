from __future__ import annotations

import json

import pytest

from examples.complete_static_emulator.emu import build_emulator
from examples.complete_static_emulator.simu import (
    create_complete_static_simulator,
    run_complete_static_chain,
)
from skyseed_v2.backend.bpf._runner_artifact import (
    _write_runner_artifact,
)
from skyseed_v2.experiment.execution import (
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
        project / "src/skyseed_v2/runtime.py",
        example / "emu.py",
        example / "simu.py",
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("VALUE = 1\n", encoding="utf-8")
    output = tmp_path / "output"
    (output / "inputs").mkdir(parents=True)
    (output / "inputs/scenario.yaml").write_text(
        "scenario: test\n", encoding="utf-8"
    )
    (output / "runtime").mkdir(parents=True)
    (output / "runtime/docker-compose.yml").write_text(
        "services: {}\n", encoding="utf-8"
    )
    (output / "runtime/compose-project-name").write_text(
        "round-b-bootstrap\n", encoding="utf-8"
    )
    _write_runner_artifact(
        realizations,
        output / "runtime/runner-artifact",
    )
    timeline = simulator.get_timeline()
    definition = _write_execution_definition(
        output,
        generated_output_identity="round-b-bootstrap",
        timeline_start=timeline.start_time,
        timeline_end=timeline.end_time,
        observation_interval=timeline.observation_interval,
        project_root=project,
        example_root=example,
        runner_artifact="runtime/runner-artifact/manifest.json",
        realization_producer="common-incremental-realization-producer-v1",
    )
    return output, project, example, definition


def test_generated_definition_is_persisted_only(tmp_path, compiled_chain) -> None:
    output, _, _, path = _generated_output(tmp_path, compiled_chain)
    definition = _read_and_verify_execution_definition(output)
    serialized = json.loads(path.read_text(encoding="utf-8"))
    assert definition == serialized
    assert definition["format_version"] == 3
    assert definition["progression"] == {
        "strategy": "sealed-precomputed-runner-artifact",
        "producer": "common-incremental-realization-producer-v1",
    }
    handoff = definition["runner_artifact"]
    assert handoff["manifest"] == "runtime/runner-artifact/manifest.json"
    assert handoff["format_version"] == 3
    assert handoff["encoding"] == "typed-packed-runtime-tables-v3"
    assert handoff["realization_count"] == 1
    assert len(handoff["sequence_sha256"]) == 64
    assert len(handoff["manifest_sha256"]) == 64
    assert definition["simulation"]["bounded"] is True
    assert "builder" not in json.dumps(definition).lower()
    assert "pickle" not in json.dumps(definition).lower()


def test_generated_definition_detects_source_and_input_drift(
    tmp_path, compiled_chain
) -> None:
    output, project, example, _ = _generated_output(tmp_path, compiled_chain)
    assert _read_and_verify_execution_definition(output)["backend"] == "bpf"

    source = example / "simu.py"
    source.write_text("VALUE = 2\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="source changed"):
        _read_and_verify_execution_definition(output)
    source.write_text("VALUE = 1\n", encoding="utf-8")

    scenario = output / "inputs/scenario.yaml"
    scenario.write_text("scenario: changed\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="runtime input changed"):
        _read_and_verify_execution_definition(output)


def test_generated_definition_rejects_old_handoff_format(
    tmp_path, compiled_chain
) -> None:
    output, _, _, _ = _generated_output(tmp_path, compiled_chain)
    manifest = output / "runtime/runner-artifact/manifest.json"
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
    with pytest.raises(ValueError, match="schema differs"):
        _read_and_verify_execution_definition(output)


def test_generated_definition_anchors_exact_handoff_manifest(
    tmp_path, compiled_chain
) -> None:
    output, _, _, _ = _generated_output(tmp_path, compiled_chain)
    manifest = output / "runtime/runner-artifact/manifest.json"
    value = json.loads(manifest.read_text(encoding="utf-8"))
    manifest.write_text(json.dumps(value, indent=4) + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Runner artifact changed"):
        _read_and_verify_execution_definition(output)


def test_generated_run_script_consumes_only_persisted_domain_history(
    tmp_path, compiled_chain
) -> None:
    output, project, _, _ = _generated_output(tmp_path, compiled_chain)
    (project / "tools/__init__.py").parent.mkdir(parents=True, exist_ok=True)
    (project / "tools/__init__.py").write_text("", encoding="utf-8")
    path = _write_generated_run_script(
        output,
        python_executable="python",
        compiler_tool_directory="bpf-compiler",
        tool_package_parent=project,
    )
    script = path.read_text(encoding="utf-8")
    assert "runtime/runner-artifact/manifest.json" in script
    assert "runtime/runner-artifact/runner-runtime.bin" in script
    assert "inspection/runtime-runs/$INVOCATION_ID" in script
    assert '--invocation-directory "$INVOCATION_DIR"' in script
    assert '"$INVOCATION_DIR/runner/runner-invocation.json"' in script
    assert '"$INVOCATION_DIR/runner/runner-events.jsonl"' in script
    assert '"$INVOCATION_DIR/traffic/traffic-telemetry.jsonl"' in script
    assert '"$INVOCATION_DIR/traffic/probe-execution.jsonl"' in script
    assert '"$INVOCATION_DIR/observation/execution-observations.jsonl"' in script
    assert "domain-history/physical-frames.jsonl" in script
    assert "domain-history/planning.jsonl.gz" in script
    assert "observation/execution-observations.jsonl" in script
    assert "write_observation_tool" in script
    assert 'exit "$traffic_tool_status"' not in script
    assert 'exit "$runner_tool_status"' not in script
    assert "detached inspection Tools failed" in script
    assert "progression_builder" not in script
    assert "slicer" not in script.lower()


def test_common_generated_control_owns_example_inputs_and_optional_workload(
    tmp_path, compiled_chain
) -> None:
    output, project, _, _ = _generated_output(tmp_path, compiled_chain)
    path = _write_generated_run_script(
        output,
        python_executable="python",
        compiler_tool_directory="bpf-compiler",
        tool_package_parent=project,
        required_input_paths=(
            "inputs/scenario.yaml",
            "inputs/zones.yaml",
        ),
        traffic_workload_path="inputs/traffic-workload.json",
    )
    script = path.read_text(encoding="utf-8")
    assert "inputs/scenario.yaml" in script
    assert "inputs/zones.yaml" in script
    assert "inputs/shells.yaml" not in script
    assert "inputs/ground_stations.yaml" not in script
    assert '--workload "$SCRIPT_DIR/inputs/traffic-workload.json"' in script
    with pytest.raises(RuntimeError, match="runtime input"):
        _write_generated_run_script(
            output,
            python_executable="python",
            compiler_tool_directory="bpf-compiler",
            tool_package_parent=project,
            required_input_paths=("../outside",),
        )


def test_generated_scripts_execute_the_frozen_division_of_responsibility(tmp_path, compiled_chain, monkeypatch):
    """Run generated shell controls with recording executables, never Docker."""
    from hashlib import sha256
    import os
    from pathlib import Path
    import subprocess
    import sys
    from skyseed_v2.compose.launcher import _write_compose_project_scripts

    output, project, _, _ = _generated_output(tmp_path, compiled_chain)
    (project / "tools").mkdir()
    (project / "tools/__init__.py").write_text("")
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
    _write_compose_project_scripts(output, "round-b-bootstrap")
    _write_generated_run_script(output, python_executable=str(executables / "record-python"),
                               compiler_tool_directory="bpf-compiler", tool_package_parent=project,
                               required_input_paths=("inputs/scenario.yaml",))
    artifact_files = tuple((output / "runtime/runner-artifact").iterdir())
    hashes = {path: sha256(path.read_bytes()).hexdigest() for path in artifact_files}

    def invoke(script):
        log.write_text("")
        subprocess.run(("bash", str(output / script)), check=True, capture_output=True, text=True)
        assert hashes == {path: sha256(path.read_bytes()).hexdigest() for path in artifact_files}
        import re
        return [[executable, [re.sub(r"/runtime-runs/[^/]+", "/runtime-runs/INVOCATION", arg) for arg in args]]
                for executable, args in (json.loads(line) for line in log.read_text().splitlines())]

    start = invoke("start.sh")
    assert all(executable == "docker" for executable, _ in start)
    assert start[-1][1][-3:] == ["up", "-d", "--build"]
    execution = invoke("run.sh")
    assert all(executable == "record-python" for executable, _ in execution)
    assert execution[0][1][:2] == ["-m", "skyseed_v2.experiment.execution"]
    assert all("main.py" not in " ".join(args) for _, args in execution)
    assert invoke("launch.sh") == start + execution
    cleanup = invoke("cleanup.sh")
    assert cleanup and all(executable == "docker" for executable, _ in cleanup)
    assert all("label=com.docker.compose.project=round-b-bootstrap" in args
               for _, args in cleanup)
    assert all(args[0] in {"ps", "network"} for _, args in cleanup)


@pytest.mark.parametrize("execution_fails", [False, True])
def test_checkpoint_write_failure_never_skips_runner_cleanup(tmp_path, monkeypatch, execution_fails):
    from types import SimpleNamespace
    from skyseed_v2.experiment import execution as module
    output = tmp_path / 'output'
    probe = output / 'inspection/traffic/probe-plan.json'
    probe.parent.mkdir(parents=True); probe.write_text('{}')
    calls = []
    primary = RuntimeError('execution failed')
    class Runner:
        def __init__(self, **kwargs): pass
        def _enable_event_history(self, path): pass
        def _enable_traffic_telemetry(self, path): pass
        def prepare(self): calls.append('prepare')
        def run(self):
            if execution_fails: raise primary
        def cleanup(self): calls.append('cleanup')
        def inspection_snapshot(self): return {}
    class Traffic:
        def __init__(self, **kwargs): pass
        def start(self, runner): pass
        def stop(self): pass
    def checkpoint(output, destination, project):
        if 'before-cleanup' in destination.name:
            raise OSError('checkpoint write failed')
    monkeypatch.setattr(module, 'BpfRunner', Runner)
    monkeypatch.setattr(module, 'TrafficProbeExecutor', Traffic)
    monkeypatch.setattr(module, 'read_traffic_probe_plan', lambda path: SimpleNamespace(episodes=()))
    monkeypatch.setattr(module, '_read_and_verify_execution', lambda output: (
        {'generated_output_identity':'test', 'runner':{}}, tmp_path/'artifact',
        {'realization_count':1, 'boundary_count':1}))
    monkeypatch.setattr(module, '_record_container_checkpoint', checkpoint)
    for name in ('_write_runtime_invocation_index', '_write_runtime_execution_observations', '_write_runner_inspection_handoff'):
        monkeypatch.setattr(module, name, lambda *a, **kw: None)
    with pytest.raises((OSError, RuntimeError)) as caught:
        module._run_generated_bpf_experiment(output, invocation_directory=output/'inspection/runtime-runs/test')
    assert calls == ['prepare', 'cleanup']
    if execution_fails:
        assert caught.value is primary
        assert any('checkpoint write failed' in note for note in primary.__notes__)
    else:
        assert 'checkpoint write failed' in str(caught.value)
