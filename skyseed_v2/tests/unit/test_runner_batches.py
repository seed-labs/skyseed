from skyseed_v2.backend.runner import deployment as deployment_module
"""Finite deliveries preserve one semantic history and bounded runtime ownership."""
from contextlib import closing
from datetime import timedelta
from types import SimpleNamespace
import json

import pytest

from examples.Sky00_tiny_network.tiny_network import build_emulator
from examples.Sky00_tiny_network.tiny_network import create_simulator as create_complete_static_simulator
from skyseed_v2.backend.execution_materials.sealing import _RunnerArtifactWriter
from skyseed_v2.backend.execution_materials.codec import _open_runner_artifact
from skyseed_v2.backend.bpf.dataplane.runtime import _TcBpfRuntimeRealization
from skyseed_v2.backend.runner import lifecycle as runner_module
from skyseed_v2.experiment.producer import Producer
from skyseed_v2.experiment.output.observation import _ExecutionObserver
from skyseed_v2.simulator.planner.values import plan_transition_fingerprint
from test_runner_runtime_optimization import _Kernel


def _produce(directory, offsets):
    simulator = create_complete_static_simulator(build_emulator())
    planner = simulator.get_planner()
    observer = _ExecutionObserver()
    planner._set_execution_observer(observer)
    timeline = simulator.get_timeline()
    transitions, paths, previous = [], [], None
    with Producer(timeline, simulator.get_physical_world(),
            simulator.get_physical_link_model(), planner, evidence_path=directory / "history") as producer:
        for index, offset in enumerate(offsets):
            writer = _RunnerArtifactWriter(directory / f"batch-{index}", continuation=previous,
                                           final=index + 1 == len(offsets), execution_observer=observer)
            cutoff = timeline.start_time + timedelta(seconds=offset)
            while (value := producer.produce_next_realization(delivery_end=cutoff)) is not None:
                transitions.extend(plan_transition_fingerprint(t) for t in producer.latest_plan.transitions)
                writer.append(value)
            paths.append(writer.finalize())
            previous = writer
        assert producer.snapshot()["domain_history_sealed"]
    observer._require_healthy()
    return transitions, paths


@pytest.fixture(scope="module")
def deliveries(tmp_path_factory):
    root = tmp_path_factory.mktemp("delivered-batches")
    continuous, whole = _produce(root / "whole", (30,))
    batched, paths = _produce(root / "batches", (0, 10, 30))
    return continuous, batched, whole, paths


def test_delivery_flush_preserves_continuous_transitions_and_compiler_lineage(deliveries):
    continuous, batched, whole, paths = deliveries
    assert batched == continuous
    timestamps = []
    previous = None
    for index, path in enumerate(paths):
        with closing(_open_runner_artifact(path)) as artifact:
            timestamps.extend(boundary.nominal_time for boundary in artifact.boundaries())
            snapshot = artifact._snapshot
            assert snapshot["delivery"]["index"] == index
            if previous is not None:
                assert snapshot["lineage"][0]["source_previous_plan_id"] == previous["lineage"][-1]["source_plan_id"]
                assert snapshot["lineage"][0]["input_state_fingerprint"] == previous["lineage"][-1]["output_state_fingerprint"]
            previous = snapshot
    assert len(timestamps) == len(set(timestamps)) == 4
    assert all(b - a == timedelta(seconds=10) for a, b in zip(timestamps, timestamps[1:]))


def test_append_rejects_reordered_replaced_and_terminal_deliveries(deliveries):
    _, _, _, paths = deliveries
    runner = runner_module.Runner(compose_project_name="batches", runtime_artifact=paths[0])
    with pytest.raises(ValueError, match="non-contiguous"):
        runner.append_batch(paths[2])
    runner.append_batch(paths[1])
    with pytest.raises(RuntimeError, match="one next"):
        runner.append_batch(paths[2])
    runner = runner_module.Runner(compose_project_name="batches", runtime_artifact=paths[2])
    with pytest.raises(ValueError, match="non-contiguous"):
        runner.append_batch(paths[1])


