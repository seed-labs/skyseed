from __future__ import annotations

from datetime import timedelta
import json

import pytest

from examples.complete_static_emulator.emu import build_emulator
from examples.complete_static_emulator.simu import (
    create_complete_static_simulator,
    run_complete_static_chain,
)
from skyseed_v2.experiment._realization_handoff import (
    _write_realization_handoff,
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
    _write_realization_handoff(
        realizations,
        output / "runtime/realizations",
    )
    timeline = simulator.get_timeline()
    definition = _write_execution_definition(
        output,
        generated_output_identity="round-b-bootstrap",
        preparation_lookahead=None,
        preparation_boundary_depth=None,
        timeline_start=timeline.start_time,
        timeline_end=timeline.end_time,
        observation_interval=timeline.observation_interval,
        project_root=project,
        example_root=example,
        realization_handoff="runtime/realizations/manifest.json",
        realization_producer="common-incremental-realization-producer-v1",
    )
    return output, project, example, definition


def test_generated_definition_is_persisted_only(tmp_path, compiled_chain) -> None:
    output, _, _, path = _generated_output(tmp_path, compiled_chain)
    definition = _read_and_verify_execution_definition(output)
    serialized = json.loads(path.read_text(encoding="utf-8"))
    assert definition == serialized
    assert definition["format_version"] == 2
    assert definition["progression"] == {
        "strategy": "persisted-canonical-realization-handoff",
        "producer": "common-incremental-realization-producer-v1",
    }
    handoff = definition["realization_handoff"]
    assert handoff["manifest"] == "runtime/realizations/manifest.json"
    assert handoff["format_version"] == 3
    assert handoff["encoding"] == "deterministic-gzip-json-v1"
    assert handoff["realization_count"] == 2
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
    manifest = output / "runtime/realizations/manifest.json"
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
    with pytest.raises(ValueError, match="fields|incompatible"):
        _read_and_verify_execution_definition(output)


def test_generated_definition_anchors_exact_handoff_manifest(
    tmp_path, compiled_chain
) -> None:
    output, _, _, _ = _generated_output(tmp_path, compiled_chain)
    manifest = output / "runtime/realizations/manifest.json"
    value = json.loads(manifest.read_text(encoding="utf-8"))
    manifest.write_text(json.dumps(value, indent=4) + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="realization handoff changed"):
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
    assert "runtime/realizations/manifest.json" in script
    assert "domain-history/physical-frames.jsonl" in script
    assert "domain-history/planning.jsonl.gz" in script
    assert "observation/execution-observations.jsonl" in script
    assert "write_observation_tool" in script
    assert 'exit "$traffic_tool_status"' not in script
    assert 'exit "$runner_tool_status"' not in script
    assert "detached inspection Tools failed" in script
    assert "progression_builder" not in script
    assert "slicer" not in script.lower()


def test_persisted_definition_requires_consistent_runner_bounds(
    tmp_path, compiled_chain
) -> None:
    simulator, _, _ = compiled_chain
    timeline = simulator.get_timeline()
    project = tmp_path / "project"
    example = project / "examples/demo"
    (project / "src/skyseed_v2").mkdir(parents=True)
    example.mkdir(parents=True)
    for name in ("emu.py", "simu.py"):
        (example / name).write_text("", encoding="utf-8")
    output = tmp_path / "output"
    (output / "inputs").mkdir(parents=True)
    (output / "runtime").mkdir()
    (output / "runtime/docker-compose.yml").write_text(
        "services: {}\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="boundary depth"):
        _write_execution_definition(
            output,
            generated_output_identity="round-b",
            preparation_lookahead=None,
            preparation_boundary_depth=2,
            timeline_start=timeline.start_time,
            timeline_end=timeline.end_time,
            observation_interval=timedelta(seconds=10),
            project_root=project,
            example_root=example,
            realization_handoff="runtime/realizations/manifest.json",
            realization_producer="producer-v1",
        )
