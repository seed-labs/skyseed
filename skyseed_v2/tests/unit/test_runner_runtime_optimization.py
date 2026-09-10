from __future__ import annotations

import json

from dataclasses import replace
from datetime import datetime
import inspect
import os
from pathlib import Path
import subprocess
import struct
from threading import Event, Lock
from time import sleep
from types import SimpleNamespace

import pytest

from skyseed_v2.backend.bpf._runner import privileged_runtime_command as command
from skyseed_v2.backend.bpf._runner import runtime_realization as runtime_module
from skyseed_v2.backend.bpf._runner.runtime_realization import (
    _IncompleteActivationError,
    _MaterializedDataplaneState,
    _TcBpfRuntimeRealization,
    _RuntimeCapacityDemandAccumulator,
    _empty_state,
)
from skyseed_v2.backend.bpf._runner_artifact import (
    _BoundaryOperation,
    _NominalBoundary,
)
from skyseed_v2.backend.bpf.compiled_realization import (
    _AttachmentDemand,
    _ForwardingBinding,
    _ForwardingIntent,
    _ForwardingKey,
    _MutationOperation,
    _PhysicalWorldProjection,
    _QdiscIntent,
    _ResourceIntent,
    _ResourceOperation,
    _ZoneEdgeIntent,
    _ZoneEdgePhase,
)
from skyseed_v2.shared import NodeId
from skyseed_v2.simulator import (
    OperationalConnectionId,
    OperationalConnectionKind,
    ZoneRouteDemandTarget,
)


def _capacity_demand(projection, boundaries):
    accumulator = _RuntimeCapacityDemandAccumulator(projection, ())
    for boundary in boundaries:
        accumulator.add(boundary)
    return accumulator.snapshot()

def _capacity_test_oracle(projection, boundaries):
    """Retain the retired startup algorithm here only as a differential oracle."""

    nodes = tuple(sorted(projection.node_ids, key=runtime_module._node_key))
    addresses, _ = runtime_module._allocate_node_addresses(nodes, ())
    state = _empty_state()
    generation_count = 0
    physical_high_water = 0
    interface_high_water = {node.value: 0 for node in nodes}
    active_totals = {node.value: 0 for node in nodes}
    route_totals = {node.value: 0 for node in nodes}
    active_maximums = {node.value: 0 for node in nodes}
    route_maximums = {node.value: 0 for node in nodes}
    for boundary in boundaries:
        previous = state
        state = runtime_module._materialize_boundary(previous, boundary)
        if (
            generation_count == 0
            or runtime_module._generation_state(previous)
            != runtime_module._generation_state(state)
        ):
            generation_count += 1
            interfaces = runtime_module._interfaces_for_state(state, nodes)
            routes = runtime_module._routes_for_state(state, nodes, addresses)
            for node in nodes:
                key = node.value
                active = len(interfaces[node])
                route = len(routes[node])
                active_totals[key] += active
                route_totals[key] += route
                active_maximums[key] = max(active_maximums[key], active)
                route_maximums[key] = max(route_maximums[key], route)
        transition = set(previous.resources) | set(state.resources)
        runtime_module._assert_unique_resource_bindings(transition)
        physical_high_water = max(physical_high_water, len(transition))
        per_node_transition = {node.value: 0 for node in nodes}
        for connection in transition:
            per_node_transition[connection.endpoint_a.value] += 1
            per_node_transition[connection.endpoint_b.value] += 1
        for key, count in per_node_transition.items():
            interface_high_water[key] = max(interface_high_water[key], count)
    terminal_interfaces = runtime_module._interfaces_for_state(state, nodes)
    terminal_routes = runtime_module._routes_for_state(state, nodes, addresses)
    return {
        "schema_version": 1,
        "boundary_count": len(boundaries),
        "generation_count": generation_count,
        "physical_connection_high_water": physical_high_water,
        "interface_binding_high_water": physical_high_water * 2,
        "interface_identity_derivation": "blake2s-40-bit-connection-key-v1",
        "collision_audit": {
            "result": "passed",
            "audited_transition_count": len(boundaries),
            "derivation": "blake2s-40-bit-connection-key-v1",
        },
        "per_node": [
            {
                "key": node.value,
                "cumulative_active_link_entries": active_totals[node.value],
                "cumulative_route_entries": route_totals[node.value],
                "maximum_generation_active_link_entries": active_maximums[
                    node.value
                ],
                "maximum_generation_route_entries": route_maximums[node.value],
                "physical_interface_high_water": interface_high_water[node.value],
                "terminal_active_link_entries": len(terminal_interfaces[node]),
                "terminal_route_entries": len(terminal_routes[node]),
            }
            for node in nodes
        ],
        "terminal": {
            "active_connection_count": len(state.resources),
            "generation_active": True,
            "per_node_count": len(nodes),
        },
    }