def test_three_deliveries_reuse_foundation_ports_and_reclaim_only_old_readers(monkeypatch, deliveries):
    _, _, _, paths = deliveries
    kernel = _Kernel(monkeypatch)
    artifacts = [_open_runner_artifact(path) for path in paths]
    first = artifacts[0]
    bindings = {node: SimpleNamespace(pid=i + 100, process_start_time_ticks=i + 200, netns_inode=i + 300)
                for i, node in enumerate(first.physical_world_projection.node_ids)}
    runtime = _TcBpfRuntimeRealization(executor=kernel, pin_root="/pins", bindings=bindings,
                                       continuation_enabled=True, batch_capacity_factor=4)
    runtime.initialize(first.physical_world_projection, zone_prefixes=first.zone_prefixes,
                       runtime_capacity_demand=first.runtime_capacity_demand, dataplane=first.dataplane)
    identities = {}
    activated = []
    try:
        for index, artifact in enumerate(artifacts):
            plans = runtime.prepare_all(tuple(artifact.boundaries()), logical_ports=artifact.logical_ports, retirements=artifact.retirements,
                **({} if index == 0 else {"runtime_capacity_demand": artifact.runtime_capacity_demand}))
            for connection, ports in artifact.logical_ports.items():
                assert identities.setdefault(connection, ports) == ports
            for plan in plans:
                runtime.activate(plan)
                activated.append(plan.nominal_time)
                if len(runtime._resident_generations) > 1:
                    old = min(runtime._resident_generations)
                    readers = kernel.maps[1000000]
                    readers[old] = 1
                    assert old not in runtime.reclaim().get("reclaimed_generations", ())
                    assert old in runtime._resident_generations
                    readers[old] = 0
                while runtime.reclaim().get("work_units"):
                    pass
            assert runtime._resident_generations == {runtime._active.generation}
        assert len(activated) == 4
        assert sum(action == "initialize" for action, _ in kernel.actions) == 1
        assert len(runtime._generation_reservations) == 1
    finally:
        runtime.cleanup()
        for artifact in artifacts:
            artifact.close()


def _runner_with_real_maintenance(monkeypatch, paths, *, keep_alive=False):
    import time
    from skyseed_v2.backend.runner.deployment import _RunnerRuntimeBinding
    kernel = _Kernel(monkeypatch)
    with closing(_open_runner_artifact(paths[0])) as artifact:
        nodes = artifact.physical_world_projection.node_ids
    bindings = {node: _RunnerRuntimeBinding(node, "service", "container", "name", "image", i + 100, i + 200, i + 300, "now")
                for i, node in enumerate(nodes)}
    monkeypatch.setattr(deployment_module._Deployment, "discover", lambda self, projection: bindings)
    monkeypatch.setattr(runner_module.Runner, "_record_resource_health_checkpoint", lambda *args: None)
    monkeypatch.setattr(runner_module._PrivilegedHelper, "start", lambda self: None)
    monkeypatch.setattr(runner_module._PrivilegedHelper, "stop", lambda self: None)
    monkeypatch.setattr(runner_module._PrivilegedHelper, "execute", lambda self, action, request: kernel.execute(action, request))
    monotonic, sleep = time.monotonic, time.sleep
    monkeypatch.setattr(runner_module, "monotonic", lambda: monotonic() * 10)
    monkeypatch.setattr(runner_module, "sleep", lambda seconds: sleep(seconds / 10))
    runner = runner_module.Runner(compose_project_name="batches", runtime_artifact=paths[0], batch_capacity_factor=4, keep_alive=keep_alive)
    return runner, kernel


def test_automatic_runner_loads_two_successors_without_reset_or_duplicate_b0(monkeypatch, deliveries, capsys):
    paths = deliveries[3]
    runner, kernel = _runner_with_real_maintenance(monkeypatch, paths)
    record = runner._record
    def observe(event, **values):
        record(event, **values)
        if event == "batch_activation_start" and values["batch_index"] == 1:
            runner.append_batch(paths[2])
    monkeypatch.setattr(runner, "_record", observe)
    runner.append_batch(paths[1])
    try:
        runner.prepare()
        runner.run()
        events = runner.inspection_snapshot()["events"]
        active = [event for event in events if event["event"] == "activation_end"]
        assert len(active) == 4
        assert sum(event["preparation"] for event in active) == 1
        ready = [event for event in events if event["event"] == "batch_ready"]
        assert len(ready) == 2
        assert all(event["readiness_margin_seconds"] > 0 for event in ready)
        assert sum(action == "initialize" for action, _ in kernel.actions) == 1
        assert runner._active_count == 4
        assert runner._batch_index == 2
        feedback = capsys.readouterr().err
        assert feedback.index('Runtime preparation starting') < feedback.index('Runtime preparation succeeded') < feedback.index('formal clock starting')
        assert 'Finite execution' in feedback and 'Runtime progression ended' in feedback
        assert 'Retained execution' not in feedback
    finally:
        runner.cleanup()


