"""Runner-local privileged runtime command mechanism."""

from __future__ import annotations

import sys
from pathlib import Path
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

import ctypes
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import errno
from ipaddress import IPv4Address, IPv4Network, ip_interface, ip_network
from skyseed_v2.backend.linux.materials import _IPv4Ranges
import json
import os
from pathlib import Path
import re
import resource
import socket
import struct
import sys
import traceback
from threading import Barrier, Lock
from time import monotonic, monotonic_ns
from typing import Any
from skyseed_v2.backend.bpf.dataplane.object_contract import _load_object
from skyseed_v2.backend.bpf.dataplane.materials import _validate_foundation, _pin_root, _RETIREMENT_GROUP_BASE
from skyseed_v2.backend.linux.netlink import _RouteSocket
from skyseed_v2.backend.linux import network as _network
from skyseed_v2.backend.linux import netlink as _netlink
from skyseed_v2.backend.bpf.dataplane.telemetry_abi import _telemetry_event_schema
from skyseed_v2.backend.bpf.dataplane.local_ingress import _business_ranges, _audit_local_ingress, _create_local_ingress, _delete_local_ingress
from skyseed_v2.backend.runner.process_incarnation import (
    _capture_process_incarnations,
    _read_process_start_time_ticks,
)
from skyseed_v2.backend.linux.bpf import (
    _BPF_MAP_TYPE_ARRAY,
    _BPF_MAP_TYPE_RINGBUF,
    _bpf_call_counts,
    _create_map,
    _pin_object,
    _open_pinned,
    _update_map,
    _update_map_entries,
    _probe_batch_update_support,
    _lookup_map,
    _lookup_u64_map,
    _update_u64_map,
    _delete_map,
)


_JOINED_NETLINK_CALLS = 0


def _kernel_call_counts() -> dict[str, int]:
    return {"netlink_request_calls": _netlink._request_count() + _JOINED_NETLINK_CALLS,
            **_bpf_call_counts()}


_INTERFACE_NAME = re.compile(r"sv2[0-9a-f]{10}[ab]")


_TELEMETRY_RING_CAPACITY = 1 << 20


class _InitializationFailure(RuntimeError):
    """Retain ownership when failed startup could not finish its own cleanup."""

    def __init__(self, message: str, *, evidence: dict[str, Any]) -> None:
        super().__init__(message)
        self.evidence = evidence


class _PrePublicationActivationFailure(RuntimeError):
    """Carry cleanup ownership after a failed pre-publication mutation."""

    def __init__(self, message: str, *, evidence: dict[str, Any]) -> None:
        super().__init__(message)
        self.evidence = evidence


class _PostPublicationActivationFailure(RuntimeError):
    """Report failure after BPF forwarding publication without pseudo-rollback."""

    def __init__(self, message: str, *, evidence: dict[str, Any]) -> None:
        super().__init__(message)
        self.evidence = evidence


def _worker_count(request: dict[str, Any], item_count: int) -> int:
    value = request.get("worker_count", 1)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("Runner privileged worker_count must be an int")
    if value <= 0 or value > 64:
        raise ValueError("Runner privileged worker_count must be between 1 and 64")
    return min(value, max(1, item_count))


def _parallel_map(request: dict[str, Any], items: list[Any], operation) -> list[Any]:
    """Bound independent startup namespace work; formal Execution never calls this."""
    if not items:
        return []
    workers = _worker_count(request, len(items))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="skyseed-bpf-prepare") as pool:
        return list(pool.map(operation, items))


def _timed_call(timings: dict[str, list[int]] | None, category: str, operation, *args, **kwargs):
    """Accumulate attempted call wall time, including a failing call; no per-call records."""
    if timings is None:
        return operation(*args, **kwargs)
    started = monotonic_ns()
    try:
        return operation(*args, **kwargs)
    finally:
        elapsed = monotonic_ns() - started
        value = timings.setdefault(category, [0, 0])
        value[0] += elapsed
        value[1] += 1


def _timing_records(timings: dict[str, list[int]]) -> dict[str, dict[str, Any]]:
    return {name: {"duration_seconds": value[0] / 1e9, "operation_count": value[1]}
            for name, value in timings.items()}


def _assert_interface_name(value: str) -> str:
    if not isinstance(value, str) or _INTERFACE_NAME.fullmatch(value) is None:
        raise ValueError(f"invalid Runner-owned interface name: {value!r}")
    return value


def _assert_netns(node: dict[str, Any]) -> None:
    pid = int(node["pid"])
    expected_start_time = int(node["process_start_time_ticks"])
    actual_start_time = _read_process_start_time_ticks(pid)
    if actual_start_time != expected_start_time:
        raise RuntimeError(
            f"node process incarnation is stale: pid={pid}, "
            f"expected={expected_start_time}, actual={actual_start_time}"
        )
    expected = int(node["netns_inode"])
    try:
        actual = os.stat(f"/proc/{pid}/ns/net").st_ino
    except OSError as error:
        raise RuntimeError(f"node PID/netns is unavailable: {pid}") from error
    if actual != expected:
        raise RuntimeError(
            f"node PID/netns evidence is stale: pid={pid}, "
            f"expected={expected}, actual={actual}"
        )


def _ip_key(address: str) -> int:
    packed = socket.inet_aton(address)
    return struct.unpack("=I", packed)[0]


def _is_zone_address(value: object) -> bool:
    return isinstance(value, str) and value.startswith(("198.18.", "198.19."))


def _namespace_ipv4_address_facts(
    node: dict[str, Any],
) -> tuple[tuple[IPv4Address, int], ...]:
    return _LIVE_NODES[node["key"]]["netlink"].ipv4_address_facts()


def _namespace_ipv4_address_networks(
    node: dict[str, Any],
) -> tuple[IPv4Network, ...]:
    address_networks = tuple(
        ip_network(f"{address}/{prefix_length}", strict=False)
        for address, prefix_length in _namespace_ipv4_address_facts(node)
    )

    return address_networks


