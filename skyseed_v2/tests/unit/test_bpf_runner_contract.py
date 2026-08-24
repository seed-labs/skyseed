from __future__ import annotations

from datetime import datetime, timedelta, timezone
from ipaddress import ip_address, ip_interface, ip_network
import json
from pathlib import Path
import inspect
import struct
from threading import Event, Thread
import time as real_time
from types import SimpleNamespace

import pytest

from examples.mini_satellite_internet.emu import (
    build_mini_satellite_internet_emulator,
)
from examples.mini_satellite_internet.simu import (
    _create_mini_satellite_internet_planner,
    create_mini_satellite_internet_simulator,
)
from skyseed_v2.backend import bpf
from skyseed_v2.backend.bpf import BpfCompiledRealization, BpfRunner
from skyseed_v2._generated_bpf_execution import _RollingRealizationSupplier
from skyseed_v2.backend.bpf._runner import (
    runtime_realization as runtime_realization_module,
)
from skyseed_v2.backend.bpf import runner as runner_module
from skyseed_v2.backend.bpf.runner import (
    _resolve_nominal_boundaries,
)
from skyseed_v2.backend.bpf._runner.runtime_realization import (
    _CapacityAuditError,
    _MaterializedDataplaneState,
    _TcBpfRuntimeRealization,
    _RetiredPhysicalRealization,
    _allocate_node_addresses,
    _empty_state,
    _materialize_boundary,
)
from skyseed_v2.backend.bpf._runner import (
    privileged_runtime_command as privileged_runtime_command_module,
)
from skyseed_v2.backend.bpf._runner.privileged_runtime_command import (
    _activate_boundary,
    _audit_node_addresses,
    _initialize,
    _key_buffer,
    _pin_root,
    _prepare_boundary,
    _tc_program,
)
from skyseed_v2.backend.bpf.compiled_realization import (
    _IntendedState,
    _IntentReference,
    _OrderingConstraint,
    _TopologySignature,
    _ZoneEdgeIntent,
)
from skyseed_v2.shared import NodeId
from skyseed_v2.simulator import (
    ConnectionId,
    ConnectionKind,
    DifferenceOperation,
)
from tools.runner.reader import read_runner_event_history


START = datetime(2026, 1, 1, tzinfo=timezone.utc)
NODE = NodeId(65001, "satellite", "sat-1")
NODE_2 = NodeId(65001, "satellite", "sat-2")
GROUND = NodeId(65001, "ground-station", "gs-1")
_REAL_DISCOVER_RUNTIME_BINDINGS = BpfRunner._discover_runtime_bindings
_REAL_PRIVILEGED_HELPER = runner_module._PrivilegedHelper


def _realization(start: int, end: int, zone: str) -> BpfCompiledRealization:
    nominal_time = START + timedelta(seconds=start)
    return BpfCompiledRealization._create(
        start_slice_id=start,
        start_timestamp=None if start == 0 else nominal_time,
        end_slice_id=end,
        end_timestamp=START + timedelta(seconds=end),
        resource_realizations=(),
        parameter_realizations=(),
        forwarding_realizations=(),
        zone_edge_realizations=(
            _ZoneEdgeIntent(
                nominal_time,
                DifferenceOperation.ADDED,
                zone,
                "198.18.0.1/32" if zone == "zone-a" else "198.18.0.2/32",
                None,
                NODE,
            ),
        ),
        constraints=(),
        intended_state=_IntendedState(
            (),
            (),
            (),
            (),
            (("zone-a", "198.18.0.1/32"), ("zone-b", "198.18.0.2/32")),
        ),
        topology_signature=_TopologySignature((NODE,), ("zone-a", "zone-b")),
    )


def _zone_realization(
    start: int,
    end: int,
    zones: tuple[str, ...],
    *,
    topology_zones: tuple[str, ...],
    topology_nodes: tuple[NodeId, ...] = (NODE,),
) -> BpfCompiledRealization:
    nominal_time = START + timedelta(seconds=start)
    return BpfCompiledRealization._create(
        start_slice_id=start,
        start_timestamp=None if start == 0 else nominal_time,
        end_slice_id=end,
        end_timestamp=START + timedelta(seconds=end),
        resource_realizations=(),
        parameter_realizations=(),
        forwarding_realizations=(),
        zone_edge_realizations=tuple(
            _ZoneEdgeIntent(
                nominal_time,
                DifferenceOperation.ADDED,
                zone,
                f"198.18.0.{topology_zones.index(zone) + 1}/32",
                None,
                NODE,
            )
            for zone in zones
        ),
        constraints=(),
        intended_state=_IntendedState(
            (),
            (),
            (),
            (),
            tuple(
                (zone, f"198.18.0.{topology_zones.index(zone) + 1}/32")
                for zone in topology_zones
            ),
        ),
        topology_signature=_TopologySignature(topology_nodes, topology_zones),
    )


def _collision_audit_result(request) -> dict[str, object]:
    return {
        "result": "passed",
        "selected_node_address_range": request["node_address_range"],
        "allocated_node_addresses": len(request["nodes"]),
        "compiled_zone_prefixes": len(request["zone_prefixes"]),
        "audited_node_namespaces": len(request["nodes"]),
        "observed_ipv4_address_prefixes": len(request["nodes"]),
        "observed_non_default_ipv4_route_prefixes": 0,
        "audited_sources": [
            "compiler-zone-prefixes",
            "live-node-netns-ipv4-addresses",
            "live-node-netns-non-default-ipv4-routes",
        ],
    }


def _foundation_result(request) -> dict[str, object]:
    return {
        "shared_active_generation_map": True,
        "collision_audit": _collision_audit_result(request),
        "installed_node_addresses": sorted(
            node["key"] for node in request["nodes"]
        ),
    }


class _FakeHelper:
    instances = []

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
    instances = []
    preparation_error: BaseException | None = None
    activation_error: BaseException | None = None
    reclaim_error: BaseException | None = None
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

    def initialize(self, topology_signature, **facts):
        self._resident_generation_capacity = (
            len(facts["capacity_boundaries"])
            if facts["preparation_lookahead"] is None
            else facts["preparation_boundary_depth"] + 1
        )
        return {
            "shared_active_generation_map": True,
            "node_count": 1,
            "capacity_audit": {
                "mode": (
                    "bounded-full-range"
                    if facts["preparation_lookahead"] is None
                    else "rolling"
                ),
                "resident_generation_capacity": self._resident_generation_capacity,
            },
        }

    def generation_capacity_status(self):
        resident = len(self._resident_generations)
        reserved = int(self._pending is not None)
        return {
            "resident_generations": resident,
            "reserved_in_flight_preparations": reserved,
            "resident_generation_capacity": self._resident_generation_capacity,
            "can_start_preparation": (
                reserved == 0 and resident < self._resident_generation_capacity
            ),
        }

    def start_preparation(self, boundary):
        if not self.generation_capacity_status()["can_start_preparation"]:
            raise _CapacityAuditError("resident generation capacity is exhausted")
        generation = len(self.prepared)
        self.operations.append(("prepare", generation))
        self.materialization_calls += 1
        pending = SimpleNamespace(
            boundary=boundary,
            state=_materialize_boundary(self._latest_state, boundary),
            generation=generation,
        )
        self._pending = pending
        return pending, {"generation": generation}

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
        if self.activation_release is not None:
            if not self.activation_release.wait(timeout=2):
                raise RuntimeError("test activation release timed out")
        self.active = ready
        return {"active_generation": ready.generation, "generation_publications": 1}

    def retirement_handles(self, prior, active):
        return prior.generation, None

    def reclaim_forwarding(self, generation):
        if self.reclaim_wait_for_activation is not None:
            if not self.reclaim_wait_for_activation.wait(timeout=2):
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
        values = tuple(resources) if not isinstance(
            resources, _RetiredPhysicalRealization
        ) else resources.resources
        self.reclaimed_resources.append(values)
        self.operations.append(("reclaim-resources", values))
        return {"deleted_connections": len(values)}

    def cleanup(self):
        self.cleanup_calls += 1
        return {"pin_root_removed": True, "errors": []}

    def runtime_node_addresses(self):
        return {NODE: "192.0.2.1"}

    def working_set_counts(self):
        return {
            "prepared_boundary_count": len(self.prepared),
            "realization_state_count": len(self.prepared),
            "resident_generation_count": len(self._resident_generations),
            "prepared_physical_count": 0,
        }