def test_missing_delivery_waits_and_pause_is_independent(monkeypatch, deliveries, capsys):
    from threading import Thread
    runner, _ = _runner_with_real_maintenance(monkeypatch, deliveries[3])
    errors = []
    def run():
        try:
            runner.run()
        except BaseException as error:
            errors.append(error)
    try:
        runner.prepare()
        thread = Thread(target=run)
        thread.start()
        _await(lambda: "material" in runner.status()["wait_reasons"])
        assert runner._active_count == 1
        runner.pause()
        _await(lambda: runner.status()["paused"])
        runner.append_batch(deliveries[3][1])
        _await(lambda: runner.status()["next_batch"] == "READY")
        assert runner._active_count == 1
        assert runner.status()["paused"]
        runner.resume()
        _await(lambda: runner._active_count == 2)
        _await(lambda: "material" in runner.status()["wait_reasons"])
        runner.resume()
        assert runner.status()["waiting_for_material"]
        runner.cancel_runtime()
        thread.join()
        assert not errors
        assert runner._state == "prepared"
        assert runner.status()["clock"]["shift_seconds"] > 0
        feedback = capsys.readouterr().err
        starts = sum(e['event'] == 'material_wait_start' for e in runner.inspection_snapshot()['events'])
        assert feedback.count('Runtime waiting for execution materials') == starts == 2
        assert feedback.count('Runtime stop requested') == 1
        assert 'Runtime resource cleanup succeeded' not in feedback
    finally:
        runner.cancel_runtime()
        runner.cleanup()


def _await(predicate):
    import time
    end = time.monotonic() + 10  # Test failure bound only, never a production deadline.
    while not predicate():
        assert time.monotonic() < end, "fixture did not reach the expected state"
        time.sleep(0.005)


def test_retained_final_range_reports_wait_once_until_cancel(monkeypatch, deliveries, capsys):
    from threading import Thread
    import time
    paths = deliveries[3]
    runner, _ = _runner_with_real_maintenance(monkeypatch, paths, keep_alive=True)
    record = runner._record
    def observe(event, **values):
        record(event, **values)
        if event == 'batch_activation_start' and values['batch_index'] == 1:
            runner.append_batch(paths[2])
    monkeypatch.setattr(runner, '_record', observe)
    runner.append_batch(paths[1])
    failures = []
    def run():
        try: runner.run()
        except BaseException as error: failures.append(error)
    runner.prepare()
    thread = Thread(target=run)
    thread.start()
    try:
        _await(lambda: failures or runner._batch_index == 2 and 'material' in runner.status()['wait_reasons'])
        time.sleep(.1)  # Exercise several existing wait polls.
        assert thread.is_alive() and not failures
        feedback = capsys.readouterr().err
        assert 'Retained execution' in feedback
        assert feedback.count('Runtime waiting for execution materials') == 1
        assert 'Runtime progression ended' not in feedback
    finally:
        runner.cancel_runtime()
        thread.join()
        runner.cleanup()
    assert not failures
    feedback = capsys.readouterr().err
    assert feedback.index('Runtime stop requested') < feedback.index('Runtime progression ended')


def test_preparation_may_finish_after_target(monkeypatch, deliveries):
    from threading import Thread, Event
    runner, _ = _runner_with_real_maintenance(monkeypatch, deliveries[3])
    runner.prepare()
    entered, release = Event(), Event()
    original = runner._prepare_delivery
    def delayed(*a, **kw):
        entered.set()
        release.wait()
        return original(*a, **kw)
    monkeypatch.setattr(runner, "_prepare_delivery", delayed)
    errors = []
    def run():
        try:
            runner.run()
        except BaseException as error:
            errors.append(error)
    runner.append_batch(deliveries[3][1])
    thread = Thread(target=run)
    thread.start()
    try:
        assert entered.wait(5)
        _await(lambda: "material" in runner.status()["wait_reasons"])
        release.set()
        _await(lambda: runner._active_count == 2)
        ready = [event for event in runner._events if event["event"] == "batch_ready"]
        assert ready[0]["readiness_margin_seconds"] < 0
        assert runner._active_frontier.isoformat() == runner._batch_end.isoformat()
    finally:
        runner.cancel_runtime()
        release.set()
        thread.join()
        runner.cleanup()
    assert not errors


def test_cancel_during_preparation_joins_owner_before_cleanup(monkeypatch, deliveries):
    from threading import Thread, Event
    runner, _ = _runner_with_real_maintenance(monkeypatch, deliveries[3])
    runner.prepare()
    entered, release, finished = Event(), Event(), Event()
    original = runner._prepare_delivery
    def delayed(*args, **kwargs):
        entered.set()
        release.wait()
        try:
            return original(*args, **kwargs)
        finally:
            finished.set()
    monkeypatch.setattr(runner, "_prepare_delivery", delayed)
    errors = []
    def run():
        try:
            runner.run()
        except BaseException as error:
            errors.append(error)
    runner.append_batch(deliveries[3][1])
    thread = Thread(target=run)
    thread.start()
    try:
        assert entered.wait(5)
        assert runner.status()["next_batch"] == "PREPARING"
        runner.cancel_runtime()
        _await(lambda: runner._maintenance_stop)
        assert thread.is_alive() and not finished.is_set()
        assert runner._active_count == 1
    finally:
        runner.cancel_runtime()
        release.set()
        thread.join()
        assert finished.is_set()
        runner.cleanup()
    assert not errors
    assert runner._state == "cleaned"
