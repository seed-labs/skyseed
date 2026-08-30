from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path
from shutil import copytree

import pytest

from examples.mini_satellite_internet import simu
from skyseed_v2.experiment.execution import (
    _construct_progression,
    _read_and_verify_execution_definition,
    _write_execution_definition,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_ROOT = PROJECT_ROOT / "examples/mini_satellite_internet"


def _write_definition(
    output: Path,
    *,
    project_root: Path,
    example_root: Path,
    builder_module: str = "examples.mini_satellite_internet.simu",
    builder_callable: str = "create_mini_satellite_internet_progression",
) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    inputs = output / "inputs"
    runtime = output / "runtime"
    inputs.mkdir(parents=True, exist_ok=True)
    runtime.mkdir(parents=True, exist_ok=True)
    for name, content in (
        ("scenario.yaml", "scenario: test\n"),
        ("zones.yaml", "zones: []\n"),
        ("traffic-workload.json", "{}\n"),
    ):
        path = inputs / name
        if not path.exists():
            path.write_text(content, encoding="utf-8")
    compose = runtime / "docker-compose.yml"
    if not compose.exists():
        compose.write_text("services: {}\n", encoding="utf-8")
    (runtime / "compose-project-name").write_text(
        "continuous-bootstrap-test\n",
        encoding="utf-8",
    )
    return _write_execution_definition(
        output,
        builder_module=builder_module,
        builder_callable=builder_callable,
        generated_output_identity="continuous-bootstrap-test",
        generation_lookahead=timedelta(seconds=300),
        preparation_lookahead=timedelta(seconds=60),
        preparation_boundary_depth=8,
        timeline_start=simu.DEFAULT_START_TIME,
        timeline_end=simu.DEFAULT_END_TIME,
        slice_interval=simu.DEFAULT_SLICE_INTERVAL,
        project_root=project_root,
        example_root=example_root,
    )


def test_generated_definition_restores_shared_builder_without_precomputation(
    tmp_path,
) -> None:
    output = tmp_path / "output"
    copytree(EXAMPLE_ROOT / "output/inputs", output / "inputs")
    path = _write_definition(
        output,
        project_root=PROJECT_ROOT,
        example_root=EXAMPLE_ROOT,
    )

    definition = _read_and_verify_execution_definition(output)
    slicer, planner = _construct_progression(output, definition)
    value = json.loads(path.read_text(encoding="utf-8"))

    assert value["kind"] == "skyseed-v2-generated-bpf-execution"
    assert value["backend"] == "bpf"
    assert value["progression_builder"] == {
        "module": "examples.mini_satellite_internet.simu",
        "callable": "create_mini_satellite_internet_progression",
        "input_directory": "inputs",
    }
    assert value["simulation"]["generation_lookahead_seconds"] == 300
    assert value["runner"] == {
        "preparation_lookahead_seconds": 60,
        "preparation_boundary_depth": 8,
    }
    assert "files" not in value["source_fingerprint"]
    assert [scope["label"] for scope in value["source_fingerprint"]["scopes"]] == [
        "skyseed",
        "example-progression",
        "seedemu",
    ]
    assert {scope["label"] for scope in value["generated_inputs"]["scopes"]} == {
        "input-snapshots",
        "docker-compose",
    }
    input_scope = next(
        scope
        for scope in value["generated_inputs"]["scopes"]
        if scope["label"] == "input-snapshots"
    )
    assert input_scope["excludes"] == ["traffic-workload.json"]
    assert slicer.get_latest_slice_id() == 0
    assert planner._processed_through_slice_id == 0


def test_source_fingerprint_detects_modify_add_and_delete(tmp_path) -> None:
    project = tmp_path / "project"
    example = project / "examples/demo"
    for relative, content in (
        ("src/skyseed_v2/engine.py", "VALUE = 1\n"),
        ("tools/view.py", "VALUE = 2\n"),
        ("examples/demo/emu.py", "VALUE = 2\n"),
        ("examples/demo/simu.py", "VALUE = 3\n"),
        ("examples/demo/main.py", "VALUE = 4\n"),
        ("examples/demo/traffic_workload.py", "VALUE = 5\n"),
    ):
        path = project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    output = tmp_path / "output"
    _write_definition(
        output,
        project_root=project,
        example_root=example,
        builder_module="not.loaded.for.this.test",
        builder_callable="unused",
    )

    assert _read_and_verify_execution_definition(output)["backend"] == "bpf"
    changed = example / "simu.py"
    original = changed.read_text(encoding="utf-8")
    changed.write_text("VALUE = 30\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match=r"source changed; rerun main\.py"):
        _read_and_verify_execution_definition(output)
    changed.write_text(original, encoding="utf-8")
    assert _read_and_verify_execution_definition(output)["backend"] == "bpf"

    added = project / "src/skyseed_v2/new_runtime_source.py"
    added.write_text("VALUE = 31\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match=r"source changed; rerun main\.py"):
        _read_and_verify_execution_definition(output)
    added.unlink()
    assert _read_and_verify_execution_definition(output)["backend"] == "bpf"

    changed.unlink()
    with pytest.raises(RuntimeError, match=r"source changed; rerun main\.py"):
        _read_and_verify_execution_definition(output)
    changed.write_text(original, encoding="utf-8")
    assert _read_and_verify_execution_definition(output)["backend"] == "bpf"


@pytest.mark.parametrize(
    "relative",
    (
        "inputs/scenario.yaml",
        "inputs/zones.yaml",
        "runtime/docker-compose.yml",
    ),
)
def test_generated_runtime_input_modification_fails_closed(
    tmp_path, relative
) -> None:
    project = tmp_path / "project"
    example = project / "examples/demo"
    for source in (
        project / "src/skyseed_v2/engine.py",
        example / "emu.py",
        example / "simu.py",
    ):
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("VALUE = 1\n", encoding="utf-8")
    output = tmp_path / "output"
    _write_definition(output, project_root=project, example_root=example)

    changed = output / relative
    changed.write_text(changed.read_text(encoding="utf-8") + "# drift\n")
    with pytest.raises(RuntimeError, match=r"runtime input changed; rerun main\.py"):
        _read_and_verify_execution_definition(output)


def test_generated_input_addition_and_deletion_fail_closed(tmp_path) -> None:
    project = tmp_path / "project"
    example = project / "examples/demo"
    for source in (
        project / "src/skyseed_v2/engine.py",
        example / "emu.py",
        example / "simu.py",
    ):
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("VALUE = 1\n", encoding="utf-8")
    output = tmp_path / "output"
    _write_definition(output, project_root=project, example_root=example)

    added = output / "inputs/extra.snapshot"
    added.write_text("extra\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match=r"runtime input changed; rerun main\.py"):
        _read_and_verify_execution_definition(output)
    added.unlink()
    (output / "inputs/scenario.yaml").unlink()
    with pytest.raises(RuntimeError, match=r"runtime input changed; rerun main\.py"):
        _read_and_verify_execution_definition(output)


def test_workload_and_tool_drift_do_not_block_normal_generated_execution(
    tmp_path,
) -> None:
    project = tmp_path / "project"
    example = project / "examples/demo"
    for relative in (
        "src/skyseed_v2/engine.py",
        "tools/view.py",
        "examples/demo/emu.py",
        "examples/demo/simu.py",
        "examples/demo/main.py",
        "examples/demo/traffic_workload.py",
    ):
        source = project / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("VALUE = 1\n", encoding="utf-8")
    output = tmp_path / "output"
    _write_definition(output, project_root=project, example_root=example)

    (output / "inputs/traffic-workload.json").write_text(
        '{"changed": true}\n', encoding="utf-8"
    )
    (project / "tools/view.py").write_text("VALUE = 2\n", encoding="utf-8")
    (example / "traffic_workload.py").write_text(
        "VALUE = 2\n", encoding="utf-8"
    )
    (example / "main.py").write_text("VALUE = 2\n", encoding="utf-8")

    assert _read_and_verify_execution_definition(output)["backend"] == "bpf"
