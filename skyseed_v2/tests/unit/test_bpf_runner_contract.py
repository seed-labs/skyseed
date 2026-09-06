from __future__ import annotations

from datetime import datetime, timedelta, timezone
import inspect
import json
from pathlib import Path
import subprocess
from threading import Event, Thread
from time import sleep as real_sleep
from types import SimpleNamespace

import pytest

from skyseed_v2.backend import bpf
from skyseed_v2.backend.bpf import runner as runner_module
from skyseed_v2.backend.bpf._runner.event_history import _NonBlockingEventHistory
from skyseed_v2.backend.bpf._runner import process_incarnation
from skyseed_v2.backend.bpf._runner.runtime_realization import (
    _IncompleteActivationError,
)
from skyseed_v2.backend.bpf.compiled_realization import _PhysicalWorldProjection
from skyseed_v2.backend.bpf.runner import (
    BpfRunner,
    _NominalBoundary,
    _PrivilegedRuntimeCommandError,
    _RuntimeBinding,
)
from skyseed_v2.shared import NodeId


START = datetime(2026, 1, 1, tzinfo=timezone.utc)
SATELLITE = NodeId(65001, "satellite", "satellite-0")
PROJECTION = _PhysicalWorldProjection((SATELLITE,), ())


def _boundary(offset: int = 0) -> _NominalBoundary:
    timestamp = START + timedelta(seconds=10 * offset)
    return _NominalBoundary(
        nominal_time=timestamp,
        operations=(),
        physical_world_projection=PROJECTION,
        source_realization_indexes=(0,),
        coverage=(("plan", timestamp.isoformat(), timestamp.isoformat()),),
        inspected_constraint_count=0,
        source_constraint_count=0,
        applied_constraint_count=0,
        outgoing_constraint_count=0,
        later_constraint_count=0,
        constraint_reasons=(),
        constraint_edges=(),
        operation_counts=(
            ("resource", 0), ("parameter", 0),
            ("forwarding", 0), ("zone-edge", 0),
        ),
        order_sha256="0" * 64,
    )

class _Artifact:
    def __init__(self, boundaries=(_boundary(),)):
        self._boundaries = tuple(boundaries)
        self.physical_world_projection = PROJECTION
        self.zone_prefixes = ()
        self.closed = False
        self.snapshot_calls = 0

    @property
    def timeline_end(self):
        return self._boundaries[-1].nominal_time.isoformat()

    @property
    def physical_world_node_count(self):
        return len(self.physical_world_projection.node_ids)

    def boundaries(self):
        return self._boundaries

    @property
    def boundary_count(self):
        return len(self._boundaries)

    @property
    def logical_ports(self):
        return {}

    def snapshot(self):
        if self.closed:
            raise RuntimeError("artifact is closed")
        self.snapshot_calls += 1
        return {
            "format_version": 2,
            "encoding": "typed-packed-runtime-tables-v2",
            "boundary_count": len(self._boundaries),
            "realization_count": 1,
            "mmap": True,
            "rich_compiled_realizations_reconstructed": False,
        }

    @property
    def runtime_capacity_demand(self):
        return {
            "schema_version": 1,
            "boundary_count": len(self._boundaries),
            "generation_count": 1,
            "physical_connection_high_water": 0,
            "interface_binding_high_water": 0,
            "interface_identity_derivation": (
                "blake2s-40-bit-connection-key-v1"
            ),
            "collision_audit": {
                "result": "passed",
                "audited_transition_count": len(self._boundaries),
                "derivation": "blake2s-40-bit-connection-key-v1",
            },
            "per_node": [
                {
                    "key": SATELLITE.value,
                    "cumulative_active_link_entries": 0,
                    "cumulative_route_entries": 1,
                    "maximum_generation_active_link_entries": 0,
                    "maximum_generation_route_entries": 1,
                    "physical_interface_high_water": 0,
                    "terminal_active_link_entries": 0,
                    "terminal_route_entries": 1,
                }
            ],
            "terminal": {
                "active_connection_count": 0,
                "generation_active": True,
                "per_node_count": 1,
            },
        }

    def close(self):
        self.closed = True

