"""Retained workflow/control tests with real finite computation and sealed output."""
from datetime import timedelta
from hashlib import sha256
import json
from pathlib import Path
from threading import Thread
import time

import pytest
from seedemu.compiler import Docker
from examples.Sky00_tiny_network import tiny_network as tiny
from skyseed_v2.experiment import ExperimentController
from skyseed_v2.experiment._control import _request, control_main
from skyseed_v2.experiment.execution import _read_and_verify_execution_definition


def await_state(predicate):
    end = time.monotonic() + 20
    while not predicate():
        assert time.monotonic() < end, "fixture did not reach the requested state"
        time.sleep(0.01)


def test_live_computation_idle_continue_multiple_deliveries_and_input_seal(tmp_path):
    network = tiny.build_emulator()
    simulation = tiny.create_simulator(network, observation_interval=timedelta(seconds=5))
    output = tmp_path / "retained"
    workflow = ExperimentController(network, simulation, output, entry_path=Path(tiny.__file__),
        input_files=(tiny.ZONE_LAYOUT_FILE,), docker_compiler=Docker(internetMapEnabled=False))
    failures = []
    def serve():
        try:
            workflow.serve(delivery_duration=timedelta(seconds=15))
        except BaseException as error:
            failures.append(error)
    thread = Thread(target=serve)
    thread.start()
    try:
        await_state(lambda: failures or workflow.status()["state"] == "IDLE")
        assert not failures
        assert _request(output, "computation", "status")["published_batches"] == 2
        first = output / "results/compiler/runner-artifact/manifest.json"
        original_hash = sha256(first.read_bytes()).hexdigest()
        control = output / "experiment_control.py"
        control.write_text(control.read_text().replace("ADDITIONAL_SECONDS = 300.0", "ADDITIONAL_SECONDS = 15.0"))
        _read_and_verify_execution_definition(output)
        response = _request(output, "computation", "continue", seconds=30, batch_seconds=15)
        assert response["requested_end"].endswith("00:01:00+00:00")
        await_state(lambda: failures or workflow.status()["state"] == "IDLE" and workflow.status()["published_batches"] == 4)
        assert not failures
        state = workflow.status()
        assert state["producer"]["formal_frame_count"] == 13
        assert state["producer"]["prehistory_frame_count"] == 0
        assert not state["producer"]["planner_finished"]
        assert sha256(first.read_bytes()).hexdigest() == original_hash
        _read_and_verify_execution_definition(output)
    finally:
        workflow.cancel_computation()
        thread.join()
    assert not failures
    assert json.loads((output / "results/experiment/domain-history-manifest.json").read_text())["counts"]["formal_frames"] == 13
    with pytest.raises(RuntimeError, match="original live process"):
        _request(output, "computation", "continue", seconds=15)


@pytest.mark.parametrize("arguments,expected", [
    (["continue"], {"seconds": None, "until": "2026-01-01T00:20:00+00:00", "batch_seconds": 90}),
    (["continue", "--seconds", "30", "--batch-seconds", "15"], {"seconds": 30, "until": None, "batch_seconds": 15}),
    (["continue", "--until", "2026-01-01T00:25:00+00:00"], {"seconds": None, "until": "2026-01-01T00:25:00+00:00", "batch_seconds": 90}),
])
def test_control_explicit_arguments_override_editable_defaults(monkeypatch, tmp_path, arguments, expected):
    from skyseed_v2.experiment import _control
    calls = []
    monkeypatch.setattr(_control, "_request", lambda out, owner, command, **kw: calls.append((owner, command, kw)))
    assert control_main(tmp_path, additional_seconds=60, target_end="2026-01-01T00:20:00+00:00",
                        batch_seconds=90, argv=arguments) == 0
    assert calls == [("computation", "continue", expected)]
