from __future__ import annotations

import multiprocessing
from pathlib import Path

import pytest

from examples.complete_static_emulator.emu import build_emulator
from examples.complete_static_emulator.simu import (
    create_complete_static_simulator,
    run_complete_static_chain,
)
from skyseed_v2.shared._execution_observation import _ExecutionObserver
from skyseed_v2.experiment._realization_handoff import (
    _write_realization_handoff,
)
from tools._detached_inspection import _DetachedInspectionExecutor
from tools.planner.reader import read_planner_history


@pytest.fixture(scope="module")
def complete_chain(tmp_path_factory):
    root = tmp_path_factory.mktemp("detached-tools")
    simulator = create_complete_static_simulator(build_emulator())
    plans, realizations, _ = run_complete_static_chain(
        simulator, evidence_path=root / "history"
    )
    evidence = read_planner_history(root / "history/planning.jsonl.gz")
    observer = _ExecutionObserver({"scenario": "detached-test"})
    observer.record(
        component="planner",
        stage="publication",
        phase="steady",
        duration_seconds=0.1,
        scope={
            "timestamp": "2026-01-01T00:00:00+00:00",
            "plan_id": plans[0].plan_id,
        },
        work={"plans": 1},
    )
    observation_path = observer.write_jsonl(root / "observations.jsonl")
    return root, plans, realizations, evidence, observation_path


@pytest.mark.skipif(
    "fork" not in multiprocessing.get_all_start_methods(),
    reason="detached worker contract requires fork",
)
def test_executor_builds_planner_compiler_and_observation_bundles(
    tmp_path, complete_chain
) -> None:
    _, plans, realizations, evidence, observation_path = complete_chain
    executor = _DetachedInspectionExecutor(tmp_path / "control", max_workers=2)
    executor.submit(
        "planner", {"evidence": evidence}, tmp_path / "planner"
    )
    executor.submit(
        "compiler",
        {"plans": plans, "realizations": realizations},
        tmp_path / "compiler",
    )
    executor.submit(
        "observation",
        {"observation_path": observation_path},
        tmp_path / "observation",
    )
    records = executor.join()

    assert {value["task"] for value in records} == {
        "planner",
        "compiler",
        "observation",
    }
    assert all(value["status"] == "COMPLETE" for value in records)
    assert all(value["wall_seconds"] > 0 for value in records)
    assert all(value["peak_rss_bytes"] > 0 for value in records)
    assert (tmp_path / "planner/planner-inspection.json").is_file()
    assert (tmp_path / "compiler/bpf-compiler-inspection.json").is_file()
    assert (tmp_path / "observation/observation-summary.json").is_file()
    assert not (tmp_path / "control").exists()


@pytest.mark.skipif(
    "fork" not in multiprocessing.get_all_start_methods(),
    reason="detached worker contract requires fork",
)
def test_executor_accepts_producer_planning_history_directly(
    tmp_path, complete_chain
) -> None:
    root, _, _, _, _ = complete_chain
    executor = _DetachedInspectionExecutor(tmp_path / "control", max_workers=1)
    executor.submit(
        "planner",
        {"planner_history_path": root / "history/planning.jsonl.gz"},
        tmp_path / "planner",
    )
    records = executor.join()

    assert records[0]["status"] == "COMPLETE"
    assert records[0]["input_evidence"]["status"] == "COMPLETE"
    assert (tmp_path / "planner/checksums.sha256").is_file()


@pytest.mark.skipif(
    "fork" not in multiprocessing.get_all_start_methods(),
    reason="detached worker contract requires fork",
)
def test_executor_reads_persisted_plan_and_realization_sequences(
    tmp_path, complete_chain
) -> None:
    root, _, realizations, _, _ = complete_chain
    manifest = _write_realization_handoff(
        realizations, tmp_path / "realizations"
    )
    executor = _DetachedInspectionExecutor(tmp_path / "control", max_workers=1)
    executor.submit(
        "compiler",
        {
            "planner_history_path": root / "history/planning.jsonl.gz",
            "realization_manifest": manifest,
        },
        tmp_path / "compiler",
    )
    records = executor.join()

    assert records[0]["status"] == "COMPLETE"
    assert (tmp_path / "compiler/bpf-compiler-inspection.json").is_file()


def test_executor_rejects_unknown_or_duplicate_tasks(tmp_path) -> None:
    executor = _DetachedInspectionExecutor(tmp_path / "control", max_workers=1)
    with pytest.raises(ValueError, match="unsupported"):
        executor.submit("unknown", {}, tmp_path / "unknown")
    executor.terminate()


def test_detached_executor_has_no_domain_progression_calls() -> None:
    source = (
        Path(__file__).resolve().parents[2] / "tools/_detached_inspection.py"
    ).read_text()
    assert "compute_observation(" not in source
    assert "produce_next_realization(" not in source
    assert "compile_bpf(" not in source
