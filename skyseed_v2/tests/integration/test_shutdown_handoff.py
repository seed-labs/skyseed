"""Real Tiny materials/control dispatch; Events select handoff interleavings."""
from contextlib import contextmanager
from datetime import timedelta
import json
from threading import Event, Thread

import pytest

from skyseed_v2.experiment import execution
from skyseed_v2.experiment._control import _request, _serve_control, control_main
from tests.integration.test_experiment_workflow import configured


def reached(event):
    assert event.wait(20), "test owner did not reach its barrier"


@contextmanager
def serving(workflow, releases, *, cleanup=True, run=True):
    failures = []

    def serve():
        try:
            workflow.serve(delivery_duration=timedelta(seconds=30), run=run, cleanup=cleanup)
        except BaseException as error:
            failures.append(error)

    thread = Thread(target=serve)
    thread.start()
    try:
        yield failures
    finally:
        for event in releases:
            event.set()
        workflow.cancel_computation()
        thread.join(20)
        assert not thread.is_alive(), "test owners did not settle"


@pytest.mark.parametrize("window", ["startup", "run-entry", "runtime-claim"])
@pytest.mark.parametrize("cleanup", [True, False])
def test_shutdown_retained_before_runtime_claim(tmp_path, monkeypatch, capsys, window, cleanup):
    output = tmp_path / "output"
    _, workflow = configured(output)
    blocked, release, tearing_down, release_cleanup = (Event() for _ in range(4))
    order = []
    request = execution._request

    def runtime_request(out, owner, command, **kwargs):
        if command == "runtime_claim" and window == "runtime-claim":
            # Endpoint already exists, but ownership has not transferred.
            blocked.set()
            reached(release)
        return request(out, owner, command, **kwargs)

    def control(script):
        order.append(script.name)
        if script.name == "deploy.sh":
            if window == "startup":
                blocked.set()
                reached(release)
            order.append("startup-settled")
        elif script.name == "run.sh":
            if window == "run-entry":
                blocked.set()
                reached(release)
            execution._run_generated_bpf_experiment(output)
        else:
            tearing_down.set()
            reached(release_cleanup)

    def forbidden(**kwargs):
        raise AssertionError("shutdown must prevent Runner construction")

    monkeypatch.setattr(execution, "_request", runtime_request)
    monkeypatch.setattr(execution, "_run_generated_control", control)
    monkeypatch.setattr(execution, "Runner", forbidden)
    with serving(workflow, [release, release_cleanup], cleanup=cleanup) as failures:
        reached(blocked)
        capsys.readouterr()
        for _ in range(2):
            assert control_main(output, argv=["shutdown"]) == 0
            response = json.loads(capsys.readouterr().out)
            assert response["shutdown_requested"] is True
            assert response["shutdown_complete"] is False
        assert workflow._stop.is_set()
        assert "cleanup.sh" not in order
        release.set()
        if cleanup:
            reached(tearing_down)
            state = _request(output, "computation", "status")
            assert state["shutdown_requested"] and not state["shutdown_complete"]
            assert control_main(output, argv=["shutdown"]) == 0
            assert order.count("cleanup.sh") == 1
    assert not failures
    expected = ["deploy.sh", "startup-settled"]
    if window != "startup":
        expected.append("run.sh")
    if cleanup:
        expected.append("cleanup.sh")
    assert order == expected
    assert workflow.status()["shutdown_complete"] is True
    # Unavailable is unknown completion, never a new launch or a false success claim.
    capsys.readouterr()
    assert control_main(output, argv=["shutdown"]) == 0
    response = json.loads(capsys.readouterr().out)
    assert not response["shutdown_requested"]
    assert response["shutdown_complete"] is None
    assert order == expected


