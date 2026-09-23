"""Reset reuses materials and deployment, but constructs a fresh lifecycle owner."""
from pathlib import Path
from threading import Event, Thread
import json

from tests.integration.test_experiment_workflow import configured
from tests.integration.test_retained_workflow import await_state
from skyseed.experiment import execution
from skyseed.experiment._control import _request


def test_reset_orders_traffic_shutdown_cleanup_and_fresh_invocation(tmp_path, monkeypatch):
    _, workflow = configured(tmp_path / "output")
    output = workflow.generate()
    before = (output / "results/compiler/runner-artifact/manifest.json").read_bytes()
    order, runners, failures = [], [], []

    class Runner:
        def __init__(self, **kwargs):
            self.index = len(runners)
            self.done = Event()
            self.state = "new"
            runners.append(self)
            order.append((self.index, "construct"))
        def _bind_execution_input(self, **facts):
            pass
        def _enable_event_history(self, path):
            self.events = path
        def _enable_traffic_telemetry(self, path):
            pass
        def _record(self, event, **facts):
            with self.events.open("a") as stream:
                stream.write(json.dumps({"event": event, **facts}) + "\n")
        def prepare(self):
            self.state = "prepared"
            order.append((self.index, "prepare"))
        def run(self):
            order.append((self.index, "run"))
            self.done.wait()
        def cancel_runtime(self):
            self.done.set()
        def status(self):
            return {"lifecycle_state": self.state, "accepts_batch": False}
        def cleanup(self):
            self.state = "cleaned"
            order.append((self.index, "cleanup"))
        def inspection_snapshot(self):
            return {"runner": self.status(), "events": ()}

    class Traffic:
        def __init__(self, **kwargs):
            self.index = len(runners) - 1
        def start(self, runner):
            order.append((self.index, "traffic_start"))
        def stop(self):
            order.append((self.index, "traffic_stop"))
        def snapshot(self):
            return {}

    monkeypatch.setattr(execution, "Runner", Runner)
    monkeypatch.setattr("tools.generation.traffic.probe_execution.TrafficExecutor", Traffic)
    monkeypatch.setattr(execution, "_record_container_checkpoint", lambda *a: None)
    def run():
        try:
            execution._run_generated_bpf_experiment(output)
        except BaseException as error:
            failures.append(error)
    thread = Thread(target=run)
    thread.start()
    try:
        await_state(lambda: failures or (0, "run") in order)
        assert not failures
        await_state(lambda: _request(output, "runtime", "status")["invocation_id"] is not None)
        first_id = _request(output, "runtime", "status")["invocation_id"]
        _request(output, "runtime", "reset")
        await_state(lambda: failures or (1, "run") in order)
        assert not failures
        await_state(lambda: _request(output, "runtime", "status")["invocation_id"] != first_id)
        second_id = _request(output, "runtime", "status")["invocation_id"]
        assert first_id != second_id
        assert order.index((0, "traffic_stop")) < order.index((0, "cleanup")) < order.index((1, "construct"))
        assert (output / f"runs/{first_id}/runner/runner-invocation.json").exists()
        assert (output / "results/compiler/runner-artifact/manifest.json").read_bytes() == before
        _request(output, "runtime", "cancel-runtime")
    finally:
        for runner in runners:
            runner.cancel_runtime()
        thread.join()
    assert not failures
    events = [json.loads(line) for line in (output / ".control/runtime-events.jsonl").read_text().splitlines()]
    assert any(row["reset_from"] == first_id and row["invocation_id"] == second_id for row in events)
