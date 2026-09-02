from __future__ import annotations

from datetime import datetime, timedelta, timezone
import inspect
import json
import struct
import subprocess
from threading import Event, Lock, Thread
import time as real_time
from types import SimpleNamespace

import pytest

from examples.complete_static_emulator.emu import build_emulator
from examples.complete_static_emulator.simu import (
    create_complete_static_simulator,
    run_complete_static_chain,
)
from skyseed_v2.backend import bpf
from skyseed_v2.backend.bpf import BpfRunner
from skyseed_v2.backend.bpf._runner.event_history import (
    _NonBlockingEventHistory,
)
from skyseed_v2.backend.bpf._runner import (
    privileged_runtime_command as privileged_runtime_command_module,
)
from skyseed_v2.backend.bpf._runner import (
    runtime_realization as runtime_realization_module,
)
from skyseed_v2.backend.bpf._runner.privileged_runtime_command import (
    _activate_boundary,
    _key_buffer,
    _parallel_map,
    _prepare_boundary,
    _tc_program,
)
from skyseed_v2.backend.bpf._runner.runtime_realization import (
    _CapacityAuditError,
    _MaterializedDataplaneState,
    _RetiredPhysicalRealization,
    _TcBpfRuntimeRealization,
    _empty_state,
    _materialize_boundary,
    _validate_boundary_lowering,
)
from skyseed_v2.backend.bpf import runner as runner_module
from skyseed_v2.backend.bpf.compiled_realization import (
    _ForwardingIntent,
    _QdiscIntent,
    _ResourceIntent,
    _ResourceOperation,
    _ZoneEdgeIntent,
    _ZoneEdgePhase,
)
from skyseed_v2.backend.bpf.runner import (
    _RuntimeBinding,
    _append_realization_boundaries,
    _resolve_nominal_boundaries,
)
from skyseed_v2.shared import NodeId
from skyseed_v2.simulator import (
    OperationalConnectionId,
    OperationalConnectionKind,
)


START = datetime(2026, 1, 1, tzinfo=timezone.utc)
NODE = NodeId(65001, "satellite", "sat-1")
NODE_2 = NodeId(65001, "satellite", "sat-2")


@pytest.fixture(scope="module")
def complete_chain():
    simulator = create_complete_static_simulator(build_emulator())
    plans, realizations, snapshot = run_complete_static_chain(simulator)
    return simulator, plans, realizations, snapshot


def _phase(intent: object) -> int:
    if isinstance(intent, _ResourceIntent):
        return 0 if intent.operation is _ResourceOperation.ESTABLISH else 5
    if isinstance(intent, _QdiscIntent):
        return 1
    if isinstance(intent, _ZoneEdgeIntent):
        return 2 if intent.phase is _ZoneEdgePhase.READINESS else 4
    if isinstance(intent, _ForwardingIntent):
        return 3
    raise TypeError(intent)


class _FakeHelper:
    instances: list[_FakeHelper] = []

    def __init__(self, **facts) -> None:
        self.facts = facts
        self.started = False
        self.stopped = False
        self.__class__.instances.append(self)

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True


class _FakeRuntimeRealization:
    instances: list[_FakeRuntimeRealization] = []
    preparation_error: BaseException | None = None
    activation_error: BaseException | None = None
    reclaim_error: BaseException | None = None
    cleanup_error: BaseException | None = None
    activation_entered: Event | None = None
    activation_release: Event | None = None
    reclaim_wait_for_activation: Event | None = None
    reclaim_progress: Event | None = None

    def __init__(self, **facts) -> None:
        self.facts = facts
        self.prepared = []
        self.active = None
        self.cleanup_calls = 0
        self.reclaimed_generations = []
        self.reclaimed_resources = []
        self.materialization_calls = 0
        self.activation_calls = 0
        self.operations = []
        self._latest_state = _empty_state()
        self._pending = None
        self._resident_generations = set()
        self._resident_generation_capacity = 0
        self.__class__.instances.append(self)

    def initialize(self, _physical_world_projection, **facts):
        self._resident_generation_capacity = (
            len(facts["capacity_boundaries"])
            if facts["preparation_lookahead"] is None
            else facts["preparation_boundary_depth"] + 1
        )
        return {
            "shared_active_generation_map": True,
            "node_count": len(self.facts["bindings"]),
            "installed_node_addresses": sorted(
                value.value for value in self.facts["bindings"]
            ),
            "capacity_audit": {
                "mode": (
                    "bounded-full-range"
                    if facts["preparation_lookahead"] is None
                    else "rolling"
                ),
                "resident_generation_capacity": (
                    self._resident_generation_capacity
                ),
            },
        }

    def generation_capacity_status(self, _boundary=None):
        resident = len(self._resident_generations)
        reserved = int(self._pending is not None)
        return {
            "resident_generations": resident,
            "reserved_in_flight_preparations": reserved,
            "resident_generation_capacity": self._resident_generation_capacity,
            "can_start_preparation": (
                reserved == 0
                and resident < self._resident_generation_capacity
            ),
        }

    def start_preparation(self, boundary):
        if not self.generation_capacity_status()["can_start_preparation"]:
            raise _CapacityAuditError(
                "resident generation capacity is exhausted"
            )
        generation = len(self.prepared)
        self.operations.append(("prepare", generation))
        self.materialization_calls += 1
        pending = SimpleNamespace(
            boundary=boundary,
            state=_materialize_boundary(self._latest_state, boundary),
            generation=generation,
        )
        self._pending = pending
        return pending, {
            "generation": generation,
            "generation_changed": True,
        }

    def finish_preparation(
        self, pending, *, foundation_token, predecessor_time
    ):
        if pending is not self._pending:
            raise RuntimeError("fake preparation reservation is stale")
        self._pending = None
        if self.preparation_error is not None:
            raise self.preparation_error
        expected_active_generation = (
            0xFFFFFFFF if pending.generation == 0 else pending.generation - 1
        )
        ready = SimpleNamespace(
            boundary=pending.boundary,
            foundation_token=foundation_token,
            predecessor_time=predecessor_time,
            expected_active_generation=expected_active_generation,
            generation=pending.generation,
            state=pending.state,
        )
        self.prepared.append(ready)
        self._resident_generations.add(pending.generation)
        self._latest_state = pending.state
        return ready, {
            "generation": pending.generation,
            "generation_changed": True,
            "expected_active_generation": expected_active_generation,
            "readiness_sha256": f"ready-{pending.generation}",
            "prepared_resources": 0,
            "capacity_audit": {"generation": pending.generation},
        }

    def activate(self, ready):
        self.activation_calls += 1
        if self.activation_error is not None:
            raise self.activation_error
        if self.activation_entered is not None:
            self.activation_entered.set()
        if self.activation_release is not None and not self.activation_release.wait(
            timeout=2
        ):
            raise RuntimeError("test activation release timed out")
        self.active = ready
        return {
            "active_generation": ready.generation,
            "generation_publications": 1,
        }

    def retirement_handles(self, prior, active):
        del active
        return prior.generation, None

    def reclaim_forwarding(self, generation):
        if (
            self.reclaim_wait_for_activation is not None
            and not self.reclaim_wait_for_activation.wait(timeout=2)
        ):
            raise RuntimeError("test reclaim activation wait timed out")
        self.reclaimed_generations.append(generation)
        self._resident_generations.discard(generation)
        self.operations.append(("reclaim-generation", generation))
        if self.reclaim_progress is not None:
            self.reclaim_progress.set()
        return {"generation": generation, "deleted_entries": 3}

    def reclaim_physical(self, resources):
        if self.reclaim_error is not None:
            raise self.reclaim_error
        values = tuple(resources)
        self.reclaimed_resources.append(values)
        self.operations.append(("reclaim-resources", values))
        return {"deleted_connections": len(values)}

    def cleanup(self):
        self.cleanup_calls += 1
        if self.cleanup_error is not None:
            raise self.cleanup_error
        return {"pin_root_removed": True, "errors": []}

    def runtime_node_addresses(self):
        return {
            node_id: f"192.0.2.{index + 1}"
            for index, node_id in enumerate(self.facts["bindings"])
        }

    def working_set_counts(self):
        return {
            "prepared_boundary_count": len(self.prepared),
            "realization_state_count": len(self.prepared),
            "resident_generation_count": len(self._resident_generations),
            "prepared_physical_count": 0,
        }


@pytest.fixture
def fake_runner_runtime(monkeypatch):
    _FakeHelper.instances.clear()
    _FakeRuntimeRealization.instances.clear()
    for name in (
        "preparation_error",
        "activation_error",
        "reclaim_error",
        "cleanup_error",
        "activation_entered",
        "activation_release",
        "reclaim_wait_for_activation",
        "reclaim_progress",
    ):
        setattr(_FakeRuntimeRealization, name, None)
    monkeypatch.setattr(runner_module, "_PrivilegedHelper", _FakeHelper)
    monkeypatch.setattr(
        runner_module, "_TcBpfRuntimeRealization", _FakeRuntimeRealization
    )

    def discover(_self, first):
        return {
            node_id: _RuntimeBinding(
                node_id=node_id,
                service_name=f"node-{index}",
                container_id=f"container-{index}",
                container_name=f"node-{index}",
                image_id="image-id",
                pid=100 + index,
                netns_inode=200 + index,
                started_at="now",
            )
            for index, node_id in enumerate(
                first._physical_world_projection.node_ids
            )
        }

    monkeypatch.setattr(BpfRunner, "_discover_runtime_bindings", discover)
    monkeypatch.setattr(
        BpfRunner, "_assert_foundation_current", lambda _self: None
    )
    return None