@pytest.mark.parametrize("window", ["claim-return", "active", "release", "released"])
def test_shutdown_reaches_runtime_after_claim(tmp_path, monkeypatch, capsys, window):
    output = tmp_path / "output"
    _, workflow = configured(output)
    blocked, release = Event(), Event()
    order, received = [], []
    request = execution._request

    def runtime_request(out, owner, command, **kwargs):
        if command == "runtime_release" and window == "release":
            blocked.set()
            reached(release)
        result = request(out, owner, command, **kwargs)
        if command == "runtime_claim" and window == "claim-return":
            # Ownership is committed; Runner construction has not happened yet.
            blocked.set()
            reached(release)
        if command == "runtime_release" and window == "released":
            # Runtime is drained/released; the still-live workflow retains intent
            # until outer cleanup, without calling back into a closing runtime.
            blocked.set()
            reached(release)
        return result

    class Runner:
        def cancel_runtime(self):
            received.append("cancel")
        def status(self):
            return {}

    def invocation(out, *, controls, **kwargs):
        controls["runner"] = Runner()
        order.append("invocation")
        if window == "active":
            blocked.set()
            reached(controls["cancel"])
            reached(release)  # Represents owned graceful drain, not a deadline.
        order.append("runtime-drained")

    def control(script):
        order.append(script.name)
        if script.name == "run.sh":
            execution._run_generated_bpf_experiment(output)

    monkeypatch.setattr(execution, "_request", runtime_request)
    monkeypatch.setattr(execution, "_run_runtime_invocation", invocation)
    monkeypatch.setattr(execution, "_run_generated_control", control)
    with serving(workflow, [release]) as failures:
        reached(blocked)
        capsys.readouterr()
        for _ in range(2):
            assert control_main(output, argv=["shutdown"]) == 0
            assert json.loads(capsys.readouterr().out)["shutdown_complete"] is False
        assert _request(output, "runtime", "status")["cancel_requested"] is (window != "released")
        assert "cleanup.sh" not in order
    assert not failures
    assert workflow.status()["shutdown_complete"]
    assert order.count("cleanup.sh") == 1
    if window == "claim-return":
        assert "invocation" not in order
    else:
        assert received == ([] if window == "released" else ["cancel", "cancel"])
        assert order.index("runtime-drained") < order.index("cleanup.sh")


@pytest.mark.parametrize("cancel_computation", [False, True])
@pytest.mark.parametrize("cleanup", [False, True])
def test_normal_start_and_computation_only_cancel_allow_runtime(
    tmp_path, monkeypatch, cancel_computation, cleanup,
):
    output = tmp_path / "output"
    _, workflow = configured(output)
    started, release, ran = Event(), Event(), Event()
    order = []

    def control(script):
        order.append(script.name)
        if script.name == "deploy.sh":
            started.set()
            reached(release)
        elif script.name == "run.sh":
            execution._run_generated_bpf_experiment(output)

    def invocation(*args, **kwargs):
        ran.set()

    monkeypatch.setattr(execution, "_run_generated_control", control)
    monkeypatch.setattr(execution, "_run_runtime_invocation", invocation)
    with serving(workflow, [release], cleanup=cleanup) as failures:
        reached(started)
        if cancel_computation:
            state = _request(output, "computation", "cancel-computation")
            assert not state["shutdown_requested"]
        release.set()
        reached(ran)
    assert not failures
    assert order == ["deploy.sh", "run.sh"] + (["cleanup.sh"] if cleanup else [])
    assert not workflow.status()["shutdown_requested"]


@pytest.mark.parametrize("state", [None, "CLOSED", "CANCELLED"])
def test_sealed_materials_execute_without_live_computation(tmp_path, monkeypatch, state):
    _, workflow = configured(tmp_path / "output")
    output = workflow.generate()
    invoked = []
    monkeypatch.setattr(execution, "_run_runtime_invocation", lambda *a, **kw: invoked.append(True))
    if state is None:
        execution._run_generated_bpf_experiment(output)
    else:
        # A computation-only endpoint has no whole-session execution ownership.
        with _serve_control(output, "computation", lambda request: {
            "state": state, "runtime_managed": False, "shutdown_requested": False,
        }):
            execution._run_generated_bpf_experiment(output)
    assert invoked == [True]


def test_dispatch_failure_is_not_reported_as_unavailable(tmp_path, capsys):
    def failed(request):
        raise RuntimeError("owned cancellation failed")
    with _serve_control(tmp_path, "computation", failed):
        with pytest.raises(SystemExit) as caught:
            control_main(tmp_path, argv=["shutdown"])
    assert caught.value.code == 1
    assert "owned cancellation failed" in capsys.readouterr().err


