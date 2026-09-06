from __future__ import annotations

import inspect
from pathlib import Path

from examples.mini_satellite_internet import main as mini_main
from examples.starlink_gen1_satellite_internet import main as starlink_main
from skyseed_v2.backend.bpf.runner import BpfRunner
from skyseed_v2.experiment import execution
from skyseed_v2.backend.bpf._runner_artifact import (
    _RunnerArtifactWriter,
    _open_runner_artifact,
)


def test_generated_execution_passes_only_artifact_path_to_runner() -> None:
    source = inspect.getsource(execution._run_generated_bpf_experiment)
    assert "runtime_artifact=artifact_path" in source
    assert "future_realization_supplier" not in source
    assert "preparation_lookahead" not in source
    assert "preparation_boundary_depth" not in source
    assert "validated_sequence" not in source


def test_runner_production_api_is_precomputed_only() -> None:
    parameters = inspect.signature(BpfRunner).parameters
    assert tuple(parameters) == ("compose_project_name", "runtime_artifact")
    source = inspect.getsource(BpfRunner)
    assert "supplier" not in source
    assert "input_exhausted" not in source
    assert "lookahead" not in source
    assert "boundary_depth" not in source


def test_examples_drain_producer_before_execution_definition() -> None:
    mini = inspect.getsource(mini_main.main)
    starlink = inspect.getsource(starlink_main._run_compiler_stage)
    assert mini.index("produce_next_realization()") < mini.index(
        "_write_runner_artifact("
    )
    assert starlink.index("produce_next_realization()") < starlink.index(
        "writer.finalize()"
    )
    assert "_RunnerArtifactWriter" in inspect.getsource(starlink_main)
    assert "_write_runner_artifact" in inspect.getsource(mini_main)


def test_runner_artifact_open_is_mmap_and_not_rich_reconstruction() -> None:
    source = inspect.getsource(_open_runner_artifact)
    owner = inspect.getsource(
        __import__(
            "skyseed_v2.backend.bpf._runner_artifact",
            fromlist=["_RunnerArtifact"],
        )._RunnerArtifact
    )
    assert "_RunnerArtifact(" in source
    assert "mmap.mmap" in owner
    assert "BpfCompiledRealization._create" not in owner
    assert "planned_operational_state" not in owner
