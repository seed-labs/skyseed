from __future__ import annotations

import inspect
from pathlib import Path

from examples.starlink_gen1_satellite_internet import main as starlink_main
from skyseed_v2.backend.bpf.runner import BpfRunner
from skyseed_v2.experiment import execution
from skyseed_v2.backend.bpf._runner_artifact import (
    _open_runner_artifact,
)


def test_generated_execution_passes_only_artifact_path_to_runner() -> None:
    source = inspect.getsource(execution._run_generated_bpf_experiment)
    assert "runtime_artifact=artifact_path" in source
    assert "future_realization_supplier" not in source
    assert "preparation_lookahead" not in source
    assert "preparation_boundary_depth" not in source
    assert "validated_sequence" not in source


def test_generated_finite_deliveries_are_sealed_before_runner_construction(tmp_path, monkeypatch):
    from contextlib import closing
    from datetime import timedelta
    import json
    from examples.complete_static_emulator.emu import build_emulator
    from examples.complete_static_emulator.simu import create_complete_static_simulator, create_complete_static_planner
    from skyseed_v2.backend.bpf import runner as module

    simulator = create_complete_static_simulator(build_emulator())
    create_complete_static_planner(simulator)
    timeline = simulator.get_timeline()
    _, snapshot, _, _ = starlink_main._run_compiler_stage(
        simulator, tmp_path, delivery_ends=(timeline.start_time + timedelta(seconds=10), timeline.end_time))
    paths = [Path(value["manifest"]) for value in snapshot["delivered_batches"]]
    assert len(paths) == 2
    timestamps = []
    for index, path in enumerate(paths):
        seal = json.loads(path.read_text())
        assert seal["terminal"] is True
        assert seal["delivery"]["final"] is (index == 1)
        with closing(_open_runner_artifact(path)) as artifact:
            timestamps.extend(b.nominal_time for b in artifact.boundaries())
    assert timestamps == list(timeline.iter_timestamps())
    # Construction/registration accepts only persisted finite materials and
    # performs no deployment or kernel work. Preparation is a separate call.
    monkeypatch.setattr(module, "_execute", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("runtime started during construction")))
    runner = BpfRunner(compose_project_name="finite-test", runtime_artifact=paths[0],
                       batch_lead_fraction=.75, batch_capacity_factor=2)
    runner.append_batch(paths[1])
    import pytest
    with pytest.raises(TypeError):
        BpfRunner(compose_project_name="finite-test", runtime_artifact=lambda: paths[0])


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