def test_public_runner_surface_and_constructor_validation_remain_closed(
    monkeypatch,
) -> None:
    assert bpf.__all__ == [
        "BpfCompiledActivationBoundary",
        "BpfCompiledRealization",
        "BpfRunner",
        "compile_bpf",
    ]
    assert {name for name in vars(BpfRunner) if not name.startswith("_")} == {
        "prepare",
        "activate_next",
        "run",
        "cleanup",
        "inspection_snapshot",
    }
    monkeypatch.setattr(runner_module, "_open_runner_artifact", lambda path: _Artifact())
    with pytest.raises(TypeError, match="must be a string"):
        BpfRunner(compose_project_name=object(), runtime_artifact=Path("/tmp/a"))
    with pytest.raises(ValueError, match="single line"):
        BpfRunner(compose_project_name="runner\nother", runtime_artifact=Path("/tmp/a"))
    with pytest.raises(TypeError, match="pathlib.Path"):
        BpfRunner(compose_project_name="runner", runtime_artifact="/tmp/a")

def test_runner_discovers_project_nodes_with_one_privileged_incarnation_batch(
    monkeypatch,
) -> None:
    artifact = _Artifact()
    monkeypatch.setattr(runner_module, "_open_runner_artifact", lambda path: artifact)
    runner = BpfRunner(
        compose_project_name="runner-project",
        runtime_artifact=Path("/tmp/a"),
    )
    inspected = [
        {
            "Id": "auxiliary",
            "Name": "/auxiliary",
            "Image": "aux-image",
            "Config": {"Labels": {runner_module._PROJECT_LABEL: "runner-project"}},
            "State": {"Running": False, "Pid": 0, "StartedAt": ""},
        },
        {
            "Id": "container-1",
            "Name": "/satellite",
            "Image": "node-image",
            "Config": {"Labels": {
                runner_module._PROJECT_LABEL: "runner-project",
                runner_module._SERVICE_LABEL: "satellite",
                runner_module._ENTITY_TYPE_LABEL: "Satellite",
                runner_module._ASN_LABEL: "65001",
                runner_module._SATELLITE_ID_LABEL: "satellite-0",
            }},
            "State": {"Running": True, "Pid": 123, "StartedAt": "now"},
        },
    ]
    commands = []

    def execute(command, **_facts):
        commands.append(tuple(command))
        assert "exec" not in command
        return subprocess.CompletedProcess(command, 0, "auxiliary\ncontainer-1\n", "")

    monkeypatch.setattr(runner_module, "_execute", execute)
    monkeypatch.setattr(BpfRunner, "_inspect", lambda self, ids: inspected)
    captures = []
    monkeypatch.setattr(
        BpfRunner,
        "_capture_privileged_incarnations",
        lambda self, *, image_id, pids: (
            captures.append((image_id, pids)) or [(77, 456)]
        ),
    )
    bindings = runner._discover_runtime_bindings(PROJECTION)
    assert set(bindings) == {SATELLITE}
    assert bindings[SATELLITE].process_start_time_ticks == 77
    assert bindings[SATELLITE].netns_inode == 456
    assert captures == [("node-image", (123,))]
    assert len(commands) == 1