@pytest.fixture(autouse=True)
def fake_runtime(monkeypatch):
    _FakeHelper.instances.clear()
    _FakeRuntimeRealization.instances.clear()
    _FakeRuntimeRealization.preparation_error = None
    _FakeRuntimeRealization.activation_error = None
    _FakeRuntimeRealization.reclaim_error = None
    _FakeRuntimeRealization.activation_entered = None
    _FakeRuntimeRealization.activation_release = None
    _FakeRuntimeRealization.reclaim_wait_for_activation = None
    _FakeRuntimeRealization.reclaim_progress = None
    binding = runner_module._RuntimeBinding(
        node_id=NODE,
        service_name="node",
        container_id="container-id",
        container_name="node",
        image_id="image-id",
        pid=123,
        netns_inode=456,
        started_at="now",
    )
    monkeypatch.setattr(runner_module, "_PrivilegedHelper", _FakeHelper)
    monkeypatch.setattr(runner_module, "_TcBpfRuntimeRealization", _FakeRuntimeRealization)
    monkeypatch.setattr(BpfRunner, "_discover_runtime_bindings", lambda self, first: {NODE: binding})
    monkeypatch.setattr(BpfRunner, "_assert_foundation_current", lambda self: None)


def _supplier(*values):
    pending = iter((*values, None))
    return lambda: next(pending)


def _wait_for(predicate) -> None:
    for _ in range(200):
        if predicate():
            return
        real_time.sleep(0.005)
    raise AssertionError("background Maintenance did not converge")


def test_durable_runner_history_bounds_recent_memory_without_losing_events(
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

    snapshot = runner.inspection_snapshot()
    assert len(runner._events) == 512
    assert snapshot["runner"]["working_set"]["recent_event_count"] == 512
    assert snapshot["runner"]["working_set"]["durable_event_count"] == 1_005
    assert snapshot["event_history"]["scope"] == "recent-only"
    assert len(snapshot["events"]) == 512
    assert snapshot["events"][-1]["generated_index"] == 1_004
    complete = read_runner_event_history(path)
    assert len(complete) == 1_005
    assert complete[0]["generated_index"] == 0
    assert complete[-1]["generated_index"] == 1_004
    assert sum(1 for _ in path.open(encoding="utf-8")) == 1_006


def test_direct_runner_events_are_bounded_and_report_recent_only() -> None:
    runner = BpfRunner(
        compose_project_name="runner-recent-events",
        future_realization_supplier=lambda: None,
        preparation_lookahead=None,
        preparation_boundary_depth=None,
    )
    for index in range(1_005):
        runner._record("synthetic_progress", generated_index=index)

    snapshot = runner.inspection_snapshot()
    assert len(snapshot["events"]) == 512
    assert snapshot["event_history"] == {
        "scope": "recent-only",
        "returned_count": 512,
        "total_count": 1_005,
        "durable_history_enabled": False,
    }
    assert snapshot["runner"]["working_set"]["durable_event_count"] == 0
    assert snapshot["runner"]["working_set"]["recorded_event_count"] == 1_005


def test_1000_boundary_runner_input_and_event_working_sets_remain_bounded(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(BpfRunner, "_maintenance_loop", lambda self: None)
    next_index = 0

    def supply():
        nonlocal next_index
        index = next_index
        next_index += 1
        active = index % 2 == 0
        nominal = START + timedelta(seconds=index)
        operation = _ZoneEdgeIntent(
            nominal,
            DifferenceOperation.ADDED if active else DifferenceOperation.REMOVED,
            "zone-a",
            "198.18.0.1/32",
            None if active else NODE,
            NODE if active else None,
        )
        return BpfCompiledRealization._create(
            start_slice_id=index,
            start_timestamp=None if index == 0 else nominal,
            end_slice_id=index + 1,
            end_timestamp=nominal + timedelta(seconds=1),
            resource_realizations=(),
            parameter_realizations=(),
            forwarding_realizations=(),
            zone_edge_realizations=(operation,),
            constraints=(),
            intended_state=_IntendedState(
                (),
                (),
                (),
                (),
                (("zone-a", "198.18.0.1/32"),),
            ),
            topology_signature=_TopologySignature((NODE,), ("zone-a",)),
        )

    runner = BpfRunner(
        compose_project_name="runner-1000-boundaries",
        future_realization_supplier=supply,
        preparation_lookahead=timedelta(seconds=100),
        preparation_boundary_depth=1,
    )
    runner._enable_event_history(tmp_path / "runner-events.jsonl")
    runner.prepare()
    runner._maintenance_thread.join(timeout=1)
    peaks = {
        "realizations": 0,
        "boundaries": 0,
        "ready": 0,
        "resident_generations": 0,
    }
    for index in range(1_005):
        if index:
            runner._maintain_until_satisfied()
        peaks["realizations"] = max(
            peaks["realizations"], len(runner._future_realizations)
        )
        peaks["boundaries"] = max(peaks["boundaries"], len(runner._boundaries))
        peaks["ready"] = max(peaks["ready"], len(runner._ready))
        runtime = _FakeRuntimeRealization.instances[0]
        peaks["resident_generations"] = max(
            peaks["resident_generations"], len(runtime._resident_generations)
        )
        runner.activate_next()

    snapshot = runner.inspection_snapshot()
    assert next_index == 1_005
    assert peaks == {
        "realizations": 2,
        "boundaries": 1,
        "ready": 1,
        "resident_generations": 2,
    }
    assert len(runner._future_realizations) == 1
    assert len(runner._boundaries) == 0
    assert len(runner._events) == 512
    assert snapshot["runner"]["working_set"]["durable_event_count"] > 10_000
    assert snapshot["event_history"]["scope"] == "recent-only"
    assert len(snapshot["events"]) == 512
    assert len(read_runner_event_history(tmp_path / "runner-events.jsonl")) == (
        snapshot["runner"]["working_set"]["durable_event_count"]
    )
    runner.cleanup()


def test_canonical_runner_preparation_consumes_only_a_rolling_prefix(tmp_path) -> None:
    simulator = create_mini_satellite_internet_simulator(
        build_mini_satellite_internet_emulator()
    )
    slicer = simulator.get_slicer()
    planner = _create_mini_satellite_internet_planner(simulator)
    slicer._enable_durable_history(tmp_path / "slice-history.jsonl")
    planner._enable_durable_history(tmp_path / "planning-history.jsonl")
    pull = _RollingRealizationSupplier(
        slicer,
        planner,
        generation_lookahead=timedelta(seconds=300),
        compiled_history_path=tmp_path / "compiled-history.jsonl",
    )
    runner = BpfRunner(
        compose_project_name="canonical-rolling-prefix",
        future_realization_supplier=pull,
        preparation_lookahead=timedelta(seconds=60),
        preparation_boundary_depth=8,
    )

    assert slicer.get_latest_slice_id() == 0
    assert pull._realization_count == 0
    runner.prepare()

    assert slicer.get_latest_slice_id() == 34
    assert planner._publication_count == 2
    assert pull._realization_count == 2
    assert pull._input_exhausted is False
    assert runner.inspection_snapshot()["runner"]["ready_depth_at_capture"] == 8
    runner.cleanup()
    before_later_pull = (
        slicer.get_latest_slice_id(),
        planner._publication_count,
        pull._realization_count,
    )
    assert pull() is not None
    assert before_later_pull[0] == 34
    assert slicer.get_latest_slice_id() == 38
    assert planner._publication_count == before_later_pull[1] + 1
    assert pull._realization_count == before_later_pull[2] + 1


def test_public_surface_has_only_approved_runner_lifecycle_and_inspection(
    tmp_path,
) -> None:
    assert bpf.__all__ == ["BpfCompiledRealization", "BpfRunner", "compile_bpf"]
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
            compose_project_name="runner-test\nother",
            future_realization_supplier=lambda: None,
            preparation_boundary_depth=1,
        )
    with pytest.raises(ValueError, match="non-negative"):
        BpfRunner(
            compose_project_name="runner-test",
            future_realization_supplier=lambda: None,
            preparation_lookahead=timedelta(seconds=-1),
            preparation_boundary_depth=1,
        )
    with pytest.raises(TypeError, match="timedelta"):
        BpfRunner(
            compose_project_name="runner-test",
            future_realization_supplier=lambda: None,
            preparation_lookahead=1,
            preparation_boundary_depth=1,
        )
    for invalid in (None, True, False, 0.5):
        with pytest.raises(TypeError, match="positive int"):
            BpfRunner(
                compose_project_name="runner-test",
                future_realization_supplier=lambda: None,
                preparation_boundary_depth=invalid,
            )
    for invalid in (0, -1):
        with pytest.raises(ValueError, match="positive"):
            BpfRunner(
                compose_project_name="runner-test",
                future_realization_supplier=lambda: None,
                preparation_boundary_depth=invalid,
            )
    with pytest.raises(ValueError, match="must be None"):
        BpfRunner(
            compose_project_name="runner-test",
            future_realization_supplier=lambda: None,
            preparation_lookahead=None,
            preparation_boundary_depth=1,
        )
    BpfRunner(
        compose_project_name="runner-test",
        future_realization_supplier=lambda: None,
        preparation_lookahead=None,
        preparation_boundary_depth=None,
    )


def test_runner_discovers_only_the_exact_project_and_ignores_auxiliaries(
    monkeypatch,
) -> None:
    runner = BpfRunner(
        compose_project_name="runner-g2",
        future_realization_supplier=lambda: None,
        preparation_boundary_depth=1,
    )
    commands = []

    def execute(command, *, input_text=None):
        commands.append(tuple(command))
        return SimpleNamespace(
            returncode=0,
            stdout="auxiliary-id\nnode-id\n",
            stderr="",
        )

    inspected = [
        {
            "Id": "auxiliary-id",
            "Name": "/auxiliary",
            "Image": "auxiliary-image",
            "Config": {
                "Labels": {
                    "com.docker.compose.project": "runner-g2",
                }
            },
            "State": {"Running": False, "Pid": 0, "StartedAt": ""},
        },
        {
            "Id": "node-id",
            "Name": "/node",
            "Image": "node-image",
            "Config": {
                "Labels": {
                    "com.docker.compose.project": "runner-g2",
                    "com.docker.compose.service": "node",
                    "org.seedsecuritylabs.seedemu.meta.asn": "65001",
                    "org.seedsecuritylabs.seedemu.meta.skyseed.entity_type": (
                        "Satellite"
                    ),
                    "org.seedsecuritylabs.seedemu.meta.skyseed.satellite_id": (
                        "sat-1"
                    ),
                }
            },
            "State": {"Running": True, "Pid": 123, "StartedAt": "now"},
        },
    ]
    monkeypatch.setattr(runner_module, "_execute", execute)
    monkeypatch.setattr(BpfRunner, "_inspect", lambda self, _ids: inspected)
    monkeypatch.setattr(
        BpfRunner,
        "_container_netns_inode",
        lambda self, _id: 456,
    )

    bindings = _REAL_DISCOVER_RUNTIME_BINDINGS(
        runner,
        _realization(0, 4, "zone-a"),
    )

    assert set(bindings) == {NODE}
    assert bindings[NODE].image_id == "node-image"
    assert commands == [
        (
            "docker",
            "ps",
            "--all",
            "--quiet",
            "--filter",
            "label=com.docker.compose.project=runner-g2",
        )
    ]


@pytest.mark.parametrize(
    ("compiled_nodes", "live_entity_ids", "expected_message"),
    (
        ((NODE, NODE_2), (NODE,), "missing=.*sat-2"),
        ((NODE,), (NODE, NODE_2), "unexpected=.*sat-2"),
    ),
)
def test_runner_requires_the_exact_compiled_topology_node_set(
    monkeypatch,
    compiled_nodes,
    live_entity_ids,
    expected_message,
) -> None:
    runner = BpfRunner(
        compose_project_name="runner-exact-set",
        future_realization_supplier=lambda: None,
        preparation_boundary_depth=1,
    )
    monkeypatch.setattr(
        runner_module,
        "_execute",
        lambda command, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="".join(
                f"container-{index}\n"
                for index, _node_id in enumerate(live_entity_ids)
            ),
            stderr="",
        ),
    )
    inspected = []
    for index, node_id in enumerate(live_entity_ids):
        inspected.append(
            {
                "Id": f"container-{index}",
                "Name": f"/node-{index}",
                "Image": "node-image",
                "Config": {
                    "Labels": {
                        "com.docker.compose.project": "runner-exact-set",
                        "com.docker.compose.service": f"node-{index}",
                        "org.seedsecuritylabs.seedemu.meta.asn": str(
                            node_id.asn
                        ),
                        "org.seedsecuritylabs.seedemu.meta.skyseed.entity_type": (
                            "Satellite"
                        ),
                        "org.seedsecuritylabs.seedemu.meta.skyseed.satellite_id": (
                            node_id.entity_id
                        ),
                    }
                },
                "State": {"Running": True, "Pid": 100 + index, "StartedAt": "now"},
            }
        )
    monkeypatch.setattr(BpfRunner, "_inspect", lambda self, _ids: inspected)
    monkeypatch.setattr(
        BpfRunner,
        "_container_netns_inode",
        lambda self, _id: 456,
    )

    first = _zone_realization(
        0,
        4,
        ("zone-a",),
        topology_zones=("zone-a", "zone-b"),
        topology_nodes=compiled_nodes,
    )
    with pytest.raises(RuntimeError, match=expected_message):
        _REAL_DISCOVER_RUNTIME_BINDINGS(runner, first)