def _supplier(*values):
    pending = iter((*values, None))
    return lambda: next(pending)


def _wait_for(predicate) -> None:
    for _ in range(400):
        if predicate():
            return
        real_time.sleep(0.005)
    raise AssertionError("background Runner maintenance did not converge")


def _container_inspection(
    node_id: NodeId,
    *,
    project: str,
    index: int,
) -> dict[str, object]:
    entity_type = (
        "Satellite"
        if node_id.entity_kind == "satellite"
        else "GroundStation"
    )
    labels = {
        runner_module._PROJECT_LABEL: project,
        runner_module._SERVICE_LABEL: f"node-{index}",
        runner_module._ASN_LABEL: str(node_id.asn),
        runner_module._ENTITY_TYPE_LABEL: entity_type,
    }
    if entity_type == "Satellite":
        labels[runner_module._SATELLITE_ID_LABEL] = node_id.entity_id
    else:
        labels[runner_module._GROUND_STATION_ID_LABEL] = node_id.entity_id
    return {
        "Id": f"container-{index}",
        "Name": f"/node-{index}",
        "Image": "node-image",
        "Config": {"Labels": labels},
        "State": {
            "Running": True,
            "Pid": 100 + index,
            "StartedAt": "now",
        },
    }


def test_runner_constructor_exposes_only_detached_realization_pull() -> None:
    parameters = inspect.signature(BpfRunner).parameters
    assert "future_realization_supplier" in parameters
    assert "planner" not in parameters
    assert "slicer" not in parameters
    runner = BpfRunner(
        compose_project_name="round-b-test",
        future_realization_supplier=lambda: None,
        preparation_lookahead=timedelta(0),
        preparation_boundary_depth=1,
    )
    assert runner.inspection_snapshot()["runner"]["lifecycle_state"] == "new"


def test_public_runner_surface_and_constructor_validation_are_closed() -> None:
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
    assert tuple(inspect.signature(BpfRunner).parameters) == (
        "compose_project_name",
        "future_realization_supplier",
        "preparation_lookahead",
        "preparation_boundary_depth",
    )
    with pytest.raises(TypeError, match="must be a string"):
        BpfRunner(
            compose_project_name=object(),
            future_realization_supplier=lambda: None,
            preparation_boundary_depth=1,
        )
    with pytest.raises(ValueError, match="single line"):
        BpfRunner(
            compose_project_name="runner\nother",
            future_realization_supplier=lambda: None,
            preparation_boundary_depth=1,
        )
    for invalid in (None, True, False, 0.5):
        with pytest.raises(TypeError, match="positive int"):
            BpfRunner(
                compose_project_name="runner",
                future_realization_supplier=lambda: None,
                preparation_boundary_depth=invalid,
            )
    for invalid in (0, -1):
        with pytest.raises(ValueError, match="positive"):
            BpfRunner(
                compose_project_name="runner",
                future_realization_supplier=lambda: None,
                preparation_boundary_depth=invalid,
            )
    with pytest.raises(ValueError, match="must be None"):
        BpfRunner(
            compose_project_name="runner",
            future_realization_supplier=lambda: None,
            preparation_lookahead=None,
            preparation_boundary_depth=1,
        )
    with pytest.raises(ValueError, match="non-negative"):
        BpfRunner(
            compose_project_name="runner",
            future_realization_supplier=lambda: None,
            preparation_lookahead=timedelta(seconds=-1),
            preparation_boundary_depth=1,
        )


def test_runner_discovers_exact_project_nodes_and_ignores_auxiliaries(
    complete_chain, monkeypatch
) -> None:
    _, _, realizations, _ = complete_chain
    first = realizations[0]
    project = "runner-exact-project"
    runner = BpfRunner(
        compose_project_name=project,
        future_realization_supplier=lambda: None,
        preparation_boundary_depth=1,
    )
    inspected = [
        {
            "Id": "auxiliary",
            "Name": "/auxiliary",
            "Image": "aux-image",
            "Config": {
                "Labels": {runner_module._PROJECT_LABEL: project}
            },
            "State": {"Running": False, "Pid": 0, "StartedAt": ""},
        },
        *(
            _container_inspection(node_id, project=project, index=index)
            for index, node_id in enumerate(
                first._physical_world_projection.node_ids
            )
        ),
    ]
    identifiers = [value["Id"] for value in inspected]
    commands = []

    def execute(command, *, input_text=None):
        del input_text
        commands.append(tuple(command))
        return subprocess.CompletedProcess(
            command, 0, "\n".join(identifiers) + "\n", ""
        )

    monkeypatch.setattr(runner_module, "_execute", execute)
    monkeypatch.setattr(BpfRunner, "_inspect", lambda _self, _ids: inspected)
    monkeypatch.setattr(
        BpfRunner,
        "_container_netns_inode",
        lambda _self, identifier: 500
        + int(identifier.rsplit("-", 1)[1]),
    )
    bindings = BpfRunner._discover_runtime_bindings(runner, first)
    assert set(bindings) == set(first._physical_world_projection.node_ids)
    assert all(value.image_id == "node-image" for value in bindings.values())
    assert commands == [
        (
            "docker",
            "ps",
            "--all",
            "--quiet",
            "--filter",
            f"label={runner_module._PROJECT_LABEL}={project}",
        )
    ]


def test_runtime_binding_netns_discovery_respects_worker_bound(
    complete_chain, monkeypatch
) -> None:
    _, _, realizations, _ = complete_chain
    first = realizations[0]
    project = "runner-parallel-bindings"
    monkeypatch.setenv("SKYSEED_BPF_RUNTIME_WORKERS", "2")
    runner = BpfRunner(
        compose_project_name=project,
        future_realization_supplier=lambda: None,
        preparation_boundary_depth=1,
    )
    inspected = [
        _container_inspection(node_id, project=project, index=index)
        for index, node_id in enumerate(
            first._physical_world_projection.node_ids
        )
    ]
    monkeypatch.setattr(
        runner_module,
        "_execute",
        lambda command, **_facts: subprocess.CompletedProcess(
            command,
            0,
            "\n".join(value["Id"] for value in inspected) + "\n",
            "",
        ),
    )
    monkeypatch.setattr(BpfRunner, "_inspect", lambda _self, _ids: inspected)
    guard = Lock()
    both_started = Event()
    active = 0
    high_water = 0

    def netns_inode(_self, identifier):
        nonlocal active, high_water
        with guard:
            active += 1
            high_water = max(high_water, active)
            if active == 2:
                both_started.set()
        assert both_started.wait(timeout=1)
        real_time.sleep(0.005)
        with guard:
            active -= 1
        return 500 + int(identifier.rsplit("-", 1)[1])

    monkeypatch.setattr(BpfRunner, "_container_netns_inode", netns_inode)
    bindings = BpfRunner._discover_runtime_bindings(runner, first)
    assert set(bindings) == set(first._physical_world_projection.node_ids)
    assert high_water == min(2, len(bindings))


def test_runner_rejects_incomplete_compiled_physical_world_binding_set(
    complete_chain, monkeypatch
) -> None:
    _, _, realizations, _ = complete_chain
    first = realizations[0]
    project = "runner-incomplete-bindings"
    runner = BpfRunner(
        compose_project_name=project,
        future_realization_supplier=lambda: None,
        preparation_boundary_depth=1,
    )
    nodes = first._physical_world_projection.node_ids[:-1]
    inspected = [
        _container_inspection(node_id, project=project, index=index)
        for index, node_id in enumerate(nodes)
    ]
    monkeypatch.setattr(
        runner_module,
        "_execute",
        lambda command, **_facts: subprocess.CompletedProcess(
            command,
            0,
            "\n".join(value["Id"] for value in inspected) + "\n",
            "",
        ),
    )
    monkeypatch.setattr(BpfRunner, "_inspect", lambda _self, _ids: inspected)
    monkeypatch.setattr(
        BpfRunner, "_container_netns_inode", lambda _self, _id: 456
    )
    with pytest.raises(RuntimeError, match="missing="):
        BpfRunner._discover_runtime_bindings(runner, first)


def test_foundation_validation_audits_container_incarnation_without_helper_io(
    complete_chain, monkeypatch
) -> None:
    _, _, realizations, _ = complete_chain
    node_id = realizations[0]._physical_world_projection.node_ids[0]
    runner = BpfRunner(
        compose_project_name="runner-foundation",
        future_realization_supplier=lambda: None,
        preparation_boundary_depth=1,
    )
    binding = _RuntimeBinding(
        node_id=node_id,
        service_name="node",
        container_id="container-id",
        container_name="node",
        image_id="image-id",
        pid=123,
        netns_inode=456,
        started_at="now",
    )
    runner._bindings = {node_id: binding}
    runner._helper = SimpleNamespace(
        execute=lambda *_args, **_kwargs: pytest.fail(
            "foundation validation must not invoke the helper"
        )
    )
    inspected = [
        {
            "Id": "container-id",
            "Image": "image-id",
            "State": {
                "Running": True,
                "Pid": 123,
                "StartedAt": "now",
            },
        }
    ]
    monkeypatch.setattr(BpfRunner, "_inspect", lambda _self, _ids: inspected)
    BpfRunner._assert_foundation_current(runner)
    inspected[0]["State"]["Running"] = False
    with pytest.raises(RuntimeError, match="incarnation is stale"):
        BpfRunner._assert_foundation_current(runner)


