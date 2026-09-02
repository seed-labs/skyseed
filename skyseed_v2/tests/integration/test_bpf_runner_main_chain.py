from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from examples.complete_static_emulator.emu import build_emulator
from examples.complete_static_emulator.simu import (
    create_complete_static_simulator,
    run_complete_static_chain,
)
from skyseed_v2.experiment import execution
from skyseed_v2.experiment._realization_handoff import (
    _write_realization_handoff,
)
from skyseed_v2.experiment.traffic.probe_plan import (
    TrafficProbePlanBuilder,
    write_traffic_probe_plan,
)


def _write_stub_probe_plan(output: Path) -> None:
    path = output / "inspection/traffic/probe-plan.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}\n", encoding="utf-8")


def _runtime_definition(*, identity: str = "round-b-runtime") -> dict[str, object]:
    return {
        "runner": {
            "preparation_lookahead_seconds": 2,
            "preparation_boundary_depth": 1,
        },
        "realization_handoff": {
            "manifest": "runtime/realizations/manifest.json",
        },
        "generated_output_identity": identity,
    }


def test_generated_runner_consumes_only_persisted_realizations(
    tmp_path, monkeypatch, capsys
) -> None:
    simulator = create_complete_static_simulator(build_emulator())
    plans, realizations, _ = run_complete_static_chain(simulator)
    output = tmp_path / "output"
    manifest = _write_realization_handoff(
        realizations,
        output / "runtime/realizations",
    )
    write_traffic_probe_plan(
        TrafficProbePlanBuilder().build(plans, realizations),
        output / "inspection/traffic/probe-plan.json",
    )
    observed: dict[str, object] = {}

    class _Runner:
        def __init__(self, **arguments):
            observed["constructor"] = arguments
            self._supplier = arguments["future_realization_supplier"]
            self._values = []
            self._state = "new"

        def _enable_event_history(self, path):
            observed["events"] = path

        def _enable_traffic_telemetry(self, path):
            observed["telemetry"] = path

        def prepare(self):
            while True:
                value = self._supplier()
                if value is None:
                    break
                self._values.append(value)
            self._state = "prepared"
            observed["realizations"] = tuple(self._values)

        def run(self):
            self._state = "completed"

        def cleanup(self):
            self._state = "cleaned"

        def inspection_snapshot(self):
            return {
                "runner": {
                    "lifecycle_state": self._state,
                    "consumed_plan_ids": [
                        value.source_plan_id for value in self._values
                    ],
                }
            }

    class _Traffic:
        def __init__(self, **arguments):
            observed["traffic_constructor"] = arguments

        def start(self, runner):
            observed["traffic_started"] = runner

        def stop(self):
            observed["traffic_stopped"] = True

    monkeypatch.setattr(execution, "BpfRunner", _Runner)
    monkeypatch.setattr(execution, "TrafficProbeExecutor", _Traffic)
    monkeypatch.setattr(
        execution,
        "_read_and_verify_execution_definition",
        lambda _output: {
            "runner": {
                "preparation_lookahead_seconds": None,
                "preparation_boundary_depth": None,
            },
            "realization_handoff": {
                "manifest": str(manifest.relative_to(output)),
            },
            "generated_output_identity": "round-b-runtime",
        },
    )

    execution._run_generated_bpf_experiment(output)

    assert observed["realizations"] == realizations
    constructor = observed["constructor"]
    assert constructor["compose_project_name"] == "round-b-runtime"
    assert constructor["preparation_lookahead"] is None
    assert constructor["preparation_boundary_depth"] is None
    assert observed["traffic_stopped"] is True
    handoff = json.loads(
        (output / "inspection/runner/runner-invocation.json").read_text(
            encoding="utf-8"
        )
    )
    assert handoff["runtime_outcome"]["status"] == "completed"
    assert handoff["runner_snapshot"]["runner"]["consumed_plan_ids"] == [
        value.source_plan_id for value in realizations
    ]
    observation_rows = [
        json.loads(line)
        for line in (
            output / "inspection/observation/execution-observations.jsonl"
        )
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert {
        (value["component"], value["stage"])
        for value in observation_rows[1:]
    } == {
        ("runner", "runner-total"),
        ("traffic", "traffic-total"),
    }
    assert all(
        value["duration_role"] == "rollup"
        for value in observation_rows[1:]
    )

    def _fail_side_work(*_args, **_kwargs):
        raise RuntimeError("injected detached inspection failure")

    monkeypatch.setattr(
        execution,
        "_write_runtime_execution_observations",
        _fail_side_work,
    )
    monkeypatch.setattr(
        execution,
        "_write_runner_inspection_handoff",
        _fail_side_work,
    )
    monkeypatch.setattr(
        execution,
        "_write_runtime_invocation_index",
        _fail_side_work,
    )
    execution._run_generated_bpf_experiment(
        output,
        invocation_directory=output / "inspection/runtime-runs/injected",
    )
    stderr = capsys.readouterr().err
    assert "Observation side work failed" in stderr
    assert "inspection handoff side work failed" in stderr
    assert "invocation index side work failed" in stderr


def test_runtime_invocation_index_preserves_each_raw_run(tmp_path: Path) -> None:
    root = tmp_path / "inspection/runtime-runs"
    execution._write_runtime_invocation_index(
        root,
        invocation_id="run-a",
        status="running",
    )
    execution._write_runtime_invocation_index(
        root,
        invocation_id="run-a",
        status="completed",
    )
    first = (root / "run-a/invocation.json").read_bytes()
    execution._write_runtime_invocation_index(
        root,
        invocation_id="run-b",
        status="failed",
    )

    assert (root / "run-a/invocation.json").read_bytes() == first
    assert json.loads(first)["raw_evidence"] == {
        "resource_samples": "runner/runner-events.jsonl",
        "runner_events": "runner/runner-events.jsonl",
        "runner_outcome": "runner/runner-invocation.json",
        "traffic_execution": "traffic/probe-execution.jsonl",
        "traffic_telemetry": "traffic/traffic-telemetry.jsonl",
    }
    assert json.loads((root / "latest.json").read_text(encoding="utf-8")) == {
        "artifact_type": "skyseed-runtime-latest-view",
        "format_version": 1,
        "invocation": "run-b/invocation.json",
        "invocation_id": "run-b",
        "status": "failed",
    }


def test_generated_execution_owns_lifecycle_and_detached_handoffs(
    monkeypatch,
    tmp_path: Path,
) -> None:
    output = tmp_path / "output"
    _write_stub_probe_plan(output)
    calls: list[object] = []

    class _Supplier:
        def __init__(self, manifest: Path) -> None:
            calls.append(("supplier", manifest))
            self._terminal = False

        def validated_sequence(self):
            calls.append("validated-sequence")
            return ()

        def snapshot(self):
            return {"consumed_count": 0}

        def __call__(self):
            self._terminal = True
            calls.append("supplier-terminal")
            return None

    class _Runner:
        def __init__(self, **facts) -> None:
            calls.append(("runner", facts))
            self._supplier = facts["future_realization_supplier"]
            self._state = "new"

        def _enable_event_history(self, path: Path) -> None:
            calls.append(("event-history", path))

        def _enable_traffic_telemetry(self, path: Path) -> None:
            calls.append(("traffic-telemetry", path))

        def prepare(self) -> None:
            calls.append("prepare")
            assert self._supplier() is None
            self._state = "prepared"

        def run(self) -> None:
            calls.append("run")
            self._state = "completed"

        def cleanup(self) -> None:
            calls.append("cleanup")
            self._state = "cleaned"

        def inspection_snapshot(self):
            calls.append("inspection-snapshot")
            return {"runner": {"lifecycle_state": self._state}}

    class _Traffic:
        def __init__(self, **facts) -> None:
            calls.append(("traffic-executor", facts))

        def start(self, runner) -> None:
            calls.append(("traffic-start", runner))

        def stop(self) -> None:
            calls.append("traffic-stop")

    monkeypatch.setattr(execution, "_PersistedRealizationSupplier", _Supplier)
    monkeypatch.setattr(execution, "BpfRunner", _Runner)
    monkeypatch.setattr(execution, "TrafficProbeExecutor", _Traffic)
    monkeypatch.setattr(
        execution,
        "read_traffic_probe_plan",
        lambda _path: SimpleNamespace(episodes=()),
    )
    monkeypatch.setattr(
        execution,
        "_read_and_verify_execution_definition",
        lambda _output: _runtime_definition(identity="lifecycle-proof"),
    )

    invocation = output / "inspection/runtime-runs/run-0001"
    execution._run_generated_bpf_experiment(
        output,
        invocation_directory=invocation,
    )

    assert calls.index("prepare") < next(
        index
        for index, value in enumerate(calls)
        if isinstance(value, tuple) and value[0] == "traffic-start"
    ) < calls.index("run") < calls.index("traffic-stop") < calls.index(
        "cleanup"
    )
    supplier = next(value for value in calls if value[0] == "supplier")
    assert supplier[1] == output / "runtime/realizations/manifest.json"
    runner_facts = next(value[1] for value in calls if value[0] == "runner")
    assert runner_facts["compose_project_name"] == "lifecycle-proof"
    assert runner_facts["preparation_lookahead"] == timedelta(seconds=2)
    assert runner_facts["preparation_boundary_depth"] == 1
    event_path = next(value[1] for value in calls if value[0] == "event-history")
    telemetry_path = next(
        value[1] for value in calls if value[0] == "traffic-telemetry"
    )
    assert event_path == invocation / "runner/runner-events.jsonl"
    assert telemetry_path == invocation / "traffic"

    handoff = json.loads(
        (invocation / "runner/runner-invocation.json").read_text(
            encoding="utf-8"
        )
    )
    assert handoff["invocation_id"] == "run-0001"
    assert handoff["runtime_outcome"]["status"] == "completed"
    assert handoff["runner_snapshot"]["runner"]["lifecycle_state"] == "cleaned"
    rows = [
        json.loads(line)
        for line in (
            invocation / "observation/execution-observations.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ]
    assert {
        (row["component"], row["stage"], row["duration_role"])
        for row in rows[1:]
    } == {
        ("runner", "runner-total", "rollup"),
        ("traffic", "traffic-total", "rollup"),
    }
    latest = json.loads(
        (output / "inspection/runtime-runs/latest.json").read_text(
            encoding="utf-8"
        )
    )
    assert latest["invocation_id"] == "run-0001"
    assert latest["status"] == "completed"


@pytest.mark.parametrize(
    (
        "runner_failure",
        "traffic_failure",
        "cleanup_failure",
        "handoff_failure",
        "expected_type",
        "expected_message",
        "expected_status",
        "expected_traffic_status",
    ),
    (
        (
            "interrupt",
            False,
            False,
            False,
            KeyboardInterrupt,
            "^$",
            "interrupted",
            "completed",
        ),
        (
            "runtime",
            True,
            True,
            True,
            RuntimeError,
            "primary runtime",
            "failed",
            "failed",
        ),
        (
            None,
            True,
            False,
            False,
            RuntimeError,
            "traffic failed",
            "failed",
            "failed",
        ),
        (
            None,
            False,
            True,
            False,
            RuntimeError,
            "cleanup failed",
            "cleanup-failed",
            "completed",
        ),
        (
            None,
            False,
            False,
            True,
            None,
            None,
            "completed",
            "completed",
        ),
    ),
)
def test_generated_execution_preserves_round_b_error_precedence(
    monkeypatch,
    tmp_path: Path,
    capsys,
    runner_failure,
    traffic_failure,
    cleanup_failure,
    handoff_failure,
    expected_type,
    expected_message,
    expected_status,
    expected_traffic_status,
) -> None:
    output = tmp_path / "output"
    _write_stub_probe_plan(output)
    captured: list[tuple[Path, object, dict[str, object]]] = []

    class _Supplier:
        def __init__(self, _manifest: Path) -> None:
            pass

        def validated_sequence(self):
            return ()

        def snapshot(self):
            return {"consumed_count": 0}

        def __call__(self):
            return None

    class _Runner:
        def __init__(self, **_facts) -> None:
            pass

        def _enable_event_history(self, _path: Path) -> None:
            pass

        def _enable_traffic_telemetry(self, _path: Path) -> None:
            pass

        def prepare(self) -> None:
            pass

        def run(self) -> None:
            if runner_failure == "interrupt":
                raise KeyboardInterrupt
            if runner_failure == "runtime":
                raise RuntimeError("primary runtime")

        def cleanup(self) -> None:
            if cleanup_failure:
                raise RuntimeError("cleanup failed")

        def inspection_snapshot(self):
            return {"runner": {}}

    class _Traffic:
        def __init__(self, **_facts) -> None:
            pass

        def start(self, _runner) -> None:
            pass

        def stop(self) -> None:
            if traffic_failure:
                raise RuntimeError("traffic failed")

    def _write_handoff(path, snapshot, outcome, **_kwargs) -> None:
        captured.append((path, snapshot, outcome))
        if handoff_failure:
            raise RuntimeError("inspection handoff failed")

    monkeypatch.setattr(execution, "_PersistedRealizationSupplier", _Supplier)
    monkeypatch.setattr(execution, "BpfRunner", _Runner)
    monkeypatch.setattr(execution, "TrafficProbeExecutor", _Traffic)
    monkeypatch.setattr(
        execution,
        "read_traffic_probe_plan",
        lambda _path: SimpleNamespace(episodes=()),
    )
    monkeypatch.setattr(
        execution,
        "_read_and_verify_execution_definition",
        lambda _output: _runtime_definition(),
    )
    monkeypatch.setattr(execution, "_write_runner_inspection_handoff", _write_handoff)

    if expected_type is None:
        execution._run_generated_bpf_experiment(output)
        caught = None
    else:
        with pytest.raises(expected_type, match=expected_message) as caught:
            execution._run_generated_bpf_experiment(output)

    assert captured[0][2]["status"] == expected_status
    assert captured[0][2]["traffic"]["status"] == expected_traffic_status
    stderr = capsys.readouterr().err
    if handoff_failure:
        assert "without replacing domain execution" in stderr
    if runner_failure == "runtime" and traffic_failure:
        assert caught is not None
        assert any(
            "secondary Traffic probe execution failure" in note
            for note in caught.value.__notes__
        )