def _namespace_ipv4_networks(
    node: dict[str, Any],
) -> tuple[tuple[IPv4Network, ...], tuple[tuple[int, IPv4Network], ...]]:
    address_networks = _namespace_ipv4_address_networks(node)
    return address_networks, _LIVE_NODES[node["key"]]["netlink"].ipv4_route_inventory()


def _audit_node_addresses(request: dict[str, Any]) -> dict[str, Any]:
    try:
        selected_range = ip_network(request["node_address_range"], strict=True)
        if not isinstance(selected_range, IPv4Network):
            raise ValueError("node address range must be IPv4")
        zone_networks = tuple(
            ip_interface(prefix).network for prefix in request["zone_prefixes"]
        )
        if any(not isinstance(network, IPv4Network) for network in zone_networks):
            raise ValueError("Zone prefix must be IPv4")
        nodes = tuple(request["nodes"])
        node_addresses = tuple(
            IPv4Address(node["node_address"]) for node in nodes
        )
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("Runner node address audit request is invalid") from error
    if len(set(node_addresses)) != len(nodes):
        raise RuntimeError("Runner node address audit found duplicate allocation")
    if any(address not in selected_range for address in node_addresses):
        raise RuntimeError("Runner node address lies outside selected range")
    if any(selected_range.overlaps(zone_network) for zone_network in zone_networks):
        raise RuntimeError("Runner node address collides with compiled Zone identity")
    # Build once per coverage kind, rather than once per node or destination.
    reserved_by_coverage = {
        covered: _IPv4Ranges(tuple(ip_network(v) for v in _business_ranges(str(selected_range), zone_networks))
                             if covered else (selected_range, *zone_networks))
        for covered in {node.get('program') is not None for node in nodes}
    }

    def audit_node(node: dict[str, Any]) -> tuple[int, int]:
        _assert_netns(node)
        address_networks, route_inventory = _namespace_ipv4_networks(node)
        route_networks = tuple(network for _table, network in route_inventory if network.prefixlen)
        reserved = reserved_by_coverage[node.get('program') is not None]
        for source, networks in (
            ("address", address_networks),
            ("route", route_networks),
        ):
            collision = next(
                (network for network in networks if reserved.overlap_end(network) is not None),
                None,
            )
            if collision is not None:
                raise RuntimeError(
                    "Runner node address range collides with live namespace "
                    f"{source} during Initialization: node={node['key']}, "
                    f"selected={selected_range}, observed={collision}"
                )
        if node.get('program') is not None:
            _audit_local_ingress(_LIVE_NODES[node['key']],
                _business_ranges(str(selected_range), zone_networks), route_inventory)
        return len(address_networks), len(route_networks)

    observed = _parallel_map(
        request, list(nodes), audit_node
    )
    observed_addresses = sum(value[0] for value in observed)
    observed_routes = sum(value[1] for value in observed)
    return {
        "result": "passed",
        "selected_node_address_range": str(selected_range),
        "allocated_node_addresses": len(node_addresses),
        "compiled_zone_prefixes": len(zone_networks),
        "audited_node_namespaces": len(nodes),
        "observed_ipv4_address_prefixes": observed_addresses,
        "observed_non_default_ipv4_route_prefixes": observed_routes,
        "audited_sources": [
            "compiler-zone-prefixes",
            "live-node-netns-ipv4-addresses",
            "live-node-netns-non-default-ipv4-routes",
        ],
    }


_LIVE_NODES: dict[str, dict[str, Any]] = {}


_GENERATION_FD = -1


_GENERATION_READERS_FD = -1


_GENERATIONS: dict[int, dict[str, int]] = {}


_VERSIONS: dict[tuple[str, int], dict[str, Any]] = {}


_READY_GENERATIONS: set[int] = set()


_OWNED_PIN_ROOT: Path | None = None


_OWNED_NODE_ADDRESSES: set[str] = set()


_INITIALIZATION_REQUESTED = False


_FOUNDATION_OUTCOME = "no-mutation"


_RETIREMENT_POOL: ThreadPoolExecutor | None = None


_RETIREMENT_WORKERS = 0


_OWNED_RETIREMENTS: dict[tuple[str, int], dict[str, dict[str, Any]]] = {}


def _start_retirement_workers(count: int) -> None:
    global _RETIREMENT_POOL, _RETIREMENT_WORKERS
    if _RETIREMENT_POOL is not None:
        raise RuntimeError("retirement workers already initialized")
    _RETIREMENT_WORKERS = count
    _RETIREMENT_POOL = ThreadPoolExecutor(max_workers=count, thread_name_prefix="skyseed-bpf-retire")
    ready = Barrier(count + 1)
    futures = [_RETIREMENT_POOL.submit(ready.wait) for _ in range(count)]
    ready.wait()
    for future in futures:
        future.result()


def _forget_connection(connection):
    key = (connection["retirement_node"], connection["retirement_group"])
    owned = _OWNED_RETIREMENTS[key]
    owned.pop(connection["connection"])
    if not owned:
        del _OWNED_RETIREMENTS[key]
    for side in ("a", "b"):
        _LIVE_NODES[connection[f"node_{side}"]]["indexes"].pop(connection[f"interface_{side}"], None)


