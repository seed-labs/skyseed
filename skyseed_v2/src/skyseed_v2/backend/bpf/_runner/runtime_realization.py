"""Runner-private precomputed tc-BPF runtime realization and state."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from hashlib import blake2s
from ipaddress import IPv4Address, IPv4Network, ip_interface
import json
from threading import RLock
from types import MappingProxyType
from time import monotonic_ns
from typing import Any, Mapping, Protocol, Sequence

from skyseed_v2.shared import NodeId
from skyseed_v2.simulator import (
    GroundStationRouteDemandTarget,
    OperationalConnectionId,
    OperationalConnectionKind,
    ZoneRouteDemandTarget,
)

from ..compiled_realization import (
    _ForwardingBinding,
    _ForwardingIntent,
    _MutationOperation,
    _QdiscIntent,
    _ResourceIntent,
    _ResourceOperation,
    _ZoneEdgeIntent,
    _ZoneEdgePhase,
    _ZoneEdgeState,
)
from .kernel_netlink import _RETIREMENT_GROUP_BASE

_NODE_ADDRESS_SPACE = IPv4Network("100.64.0.0/10")
_COMMON_MAC = "02:53:4b:59:00:01"
_NO_ACTIVE_GENERATION = 0xFFFFFFFF
_MAX_BPF_MAP_ENTRIES = 0xFFFFFFFF
_CAPACITY_DEMAND_SCHEMA_VERSION = 1
_INTERFACE_IDENTITY_DERIVATION = "blake2s-40-bit-connection-key-v1"


def _optional_duration_seconds(
    started_monotonic_ns: object,
    ended_monotonic_ns: object,
) -> float | None:
    """Return truthful timing evidence without making it semantic authority."""

    if (
        isinstance(started_monotonic_ns, bool)
        or not isinstance(started_monotonic_ns, int)
        or isinstance(ended_monotonic_ns, bool)
        or not isinstance(ended_monotonic_ns, int)
        or ended_monotonic_ns < started_monotonic_ns
    ):
        return None
    return (ended_monotonic_ns - started_monotonic_ns) / 1_000_000_000


class _CapacityAuditError(RuntimeError):
    """Report that exact simulation state cannot fit owned runtime capacity."""


class _IncompleteActivationError(RuntimeError):
    """Carry truthful evidence for a Main that did not reach formal ACTIVE."""

    def __init__(self, message: str, *, evidence: dict[str, Any]) -> None:
        super().__init__(message)
        self.evidence = evidence


class _PrivilegedCommandExecutor(Protocol):
    def execute(
        self, action: str, request: Mapping[str, Any] | str
    ) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class _QdiscValue:
    connection_id: OperationalConnectionId
    source: NodeId
    target: NodeId
    bandwidth_bps: int
    delay_us: int
    packet_loss_ppm: int


@dataclass(frozen=True, slots=True)
class _MaterializedDataplaneState:
    resources: tuple[OperationalConnectionId, ...]
    parameters: tuple[_QdiscValue, ...]
    forwarding: tuple[_ForwardingBinding, ...]
    zone_edges: tuple[_ZoneEdgeState, ...]


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class _PreparedBoundary:
    """A finite immutable Main delta; it owns no full network state."""

    nominal_time: object
    generation: int
    expected_active_generation: int
    generation_changed: bool
    request: str
    created: tuple[OperationalConnectionId, ...]
    removed: tuple[OperationalConnectionId, ...]
    withdrawn_identities: tuple[tuple[str, str], ...]
    telemetry_identities: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True, slots=True)
class _DerivedQdiscOperation:
    interface: str
    bandwidth_bps: int
    delay_us: int
    packet_loss_ppm: int


@dataclass(frozen=True, slots=True)
class _DerivedRouteOperation:
    destination: str
    interface: str


@dataclass(frozen=True, slots=True)
class _DerivedConnectionRequest:
    connection_id: OperationalConnectionId
    connection: str
    node_a: str
    node_b: str
    interface_a: str
    interface_b: str
    bandwidth_a_bps: int
    delay_a_us: int
    packet_loss_a_ppm: int
    bandwidth_b_bps: int
    delay_b_us: int
    packet_loss_b_ppm: int


@dataclass(frozen=True, slots=True)
class _DerivedGenerationNode:
    node_id: NodeId
    key: str
    active_interfaces: tuple[str, ...]
    routes: tuple[tuple[str, str | None], ...]


@dataclass(frozen=True, slots=True)
class _DerivedOperationNode:
    node_id: NodeId
    key: str
    qdisc: tuple[_DerivedQdiscOperation, ...]
    route_delete: tuple[_DerivedRouteOperation, ...]
    route_replace: tuple[_DerivedRouteOperation, ...]
    access_peer_route_delete: tuple[_DerivedRouteOperation, ...]
    access_peer_route_replace: tuple[_DerivedRouteOperation, ...]
    zone_address_delete: tuple[str, ...]
    zone_address_add: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _BoundaryBuild:
    """One-shot Preparation scratch state; never passed to formal Main."""

    boundary: _NominalBoundary
    previous_state: _MaterializedDataplaneState
    state: _MaterializedDataplaneState
    expected_active_generation: int
    generation: int
    generation_changed: bool = True
    new_resources: tuple[OperationalConnectionId, ...] = ()
    removed_resources: tuple[OperationalConnectionId, ...] = ()
    new_connection_requests: tuple[_DerivedConnectionRequest, ...] = ()
    generation_nodes: tuple[_DerivedGenerationNode, ...] = ()
    operation_nodes: tuple[_DerivedOperationNode, ...] = ()
    routes: Mapping[NodeId, dict[str, str | None]] = field(default_factory=dict)
    access_routes: Mapping[NodeId, dict[str, str]] = field(default_factory=dict)


def _node_key(node_id: NodeId) -> tuple[int, str, str]:
    return node_id.asn, node_id.entity_kind, node_id.entity_id


def _connection_key(connection_id: OperationalConnectionId) -> str:
    return "|".join(
        (
            connection_id.kind.value,
            connection_id.endpoint_a.value,
            connection_id.endpoint_b.value,
        )
    )


def _component(prefix: str, value: str, *, size: int = 5) -> str:
    digest = blake2s(value.encode("utf-8"), digest_size=size).hexdigest()
    return f"{prefix}{digest}"


def _interface_names(
    connection_id: OperationalConnectionId,
) -> tuple[str, str]:
    component = _component("", _connection_key(connection_id))
    return f"sv2{component}a", f"sv2{component}b"


def _cached_interface_names(connection_id, cache):
    if cache is None:
        return _interface_names(connection_id)
    if connection_id not in cache:
        cache[connection_id] = _interface_names(connection_id)
    return cache[connection_id]


def _interface_name_for_source(
    connection_id: OperationalConnectionId,
    source: NodeId,
    names=None,
) -> str:
    interface_a, interface_b = _cached_interface_names(connection_id, names)
    if source == connection_id.endpoint_a:
        return interface_a
    if source == connection_id.endpoint_b:
        return interface_b
    raise ValueError("compiled direction source is not a Connection endpoint")


def _ground_station_access_endpoints(
    connection_id: OperationalConnectionId,
) -> tuple[NodeId, NodeId]:
    if not isinstance(connection_id, OperationalConnectionId):
        raise TypeError(
            "access peer probe requires an OperationalConnectionId"
        )
    if (
        connection_id.kind
        is not OperationalConnectionKind.GROUND_STATION_ACCESS
    ):
        raise ValueError("access peer reachability requires ground-station access")
    endpoints = (connection_id.endpoint_a, connection_id.endpoint_b)
    ground_stations = tuple(
        value for value in endpoints if value.entity_kind == "ground-station"
    )
    satellites = tuple(
        value for value in endpoints if value.entity_kind == "satellite"
    )
    if len(ground_stations) != 1 or len(satellites) != 1:
        raise ValueError(
            "ground-station access resource must connect one GroundStation "
            "and one Satellite"
        )
    return ground_stations[0], satellites[0]


def _forwarding_key(binding: _ForwardingBinding) -> tuple[str, str, str]:
    target = binding.route_key.target
    if isinstance(target, GroundStationRouteDemandTarget):
        return (
            binding.route_key.source.value,
            "ground-station",
            target.ground_station_id.value,
        )
    if isinstance(target, ZoneRouteDemandTarget):
        return binding.route_key.source.value, "zone", target.zone_id
    raise TypeError("compiled forwarding has an unknown target")


def _empty_state() -> _MaterializedDataplaneState:
    return _MaterializedDataplaneState((), (), (), ())


def _generation_state(
    state: _MaterializedDataplaneState,
) -> tuple[
    tuple[OperationalConnectionId, ...],
    tuple[_ForwardingBinding, ...],
    tuple[_ZoneEdgeState, ...],
]:
    """Return exactly the state encoded under one BPF generation key."""

    return state.resources, state.forwarding, state.zone_edges


def _interfaces_for_state(
    state: _MaterializedDataplaneState,
    node_ids: Sequence[NodeId],
    names=None,
) -> dict[NodeId, tuple[str, ...]]:
    result: dict[NodeId, list[str]] = {node_id: [] for node_id in node_ids}
    for connection_id in state.resources:
        interface_a, interface_b = _cached_interface_names(connection_id, names)
        result[connection_id.endpoint_a].append(interface_a)
        result[connection_id.endpoint_b].append(interface_b)
    return {
        node_id: tuple(sorted(names)) for node_id, names in result.items()
    }


def _routes_for_state(
    state: _MaterializedDataplaneState,
    node_ids: Sequence[NodeId],
    node_addresses: Mapping[NodeId, str],
    names=None,
    packet_addresses=None,
    *, ordered=True,
) -> dict[NodeId, tuple[tuple[str, str | None], ...]]:
    def address(prefix):
        if packet_addresses is None:
            return str(ip_interface(prefix).ip)
        if prefix not in packet_addresses:
            packet_addresses[prefix] = str(ip_interface(prefix).ip)
        return packet_addresses[prefix]
    result: dict[NodeId, dict[str, str | None]] = {
        node_id: {node_addresses[node_id]: None} for node_id in node_ids
    }
    for edge in state.zone_edges:
        destination = address(edge.packet_prefix)
        prior = result[edge.service_satellite_id].setdefault(destination, None)
        if prior is not None:
            raise ValueError("compiled Zone address conflicts with forwarding")
    for binding in state.forwarding:
        source = binding.route_key.source
        next_hop = binding.next_hop
        if {source, next_hop} != {
            binding.connection_id.endpoint_a,
            binding.connection_id.endpoint_b,
        }:
            raise ValueError("compiled forwarding contradicts its Connection")
        target = binding.route_key.target
        if isinstance(target, GroundStationRouteDemandTarget):
            destination = node_addresses[target.ground_station_id]
        elif isinstance(target, ZoneRouteDemandTarget):
            if binding.packet_prefix is None:
                raise ValueError("compiled Zone forwarding lacks packet identity")
            destination = address(binding.packet_prefix)
        else:  # pragma: no cover - compiled target is closed
            raise TypeError("compiled forwarding has an unknown target")
        interface = _interface_name_for_source(binding.connection_id, source, names)
        prior = result[source].setdefault(destination, interface)
        if prior != interface:
            raise ValueError("compiled forwarding has conflicting destinations")
    if not ordered:
        return result
    return {
        node_id: tuple(sorted(routes.items()))
        for node_id, routes in result.items()
    }


def _assert_unique_resource_bindings(
    resources: set[OperationalConnectionId],
    cached_names=None,
) -> None:
    names: dict[str, OperationalConnectionId] = {}
    for connection_id in resources:
        for name in _cached_interface_names(connection_id, cached_names):
            prior = names.setdefault(name, connection_id)
            if prior != connection_id:
                raise _CapacityAuditError(
                    "Runner interface-name capacity collision during Preparation: "
                    f"{name} identifies both {_connection_key(prior)} and "
                    f"{_connection_key(connection_id)}"
                )


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
        for field_name in sorted(node_fields - {"key"}):
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
        self._node_addresses, _ = _allocate_node_addresses(
            self._nodes, tuple(zone_prefixes)
        )
        self._projection = physical_world_projection
        self._indexes = _BoundaryStateIndexes()
        self._boundary_count = 0
        self._generation_count = 0
        self._total_generations = 0
        self._physical_high_water = 0
        self._interface_high_water = {key: 0 for key in self._node_keys}
        self._active_totals = {key: 0 for key in self._node_keys}
        self._route_totals = {key: 0 for key in self._node_keys}
        self._active_max = {key: 0 for key in self._node_keys}
        self._route_max = {key: 0 for key in self._node_keys}
        self._interface_names = {}
        self._packet_addresses = {}
        self._live_names = {}
        self._destinations = {(node, self._node_addresses[node]): {None: 1} for node in self._nodes}
        self._forwarding_entries = {}
        self._zone_entries = {}
        self._interface_counts = {node: 0 for node in self._nodes}
        self._route_counts = {node: 1 for node in self._nodes}

    def begin_batch(self) -> None:
        """Carry the terminal state, counting its shared generation as resident."""
        if not self._boundary_count:
            raise RuntimeError("cannot continue an empty capacity batch")
        self._boundary_count = 0
        self._generation_count = 1
        self._physical_high_water = len(self._indexes.resources)
        for node in self._nodes:
            key = node.value
            self._interface_high_water[key] = self._interface_counts[node]
            self._active_totals[key] = self._active_max[key] = self._interface_counts[node]
            self._route_totals[key] = self._route_max[key] = self._route_counts[node]

    def add(self, boundary: object) -> None:
        if getattr(boundary, "physical_world_projection") != self._projection:
            raise _CapacityAuditError(
                "Runner artifact capacity boundary PhysicalWorld differs"
            )
        prior_resources, prior_forwarding, prior_zones = self._indexes.apply(boundary)
        resources = self._indexes.resources
        created = tuple(key for key, prior in prior_resources.items() if prior is None)
        removed = tuple(key for key, prior in prior_resources.items() if prior is not None)
        if prior_resources:
            # The add-before-withdraw peak includes both old and new endpoints.
            peak_counts = dict(self._interface_counts)
            for connection in created:
                for node, name in zip((connection.endpoint_a, connection.endpoint_b),
                                      _cached_interface_names(connection, self._interface_names)):
                    prior = self._live_names.setdefault(name, connection)
                    if prior != connection:
                        raise _CapacityAuditError("Runner interface-name capacity collision during Preparation")
                    peak_counts[node] += 1
                    self._interface_counts[node] += 1
            self._physical_high_water = max(self._physical_high_water, len(resources) + len(removed))
            for node, count in peak_counts.items():
                self._interface_high_water[node.value] = max(self._interface_high_water[node.value], count)
            for connection in removed:
                for node, name in zip((connection.endpoint_a, connection.endpoint_b),
                                      _cached_interface_names(connection, self._interface_names)):
                    del self._live_names[name]
                    self._interface_counts[node] -= 1
        touched_destinations = set()
        for changes, entries in ((prior_forwarding, self._forwarding_entries),
                                 (prior_zones, self._zone_entries)):
            for key in changes:
                entry = entries.pop(key, None)
                if entry is not None:
                    self._change_destination(entry, -1, touched_destinations)
        for key in prior_forwarding:
            binding = self._indexes.forwarding.get(key)
            if binding is not None:
                entry = self._forwarding_entry(binding)
                self._forwarding_entries[key] = entry
                self._change_destination(entry, 1, touched_destinations)
        for key in prior_zones:
            edge = self._indexes.zone_edges.get(key)
            if edge is not None:
                entry = (edge.service_satellite_id, self._address(edge.packet_prefix), None)
                self._zone_entries[key] = entry
                self._change_destination(entry, 1, touched_destinations)
        for key in touched_destinations:
            if len(self._destinations.get(key, ())) > 1:
                raise ValueError("compiled forwarding has conflicting destinations")
        if not self._generation_count or prior_resources or prior_forwarding or prior_zones:
            self._generation_count += 1
            self._total_generations += 1
            for node in self._nodes:
                key = node.value
                active_count, route_count = self._interface_counts[node], self._route_counts[node]
                self._active_totals[key] += active_count
                self._route_totals[key] += route_count
                self._active_max[key] = max(self._active_max[key], active_count)
                self._route_max[key] = max(self._route_max[key], route_count)
        self._boundary_count += 1

    def _address(self, prefix):
        if prefix not in self._packet_addresses:
            self._packet_addresses[prefix] = str(ip_interface(prefix).ip)
        return self._packet_addresses[prefix]

    def _forwarding_entry(self, binding):
        source, next_hop = binding.route_key.source, binding.next_hop
        connection = binding.connection_id
        if not ((source == connection.endpoint_a and next_hop == connection.endpoint_b)
                or (source == connection.endpoint_b and next_hop == connection.endpoint_a)):
            raise ValueError("compiled forwarding contradicts its Connection")
        target = binding.route_key.target
        if isinstance(target, GroundStationRouteDemandTarget):
            destination = self._node_addresses[target.ground_station_id]
        elif isinstance(target, ZoneRouteDemandTarget):
            if binding.packet_prefix is None:
                raise ValueError("compiled Zone forwarding lacks packet identity")
            destination = self._address(binding.packet_prefix)
        else:
            raise TypeError("compiled forwarding has an unknown target")
        return source, destination, _interface_name_for_source(connection, source, self._interface_names)

    def _change_destination(self, entry, delta, touched):
        node, destination, interface = entry
        key = node, destination
        touched.add(key)
        counts = self._destinations.get(key)
        if counts is None:
            counts = self._destinations[key] = {}
            self._route_counts[node] += 1
        count = counts.get(interface, 0) + delta
        if count:
            counts[interface] = count
        else:
            del counts[interface]
        if not counts:
            del self._destinations[key]
            self._route_counts[node] -= 1

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
                    "terminal_active_link_entries": self._interface_counts[node],
                    "terminal_route_entries": self._route_counts[node],
                }
                for node in self._nodes
            ],
            "terminal": {
                "active_connection_count": len(self._indexes.resources),
                "generation_active": True,
                "per_node_count": len(self._nodes),
            },
        }
        return _validate_runtime_capacity_demand(
            value,
            expected_node_keys=self._node_keys,
            expected_boundary_count=self._boundary_count,
        )


def _allocate_node_addresses(
    topology_nodes: tuple[NodeId, ...],
    zone_prefixes: tuple[str, ...],
) -> tuple[dict[NodeId, str], IPv4Network]:
    """Select the smallest deterministic private realization for this topology."""

    ordered_nodes = tuple(sorted(topology_nodes, key=_node_key))
    if not ordered_nodes:
        raise _CapacityAuditError("Runner node address allocation requires a node")
    if len(set(ordered_nodes)) != len(ordered_nodes):
        raise _CapacityAuditError("Runner topology contains duplicate node identities")
    required_address_count = len(ordered_nodes) + 2
    allocation_size = 1 << (required_address_count - 1).bit_length()
    if allocation_size > _NODE_ADDRESS_SPACE.num_addresses:
        raise _CapacityAuditError(
            "default Runner node address space cannot satisfy topology during Preparation"
        )
    prefix_length = 32 - (allocation_size.bit_length() - 1)
    selected_range = IPv4Network(
        (int(_NODE_ADDRESS_SPACE.network_address), prefix_length)
    )
    try:
        zone_networks = tuple(ip_interface(prefix).network for prefix in zone_prefixes)
    except ValueError as error:
        raise _CapacityAuditError(
            "compiled Zone packet prefix is invalid during Preparation"
        ) from error
    if any(not isinstance(network, IPv4Network) for network in zone_networks):
        raise _CapacityAuditError(
            "compiled Zone packet prefix is not IPv4 during Preparation"
        )
    if any(selected_range.overlaps(zone_network) for zone_network in zone_networks):
        raise _CapacityAuditError(
            "Runner node address range collides with compiled Zone identity during Preparation"
        )
    addresses = {
        node_id: str(IPv4Address(int(selected_range.network_address) + index))
        for index, node_id in enumerate(ordered_nodes, start=1)
    }
    if len(set(addresses.values())) != len(ordered_nodes):
        raise _CapacityAuditError("Runner node address allocation is not unique")
    return addresses, selected_range


def _validate_boundary_lowering(boundary: _NominalBoundary) -> int:
    """Prove explicit edges survive the private phased Main lowering."""

    operations = {value.reference: value.intent for value in boundary.operations}

    def phase(intent: object) -> int:
        if isinstance(intent, _ResourceIntent):
            return (
                0
                if intent.operation is _ResourceOperation.ESTABLISH
                else 5
            )
        if isinstance(intent, _QdiscIntent):
            return 1
        if (
            isinstance(intent, _ZoneEdgeIntent)
            and intent.phase is _ZoneEdgePhase.READINESS
        ):
            return 2
        if isinstance(intent, _ForwardingIntent):
            return 3
        if isinstance(intent, _ZoneEdgeIntent):
            return 4
        raise TypeError("unknown boundary operation")

    folded_forwarding_edges = 0
    for predecessor, successor, _ in boundary.constraint_edges:
        predecessor_intent = operations[predecessor]
        successor_intent = operations[successor]
        predecessor_phase = phase(predecessor_intent)
        successor_phase = phase(successor_intent)
        if predecessor_phase > successor_phase:
            raise ValueError("compiled constraint is incompatible with Main phases")
        if predecessor_phase == successor_phase:
            if not (
                isinstance(predecessor_intent, _ForwardingIntent)
                and isinstance(successor_intent, _ForwardingIntent)
            ):
                raise ValueError("Main batch would erase a non-forwarding constraint")
            folded_forwarding_edges += 1
    return folded_forwarding_edges


class _BoundaryStateIndexes:
    """Own mutable derived indexes for one ordered finite materialization pass."""

    def __init__(self, previous=None):
        previous = _empty_state() if previous is None else previous
        self._snapshot = previous
        self._dirty = set()
        self.resources = {value: value for value in previous.resources}
        self.parameters = {
            (value.connection_id, value.source, value.target): value
            for value in previous.parameters
        }
        self.forwarding = {v.route_key: v for v in previous.forwarding}
        self.zone_edges = {v.zone_id: v for v in previous.zone_edges}
        self.forwarding_by_connection = {}
        for binding in previous.forwarding:
            self.forwarding_by_connection.setdefault(
                binding.connection_id, set()
            ).add(binding.route_key)

    def apply(self, boundary):
        resources, parameters = self.resources, self.parameters
        forwarding, zone_edges = self.forwarding, self.zone_edges
        forwarding_by_connection = self.forwarding_by_connection
        prior_resources, prior_forwarding, prior_zones = {}, {}, {}
        touched_connections = set()
        for operation in boundary.operations:
            intent = operation.intent
            if isinstance(intent, _ResourceIntent):
                prior_resources.setdefault(
                    intent.connection_id, resources.get(intent.connection_id)
                )
            elif isinstance(intent, _ForwardingIntent):
                prior_forwarding.setdefault(
                    intent.route_key, forwarding.get(intent.route_key)
                )
            elif isinstance(intent, _ZoneEdgeIntent):
                prior_zones.setdefault(intent.zone_id, zone_edges.get(intent.zone_id))
            if isinstance(intent, _ResourceIntent):
                self._dirty.add("parameters")
                touched_connections.add(intent.connection_id)
                if intent.operation is _ResourceOperation.ESTABLISH:
                    if intent.connection_id in resources:
                        raise ValueError("compiled boundary establishes an active resource")
                    resources[intent.connection_id] = intent.connection_id
                else:
                    if intent.connection_id not in resources:
                        raise ValueError("compiled boundary withdraws an inactive resource")
                    del resources[intent.connection_id]
                    connection = intent.connection_id
                    parameters.pop((connection, connection.endpoint_a, connection.endpoint_b), None)
                    parameters.pop((connection, connection.endpoint_b, connection.endpoint_a), None)
            elif isinstance(intent, _QdiscIntent):
                self._dirty.add("parameters")
                touched_connections.add(intent.connection_id)
                if intent.connection_id not in resources:
                    raise ValueError("compiled qdisc uses an inactive resource")
                connection = intent.connection_id
                if (intent.source, intent.target) not in (
                    (connection.endpoint_a, connection.endpoint_b),
                    (connection.endpoint_b, connection.endpoint_a),
                ):
                    raise ValueError(
                        "materialized qdisc state does not cover active resources"
                    )
                key = intent.connection_id, intent.source, intent.target
                prior = parameters.get(key)
                bandwidth = (
                    intent.bandwidth_bps
                    if intent.bandwidth_bps is not None
                    else None if prior is None else prior.bandwidth_bps
                )
                delay = (
                    intent.delay_us
                    if intent.delay_us is not None
                    else None if prior is None else prior.delay_us
                )
                loss = (
                    intent.packet_loss_ppm
                    if intent.packet_loss_ppm is not None
                    else None if prior is None else prior.packet_loss_ppm
                )
                if bandwidth is None or delay is None or loss is None:
                    raise ValueError("compiled qdisc transition lacks a complete baseline")
                parameters[key] = _QdiscValue(
                    intent.connection_id,
                    intent.source,
                    intent.target,
                    bandwidth,
                    delay,
                    loss,
                )
            elif isinstance(intent, _ForwardingIntent):
                prior = forwarding.get(intent.route_key)
                if prior is not None:
                    forwarding_by_connection[prior.connection_id].remove(intent.route_key)
                if prior != intent.previous:
                    raise ValueError("compiled forwarding predecessor is stale")
                if intent.operation is _MutationOperation.REMOVE:
                    if intent.current is not None or prior is None:
                        raise ValueError("compiled forwarding removal is inconsistent")
                    del forwarding[intent.route_key]
                else:
                    if intent.current is None:
                        raise ValueError("compiled forwarding update has no current binding")
                    touched_connections.add(intent.current.connection_id)
                    forwarding[intent.route_key] = intent.current
                    forwarding_by_connection.setdefault(
                        intent.current.connection_id, set()
                    ).add(intent.route_key)
            elif isinstance(intent, _ZoneEdgeIntent):
                prior = zone_edges.get(intent.zone_id)
                prior_service = None if prior is None else prior.service_satellite_id
                if intent.phase is _ZoneEdgePhase.READINESS:
                    if (
                        prior_service != intent.previous_service_satellite_id
                        or intent.service_satellite_id is None
                    ):
                        raise ValueError("compiled Zone readiness is inconsistent")
                    zone_edges[intent.zone_id] = _ZoneEdgeState(
                        intent.zone_id,
                        intent.packet_prefix,
                        intent.service_satellite_id,
                    )
                else:
                    if intent.previous_service_satellite_id is None:
                        raise ValueError("compiled Zone retirement has no predecessor")
                    if intent.service_satellite_id is None:
                        if prior_service != intent.previous_service_satellite_id:
                            raise ValueError("compiled Zone retirement is stale")
                        del zone_edges[intent.zone_id]
                    elif prior_service != intent.service_satellite_id:
                        raise ValueError(
                            "compiled Zone replacement retirement is stale"
                        )
            else:  # pragma: no cover - boundary inventory is closed
                raise TypeError("unknown compiled boundary operation")
        for connection in touched_connections:
            if connection in resources:
                for source, target in (
                    (connection.endpoint_a, connection.endpoint_b),
                    (connection.endpoint_b, connection.endpoint_a),
                ):
                    if (connection, source, target) not in parameters:
                        raise ValueError("materialized qdisc state does not cover active resources")
            elif forwarding_by_connection.get(connection):
                raise ValueError("materialized forwarding uses an inactive resource")
        for key in prior_forwarding:
            binding = forwarding.get(key)
            if binding is not None and (binding.connection_id, key.source, binding.next_hop) not in parameters:
                raise ValueError("materialized forwarding lacks qdisc state")
        changed = (
            {key: value for key, value in prior_resources.items() if value != resources.get(key)},
            {key: value for key, value in prior_forwarding.items() if value != forwarding.get(key)},
            {key: value for key, value in prior_zones.items() if value != zone_edges.get(key)},
        )
        self._dirty.update(
            name for name, changes in zip(
                ("resources", "forwarding", "zone_edges"), changed
            ) if changes
        )
        return changed

    def snapshot(self):
        prior = self._snapshot
        self._snapshot = _MaterializedDataplaneState(
            tuple(sorted(self.resources, key=_connection_key))
            if "resources" in self._dirty else prior.resources,
            tuple(sorted(self.parameters.values(), key=lambda value: (
                _connection_key(value.connection_id),
                _node_key(value.source), _node_key(value.target),
            )))
            if "parameters" in self._dirty else prior.parameters,
            tuple(sorted(self.forwarding.values(), key=_forwarding_key))
            if "forwarding" in self._dirty else prior.forwarding,
            tuple(self.zone_edges[key] for key in sorted(self.zone_edges))
            if "zone_edges" in self._dirty else prior.zone_edges,
        )
        self._dirty.clear()
        return self._snapshot


def _materialize_boundary(previous, boundary):
    """Mechanically apply one ordered transition and freeze the complete state."""
    indexes = _BoundaryStateIndexes(previous)
    indexes.apply(boundary)
    return indexes.snapshot()


def _batch_entry_limits(demand, node_by_key, *, factor, continued):
    fields = ("cumulative_active_link_entries", "cumulative_route_entries", "physical_interface_high_water")
    peaks = {}
    if continued:
        # A fixed node can acquire its peers' forwarding work in a later batch.
        # Reserve from first-batch peers of the same typed entity kind, never
        # from the future session. Keep the exact sealed demand unchanged.
        for node in demand["per_node"]:
            group = peaks.setdefault(node_by_key[node["key"]].entity_kind, dict.fromkeys(fields, 1))
            for field in fields:
                group[field] = max(group[field], node[field])
    return {
        node["key"]: {
            field: factor * max(1, (peaks[node_by_key[node["key"]].entity_kind] if continued else node)[field])
            for field in fields
        }
        for node in demand["per_node"]
    }


class _TcBpfRuntimeRealization:
    """Own one-shot finite preparation and delta-only live execution."""

    def __init__(
        self,
        *,
        executor: _PrivilegedCommandExecutor,
        pin_root: str,
        bindings: Mapping[NodeId, object],
        telemetry_enabled: bool = False,
        worker_count: int = 1,
        continuation_enabled: bool = False,
        batch_capacity_factor: int = 2,
    ) -> None:
        self._executor = executor
        self._pin_root = pin_root
        self._bindings = dict(bindings)
        self._telemetry_enabled = telemetry_enabled
        self._worker_count = worker_count
        self._continuation_enabled = continuation_enabled
        self._batch_capacity_factor = batch_capacity_factor if continuation_enabled else 1
        self._previous_build = None
        self._state_indexes = _BoundaryStateIndexes()
        self._node_versions = {}
        self._batch_prewrite_work = {}
        self._generation_reservations = {}
        self._resident_active_totals = {}
        self._resident_route_totals = {}
        self._telemetry_kernel: dict[str, Any] = {}
        self._lock = RLock()
        self._nodes: tuple[dict[str, Any], ...] = ()
        self._node_addresses: dict[NodeId, str] = {}
        self._node_address_range: IPv4Network | None = None
        self._owned_node_addresses: set[NodeId] = set()
        self._all_zone_addresses: dict[NodeId, set[str]] = {}
        self._prepared_resources: set[OperationalConnectionId] = set()
        self._all_prepared_resources: set[OperationalConnectionId] = set()
        self._interface_indexes: dict[tuple[str, str], int] = {}
        self._resident_generations: set[int] = set()
        self._max_resident_generations = 0
        self._map_capacities: dict[str, tuple[int, int]] = {}
        self._capacity_plan: dict[str, Any] = {}
        self._capacity_demand: dict[str, Any] = {}
        self._capacity_demand_by_key: dict[str, dict[str, Any]] = {}
        self._observed_boundary_count = 0
        self._observed_generation_count = 0
        self._observed_active_entry_totals: dict[str, int] = {}
        self._observed_route_entry_totals: dict[str, int] = {}
        self._physical_resource_high_water = 0
        self._bpf_batch_update_supported = False
        self._active: _PreparedBoundary | None = None
        self._initialized = False
        self._cleanup_required = False
        self._initialization_outcome = "no-mutation"
        self._plans_prepared = False
        self._logical_ports: dict[OperationalConnectionId, tuple[int, int]] = {}
        self._port_by_interface: dict[tuple[str, str], int] = {}
        self._preparation_timings: dict[str, Any] = {}
        self._node_ids = tuple(sorted(self._bindings, key=_node_key))
        self._node_by_key = {node.value: node for node in self._node_ids}
        self._node_slots = {node.value: slot for slot, node in enumerate(self._node_ids)}
        self._connection_names = {}
        self._packet_addresses = {}

    def initialize(
        self,
        physical_world_projection: object,
        *,
        zone_prefixes: tuple[str, ...],
        runtime_capacity_demand: Mapping[str, Any],
    ) -> dict[str, Any]:
        if self._initialized or self._cleanup_required:
            raise RuntimeError("tc-BPF runtime realization is already initialized")
        parent_before_capacity_started_ns = monotonic_ns()
        if set(getattr(physical_world_projection, "node_ids")) != self._bindings.keys():
            raise ValueError("runtime bindings do not match compiled PhysicalWorld")
        physical_world_nodes = self._node_ids
        self._node_addresses, self._node_address_range = (
            _allocate_node_addresses(
                physical_world_nodes, tuple(zone_prefixes)
            )
        )
        components = {
            node_id: _component("n-", node_id.value)
            for node_id in physical_world_nodes
        }
        if len(set(components.values())) != len(components):
            raise ValueError("default Runner node pin hash collision")
        self._nodes = tuple(
            {
                "key": node_id.value,
                "node_slot": node_slot,
                "pid": int(getattr(self._bindings[node_id], "pid")),
                "process_start_time_ticks": int(
                    getattr(self._bindings[node_id], "process_start_time_ticks")
                ),
                "netns_inode": int(getattr(self._bindings[node_id], "netns_inode")),
                "pin_component": components[node_id],
                "node_address": self._node_addresses[node_id],
            }
            for node_slot, node_id in enumerate(physical_world_nodes)
        )
        self._all_zone_addresses = {
            node_id: set() for node_id in physical_world_nodes
        }
        capacity_plan_started_ns = monotonic_ns()
        sealed_demand = _validate_runtime_capacity_demand(
            runtime_capacity_demand,
            expected_node_keys=tuple(node["key"] for node in self._nodes),
            expected_boundary_count=int(runtime_capacity_demand.get(
                "boundary_count", -1
            )),
        )
        entry_limits = _batch_entry_limits(
            sealed_demand, self._node_by_key,
            factor=self._batch_capacity_factor, continued=self._continuation_enabled,
        )
        self._batch_admission_limits = entry_limits
        per_node = []
        for demand in sealed_demand["per_node"]:
            required_active = int(demand["cumulative_active_link_entries"])
            required_routes = int(demand["cumulative_route_entries"])
            limits = entry_limits[demand["key"]]
            per_node.append(
                {
                    "key": demand["key"],
                    "capacity_basis": "first-batch-entity-kind-peak" if self._continuation_enabled else "sealed-artifact-exact-demand",
                    "batch_admission_limits": limits,
                    "per_generation_active_link_upper_bound": demand[
                        "maximum_generation_active_link_entries"
                    ],
                    "per_generation_route_upper_bound": demand[
                        "maximum_generation_route_entries"
                    ],
                    "required_active_link_entries": required_active,
                    "configured_active_link_entries": limits["cumulative_active_link_entries"] * (2 if self._continuation_enabled else 1),
                    "required_route_entries": required_routes,
                    "configured_route_entries": limits["cumulative_route_entries"] * (2 if self._continuation_enabled else 1),
                    "physical_interface_high_water": demand[
                        "physical_interface_high_water"
                    ],
                    "configured_binding_entries": limits["physical_interface_high_water"],
                    "terminal_active_link_entries": demand[
                        "terminal_active_link_entries"
                    ],
                    "terminal_route_entries": demand[
                        "terminal_route_entries"
                    ],
                }
            )
        capacity_plan = {
            "mode": "fixed-resident-batch-reservation" if self._continuation_enabled else "sealed-artifact-exact-demand",
            "batch_capacity_factor": self._batch_capacity_factor,
            "resident_boundary_capacity": sealed_demand["boundary_count"] * self._batch_capacity_factor * (2 if self._continuation_enabled else 1),
            "known_boundary_count": sealed_demand["boundary_count"],
            "known_generation_count": sealed_demand["generation_count"],
            "resident_generation_capacity": (sealed_demand["generation_count"] * self._batch_capacity_factor * (2 if self._continuation_enabled else 1)),
            "per_node": per_node,
            "minimum_active_link_headroom": min(
                value["configured_active_link_entries"]
                - value["required_active_link_entries"]
                for value in per_node
            ),
            "minimum_route_headroom": min(
                value["configured_route_entries"]
                - value["required_route_entries"]
                for value in per_node
            ),
            "known_physical_resource_high_water": sealed_demand[
                "physical_connection_high_water"
            ],
            "known_interface_binding_high_water": sealed_demand[
                "interface_binding_high_water"
            ],
            "interface_identity_derivation": sealed_demand[
                "interface_identity_derivation"
            ],
            "sealed_collision_audit": sealed_demand["collision_audit"],
            "terminal": sealed_demand["terminal"],
            "node_address_requirement": len(self._nodes),
            "node_address_capacity": (
                0
                if self._node_address_range is None
                else self._node_address_range.num_addresses - 2
            ),
            "node_address_headroom": (
                0
                if self._node_address_range is None
                else self._node_address_range.num_addresses - 2 - len(self._nodes)
            ),
            "selected_node_address_range": (
                None
                if self._node_address_range is None
                else str(self._node_address_range)
            ),
            "node_address_selection": "smallest-prefix-for-topology-requirement",
            "per_node_interface_high_water": [
                {
                    "key": value["key"],
                    "interfaces": value["physical_interface_high_water"],
                }
                for value in per_node
            ],
            "pinned_object_count": 1 + 6 * len(self._nodes) + int(self._continuation_enabled),
        }
        self._max_resident_generations = int(
            capacity_plan["resident_generation_capacity"]
        )
        capacity_by_key = {
            value["key"]: value for value in capacity_plan["per_node"]
        }
        self._map_capacities = {
            key: (
                int(value["configured_active_link_entries"]),
                int(value["configured_route_entries"]),
            )
            for key, value in capacity_by_key.items()
        }
        self._capacity_plan = capacity_plan
        self._capacity_demand = sealed_demand
        self._batch_capacity_basis = sealed_demand
        self._capacity_demand_by_key = {
            node["key"]: node for node in sealed_demand["per_node"]
        }
        self._observed_active_entry_totals = {
            node["key"]: 0 for node in sealed_demand["per_node"]
        }
        self._observed_route_entry_totals = {
            node["key"]: 0 for node in sealed_demand["per_node"]
        }
        self._resident_active_totals = dict(self._observed_active_entry_totals)
        self._resident_route_totals = dict(self._observed_route_entry_totals)
        self._nodes = tuple(
            {
                **node,
                "active_links_capacity": self._map_capacities[node["key"]][0],
                "routes_capacity": self._map_capacities[node["key"]][1],
                "bindings_capacity": capacity_by_key[node["key"]]["configured_binding_entries"],
            }
            for node in self._nodes
        )
        capacity_plan_ended_ns = monotonic_ns()
        privileged_foundation_started_ns = monotonic_ns()
        # Dispatch can mutate the remote foundation even if its response is lost.
        self._cleanup_required = True
        self._initialization_outcome = "uncertain"
        try:
            result = self._executor.execute(
                "initialize",
                {
                    "pin_root": self._pin_root,
                    "nodes": self._nodes,
                    "retirement_group_end": _RETIREMENT_GROUP_BASE + (
                        2 * len(self._nodes) ** 2 + 1 if self._continuation_enabled
                        else sealed_demand["boundary_count"] + 1),
                    "resident_generation_capacity": self._max_resident_generations,
                    "continuation_enabled": self._continuation_enabled,
                    "inactive_generation": _NO_ACTIVE_GENERATION,
                    "telemetry_enabled": self._telemetry_enabled,
                    "node_address_range": str(self._node_address_range),
                    "zone_prefixes": tuple(zone_prefixes),
                    "worker_count": self._worker_count,
                },
            )
        except Exception as error:
            evidence = getattr(error, "remote_evidence", getattr(error, "evidence", None))
            if isinstance(evidence, Mapping):
                outcome = evidence.get("initialization_outcome")
                if outcome in {"no-mutation", "cleaned", "partial"}:
                    self._initialization_outcome = outcome
                    self._cleanup_required = outcome == "partial"
                installed = set(evidence.get("installed_node_addresses", ()))
                self._owned_node_addresses = {
                    node for node in physical_world_nodes if node.value in installed
                }
            raise _CapacityAuditError(
                "Runner node address collision audit or initialization failed during "
                f"Preparation: {error}"
            ) from error
        finally:
            privileged_foundation_ended_ns = monotonic_ns()
        parent_after_privileged_started_ns = monotonic_ns()
        collision_audit = result.get("collision_audit")
        installed_keys = result.get("installed_node_addresses")
        telemetry_kernel = result.get("traffic_telemetry")
        batch_update_supported = result.get("bpf_batch_update_supported")
        expected_keys = {node["key"] for node in self._nodes}
        try:
            self._validate_collision_audit(collision_audit)
            if not isinstance(installed_keys, list) or set(installed_keys) != expected_keys:
                raise RuntimeError(
                    "privileged runtime command node address ownership evidence is incomplete"
                )
            if not isinstance(batch_update_supported, bool):
                raise RuntimeError(
                    "privileged runtime command omitted BPF batch capability evidence"
                )
            if self._telemetry_enabled and (
                not isinstance(telemetry_kernel, dict)
                or telemetry_kernel.get("enabled") is not True
                or telemetry_kernel.get("shared_ring_buffer") is not True
                or not isinstance(telemetry_kernel.get("event_schema"), dict)
            ):
                raise RuntimeError(
                    "privileged runtime command omitted traffic telemetry foundation"
                )
        except BaseException:
            self.cleanup()
            raise
        self._owned_node_addresses = set(physical_world_nodes)
        self._bpf_batch_update_supported = batch_update_supported
        if self._telemetry_enabled:
            assert isinstance(telemetry_kernel, dict)
            self._telemetry_kernel = dict(telemetry_kernel)
        capacity_plan = {
            **capacity_plan,
            "collision_audit": collision_audit,
        }
        self._capacity_plan = capacity_plan
        self._initialized = True
        self._initialization_outcome = "initialized"
        parent_after_privileged_ended_ns = monotonic_ns()
        parent_before_capacity_seconds = _optional_duration_seconds(
            parent_before_capacity_started_ns,
            capacity_plan_started_ns,
        )
        parent_after_privileged_seconds = _optional_duration_seconds(
            parent_after_privileged_started_ns,
            parent_after_privileged_ended_ns,
        )
        capacity_to_privileged_seconds = _optional_duration_seconds(
            capacity_plan_ended_ns,
            privileged_foundation_started_ns,
        )
        privileged_to_parent_after_seconds = _optional_duration_seconds(
            privileged_foundation_ended_ns,
            parent_after_privileged_started_ns,
        )
        remaining_parent_parts = (
            parent_before_capacity_seconds,
            capacity_to_privileged_seconds,
            privileged_to_parent_after_seconds,
            parent_after_privileged_seconds,
        )
        remaining_parent_seconds = (
            None
            if any(value is None for value in remaining_parent_parts)
            else sum(value for value in remaining_parent_parts if value is not None)
        )
        child_phases = result.get("preparation_phases")
        preparation_phases = (
            dict(child_phases) if isinstance(child_phases, Mapping) else {}
        )
        preparation_phases.update(
            {
                "sealed_capacity_demand_validation_seconds": (
                    _optional_duration_seconds(
                        capacity_plan_started_ns,
                        capacity_plan_ended_ns,
                    )
                ),
                "full_range_capacity_plan_materialization_seconds": None,
                "privileged_foundation_initialization_seconds": (
                    _optional_duration_seconds(
                        privileged_foundation_started_ns,
                        privileged_foundation_ended_ns,
                    )
                ),
                "remaining_parent_bookkeeping_validation_seconds": (
                    remaining_parent_seconds
                ),
            }
        )
        return {
            **result,
            "preparation_phases": preparation_phases,
            "capacity_audit": capacity_plan,
        }

    def prepare_all(
        self, boundaries: Sequence[object], *,
        logical_ports: Mapping[OperationalConnectionId, tuple[int, int]],
        runtime_capacity_demand: Mapping[str, Any] | None = None,
        work_checkpoint=None,
    ) -> tuple[_PreparedBoundary, ...]:
        """Validate/materialize the complete finite sequence and prewrite semantics."""
        if not self._initialized or (self._plans_prepared and not self._continuation_enabled):
            raise RuntimeError("finite runtime Preparation may run only once")
        if runtime_capacity_demand is not None:
            self._admit_batch(runtime_capacity_demand)
        checkpoint = work_checkpoint or (lambda: None)
        construction_started = monotonic_ns()
        construction_ns = prewrite_ns = prewrite_count = 0
        prewrite_helper_seconds = 0.0
        prewrite_helper_count = 0
        self._plans_prepared = True
        self._batch_prewrite_work = {"node_versions_written": 0, "node_versions_reused": 0,
                                     "active_entries_written": 0, "route_entries_written": 0,
                                     "prewrite_work_units": 0}
        if any(logical_ports.get(connection) != ports for connection, ports in self._logical_ports.items()):
            raise ValueError("continued batch changed a logical port identity")
        self._logical_ports = dict(logical_ports)
        for connection, ports in logical_ports.items():
            for endpoint, name, port in zip(
                (connection.endpoint_a, connection.endpoint_b),
                _cached_interface_names(connection, self._connection_names), ports,
            ):
                self._port_by_interface[(endpoint.value, name)] = port
        previous = self._previous_build
        plans = []
        requests = []
        seen_connections = set()
        state_indexes = self._state_indexes
        for boundary in boundaries:
            checkpoint()
            draft = self._derive_boundary_plan(
                boundary, predecessor=previous, state_indexes=state_indexes,
                previous_state=_empty_state() if previous is None else previous.state,
            )
            self._audit_boundary_capacity(draft)
            seen_connections.update(draft.new_resources)
            if not seen_connections <= logical_ports.keys():
                raise ValueError("Runner artifact omitted a logical port identity")
            if draft.generation_changed:
                prewrite_started = monotonic_ns()
                construction_ns += prewrite_started - construction_started
                result = self._prewrite_nodes(draft, checkpoint)
                construction_started = monotonic_ns()
                prewrite_ns += construction_started - prewrite_started
                prewrite_count += 1
                prewrite_helper_seconds += result["helper_seconds"]
                prewrite_helper_count += int(result["helper_complete"])
                self._resident_generations.add(draft.generation)
                reservation = tuple((len(node.active_interfaces), len(node.routes)) for node in draft.generation_nodes)
                self._generation_reservations[draft.generation] = reservation
                for node, (active, routes) in zip(self._nodes, reservation):
                    self._resident_active_totals[node["key"]] += active
                    self._resident_route_totals[node["key"]] += routes
            self._commit_observed_capacity(draft)
            request = self._activation_request(
                draft, expected_active_generation=draft.expected_active_generation,
                generation=draft.generation, generation_changed=draft.generation_changed,
            )
            request["connections"] = [
                self._derived_connection_request(value) for value in draft.new_connection_requests
            ]
            request["removed_connections"] = [
                self._connection_identity(value) for value in draft.removed_resources
            ]
            request["last_completed_active_frontier"] = (
                None if previous is None else previous.boundary.nominal_time.isoformat()
            )
            for connection in (*request["connections"], *request["removed_connections"]):
                connection["logical_port_a"] = self._port_by_interface[(connection["node_a"], connection["interface_a"])]
                connection["logical_port_b"] = self._port_by_interface[(connection["node_b"], connection["interface_b"])]
            for edge in draft.state.zone_edges:
                self._all_zone_addresses[edge.service_satellite_id].add(str(ip_interface(edge.packet_prefix).ip))
            telemetry = []
            if self._telemetry_enabled and draft.generation_changed:
                routes_by_interface = {}
                for node in draft.generation_nodes:
                    for destination, interface in node.routes:
                        routes_by_interface.setdefault((node.key, interface), []).append(destination)
                for connection in draft.state.resources:
                    for endpoint, peer, name, port in zip(
                        (connection.endpoint_a, connection.endpoint_b),
                        (connection.endpoint_b, connection.endpoint_a),
                        _cached_interface_names(connection, self._connection_names), logical_ports[connection],
                    ):
                        telemetry.append(_freeze({
                            "generation": draft.generation,
                            "boundary_timestamp": boundary.nominal_time.isoformat(),
                            "node_slot": self._node_slots[endpoint.value], "node_id": endpoint.value,
                            "logical_port_id": port, "interface": name,
                            "connection_id": _connection_key(connection), "peer_node_id": peer.value,
                            "route_destinations": tuple(routes_by_interface.get((endpoint.value, name), ())),
                            "validity": "exact-generation",
                        }))
            requests.append(request)
            plans.append(_PreparedBoundary(
                boundary.nominal_time, draft.generation, draft.expected_active_generation,
                draft.generation_changed, "", draft.new_resources,
                draft.removed_resources,
                tuple((endpoint.value, name) for connection in draft.removed_resources
                      for endpoint, name in zip((connection.endpoint_a, connection.endpoint_b), _cached_interface_names(connection, self._connection_names))),
                tuple(telemetry),
            ))
            previous = draft
        if not self._continuation_enabled and seen_connections != logical_ports.keys():
            raise ValueError("Runner artifact logical port inventory differs from finite resources")
        if len(plans) != self._capacity_demand["boundary_count"]:
            raise _CapacityAuditError("Runner finite plan count differs from sealed demand")
        for node in self._capacity_demand["per_node"]:
            key = node["key"]
            if (self._observed_active_entry_totals[key] != node["cumulative_active_link_entries"]
                or self._observed_route_entry_totals[key] != node["cumulative_route_entries"]):
                raise _CapacityAuditError("finite semantic entry totals differ from sealed demand")
        if self._continuation_enabled:
            # Stable per-connection ownership has no dependence on an unknown
            # future withdrawal. Re-establishment reuses the same logical IDs.
            for request in requests:
                for value in request["connections"]:
                    value["retirement_node"] = value["node_a"]
                    value["retirement_group"] = _RETIREMENT_GROUP_BASE + value["logical_port_a"]
                request["retirement_batches"] = [
                    {"key": value["node_a"], "group": _RETIREMENT_GROUP_BASE + value["logical_port_a"],
                     "connections": [value]}
                    for value in request.pop("removed_connections")
                ]
        else:
            # Bind each physical lifetime to exactly one future retirement cohort.
            # Only one endpoint receives a nonzero group; the kernel removes its peer.
            active = {}
            for ordinal, (plan, request) in enumerate(zip(plans, requests)):
                for connection, value in zip(plan.created, request["connections"]):
                    active[connection] = value
                cohort = [(active.pop(connection), value)
                          for connection, value in zip(plan.removed, request.pop("removed_connections"))]
                request["retirement_batches"] = self._assign_retirement_cohort(
                    cohort, _RETIREMENT_GROUP_BASE + ordinal + 1)
            self._assign_retirement_cohort(
                [(value, None) for value in active.values()],
                _RETIREMENT_GROUP_BASE + len(plans) + 1)
        encoding_started = monotonic_ns()
        plans = [replace(plan, request=json.dumps(
            {"action": "activate_boundary", "request": request}, separators=(",", ":")) + "\n")
            for plan, request in zip(plans, requests)]
        encoding_ns = monotonic_ns() - encoding_started
        construction_ns += monotonic_ns() - construction_started
        self._preparation_timings = {
            **self._batch_prewrite_work,
            "finite_plan_construction_seconds": construction_ns / 1e9,
            "activation_wire_encoding_seconds": encoding_ns / 1e9,
            "semantic_prewrite_roundtrip_seconds": prewrite_ns / 1e9,
            "semantic_prewrite_helper_seconds": prewrite_helper_seconds if prewrite_helper_count == prewrite_count else None,
            "semantic_prewrite_count": prewrite_count,
            "boundary_count": len(plans),
        }
        self._previous_build = previous
        return tuple(plans)

    def _admit_batch(self, demand):
        demand = _validate_runtime_capacity_demand(
            demand, expected_node_keys=tuple(node["key"] for node in self._nodes),
            expected_boundary_count=int(demand.get("boundary_count", -1)))
        basis = self._batch_capacity_basis
        factor = self._batch_capacity_factor
        carry = self._previous_build
        if (demand["boundary_count"] > factor * basis["boundary_count"]
            or demand["generation_count"] > factor * basis["generation_count"]
            or len(self._resident_generations) + demand["generation_count"] - int(carry is not None) > self._max_resident_generations):
            raise _CapacityAuditError("batch exceeds the fixed resident boundary/generation reservation")
        by_key = {node["key"]: node for node in demand["per_node"]}
        for key, limits in self._batch_admission_limits.items():
            value = by_key[key]
            for field, limit in limits.items():
                if value[field] > limit:
                    raise _CapacityAuditError(f"batch exceeds fixed per-node admission: {key}.{field}")
            active_cap, route_cap = self._map_capacities[key]
            # The carried generation is already reserved; counting it again is
            # conservative and leaves room for delayed in-flight retirement.
            if (self._resident_active_totals[key] + value["cumulative_active_link_entries"] > active_cap
                or self._resident_route_totals[key] + value["cumulative_route_entries"] > route_cap):
                raise _CapacityAuditError(f"batch resident entry reservation exhausted: {key}")
        self._capacity_demand = demand
        self._capacity_demand_by_key = by_key
        self._observed_boundary_count = 0
        self._observed_generation_count = int(carry is not None)
        self._observed_active_entry_totals = {node["key"]: 0 for node in self._nodes}
        self._observed_route_entry_totals = dict(self._observed_active_entry_totals)
        if carry is not None:
            for node in carry.generation_nodes:
                self._observed_active_entry_totals[node.key] = len(node.active_interfaces)
                self._observed_route_entry_totals[node.key] = len(node.routes)

    def _prewrite_nodes(self, draft, checkpoint):
        chunks, records, entries = [], [], 0
        for node in draft.generation_nodes:
            prior = self._node_versions.get(node.key)
            reuse = prior is not None and prior[0] is node
            version = prior[1] if reuse else draft.generation
            active = [] if reuse else [self._port_by_interface[node.key, name] for name in node.active_interfaces]
            routes = [] if reuse else [
                {"destination": destination, "logical_port_id": 0 if interface is None else self._port_by_interface[node.key, interface]}
                for destination, interface in node.routes]
            self._node_versions[node.key] = node, version
            self._batch_prewrite_work["node_versions_reused" if reuse else "node_versions_written"] += 1
            pending = [(True, value) for value in active] + [(False, value) for value in routes]
            for offset in range(0, max(1, len(pending)), 2048):
                values = pending[offset:offset + 2048]
                if records and (len(records) == 64 or entries + len(values) > 2048):
                    chunks.append(records)
                    records, entries = [], 0
                records.append({"key": node.key, "version": version,
                    "active_ports": [value for is_active, value in values if is_active],
                    "routes": [value for is_active, value in values if not is_active],
                    "complete": offset + 2048 >= len(pending)})
                entries += len(values)
        if records:
            chunks.append(records)
        helper_seconds = 0.0
        helper_complete = True
        for index, records in enumerate(chunks):
            checkpoint()
            result = self._executor.execute("prewrite_generation", {
                "pin_root": self._pin_root, "generation": draft.generation,
                "generation_nodes": records, "seal": index + 1 == len(chunks),
                "bpf_batch_update_supported": self._bpf_batch_update_supported,
            })
            expected_active = sum(len(node["active_ports"]) for node in records)
            expected_routes = sum(len(node["routes"]) for node in records)
            self._batch_prewrite_work["active_entries_written"] += expected_active
            self._batch_prewrite_work["route_entries_written"] += expected_routes
            self._batch_prewrite_work["prewrite_work_units"] += 1
            if (result.get("preloaded_active_links") != expected_active
                or result.get("preloaded_routes") != expected_routes):
                raise RuntimeError("finite BPF prewrite returned an incomplete entry count")
            helper = (result.get("command_timing") or {}).get("helper") or {}
            seconds = helper.get("dispatch_seconds")
            helper_complete &= seconds is not None
            helper_seconds += seconds or 0
        return {"helper_seconds": helper_seconds, "helper_complete": helper_complete}

    def reclaim(self):
        active = self._active
        if active is None:
            return {"reclaimed_generations": [], "deleted_entries": 0}
        result = self._executor.execute("reclaim_generations", {
            "before_generation": active.generation, "maximum_entries": 1024})
        for generation in result["reclaimed_generations"]:
            self._resident_generations.remove(generation)
            reservation = self._generation_reservations.pop(generation)
            for node, (active_entries, routes) in zip(self._nodes, reservation):
                self._resident_active_totals[node["key"]] -= active_entries
                self._resident_route_totals[node["key"]] -= routes
        return result

    def activate(self, plan: _PreparedBoundary) -> dict[str, Any]:
        """Submit only an immutable current delta and handle real mutation results."""
        request_started = monotonic_ns()
        actual = _NO_ACTIVE_GENERATION if self._active is None else self._active.generation
        if plan.expected_active_generation != actual:
            raise RuntimeError("prepared boundary predecessor is stale")
        # Retain conservative ownership even when transport dies after a mutation.
        self._all_prepared_resources.update(plan.created)
        request_ended = monotonic_ns()
        try:
            result = self._executor.execute("activate_boundary", plan.request)
        except BaseException as error:
            evidence = getattr(error, "remote_evidence", getattr(error, "evidence", None))
            if not isinstance(evidence, Mapping):
                evidence = {
                    "failure_phase": "executor-result",
                    "target_transition_complete": False,
                    "generation_publication_occurred": None,
                    "live_state_may_be_partial": True,
                    "cleanup_ownership_retained": True,
                }
            raise _IncompleteActivationError(
                "live activation did not complete the target formal boundary",
                evidence={**evidence, "command_timing": getattr(error, "command_timing", None),
                          "parent_request_preparation_seconds": (request_ended - request_started) / 1e9},
            ) from error
        ledger_started = monotonic_ns()
        if (result.get("created_connections") != len(plan.created)
            or result.get("deleted_connections") != len(plan.removed)
            or result.get("target_transition_complete") is not True
            or result.get("boundary_timestamp") != plan.nominal_time.isoformat()
            or bool(result.get("generation_publication_occurred")) != plan.generation_changed):
            raise _IncompleteActivationError(
                "Main kernel mutation result is incomplete",
                evidence={**result, "target_transition_complete": False,
                          "live_state_may_be_partial": True,
                          "cleanup_ownership_retained": True},
            )
        self._prepared_resources.update(plan.created)
        self._prepared_resources.difference_update(plan.removed)
        self._all_prepared_resources.difference_update(plan.removed)
        for identity in plan.withdrawn_identities:
            self._interface_indexes.pop(identity, None)
        for value in result.get("interface_identities", ()):
            if int(value["ifindex"]) <= 0:
                raise RuntimeError("Main returned an invalid live ifindex")
            self._interface_indexes[(value["key"], value["interface"])] = int(value["ifindex"])
        self._active = plan
        result["parent_runtime_timing"] = {
            "request_preparation_seconds": (request_ended - request_started) / 1e9,
            "result_ledger_seconds": (monotonic_ns() - ledger_started) / 1e9,
        }
        return result

    def working_set_counts(self) -> dict[str, int]:
        return {
            "resident_generation_keys": len(self._resident_generations),
            "resident_generation_capacity": self._max_resident_generations,
            "resident_reserved_active_entries": sum(self._resident_active_totals.values()),
            "resident_reserved_route_entries": sum(self._resident_route_totals.values()),
            "prepared_physical_resources": len(self._prepared_resources),
            "all_owned_physical_resources": len(self._all_prepared_resources),
        }


    def telemetry_foundation(self, *, foundation_token: str) -> dict[str, Any]:
        """Return detached collector and durable identity facts after initialize."""

        if not self._telemetry_enabled or not self._initialized:
            raise RuntimeError("traffic telemetry foundation is not initialized")
        return {
            "foundation_token": foundation_token,
            "pin_root": self._pin_root,
            "ring_capacity_bytes": int(
                self._telemetry_kernel["ring_capacity_bytes"]
            ),
            "event_schema": dict(self._telemetry_kernel["event_schema"]),
            "nodes": tuple(
                {
                    "node_slot": int(node["node_slot"]),
                    "node_id": str(node["key"]),
                    "runtime_ipv4": str(node["node_address"]),
                    "pin_component": str(node["pin_component"]),
                }
                for node in self._nodes
            ),
            "collector_request": {
                "pin_root": self._pin_root,
                "nodes": tuple(
                    {
                        "node_slot": int(node["node_slot"]),
                        "node_id": str(node["key"]),
                    }
                    for node in self._nodes
                ),
            },
        }

    def runtime_node_addresses(self) -> dict[NodeId, str]:
        """Return detached Runner-owned node addresses already frozen at init."""

        with self._lock:
            return dict(self._node_addresses)

    def _validate_collision_audit(self, audit: object) -> None:
        if (
            not isinstance(audit, dict)
            or audit.get("result") != "passed"
            or audit.get("selected_node_address_range")
            != str(self._node_address_range)
            or audit.get("audited_node_namespaces") != len(self._nodes)
        ):
            raise _CapacityAuditError(
                "privileged runtime command node address collision evidence "
                "is incomplete during Preparation"
            )

    def _audit_boundary_capacity(
        self, draft: _BoundaryBuild
    ) -> dict[str, Any]:
        """Fail before helper work if decoded demand exceeds the sealed envelope."""

        with self._lock:
            observed_boundaries = self._observed_boundary_count
            observed_generations = self._observed_generation_count
            active_totals = dict(self._observed_active_entry_totals)
            route_totals = dict(self._observed_route_entry_totals)
            demand = self._capacity_demand
        if observed_boundaries + 1 > int(demand["boundary_count"]):
            raise _CapacityAuditError(
                "decoded boundary count exceeds sealed runtime capacity demand"
            )
        by_key = self._capacity_demand_by_key
        generation_nodes = {node.key: node for node in draft.generation_nodes}
        if set(generation_nodes) != set(by_key):
            raise _CapacityAuditError(
                "decoded generation node keys exceed sealed runtime capacity demand"
            )
        if draft.generation_changed:
            if observed_generations + 1 > int(demand["generation_count"]):
                raise _CapacityAuditError(
                    "decoded generation count exceeds sealed runtime capacity demand"
                )
            for key, node in generation_nodes.items():
                active_count = len(node.active_interfaces)
                route_count = len(node.routes)
                sealed = by_key[key]
                if (
                    active_count
                    > sealed["maximum_generation_active_link_entries"]
                    or route_count > sealed["maximum_generation_route_entries"]
                    or active_totals[key] + active_count
                    > sealed["cumulative_active_link_entries"]
                    or route_totals[key] + route_count
                    > sealed["cumulative_route_entries"]
                ):
                    raise _CapacityAuditError(
                        "decoded generation exceeds sealed runtime capacity demand: "
                        f"node={key}"
                    )
        transition_resources = set(draft.previous_state.resources) | set(
            draft.state.resources
        )
        _assert_unique_resource_bindings(transition_resources, self._connection_names)
        if len(transition_resources) > demand["physical_connection_high_water"]:
            raise _CapacityAuditError(
                "decoded transition physical demand exceeds sealed envelope"
            )
        physical_by_key = {key: 0 for key in by_key}
        for connection_id in transition_resources:
            physical_by_key[connection_id.endpoint_a.value] += 1
            physical_by_key[connection_id.endpoint_b.value] += 1
        if any(
            physical_by_key[key] > by_key[key]["physical_interface_high_water"]
            for key in by_key
        ):
            raise _CapacityAuditError(
                "decoded transition interface demand exceeds sealed envelope"
            )
        terminal_checked = observed_boundaries + 1 == demand["boundary_count"]
        if terminal_checked:
            terminal = demand["terminal"]
            if (
                len(draft.state.resources) != terminal["active_connection_count"]
                or observed_generations + int(draft.generation_changed)
                != demand["generation_count"]
                or any(
                    len(generation_nodes[key].active_interfaces)
                    != by_key[key]["terminal_active_link_entries"]
                    or len(generation_nodes[key].routes)
                    != by_key[key]["terminal_route_entries"]
                    for key in by_key
                )
            ):
                raise _CapacityAuditError(
                    "terminal decoded state differs from sealed capacity demand"
                )
        return {
            "result": "passed",
            "boundary_count_after": observed_boundaries + 1,
            "generation_count_after": (
                observed_generations + int(draft.generation_changed)
            ),
            "transition_physical_connections": len(transition_resources),
            "terminal_reconciled": terminal_checked,
        }

    def _commit_observed_capacity(self, draft: _BoundaryBuild) -> None:
        self._observed_boundary_count += 1
        if draft.generation_changed:
            self._observed_generation_count += 1
            for node in draft.generation_nodes:
                self._observed_active_entry_totals[node.key] += len(
                    node.active_interfaces
                )
                self._observed_route_entry_totals[node.key] += len(node.routes)

    def _connection_identity(
        self, connection_id: OperationalConnectionId
    ) -> dict[str, Any]:
        interface_a, interface_b = _cached_interface_names(connection_id, self._connection_names)
        return {
            "connection": _connection_key(connection_id),
            "node_a": connection_id.endpoint_a.value,
            "node_b": connection_id.endpoint_b.value,
            "interface_a": interface_a,
            "interface_b": interface_b,
        }

    @staticmethod
    def _derived_connection_request(
        value: _DerivedConnectionRequest,
    ) -> dict[str, Any]:
        return {
            "connection": value.connection,
            "node_a": value.node_a,
            "node_b": value.node_b,
            "interface_a": value.interface_a,
            "interface_b": value.interface_b,
            "parameters_a": {
                "bandwidth_bps": value.bandwidth_a_bps,
                "delay_us": value.delay_a_us,
                "packet_loss_ppm": value.packet_loss_a_ppm,
            },
            "parameters_b": {
                "bandwidth_bps": value.bandwidth_b_bps,
                "delay_us": value.delay_b_us,
                "packet_loss_ppm": value.packet_loss_b_ppm,
            },
        }

    def _ground_station_access_peer_routes(
        self, state: _MaterializedDataplaneState
    ) -> dict[NodeId, dict[str, str]]:
        """Derive G-side direct-peer routes from ACTIVE access resources."""

        result: dict[NodeId, dict[str, str]] = {
            node_id: {} for node_id in self._bindings
        }
        for connection_id in state.resources:
            if (
                connection_id.kind
                is not OperationalConnectionKind.GROUND_STATION_ACCESS
            ):
                continue
            ground_station, satellite = _ground_station_access_endpoints(
                connection_id
            )
            names = _cached_interface_names(connection_id, self._connection_names)
            interface = (
                names[0]
                if connection_id.endpoint_a == ground_station
                else names[1]
            )
            destination = self._node_addresses[satellite]
            prior = result[ground_station].setdefault(destination, interface)
            if prior != interface:
                raise ValueError(
                    "active ground-station access resources conflict for one peer"
                )
        return result

    def _derive_boundary_plan(
        self,
        boundary: _NominalBoundary,
        *,
        predecessor: _BoundaryBuild | None,
        previous_state: _MaterializedDataplaneState,
        state_indexes: _BoundaryStateIndexes,
    ) -> _BoundaryBuild:
        """Derive a finite plan only during one-shot Preparation."""

        _validate_boundary_lowering(boundary)
        resource_changes, forwarding_changes, zone_changes = state_indexes.apply(boundary)
        state = state_indexes.snapshot()
        generation_changed = predecessor is None or bool(resource_changes or forwarding_changes or zone_changes)
        expected_active_generation = (
            _NO_ACTIVE_GENERATION
            if predecessor is None
            else predecessor.generation
        )
        generation = (
            0
            if predecessor is None
            else predecessor.generation + int(generation_changed)
        )
        if generation >= _NO_ACTIVE_GENERATION:
            raise _CapacityAuditError("session generation identity space is exhausted")
        node_ids = self._node_ids
        new_interfaces = (_interfaces_for_state(state, node_ids, self._connection_names)
                          if predecessor is None or resource_changes
                          else {node.node_id: node.active_interfaces for node in predecessor.generation_nodes})
        old_routes = (predecessor.routes if predecessor is not None else
                      {node: {self._node_addresses[node]: None} for node in node_ids})
        new_routes = (_routes_for_state(
            state, node_ids, self._node_addresses, self._connection_names,
            self._packet_addresses, ordered=False,
        ) if predecessor is None or forwarding_changes or zone_changes else old_routes)
        old_access = predecessor.access_routes if predecessor is not None else {node: {} for node in node_ids}
        new_access = (self._ground_station_access_peer_routes(state)
                      if predecessor is None or resource_changes else old_access)
        # Equality is checked once in C; untouched node dictionaries then skip
        # Python-level old/new delta scans and generation route sorting.
        if new_routes is not old_routes:
            for node_id in node_ids:
                if new_routes[node_id] == old_routes[node_id]:
                    new_routes[node_id] = old_routes[node_id]
        if new_access is not old_access:
            for node_id in node_ids:
                if new_access[node_id] == old_access[node_id]:
                    new_access[node_id] = old_access[node_id]
        old_resources = set(previous_state.resources)
        added_resources = tuple(sorted((key for key, prior in resource_changes.items() if prior is None), key=_connection_key))
        removed_resources = tuple(sorted((key for key, prior in resource_changes.items() if prior is not None), key=_connection_key))
        parameters = state_indexes.parameters
        new_connection_requests = []
        for connection_id in added_resources:
            interface_a, interface_b = _cached_interface_names(connection_id, self._connection_names)
            parameters_a = parameters[
                (
                    connection_id,
                    connection_id.endpoint_a,
                    connection_id.endpoint_b,
                )
            ]
            parameters_b = parameters[
                (
                    connection_id,
                    connection_id.endpoint_b,
                    connection_id.endpoint_a,
                )
            ]
            new_connection_requests.append(
                _DerivedConnectionRequest(
                    connection_id=connection_id,
                    connection=_connection_key(connection_id),
                    node_a=connection_id.endpoint_a.value,
                    node_b=connection_id.endpoint_b.value,
                    interface_a=interface_a,
                    interface_b=interface_b,
                    bandwidth_a_bps=parameters_a.bandwidth_bps,
                    delay_a_us=parameters_a.delay_us,
                    packet_loss_a_ppm=parameters_a.packet_loss_ppm,
                    bandwidth_b_bps=parameters_b.bandwidth_bps,
                    delay_b_us=parameters_b.delay_us,
                    packet_loss_b_ppm=parameters_b.packet_loss_ppm,
                )
            )
        mutable: dict[NodeId, dict[str, list[Any]]] = {
            node_id: {
                "qdisc": [],
                "route_delete": [],
                "route_replace": [],
                "access_peer_route_delete": [],
                "access_peer_route_replace": [],
                "zone_address_delete": [],
                "zone_address_add": [],
            }
            for node_id in node_ids
        }
        old_edges = {value.zone_id: value for value in previous_state.zone_edges}
        new_edges = {value.zone_id: value for value in state.zone_edges}
        changed_zone_ids = {
            zone_id
            for zone_id in set(old_edges) | set(new_edges)
            if old_edges.get(zone_id) != new_edges.get(zone_id)
        }
        for zone_id in changed_zone_ids:
            old = old_edges.get(zone_id)
            new = new_edges.get(zone_id)
            if old is not None:
                mutable[old.service_satellite_id][
                    "zone_address_delete"
                ].append(str(ip_interface(old.packet_prefix).ip))
            if new is not None:
                mutable[new.service_satellite_id]["zone_address_add"].append(
                    str(ip_interface(new.packet_prefix).ip)
                )
        for operation in boundary.operations:
            intent = operation.intent
            if (
                not isinstance(intent, _QdiscIntent)
                or intent.connection_id not in old_resources
            ):
                continue
            value = parameters[(intent.connection_id, intent.source, intent.target)]
            mutable[value.source]["qdisc"].append(
                _DerivedQdiscOperation(
                    interface=_interface_name_for_source(
                        value.connection_id, value.source, self._connection_names
                    ),
                    bandwidth_bps=value.bandwidth_bps,
                    delay_us=value.delay_us,
                    packet_loss_ppm=value.packet_loss_ppm,
                )
            )
        for node_id in node_ids:
            if old_routes[node_id] is not new_routes[node_id]:
                mutable[node_id]["route_delete"] = [
                    _DerivedRouteOperation(destination, interface)
                    for destination, interface in old_routes[node_id].items()
                    if interface is not None
                    and new_routes[node_id].get(destination) != interface
                ]
                mutable[node_id]["route_replace"] = [
                    _DerivedRouteOperation(destination, interface)
                    for destination, interface in new_routes[node_id].items()
                    if interface is not None
                    and old_routes[node_id].get(destination) != interface
                ]
            if old_access[node_id] is not new_access[node_id]:
                mutable[node_id]["access_peer_route_delete"] = [
                    _DerivedRouteOperation(destination, interface)
                    for destination, interface in old_access[node_id].items()
                    if new_access[node_id].get(destination) != interface
                ]
                mutable[node_id]["access_peer_route_replace"] = [
                    _DerivedRouteOperation(destination, interface)
                    for destination, interface in new_access[node_id].items()
                    if old_access[node_id].get(destination) != interface
                ]
        if not generation_changed:
            generation_nodes = predecessor.generation_nodes
        else:
            prior_nodes = {} if predecessor is None else {
                node.node_id: node for node in predecessor.generation_nodes
            }
            generation_nodes = tuple(
                prior_nodes[node_id]
                if node_id in prior_nodes
                and new_interfaces[node_id] == prior_nodes[node_id].active_interfaces
                and new_routes[node_id] == old_routes[node_id]
                else _DerivedGenerationNode(
                    node_id=node_id,
                    key=node_id.value,
                    active_interfaces=new_interfaces[node_id],
                    routes=(prior_nodes[node_id].routes
                            if node_id in prior_nodes and new_routes[node_id] == old_routes[node_id]
                            else tuple(sorted(new_routes[node_id].items()))),
                )
                for node_id in node_ids
            )
        operation_nodes = []
        operation_fields = (
            "qdisc",
            "route_delete",
            "route_replace",
            "access_peer_route_delete",
            "access_peer_route_replace",
            "zone_address_delete",
            "zone_address_add",
        )
        for node_id in node_ids:
            values = mutable[node_id]
            if not any(values[field] for field in operation_fields):
                continue
            operation_nodes.append(
                _DerivedOperationNode(
                    node_id=node_id,
                    key=node_id.value,
                    qdisc=tuple(
                        sorted(values["qdisc"], key=lambda value: value.interface)
                    ),
                    route_delete=tuple(sorted(values["route_delete"], key=lambda value: value.destination)),
                    route_replace=tuple(sorted(values["route_replace"], key=lambda value: value.destination)),
                    access_peer_route_delete=tuple(
                        values["access_peer_route_delete"]
                    ),
                    access_peer_route_replace=tuple(
                        values["access_peer_route_replace"]
                    ),
                    zone_address_delete=tuple(
                        sorted(set(values["zone_address_delete"]))
                    ),
                    zone_address_add=tuple(
                        sorted(set(values["zone_address_add"]))
                    ),
                )
            )
        return _BoundaryBuild(
            boundary=boundary,
            previous_state=previous_state,
            state=state,
            expected_active_generation=expected_active_generation,
            generation=generation,
            generation_changed=generation_changed,
            new_resources=added_resources,
            removed_resources=removed_resources,
            new_connection_requests=tuple(new_connection_requests),
            generation_nodes=generation_nodes,
            operation_nodes=tuple(operation_nodes),
            routes=new_routes,
            access_routes=new_access,
        )

    @staticmethod
    def _assign_retirement_cohort(cohort, group):
        incidents = {}
        for index, (created, _) in enumerate(cohort):
            for side in ("a", "b"):
                incidents.setdefault(created[f"node_{side}"], []).append(index)
        assigned = set()
        batches = []
        # Stable high-degree-first ownership reduces namespace submissions while
        # membership remains the exact physical cohort, independent of topology.
        for key in sorted(incidents, key=lambda key: (-len(incidents[key]), key)):
            members = []
            for index in incidents[key]:
                if index in assigned:
                    continue
                assigned.add(index)
                created, removed = cohort[index]
                created["retirement_node"] = key
                created["retirement_group"] = group
                if removed is not None:
                    members.append(removed)
            if members:
                batches.append({"key": key, "group": group, "connections": members})
        return batches

    def _activation_request(
        self, draft: _BoundaryBuild, *, expected_active_generation: int,
        generation: int, generation_changed: bool = True,
    ) -> dict[str, Any]:
        operation_nodes = []
        for node in draft.operation_nodes:
            replacements = {(value.destination, value.interface)
                            for value in (*node.route_replace, *node.access_peer_route_replace)}
            replacing_destinations = {destination for destination, _ in replacements}
            withdrawals = {(value.destination, value.interface)
                           for value in (*node.route_delete, *node.access_peer_route_delete)} - replacements
            operation_nodes.append({
                "key": node.key,
                "qdisc": [{"interface": value.interface, "bandwidth_bps": value.bandwidth_bps,
                           "delay_us": value.delay_us, "packet_loss_ppm": value.packet_loss_ppm}
                          for value in node.qdisc],
                "route_replace": sorted(replacements),
                # Neighbours on the former interface still retire when a route
                # for this destination was replaced on another interface.
                "route_withdraw": [(destination, interface, destination not in replacing_destinations)
                                   for destination, interface in sorted(withdrawals)],
                "zone_address_delete": list(node.zone_address_delete),
                "zone_address_add": list(node.zone_address_add),
            })
        return {
            "common_mac": _COMMON_MAC,
            "boundary_timestamp": draft.boundary.nominal_time.isoformat(),
            "expected_active_generation": expected_active_generation,
            "generation": generation, "generation_changed": generation_changed,
            "operation_nodes": operation_nodes,
        }

    def cleanup(self) -> dict[str, Any]:
        if not self._cleanup_required:
            return {"deleted_connections": 0,
                    "pin_root_removed": True if self._initialization_outcome == "cleaned" else None,
                    "cleanup_confirmed": True,
                    "initialization_outcome": self._initialization_outcome, "errors": []}
        construction_started = monotonic_ns()
        request = {
                "pin_root": self._pin_root,
                "nodes": [
                    {
                        **node,
                        "node_address_owned": self._node_by_key[node["key"]] in self._owned_node_addresses,
                        "zone_addresses": sorted(self._all_zone_addresses[self._node_by_key[node["key"]]]),
                    }
                    for node in self._nodes
                ],
                "connections": [
                    self._connection_identity(value)
                    for value in sorted(
                        self._all_prepared_resources, key=_connection_key
                    )
                ],
                "worker_count": self._worker_count,
            }
        construction_ended = monotonic_ns()
        result = self._executor.execute("cleanup", request)
        ledger_started = monotonic_ns()
        known_no_mutation = (result.get("cleanup_confirmed") is True
                             and result.get("initialization_outcome") == "no-mutation")
        if (result.get("pin_root_removed") is not True and not known_no_mutation) or result.get("errors"):
            raise RuntimeError(
                "Runner teardown evidence is incomplete; ownership retained for retry"
            )
        self._initialized = False
        self._cleanup_required = False
        self._initialization_outcome = "no-mutation" if known_no_mutation else "cleaned"
        self._telemetry_kernel.clear()
        self._prepared_resources.clear()
        self._all_prepared_resources.clear()
        self._interface_indexes.clear()
        self._resident_generations.clear()
        self._map_capacities.clear()
        self._capacity_plan.clear()
        self._capacity_demand.clear()
        self._observed_boundary_count = 0
        self._observed_generation_count = 0
        self._observed_active_entry_totals.clear()
        self._observed_route_entry_totals.clear()
        self._physical_resource_high_water = 0
        self._bpf_batch_update_supported = False
        self._active = None
        self._all_zone_addresses.clear()
        self._owned_node_addresses.clear()
        result["parent_cleanup_timing"] = {
            "request_construction_seconds": (construction_ended - construction_started) / 1e9,
            "result_ledger_seconds": (monotonic_ns() - ledger_started) / 1e9,
        }
        return result


__all__: list[str] = []