def test_privileged_helper_does_not_inherit_compose_project_ownership(
    monkeypatch,
) -> None:
    commands = []

    def execute(command, *, input_text=None):
        del input_text
        commands.append(tuple(command))
        return subprocess.CompletedProcess(
            command,
            1 if command[1] == "inspect" else 0,
            "",
            "",
        )

    monkeypatch.setattr(runner_module, "_execute", execute)
    helper = runner_module._PrivilegedHelper(
        docker_executable="docker",
        name="runner-helper",
        foundation_token="foundation-token",
        image_id="node-image",
    )
    helper.start()
    run_command = commands[1]
    assert (
        "--label",
        "org.skyseed.runner.foundation=foundation-token",
    ) == run_command[5:7]
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
    assert "runtime_realization" in source


def test_runner_resolves_every_explicit_formal_boundary(
    complete_chain,
) -> None:
    _, _, realizations, _ = complete_chain
    boundaries = _resolve_nominal_boundaries(realizations)
    assert tuple(value.nominal_time for value in boundaries) == tuple(
        timestamp
        for realization in realizations
        for timestamp in realization.activation_timestamps
    )
    assert tuple(
        value.source_realization_indexes for value in boundaries
    ) == ((0,), (0,), (0,), (1,))


def test_runner_topological_order_preserves_all_compiled_constraints(
    complete_chain,
) -> None:
    _, _, realizations, _ = complete_chain
    boundaries = _resolve_nominal_boundaries(realizations)
    for boundary in boundaries:
        order = {
            operation.reference: index
            for index, operation in enumerate(boundary.operations)
        }
        for predecessor, successor, _ in boundary.constraint_edges:
            assert order[predecessor] < order[successor]
        assert tuple(_phase(value.intent) for value in boundary.operations) == tuple(
            sorted(_phase(value.intent) for value in boundary.operations)
        )
        assert _validate_boundary_lowering(boundary) >= 0


def test_zone_readiness_is_before_route_publication(
    complete_chain,
) -> None:
    _, _, realizations, _ = complete_chain
    first = _resolve_nominal_boundaries(realizations)[0]
    phases = [_phase(value.intent) for value in first.operations]
    assert 2 in phases
    assert 3 in phases
    assert max(index for index, value in enumerate(phases) if value == 2) < min(
        index for index, value in enumerate(phases) if value == 3
    )


def test_materialized_boundaries_reach_compiler_intended_state(
    complete_chain,
) -> None:
    _, _, realizations, _ = complete_chain
    state = _MaterializedDataplaneState((), (), (), ())
    realization_by_end = {value.end_timestamp: value for value in realizations}
    for boundary in _resolve_nominal_boundaries(realizations):
        state = _materialize_boundary(state, boundary)
        realization = realization_by_end.get(boundary.nominal_time)
        if realization is None:
            continue
        assert state.resources == tuple(
            value.connection.connection_id
            for value in realization.intended_resources
        )
        assert state.forwarding == realization.intended_forwarding
        assert state.zone_edges == realization.intended_zone_edges


def test_incremental_boundary_append_accepts_disjoint_plan_intervals(
    complete_chain,
) -> None:
    _, _, realizations, _ = complete_chain
    boundaries, first_new = _append_realization_boundaries(
        (),
        realizations[0],
        realization_index=0,
        active_frontier=None,
    )
    boundaries, second_new = _append_realization_boundaries(
        boundaries,
        realizations[1],
        realization_index=1,
        active_frontier=None,
    )
    assert first_new[-1].nominal_time < second_new[0].nominal_time
    assert boundaries == (*first_new, *second_new)


def test_duplicate_formal_boundary_is_rejected(complete_chain) -> None:
    _, _, realizations, _ = complete_chain
    with pytest.raises(ValueError, match="duplicate"):
        _resolve_nominal_boundaries((realizations[0], realizations[0]))


def test_durable_event_history_is_complete_while_recent_memory_is_bounded(
    tmp_path,
) -> None:
    runner = BpfRunner(
        compose_project_name="runner-durable-events",
        future_realization_supplier=lambda: None,
        preparation_lookahead=None,
        preparation_boundary_depth=None,
    )
    path = tmp_path / "runner-events.jsonl"
    runner._enable_event_history(path)

    for index in range(1_005):
        runner._record("synthetic_progress", generated_index=index)

    durable = runner._event_history.close()
    snapshot = runner.inspection_snapshot()
    assert len(runner._events) == 512
    assert snapshot["runner"]["working_set"]["recent_event_count"] == 512
    assert durable["complete"] is True
    assert durable["written_count"] == 1_005
    rows = tuple(
        json.loads(value)
        for value in path.read_text(encoding="utf-8").splitlines()
    )
    assert len(rows) == 1_006
    assert rows[1]["generated_index"] == 0
    assert rows[-1]["generated_index"] == 1_004


def test_nonblocking_event_history_reports_bounded_overflow(
    tmp_path, monkeypatch
) -> None:
    release = Event()
    original = _NonBlockingEventHistory._write_loop

    def paused_writer(history) -> None:
        assert release.wait(timeout=1)
        original(history)

    monkeypatch.setattr(_NonBlockingEventHistory, "_write_loop", paused_writer)
    history = _NonBlockingEventHistory(tmp_path / "events.jsonl", capacity=1)
    assert history.enqueue({"event": "first"}) is True
    started = real_time.monotonic()
    assert history.enqueue({"event": "overflow"}) is False
    assert real_time.monotonic() - started < 0.1
    release.set()
    status = history.close()
    assert status["written_count"] == 1
    assert status["dropped_count"] == 1
    assert status["overflowed"] is True
    assert status["complete"] is False


def test_runner_event_snapshot_is_recent_only_and_deeply_detached() -> None:
    runner = BpfRunner(
        compose_project_name="runner-recent-events",
        future_realization_supplier=lambda: None,
        preparation_lookahead=None,
        preparation_boundary_depth=None,
    )
    nested = {"capacity": {"per_node": [{"key": "a"}, {"key": "b"}]}}
    runner._record("nested", evidence=nested)
    for index in range(1_005):
        runner._record("synthetic_progress", generated_index=index)
    snapshot = runner.inspection_snapshot()
    assert len(snapshot["events"]) == 512
    assert snapshot["event_history"]["scope"] == "recent-only"
    assert snapshot["event_history"]["total_count"] == 1_006
    snapshot["events"][-1]["generated_index"] = -1
    assert runner.inspection_snapshot()["events"][-1]["generated_index"] == 1_004


def test_inspection_snapshot_after_cleanup_is_detached_and_read_only(
    complete_chain, fake_runner_runtime, monkeypatch
) -> None:
    del fake_runner_runtime
    _, _, realizations, _ = complete_chain
    runner = BpfRunner(
        compose_project_name="runner-detached-inspection",
        future_realization_supplier=_supplier(realizations[0]),
        preparation_lookahead=None,
        preparation_boundary_depth=None,
    )
    runner.prepare()
    runner.activate_next()
    runner.cleanup()

    def reject_live_inspection(*_args, **_kwargs):
        raise AssertionError("inspection_snapshot must not query Docker")

    monkeypatch.setattr(BpfRunner, "_inspect", reject_live_inspection)
    before = runner.inspection_snapshot()
    assert before["format_version"] == 1
    assert before["runner"]["lifecycle_state"] == "cleaned"
    assert before["runner"]["execution_mode"] == "manual"
    assert before["runner"]["activation_count"] == 1
    assert before["events"][-2]["event"] == "cleanup_end"
    assert before["events"][-1]["checkpoint"] == "after-teardown"
    before["runner"]["frontiers"]["last_active"] = "changed"
    before["runner"]["runtime_foundation"]["bindings"][0]["pid"] = 0
    before["events"][0]["event"] = "changed"
    after = runner.inspection_snapshot()
    assert after["runner"]["frontiers"]["last_active"] == (
        realizations[0].activation_timestamps[0].isoformat()
    )
    assert after["runner"]["runtime_foundation"]["bindings"][0]["pid"] != 0
    assert after["events"][0]["event"] == "preparation_start"


def test_full_range_runner_prepares_and_activates_every_formal_boundary(
    complete_chain, fake_runner_runtime
) -> None:
    del fake_runner_runtime
    _, _, realizations, _ = complete_chain
    runner = BpfRunner(
        compose_project_name="runner-full-range",
        future_realization_supplier=_supplier(*realizations),
        preparation_lookahead=None,
        preparation_boundary_depth=None,
    )
    try:
        runner.prepare()
        runtime = _FakeRuntimeRealization.instances[0]
        expected = tuple(
            timestamp
            for realization in realizations
            for timestamp in realization.activation_timestamps
        )
        assert tuple(
            value.boundary.nominal_time for value in runtime.prepared
        ) == expected
        assert runtime.materialization_calls == len(expected)
        assert runtime.active is None
        for _ in expected:
            runner.activate_next()
        assert runner._active_frontier == expected[-1]
        assert runtime.activation_calls == len(expected)
        assert runner.inspection_snapshot()["runner"]["activation_count"] == len(
            expected
        )
    finally:
        runner.cleanup()
    assert _FakeHelper.instances[0].stopped