def _retire_batches(batches, *, cleanup=False):
    """Join bounded namespace jobs; each retained socket has one ACK reader."""
    global _JOINED_NETLINK_CALLS
    started = monotonic_ns()
    by_node = {}
    for batch in batches:
        key = (batch["key"], batch["group"])
        owned = _OWNED_RETIREMENTS.get(key, {})
        if {value["connection"] for value in batch["connections"]} != owned.keys():
            raise RuntimeError("retirement batch differs from the exact owned cohort")
        by_node.setdefault(batch["key"], []).append(batch)
    result = {"retired_pairs": 0, "deletion_requests": 0, "grouped_batches": 0,
              "task_duration_seconds_sum": 0.0, "errors": [],
              "workers": min(_RETIREMENT_WORKERS, len(by_node))}

    def retire_node(item):
        key, values = item
        task_started = monotonic_ns()
        calls = _netlink._request_count()
        confirmed = []
        errors = []
        attempted = 0
        for batch in values:
            attempted += 1
            try:
                _LIVE_NODES[key]["netlink"].delete_group(batch["group"])
            except OSError as error:
                if not cleanup or error.errno not in (errno.ENODEV, errno.ENOENT):
                    errors.append(f"{key} group {batch['group']}: {error}")
                    continue
            except Exception as error:
                errors.append(f"{key} group {batch['group']}: {error}")
                continue
            confirmed.append(batch)
        return confirmed, errors, attempted, _netlink._request_count() - calls, monotonic_ns() - task_started

    pending = set()
    items = iter(by_node.items())
    if by_node and _RETIREMENT_POOL is None:
        raise RuntimeError("retirement workers were not initialized in Initialization")
    for _ in range(min(_RETIREMENT_WORKERS, len(by_node))):
        pending.add(_RETIREMENT_POOL.submit(retire_node, next(items)))
    while pending:
        done, pending = wait(pending, return_when=FIRST_COMPLETED)
        for future in done:
            confirmed, errors, attempted, calls, duration = future.result()
            _JOINED_NETLINK_CALLS += calls
            result["deletion_requests"] += attempted
            result["grouped_batches"] += len(confirmed)
            result["task_duration_seconds_sum"] += duration / 1e9
            result["errors"].extend(errors)
            for batch in confirmed:
                owned = _OWNED_RETIREMENTS[(batch["key"], batch["group"])]
                for connection in tuple(owned.values()):
                    _forget_connection(connection)
                    result["retired_pairs"] += 1
            item = next(items, None)
            if item is not None:
                pending.add(_RETIREMENT_POOL.submit(retire_node, item))
    result["wall_seconds"] = (monotonic_ns() - started) / 1e9
    return result


def _setns(fd: int) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.setns(ctypes.c_int(fd), ctypes.c_int(0x40000000)) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


def _retain_namespaces(nodes):
    try:
        _network._retain_namespaces(nodes, _LIVE_NODES, assert_netns=_assert_netns,
                                    route_socket=_RouteSocket, setns=_setns)
    except BaseException:
        _close_foundation()
        raise


def _close_foundation(timings=None) -> None:
    global _GENERATION_FD, _GENERATION_READERS_FD, _RETIREMENT_POOL, _RETIREMENT_WORKERS
    if _RETIREMENT_POOL is not None:
        _timed_call(timings, "retirement_worker_shutdown", _RETIREMENT_POOL.shutdown, wait=True)
        _RETIREMENT_POOL = None
        _RETIREMENT_WORKERS = 0
    for node in _LIVE_NODES.values():
        for obj in node.pop("objects", []):
            _timed_call(timings, "bpf_object_close", obj.close)
        for fd in node["maps"].values():
            _timed_call(timings, "bpf_descriptor_close", os.close, fd)
        _timed_call(timings, "retained_socket_close", node["netlink"].close)
        _timed_call(timings, "retained_namespace_fd_close", os.close, node["namespace_fd"])
    _LIVE_NODES.clear()
    _GENERATIONS.clear()
    _VERSIONS.clear()
    _READY_GENERATIONS.clear()
    if _GENERATION_READERS_FD >= 0:
        _timed_call(timings, "bpf_descriptor_close", os.close, _GENERATION_READERS_FD)
        _GENERATION_READERS_FD = -1
    if _GENERATION_FD >= 0:
        _timed_call(timings, "bpf_descriptor_close", os.close, _GENERATION_FD)
        _GENERATION_FD = -1


def _reserve_foundation_descriptors(node_count: int, worker_count: int, object_maps: int = 10) -> None:
    # Retained namespace/socket, runtime FDs, object-owned/duplicated map FDs,
    # program and BTF. Include transient per-worker setup and shared descriptors.
    required = (10 + object_maps) * node_count + (8 + object_maps) * worker_count + 64
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if hard != resource.RLIM_INFINITY and required > hard:
        raise RuntimeError(
            f"Runner foundation needs {required} file descriptors; hard limit is {hard}"
        )
    if soft != resource.RLIM_INFINITY and soft < required:
        resource.setrlimit(resource.RLIMIT_NOFILE, (required, hard))