def _capacity_transition_sequence():
    a = NodeId(65001, "satellite", "sat-a")
    b = NodeId(65001, "satellite", "sat-b")
    c = NodeId(65001, "satellite", "sat-c")
    projection = _PhysicalWorldProjection((a, b, c), ("zone-a",))
    first = OperationalConnectionId(
        OperationalConnectionKind.INTER_SATELLITE, a, b
    )
    second = OperationalConnectionId(
        OperationalConnectionKind.INTER_SATELLITE, a, c
    )
    target = ZoneRouteDemandTarget("zone-a")
    route_key = _ForwardingKey(a, target)
    first_binding = _ForwardingBinding(route_key, b, first, "198.18.0.1/32")
    second_binding = _ForwardingBinding(route_key, c, second, "198.18.0.1/32")

    def resource(when, operation, connection):
        return _ResourceIntent(
            when,
            operation,
            connection,
            (
                _AttachmentDemand(
                    connection, connection.endpoint_a, connection.endpoint_b
                ),
                _AttachmentDemand(
                    connection, connection.endpoint_b, connection.endpoint_a
                ),
            ),
        )

    def qdiscs(when, connection, delay):
        return tuple(
            _QdiscIntent(
                when,
                connection,
                source,
                target_node,
                1_000_000,
                delay,
                0,
                "capacity-test",
            )
            for source, target_node in (
                (connection.endpoint_a, connection.endpoint_b),
                (connection.endpoint_b, connection.endpoint_a),
            )
        )

    def boundary(offset, intents):
        when = datetime.fromisoformat("2026-01-01T00:00:00+00:00")
        when = when.replace(second=offset)
        domains = []
        counts = {name: 0 for name in ("resource", "parameter", "forwarding", "zone-edge")}
        for intent in intents:
            domain = (
                "resource"
                if isinstance(intent, _ResourceIntent)
                else "parameter"
                if isinstance(intent, _QdiscIntent)
                else "forwarding"
                if isinstance(intent, _ForwardingIntent)
                else "zone-edge"
            )
            index = counts[domain]
            counts[domain] += 1
            domains.append(_BoundaryOperation(0, domain, index, intent))
        return _NominalBoundary(
            nominal_time=when,
            operations=tuple(domains),
            physical_world_projection=projection,
            source_realization_indexes=(0,),
            coverage=(),
            inspected_constraint_count=0,
            source_constraint_count=0,
            applied_constraint_count=0,
            outgoing_constraint_count=0,
            later_constraint_count=0,
            constraint_reasons=(),
            constraint_edges=(),
            operation_counts=tuple(counts.items()),
            order_sha256=f"{offset:064x}",
        )

    t0 = datetime.fromisoformat("2026-01-01T00:00:00+00:00")
    t1 = t0.replace(second=1)
    t2 = t0.replace(second=2)
    t3 = t0.replace(second=3)
    t4 = t0.replace(second=4)
    boundaries = (
        boundary(
            0,
            (
                resource(t0, _ResourceOperation.ESTABLISH, first),
                *qdiscs(t0, first, 10),
                _ZoneEdgeIntent(
                    t0, _MutationOperation.ADD, _ZoneEdgePhase.READINESS,
                    "zone-a", "198.18.0.1/32", None, b,
                ),
                _ForwardingIntent(
                    t0, _MutationOperation.ADD, route_key, None, first_binding
                ),
            ),
        ),
        boundary(1, (qdiscs(t1, first, 20)[0],)),
        boundary(2, ()),
        boundary(
            3,
            (
                resource(t3, _ResourceOperation.ESTABLISH, second),
                *qdiscs(t3, second, 30),
                _ZoneEdgeIntent(
                    t3, _MutationOperation.CHANGE, _ZoneEdgePhase.READINESS,
                    "zone-a", "198.18.0.1/32", b, c,
                ),
                _ForwardingIntent(
                    t3, _MutationOperation.CHANGE, route_key,
                    first_binding, second_binding,
                ),
                _ZoneEdgeIntent(
                    t3, _MutationOperation.CHANGE, _ZoneEdgePhase.RETIREMENT,
                    "zone-a", "198.18.0.1/32", b, c,
                ),
                resource(t3, _ResourceOperation.WITHDRAW, first),
            ),
        ),
        boundary(
            4,
            (
                _ForwardingIntent(
                    t4, _MutationOperation.REMOVE, route_key,
                    second_binding, None,
                ),
                _ZoneEdgeIntent(
                    t4, _MutationOperation.REMOVE, _ZoneEdgePhase.RETIREMENT,
                    "zone-a", "198.18.0.1/32", c, None,
                ),
                resource(t4, _ResourceOperation.WITHDRAW, second),
            ),
        ),
    )
    return projection, boundaries

def test_incremental_capacity_matches_retired_full_range_test_oracle() -> None:
    projection, boundaries = _capacity_transition_sequence()
    actual = _capacity_demand(projection, boundaries)
    assert actual == _capacity_test_oracle(projection, boundaries)
    assert actual["generation_count"] == 3
    assert actual["physical_connection_high_water"] == 2
    assert actual["terminal"]["active_connection_count"] == 0


def test_continued_admission_allows_peer_work_migration_but_keeps_fixed_limits(monkeypatch):
    from copy import deepcopy
    projection, boundaries = _capacity_transition_sequence()
    first = _capacity_demand(projection, boundaries[:3])
    original = deepcopy(first)
    kernel = _Kernel(monkeypatch)
    bindings = {node: SimpleNamespace(pid=i + 100, process_start_time_ticks=i + 200, netns_inode=i + 300)
                for i, node in enumerate(projection.node_ids)}
    runtime = _TcBpfRuntimeRealization(executor=kernel, pin_root="/pins", bindings=bindings,
                                       continuation_enabled=True, batch_capacity_factor=2)
    try:
        runtime.initialize(projection, zone_prefixes=(), runtime_capacity_demand=first)
        caps = dict(runtime._map_capacities)
        future = deepcopy(first)
        idle = min(future["per_node"], key=lambda node: node["cumulative_route_entries"])
        assert idle["cumulative_route_entries"] == 1
        idle["cumulative_route_entries"] = 3
        idle["maximum_generation_route_entries"] = 3
        idle["terminal_route_entries"] = 3
        runtime._admit_batch(future)
        assert runtime._capacity_demand["per_node"] == future["per_node"]
        idle["cumulative_route_entries"] = runtime._batch_admission_limits[idle["key"]]["cumulative_route_entries"] + 1
        with pytest.raises(runtime_module._CapacityAuditError, match="fixed per-node admission"):
            runtime._admit_batch(future)
        assert runtime._map_capacities == caps
        assert first == original
    finally:
        runtime.cleanup()


def test_continued_entry_limits_keep_entity_kinds_separate_and_single_exact():
    sat = NodeId(65001, "satellite", "a")
    ground = NodeId(65001, "ground-station", "b")
    fields = ("cumulative_active_link_entries", "cumulative_route_entries", "physical_interface_high_water")
    demand = {"per_node": [{"key": sat.value, **dict.fromkeys(fields, 10)},
                           {"key": ground.value, **dict.fromkeys(fields, 0)}]}
    nodes = {node.value: node for node in (sat, ground)}
    continued = runtime_module._batch_entry_limits(demand, nodes, factor=2, continued=True)
    assert set(continued[sat.value].values()) == {20}
    assert set(continued[ground.value].values()) == {2}
    single = runtime_module._batch_entry_limits(demand, nodes, factor=1, continued=False)
    assert set(single[sat.value].values()) == {10}
    assert set(single[ground.value].values()) == {1}

def test_incremental_capacity_collision_audit_covers_transition_union(
    monkeypatch,
) -> None:
    projection, boundaries = _capacity_transition_sequence()
    accumulator = _RuntimeCapacityDemandAccumulator(projection, ())
    monkeypatch.setattr(
        runtime_module,
        "_interface_names",
        lambda connection: ("sv2collisiona", "sv2collisionb"),
    )
    accumulator.add(boundaries[0])
    accumulator.add(boundaries[1])
    accumulator.add(boundaries[2])
    with pytest.raises(runtime_module._CapacityAuditError, match="collision"):
        accumulator.add(boundaries[3])

def _initialization_timing_fixture():
    node = NodeId(65001, "satellite", "sat-a")
    projection = _PhysicalWorldProjection((node,), ())
    boundary = _NominalBoundary(
        nominal_time=datetime.fromisoformat("2026-01-01T00:00:00+00:00"),
        operations=(),
        physical_world_projection=projection,
        source_realization_indexes=(0,),
        coverage=(),
        inspected_constraint_count=0,
        source_constraint_count=0,
        applied_constraint_count=0,
        outgoing_constraint_count=0,
        later_constraint_count=0,
        constraint_reasons=(),
        constraint_edges=(),
        operation_counts=(),
        order_sha256="0" * 64,
    )
    binding = SimpleNamespace(
        pid=101,
        process_start_time_ticks=202,
        netns_inode=303,
    )

    class Executor:
        def execute(self, action, request):
            assert action == "initialize"
            return {
                "collision_audit": {
                    "result": "passed",
                    "selected_node_address_range": request["node_address_range"],
                    "audited_node_namespaces": len(request["nodes"]),
                },
                "installed_node_addresses": [
                    value["key"] for value in request["nodes"]
                ],
                "bpf_batch_update_supported": True,
                "preparation_phases": {"collision_audit_seconds": 0.25},
            }

    runtime = _TcBpfRuntimeRealization(
        executor=Executor(), pin_root="/pins", bindings={node: binding}
    )
    return runtime, projection, boundary, _capacity_demand(projection, (boundary,))

