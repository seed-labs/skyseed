"""Versioned BPF ELF foundation, material replay and exact capacity accounting.

No compiler, policy expansion, kernel loading or resource owner.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from ipaddress import IPv4Address, ip_network
import struct
import sys
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from skyseed.backend.execution_materials.mechanics import (
    _CapacityAuditError, _node_key, _cached_interface_names, _INTERFACE_IDENTITY_DERIVATION,
)

_DATAPLANE_CONTRACT = "skyseed-tc-elf/3"
def _validate_foundation(value, node_keys):
    if (not isinstance(value, dict) or set(value) != {"contract", "byteorder", "programs", "node_address_range", "nodes"}
        or value["contract"] != _DATAPLANE_CONTRACT or value["byteorder"] != "little"
        or sys.byteorder != value["byteorder"]):
        raise ValueError("unsupported DataPlane contract/map ABI; regenerate execution materials")
    from .object_contract import _validate_program
    if not isinstance(value["programs"], list):
        raise ValueError("DataPlane program definitions must be a list")
    for program in value["programs"]:
        _validate_program(program)
    nodes = value["nodes"]
    network = ip_network(value["node_address_range"])
    if any(type(n["node_slot"]) is not int for n in nodes):
        raise ValueError("DataPlane node slot must be an integer")
    if [n["key"] for n in nodes] != list(node_keys) or [n["node_slot"] for n in nodes] != list(range(len(nodes))):
        raise ValueError("DataPlane foundation node identities differ")
    if (len({n["node_address"] for n in nodes}) != len(nodes)
        or len({n["pin_component"] for n in nodes}) != len(nodes)
        or any(IPv4Address(n["node_address"]) not in network or IPv4Address(n["node_address"]) in (network.network_address, network.broadcast_address)
               or re.fullmatch(r"n-[a-z0-9-]{1,32}", n["pin_component"]) is None for n in nodes)):
        raise ValueError("DataPlane foundation address/pin identities differ")
    for node in nodes:
        if set(node) != {'key', 'node_slot', 'node_address', 'pin_component', 'program'}:
            raise ValueError('node program schema differs; regenerate execution materials')
        program = node['program']
        if program is not None and (type(program) is not int or not 0 <= program < len(value['programs'])):
            raise ValueError('invalid node program selection')
    return value


@dataclass(frozen=True, slots=True)
class _NodeVersion:
    node_id: object
    key: str
    version: int
    prefix: bytes
    active: dict
    forwarding: dict
    active_interfaces: tuple
    routes: tuple
    table_counts: dict


@dataclass(frozen=True, slots=True)
class _MaterializedBoundary:
    boundary: object
    generation: int
    expected_active_generation: int
    generation_changed: bool
    previous_resources: frozenset
    resources: frozenset
    new_resources: tuple
    removed_resources: tuple
    generation_nodes: tuple[_NodeVersion, ...]
    generation_by_node: dict
    request: dict


class _MaterialCursor:
    """Replay byte/table patches; retain only the last version of each node.

    This does no intention expansion. Unchanged versions preserve object identity,
    including across deliveries. The adapter separately decides kernel residency.
    """
    def __init__(self, nodes, foundation=None):
        self.nodes = {node.value: node for node in nodes}
        self.versions = {}
        self.resources = set()
        self.previous = None
        self.port_by_interface = None
        self.foundation = foundation
        self.programs = None if foundation is None else {n['key']: n['program'] for n in foundation['nodes']}

    def apply(self, boundary):
        value = boundary.executable
        _validate_executable(value, self.nodes)
        request = value["request"]
        generation = request["generation"]
        expected = 0xffffffff if self.previous is None else self.previous.generation
        changed = request["generation_changed"]
        if (type(changed) is not bool or request["expected_active_generation"] != expected
            or type(generation) is not int or not 0 <= generation < 0xffffffff
            or generation != (0 if self.previous is None else expected + int(changed))
            or request["boundary_timestamp"] != boundary.nominal_time.isoformat()):
            raise ValueError("prepared DataPlane generation continuity differs")
        if value["node_patches"] and not changed:
            raise ValueError("unchanged generation carries node patches")
        seen = set()
        for patch in value["node_patches"]:
            key = patch["key"]
            old = self.versions.get(key)
            if key not in self.nodes or key in seen or patch["base"] != (None if old is None else old.version) or patch["version"] != generation:
                raise ValueError("prepared node version/base differs")
            seen.add(key)
            active = {} if old is None else dict(old.active)
            forwarding = {} if old is None else dict(old.forwarding)
            for identity in patch["active_delete"]:
                if tuple(identity) not in active:
                    raise ValueError("prepared active entry deletion is absent")
                del active[tuple(identity)]
            for identity in patch["route_delete"]:
                if tuple(identity) not in forwarding:
                    raise ValueError("prepared route deletion is absent")
                del forwarding[tuple(identity)]
            def declaration(instance):
                if type(instance) is not int or instance < 0 or (self.programs is not None and instance != self.programs[key]):
                    raise ValueError("prepared entry references an uncovered instance")
                return None if self.foundation is None else self.foundation['programs'][instance]['declaration']
            for instance, name, data in patch["active_put"]:
                definition = declaration(instance)
                if definition is not None and not definition['managed_tables']:
                    raise ValueError('unmanaged object cannot receive planned data')
                if not isinstance(data, bytes) or len(data) != 8:
                    raise ValueError("prepared active entry encoding differs")
                if struct.unpack_from("<I", data, 4)[0] != 1 or (self.port_by_interface is not None and struct.unpack_from("<I", data)[0] != self.port_by_interface.get((key, name))):
                    raise ValueError("prepared active port binding differs")
                active[instance, name] = data
            for instance, address, interface, data in patch["route_put"]:
                definition = declaration(instance)
                size = None if definition is None else definition['maps'].get('routes', [0, 0, -1])[2]
                if not isinstance(data, bytes) or len(data) < 8 or (size is not None and len(data) != 4 + size) or data[:4] != IPv4Address(address).packed:
                    raise ValueError("prepared route entry encoding differs")
                if definition is not None and definition['managed_tables'] == 1 and interface is not None and self.port_by_interface is not None and struct.unpack_from("<I", data, 4)[0] != self.port_by_interface.get((key, interface)):
                    raise ValueError("prepared route port binding differs")
                forwarding[instance, address] = (interface, data)
            prefix = patch["prefix"]
            if not isinstance(prefix, bytes) or prefix != struct.pack("<I", generation):
                raise ValueError("prepared map version prefix differs")
            counts = {}
            for collection, index in ((active, 0), (forwarding, 1)):
                for instance, _ in collection:
                    counts.setdefault(instance, [0, 0])[index] += 1
            self.versions[key] = _NodeVersion(self.nodes[key], key, generation, prefix,
                active, forwarding, tuple(sorted(active)),
                tuple((address, entry[0]) for (instance, address), entry in sorted(forwarding.items())), counts)
        if self.versions.keys() != self.nodes.keys():
            raise ValueError("prepared generation omitted a node version")
        before = frozenset() if self.previous is None else self.previous.resources
        created, removed = tuple(value["created"]), tuple(value["removed"])
        if (len(set(created)) != len(created) or len(set(removed)) != len(removed)
            or any(c in self.resources for c in created) or any(c not in self.resources for c in removed)):
            raise ValueError("prepared connection delta differs from predecessor")
        self.resources.update(created)
        self.resources.difference_update(removed)
        nodes = (self.previous.generation_nodes if not changed else tuple(self.versions[key] for key in self.nodes))
        draft = _MaterializedBoundary(boundary=boundary, generation=generation,
            expected_active_generation=expected, generation_changed=changed,
            previous_resources=before, resources=frozenset(self.resources) if created or removed else before,
            new_resources=created, removed_resources=removed, generation_nodes=nodes,
            generation_by_node={n.node_id: n for n in nodes} if changed else self.previous.generation_by_node, request=deepcopy(request))
        self.previous = draft
        return draft


def _encode_foundation(foundation):
    """Compress the constant templates once per independent sealed delivery."""
    import base64
    import json
    import zlib
    return {"contract": _DATAPLANE_CONTRACT,
            "encoding": "zlib-json-base64",
            "content": base64.b64encode(zlib.compress(json.dumps(foundation, sort_keys=True, separators=(",", ":")).encode(), 9)).decode()}


def _decode_foundation(value, node_keys):
    import base64
    import json
    import zlib
    if (not isinstance(value, dict) or set(value) != {"contract", "encoding", "content"}
        or value["contract"] != _DATAPLANE_CONTRACT or value["encoding"] != "zlib-json-base64"):
        raise ValueError("unsupported DataPlane material contract; regenerate execution materials")
    try:
        decoder = zlib.decompressobj()
        raw = decoder.decompress(base64.b64decode(value["content"], validate=True))
        if not decoder.eof or decoder.unused_data:
            raise ValueError("DataPlane foundation stream is truncated or has trailing data")
        return _validate_foundation(json.loads(raw), node_keys)
    except (ValueError, TypeError, KeyError, zlib.error) as error:
        raise ValueError(f"invalid prepared DataPlane foundation: {error}") from error


_PIN_PARENT = Path("/sys/fs/bpf/skyseed-v2")
_RETIREMENT_GROUP_BASE = 0x53530000


def _pin_root(value: str) -> Path:
    path = Path(value)
    try:
        relative = path.relative_to(_PIN_PARENT)
    except ValueError as error:
        raise ValueError("pin root is outside the Runner-owned parent") from error
    if len(relative.parts) != 1 or not re.fullmatch(
        r"[a-z0-9][a-z0-9-]{5,63}", relative.name
    ):
        raise ValueError("pin root has an invalid deployment token")
    return path


def _validate_retirements(value, node_keys):
    if not isinstance(value, dict) or set(value) != {"bindings", "batches"}:
        raise ValueError("prepared retirement schema differs")
    if not isinstance(value["bindings"], dict) or not isinstance(value["batches"], dict):
        raise ValueError("prepared retirement bindings/batches must be objects")
    def identity(key, group):
        if key not in node_keys or type(group) is not int or not _RETIREMENT_GROUP_BASE < group <= 0xffffffff:
            raise ValueError("prepared retirement node/group is invalid")
    for reference, pair in value["bindings"].items():
        if not isinstance(reference, str) or not reference.isdecimal() or not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError("prepared retirement binding is invalid")
        identity(*pair)
    for timestamp, batches in value["batches"].items():
        from datetime import datetime
        if datetime.fromisoformat(timestamp).tzinfo is None or not isinstance(batches, list):
            raise ValueError("prepared retirement boundary is invalid")
        seen = set()
        for batch in batches:
            if not isinstance(batch, dict) or set(batch) != {"key", "group", "connections"}:
                raise ValueError("prepared retirement batch is invalid")
            identity(batch["key"], batch["group"])
            for connection in batch["connections"]:
                if (connection["connection"] in seen or batch["key"] not in (connection["node_a"], connection["node_b"])
                    or connection["node_a"] not in node_keys or connection["node_b"] not in node_keys):
                    raise ValueError("prepared retirement membership is invalid")
                seen.add(connection["connection"])
    return value


def _validate_executable(value, node_keys):
    if not isinstance(value, dict) or set(value) != {"request", "node_patches", "created", "removed"}:
        raise ValueError("prepared DataPlane executable schema differs")
    request = value["request"]
    if not isinstance(request, dict) or set(request) != {
        "common_mac", "boundary_timestamp", "expected_active_generation", "generation",
        "generation_changed", "operation_nodes", "connections", "last_completed_active_frontier",
    }:
        raise ValueError("prepared activation request schema differs")
    if request["common_mac"] != "02:53:4b:59:00:01":
        raise ValueError("prepared BPF link identity differs")
    if len(request["connections"]) != len(value["created"]):
        raise ValueError("prepared connection action count differs")
    for connection, action in zip(value["created"], request["connections"]):
        if (action["node_a"] != connection.endpoint_a.value or action["node_b"] != connection.endpoint_b.value
            or action["node_a"] not in node_keys or action["node_b"] not in node_keys
            or not isinstance(action.get("retirement_binding"), str)):
            raise ValueError("prepared connection action identity differs")
    operation_keys = set()
    for node in request["operation_nodes"]:
        if (not isinstance(node, dict) or set(node) != {"key", "qdisc", "zone_address_delete", "zone_address_add"}
            or node["key"] not in node_keys or node["key"] in operation_keys):
            raise ValueError("prepared node action schema differs")
        operation_keys.add(node["key"])
    for patch in value["node_patches"]:
        if not isinstance(patch, dict) or set(patch) != {"key", "base", "version", "prefix", "active_delete", "active_put", "route_delete", "route_put"}:
            raise ValueError("prepared node patch schema differs")


_MAX_BPF_MAP_ENTRIES = 0xFFFFFFFF
_CAPACITY_DEMAND_SCHEMA_VERSION = 2


def _capacity_int(value: object, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _CapacityAuditError(
            f"sealed runtime capacity demand {field_name} is invalid"
        )
    if value > _MAX_BPF_MAP_ENTRIES:
        raise _CapacityAuditError(
            f"sealed runtime capacity demand {field_name} exceeds u32"
        )
    return value


def _validate_runtime_capacity_demand(
    value: object,
    *,
    expected_node_keys: Sequence[str],
    expected_boundary_count: int,
) -> dict[str, Any]:
    """Validate and detach the strict artifact-v2 mechanical demand schema."""

    expected = {
        "schema_version",
        "boundary_count",
        "generation_count",
        "physical_connection_high_water",
        "interface_binding_high_water",
        "interface_identity_derivation",
        "collision_audit",
        "per_node",
        "terminal",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise _CapacityAuditError("sealed runtime capacity demand schema differs")
    if value.get("schema_version") != _CAPACITY_DEMAND_SCHEMA_VERSION:
        raise _CapacityAuditError(
            "sealed runtime capacity demand version is unsupported"
        )
    boundary_count = _capacity_int(
        value.get("boundary_count"), field_name="boundary_count"
    )
    generation_count = _capacity_int(
        value.get("generation_count"), field_name="generation_count"
    )
    if boundary_count != expected_boundary_count or generation_count <= 0:
        raise _CapacityAuditError(
            "sealed runtime capacity demand boundary/generation count differs"
        )
    physical_high_water = _capacity_int(
        value.get("physical_connection_high_water"),
        field_name="physical_connection_high_water",
    )
    interface_high_water = _capacity_int(
        value.get("interface_binding_high_water"),
        field_name="interface_binding_high_water",
    )
    if interface_high_water != physical_high_water * 2:
        raise _CapacityAuditError(
            "sealed runtime capacity interface high-water is inconsistent"
        )
    if value.get("interface_identity_derivation") != _INTERFACE_IDENTITY_DERIVATION:
        raise _CapacityAuditError(
            "sealed runtime capacity interface derivation is unsupported"
        )
    collision = value.get("collision_audit")
    if (
        not isinstance(collision, Mapping)
        or set(collision)
        != {"result", "audited_transition_count", "derivation"}
        or collision.get("result") != "passed"
        or collision.get("derivation") != _INTERFACE_IDENTITY_DERIVATION
        or _capacity_int(
            collision.get("audited_transition_count"),
            field_name="collision_audit.audited_transition_count",
        )
        != boundary_count
    ):
        raise _CapacityAuditError(
            "sealed runtime capacity collision evidence differs"
        )
    raw_nodes = value.get("per_node")
    if not isinstance(raw_nodes, list):
        raise _CapacityAuditError("sealed runtime capacity per_node is invalid")
    node_fields = {
        "key",
        "cumulative_active_link_entries",
        "cumulative_route_entries",
        "maximum_generation_active_link_entries",
        "maximum_generation_route_entries",
        "physical_interface_high_water",
        "terminal_active_link_entries",
        "terminal_route_entries",
        "tables",
    }
    nodes: list[dict[str, Any]] = []
    for raw_node in raw_nodes:
        if not isinstance(raw_node, Mapping) or set(raw_node) != node_fields:
            raise _CapacityAuditError(
                "sealed runtime capacity per-node schema differs"
            )
        key = raw_node.get("key")
        if not isinstance(key, str) or not key:
            raise _CapacityAuditError("sealed runtime capacity node key is invalid")
        node = {"key": key}
        for field_name in sorted(node_fields - {"key", "tables"}):
            node[field_name] = _capacity_int(
                raw_node.get(field_name), field_name=f"{key}.{field_name}"
            )
        if (
            node["maximum_generation_active_link_entries"]
            > node["cumulative_active_link_entries"]
            or node["maximum_generation_route_entries"]
            > node["cumulative_route_entries"]
            or node["terminal_active_link_entries"]
            > node["maximum_generation_active_link_entries"]
            or node["terminal_route_entries"]
            > node["maximum_generation_route_entries"]
        ):
            raise _CapacityAuditError(
                "sealed runtime capacity per-node bounds are inconsistent"
            )
        tables = raw_node['tables']
        table_fields = {'program', 'active', 'routes', 'terminal_active', 'terminal_routes'}
        if not isinstance(tables, list) or any(not isinstance(t, dict) or set(t) != table_fields for t in tables):
            raise _CapacityAuditError('invalid table capacity declarations')
        node['tables'] = [{k: _capacity_int(v, field_name=f'{key}.table.{k}') for k, v in t.items()} for t in tables]
        if len({t['program'] for t in tables}) != len(tables) or any(t['terminal_active'] > t['active'] or t['terminal_routes'] > t['routes'] for t in tables):
            raise _CapacityAuditError('inconsistent table capacities')
        if sum(t['active'] for t in tables) != node['cumulative_active_link_entries'] or sum(t['routes'] for t in tables) != node['cumulative_route_entries']:
            raise _CapacityAuditError('table capacities differ from node totals')
        nodes.append(node)
    actual_keys = [node["key"] for node in nodes]
    if actual_keys != sorted(expected_node_keys) or len(set(actual_keys)) != len(
        actual_keys
    ):
        raise _CapacityAuditError(
            "sealed runtime capacity node keys differ from PhysicalWorld"
        )
    terminal = value.get("terminal")
    if (
        not isinstance(terminal, Mapping)
        or set(terminal)
        != {"active_connection_count", "generation_active", "per_node_count"}
        or _capacity_int(
            terminal.get("active_connection_count"),
            field_name="terminal.active_connection_count",
        )
        > physical_high_water
        or terminal.get("generation_active") is not True
        or _capacity_int(
            terminal.get("per_node_count"), field_name="terminal.per_node_count"
        )
        != len(nodes)
    ):
        raise _CapacityAuditError("sealed runtime capacity terminal facts differ")
    return {
        "schema_version": _CAPACITY_DEMAND_SCHEMA_VERSION,
        "boundary_count": boundary_count,
        "generation_count": generation_count,
        "physical_connection_high_water": physical_high_water,
        "interface_binding_high_water": interface_high_water,
        "interface_identity_derivation": _INTERFACE_IDENTITY_DERIVATION,
        "collision_audit": dict(collision),
        "per_node": nodes,
        "terminal": dict(terminal),
    }


class _RuntimeCapacityDemandAccumulator:
    """Incrementally seal exact mechanical demand while boundaries are hot."""

    def __init__(self, physical_world_projection: object, zone_prefixes: Sequence[str]):
        self._nodes = tuple(
            sorted(getattr(physical_world_projection, "node_ids"), key=_node_key)
        )
        self._node_keys = tuple(node.value for node in self._nodes)
        self._projection = physical_world_projection
        self._cursor = _MaterialCursor(self._nodes)
        self._boundary_count = self._generation_count = self._total_generations = 0
        self._physical_high_water = 0
        self._interface_high_water = {key: 0 for key in self._node_keys}
        self._active_totals = {key: 0 for key in self._node_keys}
        self._route_totals = {key: 0 for key in self._node_keys}
        self._active_max = {key: 0 for key in self._node_keys}
        self._route_max = {key: 0 for key in self._node_keys}
        self._interface_names, self._live_names = {}, {}
        self._interface_counts = {node: 0 for node in self._nodes}
        self._tables = {key: {} for key in self._node_keys}

    def begin_batch(self):
        if not self._boundary_count:
            raise RuntimeError('cannot continue an empty capacity batch')
        self._boundary_count = 0
        self._generation_count = 1
        self._physical_high_water = len(self._cursor.previous.resources)
        for node in self._cursor.previous.generation_nodes:
            key = node.key
            self._interface_high_water[key] = self._interface_counts[node.node_id]
            self._active_totals[key] = self._active_max[key] = len(node.active)
            self._route_totals[key] = self._route_max[key] = len(node.forwarding)
            for table in self._tables[key].values():
                table['active'] = table['terminal_active']
                table['routes'] = table['terminal_routes']

    def add(self, boundary):
        if boundary.physical_world_projection != self._projection:
            raise _CapacityAuditError('capacity boundary PhysicalWorld differs')
        draft = self._cursor.apply(boundary)
        peak_counts = dict(self._interface_counts)
        for connection in draft.new_resources:
            for node, name in zip((connection.endpoint_a, connection.endpoint_b), _cached_interface_names(connection, self._interface_names)):
                prior = self._live_names.setdefault(name, connection)
                if prior != connection:
                    raise _CapacityAuditError('Runner interface-name capacity collision during preparation')
                peak_counts[node] += 1
                self._interface_counts[node] += 1
        self._physical_high_water = max(self._physical_high_water, len(draft.previous_resources) + len(draft.new_resources))
        for node, count in peak_counts.items():
            self._interface_high_water[node.value] = max(self._interface_high_water[node.value], count)
        for connection in draft.removed_resources:
            for node, name in zip((connection.endpoint_a, connection.endpoint_b), _cached_interface_names(connection, self._interface_names)):
                del self._live_names[name]
                self._interface_counts[node] -= 1
        if draft.generation_changed:
            self._generation_count += 1
            self._total_generations += 1
            for node in draft.generation_nodes:
                key = node.key
                active_count, route_count = len(node.active), len(node.forwarding)
                self._active_totals[key] += active_count
                self._route_totals[key] += route_count
                self._active_max[key] = max(self._active_max[key], active_count)
                self._route_max[key] = max(self._route_max[key], route_count)
                counts = node.table_counts
                for program in self._tables[key].keys() | counts.keys():
                    table = self._tables[key].setdefault(program, dict(program=program, active=0, routes=0, terminal_active=0, terminal_routes=0))
                    count = counts.get(program, (0, 0))
                    for index, field in enumerate(('active', 'routes')):
                        table[field] += count[index]
                        table['terminal_' + field] = count[index]
        self._boundary_count += 1

    def snapshot(self) -> dict[str, Any]:
        if self._boundary_count <= 0 or self._generation_count <= 0:
            raise _CapacityAuditError(
                "Runner artifact capacity demand requires a complete sequence"
            )
        value = {
            "schema_version": _CAPACITY_DEMAND_SCHEMA_VERSION,
            "boundary_count": self._boundary_count,
            "generation_count": self._generation_count,
            "physical_connection_high_water": self._physical_high_water,
            "interface_binding_high_water": self._physical_high_water * 2,
            "interface_identity_derivation": _INTERFACE_IDENTITY_DERIVATION,
            "collision_audit": {
                "result": "passed",
                "audited_transition_count": self._boundary_count,
                "derivation": _INTERFACE_IDENTITY_DERIVATION,
            },
            "per_node": [
                {
                    "key": node.value,
                    "cumulative_active_link_entries": self._active_totals[node.value],
                    "cumulative_route_entries": self._route_totals[node.value],
                    "maximum_generation_active_link_entries": self._active_max[
                        node.value
                    ],
                    "maximum_generation_route_entries": self._route_max[node.value],
                    "physical_interface_high_water": self._interface_high_water[
                        node.value
                    ],
                    "terminal_active_link_entries": len(self._cursor.versions[node.value].active),
                    "terminal_route_entries": len(self._cursor.versions[node.value].forwarding),
                    "tables": [dict(table) for table in self._tables[node.value].values()],
                }
                for node in self._nodes
            ],
            "terminal": {
                "active_connection_count": len(self._cursor.previous.resources),
                "generation_active": True,
                "per_node_count": len(self._nodes),
            },
        }
        return _validate_runtime_capacity_demand(
            value,
            expected_node_keys=self._node_keys,
            expected_boundary_count=self._boundary_count,
        )