def _initialize(request: dict[str, Any]) -> dict[str, Any]:
    global _GENERATION_FD, _GENERATION_READERS_FD, _OWNED_PIN_ROOT, _INITIALIZATION_REQUESTED, _FOUNDATION_OUTCOME
    if _INITIALIZATION_REQUESTED:
        raise RuntimeError("Runner foundation initialization is one-shot")
    _INITIALIZATION_REQUESTED = True
    root = _pin_root(request["pin_root"])
    nodes = tuple(request["nodes"])
    from .object_contract import _map_layouts
    shared_layouts = _map_layouts(False)
    def capacity_for(symbol, node=None):
        capacity = shared_layouts[symbol][4]
        bindings = {"node_count": len(nodes), **request, **(node or {})}
        return int(bindings[capacity]) if isinstance(capacity, str) else capacity

    def create_map(symbol, node=None):
        name, kind, key_size, value_size, capacity = shared_layouts[symbol]
        capacity = capacity_for(symbol, node)
        return _create_map(name, key_size=key_size, value_size=value_size,
                           max_entries=capacity, map_type=kind)
    try:
        if root.exists():
            raise RuntimeError(f"Runner pin root already exists: {root}")
        foundation = _validate_foundation(request["dataplane"], tuple(n["key"] for n in nodes))
        if any(n["program"] != f["program"] for n, f in zip(nodes, foundation["nodes"])):
            raise ValueError("live attachment request differs from sealed scopes")
        _reserve_foundation_descriptors(len(nodes), _worker_count(request, len(nodes)),
                                        max((len(p["declaration"]["maps"]) for p in foundation["programs"]), default=0))
        _retain_namespaces(nodes)
        group_end = request["retirement_group_end"]
        if type(group_end) is not int or not _RETIREMENT_GROUP_BASE < group_end <= 0xFFFFFFFF:
            raise ValueError("invalid finite retirement group range")
        for live in _LIVE_NODES.values():
            live["loopback"] = live["netlink"].initial_links(reserved_prefix="sv2", group_start=_RETIREMENT_GROUP_BASE, group_end=group_end)
        _start_retirement_workers(_worker_count(request, len(nodes)))
        batch_update_supported = _probe_batch_update_support()
        collision_audit_started = monotonic()
        collision_audit = _audit_node_addresses(request)
        ranges = _business_ranges(request["node_address_range"], request["zone_prefixes"])
        collision_audit_seconds = monotonic() - collision_audit_started
        root.mkdir(parents=True)
    except BaseException as error:
        _close_foundation()
        raise _InitializationFailure(str(error), evidence={
            "initialization_outcome": "no-mutation",
        }) from error
    _OWNED_PIN_ROOT = root
    _FOUNDATION_OUTCOME = "partial"
    installed_node_keys = _OWNED_NODE_ADDRESSES
    installed_node_keys_lock = Lock()
    active_generation_fd = -1
    generation_readers_fd = -1
    telemetry_ring_fd = -1
    telemetry_loss_fd = -1
    telemetry_enabled = request.get("telemetry_enabled") is True
    try:
        shared_scaffolding_started = monotonic()
        active_generation_fd = create_map("active_generation")
        _pin_object(active_generation_fd, root / "active_generation")
        _update_map(
            active_generation_fd, 0, int(request["inactive_generation"])
        )
        node_maps = ("ingress_bindings", "egress_bindings")
        if request.get("continuation_enabled") is True:
            generation_readers_fd = create_map("generation_readers")
            _pin_object(generation_readers_fd, root / "generation_readers")
        if telemetry_enabled:
            telemetry_ring_fd = create_map("traffic_events")
            telemetry_loss_fd = create_map("traffic_losses")
            _pin_object(telemetry_ring_fd, root / "traffic_events")
            _pin_object(telemetry_loss_fd, root / "traffic_losses")
        shared_scaffolding_seconds = monotonic() - shared_scaffolding_started
        def initialize_node(node: dict[str, Any]) -> dict[str, Any]:
            live = _LIVE_NODES[node["key"]]
            channel = live["netlink"]
            with installed_node_keys_lock:
                installed_node_keys.add(node["key"])
            # The collision audit established absence. Retain ownership before
            # sending the mutation, including a lost or failed ACK.
            _network._create_identity(live, node["node_address"])
            node_root = root / node["pin_component"]
            node_root.mkdir()
            descriptors = {}
            try:
                for name in node_maps:
                    fd = create_map(name, node)
                    descriptors[name] = fd
                    _pin_object(fd, node_root / name)
                bindings = {**descriptors, "active_generation": active_generation_fd,
                            "generation_readers": generation_readers_fd,
                            "traffic_events": telemetry_ring_fd, "traffic_losses": telemetry_loss_fd,
                            "node_slot": int(node["node_slot"])}
                live['program'] = node['program']
                live['table_layouts'] = {}
                live['objects'] = []
                capacity_rows = {t['program']: t for t in node['table_capacities']}
                if node['program'] is not None:
                    index = node['program']
                    program = foundation['programs'][index]
                    declaration = program['declaration']
                    capacities = {name: capacity_for(name, node) for name in shared_layouts}
                    instance_bindings = dict(bindings)
                    if declaration['managed_tables']:
                        for symbol, c_name, capacity in (
                            ('active_links', 'active_links', capacity_rows[index]['active']),
                            ('routes', 'routes', capacity_rows[index]['routes']),
                            ('generation_versions', 'gen_versions', request['resident_generation_capacity'])):
                            kind, key_size, value_size = declaration['maps'][c_name][:3]
                            fd = _create_map(c_name, key_size=key_size, value_size=value_size, max_entries=capacity, map_type=kind)
                            identity = f'{symbol}@{index}'
                            descriptors[identity] = fd
                            _pin_object(fd, node_root / identity)
                            instance_bindings[symbol] = fd
                            capacities[symbol] = capacity
                            live['table_layouts'][identity] = (key_size, value_size)
                    obj, program_fd = _load_object(program, bindings=instance_bindings,
                        capacities=capacities, node_slot=int(node['node_slot']), telemetry=telemetry_enabled,
                        continuation=request.get('continuation_enabled') is True)
                    live['objects'].append(obj)
                    identity = f'tc_ingress@{index}'
                    _pin_object(program_fd, node_root / identity)
                    # Open retained descriptors while this object is owned.
                    live['maps'][identity] = _open_pinned(node_root / identity)
                for name in descriptors:
                    live['maps'][name] = _open_pinned(node_root / name)
                if node['program'] is not None:
                    _create_local_ingress(live, ranges, live['maps'][f"tc_ingress@{node['program']}"], _update_map)

            finally:
                for fd in descriptors.values():
                    os.close(fd)
            return {
                "key": node["key"],
                "active_links": sum(t["active"] for t in node["table_capacities"]),
                "routes": sum(t["routes"] for t in node["table_capacities"]),
            }

        node_foundation_started = monotonic()
        map_capacities = _parallel_map(
            request,
            list(nodes),
            initialize_node,
        )
        node_foundation_seconds = monotonic() - node_foundation_started
        _GENERATION_FD = _open_pinned(root / "active_generation")
        if generation_readers_fd >= 0:
            _GENERATION_READERS_FD = _open_pinned(root / "generation_readers")
        return {
            "pin_root": str(root),
            "node_count": len(nodes),
            "active_generation": int(request["inactive_generation"]),
            "shared_active_generation_map": True,
            "bpf_batch_update_supported": batch_update_supported,
            "traffic_telemetry": {
                "enabled": telemetry_enabled,
                "shared_ring_buffer": telemetry_enabled,
                "ring_capacity_bytes": (
                    _TELEMETRY_RING_CAPACITY if telemetry_enabled else None
                ),
                "event_schema": (
                    _telemetry_event_schema() if telemetry_enabled else None
                ),
                "loss_counter_entries": len(nodes) if telemetry_enabled else 0,
            },
            "collision_audit": collision_audit,
            "installed_node_addresses": sorted(installed_node_keys),
            "map_capacities": map_capacities,
            "preparation_phases": {
                "collision_audit_seconds": collision_audit_seconds,
                "shared_bpf_map_pin_scaffolding_seconds": (
                    shared_scaffolding_seconds
                ),
                "node_address_map_program_scaffolding_seconds": (
                    node_foundation_seconds
                ),
            },
        }
    except BaseException as error:
        try:
            cleanup_result = _cleanup(
                {
                    **request,
                    "nodes": [
                        {
                            **node,
                            "node_address_owned": node["key"] in installed_node_keys,
                        }
                        for node in nodes
                    ],
                    "connections": (),
                },
                tolerate_errors=True,
            )
        except BaseException as cleanup_error:
            cleanup_result = {"errors": [str(cleanup_error)]}
        raise _InitializationFailure(str(error), evidence={
            "initialization_outcome": "partial" if cleanup_result.get("errors") else "cleaned",
            "installed_node_addresses": sorted(installed_node_keys),
            "cleanup_errors": cleanup_result.get("errors", []),
        }) from error
    finally:
        for file_descriptor in (
            telemetry_loss_fd,
            telemetry_ring_fd,
            active_generation_fd,
            generation_readers_fd,
        ):
            if file_descriptor >= 0:
                os.close(file_descriptor)