def test_runtime_initialization_reports_disjoint_truthful_preparation_timings(
    monkeypatch,
) -> None:
    runtime, projection, boundary, demand = _initialization_timing_fixture()
    clock = iter((0, 2_000_000_000, 5_000_000_000, 6_000_000_000,
                  10_000_000_000, 11_000_000_000, 13_000_000_000))
    monkeypatch.setattr(runtime_module, "monotonic_ns", lambda: next(clock))

    result = runtime.initialize(
        projection, zone_prefixes=(), runtime_capacity_demand=demand
    )

    phases = result["preparation_phases"]
    assert phases["collision_audit_seconds"] == 0.25
    assert phases["sealed_capacity_demand_validation_seconds"] == 3.0
    assert phases["full_range_capacity_plan_materialization_seconds"] is None
    assert phases["privileged_foundation_initialization_seconds"] == 4.0
    assert phases["remaining_parent_bookkeeping_validation_seconds"] == 6.0
    assert runtime._initialized is True

def test_runtime_initialization_timing_defects_are_nullable_not_semantic(
    monkeypatch,
) -> None:
    runtime, projection, boundary, demand = _initialization_timing_fixture()
    clock = iter((7, 6, 5, 4, 3, 2, 1))
    monkeypatch.setattr(runtime_module, "monotonic_ns", lambda: next(clock))

    result = runtime.initialize(
        projection, zone_prefixes=(), runtime_capacity_demand=demand
    )

    phases = result["preparation_phases"]
    assert phases["sealed_capacity_demand_validation_seconds"] is None
    assert phases["privileged_foundation_initialization_seconds"] is None
    assert phases["remaining_parent_bookkeeping_validation_seconds"] is None
    assert runtime._initialized is True

def test_parallel_map_preserves_input_order_and_worker_bound() -> None:
    guard = Lock()
    both_running = Event()
    active = 0
    high_water = 0

    def operation(value):
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

    assert command._parallel_map(
        {"worker_count": 2}, [3, 1], operation
    ) == [30, 10]
    assert high_water == 2

def test_bpf_pin_scope_and_exact_batch_key_encoding(monkeypatch) -> None:
    program = command._tc_program(10, 11, 12, 13, 14)
    assert len(program) % 8 == 0
    assert len(program) >= 40 * 8
    assert command._pin_root(
        "/sys/fs/bpf/skyseed-v2/run-0123456789ab"
    ).name == "run-0123456789ab"
    with pytest.raises(ValueError, match="outside"):
        command._pin_root("/sys/fs/bpf/not-owned")

    observed = []

    def bpf(command_number, attributes):
        observed.append((
            command_number,
            bytes(command.ctypes.string_at(attributes.keys, attributes.count * 8)),
        ))
        return 0

    monkeypatch.setattr(command, "_bpf", bpf)
    assert command._update_map_batch(19, (((7, 11), 1), ((7, 13), 1))) == 2
    assert observed == [
        (command._BPF_MAP_UPDATE_BATCH, struct.pack("=IIII", 7, 11, 7, 13))
    ]

def test_runtime_cleanup_failure_retains_ownership_for_retry() -> None:
    first = NodeId(65001, "satellite", "sat-a")
    second = NodeId(65001, "satellite", "sat-b")
    connection = OperationalConnectionId(
        OperationalConnectionKind.INTER_SATELLITE, first, second
    )

    class Executor:
        def __init__(self):
            self.calls = 0

        def execute(self, action, request):
            assert action == "cleanup"
            self.calls += 1
            if self.calls == 1:
                return {"pin_root_removed": False, "errors": ["pin remains"]}
            return {"pin_root_removed": True, "errors": []}

    runtime = _TcBpfRuntimeRealization(
        executor=Executor(), pin_root="/pins", bindings={}
    )
    runtime._initialized = True
    runtime._cleanup_required = True
    runtime._prepared_resources.add(connection)
    runtime._all_prepared_resources.add(connection)
    runtime._interface_indexes[(first.value, "sv20000000001a")] = 17
    with pytest.raises(RuntimeError, match="ownership retained"):
        runtime.cleanup()
    assert runtime._initialized is True
    assert runtime._all_prepared_resources == {connection}
    assert runtime._interface_indexes
    runtime.cleanup()
    assert runtime._initialized is False
    assert not runtime._all_prepared_resources