def test_runner_does_not_attach_a_previous_generation(monkeypatch) -> None:
    runner = BpfRunner(
        compose_project_name="runner-g2",
        future_realization_supplier=lambda: None,
        preparation_boundary_depth=1,
    )

    def execute(command, *, input_text=None):
        assert command[-1] == "label=com.docker.compose.project=runner-g2"
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(runner_module, "_execute", execute)

    with pytest.raises(RuntimeError, match="start the generated deployment"):
        _REAL_DISCOVER_RUNTIME_BINDINGS(
            runner,
            _realization(0, 4, "zone-a"),
        )


def test_runner_helper_does_not_inherit_compose_project_ownership(
    monkeypatch,
) -> None:
    commands = []

    def execute(command, *, input_text=None):
        commands.append(tuple(command))
        return SimpleNamespace(
            returncode=1 if command[1] == "inspect" else 0,
            stdout="",
            stderr="",
        )

    monkeypatch.setattr(runner_module, "_execute", execute)
    helper = _REAL_PRIVILEGED_HELPER(
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


def test_runner_depends_only_on_opaque_runtime_realization_seam() -> None:
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


def test_runner_events_freeze_nested_capacity_evidence(tmp_path) -> None:
    runner = BpfRunner(
        compose_project_name="runner-test",
        future_realization_supplier=lambda: None,
        preparation_boundary_depth=1,
    )
    capacity = {"per_node": [{"key": NODE.value, "routes": 17}]}
    runner._record("capacity", capacity=capacity)
    capacity.clear()
    first = runner.inspection_snapshot()["events"]
    first[0]["capacity"]["per_node"][0]["routes"] = 0
    assert runner.inspection_snapshot()["events"][0]["capacity"] == {
        "per_node": [{"key": NODE.value, "routes": 17}]
    }


def test_inspection_snapshot_is_detached_and_has_no_lifecycle_side_effect(
    tmp_path,
    monkeypatch,
) -> None:
    runner = BpfRunner(
        compose_project_name="runner-inspection",
        future_realization_supplier=_supplier(_realization(0, 4, "zone-a")),
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
    assert set(before) == {
        "format_version",
        "runner",
        "event_history",
        "events",
    }
    assert before["runner"] == {
        "lifecycle_state": "cleaned",
        "execution_mode": "manual",
        "frontiers": {
            "last_active": START.isoformat(),
            "ready_at_capture": None,
            "input": (START + timedelta(seconds=4)).isoformat(),
        },
        "input_exhausted": True,
            "preparation_lookahead": {
                "mode": "bounded-full-range",
                "seconds": None,
            },
            "preparation_boundary_depth": None,
            "activation_count": 1,
            "ready_count_at_capture": 0,
            "ready_depth_at_capture": 0,
            "ready_limiting_reason": "input",
        "runtime_foundation": {
            "compose_project_name": "runner-inspection",
            "foundation_token": runner._foundation_token,
            "expected_node_count": 1,
            "bound_node_count": 1,
            "bindings": (
                {
                    "node_id": NODE.value,
                    "service": "node",
                    "container_id": "container-id",
                    "container_name": "node",
                    "image_id": "image-id",
                    "pid": 123,
                    "netns_inode": 456,
                    "started_at": "now",
                    "runtime_ipv4": "192.0.2.1",
                },
            ),
        },
        "maintenance": {
            "in_flight": False,
            "failed": False,
            "pending_forwarding_reclaim": 0,
            "pending_physical_reclaim": 0,
        },
        "working_set": {
            "received_realization_count": 1,
            "retained_realization_count": 1,
            "retained_future_boundary_count": 0,
            "recent_event_count": 19,
            "recorded_event_count": 19,
            "durable_event_count": 0,
            "prepared_boundary_count": 1,
            "realization_state_count": 1,
            "resident_generation_count": 1,
            "prepared_physical_count": 0,
        },
        "traffic_telemetry": {"enabled": False},
    }
    assert before["events"][-1]["event"] == "cleanup_end"

    before["runner"]["frontiers"]["last_active"] = "changed"
    before["runner"]["runtime_foundation"]["bindings"][0]["pid"] = 0
    before["events"][0]["event"] = "changed"
    after = runner.inspection_snapshot()
    assert after["runner"]["frontiers"]["last_active"] == START.isoformat()
    assert after["events"][0]["event"] == "preparation_start"
    assert after["runner"]["lifecycle_state"] == "cleaned"
    assert after["runner"]["runtime_foundation"]["bindings"][0]["pid"] == 123
    assert not isinstance(
        after["runner"]["runtime_foundation"]["bindings"][0],
        runner_module._RuntimeBinding,
    )


def test_full_range_prepares_and_activates_multiple_boundaries(tmp_path) -> None:
    first = _realization(0, 4, "zone-a")
    second = _realization(4, 8, "zone-b")
    runner = BpfRunner(
        compose_project_name="runner-test",
        future_realization_supplier=_supplier(first, second),
        preparation_lookahead=None,
        preparation_boundary_depth=None,
    )
    runner.prepare()
    assert [value.boundary.nominal_time for value in _FakeRuntimeRealization.instances[0].prepared] == [
        START,
        START + timedelta(seconds=4),
    ]
    assert _FakeRuntimeRealization.instances[0].materialization_calls == 2
    assert _FakeRuntimeRealization.instances[0].active is None
    runner.activate_next()
    runner.activate_next()
    assert runner._active_frontier == START + timedelta(seconds=4)
    _wait_for(lambda: 0 in _FakeRuntimeRealization.instances[0].reclaimed_generations)
    runner.cleanup()
    assert _FakeHelper.instances[0].stopped


def test_zero_lookahead_rolls_one_next_boundary_after_activation(tmp_path) -> None:
    runner = BpfRunner(
        compose_project_name="runner-test",
        future_realization_supplier=_supplier(
            _realization(0, 4, "zone-a"),
            _realization(4, 8, "zone-b"),
        ),
        preparation_boundary_depth=1,
    )
    runner.prepare()
    assert len(runner._ready) == 1
    runner.activate_next()
    _wait_for(lambda: len(runner._ready) == 1)
    assert runner._ready[0].boundary.nominal_time == START + timedelta(seconds=4)
    runner.cleanup()


def test_time_bound_stops_continuous_startup_ready_prefix() -> None:
    zones = ("zone-a", "zone-b", "zone-c")
    runner = BpfRunner(
        compose_project_name="runner-time-bound",
        future_realization_supplier=_supplier(
            *(
                _zone_realization(
                    start,
                    start + 4,
                    (zone,),
                    topology_zones=zones,
                )
                for start, zone in zip((0, 4, 8), zones)
            )
        ),
        preparation_lookahead=timedelta(seconds=4),
        preparation_boundary_depth=8,
    )

    runner.prepare()

    assert [value.boundary.nominal_time for value in runner._ready] == [
        START,
        START + timedelta(seconds=4),
    ]
    assert [value.predecessor_time for value in runner._ready] == [None, START]
    assert runner.inspection_snapshot()["runner"]["ready_limiting_reason"] == "time"
    runner.cleanup()


def test_depth_bound_counts_startup_and_post_active_future_ready_only() -> None:
    zones = ("zone-a", "zone-b", "zone-c", "zone-d")
    runner = BpfRunner(
        compose_project_name="runner-depth-bound",
        future_realization_supplier=_supplier(
            *(
                _zone_realization(
                    start,
                    start + 4,
                    (zone,),
                    topology_zones=zones,
                )
                for start, zone in zip((0, 4, 8, 12), zones)
            )
        ),
        preparation_lookahead=timedelta(seconds=100),
        preparation_boundary_depth=2,
    )

    runner.prepare()
    assert len(runner._ready) == 2
    assert runner.inspection_snapshot()["runner"]["ready_limiting_reason"] == "depth"

    runner.activate_next()
    _wait_for(lambda: len(runner._ready) == 2)
    assert runner._active is not None
    assert runner._active.boundary.nominal_time == START
    assert [value.boundary.nominal_time for value in runner._ready] == [
        START + timedelta(seconds=4),
        START + timedelta(seconds=8),
    ]
    assert runner.inspection_snapshot()["runner"]["ready_depth_at_capture"] == 2
    runner.cleanup()


def test_runner_rejects_later_input_that_changes_a_known_semantic_boundary() -> None:
    first = _realization(0, 4, "zone-a")
    later = _realization(4, 8, "zone-b")
    changed = BpfCompiledRealization._create(
        start_slice_id=later.start_slice_id,
        start_timestamp=later.start_timestamp,
        end_slice_id=later.end_slice_id,
        end_timestamp=later.end_timestamp,
        resource_realizations=later.resource_realizations,
        parameter_realizations=later.parameter_realizations,
        forwarding_realizations=later.forwarding_realizations,
        zone_edge_realizations=(
            _ZoneEdgeIntent(
                START,
                DifferenceOperation.ADDED,
                "zone-b",
                "198.18.0.2/32",
                None,
                NODE,
            ),
        ),
        constraints=later.constraints,
        intended_state=later._intended_state,
        topology_signature=later._topology_signature,
    )
    runner = BpfRunner(
        compose_project_name="runner-test",
        future_realization_supplier=_supplier(first, changed),
        preparation_boundary_depth=1,
    )

    runner._pull_one()
    with pytest.raises(
        ValueError,
        match="new compiled work changed an existing nominal boundary",
    ):
        runner._pull_one()


def test_concurrent_manual_main_attempt_fails_before_second_mutation(tmp_path) -> None:
    entered = Event()
    release = Event()
    _FakeRuntimeRealization.activation_entered = entered
    _FakeRuntimeRealization.activation_release = release
    runner = BpfRunner(
        compose_project_name="runner-test",
        future_realization_supplier=_supplier(_realization(0, 4, "zone-a")),
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
    assert not second.is_alive()
    assert len(failures) == 1
    assert "activation in flight" in str(failures[0])
    assert _FakeRuntimeRealization.instances[0].activation_calls == 1
    assert _FakeRuntimeRealization.instances[0].active is None
    release.set()
    first.join(timeout=1)
    assert not first.is_alive()
    assert _FakeRuntimeRealization.instances[0].active.generation == 0
    runner.cleanup()


def test_maintenance_progresses_while_one_main_activation_is_in_flight(
    tmp_path,
) -> None:
    activation_entered = Event()
    activation_release = Event()
    reclaim_progress = Event()
    _FakeRuntimeRealization.activation_entered = activation_entered
    _FakeRuntimeRealization.activation_release = activation_release
    _FakeRuntimeRealization.reclaim_wait_for_activation = activation_entered
    _FakeRuntimeRealization.reclaim_progress = reclaim_progress
    runner = BpfRunner(
        compose_project_name="runner-test",
        future_realization_supplier=_supplier(_realization(0, 4, "zone-a")),
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
    assert activation_entered.wait(timeout=1)
    assert reclaim_progress.wait(timeout=1)
    assert main.is_alive()
    assert 91 in _FakeRuntimeRealization.instances[0].reclaimed_generations
    activation_release.set()
    main.join(timeout=1)
    assert not main.is_alive()
    runner.cleanup()


def test_forward_deficit_precedes_reclaim_then_both_reclaim_classes_progress(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(BpfRunner, "_maintenance_loop", lambda self: None)
    runner = BpfRunner(
        compose_project_name="runner-test",
        future_realization_supplier=_supplier(
            _realization(0, 4, "zone-a"),
            _realization(4, 8, "zone-b"),
        ),
        preparation_boundary_depth=1,
    )
    runner.prepare()
    runner._maintenance_thread.join(timeout=1)
    runner.activate_next()
    dataplane = _FakeRuntimeRealization.instances[0]
    dataplane.operations.clear()
    retired = object()
    with runner._condition:
        runner._reclaim_forwarding.append(73)
        runner._reclaim_physical.append((retired,))
    runner._maintain_until_satisfied()
    assert runner._ready[0].boundary.nominal_time == START + timedelta(seconds=4)
    assert dataplane.reclaimed_generations == [73]
    assert dataplane.reclaimed_resources == [(retired,)]
    assert dataplane.operations == [
        ("prepare", 1),
        ("reclaim-generation", 73),
        ("reclaim-resources", (retired,)),
    ]
    assert any(
        value["event"] == "maintenance_forward_priority"
        for value in runner.inspection_snapshot()["events"]
    )
    runner.cleanup()


def test_manual_and_automatic_modes_are_mutually_exclusive(tmp_path) -> None:
    runner = BpfRunner(
        compose_project_name="runner-test",
        future_realization_supplier=_supplier(_realization(0, 4, "zone-a")),
        preparation_lookahead=None,
        preparation_boundary_depth=None,
    )
    runner.prepare()
    runner.activate_next()
    with pytest.raises(RuntimeError, match="exclusive"):
        runner.run()
    runner.cleanup()


def test_automatic_run_uses_relative_monotonic_schedule(tmp_path, monkeypatch) -> None:
    clock = [100.0]
    sleeps = []

    def fake_sleep(duration):
        sleeps.append(duration)
        clock[0] += duration

    runner = BpfRunner(
        compose_project_name="runner-test",
        future_realization_supplier=_supplier(
            _realization(0, 4, "zone-a"),
            _realization(4, 8, "zone-b"),
        ),
        preparation_lookahead=None,
        preparation_boundary_depth=None,
    )
    runner.prepare()
    monkeypatch.setattr(runner_module, "monotonic", lambda: clock[0])
    monkeypatch.setattr(runner_module, "sleep", fake_sleep)
    runner.run()
    assert sleeps == [4.0]
    assert runner._active_frontier == START + timedelta(seconds=4)
    runner.cleanup()


def test_automatic_deadline_miss_fails_without_mutating_missing_boundary(
    tmp_path, monkeypatch
) -> None:
    clock = [10.0]
    runner = BpfRunner(
        compose_project_name="runner-test",
        future_realization_supplier=_supplier(
            _realization(0, 4, "zone-a"),
            _realization(4, 8, "zone-b"),
        ),
        preparation_lookahead=None,
        preparation_boundary_depth=None,
    )
    runner.prepare()
    runner._ready.pop()
    monkeypatch.setattr(runner_module, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        runner_module,
        "sleep",
        lambda duration: clock.__setitem__(0, clock[0] + duration),
    )
    with pytest.raises(RuntimeError, match="not READY"):
        runner.run()
    assert _FakeRuntimeRealization.instances[0].active.generation == 0
    assert any(value["event"] == "deadline_miss" for value in runner.inspection_snapshot()["events"])
    runner.cleanup()


def test_ready_predecessor_staleness_fails_before_mutation(tmp_path) -> None:
    runner = BpfRunner(
        compose_project_name="runner-test",
        future_realization_supplier=_supplier(_realization(0, 4, "zone-a")),
        preparation_lookahead=None,
        preparation_boundary_depth=None,
    )
    runner.prepare()
    runner._active_frontier = START - timedelta(seconds=1)
    with pytest.raises(RuntimeError, match="predecessor"):
        runner.activate_next()
    assert _FakeRuntimeRealization.instances[0].active is None
    runner.cleanup()


def test_later_boundary_resolver_uses_constraints_not_tuple_order() -> None:
    operations = (
        _ZoneEdgeIntent(START, DifferenceOperation.ADDED, "zone-a", "198.18.0.1/32", None, NODE),
        _ZoneEdgeIntent(START, DifferenceOperation.ADDED, "zone-b", "198.18.0.2/32", None, NODE),
        _ZoneEdgeIntent(START + timedelta(seconds=1), DifferenceOperation.CHANGED, "zone-a", "198.18.0.1/32", NODE, NODE),
    )
    realization = BpfCompiledRealization._create(
        start_slice_id=0,
        start_timestamp=None,
        end_slice_id=4,
        end_timestamp=START + timedelta(seconds=4),
        resource_realizations=(), parameter_realizations=(), forwarding_realizations=(),
        zone_edge_realizations=operations,
        constraints=(
            _OrderingConstraint(_IntentReference("zone-edge", 1), _IntentReference("zone-edge", 0), "reverse"),
            _OrderingConstraint(_IntentReference("zone-edge", 0), _IntentReference("zone-edge", 2), "later"),
        ),
        intended_state=_IntendedState((), (), (), (), ()),
        topology_signature=_TopologySignature((NODE,), ("zone-a", "zone-b")),
    )
    boundaries = _resolve_nominal_boundaries((realization,))
    assert [value.nominal_time for value in boundaries] == [START, START + timedelta(seconds=1)]
    assert [value.index for value in boundaries[0].operations] == [1, 0]


def test_private_worker_program_has_shared_generation_lookup_and_owned_pin_scope() -> None:
    program = _tc_program(10, 11, 12)
    assert len(program) % 8 == 0
    assert len(program) >= 40 * 8
    assert _pin_root("/sys/fs/bpf/skyseed-v2/run-0123456789ab").name == "run-0123456789ab"
    with pytest.raises(ValueError, match="outside"):
        _pin_root("/sys/fs/bpf/not-owned")


def test_private_generation_key_and_future_link_mutation_are_isolated() -> None:
    assert _key_buffer((7, 19)).raw[:8] == struct.pack("=II", 7, 19)
    prepare_source = inspect.getsource(_prepare_boundary)
    activate_source = inspect.getsource(_activate_boundary)
    assert "link set dev {name} up" not in prepare_source
    assert "prepared_links_admin_down" in prepare_source
    assert "link set dev {_assert_interface_name(name)} up" in activate_source
    assert 'root / "active_generation"' in activate_source
    assert "updated_nodes" not in activate_source
    assert "updated_interfaces" not in activate_source


def test_active_ground_access_derives_only_ground_local_peer_route() -> None:
    access = ConnectionId(
        ConnectionKind.GROUND_STATION_ACCESS,
        GROUND,
        NODE,
    )
    dataplane = _TcBpfRuntimeRealization(
        executor=SimpleNamespace(),
        pin_root="/sys/fs/bpf/skyseed-v2/run-access-peer",
        bindings={GROUND: object(), NODE: object()},
    )
    dataplane._node_addresses = {
        GROUND: "100.64.0.1",
        NODE: "100.64.0.2",
    }
    dataplane._nodes = (
        {"key": GROUND.value},
        {"key": NODE.value},
    )
    empty = _MaterializedDataplaneState((), (), (), ())
    active = _MaterializedDataplaneState((access,), (), (), ())
    boundary = SimpleNamespace(nominal_time=START, operations=())

    establish = dataplane._activation_request(
        boundary,
        previous_state=empty,
        state=active,
        expected_active_generation=0xFFFFFFFF,
        generation=0,
    )
    withdraw = dataplane._activation_request(
        boundary,
        previous_state=active,
        state=empty,
        expected_active_generation=0,
        generation=1,
    )
    by_node = {value["key"]: value for value in establish["nodes"]}
    withdrawn_by_node = {value["key"]: value for value in withdraw["nodes"]}

    assert by_node[GROUND.value]["access_peer_route_replace"] == [
        {"destination": "100.64.0.2", "interface": by_node[GROUND.value]["link_up"][0]}
    ]
    assert by_node[NODE.value]["access_peer_route_replace"] == []
    assert withdrawn_by_node[GROUND.value]["access_peer_route_delete"] == [
        {
            "destination": "100.64.0.2",
            "interface": withdrawn_by_node[GROUND.value]["link_down"][0],
        }
    ]
    generation_nodes = dataplane._generation_nodes(active)
    ground_generation = next(
        value for value in generation_nodes if value["key"] == GROUND.value
    )
    assert ground_generation["routes"] == [
        {"destination": "100.64.0.1", "interface": None}
    ]


def test_privileged_activation_batches_access_peer_route_and_neighbour(
    monkeypatch,
) -> None:
    batches: list[str] = []
    monkeypatch.setattr(
        privileged_runtime_command_module,
        "_open_pinned",
        lambda _path: 10,
    )
    monkeypatch.setattr(
        privileged_runtime_command_module,
        "_lookup_map",
        lambda _fd, _key: 0xFFFFFFFF,
    )
    monkeypatch.setattr(
        privileged_runtime_command_module,
        "_update_map",
        lambda *_facts: None,
    )
    monkeypatch.setattr(
        privileged_runtime_command_module,
        "_assert_netns",
        lambda _node: None,
    )
    monkeypatch.setattr(
        privileged_runtime_command_module,
        "_interface_index",
        lambda *_facts: 7,
    )

    def in_netns(_pid, command, *, input_text=None, **_facts):
        if command[:2] == ["ip", "-force"]:
            batches.append(input_text)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(
        privileged_runtime_command_module,
        "_in_netns",
        in_netns,
    )
    monkeypatch.setattr(
        privileged_runtime_command_module.os,
        "close",
        lambda _fd: None,
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
                        {"destination": "100.64.0.2", "interface": interface},
                    ),
                    "access_peer_route_replace": (
                        {"destination": "100.64.0.3", "interface": interface},
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


def test_node_address_capacity_scales_deterministically_beyond_254_nodes() -> None:
    satellites = (NODE,) + tuple(
        NodeId(65001, "satellite", f"sat-{index:04d}")
        for index in range(2, 301)
    )
    ground_stations = tuple(
        NodeId(65001, "ground-station", f"gs-{index:02d}")
        for index in range(1, 5)
    )
    topology_nodes = satellites + ground_stations
    zones = ("zone-a", "zone-b")
    realization = _zone_realization(
        0,
        4,
        ("zone-a",),
        topology_zones=zones,
        topology_nodes=topology_nodes,
    )
    boundaries = _resolve_nominal_boundaries((realization,))
    bindings = {
        node_id: SimpleNamespace(pid=index + 100, netns_inode=index + 200)
        for index, node_id in enumerate(topology_nodes)
    }

    class _Executor:
        def __init__(self) -> None:
            self.requests = []

        def execute(self, action, request):
            self.requests.append((action, request))
            if action == "audit_node_addresses":
                return _collision_audit_result(request)
            if action == "initialize":
                return _foundation_result(request)
            return {
                "deleted_connections": 0,
                "deleted_node_addresses": len(request.get("nodes", ())),
                "pin_root_removed": True,
                "errors": [],
            }

    def initialize(executor, ordered_bindings, suffix):
        dataplane = _TcBpfRuntimeRealization(
            executor=executor,
            pin_root=f"/sys/fs/bpf/skyseed-v2/run-scale-{suffix}",
            bindings=ordered_bindings,
        )
        result = dataplane.initialize(
            realization._topology_signature,
            zone_prefixes=tuple(prefix for _, prefix in realization.zone_prefixes),
            capacity_boundaries=boundaries,
            preparation_lookahead=None,
            preparation_boundary_depth=None,
        )
        request = next(
            request
            for action, request in executor.requests
            if action == "initialize"
        )
        return dataplane, result, request

    first_executor = _Executor()
    first_dataplane, first_result, first_request = initialize(
        first_executor, bindings, "first"
    )
    second_executor = _Executor()
    second_dataplane, second_result, second_request = initialize(
        second_executor, dict(reversed(tuple(bindings.items()))), "second"
    )

    first_addresses = tuple(
        (node["key"], node["node_address"]) for node in first_request["nodes"]
    )
    second_addresses = tuple(
        (node["key"], node["node_address"]) for node in second_request["nodes"]
    )
    capacity = first_result["capacity_audit"]
    zone_addresses = {
        ip_interface(prefix).ip for _, prefix in realization.zone_prefixes
    }
    node_addresses = {ip_address(address) for _, address in first_addresses}
    assert len(topology_nodes) == 304
    assert len(first_addresses) == len(set(first_addresses)) == 304
    assert len(node_addresses) == 304
    assert tuple(key for key, _ in first_addresses) == tuple(
        node.value
        for node in sorted(
            topology_nodes,
            key=lambda node: (node.asn, node.entity_kind, node.entity_id),
        )
    )
    assert first_addresses == second_addresses
    assert capacity["node_address_requirement"] == 304
    assert capacity["node_address_capacity"] == 510
    assert capacity["node_address_headroom"] == 206
    assert capacity["selected_node_address_range"] == "100.64.0.0/23"
    assert capacity["collision_audit"]["result"] == "passed"
    assert node_addresses.isdisjoint(zone_addresses)
    assert first_result["capacity_audit"] == second_result["capacity_audit"]

    first_dataplane.cleanup()
    cleanup_request = first_executor.requests[-1][1]
    assert all(node["node_address_owned"] for node in cleanup_request["nodes"])
    second_dataplane.cleanup()


def test_node_address_collision_fails_before_initialize_or_cleanup_mutation() -> None:
    realization = _zone_realization(
        0,
        4,
        ("zone-a",),
        topology_zones=("zone-a",),
    )
    boundary = _resolve_nominal_boundaries((realization,))[0]

    class _CollisionExecutor:
        def __init__(self) -> None:
            self.actions = []

        def execute(self, action, request):
            self.actions.append(action)
            if action == "audit_node_addresses":
                raise RuntimeError("live namespace address collision")
            raise AssertionError("collision audit must precede runtime mutation")

    executor = _CollisionExecutor()
    dataplane = _TcBpfRuntimeRealization(
        executor=executor,
        pin_root="/sys/fs/bpf/skyseed-v2/run-address-collision",
        bindings={NODE: SimpleNamespace(pid=101, netns_inode=201)},
    )
    with pytest.raises(_CapacityAuditError, match="collision audit failed"):
        dataplane.initialize(
            realization._topology_signature,
            zone_prefixes=tuple(prefix for _, prefix in realization.zone_prefixes),
            capacity_boundaries=(boundary,),
            preparation_lookahead=None,
            preparation_boundary_depth=None,
        )
    assert executor.actions == ["audit_node_addresses"]
    assert dataplane.cleanup()["pin_root_removed"] is True
    assert executor.actions == ["audit_node_addresses"]


def test_node_address_allocation_rejects_compiled_zone_collision() -> None:
    with pytest.raises(_CapacityAuditError, match="compiled Zone identity"):
        _allocate_node_addresses((NODE,), ("100.64.0.1/32",))


@pytest.mark.parametrize(
    ("address_payload", "route_payload", "source"),
    (
        (
            [{"addr_info": [{"family": "inet", "local": "100.64.0.77", "prefixlen": 24}]}],
            [],
            "address",
        ),
        ([], [{"dst": "100.64.0.0/24"}], "route"),
    ),
)
def test_worker_audits_live_namespace_address_and_route_collisions(
    monkeypatch, address_payload, route_payload, source
) -> None:
    monkeypatch.setattr(privileged_runtime_command_module, "_assert_netns", lambda node: None)

    def execute(_pid, command, **_kwargs):
        payload = address_payload if "address" in command else route_payload
        return SimpleNamespace(stdout=json.dumps(payload))

    monkeypatch.setattr(privileged_runtime_command_module, "_in_netns", execute)
    request = {
        "node_address_range": "100.64.0.0/26",
        "zone_prefixes": ("198.18.0.1/32",),
        "nodes": (
            {
                "key": NODE.value,
                "pid": 101,
                "netns_inode": 201,
                "node_address": "100.64.0.1",
            },
        ),
    }
    with pytest.raises(RuntimeError, match=f"namespace {source}"):
        _audit_node_addresses(request)


def test_worker_audits_before_mutation_and_adds_owned_node_addresses() -> None:
    source = inspect.getsource(_initialize)
    assert source.index("_audit_node_addresses") < source.index("root.mkdir")
    assert "address add {node['node_address']}/32" in source
    assert "address replace {node['node_address']}/32" not in source


def test_worker_partial_install_cleanup_marks_only_owned_node_addresses(
    monkeypatch, tmp_path: Path
) -> None:
    cleanup_requests = []
    nodes = (
        {
            "key": NODE.value,
            "pid": 101,
            "netns_inode": 201,
            "pin_component": "n-first",
            "node_address": "100.64.0.1",
            "active_links_capacity": 1,
            "routes_capacity": 1,
        },
        {
            "key": NODE_2.value,
            "pid": 102,
            "netns_inode": 202,
            "pin_component": "n-second",
            "node_address": "100.64.0.2",
            "active_links_capacity": 1,
            "routes_capacity": 1,
        },
    )
    request = {
        "pin_root": "/sys/fs/bpf/skyseed-v2/run-partial-install",
        "nodes": nodes,
        "inactive_generation": 0xFFFFFFFF,
        "node_address_range": "100.64.0.0/29",
        "zone_prefixes": ("198.18.0.1/32",),
    }

    monkeypatch.setattr(
        privileged_runtime_command_module,
        "_pin_root",
        lambda _value: tmp_path / "pins",
    )
    monkeypatch.setattr(privileged_runtime_command_module, "_assert_netns", lambda _node: None)
    monkeypatch.setattr(
        privileged_runtime_command_module,
        "_audit_node_addresses",
        lambda _request: {"result": "passed"},
    )
    monkeypatch.setattr(
        privileged_runtime_command_module,
        "_create_map",
        lambda *_args, **_kwargs: 10,
    )
    monkeypatch.setattr(privileged_runtime_command_module, "_pin_object", lambda *_args: None)
    monkeypatch.setattr(privileged_runtime_command_module, "_update_map", lambda *_args: None)
    monkeypatch.setattr(privileged_runtime_command_module, "_load_program", lambda *_args: 11)
    monkeypatch.setattr(privileged_runtime_command_module.os, "close", lambda _descriptor: None)

    def execute(pid, _command, **_kwargs):
        if pid == 102:
            raise RuntimeError("second node install failed")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def cleanup(cleanup_request, *, tolerate_errors=False):
        assert tolerate_errors is True
        cleanup_requests.append(cleanup_request)
        return {"errors": []}

    monkeypatch.setattr(privileged_runtime_command_module, "_in_netns", execute)
    monkeypatch.setattr(privileged_runtime_command_module, "_cleanup", cleanup)

    with pytest.raises(RuntimeError, match="second node install failed"):
        _initialize(request)

    ownership = {
        node["key"]: node["node_address_owned"]
        for node in cleanup_requests[0]["nodes"]
    }
    assert ownership == {NODE.value: True, NODE_2.value: False}


def test_worker_cleanup_skips_prepared_but_never_active_zone_addresses(
    monkeypatch,
    tmp_path: Path,
) -> None:
    pin_parent = tmp_path / "pins"
    pin_parent.mkdir()
    pin_root = pin_parent / "run-cleanup"
    address_batches = []
    observed_addresses = iter(
        (
            frozenset(
                {
                    ip_address("100.64.0.1"),
                    ip_address("198.18.0.1"),
                }
            ),
            frozenset(),
        )
    )
    monkeypatch.setattr(privileged_runtime_command_module, "_PIN_PARENT", pin_parent)
    monkeypatch.setattr(privileged_runtime_command_module, "_assert_netns", lambda node: None)
    monkeypatch.setattr(
        privileged_runtime_command_module,
        "_namespace_ipv4_local_addresses",
        lambda node: next(observed_addresses),
    )

    def execute(_pid, command, *, input_text=None, **_kwargs):
        assert command == ["ip", "-force", "-batch", "-"]
        address_batches.append(input_text)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(privileged_runtime_command_module, "_in_netns", execute)
    result = privileged_runtime_command_module._cleanup(
        {
            "pin_root": str(pin_root),
            "nodes": (
                {
                    "key": NODE.value,
                    "pid": 101,
                    "netns_inode": 201,
                    "node_address": "100.64.0.1",
                    "node_address_owned": True,
                    "zone_addresses": (
                        "198.18.0.1",
                        "198.18.0.2",
                    ),
                },
            ),
            "connections": (),
        }
    )

    assert address_batches == [
        "address del 100.64.0.1/32 dev lo\n"
        "address del 198.18.0.1/32 dev lo\n"
    ]
    assert result["deleted_node_addresses"] == 1
    assert result["errors"] == []


def test_worker_cleanup_does_not_infer_exact_address_from_broad_subnet(
    monkeypatch,
    tmp_path: Path,
) -> None:
    pin_parent = tmp_path / "pins"
    pin_parent.mkdir()
    pin_root = pin_parent / "run-cleanup"
    address_queries = []
    monkeypatch.setattr(privileged_runtime_command_module, "_PIN_PARENT", pin_parent)
    monkeypatch.setattr(privileged_runtime_command_module, "_assert_netns", lambda node: None)

    def execute(_pid, command, *, input_text=None, **_kwargs):
        assert input_text is None
        assert command == ["ip", "-j", "-4", "address", "show"]
        address_queries.append(tuple(command))
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                [
                    {
                        "addr_info": [
                            {
                                "family": "inet",
                                "local": "198.18.0.1",
                                "prefixlen": 15,
                            }
                        ]
                    }
                ]
            ),
            stderr="",
        )

    monkeypatch.setattr(privileged_runtime_command_module, "_in_netns", execute)
    result = privileged_runtime_command_module._cleanup(
        {
            "pin_root": str(pin_root),
            "nodes": (
                {
                    "key": NODE.value,
                    "pid": 101,
                    "netns_inode": 201,
                    "node_address": "100.64.0.1",
                    "node_address_owned": False,
                    "zone_addresses": ("198.18.10.25",),
                },
            ),
            "connections": (),
        }
    )

    assert len(address_queries) == 2
    assert result["deleted_node_addresses"] == 0
    assert result["errors"] == []


def test_worker_cleanup_does_not_count_an_already_absent_node_address(
    monkeypatch,
    tmp_path: Path,
) -> None:
    pin_parent = tmp_path / "pins"
    pin_parent.mkdir()
    pin_root = pin_parent / "run-cleanup"
    monkeypatch.setattr(privileged_runtime_command_module, "_PIN_PARENT", pin_parent)
    monkeypatch.setattr(privileged_runtime_command_module, "_assert_netns", lambda node: None)
    monkeypatch.setattr(
        privileged_runtime_command_module,
        "_namespace_ipv4_local_addresses",
        lambda node: frozenset(),
    )

    def unexpected_execute(*_args, **_kwargs):
        raise AssertionError("an absent address must not issue a delete command")

    monkeypatch.setattr(
        privileged_runtime_command_module,
        "_in_netns",
        unexpected_execute,
    )

    result = privileged_runtime_command_module._cleanup(
        {
            "pin_root": str(pin_root),
            "nodes": (
                {
                    "key": NODE.value,
                    "pid": 101,
                    "netns_inode": 201,
                    "node_address": "100.64.0.1",
                    "node_address_owned": True,
                    "zone_addresses": (),
                },
            ),
            "connections": (),
        }
    )

    assert result["deleted_node_addresses"] == 0
    assert result["errors"] == []


def test_rolling_capacity_uses_topology_bounds_times_depth_plus_active() -> None:
    satellites = (NODE,) + tuple(
        NodeId(65001, "satellite", f"sat-{index}")
        for index in range(2, 33)
    )
    ground_stations = tuple(
        NodeId(65001, "ground-station", f"gs-{index}")
        for index in range(1, 4)
    )
    topology_nodes = satellites + ground_stations
    zones = tuple(f"zone-{index}" for index in range(5))
    realization = _zone_realization(
        0,
        4,
        (zones[0],),
        topology_zones=zones,
        topology_nodes=topology_nodes,
    )
    boundaries = _resolve_nominal_boundaries((realization,))

    class _Executor:
        def execute(self, action, request):
            if action == "audit_node_addresses":
                return _collision_audit_result(request)
            if action == "initialize":
                return _foundation_result(request)
            return {"shared_active_generation_map": True}

    dataplane = _TcBpfRuntimeRealization(
        executor=_Executor(),
        pin_root="/sys/fs/bpf/skyseed-v2/run-topology-capacity",
        bindings={
            node_id: SimpleNamespace(pid=index + 1, netns_inode=index + 101)
            for index, node_id in enumerate(topology_nodes)
        },
    )
    capacity = dataplane.initialize(
        realization._topology_signature,
        zone_prefixes=tuple(prefix for _, prefix in realization.zone_prefixes),
        capacity_boundaries=boundaries,
        preparation_lookahead=timedelta(seconds=60),
        preparation_boundary_depth=8,
    )["capacity_audit"]
    per_node = {value["key"]: value for value in capacity["per_node"]}

    assert capacity["resident_generation_capacity"] == 9
    assert per_node[ground_stations[0].value][
        "per_generation_active_link_upper_bound"
    ] == 32
    assert per_node[ground_stations[0].value][
        "per_generation_route_upper_bound"
    ] == 6
    assert per_node[ground_stations[0].value][
        "configured_active_link_entries"
    ] == 288
    assert per_node[ground_stations[0].value]["configured_route_entries"] == 54
    assert per_node[NODE.value]["per_generation_active_link_upper_bound"] == 34
    assert per_node[NODE.value]["per_generation_route_upper_bound"] == 9
    assert per_node[NODE.value]["configured_active_link_entries"] == 306
    assert per_node[NODE.value]["configured_route_entries"] == 81


def test_bounded_exact_and_rolling_topology_capacity_are_distinct() -> None:
    zones = ("zone-a", "zone-b", "zone-c")
    first = _zone_realization(0, 4, ("zone-a",), topology_zones=zones)
    second = _zone_realization(
        4,
        8,
        ("zone-b", "zone-c"),
        topology_zones=zones,
    )
    boundaries = _resolve_nominal_boundaries((first, second))

    class _Executor:
        def __init__(self) -> None:
            self.requests = []

        def execute(self, action, request):
            self.requests.append((action, request))
            if action == "audit_node_addresses":
                return _collision_audit_result(request)
            if action == "initialize":
                return _foundation_result(request)
            return {"shared_active_generation_map": True}

    bindings = {NODE: SimpleNamespace(pid=101, netns_inode=201)}
    bounded_executor = _Executor()
    bounded = _TcBpfRuntimeRealization(
        executor=bounded_executor,
        pin_root="/sys/fs/bpf/skyseed-v2/run-bounded-capacity",
        bindings=bindings,
    )
    bounded_result = bounded.initialize(
        first._topology_signature,
        zone_prefixes=tuple(prefix for _, prefix in first.zone_prefixes),
        capacity_boundaries=boundaries,
        preparation_lookahead=None,
        preparation_boundary_depth=None,
    )
    bounded_node = bounded_result["capacity_audit"]["per_node"][0]
    assert bounded_result["capacity_audit"]["known_generation_count"] == 2
    assert bounded_result["capacity_audit"]["resident_generation_capacity"] == 2
    assert bounded_node["required_route_entries"] == 6
    assert bounded_node["configured_route_entries"] == 6
    initialize_request = next(
        request
        for action, request in bounded_executor.requests
        if action == "initialize"
    )
    assert initialize_request["nodes"][0]["routes_capacity"] == 6

    rolling_executor = _Executor()
    rolling = _TcBpfRuntimeRealization(
        executor=rolling_executor,
        pin_root="/sys/fs/bpf/skyseed-v2/run-rolling-capacity",
        bindings=bindings,
    )
    rolling_result = rolling.initialize(
        first._topology_signature,
        zone_prefixes=tuple(prefix for _, prefix in first.zone_prefixes),
        capacity_boundaries=(boundaries[0],),
        preparation_lookahead=timedelta(0),
        preparation_boundary_depth=1,
    )
    rolling_node = rolling_result["capacity_audit"]["per_node"][0]
    assert rolling_result["capacity_audit"]["resident_generation_capacity"] == 2
    assert rolling_result["capacity_audit"]["preparation_boundary_depth"] == 1
    assert rolling_node["capacity_basis"] == (
        "topology-upper-bound-times-resident-capacity"
    )
    assert rolling_node["per_generation_route_upper_bound"] == 4
    assert rolling_node["required_route_entries"] == 8
    assert rolling_node["configured_route_entries"] == 8


def test_rolling_resident_capacity_reserves_in_flight_and_fails_before_preload(
    monkeypatch,
) -> None:
    zones = ("zone-a", "zone-b", "zone-c")
    first = _zone_realization(0, 4, ("zone-a",), topology_zones=zones)
    second = _zone_realization(
        4,
        8,
        ("zone-b", "zone-c"),
        topology_zones=zones,
    )
    boundaries = _resolve_nominal_boundaries((first, second))

    class _Executor:
        def __init__(self) -> None:
            self.actions = []

        def execute(self, action, request):
            self.actions.append(action)
            if action == "audit_node_addresses":
                return _collision_audit_result(request)
            if action == "initialize":
                return _foundation_result(request)
            if action == "prepare_boundary":
                return {
                    "generation_keys": [
                        {
                            "key": node["key"],
                            "active_interface_indexes": (),
                            "route_keys": tuple(range(len(node["routes"]))),
                        }
                        for node in request["generation_nodes"]
                    ]
                }
            return {"shared_active_generation_map": True}

    executor = _Executor()
    dataplane = _TcBpfRuntimeRealization(
        executor=executor,
        pin_root="/sys/fs/bpf/skyseed-v2/run-fail-capacity",
        bindings={NODE: SimpleNamespace(pid=101, netns_inode=201)},
    )
    dataplane.initialize(
        first._topology_signature,
        zone_prefixes=tuple(prefix for _, prefix in first.zone_prefixes),
        capacity_boundaries=(boundaries[0],),
        preparation_lookahead=timedelta(0),
        preparation_boundary_depth=1,
    )
    materialization_calls = 0
    materialize = runtime_realization_module._materialize_boundary

    def count_materialization(previous, boundary):
        nonlocal materialization_calls
        materialization_calls += 1
        return materialize(previous, boundary)

    monkeypatch.setattr(
        runtime_realization_module,
        "_materialize_boundary",
        count_materialization,
    )
    pending, _ = dataplane.start_preparation(boundaries[0])
    assert dataplane.generation_capacity_status() == {
        "resident_generations": 0,
        "reserved_in_flight_preparations": 1,
        "resident_generation_capacity": 2,
        "can_start_preparation": False,
    }
    ready, _ = dataplane.finish_preparation(
        pending,
        foundation_token="foundation",
        predecessor_time=None,
    )
    pending, _ = dataplane.start_preparation(boundaries[1])
    ready_two, _ = dataplane.finish_preparation(
        pending,
        foundation_token="foundation",
        predecessor_time=boundaries[0].nominal_time,
    )
    with pytest.raises(_CapacityAuditError, match="resident generation capacity"):
        dataplane.start_preparation(boundaries[1])
    assert executor.actions.count("prepare_boundary") == 2
    assert set(dataplane._generation_keys) == {0, 1}
    assert materialization_calls == 2
    dataplane.activate(ready)
    dataplane.activate(ready_two)
    retirement, _resources = dataplane.retirement_handles(ready, ready_two)
    dataplane.reclaim_forwarding(retirement)
    assert set(dataplane._generation_keys) == {1}
    assert set(dataplane._realizations) == {1}


def test_capacity_pressure_reclaims_generation_before_forward_preparation(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(BpfRunner, "_maintenance_loop", lambda self: None)
    runner = BpfRunner(
        compose_project_name="runner-test",
        future_realization_supplier=_supplier(
            _realization(0, 4, "zone-a"),
            _realization(4, 8, "zone-b"),
            _realization(8, 12, "zone-c"),
        ),
        preparation_boundary_depth=1,
    )
    runner.prepare()
    runner._maintenance_thread.join(timeout=1)
    runner.activate_next()
    runner._maintain_until_satisfied()
    runner.activate_next()
    dataplane = _FakeRuntimeRealization.instances[0]
    dataplane.operations.clear()
    runner._maintain_until_satisfied()
    assert dataplane.operations[:2] == [
        ("reclaim-generation", 0),
        ("prepare", 2),
    ]
    assert [value.generation for value in dataplane.prepared] == [0, 1, 2]
    assert any(
        value["event"] == "maintenance_capacity_pressure"
        and value["action"] == "reclaim-forwarding"
        for value in runner.inspection_snapshot()["events"]
    )
    runner.cleanup()


def test_full_resident_capacity_with_depth_satisfied_waits_for_main(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(BpfRunner, "_maintenance_loop", lambda self: None)
    runner = BpfRunner(
        compose_project_name="runner-depth-wait",
        future_realization_supplier=_supplier(
            _realization(0, 4, "zone-a"),
            _realization(4, 8, "zone-b"),
            _realization(8, 12, "zone-c"),
            _realization(12, 16, "zone-d"),
        ),
        preparation_lookahead=timedelta(seconds=100),
        preparation_boundary_depth=2,
    )
    runner.prepare()
    runner._maintenance_thread.join(timeout=1)
    runner.activate_next()
    runner._maintain_until_satisfied()

    dataplane = _FakeRuntimeRealization.instances[0]
    assert len(runner._ready) == 2
    assert dataplane.generation_capacity_status() == {
        "resident_generations": 3,
        "reserved_in_flight_preparations": 0,
        "resident_generation_capacity": 3,
        "can_start_preparation": False,
    }
    assert runner._next_maintenance_action() == ("done", None)
    runner.cleanup()


def test_exact_next_missing_full_capacity_without_reclaim_fails_closed(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(BpfRunner, "_maintenance_loop", lambda self: None)
    runner = BpfRunner(
        compose_project_name="runner-resident-deadlock",
        future_realization_supplier=_supplier(
            _realization(0, 4, "zone-a"),
            _realization(4, 8, "zone-b"),
            _realization(8, 12, "zone-c"),
        ),
        preparation_boundary_depth=1,
    )
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
    runner.cleanup()


def test_strict_physical_reclaim_retains_inventory_and_fails_runner(
    tmp_path,
) -> None:
    connection = ConnectionId(ConnectionKind.INTER_SATELLITE, NODE, NODE_2)

    class _FailingExecutor:
        def execute(self, action, request):
            raise RuntimeError("kernel deletion failed")

    dataplane = _TcBpfRuntimeRealization(
        executor=_FailingExecutor(),
        pin_root="/sys/fs/bpf/skyseed-v2/run-reclaim-failure",
        bindings={},
    )
    dataplane._prepared_resources.add(connection)
    dataplane._all_prepared_resources.add(connection)
    with pytest.raises(RuntimeError, match="kernel deletion failed"):
        dataplane.reclaim_physical(_RetiredPhysicalRealization((connection,)))
    assert connection in dataplane._prepared_resources
    assert connection in dataplane._all_prepared_resources

    _FakeRuntimeRealization.reclaim_error = RuntimeError("kernel deletion failed")
    runner = BpfRunner(
        compose_project_name="runner-test",
        future_realization_supplier=_supplier(_realization(0, 4, "zone-a")),
        preparation_lookahead=None,
        preparation_boundary_depth=None,
    )
    runner.prepare()
    with runner._condition:
        runner._reclaim_physical.append((object(),))
        runner._maintenance_revision += 1
        runner._condition.notify_all()
    _wait_for(lambda: runner._state == "failed")
    assert isinstance(runner._maintenance_error, RuntimeError)
    assert "kernel deletion failed" in str(runner._maintenance_error)
    runner.cleanup()