def test_runner_rejects_incomplete_physical_world_binding_set(monkeypatch) -> None:
    second = NodeId(65001, "satellite", "satellite-1")
    artifact = _Artifact()
    monkeypatch.setattr(runner_module, "_open_runner_artifact", lambda path: artifact)
    runner = BpfRunner(compose_project_name="runner", runtime_artifact=Path("/tmp/a"))
    monkeypatch.setattr(
        runner_module,
        "_execute",
        lambda command, **facts: subprocess.CompletedProcess(command, 0, "container\n", ""),
    )
    monkeypatch.setattr(BpfRunner, "_inspect", lambda self, ids: [{
        "Id": "container", "Name": "/satellite", "Image": "image",
        "Config": {"Labels": {
            runner_module._PROJECT_LABEL: "runner",
            runner_module._SERVICE_LABEL: "satellite",
            runner_module._ENTITY_TYPE_LABEL: "Satellite",
            runner_module._ASN_LABEL: "65001",
            runner_module._SATELLITE_ID_LABEL: "satellite-0",
        }},
        "State": {"Running": True, "Pid": 123, "StartedAt": "now"},
    }])
    with pytest.raises(RuntimeError, match="missing="):
        runner._discover_runtime_bindings(_PhysicalWorldProjection((SATELLITE, second), ()))

