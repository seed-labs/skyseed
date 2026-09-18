"""Pure shared naming, state application and capacity accounting rules."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import blake2s
from ipaddress import IPv4Address, IPv4Network, ip_interface
from typing import Any, Mapping, Sequence
from skyseed_v2.shared import NodeId
from skyseed_v2.simulator import (
    GroundStationRouteDemandTarget,
    OperationalConnectionId,
    ZoneRouteDemandTarget,
)
from skyseed_v2.backend.bpf.execution_materials.actions import (
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


_NODE_ADDRESS_SPACE = IPv4Network("100.64.0.0/10")


_MAX_BPF_MAP_ENTRIES = 0xFFFFFFFF


_CAPACITY_DEMAND_SCHEMA_VERSION = 1


_INTERFACE_IDENTITY_DERIVATION = "blake2s-40-bit-connection-key-v1"


class _CapacityAuditError(RuntimeError):
    """Report that exact simulation state cannot fit owned runtime capacity."""


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


def _forwarding_key(binding: _ForwardingBinding) -> tuple[str, str, str]:
    target = binding.route_key.target
    if isinstance(target, GroundStationRouteDemandTarget):
        return (
            binding.route_key.source.value,
            "ground-station",
            target.ground_station_id.value,
        )
    if isinstance(target, ZoneRouteDemandTarget):
        return binding.route_key.source.value, "zone", target.service_key
    raise TypeError("compiled forwarding has an unknown target")


def _empty_state() -> _MaterializedDataplaneState:
    return _MaterializedDataplaneState((), (), (), ())


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
                        raise _CapacityAuditError("Runner interface-name capacity collision during preparation")
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
            "default Runner node address space cannot satisfy topology during preparation"
        )
    prefix_length = 32 - (allocation_size.bit_length() - 1)
    selected_range = IPv4Network(
        (int(_NODE_ADDRESS_SPACE.network_address), prefix_length)
    )
    try:
        zone_networks = tuple(ip_interface(prefix).network for prefix in zone_prefixes)
    except ValueError as error:
        raise _CapacityAuditError(
            "compiled Zone packet prefix is invalid during preparation"
        ) from error
    if any(not isinstance(network, IPv4Network) for network in zone_networks):
        raise _CapacityAuditError(
            "compiled Zone packet prefix is not IPv4 during preparation"
        )
    if any(selected_range.overlaps(zone_network) for zone_network in zone_networks):
        raise _CapacityAuditError(
            "Runner node address range collides with compiled Zone identity during preparation"
        )
    addresses = {
        node_id: str(IPv4Address(int(selected_range.network_address) + index))
        for index, node_id in enumerate(ordered_nodes, start=1)
    }
    if len(set(addresses.values())) != len(ordered_nodes):
        raise _CapacityAuditError("Runner node address allocation is not unique")
    return addresses, selected_range


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
        self.zone_edges = {v.service_key: v for v in previous.zone_edges}
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
                prior_zones.setdefault(intent.service_key, zone_edges.get(intent.service_key))
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
                prior = zone_edges.get(intent.service_key)
                prior_service = None if prior is None else prior.service_satellite_id
                if intent.phase is _ZoneEdgePhase.READINESS:
                    if (
                        prior_service != intent.previous_service_satellite_id
                        or intent.service_satellite_id is None
                    ):
                        raise ValueError("compiled Zone readiness is inconsistent")
                    zone_edges[intent.service_key] = _ZoneEdgeState(
                        intent.zone_id,
                        intent.packet_prefix,
                        intent.service_satellite_id,
                        intent.network_scope,
                    )
                else:
                    if intent.previous_service_satellite_id is None:
                        raise ValueError("compiled Zone retirement has no predecessor")
                    if intent.service_satellite_id is None:
                        if prior_service != intent.previous_service_satellite_id:
                            raise ValueError("compiled Zone retirement is stale")
                        del zone_edges[intent.service_key]
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

    def snapshot(self, *, ordered=True):
        prior = self._snapshot
        def values(items, key):
            return tuple(sorted(items, key=key)) if ordered else tuple(items)
        self._snapshot = _MaterializedDataplaneState(
            values(self.resources, _connection_key)
            if "resources" in self._dirty else prior.resources,
            values(self.parameters.values(), lambda value: (
                _connection_key(value.connection_id),
                _node_key(value.source), _node_key(value.target),
            ))
            if "parameters" in self._dirty else prior.parameters,
            values(self.forwarding.values(), _forwarding_key)
            if "forwarding" in self._dirty else prior.forwarding,
            values(self.zone_edges.values(), lambda value: value.service_key)
            if "zone_edges" in self._dirty else prior.zone_edges,
        )
        self._dirty.clear()
        return self._snapshot


_ZONE_POOL = IPv4Network("198.18.0.0/15")


def _allocate_zone_prefixes(zone_ids: Sequence[str]) -> dict[str, str]:
    if len(zone_ids) > _ZONE_POOL.num_addresses - 2:
        raise ValueError("PhysicalWorld has too many Zones for BPF identity space")
    return {
        zone_id: f"{IPv4Address(int(_ZONE_POOL.network_address) + index)}/32"
        for index, zone_id in enumerate(zone_ids, start=1)
    }