class _Kernel:
    """Fake kernel state under the real prepared-plan and barrier executors."""

    def __init__(self, monkeypatch):
        self.actions = []
        self.operations = []
        self.maps = {"generation": {0: 0xFFFFFFFF}}
        self.links = {}
        self.publications = []
        self.fail = None
        self.nodes = {}
        self.groups = {}
        from concurrent.futures import ThreadPoolExecutor
        from weakref import finalize
        pool = ThreadPoolExecutor(max_workers=2)
        finalize(self, pool.shutdown)
        monkeypatch.setattr(command, "_RETIREMENT_POOL", pool)
        monkeypatch.setattr(command, "_RETIREMENT_WORKERS", 2)
        monkeypatch.setattr(command, "_OWNED_RETIREMENTS", {})
        monkeypatch.setattr(command, "_LIVE_NODES", self.nodes)
        monkeypatch.setattr(command, "_GENERATION_FD", "generation")
        monkeypatch.setattr(command, "_GENERATION_READERS_FD", -1)
        monkeypatch.setattr(command, "_GENERATIONS", {})
        monkeypatch.setattr(command, "_VERSIONS", {})
        monkeypatch.setattr(command, "_READY_GENERATIONS", set())
        monkeypatch.setattr(command, "_lookup_map", lambda fd, key: self.maps[fd].get(key))
        monkeypatch.setattr(command, "_lookup_u64_map", lambda fd, key: self.maps[fd].get(key))
        monkeypatch.setattr(command, "_update_u64_map", self.update)
        monkeypatch.setattr(command, "_update_map", self.update)
        monkeypatch.setattr(command, "_delete_map", self.delete)
        monkeypatch.setattr(command, "_update_map_entries", self.update_entries)
        def forbidden(*args, **kwargs):
            raise AssertionError("formal runtime attempted a syscall/process/identity preflight outside its fake executor")
        for name in ("_bpf", "_run", "_in_netns", "_assert_netns"):
            monkeypatch.setattr(command, name, forbidden)

    def update(self, fd, key, value):
        if fd == "generation":
            from copy import deepcopy
            self.publications.append((value, deepcopy(self.maps), dict(self.links)))
        self.maps[fd][key] = value
        self.operations.append(("map-update", fd, key, value))

    def delete(self, fd, key):
        self.operations.append(("map-delete", fd, key))
        return self.maps[fd].pop(key, None) is not None

    def update_entries(self, fd, entries, **kwargs):
        for key, value in entries:
            self.update(fd, key, value)
        return len(entries)

    def execute(self, action, request):
        if isinstance(request, str):
            request = json.loads(request)["request"]
        self.actions.append((action, request))
        if action == "initialize":
            if request.get("continuation_enabled"):
                command._GENERATION_READERS_FD = 1000000
                self.maps[1000000] = {}
            for number, value in enumerate(request["nodes"]):
                key = value["key"]
                maps = {name: (key, name) for name in ("active_links", "routes", "ingress_bindings", "egress_bindings", "tc_ingress", "generation_versions")}
                for fd in maps.values():
                    self.maps[fd] = {}
                self.nodes[key] = {**value, "namespace_fd": number,
                                   "maps": maps, "indexes": {}, "loopback": 1,
                                   "netlink": self.Channel(self, key)}
            return {"collision_audit": {"result": "passed", "selected_node_address_range": request["node_address_range"],
                                        "audited_node_namespaces": len(request["nodes"])},
                    "installed_node_addresses": [node["key"] for node in request["nodes"]],
                    "bpf_batch_update_supported": True,
                    "traffic_telemetry": {"enabled": True, "shared_ring_buffer": True,
                                          "event_schema": {}, "ring_capacity_bytes": 1 << 20}}
        if action == "cleanup":
            return {"pin_root_removed": True, "errors": []}
        return command._dispatch_action(action, request)

    class Channel:
        def __init__(self, kernel, key):
            self.kernel = kernel
            self.key = key
            self.next_index = 10

        def _op(self, name, *args, **kwargs):
            kernel = self.kernel
            kernel.operations.append((name, self.key, args, kwargs))
            if kernel.fail == name:
                raise OSError(5, name)

        def create_veth(self, name, peer_name, peer_fd, *, mac, group, peer_group):
            self._op("create", name, peer_name, peer_fd, mac=mac, group=group, peer_group=peer_group)
            peer = next(node for node in self.kernel.nodes.values() if node["namespace_fd"] == peer_fd)
            for channel, interface, cohort in ((self, name, group), (peer["netlink"], peer_name, peer_group)):
                self.kernel.groups[(channel.key, interface)] = cohort
                channel.next_index += 1
                self.kernel.links[(channel.key, interface)] = channel.next_index

        def link_index(self, name):
            self._op("index", name)
            return self.kernel.links[(self.key, name)]

        def delete_group(self, group):
            self._op("delete", group)
            targets = [key for key, value in self.kernel.groups.items()
                       if key[0] == self.key and value == group and key in self.kernel.links]
            if not targets:
                raise OSError(19, "group absent")
            for key in targets:
                self._remove_pair(key[1])

        def _remove_pair(self, name):
            for key in tuple(self.kernel.links):
                if key[1][:-1] == name[:-1]:
                    self.kernel.links.pop(key)
                    self.kernel.groups.pop(key)

        def delete_link(self, index):
            self._op("delete", index)
            name = next(name for (key, name), value in self.kernel.links.items() if key == self.key and value == index)
            self._remove_pair(name)

        def set_link(self, *args, **kwargs): self._op("link", *args, **kwargs)
        def netem(self, *args, **kwargs): self._op("qdisc", *args, **kwargs)
        def attach_bpf(self, *args, **kwargs): self._op("attach", *args, **kwargs)
        def address(self, *args, **kwargs): self._op("address", *args, **kwargs)
        def route(self, *args, **kwargs): self._op("route", *args, **kwargs)
        def neighbour(self, *args, **kwargs): self._op("neighbour", *args, **kwargs)


def _finite_runtime(monkeypatch, sequence=None, *, telemetry_enabled=False):
    projection, boundaries = _capacity_transition_sequence() if sequence is None else sequence
    kernel = _Kernel(monkeypatch)
    bindings = {node: SimpleNamespace(pid=i + 100, process_start_time_ticks=i + 200, netns_inode=i + 300)
                for i, node in enumerate(projection.node_ids)}
    runtime = _TcBpfRuntimeRealization(executor=kernel, pin_root="/pins", bindings=bindings, telemetry_enabled=telemetry_enabled)
    runtime.initialize(projection, zone_prefixes=("198.18.0.1/32",),
                       runtime_capacity_demand=_capacity_demand(projection, boundaries))
    connections = tuple(dict.fromkeys(op.intent.connection_id for boundary in boundaries for op in boundary.operations
                                     if isinstance(op.intent, _ResourceIntent)))
    ports = {connection: (2 * index + 1, 2 * index + 2) for index, connection in enumerate(connections)}
    plans = runtime.prepare_all(boundaries, logical_ports=ports)
    return runtime, plans, kernel


def test_all_finite_semantics_are_prepared_without_future_veth(monkeypatch):
    runtime, plans, kernel = _finite_runtime(monkeypatch)
    assert len(plans) == 5
    assert [plan.generation for plan in plans] == [0, 0, 0, 1, 2]
    assert kernel.links == {}
    assert kernel.maps["generation"] == {0: 0xFFFFFFFF}
    assert [action for action, _ in kernel.actions] == ["initialize", *(["prewrite_generation"] * 3)]
    assert len(runtime._resident_generations) == 3
    with pytest.raises(TypeError):
        plans[0].request["generation"] = 5
    assert isinstance(plans[0].request, str)
    assert json.loads(plans[0].request)["request"]["connections"][0]["logical_port_a"] > 0
    assert not hasattr(plans[0], "state")
    assert "preflight_nodes" not in plans[0].request
    assert "generation_nodes" not in plans[0].request
    with pytest.raises(RuntimeError, match="only once"):
        runtime.prepare_all((), logical_ports={})


def test_formal_main_never_derives_audits_preflights_or_reclaims(monkeypatch):
    from copy import deepcopy
    runtime, plans, kernel = _finite_runtime(monkeypatch)
    semantic = {fd: deepcopy(values) for fd, values in kernel.maps.items()
                if isinstance(fd, tuple) and fd[1] in ("active_links", "routes")}
    def forbidden(*args, **kwargs): raise AssertionError("full-state work entered formal runtime")
    for name in ("_materialize_boundary", "_routes_for_state", "_interfaces_for_state"):
        monkeypatch.setattr(runtime_module, name, forbidden)
    for name in ("_derive_boundary_plan", "_audit_boundary_capacity", "_validate_collision_audit"):
        monkeypatch.setattr(runtime, name, forbidden)
    for plan in plans:
        runtime.activate(plan)
    assert kernel.links == {}
    assert semantic == {fd: kernel.maps[fd] for fd in semantic}
    assert len(runtime._resident_generations) == 3
    assert [action for action, _ in kernel.actions[-5:]] == ["activate_boundary"] * 5
    assert not hasattr(runtime, "reclaim_forwarding")
    assert all(not values for fd, values in kernel.maps.items()
               if isinstance(fd, tuple) and fd[1] in ("ingress_bindings", "egress_bindings"))