def _capture_incarnations(request: dict[str, Any]) -> dict[str, Any]:
    """Capture one complete PID set inside the privileged host-PID context."""

    raw_pids = request.get("pids")
    if not isinstance(raw_pids, list) or not raw_pids:
        raise ValueError("Runner privileged incarnation request needs a PID list")
    pids = tuple(raw_pids)
    values = _capture_process_incarnations(pids)
    if len(values) != len(pids):
        raise RuntimeError("Runner privileged incarnation capture is incomplete")
    return {
        "incarnations": values,
        "requested_pid_count": len(pids),
        "captured_pid_count": len(values),
        "capture_mode": "single-privileged-batch",
    }


def _prewrite_generation(request: dict[str, Any]) -> dict[str, Any]:
    generation = int(request["generation"])
    active_generation = _lookup_map(_GENERATION_FD, 0)
    if active_generation != 0xFFFFFFFF and generation <= active_generation:
        raise RuntimeError("prewrite must target a strictly future generation")
    records = request["generation_nodes"]
    if (len(records) > 64 or sum(
        len(record["active_entries"]) + len(record["route_entries"]) for record in records
    ) > 2048):
        raise ValueError("prewrite work unit exceeds the bounded channel budget")
    if generation not in _GENERATIONS:
        _GENERATIONS[generation] = {}
        if _GENERATION_READERS_FD >= 0:
            _update_u64_map(_GENERATION_READERS_FD, generation, 0)
    active_count = route_count = reused = new_versions = 0
    for record in records:
        node_key = record["key"]
        maps = _LIVE_NODES[node_key]["maps"]
        version = int(record["version"])
        identity = node_key, version
        value = _VERSIONS.get(identity)
        if node_key not in _GENERATIONS[generation]:
            if value is None:
                if version != generation:
                    raise RuntimeError("shared version is not resident")
                value = _VERSIONS[identity] = {"active": [], "routes": [], "references": 0, "complete": False}
                new_versions += 1
            elif version != generation:
                if not value["complete"] or record["active_entries"] or record["route_entries"]:
                    raise RuntimeError("shared version is incomplete or being overwritten")
                reused += 1
            value["references"] += 1
            _GENERATIONS[generation][node_key] = version
            for name in maps:
                if name.startswith("generation_versions@"):
                    _update_map(maps[name], generation, version)
        elif _GENERATIONS[generation][node_key] != version or value["complete"]:
            raise RuntimeError("generation node version cannot be replaced")
        def entries(rows, symbol):
            groups = {}
            for instance, encoded in rows:
                identity = f'{symbol}@{instance}'
                if identity not in maps:
                    raise ValueError('program does not declare this managed table instance')
                key_size, value_size = _LIVE_NODES[node_key]['table_layouts'][identity]
                data = bytes.fromhex(encoded)
                if len(data) != key_size + value_size or struct.unpack_from('<I', data)[0] != version:
                    raise ValueError('prepared map row/version encoding differs')
                groups.setdefault(identity, []).append((data[:key_size], data[key_size:]))
            return groups
        active = entries(record['active_entries'], 'active_links')
        routes = entries(record['route_entries'], 'routes')
        for groups, field in ((active, 'active'), (routes, 'routes')):
            for identity, rows in groups.items():
                # Retain all attempted keys BEFORE a batch operation, including partial failure.
                value[field].extend((identity, key) for key, _ in rows)
                count = _update_map_entries(maps[identity], rows, batch_supported=bool(request['bpf_batch_update_supported']))
                if field == 'active': active_count += count
                else: route_count += count
        if version == generation:
            value["complete"] = record.get("complete", True)
    if request.get("seal"):
        nodes = _GENERATIONS[generation]
        if nodes.keys() != _LIVE_NODES.keys() or any(
            not _VERSIONS[key, version]["complete"] for key, version in nodes.items()
        ):
            raise RuntimeError("generation prewrite is incomplete")
        _READY_GENERATIONS.add(generation)
    return {"preloaded_active_links": active_count, "preloaded_routes": route_count,
            "node_versions_written": new_versions, "node_versions_reused": reused}