def test_computation_failure_still_cancels_independent_runtime(tmp_path, monkeypatch):
    output = tmp_path / "output"
    _, workflow = configured(output)
    published, fail, active, cancelled = (Event() for _ in range(4))
    primary = RuntimeError("computation failed after publication")
    runtime_failures = []

    def next_delivery(self, completed, initial):
        published.set()
        reached(fail)
        raise primary

    def invocation(out, *, controls, **kwargs):
        active.set()
        reached(controls["cancel"])
        cancelled.set()

    def runtime():
        try:
            execution._run_generated_bpf_experiment(output)
        except BaseException as error:
            runtime_failures.append(error)

    monkeypatch.setattr("skyseed_v2.experiment.producer.Producer._next_delivery", next_delivery)
    monkeypatch.setattr(execution, "_run_runtime_invocation", invocation)
    thread = Thread(target=runtime)
    with serving(workflow, [fail], run=False) as failures:
        reached(published)
        thread.start()
        try:
            reached(active)
            fail.set()
            reached(cancelled)
        finally:
            fail.set()
            thread.join(20)
            assert not thread.is_alive()
    assert not runtime_failures
    assert failures == [primary]
    assert workflow.status()["state"] == "FAILED"
    assert not workflow.status()["shutdown_requested"]


def test_cancel_racing_runner_construction_is_delivered(tmp_path, monkeypatch):
    _, workflow = configured(tmp_path / "output")
    output = workflow.generate()
    ready, setting_cancel, constructed, release_cancel, cancelled = (Event() for _ in range(5))
    failures = []

    class PausedCancel(Event):
        def set(self):
            setting_cancel.set()
            reached(release_cancel)
            super().set()

    flags = iter([Event(), PausedCancel()])
    monkeypatch.setattr(execution, "Event", lambda: next(flags))

    class Runner:
        def cancel_runtime(self):
            cancelled.set()
        def status(self):
            return {}

    def invocation(out, *, controls, **kwargs):
        ready.set()
        reached(setting_cancel)
        controls["runner"] = Runner()
        # Match the real construction handshake. Cancellation publication is
        # paused, so only the dispatcher's subsequent reference read can see us.
        assert not controls["cancel"].is_set()
        constructed.set()
        reached(cancelled)

    def run():
        try:
            execution._run_generated_bpf_experiment(output)
        except BaseException as error:
            failures.append(error)

    def cancel():
        try:
            control_main(output, argv=["shutdown"])
        except BaseException as error:
            failures.append(error)

    monkeypatch.setattr(execution, "_run_runtime_invocation", invocation)
    runtime, client = Thread(target=run), Thread(target=cancel)
    runtime.start()
    try:
        reached(ready)
        client.start()
        reached(constructed)
        release_cancel.set()
        reached(cancelled)
    finally:
        release_cancel.set()
        cancelled.set()
        runtime.join(20)
        if client.ident is not None:
            client.join(20)
        assert not runtime.is_alive() and not client.is_alive()
    assert not failures


def test_shutdown_preserves_startup_and_cleanup_failures(tmp_path, monkeypatch):
    output = tmp_path / "output"
    _, workflow = configured(output)
    started, release = Event(), Event()
    order = []
    primary = RuntimeError("startup failed after shutdown request")

    def control(script):
        order.append(script.name)
        if script.name == "deploy.sh":
            started.set()
            reached(release)
            raise primary
        if script.name == "cleanup.sh":
            raise RuntimeError("cleanup failed")
        raise AssertionError("unexpected runtime")

    monkeypatch.setattr(execution, "_run_generated_control", control)
    with serving(workflow, [release]) as failures:
        reached(started)
        assert control_main(output, argv=["shutdown"]) == 0
    assert failures == [primary]
    assert "cleanup failed" in primary.__notes__[0]
    assert order == ["deploy.sh", "cleanup.sh"]
    assert not workflow.status()["shutdown_complete"]
    assert workflow.status()["execution_error"] == str(primary)