def test_withdrawn_bindings_and_veth_survive_until_publication(monkeypatch):
    runtime, plans, kernel = _finite_runtime(monkeypatch)
    for plan in plans[:4]: runtime.activate(plan)
    generation, maps_at_commit, links_at_commit = kernel.publications[-1]
    assert generation == 1
    assert len(links_at_commit) == 4  # add-before-withdraw physical high water
    assert sum(len(values) for fd, values in maps_at_commit.items()
               if isinstance(fd, tuple) and fd[1] == "egress_bindings") == 4
    assert len(kernel.links) == 2
    assert sum(len(values) for fd, values in kernel.maps.items()
               if isinstance(fd, tuple) and fd[1] == "egress_bindings") == 2
    commit = next(i for i, event in enumerate(kernel.operations) if event[:4] == ("map-update", "generation", 0, 1))
    assert all(i > commit for i, event in enumerate(kernel.operations) if event[0] in ("map-delete", "delete"))


@pytest.mark.parametrize("failure", ["create", "attach", "route"])
def test_prepublication_failure_uses_same_channel_for_candidate_rollback(monkeypatch, failure):
    runtime, plans, kernel = _finite_runtime(monkeypatch)
    kernel.fail = failure
    with pytest.raises(_IncompleteActivationError) as caught:
        runtime.activate(plans[0])
    assert kernel.maps["generation"][0] == 0xFFFFFFFF
    assert runtime._active is None
    assert kernel.links == {}
    assert caught.value.evidence["generation_publication_occurred"] is False


def test_postpublication_failure_preserves_selected_resources_and_formal_frontier(monkeypatch):
    runtime, plans, kernel = _finite_runtime(monkeypatch)
    for plan in plans[:3]: runtime.activate(plan)
    kernel.fail = "delete"
    with pytest.raises(_IncompleteActivationError) as caught:
        runtime.activate(plans[3])
    assert runtime._active is plans[2]
    assert kernel.maps["generation"][0] == 1
    assert len(kernel.links) == 4
    assert caught.value.evidence["candidate_rollback_attempted"] is False
    assert caught.value.evidence["target_transition_complete"] is False
    assert len(runtime._all_prepared_resources) == 2


def test_parameter_boundary_uses_only_its_affected_namespace(monkeypatch):
    runtime, plans, kernel = _finite_runtime(monkeypatch)
    runtime.activate(plans[0])
    kernel.operations.clear()
    class LookupOnly(dict):
        def __iter__(self): raise AssertionError("all-node scan")
        def values(self): raise AssertionError("all-node scan")
        def items(self): raise AssertionError("all-node scan")
    monkeypatch.setattr(command, "_LIVE_NODES", LookupOnly(kernel.nodes))
    runtime.activate(plans[1])
    assert [operation[0] for operation in kernel.operations] == ["qdisc"]
    assert len(json.loads(plans[1].request)["request"]["operation_nodes"]) == 1


def test_withdrawn_endpoint_reuses_logical_port_with_fresh_live_ifindex(monkeypatch):
    projection, boundaries = _capacity_transition_sequence()
    when = boundaries[-1].nominal_time.replace(second=5)
    first = boundaries[0]
    recreated = replace(first, nominal_time=when, operations=tuple(
        replace(operation, intent=replace(operation.intent, nominal_time=when))
        for operation in first.operations))
    runtime, plans, kernel = _finite_runtime(monkeypatch, (projection, (*boundaries, recreated)))
    first_result = runtime.activate(plans[0])
    for plan in plans[1:]:
        last_result = runtime.activate(plan)
    before = first_result["interface_identities"]
    after = last_result["interface_identities"]
    assert [value["logical_port_id"] for value in before] == [value["logical_port_id"] for value in after]
    assert [value["ifindex"] for value in before] != [value["ifindex"] for value in after]
    for old in before:
        assert old["ifindex"] not in kernel.maps[(old["key"], "ingress_bindings")]
    assert len(runtime._resident_generations) == 4


def test_incomplete_semantic_prewrite_count_fails_in_preparation(monkeypatch):
    from copy import deepcopy
    projection, boundaries = _capacity_transition_sequence()
    kernel = _Kernel(monkeypatch)
    bindings = {node: SimpleNamespace(pid=100 + i, process_start_time_ticks=200 + i, netns_inode=300 + i)
                for i, node in enumerate(projection.node_ids)}
    runtime = _TcBpfRuntimeRealization(executor=kernel, pin_root="/pins", bindings=bindings)
    runtime.initialize(projection, zone_prefixes=(), runtime_capacity_demand=_capacity_demand(projection, boundaries))
    monkeypatch.setattr(command, "_update_map_entries", lambda *args, **kwargs: 0)
    connections = tuple(dict.fromkeys(op.intent.connection_id for boundary in boundaries for op in boundary.operations
                                     if isinstance(op.intent, _ResourceIntent)))
    with pytest.raises(RuntimeError, match="incomplete entry count"):
        runtime.prepare_all(boundaries, logical_ports={connection: (2*i+1, 2*i+2) for i, connection in enumerate(connections)})
    assert kernel.links == {}
    assert kernel.maps["generation"][0] == 0xFFFFFFFF


def test_failed_preparation_retains_partial_initialization_for_final_cleanup():
    runtime, projection, _, demand = _initialization_timing_fixture()
    key = projection.node_ids[0].value
    calls = []
    class Executor:
        def execute(self, action, request):
            calls.append((action, request))
            if action == "initialize":
                error = RuntimeError("startup failed and cleanup was incomplete")
                error.remote_evidence = {"initialization_outcome": "partial",
                                         "installed_node_addresses": [key]}
                raise error
            return {"pin_root_removed": True, "errors": []}
    runtime._executor = Executor()
    with pytest.raises(runtime_module._CapacityAuditError, match="startup failed and cleanup was incomplete"):
        runtime.initialize(projection, zone_prefixes=(), runtime_capacity_demand=demand)
    assert runtime._cleanup_required
    runtime.cleanup()
    assert calls[-1][0] == "cleanup"
    assert calls[-1][1]["nodes"][0]["node_address_owned"] is True
    assert not runtime._initialized