def _reclaim_generations(request: dict[str, Any]) -> dict[str, Any]:
    """Retire a bounded number of entries after publication and reader quiescence."""
    if _GENERATION_READERS_FD < 0:
        raise RuntimeError("generation reclaim requires instrumented in-flight readers")
    budget = request.get("maximum_entries", 1024)
    if type(budget) is not int or not 1 <= budget <= 2048:
        raise ValueError("invalid reclaim work-unit budget")
    active = _lookup_map(_GENERATION_FD, 0)
    if active is None or active == 0xFFFFFFFF:
        return {"reclaimed_generations": [], "deleted_entries": 0, "reader_wait": False}
    cutoff = min(int(request["before_generation"]), active)
    deleted = units = 0
    reclaimed = []
    reader_wait = False
    for generation in sorted(g for g in _GENERATIONS if g < cutoff):
        if _lookup_u64_map(_GENERATION_READERS_FD, generation) != 0:
            reader_wait = True
            continue
        nodes = _GENERATIONS[generation]
        while nodes and units < budget:
            key = next(iter(nodes))
            version = nodes[key]
            value = _VERSIONS[key, version]
            maps = _LIVE_NODES[key]["maps"]
            if value["references"] == 1:
                for field, map_name in (("active", "active_links"), ("routes", "routes")):
                    entries = value[field]
                    while entries and units < budget:
                        identity, key_bytes = entries[-1]
                        deleted += int(_delete_map(maps[identity], key_bytes))
                        entries.pop()
                        units += 1
                if value["active"] or value["routes"] or units >= budget:
                    break
                del _VERSIONS[key, version]
            else:
                value["references"] -= 1
            for name in maps:
                if name.startswith("generation_versions@"):
                    deleted += int(_delete_map(maps[name], generation))
            del nodes[key]
            units += 1
        if not nodes:
            _delete_map(_GENERATION_READERS_FD, generation)
            del _GENERATIONS[generation]
            _READY_GENERATIONS.discard(generation)
            reclaimed.append(generation)
        if units >= budget:
            break
    return {"reclaimed_generations": reclaimed, "deleted_entries": deleted,
            "work_units": units, "reader_wait": reader_wait,
            "active_generation": active, "resident_generations": len(_GENERATIONS),
            "resident_node_versions": len(_VERSIONS)}