def test_zero_lookahead_rolls_exactly_one_ready_boundary(
    complete_chain, fake_runner_runtime
) -> None:
    del fake_runner_runtime
    _, _, realizations, _ = complete_chain
    expected = tuple(
        timestamp
        for realization in realizations
        for timestamp in realization.activation_timestamps
    )
    runner = BpfRunner(
        compose_project_name="runner-zero-lookahead",
        future_realization_supplier=_supplier(*realizations),
        preparation_lookahead=timedelta(0),
        preparation_boundary_depth=1,
    )
    try:
        runner.prepare()
        assert tuple(value.boundary.nominal_time for value in runner._ready) == (
            expected[0],
        )
        runner.activate_next()
        _wait_for(lambda: len(runner._ready) == 1)
        assert runner._ready[0].boundary.nominal_time == expected[1]
        assert runner.inspection_snapshot()["runner"][
            "ready_depth_at_capture"
        ] == 1
    finally:
        runner.cleanup()


def test_time_and_depth_bounds_stop_continuous_ready_preparation(
    complete_chain, fake_runner_runtime
) -> None:
    del fake_runner_runtime
    _, _, realizations, _ = complete_chain
    timestamps = realizations[0].activation_timestamps
    interval = timestamps[1] - timestamps[0]
    by_time = BpfRunner(
        compose_project_name="runner-time-bound",
        future_realization_supplier=_supplier(*realizations),
        preparation_lookahead=interval,
        preparation_boundary_depth=8,
    )
    try:
        by_time.prepare()
        assert tuple(
            value.boundary.nominal_time for value in by_time._ready
        ) == timestamps[:2]
        assert by_time.inspection_snapshot()["runner"][
            "ready_limiting_reason"
        ] == "time"
    finally:
        by_time.cleanup()

    _FakeHelper.instances.clear()
    _FakeRuntimeRealization.instances.clear()
    by_depth = BpfRunner(
        compose_project_name="runner-depth-bound",
        future_realization_supplier=_supplier(*realizations),
        preparation_lookahead=timedelta(seconds=100),
        preparation_boundary_depth=2,
    )
    try:
        by_depth.prepare()
        assert len(by_depth._ready) == 2
        assert by_depth.inspection_snapshot()["runner"][
            "ready_limiting_reason"
        ] == "depth"
        by_depth.activate_next()
        _wait_for(lambda: len(by_depth._ready) == 2)
        assert tuple(
            value.boundary.nominal_time for value in by_depth._ready
        ) == timestamps[1:3]
    finally:
        by_depth.cleanup()


def test_concurrent_manual_activation_rejects_second_kernel_mutation(
    complete_chain, fake_runner_runtime
) -> None:
    del fake_runner_runtime
    _, _, realizations, _ = complete_chain
    entered = Event()
    release = Event()
    _FakeRuntimeRealization.activation_entered = entered
    _FakeRuntimeRealization.activation_release = release
    runner = BpfRunner(
        compose_project_name="runner-concurrent-main",
        future_realization_supplier=_supplier(realizations[0]),
        preparation_lookahead=None,
        preparation_boundary_depth=None,
    )
    runner.prepare()
    failures = []
    first = Thread(target=runner.activate_next)
    first.start()
    assert entered.wait(timeout=1)

    def second_attempt() -> None:
        try:
            runner.activate_next()
        except BaseException as error:
            failures.append(error)

    second = Thread(target=second_attempt)
    second.start()
    second.join(timeout=1)
    try:
        assert not second.is_alive()
        assert len(failures) == 1
        assert "activation in flight" in str(failures[0])
        assert _FakeRuntimeRealization.instances[0].activation_calls == 1
        assert _FakeRuntimeRealization.instances[0].active is None
    finally:
        release.set()
        first.join(timeout=1)
        runner.cleanup()
    assert not first.is_alive()


def test_maintenance_reclaim_progresses_during_main_activation(
    complete_chain, fake_runner_runtime
) -> None:
    del fake_runner_runtime
    _, _, realizations, _ = complete_chain
    activation_entered = Event()
    activation_release = Event()
    reclaim_progress = Event()
    _FakeRuntimeRealization.activation_entered = activation_entered
    _FakeRuntimeRealization.activation_release = activation_release
    _FakeRuntimeRealization.reclaim_wait_for_activation = activation_entered
    _FakeRuntimeRealization.reclaim_progress = reclaim_progress
    runner = BpfRunner(
        compose_project_name="runner-concurrent-maintenance",
        future_realization_supplier=_supplier(realizations[0]),
        preparation_lookahead=None,
        preparation_boundary_depth=None,
    )
    runner.prepare()
    with runner._condition:
        runner._reclaim_forwarding.append(91)
        runner._maintenance_revision += 1
        runner._condition.notify_all()
    main = Thread(target=runner.activate_next)
    main.start()
    try:
        assert activation_entered.wait(timeout=1)
        assert reclaim_progress.wait(timeout=1)
        assert main.is_alive()
        assert 91 in _FakeRuntimeRealization.instances[0].reclaimed_generations
    finally:
        activation_release.set()
        main.join(timeout=1)
        runner.cleanup()


def test_forward_deficit_precedes_both_reclaim_classes(
    complete_chain, fake_runner_runtime, monkeypatch
) -> None:
    del fake_runner_runtime
    _, _, realizations, _ = complete_chain
    monkeypatch.setattr(BpfRunner, "_maintenance_loop", lambda _self: None)
    runner = BpfRunner(
        compose_project_name="runner-forward-priority",
        future_realization_supplier=_supplier(*realizations),
        preparation_lookahead=timedelta(0),
        preparation_boundary_depth=1,
    )
    runner.prepare()
    runner._maintenance_thread.join(timeout=1)
    runner.activate_next()
    runtime = _FakeRuntimeRealization.instances[0]
    runtime.operations.clear()
    retired = object()
    with runner._condition:
        runner._reclaim_forwarding.append(73)
        runner._reclaim_physical.append((retired,))
    runner._maintain_until_satisfied()
    try:
        assert runtime.operations[:3] == [
            ("prepare", 1),
            ("reclaim-generation", 73),
            ("reclaim-resources", (retired,)),
        ]
        assert any(
            value["event"] == "maintenance_forward_priority"
            for value in runner.inspection_snapshot()["events"]
        )
    finally:
        runner.cleanup()


def test_capacity_pressure_reclaims_generation_before_next_preparation(
    complete_chain, fake_runner_runtime, monkeypatch
) -> None:
    del fake_runner_runtime
    _, _, realizations, _ = complete_chain
    monkeypatch.setattr(BpfRunner, "_maintenance_loop", lambda _self: None)
    runner = BpfRunner(
        compose_project_name="runner-capacity-pressure",
        future_realization_supplier=_supplier(*realizations),
        preparation_lookahead=timedelta(0),
        preparation_boundary_depth=1,
    )
    runner.prepare()
    runner._maintenance_thread.join(timeout=1)
    runner.activate_next()
    runner._maintain_until_satisfied()
    runner.activate_next()
    runtime = _FakeRuntimeRealization.instances[0]
    runtime.operations.clear()
    runner._maintain_until_satisfied()
    try:
        assert runtime.operations[:2] == [
            ("reclaim-generation", 0),
            ("prepare", 2),
        ]
        assert any(
            value["event"] == "maintenance_capacity_pressure"
            for value in runner.inspection_snapshot()["events"]
        )
    finally:
        runner.cleanup()


def test_preparation_and_activation_failures_do_not_publish_partial_state(
    complete_chain, fake_runner_runtime
) -> None:
    del fake_runner_runtime
    _, _, realizations, _ = complete_chain
    _FakeRuntimeRealization.preparation_error = RuntimeError("prepare failed")
    preparing = BpfRunner(
        compose_project_name="runner-prepare-failure",
        future_realization_supplier=_supplier(realizations[0]),
        preparation_lookahead=None,
        preparation_boundary_depth=None,
    )
    with pytest.raises(RuntimeError, match="prepare failed"):
        preparing.prepare()
    assert preparing._state == "failed"
    assert not preparing._ready
    assert _FakeRuntimeRealization.instances[0].active is None
    _FakeRuntimeRealization.preparation_error = None
    preparing.cleanup()

    _FakeHelper.instances.clear()
    _FakeRuntimeRealization.instances.clear()
    activating = BpfRunner(
        compose_project_name="runner-activation-failure",
        future_realization_supplier=_supplier(realizations[0]),
        preparation_lookahead=None,
        preparation_boundary_depth=None,
    )
    activating.prepare()
    runtime = _FakeRuntimeRealization.instances[0]
    _FakeRuntimeRealization.activation_error = RuntimeError("activate failed")
    with pytest.raises(RuntimeError, match="activate failed"):
        activating.activate_next()
    assert activating._state == "failed"
    assert runtime.active is None
    assert len(activating._ready) == len(
        realizations[0].activation_timestamps
    )
    _FakeRuntimeRealization.activation_error = None
    activating.cleanup()


def test_maintenance_reclaim_failure_fails_runner_without_losing_ownership(
    complete_chain, fake_runner_runtime
) -> None:
    del fake_runner_runtime
    _, _, realizations, _ = complete_chain
    runner = BpfRunner(
        compose_project_name="runner-reclaim-failure",
        future_realization_supplier=_supplier(*realizations),
        preparation_lookahead=timedelta(0),
        preparation_boundary_depth=1,
    )
    try:
        runner.prepare()
        runner.activate_next()
        _wait_for(lambda: len(runner._ready) == 1)
        _FakeRuntimeRealization.reclaim_error = RuntimeError(
            "kernel deletion failed"
        )
        with runner._condition:
            runner._reclaim_physical.append((object(),))
            runner._maintenance_revision += 1
            runner._condition.notify_all()
        _wait_for(lambda: runner._state == "failed")
        assert isinstance(runner._maintenance_error, RuntimeError)
        assert "kernel deletion failed" in str(runner._maintenance_error)
    finally:
        _FakeRuntimeRealization.reclaim_error = None
        runner.cleanup()