@pytest.mark.parametrize("boundary_count", [1, 6])
def test_actual_runner_records_preserve_boundaries_generations_and_traffic_identity(monkeypatch, tmp_path, boundary_count):
    import io
    import json
    from time import monotonic_ns
    from skyseed_v2.backend.bpf import runner as runner_module
    from skyseed_v2.backend.bpf._runner.traffic_telemetry import _TrafficTelemetryRecorder
    from tools.traffic.reader import _read_telemetry, _validate_interface_generation_boundaries
    from tools.traffic.projection import _project_probe_qualification
    from tools.runner.projection import project_runner_inspection

    projection, boundaries = _capacity_transition_sequence()
    when = boundaries[-1].nominal_time.replace(second=5)
    recreated = replace(boundaries[0], nominal_time=when, operations=tuple(
        replace(operation, intent=replace(operation.intent, nominal_time=when))
        for operation in boundaries[0].operations))
    boundaries = (*boundaries, recreated)[:boundary_count]
    runtime, plans, kernel = _finite_runtime(monkeypatch, (projection, boundaries), telemetry_enabled=True)
    assert [plan.generation for plan in plans] == [0, 0, 0, 1, 2, 3][:boundary_count]
    assert sum(bool(plan.telemetry_identities) for plan in plans) == (1 if boundary_count == 1 else 3)
    foundation = runtime.telemetry_foundation(foundation_token="test")
    request_stream = io.StringIO()
    def reply():
        received = monotonic_ns()
        request = json.loads(request_stream.getvalue().splitlines()[-1])
        decoded = monotonic_ns()
        return json.dumps(command._respond(request["action"], request["request"], request_timing={
            "line_received_monotonic_ns": received, "request_decoded_monotonic_ns": decoded,
            "request_decode_seconds": (decoded - received) / 1e9,
        })) + "\n"
    transport = runner_module._PrivilegedHelper(docker_executable="docker", name="test", foundation_token="test", image_id="test")
    transport._process = SimpleNamespace(stdin=request_stream, stdout=SimpleNamespace(readline=reply), poll=lambda: None)
    runtime._executor = transport
    process = SimpleNamespace(stdin=io.StringIO(), stdout=io.StringIO('{"record_type":"collector_end"}\n'),
                              stderr=io.StringIO(), poll=lambda: 0, wait=lambda **kwargs: 0)
    recorder = _TrafficTelemetryRecorder(tmp_path)
    recorder.start(helper=SimpleNamespace(start_telemetry_collector=lambda request: process), foundation=foundation)
    runner = runner_module.BpfRunner(compose_project_name="test", runtime_artifact=tmp_path / "manifest.json")
    runner._runtime_realization = runtime
    runner._helper = transport
    runner._prepared_plans = plans
    runner._artifact_boundary_count = len(plans)
    runner._nominal_origin = plans[0].nominal_time
    runner._traffic_telemetry = recorder
    runner._state = "prepared"
    monkeypatch.setattr(runner_module, "sleep", lambda seconds: None)
    update_map = command._update_map
    def observed_publication(fd, key, value):
        if fd == "generation":
            for point, generation in (("before", kernel.maps[fd][0]), ("after", value)):
                if point == "after":
                    update_map(fd, key, value)
                for node in kernel.nodes.values():
                    for index in node["indexes"].values():
                        recorder._write({"record_type": "packet", "kernel_monotonic_ns": monotonic_ns(),
                            "sample_point": point, "node_slot": node["node_slot"], "generation": generation,
                            "ingress_ifindex": index, "selected_egress_ifindex": None,
                            "source_ipv4": "100.64.0.1", "destination_ipv4": "100.64.0.2",
                            "protocol": 1, "icmp": {"valid": False}})
        else:
            update_map(fd, key, value)
    monkeypatch.setattr(command, "_update_map", observed_publication)
    activate = runner._activate_prepared
    def observe(**kwargs):
        activate(**kwargs)
        if not runtime._interface_indexes:
            return
        (node, name), index = next(iter(runtime._interface_indexes.items()))
        slot = next(value["node_slot"] for value in foundation["nodes"] if value["node_id"] == node)
        recorder._write({"record_type": "packet", "kernel_monotonic_ns": monotonic_ns(),
                         "sample_point": "completed",
                         "node_slot": slot, "generation": runtime._active.generation,
                         "ingress_ifindex": index, "selected_egress_ifindex": None,
                         "source_ipv4": "100.64.0.1", "destination_ipv4": "100.64.0.2",
                         "protocol": 1, "icmp": {"valid": False}})
    monkeypatch.setattr(runner, "_activate_prepared", observe)
    observe(ideal_activation=None, paced_not_before=None, preparation=True)
    runner.run()
    assert recorder.stop_and_drain()["complete"]
    telemetry = _read_telemetry(tmp_path / "traffic-telemetry.jsonl")
    formal = tuple({"timestamp": plan.nominal_time.isoformat()} for plan in plans)
    _validate_interface_generation_boundaries(telemetry["interfaces"], formal, telemetry["boundaries"])
    assert len(telemetry["boundaries"]) == boundary_count
    assert all(packet["ingress_identity"] for packet in telemetry["packets"])
    steady = [packet for packet in telemetry["packets"] if packet["sample_point"] == "completed"]
    assert steady[1 if boundary_count > 1 else 0]["formal_boundary_timestamp"] == formal[1 if boundary_count > 1 else 0]["timestamp"]
    transient = [packet for packet in telemetry["packets"] if packet["ingress_identity"]["validity"] == "in-flight-boundary"]
    assert transient and all(packet["transition_boundary_timestamp"] for packet in transient)
    if boundary_count > 1:
        assert {packet["sample_point"] for packet in transient} == {"before", "after"}
    if boundary_count > 1:
        endpoint = telemetry["interfaces"]["endpoints"][0]
        first = next(value for value in endpoint["incarnations"] if value[0] == 0)
        last = next(value for value in endpoint["incarnations"] if value[1] == 3)
        assert first[2] != last[2]
        for boundary in telemetry["boundaries"][1:3]:
            assert not boundary["generation_publication_occurred"]
            assert "generation_switch_start_monotonic_ns" not in boundary
    view = project_runner_inspection(runner.inspection_snapshot(), {"status": "completed"})
    assert len(view["timing"]["activations"]) == boundary_count - 1
    assert view["overview"]["activation_count"] == boundary_count - 1
    assert view["overview"]["completed_boundary_count"] == boundary_count
    assert len(view["timing"]["startup_activations"]) == 1
    assert len(view["scale_safety"]["main_subphases"]) == boundary_count - 1
    detail = view["detailed_timing"]
    assert detail["formal_boundary_count"] == detail["instrumented_boundary_count"] == boundary_count - 1
    assert len(detail["startup"]) == 1
    if boundary_count > 1:
        assert detail["envelope"]["command_seconds"]["total"] >= detail["envelope"]["helper_dispatch_seconds"]["total"] > 0
        for row in detail["boundaries"][:2]:
            assert "active_generation_update" not in row["operations"]
        assert detail["operations"]["active_generation_update"]["seconds"]["count"] == 3
    else:
        assert detail["main_seconds"]["p50"] is None
        assert detail["operations"]["pre_veth_creation"]["seconds"]["p50"] is None
    if boundary_count == 1:
        assert view["timing"]["max_activation_duration_seconds"] is None
    planning = {key: 0 for key in ("duration_seconds", "persistent_episode_count", "activation_window_episode_count", "estimated_echo_requests", "estimated_telemetry_events")}
    planning.update({key: {} for key in ("direction_counts", "path_length_class_counts", "selection_reason_counts")})
    qualified = _project_probe_qualification({
        "plan": {"planning_horizon": {"revision_id": "test"}, "planning": planning, "probe_identities": [], "episodes": []},
        "execution": [{"record_type": "execution_header"}, {"record_type": "executor_end", "complete": True, "cleanup_complete": True, "active_generators": 0}],
        "runner_events": tuple(runner._events),
    }, ping_transactions=(), telemetry_complete=True, telemetry_losses=0)
    assert qualified["summary"]["status"] == "PASS"
    assert qualified["summary"]["completed_boundary_count"] == boundary_count
    assert qualified["runner_observer_effect"]["main_seconds"]["count"] == boundary_count - 1