def _activate_boundary(request: dict[str, Any]) -> dict[str, Any]:
    """Execute one prepared delta through retained namespace sockets and BPF FDs."""
    activation_started_ns = monotonic_ns()
    generation = int(request["generation"])
    expected = int(request["expected_active_generation"])
    changed = request["generation_changed"]
    if _GENERATIONS and changed and generation not in _READY_GENERATIONS:
        raise RuntimeError("Execution cannot publish an unready generation")
    connections = request["connections"]
    batches = request["retirement_batches"]
    removed = [connection for batch in batches for connection in batch["connections"]]
    operations = {node["key"]: node for node in request["operation_nodes"]}
    new_endpoints: dict[str, list[tuple[str, int, dict[str, Any]]]] = {}
    old_endpoints: dict[str, list[tuple[str, int]]] = {}
    for connection in connections:
        for side in ("a", "b"):
            new_endpoints.setdefault(connection[f"node_{side}"], []).append((
                connection[f"interface_{side}"], connection[f"logical_port_{side}"],
                connection[f"parameters_{side}"],
            ))
    for connection in removed:
        for side in ("a", "b"):
            old_endpoints.setdefault(connection[f"node_{side}"], []).append((
                connection[f"interface_{side}"], connection[f"logical_port_{side}"],
            ))
    created = []
    deleted = 0
    identities = []
    published = False
    mutation_started = False
    complete = False
    phase = "pre-publication"
    timings = {}
    switch = {}
    anchor = monotonic_ns()
    detail: dict[str, list[int]] = {}
    retirement = None

    def evidence() -> dict[str, Any]:
        return {
            "boundary_timestamp": request["boundary_timestamp"],
            "last_completed_active_frontier": request.get("last_completed_active_frontier"),
            "previous_generation": expected, "active_generation": generation if published else expected,
            "generation_publication_occurred": published, "generation_publications": int(published),
            "generation_reused": not changed, "target_transition_complete": complete,
            "live_mutation_started": mutation_started,
            "created_connections": len(created), "deleted_connections": deleted,
            "interface_identities": identities,
            "withdrawn_logical_ports": [port for values in old_endpoints.values() for _, port in values],
            "activation_anchor_monotonic_ns": anchor,
            "activation_phases": timings,
            "detailed_timing_version": 2,
            "retirement": retirement,
            "standalone_mac_requests": 0, "standalone_down_requests": 0,
            "operation_timings": _timing_records(detail),
            "helper_activation_start_monotonic_ns": activation_started_ns,
            "helper_activation_evidence_monotonic_ns": monotonic_ns(),
            **switch,
        }

    def index(node: dict[str, Any], name: str) -> int:
        # Only new-interface creation populates this retained binding ledger.
        # A missing/stale retained resource fails the corresponding operation.
        return node["indexes"][name]

    try:
        if _lookup_map(_GENERATION_FD, 0) != expected:
            raise RuntimeError("active generation predecessor differs")
        started = monotonic()
        for connection in connections:
            node = _LIVE_NODES[connection["node_a"]]
            peer = _LIVE_NODES[connection["node_b"]]
            mutation_started = True
            owner = connection["retirement_node"]
            group = connection["retirement_group"]
            _OWNED_RETIREMENTS.setdefault((owner, group), {})[connection["connection"]] = connection
            try:
                _timed_call(detail, "pre_veth_creation", _network._create_connection,
                            _LIVE_NODES, connection,
                            mac=request["common_mac"], group=group if owner == connection["node_a"] else 0,
                            peer_group=group if owner == connection["node_b"] else 0)
            except OSError as error:
                if error.errno == errno.EEXIST:
                    _forget_connection(connection)
                raise
            created.append(connection)
        # Affected namespaces only. A socket is already bound to each namespace;
        # profiling does not introduce namespace entries or process barriers.
        for key in sorted(operations.keys() | new_endpoints.keys()):
            node = _LIVE_NODES[key]
            channel = node["netlink"]
            maps = node["maps"]
            operation = operations.get(key, {})
            for name, port, parameters in new_endpoints.get(key, ()):
                mutation_started = True
                ifindex = _timed_call(detail, "pre_ifindex_resolution", channel.link_index, name)
                node["indexes"][name] = ifindex
                _timed_call(detail, "pre_qdisc_creation", channel.netem, ifindex, parameters)
                attachment = node['program']
                if attachment is not None:
                    _timed_call(detail, "pre_bpf_attachment", channel.attach_bpf, ifindex, maps[f'tc_ingress@{attachment}'])
                _timed_call(detail, "pre_ingress_binding_update", _update_map, maps["ingress_bindings"], ifindex, port)
                _timed_call(detail, "pre_egress_binding_update", _update_map, maps["egress_bindings"], port, ifindex)
                identities.append({"key": key, "interface": name, "ifindex": ifindex, "logical_port_id": port})
                _timed_call(detail, "pre_link_up", channel.set_link, ifindex, up=True)
            for value in operation.get("qdisc", ()):
                mutation_started = True
                _timed_call(detail, "pre_existing_qdisc_update", channel.netem, index(node, value["interface"]), value)
            for address in operation.get("zone_address_add", ()):
                mutation_started = True
                _timed_call(detail, "pre_zone_address_addition", channel.address, node["identity"], address)
        timings["pre_publication"] = {"duration_seconds": monotonic() - started}
        phase = "active-generation-publication"
        if changed:
            switch["generation_switch_start_monotonic_ns"] = monotonic_ns()
            try:
                _update_map(_GENERATION_FD, 0, generation)
            except BaseException:
                detail["active_generation_update"] = [monotonic_ns() - switch["generation_switch_start_monotonic_ns"], 1]
                raise
            published = True
            switch["generation_switch_end_monotonic_ns"] = monotonic_ns()
            timings["active_generation_publication"] = {"duration_seconds": (
                switch["generation_switch_end_monotonic_ns"]
                - switch["generation_switch_start_monotonic_ns"]
            ) / 1e9}
            detail["active_generation_update"] = [switch["generation_switch_end_monotonic_ns"] - switch["generation_switch_start_monotonic_ns"], 1]
        phase = "post-publication"
        started = monotonic()
        for key in sorted(operations.keys() | old_endpoints.keys()):
            node = _LIVE_NODES[key]
            operation = operations.get(key, {})
            for address in operation.get("zone_address_delete", ()):
                _timed_call(detail, "post_zone_address_removal", node["netlink"].address, node["identity"], address, delete=True)
            for name, port in old_endpoints.get(key, ()):
                ifindex = index(node, name)
                if not _timed_call(detail, "post_ingress_binding_deletion", _delete_map, node["maps"]["ingress_bindings"], ifindex):
                    raise RuntimeError("withdrawn ingress binding was absent")
                if not _timed_call(detail, "post_egress_binding_deletion", _delete_map, node["maps"]["egress_bindings"], port):
                    raise RuntimeError("withdrawn egress binding was absent")
        timings["withdrawal"] = {"duration_seconds": monotonic() - started}
        retirement = _retire_batches(batches)
        timings["physical_deletion"] = {"duration_seconds": retirement["wall_seconds"]}
        deleted = retirement["retired_pairs"]
        detail["post_veth_deletion"] = [round(retirement["wall_seconds"] * 1e9), deleted]
        if retirement["errors"]:
            raise RuntimeError("physical retirement incomplete: " + "; ".join(retirement["errors"]))
        timings["post_publication"] = {"duration_seconds": monotonic() - started}
        complete = True
        return evidence()
    except BaseException as error:
        rollback_errors = []
        if not published:
            # Roll back newly allocated physical/binding resources through the
            # same sockets and syscalls, retaining all uncertain cleanup ownership.
            for connection in reversed(created):
                try:
                    for side in ("a", "b"):
                        node = _LIVE_NODES[connection[f"node_{side}"]]
                        name = connection[f"interface_{side}"]
                        ifindex = node["indexes"].get(name)
                        if ifindex is not None:
                            _delete_map(node["maps"]["ingress_bindings"], ifindex)
                        _delete_map(node["maps"]["egress_bindings"], connection[f"logical_port_{side}"])
                    node = _LIVE_NODES[connection["node_a"]]
                    name = connection["interface_a"]
                    ifindex = node["indexes"].get(name)
                    if ifindex is None:
                        ifindex = node["netlink"].link_index(name)
                    node["netlink"].delete_link(ifindex)
                    _forget_connection(connection)
                except BaseException as rollback_error:
                    rollback_errors.append(str(rollback_error))
        failure_evidence = {
            **evidence(), "failure_phase": phase, "failure_type": type(error).__name__,
            "failure_message": str(error), "live_state_may_be_partial": mutation_started,
            "cleanup_connections": list(connections) + list(removed),
            "cleanup_ownership_retained": True,
            "candidate_rollback_attempted": bool(created) and not published,
            "candidate_rollback_complete": not rollback_errors if created and not published else None,
            "rollback_errors": rollback_errors,
        }
        failure_type = _PostPublicationActivationFailure if published else _PrePublicationActivationFailure
        raise failure_type(str(error), evidence=failure_evidence) from error