def test_runtime_teardown_failure_preserves_helper_for_retry(
    complete_chain, fake_runner_runtime
) -> None:
    del fake_runner_runtime
    _, _, realizations, _ = complete_chain
    runner = BpfRunner(
        compose_project_name="runner-cleanup-retry",
        future_realization_supplier=_supplier(realizations[0]),
        preparation_lookahead=None,
        preparation_boundary_depth=None,
    )
    runner.prepare()
    runtime = _FakeRuntimeRealization.instances[0]
    helper = _FakeHelper.instances[0]
    _FakeRuntimeRealization.cleanup_error = RuntimeError("pin remains")
    with pytest.raises(RuntimeError, match="pin remains"):
        runner.cleanup()
    assert runner._state == "teardown-failed"
    assert runner._runtime_realization is runtime
    assert runner._helper is helper
    assert helper.stopped is False
    _FakeRuntimeRealization.cleanup_error = None
    runner.cleanup()
    assert runtime.cleanup_calls == 2
    assert helper.stopped is True
    assert runner.inspection_snapshot()["runner"]["lifecycle_state"] == "cleaned"


def test_runner_teardown_timeout_retains_runtime_for_retry(
    fake_runner_runtime, monkeypatch
) -> None:
    del fake_runner_runtime
    release = Event()
    maintenance = Thread(target=release.wait)
    maintenance.start()
    runtime = _FakeRuntimeRealization()
    runner = BpfRunner(
        compose_project_name="runner-cleanup-timeout",
        future_realization_supplier=lambda: None,
        preparation_lookahead=None,
        preparation_boundary_depth=None,
    )
    runner._runtime_realization = runtime
    runner._maintenance_thread = maintenance
    runner._state = "prepared"
    monkeypatch.setattr(
        runner_module, "_MAINTENANCE_SHUTDOWN_TIMEOUT_SECONDS", 0.01
    )
    with pytest.raises(RuntimeError, match="did not stop"):
        runner.cleanup()
    assert runner._state == "teardown-failed"
    assert runner._runtime_realization is runtime
    assert runtime.cleanup_calls == 0
    release.set()
    maintenance.join(timeout=1)
    runner.cleanup()
    assert runtime.cleanup_calls == 1


def test_manual_and_automatic_modes_are_mutually_exclusive(
    complete_chain, fake_runner_runtime
) -> None:
    del fake_runner_runtime
    _, _, realizations, _ = complete_chain
    runner = BpfRunner(
        compose_project_name="runner-mode",
        future_realization_supplier=_supplier(realizations[0]),
        preparation_lookahead=None,
        preparation_boundary_depth=None,
    )
    try:
        runner.prepare()
        runner.activate_next()
        with pytest.raises(RuntimeError, match="exclusive"):
            runner.run()
    finally:
        runner.cleanup()


def test_automatic_run_uses_relative_monotonic_schedule(
    complete_chain, fake_runner_runtime, monkeypatch
) -> None:
    del fake_runner_runtime
    _, _, realizations, _ = complete_chain
    clock = [100.0]
    sleeps = []

    def fake_sleep(duration):
        sleeps.append(duration)
        clock[0] += duration

    runner = BpfRunner(
        compose_project_name="runner-automatic",
        future_realization_supplier=_supplier(realizations[0]),
        preparation_lookahead=None,
        preparation_boundary_depth=None,
    )
    try:
        runner.prepare()
        expected = realizations[0].activation_timestamps
        monkeypatch.setattr(runner_module, "monotonic", lambda: clock[0])
        monkeypatch.setattr(runner_module, "sleep", fake_sleep)
        runner.run()
        assert sleeps == [
            (current - previous).total_seconds()
            for previous, current in zip(expected, expected[1:])
        ]
        assert runner._active_frontier == expected[-1]
    finally:
        runner.cleanup()


def test_automatic_activation_limit_stops_one_exact_formal_prefix(
    complete_chain, fake_runner_runtime, monkeypatch
) -> None:
    del fake_runner_runtime
    _, _, realizations, _ = complete_chain
    clock = [100.0]
    runner = BpfRunner(
        compose_project_name="runner-bounded-prefix",
        future_realization_supplier=_supplier(*realizations),
        preparation_lookahead=None,
        preparation_boundary_depth=None,
    )
    try:
        runner.prepare()
        expected = tuple(
            timestamp
            for realization in realizations
            for timestamp in realization.activation_timestamps
        )
        monkeypatch.setattr(runner_module, "monotonic", lambda: clock[0])
        monkeypatch.setattr(
            runner_module,
            "sleep",
            lambda duration: clock.__setitem__(0, clock[0] + duration),
        )
        runner._run_bounded(2)
        assert runner._active_count == 2
        assert runner._active_frontier == expected[1]
        end = runner.inspection_snapshot()["events"][-1]
        assert end["event"] == "automatic_run_end"
        assert end["stopped_by_activation_limit"] is True
    finally:
        runner.cleanup()


def test_automatic_deadline_miss_fails_before_missing_boundary_mutation(
    complete_chain, fake_runner_runtime, monkeypatch
) -> None:
    del fake_runner_runtime
    _, _, realizations, _ = complete_chain
    clock = [10.0]
    runner = BpfRunner(
        compose_project_name="runner-deadline-miss",
        future_realization_supplier=_supplier(realizations[0]),
        preparation_lookahead=None,
        preparation_boundary_depth=None,
    )
    runner.prepare()
    runner._ready.popleft()
    monkeypatch.setattr(runner_module, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        runner_module,
        "sleep",
        lambda duration: clock.__setitem__(0, clock[0] + duration),
    )
    try:
        with pytest.raises(RuntimeError, match="not READY"):
            runner.run()
        runtime = _FakeRuntimeRealization.instances[0]
        assert runtime.activation_calls == 0
        assert runtime.active is None
        assert any(
            value["event"] == "deadline_miss"
            for value in runner.inspection_snapshot()["events"]
        )
    finally:
        runner.cleanup()


def test_ready_predecessor_staleness_fails_before_runtime_mutation(
    complete_chain, fake_runner_runtime
) -> None:
    del fake_runner_runtime
    _, _, realizations, _ = complete_chain
    runner = BpfRunner(
        compose_project_name="runner-stale-predecessor",
        future_realization_supplier=_supplier(realizations[0]),
        preparation_lookahead=None,
        preparation_boundary_depth=None,
    )
    try:
        runner.prepare()
        runner._active_frontier = START - timedelta(seconds=1)
        with pytest.raises(RuntimeError, match="predecessor"):
            runner.activate_next()
        runtime = _FakeRuntimeRealization.instances[0]
        assert runtime.activation_calls == 0
        assert runtime.active is None
    finally:
        runner.cleanup()


def test_full_resident_capacity_with_ready_depth_satisfied_waits_for_main(
    complete_chain, fake_runner_runtime, monkeypatch
) -> None:
    del fake_runner_runtime
    _, _, realizations, _ = complete_chain
    monkeypatch.setattr(BpfRunner, "_maintenance_loop", lambda _self: None)
    runner = BpfRunner(
        compose_project_name="runner-depth-wait",
        future_realization_supplier=_supplier(*realizations),
        preparation_lookahead=timedelta(seconds=100),
        preparation_boundary_depth=2,
    )
    try:
        runner.prepare()
        runner._maintenance_thread.join(timeout=1)
        runner.activate_next()
        runner._maintain_until_satisfied()
        runtime = _FakeRuntimeRealization.instances[0]
        assert len(runner._ready) == 2
        assert runtime.generation_capacity_status() == {
            "resident_generations": 3,
            "reserved_in_flight_preparations": 0,
            "resident_generation_capacity": 3,
            "can_start_preparation": False,
        }
        assert runner._next_maintenance_action() == ("done", None)
    finally:
        runner.cleanup()


def test_missing_exact_next_at_full_capacity_without_reclaim_fails_closed(
    complete_chain, fake_runner_runtime, monkeypatch
) -> None:
    del fake_runner_runtime
    _, _, realizations, _ = complete_chain
    monkeypatch.setattr(BpfRunner, "_maintenance_loop", lambda _self: None)
    runner = BpfRunner(
        compose_project_name="runner-capacity-deadlock",
        future_realization_supplier=_supplier(*realizations),
        preparation_lookahead=timedelta(0),
        preparation_boundary_depth=1,
    )
    try:
        runner.prepare()
        runner._maintenance_thread.join(timeout=1)
        runner.activate_next()
        runner._maintain_until_satisfied()
        runner.activate_next()
        with runner._condition:
            runner._reclaim_forwarding.clear()
        with pytest.raises(_CapacityAuditError, match="no retired generation"):
            runner._maintain_until_satisfied()
        assert any(
            value["event"] == "capacity_audit_failure"
            and value["scope"] == "resident-generation"
            for value in runner.inspection_snapshot()["events"]
        )
    finally:
        runner.cleanup()