def test_privileged_incarnation_capture_is_one_bounded_docker_run(monkeypatch) -> None:
    artifact = _Artifact()
    monkeypatch.setattr(runner_module, "_open_runner_artifact", lambda path: artifact)
    runner = BpfRunner(compose_project_name="runner", runtime_artifact=Path("/tmp/a"))
    calls = []

    def execute(command, **facts):
        calls.append((tuple(command), facts.get("input_text")))
        payload = {
            "status": "ok",
            "result": {
                "capture_mode": "single-privileged-batch",
                "requested_pid_count": 2,
                "captured_pid_count": 2,
                "incarnations": [
                    {"pid": 102, "process_start_time_ticks": 12, "netns_inode": 22},
                    {"pid": 101, "process_start_time_ticks": 11, "netns_inode": 21},
                ],
            },
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr(runner_module, "_execute", execute)
    assert runner._capture_privileged_incarnations(
        image_id="node-image", pids=(101, 102)
    ) == [(11, 21), (12, 22)]
    assert len(calls) == 1
    command_line, input_text = calls[0]
    assert command_line[:4] == ("docker", "run", "--rm", "--interactive")
    assert "--privileged" in command_line
    assert command_line[command_line.index("--pid") + 1] == "host"
    assert "exec" not in command_line
    assert command_line[-1] == "capture_incarnations"
    assert json.loads(input_text) == {"pids": [101, 102]}

def test_parent_rejects_duplicate_privileged_netns_evidence(monkeypatch) -> None:
    artifact = _Artifact()
    monkeypatch.setattr(runner_module, "_open_runner_artifact", lambda path: artifact)
    runner = BpfRunner(compose_project_name="runner", runtime_artifact=Path("/tmp/a"))
    payload = {
        "status": "ok",
        "result": {
            "capture_mode": "single-privileged-batch",
            "requested_pid_count": 2,
            "captured_pid_count": 2,
            "incarnations": [
                {"pid": 101, "process_start_time_ticks": 11, "netns_inode": 21},
                {"pid": 102, "process_start_time_ticks": 12, "netns_inode": 21},
            ],
        },
    }
    monkeypatch.setattr(
        runner_module,
        "_execute",
        lambda command, **facts: subprocess.CompletedProcess(
            command, 0, json.dumps(payload), ""
        ),
    )
    with pytest.raises(RuntimeError, match="netns evidence is duplicated"):
        runner._capture_privileged_incarnations(
            image_id="node-image", pids=(101, 102)
        )

def test_process_incarnation_capture_guards_pid_reuse_and_netns_errors(
    monkeypatch,
) -> None:
    read_netns_inode = process_incarnation._read_process_netns_inode
    starts = iter((11, 11))
    monkeypatch.setattr(
        process_incarnation,
        "_read_process_start_time_ticks",
        lambda pid: next(starts),
    )
    monkeypatch.setattr(process_incarnation, "_read_process_netns_inode", lambda pid: 22)
    assert process_incarnation._capture_process_incarnation(123) == (11, 22)

    starts = iter((11, 12))
    monkeypatch.setattr(
        process_incarnation,
        "_read_process_start_time_ticks",
        lambda pid: next(starts),
    )
    with pytest.raises(RuntimeError, match="changed during discovery"):
        process_incarnation._capture_process_incarnation(123)
    monkeypatch.setattr(
        process_incarnation, "_read_process_netns_inode", read_netns_inode
    )
    monkeypatch.setattr(
        process_incarnation.os,
        "stat",
        lambda *args, **kwargs: (_ for _ in ()).throw(PermissionError("denied")),
    )
    with pytest.raises(RuntimeError, match="network namespace is unavailable"):
        process_incarnation._read_process_netns_inode(123)

def test_process_incarnation_batch_is_complete_and_rejects_missing_or_duplicate(
    monkeypatch,
) -> None:
    observed = []
    monkeypatch.setattr(
        process_incarnation,
        "_capture_process_incarnation",
        lambda pid: observed.append(pid) or (pid * 10, pid * 100),
    )
    assert process_incarnation._capture_process_incarnations((11, 12)) == [
        {"pid": 11, "process_start_time_ticks": 110, "netns_inode": 1100},
        {"pid": 12, "process_start_time_ticks": 120, "netns_inode": 1200},
    ]
    assert observed == [11, 12]

    monkeypatch.setattr(
        process_incarnation,
        "_capture_process_incarnation",
        lambda pid: (_ for _ in ()).throw(RuntimeError("unavailable")),
    )
    with pytest.raises(RuntimeError, match="unavailable"):
        process_incarnation._capture_process_incarnations((11,))

    monkeypatch.setattr(
        process_incarnation,
        "_capture_process_incarnation",
        lambda pid: (pid * 10, 99),
    )
    with pytest.raises(RuntimeError, match="duplicated"):
        process_incarnation._capture_process_incarnations((11, 12))

def test_privileged_helper_has_private_ownership_labels(monkeypatch) -> None:
    commands = []

    def execute(command, **_facts):
        commands.append(tuple(command))
        return subprocess.CompletedProcess(
            command, 1 if command[1] == "inspect" else 0, "", ""
        )

    monkeypatch.setattr(runner_module, "_execute", execute)
    helper = runner_module._PrivilegedHelper(
        docker_executable="docker",
        name="runner-helper",
        foundation_token="foundation-token",
        image_id="node-image",
    )
    monkeypatch.setattr(runner_module.subprocess, "Popen", lambda *args, **kwargs: SimpleNamespace())
    helper.start()
    run_command = commands[1]
    assert "org.skyseed.runner.foundation=foundation-token" in run_command
    assert "com.docker.compose.project=" in run_command
    assert "com.docker.compose.service=" in run_command

def test_runner_depends_on_opaque_runtime_realization_seam() -> None:
    source = inspect.getsource(runner_module)
    for implementation_symbol in (
        "_MaterializedDataplaneState",
        "_ReadyDataplaneBoundary",
        "_empty_state",
        "_materialize_boundary",
        "_NO_ACTIVE_GENERATION",
    ):
        assert implementation_symbol not in source

def test_durable_history_and_recent_snapshot_preserve_detached_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    artifact = _Artifact()
    monkeypatch.setattr(runner_module, "_open_runner_artifact", lambda path: artifact)
    runner = BpfRunner(compose_project_name="runner", runtime_artifact=Path("/tmp/a"))
    history_path = tmp_path / "events.jsonl"
    runner._enable_event_history(history_path)
    nested = {"capacity": {"per_node": [{"key": "a"}]}}
    runner._record("nested", evidence=nested)
    for index in range(1_005):
        runner._record("progress", generated_index=index)
    durable = runner._event_history.close()
    snapshot = runner.inspection_snapshot()
    assert durable["complete"] is True
    assert durable["written_count"] == 1_006
    assert len(snapshot["events"]) == 512
    assert snapshot["event_history"]["scope"] == "recent-only"
    snapshot["events"][-1]["generated_index"] = -1
    assert runner.inspection_snapshot()["events"][-1]["generated_index"] == 1_004
    assert len(history_path.read_text(encoding="utf-8").splitlines()) == 1_007

def test_nonblocking_event_history_reports_bounded_overflow(
    tmp_path: Path, monkeypatch
) -> None:
    release = Event()
    original = _NonBlockingEventHistory._write_loop

    def paused_writer(history):
        assert release.wait(timeout=1)
        original(history)

    monkeypatch.setattr(_NonBlockingEventHistory, "_write_loop", paused_writer)
    history = _NonBlockingEventHistory(tmp_path / "events.jsonl", capacity=1)
    assert history.enqueue({"event": "first"}) is True
    assert history.enqueue({"event": "overflow"}) is False
    release.set()
    status = history.close()
    assert status["written_count"] == 1
    assert status["dropped_count"] == 1
    assert status["complete"] is False


def _finite_runner(monkeypatch, *, one_boundary=False):
    from test_runner_runtime_optimization import _Kernel, _capacity_transition_sequence, _capacity_demand
    projection, boundaries = _capacity_transition_sequence()
    if one_boundary:
        boundaries = boundaries[:1]
    kernel = _Kernel(monkeypatch)
    class Artifact(_Artifact):
        @property
        def runtime_capacity_demand(self):
            return _capacity_demand(projection, boundaries)
        @property
        def logical_ports(self):
            from skyseed_v2.backend.bpf.compiled_realization import _ResourceIntent
            connections = tuple(dict.fromkeys(op.intent.connection_id for boundary in boundaries for op in boundary.operations
                                             if isinstance(op.intent, _ResourceIntent)))
            return {connection: (2 * index + 1, 2 * index + 2) for index, connection in enumerate(connections)}
    artifact = Artifact(boundaries)
    artifact.physical_world_projection = projection
    artifact.zone_prefixes = ("198.18.0.1/32",)
    opens = []
    monkeypatch.setattr(runner_module, "_open_runner_artifact", lambda path: opens.append(path) or artifact)
    bindings = {node: _RuntimeBinding(node, "service", "container", "name", "image", i + 100, i + 200, i + 300, "now")
                for i, node in enumerate(projection.node_ids)}
    monkeypatch.setattr(BpfRunner, "_discover_runtime_bindings", lambda self, projection: bindings)
    monkeypatch.setattr(runner_module._PrivilegedHelper, "start", lambda self: None)
    monkeypatch.setattr(runner_module._PrivilegedHelper, "stop", lambda self: None)
    monkeypatch.setattr(runner_module._PrivilegedHelper, "execute", lambda self, action, request: kernel.execute(action, request))
    runner = BpfRunner(compose_project_name="finite", runtime_artifact=Path("/artifact"))
    return runner, artifact, kernel, opens


def test_prepare_activates_boundary_zero_and_is_one_shot(monkeypatch):
    runner, artifact, kernel, opens = _finite_runner(monkeypatch)
    assert not opens
    runner.prepare()
    assert len(opens) == 1
    assert runner._wall_origin is None
    assert runner._active_count == 1
    assert runner._active_frontier == artifact._boundaries[0].nominal_time
    assert kernel.maps["generation"][0] == 0
    assert len(kernel.links) == 2
    assert all(action == "prewrite_generation" for action, _ in kernel.actions[1:-1])
    assert kernel.actions[-1][0] == "activate_boundary"
    assert not hasattr(runner, "_maintenance_thread")
    assert not hasattr(runner, "_ready")
    with pytest.raises(RuntimeError, match="only once"):
        runner.prepare()


def test_formal_run_begins_at_one_without_decode_or_ready_wait(monkeypatch):
    runner, artifact, kernel, _ = _finite_runner(monkeypatch)
    runner.prepare()
    def forbidden(*args, **kwargs): raise AssertionError("runtime called Preparation/decode/discovery")
    monkeypatch.setattr(artifact, "boundaries", forbidden)
    monkeypatch.setattr(BpfRunner, "_discover_runtime_bindings", forbidden)
    monkeypatch.setattr(runner._runtime_realization, "prepare_all", forbidden)
    monkeypatch.setattr(runner._condition, "wait", forbidden)
    clock = [100.0]
    monkeypatch.setattr(runner_module, "monotonic", lambda: clock[0])
    monkeypatch.setattr(runner_module, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    runner.run()
    formal = [event for event in runner._events if event["event"] == "activation_end" and not event["preparation"]]
    assert [event["boundary_timestamp"] for event in formal] == [boundary.nominal_time.isoformat() for boundary in artifact._boundaries[1:]]
    assert runner._active_count == 5
    assert all(action == "activate_boundary" for action, _ in kernel.actions[5:])
    runner.cleanup()
    assert artifact.closed


def test_one_boundary_experiment_has_no_formal_network_activation(monkeypatch):
    runner, _, kernel, _ = _finite_runner(monkeypatch, one_boundary=True)
    runner.prepare()
    action_count = len(kernel.actions)
    runner.run()
    assert len(kernel.actions) == action_count
    assert runner._active_count == 1
    with pytest.raises(RuntimeError, match="exclusive"):
        runner.activate_next()


def test_fixed_origin_absorbs_encoding_and_wake_delays_and_reports_overrun(monkeypatch):
    from dataclasses import replace
    from datetime import timedelta
    runner, _, _, _ = _finite_runner(monkeypatch)
    runner.prepare()
    origin = runner._prepared_plans[0].nominal_time
    runner._prepared_plans = tuple(replace(plan, nominal_time=origin + timedelta(seconds=offset))
                                   for plan, offset in zip(runner._prepared_plans, (0, 1, 3, 7, 8)))
    clock = [100.0]
    sleeps = []
    durations = iter((0.6, 0.3, 2.0, 0.1))
    monkeypatch.setattr(runner_module, "monotonic", lambda: clock[0])
    monkeypatch.setattr(runner_module, "monotonic_ns", lambda: round(clock[0] * 1e9))
    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds + (0.4 if len(sleeps) == 1 else 0)
    def activate(plan):
        # Parent encoding/helper-arrival delay is elapsed Main work; its
        # observed anchor must never become the next scheduling origin.
        anchor = clock[0] + 0.2
        clock[0] += next(durations)
        return {"activation_anchor_monotonic_ns": round(anchor * 1e9)}
    monkeypatch.setattr(runner_module, "sleep", sleep)
    monkeypatch.setattr(runner._runtime_realization, "activate", activate)
    runner.run()
    events = [event for event in runner._events if event["event"] == "activation_end" and not event["preparation"]]
    assert [event["main_start_monotonic_seconds"] for event in events] == pytest.approx([101.4, 103, 107, 109])
    assert [event["ideal_start_lag_seconds"] for event in events] == pytest.approx([0.4, 0, 0, 1])
    assert [event["ideal_completion_lag_seconds"] for event in events] == pytest.approx([1, 0.3, 2, 1.1])
    assert runner._active_count == 5


def test_failed_main_keeps_last_completed_frontier_and_cleanup_ownership(monkeypatch):
    runner, _, kernel, _ = _finite_runner(monkeypatch)
    runner.prepare()
    runner.activate_next()
    runner.activate_next()
    frontier = runner._active_frontier
    kernel.fail = "delete"
    with pytest.raises(_IncompleteActivationError):
        runner.activate_next()
    assert runner._active_frontier == frontier
    assert runner._state == "failed"
    assert len(runner._runtime_realization._all_prepared_resources) == 2
    assert kernel.maps["generation"][0] == 1


def test_persistent_helper_reuses_one_command_stream_without_inspection(monkeypatch):
    import io
    helper = runner_module._PrivilegedHelper(docker_executable="docker", name="helper", foundation_token="token", image_id="image")
    requests = io.StringIO()
    replies = io.StringIO('{"status":"ok","result":{"count":1}}\n{"status":"ok","result":{"count":2}}\n')
    helper._process = SimpleNamespace(stdin=requests, stdout=replies, poll=lambda: None)
    def forbidden(*args, **kwargs): raise AssertionError("per-action process or Docker inspection")
    monkeypatch.setattr(runner_module, "_execute", forbidden)
    monkeypatch.setattr(runner_module.subprocess, "Popen", forbidden)
    monkeypatch.setattr(helper, "assert_current", forbidden)
    for generation in (1, 2):
        result = helper.execute("activate_boundary", {"generation": generation})
        assert result["count"] == generation
        timing = result["command_timing"]
        assert timing["command_seconds"] >= timing["json_encode_seconds"] >= 0
        assert timing["helper"] is None  # This transport fixture has no helper clock evidence.
    assert [json.loads(line)["request"]["generation"] for line in requests.getvalue().splitlines()] == [1, 2]


@pytest.mark.parametrize("reply,error_type", [
    ("", RuntimeError),
    ("invalid-json\n", json.JSONDecodeError),
    ("[]\n", AttributeError),
    ('{"status":"error","error":"mutation failed","error_type":"OSError",'
     '"evidence":{"cleanup_ownership_retained":true},'
     '"helper_timing":{"dispatch_seconds":0.25}}\n', runner_module._PrivilegedRuntimeCommandError),
])
def test_command_failure_keeps_original_error_and_partial_timing(reply, error_type):
    import io
    helper = runner_module._PrivilegedHelper(
        docker_executable="docker", name="helper", foundation_token="token", image_id="image")
    helper._process = SimpleNamespace(stdin=io.StringIO(), stdout=io.StringIO(reply), poll=lambda: None)
    with pytest.raises(error_type) as failure:
        helper.execute("activate_boundary", {"generation": 1})
    timing = failure.value.command_timing
    assert timing["command_seconds"] >= timing["json_encode_seconds"] >= 0
    assert timing["channel_wait_seconds"] >= 0
    if error_type is runner_module._PrivilegedRuntimeCommandError:
        assert failure.value.remote_evidence == {"cleanup_ownership_retained": True}
        assert timing["helper"]["dispatch_seconds"] == .25
    elif error_type is not AttributeError:
        assert timing["response_decode_seconds"] is None


def test_main_enqueues_prebuilt_telemetry_templates_without_build_or_flush(monkeypatch, tmp_path):
    queued = []
    class Recorder:
        def __init__(self, directory): pass
        def start(self, **kwargs): pass
        def snapshot(self): return {"enabled": True}
        def enqueue_generation_identity(self, templates, installed, withdrawn, boundary):
            queued.append((templates, installed, withdrawn))
        def record_failure(self, error): raise AssertionError(str(error))
        def write_generation_identity(self, *args): raise AssertionError("synchronous bulk telemetry")
    monkeypatch.setattr(runner_module, "_TrafficTelemetryRecorder", Recorder)
    runner, _, _, _ = _finite_runner(monkeypatch)
    runner._enable_traffic_telemetry(tmp_path)
    runner.prepare()
    assert len(queued[0][0]) == 2
    assert queued[0][0] is runner._prepared_plans[0].telemetry_identities
    for _ in range(3): runner.activate_next()
    assert queued[-1][0] is runner._prepared_plans[3].telemetry_identities
    assert len(queued[-1][1]) == 2  # only newly installed live endpoints
    assert len(queued[-1][2]) == 2  # only withdrawn live endpoints


def test_bulk_inspection_does_not_hold_main_publication_lock(monkeypatch):
    runner, _, _, _ = _finite_runner(monkeypatch)
    runner.prepare()
    entered, release, activated = Event(), Event(), Event()
    def addresses():
        entered.set()
        assert release.wait(2)
        return {}
    monkeypatch.setattr(runner._runtime_realization, "runtime_node_addresses", addresses)
    inspector = Thread(target=runner.inspection_snapshot)
    main = Thread(target=lambda: (runner.activate_next(), activated.set()))
    inspector.start()
    try:
        assert entered.wait(1)
        main.start()
        assert activated.wait(1)
    finally:
        release.set()
        inspector.join(2)
        if main.ident is not None:
            main.join(2)
    assert runner._active_count == 2