def _cleanup(request: dict[str, Any], *, tolerate_errors: bool = False) -> dict[str, Any]:
    global _OWNED_PIN_ROOT, _FOUNDATION_OUTCOME
    detail: dict[str, list[int]] = {}
    phases = {}
    root = _pin_root(request["pin_root"])
    if _OWNED_PIN_ROOT is None:
        # A rejected startup must never remove a conflicting pre-existing root.
        _close_foundation(detail)
        return {"deleted_connections": 0, "pin_root_removed": not root.exists(),
                "cleanup_confirmed": True, "initialization_outcome": _FOUNDATION_OUTCOME, "errors": [],
                "detailed_timing_version": 1, "cleanup_operations": _timing_records(detail)}
    if _OWNED_PIN_ROOT != root:
        raise RuntimeError("cleanup pin root differs from the owned foundation")
    errors = []
    deleted = 0
    network_started = monotonic_ns()
    retirement = _retire_batches([
        {"key": key, "group": group, "connections": tuple(owned.values())}
        for (key, group), owned in _OWNED_RETIREMENTS.items()
    ], cleanup=True)
    deleted = retirement["retired_pairs"]
    errors.extend(retirement["errors"])
    detail["remaining_veth_deletion"] = [round(retirement["wall_seconds"] * 1e9), deleted]
    network_ended = monotonic_ns()
    phases["remaining_network_seconds"] = (network_ended - network_started) / 1e9
    for node in _LIVE_NODES.values():
        try:
            _timed_call(detail, "local_ingress_removal", _delete_local_ingress, node)
            _timed_call(detail, "identity_removal", _network._delete_identity, node)
        except BaseException as error:
            errors.append(str(error))
    addresses_ended = monotonic_ns()
    phases["address_removal_seconds"] = (addresses_ended - network_ended) / 1e9
    if not errors:
        if root.exists():
            for path in sorted(root.rglob("*"), reverse=True):
                try:
                    _timed_call(detail, "pin_directory_removal", path.rmdir) if path.is_dir() else _timed_call(detail, "bpf_unpin", path.unlink)
                except OSError as error:
                    errors.append(str(error))
            if not errors:
                _timed_call(detail, "pin_directory_removal", root.rmdir)
        unpin_ended = monotonic_ns()
        phases["bpf_unpin_seconds"] = (unpin_ended - addresses_ended) / 1e9
        if not errors:
            _close_foundation(detail)
            phases["retained_descriptor_close_seconds"] = (monotonic_ns() - unpin_ended) / 1e9
            _OWNED_PIN_ROOT = None
            _FOUNDATION_OUTCOME = "cleaned"
            _OWNED_NODE_ADDRESSES.clear()
    if errors and not tolerate_errors:
        error = RuntimeError("Runner cleanup left residue: " + "; ".join(errors))
        error.evidence = {"retirement": retirement, "cleanup_phases": phases, "cleanup_operations": _timing_records(detail)}
        raise error
    return {"deleted_connections": deleted, "pin_root_removed": not root.exists(),
            "cleanup_confirmed": not errors, "initialization_outcome": "partial" if errors else "cleaned",
            "errors": errors, "detailed_timing_version": 2,
            "retirement": retirement, "cleanup_phases": phases, "cleanup_operations": _timing_records(detail)}


def _dispatch_action(action: str, request: dict[str, Any]) -> dict[str, Any]:
    actions = {
        "initialize": _initialize,
        "prewrite_generation": _prewrite_generation,
        "reclaim_generations": _reclaim_generations,
        "activate_boundary": _activate_boundary,
        "cleanup": _cleanup,
    }
    if action not in actions:
        raise ValueError("unknown private Runner command action")
    return actions[action](request)


def _respond(action: str, request: dict[str, Any], *, request_timing=None) -> dict[str, Any]:
    # Execution/Cleanup add joined worker counters to the dispatch thread counters.
    # Startup worker counts remain unavailable. These are attempted calls.
    counters = None if action == "initialize" else _kernel_call_counts()
    started = monotonic_ns()
    try:
        result = _dispatch_action(action, request)
        ended = monotonic_ns()
        response = {"status": "ok", "result": result}
    except BaseException as error:
        ended = monotonic_ns()
        response = {"status": "error", "error": str(error), "error_type": type(error).__name__,
                "evidence": getattr(error, "evidence", None), "traceback": traceback.format_exc()}
    response["helper_timing"] = {
        **(request_timing or {}),
        "dispatch_start_monotonic_ns": started, "dispatch_end_monotonic_ns": ended,
        "dispatch_seconds": (ended - started) / 1e9,
        "kernel_calls": None if counters is None else {
            key: value - counters[key] for key, value in _kernel_call_counts().items()
        },
    }
    return response


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    if sys.argv[1] == "capture_incarnations":
        try:
            result = _capture_incarnations(json.load(sys.stdin))
            print(json.dumps({"status": "ok", "result": result}), flush=True)
            return 0
        except BaseException as error:
            print(json.dumps({"status": "error", "error": str(error)}), flush=True)
            return 1
    if sys.argv[1] != "serve":
        return 2
    try:
        for line in sys.stdin:
            received = monotonic_ns()
            envelope = json.loads(line)
            decoded = monotonic_ns()
            response = _respond(envelope["action"], envelope["request"], request_timing={
                "line_received_monotonic_ns": received,
                "request_decoded_monotonic_ns": decoded,
                "request_decode_seconds": (decoded - received) / 1e9,
            })
            print(json.dumps(response, separators=(",", ":")), flush=True)
    finally:
        _close_foundation()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