def test_privileged_activation_preserves_readiness_route_switch_retirement_order(
    monkeypatch, tmp_path
) -> None:
    operations = []
    interface = "sv21234567890a"

    def execute_in_netns(_pid, command, *, input_text=None, **_facts):
        payload = input_text or ""
        if "link set" in payload and " up" in payload:
            operations.append("link-up")
        elif "address replace" in payload:
            operations.append("zone-readiness")
        elif "route replace" in payload:
            operations.append("route-publication")
        elif "address del" in payload:
            operations.append("zone-retirement")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(
        privileged_runtime_command_module, "_pin_root", lambda _value: tmp_path
    )
    monkeypatch.setattr(
        privileged_runtime_command_module, "_open_pinned", lambda _path: 10
    )
    monkeypatch.setattr(
        privileged_runtime_command_module, "_lookup_map", lambda _fd, _key: 0xFFFFFFFF
    )
    monkeypatch.setattr(
        privileged_runtime_command_module,
        "_update_map",
        lambda *_args: operations.append("generation-switch"),
    )
    monkeypatch.setattr(
        privileged_runtime_command_module, "_assert_netns", lambda _node: None
    )
    monkeypatch.setattr(
        privileged_runtime_command_module, "_in_netns", execute_in_netns
    )
    monkeypatch.setattr(
        privileged_runtime_command_module.os, "close", lambda _fd: None
    )
    request = {
        "pin_root": str(tmp_path),
        "common_mac": "02:53:4b:59:00:01",
        "expected_active_generation": 0xFFFFFFFF,
        "generation": 0,
        "generation_changed": True,
        "boundary_timestamp": datetime(
            2026, 1, 1, tzinfo=timezone.utc
        ).isoformat(),
        "worker_count": 1,
        "nodes": [
            {
                "key": "node",
                "pid": 101,
                "netns_inode": 201,
                "interface_identities": [
                    {"interface": interface, "ifindex": 17}
                ],
                "link_up": [interface],
                "link_down": [],
                "qdisc": [],
                "zone_address_add": ["198.18.0.1"],
                "zone_address_delete": ["198.18.0.2"],
                "route_delete": [],
                "route_replace": [
                    {
                        "destination": "192.0.2.1",
                        "interface": interface,
                    }
                ],
                "access_peer_route_delete": [],
                "access_peer_route_replace": [],
            }
        ],
    }

    result = _activate_boundary(request)

    assert operations == [
        "link-up",
        "zone-readiness",
        "route-publication",
        "generation-switch",
        "zone-retirement",
    ]
    assert result["active_generation"] == 0
    assert result["namespace_process_families"]["zone_readiness"] == 1


def test_privileged_activation_stale_generation_fails_before_namespace_mutation(
    monkeypatch, tmp_path
) -> None:
    mutations = []
    monkeypatch.setattr(
        privileged_runtime_command_module, "_pin_root", lambda _value: tmp_path
    )
    monkeypatch.setattr(
        privileged_runtime_command_module, "_open_pinned", lambda _path: 10
    )
    monkeypatch.setattr(
        privileged_runtime_command_module, "_lookup_map", lambda _fd, _key: 7
    )
    monkeypatch.setattr(
        privileged_runtime_command_module,
        "_in_netns",
        lambda *_args, **_kwargs: mutations.append("namespace"),
    )
    monkeypatch.setattr(
        privileged_runtime_command_module,
        "_update_map",
        lambda *_args: mutations.append("map"),
    )
    monkeypatch.setattr(
        privileged_runtime_command_module.os, "close", lambda _fd: None
    )
    with pytest.raises(RuntimeError, match="active generation evidence is stale"):
        _activate_boundary(
            {
                "pin_root": str(tmp_path),
                "expected_active_generation": 6,
                "generation": 8,
                "nodes": [],
            }
        )
    assert mutations == []


def test_private_worker_program_uses_shared_generation_and_owned_pin_scope() -> None:
    program = _tc_program(10, 11, 12)
    assert len(program) % 8 == 0
    assert len(program) >= 40 * 8
    assert (
        privileged_runtime_command_module._pin_root(
            "/sys/fs/bpf/skyseed-v2/run-0123456789ab"
        ).name
        == "run-0123456789ab"
    )
    with pytest.raises(ValueError, match="outside"):
        privileged_runtime_command_module._pin_root(
            "/sys/fs/bpf/not-owned"
        )


def test_bounded_parallel_map_preserves_input_order_and_worker_limit() -> None:
    guard = Lock()
    both_running = Event()
    active = 0
    high_water = 0

    def operation(value: int) -> int:
        nonlocal active, high_water
        with guard:
            active += 1
            high_water = max(high_water, active)
            if active == 2:
                both_running.set()
        assert both_running.wait(timeout=1)
        with guard:
            active -= 1
        return value * 10

    assert _parallel_map(
        {"worker_count": 2}, [3, 1], operation
    ) == [30, 10]
    assert high_water == 2


def test_generation_reclaim_uses_exact_map_batch_delete(monkeypatch) -> None:
    observed = []

    def bpf(command, attributes):
        observed.append(
            (
                command,
                attributes.map_fd,
                attributes.count,
                bytes(
                    privileged_runtime_command_module.ctypes.string_at(
                        attributes.keys, attributes.count * 8
                    )
                ),
            )
        )
        return 0

    monkeypatch.setattr(privileged_runtime_command_module, "_bpf", bpf)
    deleted = privileged_runtime_command_module._delete_map_batch(
        19,
        ((7, 11), (7, 13)),
    )
    assert deleted == 2
    assert observed == [
        (
            privileged_runtime_command_module._BPF_MAP_DELETE_BATCH,
            19,
            2,
            struct.pack("=IIII", 7, 11, 7, 13),
        )
    ]
    assert _key_buffer((7, 19)).raw[:8] == struct.pack("=II", 7, 19)


def test_privileged_preparation_chunks_physical_resources_deterministically(
    monkeypatch, tmp_path
) -> None:
    root = tmp_path / "pins"
    root.mkdir()
    host_batches: list[str] = []
    monkeypatch.setattr(
        privileged_runtime_command_module, "_pin_root", lambda _value: root
    )
    monkeypatch.setattr(
        privileged_runtime_command_module, "_assert_netns", lambda _node: None
    )
    monkeypatch.setattr(
        privileged_runtime_command_module,
        "_run",
        lambda command, *, input_text=None, **_facts: (
            host_batches.append(input_text)
            if command == ["ip", "-batch", "-"]
            else None
        ),
    )
    monkeypatch.setattr(
        privileged_runtime_command_module,
        "_in_netns",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            (), 0, "", ""
        ),
    )
    nodes = [
        {
            "key": NODE.value,
            "pid": 101,
            "netns_inode": 201,
            "pin_component": "a",
        },
        {
            "key": NODE_2.value,
            "pid": 102,
            "netns_inode": 202,
            "pin_component": "b",
        },
    ]
    parameters = {
        "delay_us": 10,
        "packet_loss_ppm": 0,
        "bandwidth_bps": 1_000_000,
    }
    connections = [
        {
            "node_a": NODE.value,
            "node_b": NODE_2.value,
            "interface_a": f"sv2{index:010x}a",
            "interface_b": f"sv2{index:010x}b",
            "parameters_a": parameters,
            "parameters_b": parameters,
        }
        for index in (1, 2)
    ]
    result = _prepare_boundary(
        {
            "pin_root": str(root),
            "common_mac": "02:53:4b:59:00:01",
            "nodes": nodes,
            "connections": connections,
            "generation": 0,
            "generation_nodes": [],
            "boundary_timestamp": START.isoformat(),
            "worker_count": 2,
        }
    )
    assert result["prepared_connections"] == 2
    assert len(host_batches) == 2
    for index, batch in zip((1, 2), host_batches, strict=True):
        assert batch.splitlines() == [
            f"link add sv2{index:010x}a netns 101 type veth "
            f"peer name sv2{index:010x}b netns 102"
        ]


def test_privileged_physical_chunk_failure_joins_and_rolls_back_all_candidates(
    monkeypatch, tmp_path
) -> None:
    root = tmp_path / "pins"
    root.mkdir()
    rollback_interfaces = []
    monkeypatch.setattr(
        privileged_runtime_command_module, "_pin_root", lambda _value: root
    )
    monkeypatch.setattr(
        privileged_runtime_command_module, "_assert_netns", lambda _node: None
    )

    def run(_command, *, input_text=None, **_facts):
        if "sv20000000001a" in (input_text or ""):
            raise RuntimeError("physical chunk failed")
        return subprocess.CompletedProcess((), 0, "", "")

    def in_netns(_pid, command, **_facts):
        rollback_interfaces.append(command[-1])
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(privileged_runtime_command_module, "_run", run)
    monkeypatch.setattr(
        privileged_runtime_command_module, "_in_netns", in_netns
    )
    nodes = [
        {
            "key": NODE.value,
            "pid": 101,
            "netns_inode": 201,
            "pin_component": "a",
        },
        {
            "key": NODE_2.value,
            "pid": 102,
            "netns_inode": 202,
            "pin_component": "b",
        },
    ]
    parameters = {
        "delay_us": 10,
        "packet_loss_ppm": 0,
        "bandwidth_bps": 1_000_000,
    }
    connections = [
        {
            "node_a": NODE.value,
            "node_b": NODE_2.value,
            "interface_a": f"sv2{index:010x}a",
            "interface_b": f"sv2{index:010x}b",
            "parameters_a": parameters,
            "parameters_b": parameters,
        }
        for index in (1, 2)
    ]
    with pytest.raises(RuntimeError, match="physical chunk failed"):
        _prepare_boundary(
            {
                "pin_root": str(root),
                "common_mac": "02:53:4b:59:00:01",
                "nodes": nodes,
                "connections": connections,
                "generation": 0,
                "generation_nodes": [],
                "boundary_timestamp": START.isoformat(),
                "worker_count": 2,
            }
        )
    assert rollback_interfaces == [
        "sv20000000002a",
        "sv20000000001a",
    ]


