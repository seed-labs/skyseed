"""Pure shared identity allocation, naming and state application rules."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import blake2s
from ipaddress import IPv4Address, IPv4Network, ip_interface
from typing import Sequence
from skyseed.shared import NodeId
from skyseed.simulator import (
    GroundStationRouteDemandTarget, SatelliteRouteDemandTarget,
    OperationalConnectionId,
    ZoneRouteDemandTarget,
)
from skyseed.backend.execution_materials.actions import (
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


_INTERFACE_IDENTITY_DERIVATION = "blake2s-40-bit-connection-key-v1"


class _CapacityAuditError(RuntimeError):
    """Report a violation of runtime capacity or shared identity-allocation constraints."""


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
    if isinstance(target, SatelliteRouteDemandTarget):
        return binding.route_key.source.value, "satellite", target.satellite_id.value
    if isinstance(target, ZoneRouteDemandTarget):
        return binding.route_key.source.value, "zone", target.service_key
    raise TypeError("compiled forwarding has an unknown target")


def _empty_state() -> _MaterializedDataplaneState:
    return _MaterializedDataplaneState((), (), (), ())


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
            for connection in binding.connection_ids:
                self.forwarding_by_connection.setdefault(connection, set()).add(binding.route_key)

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
                    for connection in prior.connection_ids:
                        forwarding_by_connection[connection].remove(intent.route_key)
                if prior != intent.previous:
                    raise ValueError("compiled forwarding predecessor is stale")
                if intent.operation is _MutationOperation.REMOVE:
                    if intent.current is not None or prior is None:
                        raise ValueError("compiled forwarding removal is inconsistent")
                    del forwarding[intent.route_key]
                else:
                    if intent.current is None:
                        raise ValueError("compiled forwarding update has no current binding")
                    touched_connections.update(intent.current.connection_ids)
                    forwarding[intent.route_key] = intent.current
                    for connection in intent.current.connection_ids:
                        forwarding_by_connection.setdefault(connection, set()).add(intent.route_key)
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
            if binding is not None and any((v.connection, v.source, v.peer) not in parameters for v in binding.exits):
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
