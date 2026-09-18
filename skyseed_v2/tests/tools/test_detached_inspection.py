from __future__ import annotations

import multiprocessing
from pathlib import Path

import pytest

from examples.Sky00_tiny_network.tiny_network import build_emulator
from examples.Sky00_tiny_network.tiny_network import create_simulator as create_complete_static_simulator
from tests._support import drain_simulator as run_complete_static_chain
from skyseed_v2.experiment.output.observation import _ExecutionObserver
from tools._detached_inspection import _DetachedInspectionExecutor
from tools.planner.reader import read_planner_history


@pytest.fixture(scope="module")
def complete_chain(tmp_path_factory):
    root = tmp_path_factory.mktemp("detached-tools")
    simulator = create_complete_static_simulator(build_emulator())
    plans, realizations, _ = run_complete_static_chain(
        simulator, evidence_path=root / "history"
    )
    evidence = read_planner_history(root / "history/planner/planning.jsonl.gz")
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
        "performance",
        {"observation_path": observation_path},
        tmp_path / "performance",
    )
    records = executor.join()

    assert {value["task"] for value in records} == {
        "planner",
        "compiler",
        "performance",
    }
    assert all(value["status"] == "COMPLETE" for value in records)
    assert all(value["wall_seconds"] > 0 for value in records)
    assert all(value["peak_rss_bytes"] > 0 for value in records)
    assert (tmp_path / "planner/planner-inspection.json").is_file()
    assert (tmp_path / "compiler/bpf-compiler-inspection.json").is_file()
    assert (tmp_path / "performance/performance-summary.json").is_file()
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
        {"planner_history_path": root / "history/planner/planning.jsonl.gz"},
        tmp_path / "planner",
    )
    records = executor.join()

    assert records[0]["status"] == "COMPLETE", records[0].get("message")
    assert (tmp_path / "planner/checksums.sha256").is_file()


@pytest.mark.skipif(
    "fork" not in multiprocessing.get_all_start_methods(),
    reason="detached worker contract requires fork",
)
def test_executor_reads_compact_compiler_history_without_runtime_payload(
    tmp_path, complete_chain
) -> None:
    root, _, _realizations, _, _ = complete_chain
    executor = _DetachedInspectionExecutor(tmp_path / "control", max_workers=1)
    executor.submit(
        "compiler",
        {
            "compiler_history_path": root / "history/compiler/compiler.jsonl",
        },
        tmp_path / "compiler",
    )
    records = executor.join()

    assert records[0]["status"] == "COMPLETE", records[0].get("message")
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


def test_cancellation_drains_workers_and_preserves_other_staging(tmp_path, monkeypatch):
    from threading import Thread
    from tools import _detached_inspection as module
    context = multiprocessing.get_context('fork')
    entered, release = context.Event(), context.Event()
    def build(path, directory):
        entered.set()
        release.wait()
        (directory/'worker-finished').write_text('complete')
        return {'status': 'PASS'}
    monkeypatch.setattr(module, '_build_observation', build)
    output = tmp_path/'performance'
    foreign = tmp_path/'.performance.staging-another-invocation'
    foreign.mkdir()
    (foreign/'owned-elsewhere').write_text('keep')
    executor = _DetachedInspectionExecutor(tmp_path/'control')
    executor.submit('performance', {'observation_path': tmp_path/'source'}, output)
    entered.wait()
    cancelled = Thread(target=executor.terminate)
    cancelled.start()
    executor._cancelled.wait()
    assert cancelled.is_alive()
    assert executor._active[0]['process'].is_alive()
    with pytest.raises(RuntimeError, match='closed'):
        executor.submit('compiler', {}, tmp_path/'compiler')
    release.set()
    cancelled.join()
    assert executor.records()[0]['status'] == 'CANCELLED'
    assert not output.exists()
    assert (foreign/'owned-elsewhere').read_text() == 'keep'
    assert list(tmp_path.glob('.performance.staging-*')) == [foreign]


def test_conflicting_publisher_rejected_without_removing_another_invocation(tmp_path, monkeypatch):
    from tools import _detached_inspection as module
    context = multiprocessing.get_context('fork')
    release = context.Event()
    def build(path, directory):
        release.wait()
        (directory/'index.html').write_text('complete')
        return {'status':'PASS'}
    monkeypatch.setattr(module, '_build_observation', build)
    first = _DetachedInspectionExecutor(tmp_path/'control')
    second = _DetachedInspectionExecutor(tmp_path/'control')
    first.submit('performance', {'observation_path': tmp_path/'source'}, tmp_path/'performance')
    with pytest.raises(RuntimeError, match='active publisher'):
        second.submit('performance', {}, tmp_path/'performance')
    second.terminate()
    assert first._task_directory.is_dir()
    release.set()
    assert first.join()[0]['status'] == 'COMPLETE'
    assert (tmp_path/'performance/index.html').read_text() == 'complete'


def test_staging_cleanup_failure_does_not_abandon_other_accepted_workers(tmp_path, monkeypatch):
    from threading import Thread
    from tools import _detached_inspection as module
    context = multiprocessing.get_context('fork')
    entered, release = context.Event(), context.Event()
    def worker(task, source, output, staging, result_path, cancelled):
        staging.mkdir()
        if task == 'planner':
            entered.set()
            release.wait()
        result_path.write_text('{"status":"COMPLETE","tool_result":{"status":"PASS"}}')
    monkeypatch.setattr(module, '_worker_entry', worker)
    remove = module.shutil.rmtree
    def fail_owned_staging(path, *args, **kwargs):
        if Path(path).name.startswith('.performance.staging-'):
            raise OSError('injected local staging cleanup failure')
        return remove(path, *args, **kwargs)
    monkeypatch.setattr(module.shutil, 'rmtree', fail_owned_staging)
    executor = _DetachedInspectionExecutor(tmp_path/'control')
    executor.submit('performance', {}, tmp_path/'performance')
    executor.submit('planner', {}, tmp_path/'planner')
    entered.wait()
    cancelled = Thread(target=executor.terminate)
    cancelled.start()
    executor._cancelled.wait()
    release.set()
    cancelled.join()
    assert not executor._active
    records = {r['task']:r for r in executor.records()}
    assert records['planner']['status'] == 'COMPLETE'
    assert records['performance']['status'] == 'FAILED'
    assert 'staging cleanup failure' in records['performance']['message']
    assert not (tmp_path/'control').exists()
    # Failed local removal remains explicit and retains the owned path for repair.
    remaining = list(tmp_path.glob('.performance.staging-*'))
    assert len(remaining) == 1
    remove(remaining[0])