def test_privileged_activation_rejects_stale_netns_before_kernel_mutation(
    monkeypatch
) -> None:
    mutations = []
    monkeypatch.setattr(
        privileged_runtime_command_module, "_open_pinned", lambda _path: 10
    )
    monkeypatch.setattr(
        privileged_runtime_command_module,
        "_lookup_map",
        lambda _fd, _key: 0xFFFFFFFF,
    )
    monkeypatch.setattr(
        privileged_runtime_command_module,
        "_assert_netns",
        lambda _node: (_ for _ in ()).throw(RuntimeError("stale netns")),
    )
    monkeypatch.setattr(
        privileged_runtime_command_module,
        "_update_map",
        lambda *_args: mutations.append("map-update"),
    )
    monkeypatch.setattr(
        privileged_runtime_command_module,
        "_in_netns",
        lambda *_args, **_kwargs: mutations.append("namespace-mutation"),
    )
    monkeypatch.setattr(
        privileged_runtime_command_module.os, "close", lambda _fd: None
    )
    with pytest.raises(RuntimeError, match="stale netns"):
        _activate_boundary(
            {
                "pin_root": "/sys/fs/bpf/skyseed-v2/run-stale-preflight",
                "expected_active_generation": 0xFFFFFFFF,
                "nodes": ({"pid": 123, "netns_inode": 456},),
            }
        )
    assert mutations == []


def test_privileged_parameter_only_activation_reuses_generation(
    monkeypatch
) -> None:
    commands = []
    publications = []
    monkeypatch.setattr(
        privileged_runtime_command_module, "_open_pinned", lambda _path: 10
    )
    monkeypatch.setattr(
        privileged_runtime_command_module, "_lookup_map", lambda _fd, _key: 7
    )
    monkeypatch.setattr(
        privileged_runtime_command_module,
        "_update_map",
        lambda *_values: publications.append(_values),
    )
    monkeypatch.setattr(
        privileged_runtime_command_module, "_assert_netns", lambda _node: None
    )
    monkeypatch.setattr(
        privileged_runtime_command_module,
        "_in_netns",
        lambda _pid, command, *, input_text=None, **_facts: (
            commands.append((command, input_text))
            or subprocess.CompletedProcess(command, 0, "", "")
        ),
    )
    monkeypatch.setattr(
        privileged_runtime_command_module.os, "close", lambda _fd: None
    )
    result = _activate_boundary(
        {
            "pin_root": "/sys/fs/bpf/skyseed-v2/run-reuse-generation",
            "boundary_timestamp": START.isoformat(),
            "expected_active_generation": 7,
            "generation": 7,
            "generation_changed": False,
            "common_mac": "02:53:4b:59:00:01",
            "nodes": [
                {
                    "pid": 101,
                    "netns_inode": 201,
                    "interface_identities": (),
                    "link_up": (),
                    "link_down": (),
                    "qdisc": (
                        {
                            "interface": "sv21234567890a",
                            "delay_us": 25,
                            "packet_loss_ppm": 10,
                            "bandwidth_bps": 1_000_000,
                        },
                    ),
                    "route_delete": (),
                    "route_replace": (),
                    "access_peer_route_delete": (),
                    "access_peer_route_replace": (),
                    "zone_address_delete": (),
                    "zone_address_add": (),
                }
            ],
        }
    )
    assert publications == []
    assert len(commands) == 1
    assert commands[0][0] == ["tc", "-batch", "-"]
    assert result["active_generation"] == 7
    assert result["generation_publications"] == 0
    assert result["generation_reused"] is True
    assert result["qdisc_updates"] == 1


def test_privileged_activation_batches_access_peer_route_and_neighbour(
    monkeypatch
) -> None:
    batches: list[str] = []
    monkeypatch.setattr(
        privileged_runtime_command_module, "_open_pinned", lambda _path: 10
    )
    monkeypatch.setattr(
        privileged_runtime_command_module,
        "_lookup_map",
        lambda _fd, _key: 0xFFFFFFFF,
    )
    monkeypatch.setattr(
        privileged_runtime_command_module, "_update_map", lambda *_facts: None
    )
    monkeypatch.setattr(
        privileged_runtime_command_module, "_assert_netns", lambda _node: None
    )

    def in_netns(_pid, command, *, input_text=None, **_facts):
        if command[:2] == ["ip", "-force"]:
            batches.append(input_text)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(
        privileged_runtime_command_module, "_in_netns", in_netns
    )
    monkeypatch.setattr(
        privileged_runtime_command_module.os, "close", lambda _fd: None
    )
    interface = "sv21234567890a"
    result = _activate_boundary(
        {
            "pin_root": "/sys/fs/bpf/skyseed-v2/run-access-command",
            "boundary_timestamp": START.isoformat(),
            "expected_active_generation": 0xFFFFFFFF,
            "generation": 0,
            "common_mac": "02:53:4b:59:00:01",
            "nodes": [
                {
                    "pid": 101,
                    "netns_inode": 201,
                    "link_up": (),
                    "link_down": (),
                    "qdisc": (),
                    "route_delete": (),
                    "route_replace": (),
                    "access_peer_route_delete": (
                        {
                            "destination": "100.64.0.2",
                            "interface": interface,
                        },
                    ),
                    "access_peer_route_replace": (
                        {
                            "destination": "100.64.0.3",
                            "interface": interface,
                        },
                    ),
                    "zone_address_delete": (),
                    "zone_address_add": (),
                }
            ],
        }
    )
    assert batches == [
        "route del 100.64.0.2/32\n"
        f"neigh del 100.64.0.2 dev {interface}\n"
        f"route replace 100.64.0.3/32 dev {interface} scope link\n"
        "neigh replace 100.64.0.3 lladdr 02:53:4b:59:00:01 "
        f"nud permanent dev {interface}\n"
    ]
    assert result["route_neighbour_commands"] == 4
    assert result["access_peer_route_neighbour_commands"] == 4


def test_actual_runtime_bounded_and_rolling_capacity_plans_are_distinct(
    complete_chain,
) -> None:
    _, _, realizations, _ = complete_chain
    projection = realizations[0]._physical_world_projection
    boundaries = _resolve_nominal_boundaries(realizations)
    zone_prefixes = tuple(
        prefix for _, prefix in realizations[0]._intended_state.zone_prefixes
    )
    bindings = {
        node_id: SimpleNamespace(
            pid=index + 101,
            netns_inode=index + 201,
        )
        for index, node_id in enumerate(projection.node_ids)
    }

    class _Executor:
        def execute(self, action, request):
            if action == "initialize":
                return {
                    "shared_active_generation_map": True,
                    "collision_audit": {
                        "result": "passed",
                        "selected_node_address_range": request[
                            "node_address_range"
                        ],
                        "audited_node_namespaces": len(request["nodes"]),
                    },
                    "installed_node_addresses": [
                        node["key"] for node in request["nodes"]
                    ],
                }
            if action == "cleanup":
                return {"pin_root_removed": True, "errors": []}
            raise AssertionError(action)

    bounded = _TcBpfRuntimeRealization(
        executor=_Executor(),
        pin_root="/sys/fs/bpf/skyseed-v2/run-bounded-capacity",
        bindings=bindings,
    )
    rolling = _TcBpfRuntimeRealization(
        executor=_Executor(),
        pin_root="/sys/fs/bpf/skyseed-v2/run-rolling-capacity",
        bindings=bindings,
    )
    try:
        bounded_audit = bounded.initialize(
            projection,
            zone_prefixes=zone_prefixes,
            capacity_boundaries=boundaries,
            preparation_lookahead=None,
            preparation_boundary_depth=None,
        )["capacity_audit"]
        rolling_audit = rolling.initialize(
            projection,
            zone_prefixes=zone_prefixes,
            capacity_boundaries=(boundaries[0],),
            preparation_lookahead=timedelta(0),
            preparation_boundary_depth=1,
        )["capacity_audit"]
        assert bounded_audit["mode"] == "bounded-full-range"
        assert bounded_audit["resident_generation_capacity"] == (
            bounded_audit["known_generation_count"]
        )
        assert 1 <= bounded_audit["known_generation_count"] <= len(boundaries)
        assert {
            value["capacity_basis"] for value in bounded_audit["per_node"]
        } == {"bounded-full-range-exact-demand"}
        assert rolling_audit["mode"] == "rolling"
        assert rolling_audit["resident_generation_capacity"] == 2
        assert rolling_audit["preparation_boundary_depth"] == 1
        assert {
            value["capacity_basis"] for value in rolling_audit["per_node"]
        } == {"topology-upper-bound-times-resident-capacity"}
        assert all(
            value["configured_active_link_entries"]
            >= value["required_active_link_entries"]
            and value["configured_route_entries"]
            >= value["required_route_entries"]
            for value in (*bounded_audit["per_node"], *rolling_audit["per_node"])
        )
    finally:
        bounded.cleanup()
        rolling.cleanup()