@pytest.mark.parametrize("error_type", [EOFError, BrokenPipeError, RuntimeError])
def test_initialization_response_loss_retains_cleanup_until_confirmed(error_type):
    runtime, projection, _, demand = _initialization_timing_fixture()
    calls = []
    cleanup_fails = True
    class Executor:
        def execute(self, action, request):
            calls.append(action)
            if action == "initialize":
                raise error_type("response lost after potentially completed mutations")
            if cleanup_fails:
                raise EOFError("cleanup response also lost")
            return {"pin_root_removed": True, "cleanup_confirmed": True, "errors": []}
    runtime._executor = Executor()
    with pytest.raises(runtime_module._CapacityAuditError, match="response lost after potentially completed mutations"):
        runtime.initialize(projection, zone_prefixes=(), runtime_capacity_demand=demand)
    assert runtime._initialization_outcome == "uncertain"
    assert runtime._cleanup_required
    with pytest.raises(EOFError):
        runtime.cleanup()
    assert runtime._cleanup_required
    cleanup_fails = False
    assert runtime.cleanup()["pin_root_removed"] is True
    assert calls == ["initialize", "cleanup", "cleanup"]
    assert not runtime._cleanup_required


def test_rejected_startup_cleanup_preserves_foreign_pin_root(monkeypatch, tmp_path):
    root = tmp_path / "foreign"
    root.mkdir()
    (root / "foreign-map").write_text("owned elsewhere")
    monkeypatch.setattr(command, "_pin_root", lambda value: root)
    monkeypatch.setattr(command, "_INITIALIZATION_REQUESTED", False)
    monkeypatch.setattr(command, "_FOUNDATION_OUTCOME", "no-mutation")
    monkeypatch.setattr(command, "_OWNED_PIN_ROOT", None)
    monkeypatch.setattr(command, "_close_foundation", lambda *args: None)
    response = command._respond("initialize", {"pin_root": str(root), "nodes": []})
    assert response["evidence"]["initialization_outcome"] == "no-mutation"
    result = command._cleanup({"pin_root": str(root)})
    assert result["cleanup_confirmed"] is True
    assert result["pin_root_removed"] is False
    assert (root / "foreign-map").read_text() == "owned elsewhere"


@pytest.mark.parametrize("lose_cleanup_response", [False, True])
def test_real_initialization_ledger_survives_response_loss(monkeypatch, tmp_path, lose_cleanup_response):
    runtime, projection, _, demand = _initialization_timing_fixture()
    root = tmp_path / "owned-pins"
    addresses = set()
    next_fd = iter(range(10000, 20000))
    class Channel:
        def initial_links(self, group_end): return 1
        def set_link(self, *args, **kwargs): pass
        def address(self, index, address, *, delete=False):
            if delete:
                addresses.discard(address)
            else:
                addresses.add(address)
        def close(self): pass
    nodes = {}
    def retain(values):
        nodes.update({node["key"]: {**node, "netlink": Channel(), "maps": {}, "namespace_fd": next(next_fd)} for node in values})
    monkeypatch.setattr(command, "_LIVE_NODES", nodes)
    monkeypatch.setattr(command, "_INITIALIZATION_REQUESTED", False)
    monkeypatch.setattr(command, "_FOUNDATION_OUTCOME", "no-mutation")
    monkeypatch.setattr(command, "_OWNED_PIN_ROOT", None)
    monkeypatch.setattr(command, "_OWNED_NODE_ADDRESSES", set())
    monkeypatch.setattr(command, "_GENERATION_FD", -1)
    monkeypatch.setattr(command, "_pin_root", lambda value: root)
    monkeypatch.setattr(command, "_retain_namespaces", retain)
    monkeypatch.setattr(command, "_probe_batch_update_support", lambda: True)
    monkeypatch.setattr(command, "_audit_node_addresses", lambda request: {})
    monkeypatch.setattr(command, "_create_map", lambda *args, **kwargs: next(next_fd))
    monkeypatch.setattr(command, "_pin_object", lambda fd, path: path.touch())
    monkeypatch.setattr(command, "_load_program", lambda *args, **kwargs: next(next_fd))
    monkeypatch.setattr(command, "_open_pinned", lambda path: next(next_fd))
    monkeypatch.setattr(command, "_update_map", lambda *args: None)
    original_close = os.close
    monkeypatch.setattr(command.os, "close", lambda fd: None if fd >= 10000 else original_close(fd))
    class Executor:
        def execute(self, action, request):
            result = command._dispatch_action(action, request)
            if action == "initialize" or lose_cleanup_response:
                raise EOFError("completed remote action lost its response")
            return result
    runtime._executor = Executor()
    with pytest.raises(runtime_module._CapacityAuditError):
        runtime.initialize(projection, zone_prefixes=(), runtime_capacity_demand=demand)
    assert root.exists() and addresses
    assert runtime._cleanup_required and command._OWNED_PIN_ROOT == root
    if lose_cleanup_response:
        with pytest.raises(EOFError):
            runtime.cleanup()
        assert runtime._cleanup_required
        lose_cleanup_response = False
        assert runtime.cleanup()["initialization_outcome"] == "cleaned"
        assert not runtime._cleanup_required
    else:
        assert runtime.cleanup()["cleanup_confirmed"]
        assert not runtime._cleanup_required
    assert not root.exists() and not addresses


@pytest.mark.parametrize("soft", [1024, 16384])
def test_foundation_reserves_retained_descriptors_without_changing_hard_limit(monkeypatch, soft):
    changes = []
    monkeypatch.setattr(command.resource, "getrlimit", lambda kind: (soft, 524288))
    monkeypatch.setattr(command.resource, "setrlimit", lambda kind, limits: changes.append(limits))
    command._reserve_foundation_descriptors(1075, 8)
    if soft == 1024:
        assert len(changes) == 1
        assert changes[0][0] >= 7 * 1075 + 5 * 8
        assert changes[0][1] == 524288
    else:
        assert changes == []


def test_insufficient_descriptor_limit_fails_before_namespace_or_kernel_mutation(monkeypatch, tmp_path):
    root = tmp_path / "uncreated"
    monkeypatch.setattr(command, "_pin_root", lambda value: root)
    monkeypatch.setattr(command, "_INITIALIZATION_REQUESTED", False)
    monkeypatch.setattr(command, "_close_foundation", lambda: None)
    monkeypatch.setattr(command.resource, "getrlimit", lambda kind: (1024, 1024))
    monkeypatch.setattr(command, "_retain_namespaces", lambda nodes: pytest.fail("must reject before retaining namespaces"))
    result = command._respond("initialize", {"pin_root": str(root), "nodes": [{}] * 1075})
    assert result["status"] == "error"
    assert "hard limit is 1024" in result["error"]
    assert result["evidence"]["initialization_outcome"] == "no-mutation"
    assert not root.exists()


@pytest.mark.parametrize("failure", [None, "route", "delete"])
def test_timing_preserves_kernel_operation_order_results_and_failure_ownership(failure):
    from copy import deepcopy
    def execute(observed):
        with pytest.MonkeyPatch.context() as patch:
            runtime, plans, kernel = _finite_runtime(patch)
            if not observed:
                patch.setattr(command, "_timed_call", lambda timings, category, operation, *args, **kwargs: operation(*args, **kwargs))
            for plan in plans[:3]:
                runtime.activate(plan)
            kernel.operations.clear()
            kernel.fail = failure
            try:
                result = runtime.activate(plans[3])
                error_name = None
            except _IncompleteActivationError as error:
                result = error.evidence
                error_name = type(error).__name__
            state = (tuple(kernel.operations), deepcopy(kernel.maps), deepcopy(kernel.links),
                     set(runtime._all_prepared_resources), runtime._active.generation, error_name)
            return state, result
    plain, _ = execute(False)
    observed, evidence = execute(True)
    assert plain == observed
    timings = evidence["operation_timings"]
    assert timings["pre_veth_creation"]["operation_count"] == 1
    assert all(value["duration_seconds"] >= 0 for value in timings.values())
    if failure == "delete":
        assert timings["post_veth_deletion"]["operation_count"] == 0
        assert evidence["retirement"]["deletion_requests"] == 1
        assert evidence["generation_publication_occurred"]
    elif failure == "route":
        assert timings["pre_route_replacement"]["operation_count"] == 1
        assert "active_generation_update" not in timings


def test_actual_bpf_call_counters_include_attempts_at_the_syscall_boundary(monkeypatch):
    from threading import local
    monkeypatch.setattr(command, "_BPF_CALLS", local())
    calls = []
    monkeypatch.setattr(command, "_LIBC", SimpleNamespace(syscall=lambda number, operation, *args: calls.append(operation) or 0))
    command._update_map(123, 0, 1)
    assert command._delete_map(123, 0)
    assert calls == [command._BPF_MAP_UPDATE_ELEM, command._BPF_MAP_DELETE_ELEM]
    counts = command._kernel_call_counts()
    assert counts["bpf_update_calls"] == counts["bpf_delete_calls"] == 1
    assert counts["bpf_update_batch_calls"] == 0


def test_cohorts_assign_one_endpoint_per_lifetime_and_preserve_future_groups(monkeypatch):
    runtime, plans, kernel = _finite_runtime(monkeypatch)
    wires = [json.loads(plan.request)["request"] for plan in plans]
    first = wires[0]["connections"][0]
    second = wires[3]["connections"][0]
    assert first["retirement_group"] != second["retirement_group"]
    for ordinal, connection in ((3, first), (4, second)):
        batch = wires[ordinal]["retirement_batches"][0]
        assert batch["key"] == connection["retirement_node"]
        assert batch["group"] == connection["retirement_group"]
        assert [item["connection"] for item in batch["connections"]] == [connection["connection"]]
    for plan in plans[:3]:
        runtime.activate(plan)
    result = runtime.activate(plans[3])
    assert result["retirement"]["retired_pairs"] == 1
    assert result["retirement"]["deletion_requests"] == 1
    assert len(kernel.links) == 2
    assert result["standalone_mac_requests"] == result["standalone_down_requests"] == 0
    assert all(event[3] == {"up": True} for event in kernel.operations if event[0] == "link")


def test_group_cleanup_retains_uncertain_ack_ownership_and_confirms_absence(monkeypatch):
    runtime, plans, kernel = _finite_runtime(monkeypatch)
    for plan in plans[:3]:
        runtime.activate(plan)
    wire = json.loads(plans[3].request)["request"]
    batch = wire["retirement_batches"][0]
    channel = kernel.nodes[batch["key"]]["netlink"]
    original = channel.delete_group
    def lose_ack(group):
        original(group)
        raise OSError(5, "lost acknowledgement after kernel deletion")
    monkeypatch.setattr(channel, "delete_group", lose_ack)
    with pytest.raises(_IncompleteActivationError) as caught:
        runtime.activate(plans[3])
    assert caught.value.evidence["target_transition_complete"] is False
    assert (batch["key"], batch["group"]) in command._OWNED_RETIREMENTS
    assert len(kernel.links) == 2  # new lifetime survived old-group deletion
    monkeypatch.setattr(channel, "delete_group", original)
    result = command._retire_batches([
        {"key": key, "group": group, "connections": list(owned.values())}
        for (key, group), owned in command._OWNED_RETIREMENTS.items()
    ], cleanup=True)
    assert not result["errors"]
    assert not command._OWNED_RETIREMENTS and not kernel.links


def test_retirement_workers_overlap_namespaces_serialize_same_socket_and_join(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, Lock
    from time import sleep
    pool = ThreadPoolExecutor(max_workers=2)
    monkeypatch.setattr(command, "_RETIREMENT_POOL", pool)
    monkeypatch.setattr(command, "_RETIREMENT_WORKERS", 2)
    entered = {key: Event() for key in ("a", "b")}
    release = Event()
    lock = Lock()
    active = set()
    class Channel:
        def __init__(self, key): self.key = key
        def delete_group(self, group):
            with lock:
                assert self.key not in active
                active.add(self.key)
            entered[self.key].set()
            assert release.wait(5)
            sleep(0.005)
            with lock: active.remove(self.key)
    nodes = {key: {"netlink": Channel(key), "indexes": {}} for key in entered}
    monkeypatch.setattr(command, "_LIVE_NODES", nodes)
    owned = {}
    batches = []
    for key, group in (("a", 0x53530001), ("a", 0x53530002), ("b", 0x53530001)):
        value = {"connection": f"{key}:{group}", "retirement_node": key, "retirement_group": group,
                 "node_a": "a", "node_b": "b", "interface_a": "left", "interface_b": "right"}
        owned[(key, group)] = {value["connection"]: value}
        batches.append({"key": key, "group": group, "connections": [value]})
    monkeypatch.setattr(command, "_OWNED_RETIREMENTS", owned)
    try:
        with ThreadPoolExecutor(max_workers=1) as caller:
            future = caller.submit(command._retire_batches, batches)
            try:
                assert all(event.wait(5) for event in entered.values())
                assert not future.done()
            finally:
                release.set()
            result = future.result()
        assert result["retired_pairs"] == result["grouped_batches"] == result["deletion_requests"] == 3
        assert result["task_duration_seconds_sum"] > result["wall_seconds"]
        assert not active and not owned
    finally:
        pool.shutdown()


def test_prepared_route_replacement_preserves_new_route_during_old_neighbour_withdrawal(monkeypatch):
    runtime, plans, kernel = _finite_runtime(monkeypatch)
    request = json.loads(plans[3].request)["request"]
    destination = "198.18.0.1"
    operation = next(node for node in request["operation_nodes"] if node["route_replace"])
    replacement = next(value for value in operation["route_replace"] if value[0] == destination)
    withdrawal = next(value for value in operation["route_withdraw"] if value[0] == destination)
    assert replacement[1] != withdrawal[1]
    assert withdrawal[2] is False
    for plan in plans[:3]: runtime.activate(plan)
    kernel.operations.clear()
    runtime.activate(plans[3])
    routes = [event for event in kernel.operations if event[0] == "route" and event[2][1] == destination]
    neighbours = [event for event in kernel.operations if event[0] == "neighbour" and event[2][1] == destination]
    assert len(routes) == 1 and routes[0][3]["delete"] is False
    assert [event[3]["delete"] for event in neighbours] == [False, True]