def test_actual_runtime_stale_physical_reclaim_protects_in_flight_state() -> None:
    connection = OperationalConnectionId(
        OperationalConnectionKind.INTER_SATELLITE,
        NODE,
        NODE_2,
    )

    class _Executor:
        def execute(self, action, request):
            raise AssertionError((action, request))

    dataplane = _TcBpfRuntimeRealization(
        executor=_Executor(),
        pin_root="/sys/fs/bpf/skyseed-v2/run-in-flight-reclaim",
        bindings={},
    )
    dataplane._prepared_resources.add(connection)
    dataplane._all_prepared_resources.add(connection)
    dataplane._preparing = runtime_realization_module._PendingDataplaneBoundary(
        SimpleNamespace(
            nominal_time=START,
            operations=(),
            order_sha256="pending",
        ),
        _empty_state(),
        _MaterializedDataplaneState((connection,), (), (), ()),
        1,
        2,
        0,
    )
    result = dataplane.reclaim_physical(
        _RetiredPhysicalRealization((connection,))
    )
    assert result["candidate_connections"] == 1
    assert result["protected_connections"] == 1
    assert result["deleted_connections"] == 0
    assert connection in dataplane._prepared_resources


def test_actual_runtime_reclaim_failure_retains_owned_inventory() -> None:
    connection = OperationalConnectionId(
        OperationalConnectionKind.INTER_SATELLITE,
        NODE,
        NODE_2,
    )

    class _FailingExecutor:
        def execute(self, action, request):
            assert action == "reclaim_resources"
            raise RuntimeError("kernel deletion failed")

    dataplane = _TcBpfRuntimeRealization(
        executor=_FailingExecutor(),
        pin_root="/sys/fs/bpf/skyseed-v2/run-reclaim-failure",
        bindings={},
    )
    dataplane._prepared_resources.add(connection)
    dataplane._all_prepared_resources.add(connection)
    interface_a, interface_b = runtime_realization_module._interface_names(
        connection
    )
    dataplane._interface_indexes[(NODE.value, interface_a)] = 17
    dataplane._interface_indexes[(NODE_2.value, interface_b)] = 18
    with pytest.raises(RuntimeError, match="kernel deletion failed"):
        dataplane.reclaim_physical(
            _RetiredPhysicalRealization((connection,))
        )
    assert connection in dataplane._prepared_resources
    assert connection in dataplane._all_prepared_resources
    assert dataplane._interface_indexes == {
        (NODE.value, interface_a): 17,
        (NODE_2.value, interface_b): 18,
    }


def test_actual_runtime_cleanup_failure_retains_ownership_for_retry() -> None:
    connection = OperationalConnectionId(
        OperationalConnectionKind.INTER_SATELLITE,
        NODE,
        NODE_2,
    )

    class _RetryExecutor:
        def __init__(self) -> None:
            self.calls = 0

        def execute(self, action, request):
            assert action == "cleanup"
            self.calls += 1
            if self.calls == 1:
                return {
                    "pin_root_removed": False,
                    "errors": ["pinned map remains"],
                }
            return {"pin_root_removed": True, "errors": []}

    executor = _RetryExecutor()
    dataplane = _TcBpfRuntimeRealization(
        executor=executor,
        pin_root="/sys/fs/bpf/skyseed-v2/run-cleanup-retry",
        bindings={},
    )
    dataplane._initialized = True
    dataplane._prepared_resources.add(connection)
    dataplane._all_prepared_resources.add(connection)
    dataplane._interface_indexes[(NODE.value, "sv21234567890a")] = 17
    with pytest.raises(RuntimeError, match="ownership retained"):
        dataplane.cleanup()
    assert dataplane._initialized is True
    assert connection in dataplane._all_prepared_resources
    assert dataplane._interface_indexes
    assert dataplane.cleanup()["pin_root_removed"] is True
    assert dataplane._initialized is False
    assert not dataplane._all_prepared_resources
    assert not dataplane._interface_indexes


def test_actual_runner_uses_fake_privileged_helper_without_runner_replacement(
    complete_chain, monkeypatch
) -> None:
    simulator, _, realizations, _ = complete_chain
    project = "actual-runner-fake-helper"
    projection = realizations[0]._physical_world_projection
    containers = {}
    for index, node_id in enumerate(projection.node_ids):
        entity_type = (
            "Satellite"
            if node_id.entity_kind == "satellite"
            else "GroundStation"
        )
        labels = {
            runner_module._PROJECT_LABEL: project,
            runner_module._SERVICE_LABEL: f"node-{index}",
            runner_module._ASN_LABEL: str(node_id.asn),
            runner_module._ENTITY_TYPE_LABEL: entity_type,
        }
        if entity_type == "Satellite":
            labels[runner_module._SATELLITE_ID_LABEL] = node_id.entity_id
        else:
            labels[runner_module._GROUND_STATION_ID_LABEL] = node_id.entity_id
        identifier = f"container-{index}"
        containers[identifier] = {
            "Id": identifier,
            "Name": f"/node-{index}",
            "Image": "image-id",
            "Config": {"Labels": labels},
            "State": {
                "Running": True,
                "Pid": 1000 + index,
                "StartedAt": "2026-01-01T00:00:00Z",
            },
        }

    def fake_execute(command, *, input_text=None):
        del input_text
        values = tuple(command)
        if values[1] == "ps":
            return subprocess.CompletedProcess(
                values, 0, "\n".join(containers) + "\n", ""
            )
        if values[1] == "inspect":
            selected = [containers[value] for value in values[2:]]
            return subprocess.CompletedProcess(
                values, 0, json.dumps(selected), ""
            )
        if values[1] == "exec" and values[-2:] == (
            "readlink",
            "/proc/self/ns/net",
        ):
            index = int(values[2].rsplit("-", 1)[1])
            return subprocess.CompletedProcess(
                values, 0, f"net:[{5000 + index}]\n", ""
            )
        raise AssertionError(values)

    class _PrivilegedRecorder:
        instances = []

        def __init__(self, **facts):
            self.facts = facts
            self.actions = []
            self.interface_indexes = {}
            self.next_ifindex = 10
            self.started = False
            self.stopped = False
            self.__class__.instances.append(self)

        def start(self):
            self.started = True

        def stop(self):
            self.stopped = True

        def execute(self, action, request):
            self.actions.append((action, request))
            if action == "initialize":
                return {
                    "shared_active_generation_map": True,
                    "collision_audit": {
                        "result": "passed",
                        "selected_node_address_range": request[
                            "node_address_range"
                        ],
                        "audited_node_namespaces": len(request["nodes"]),
                    },
                    "installed_node_addresses": [
                        value["key"] for value in request["nodes"]
                    ],
                }
            if action == "prepare_boundary":
                generation_keys = []
                for node in request["generation_nodes"]:
                    identities = []
                    indexes = []
                    for interface in node["active_interfaces"]:
                        key = node["key"], interface
                        if key not in self.interface_indexes:
                            self.interface_indexes[key] = self.next_ifindex
                            self.next_ifindex += 1
                        ifindex = self.interface_indexes[key]
                        indexes.append(ifindex)
                        identities.append(
                            {"interface": interface, "ifindex": ifindex}
                        )
                    generation_keys.append(
                        {
                            "key": node["key"],
                            "active_interface_indexes": indexes,
                            "route_keys": list(range(len(node["routes"]))),
                            "interface_identities": identities,
                        }
                    )
                return {
                    "generation_keys": generation_keys,
                    "prepared_connections": len(request["connections"]),
                    "preloaded_active_links": sum(
                        len(value["active_interface_indexes"])
                        for value in generation_keys
                    ),
                    "preloaded_routes": sum(
                        len(value["route_keys"]) for value in generation_keys
                    ),
                    "stable_interface_identity_count": len(
                        self.interface_indexes
                    ),
                    "new_interface_identity_count": len(
                        request["connections"]
                    )
                    * 2,
                }
            if action == "activate_boundary":
                return {
                    "active_generation": request["generation"],
                    "generation_publications": int(
                        request.get("generation_changed", True)
                    ),
                }
            if action == "reclaim_generation":
                return {
                    "generation": request["generation"],
                    "deleted_entries": sum(
                        len(value["active_interface_indexes"])
                        + len(value["route_keys"])
                        for value in request["generation_keys"]
                    ),
                }
            if action == "reclaim_resources":
                return {
                    "deleted_connections": len(request["connections"])
                }
            if action == "cleanup":
                return {
                    "pin_root_removed": True,
                    "errors": [],
                    "deleted_connections": len(request["connections"]),
                }
            raise AssertionError(action)

    monkeypatch.setattr(runner_module, "_execute", fake_execute)
    monkeypatch.setattr(
        runner_module, "_PrivilegedHelper", _PrivilegedRecorder
    )
    runner = BpfRunner(
        compose_project_name=project,
        future_realization_supplier=_supplier(*realizations),
        preparation_lookahead=None,
        preparation_boundary_depth=None,
    )
    try:
        runner.prepare()
        assert type(runner) is BpfRunner
        assert isinstance(runner._runtime_realization, _TcBpfRuntimeRealization)
        expected_count = sum(
            len(value.activation_timestamps) for value in realizations
        )
        for _ in range(expected_count):
            runner.activate_next()
        assert runner.inspection_snapshot()["runner"][
            "activation_count"
        ] == expected_count
        assert runner._active_frontier == realizations[-1].end_timestamp
    finally:
        runner.cleanup()
        simulator.get_planner().shutdown(wait=True)
    helper = _PrivilegedRecorder.instances[0]
    actions = tuple(value[0] for value in helper.actions)
    assert helper.started and helper.stopped
    assert actions[0] == "initialize"
    assert actions.count("prepare_boundary") >= 1
    assert actions.count("activate_boundary") == expected_count
    assert actions[-1] == "cleanup"
