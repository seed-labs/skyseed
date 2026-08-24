"""Runner-private rolling tc-BPF runtime realization and state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from hashlib import blake2s, sha256
from ipaddress import IPv4Address, IPv4Network, ip_interface
import json
from threading import RLock
from typing import Any, Mapping, Protocol

from skyseed_v2.shared import NodeId
from skyseed_v2.simulator import (
    ConnectionDirection,
    ConnectionId,
    ConnectionKind,
    ConnectionTransitionOperation,
    DifferenceOperation,
    GroundStationRouteTarget,
    RouteKey,
    ZoneRouteTarget,
)

from ..compiled_realization import (
    _ForwardingBinding,
    _ForwardingIntent,
    _QdiscIntent,
    _ResourceIntent,
    _ZoneEdgeIntent,
    _ZoneEdgeState,
)

_NODE_ADDRESS_SPACE = IPv4Network("100.64.0.0/10")
_COMMON_MAC = "02:53:4b:59:00:01"
_NO_ACTIVE_GENERATION = 0xFFFFFFFF
_MAX_BPF_MAP_ENTRIES = 0xFFFFFFFF


class _CapacityAuditError(RuntimeError):
    """Report that exact simulation state cannot fit owned runtime capacity."""


class _PrivilegedCommandExecutor(Protocol):
    def execute(
        self, action: str, request: Mapping[str, Any]
    ) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class _QdiscValue:
    connection_id: ConnectionId
    direction: ConnectionDirection
    bandwidth_bps: int
    delay_us: int
    packet_loss_ppm: int


@dataclass(frozen=True, slots=True)
class _MaterializedDataplaneState:
    resources: tuple[ConnectionId, ...]
    parameters: tuple[_QdiscValue, ...]
    forwarding: tuple[_ForwardingBinding, ...]
    zone_edges: tuple[_ZoneEdgeState, ...]


@dataclass(frozen=True, slots=True)
class _ReadyDataplaneBoundary:
    boundary: _NominalBoundary
    foundation_token: str
    predecessor_time: object
    expected_active_generation: int
    generation: int
    state: _MaterializedDataplaneState
    preparation_request: dict[str, Any]
    activation_request: dict[str, Any]
    generation_keys: tuple[tuple[str, tuple[int, ...], tuple[int, ...]], ...]
    prepared_resources: tuple[ConnectionId, ...]
    readiness_sha256: str
    preparation_result: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _PendingDataplaneBoundary:
    boundary: _NominalBoundary
    previous_state: _MaterializedDataplaneState
    state: _MaterializedDataplaneState
    expected_active_generation: int
    generation: int
    folded_forwarding_constraints: int


@dataclass(frozen=True, slots=True)
class _RetiredForwardingRealization:
    generation: int


@dataclass(frozen=True, slots=True)
class _RetiredPhysicalRealization:
    resources: tuple[ConnectionId, ...]


def _node_key(node_id: NodeId) -> tuple[int, str, str]:
    return node_id.asn, node_id.entity_kind, node_id.entity_id


def _connection_key(connection_id: ConnectionId) -> str:
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


def _interface_names(connection_id: ConnectionId) -> tuple[str, str]:
    component = _component("", _connection_key(connection_id))
    return f"sv2{component}a", f"sv2{component}b"


def _direction_endpoints(
    connection_id: ConnectionId,
    direction: ConnectionDirection,
) -> tuple[NodeId, NodeId]:
    if direction is ConnectionDirection.ENDPOINT_A_TO_B:
        return connection_id.endpoint_a, connection_id.endpoint_b
    return connection_id.endpoint_b, connection_id.endpoint_a


def _ground_station_access_endpoints(
    connection_id: ConnectionId,
) -> tuple[NodeId, NodeId]:
    if not isinstance(connection_id, ConnectionId):
        raise TypeError("access peer probe requires a ConnectionId")
    if connection_id.kind is not ConnectionKind.GROUND_STATION_ACCESS:
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
    if isinstance(target, GroundStationRouteTarget):
        return (
            binding.route_key.source.value,
            "ground-station",
            target.ground_station_id.value,
        )
    if isinstance(target, ZoneRouteTarget):
        return binding.route_key.source.value, "zone", target.zone_id
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
            "default Runner node address space cannot satisfy topology before READY"
        )
    prefix_length = 32 - (allocation_size.bit_length() - 1)
    selected_range = IPv4Network(
        (int(_NODE_ADDRESS_SPACE.network_address), prefix_length)
    )
    try:
        zone_networks = tuple(ip_interface(prefix).network for prefix in zone_prefixes)
    except ValueError as error:
        raise _CapacityAuditError(
            "compiled Zone packet prefix is invalid before READY"
        ) from error
    if any(not isinstance(network, IPv4Network) for network in zone_networks):
        raise _CapacityAuditError(
            "compiled Zone packet prefix is not IPv4 before READY"
        )
    if any(selected_range.overlaps(zone_network) for zone_network in zone_networks):
        raise _CapacityAuditError(
            "Runner node address range collides with compiled Zone identity before READY"
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
                if intent.operation is ConnectionTransitionOperation.ESTABLISH
                else 4
            )
        if isinstance(intent, _QdiscIntent):
            return 1
        if isinstance(intent, _ForwardingIntent):
            return 2
        if isinstance(intent, _ZoneEdgeIntent):
            return 3
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


def _materialize_boundary(
    previous: _MaterializedDataplaneState,
    boundary: _NominalBoundary,
) -> _MaterializedDataplaneState:
    """Mechanically apply one ordered compiled transition to full state."""

    resources = {value: value for value in previous.resources}
    parameters = {
        (value.connection_id, value.direction): value
        for value in previous.parameters
    }
    forwarding = {value.route_key: value for value in previous.forwarding}
    zone_edges = {value.zone_id: value for value in previous.zone_edges}
    for operation in boundary.operations:
        intent = operation.intent
        if isinstance(intent, _ResourceIntent):
            if intent.operation is ConnectionTransitionOperation.ESTABLISH:
                if intent.connection_id in resources:
                    raise ValueError("compiled boundary establishes an active resource")
                resources[intent.connection_id] = intent.connection_id
            else:
                if intent.connection_id not in resources:
                    raise ValueError("compiled boundary withdraws an inactive resource")
                del resources[intent.connection_id]
                for direction in ConnectionDirection:
                    parameters.pop((intent.connection_id, direction), None)
        elif isinstance(intent, _QdiscIntent):
            if intent.connection_id not in resources:
                raise ValueError("compiled qdisc uses an inactive resource")
            key = intent.connection_id, intent.direction
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
                intent.connection_id, intent.direction, bandwidth, delay, loss
            )
        elif isinstance(intent, _ForwardingIntent):
            prior = forwarding.get(intent.route_key)
            if prior != intent.previous:
                raise ValueError("compiled forwarding predecessor is stale")
            if intent.operation is DifferenceOperation.REMOVED:
                if intent.current is not None or prior is None:
                    raise ValueError("compiled forwarding removal is inconsistent")
                del forwarding[intent.route_key]
            else:
                if intent.current is None:
                    raise ValueError("compiled forwarding update has no current binding")
                forwarding[intent.route_key] = intent.current
        elif isinstance(intent, _ZoneEdgeIntent):
            prior = zone_edges.get(intent.zone_id)
            prior_service = None if prior is None else prior.service_satellite_id
            if prior_service != intent.previous_service_satellite_id:
                raise ValueError("compiled Zone-edge predecessor is stale")
            if intent.operation is DifferenceOperation.REMOVED:
                if intent.service_satellite_id is not None or prior is None:
                    raise ValueError("compiled Zone-edge removal is inconsistent")
                del zone_edges[intent.zone_id]
            else:
                if intent.service_satellite_id is None:
                    raise ValueError("compiled Zone-edge update has no service node")
                zone_edges[intent.zone_id] = _ZoneEdgeState(
                    intent.zone_id,
                    intent.packet_prefix,
                    intent.service_satellite_id,
                )
        else:  # pragma: no cover - boundary inventory is closed
            raise TypeError("unknown compiled boundary operation")

    expected_parameters = {
        (connection_id, direction)
        for connection_id in resources
        for direction in ConnectionDirection
    }
    if set(parameters) != expected_parameters:
        raise ValueError("materialized qdisc state does not cover active resources")
    for binding in forwarding.values():
        if binding.connection_id not in resources:
            raise ValueError("materialized forwarding uses an inactive resource")
        if (binding.connection_id, binding.direction) not in parameters:
            raise ValueError("materialized forwarding lacks qdisc state")
    return _MaterializedDataplaneState(
        tuple(sorted(resources, key=_connection_key)),
        tuple(
            parameters[key]
            for key in sorted(
                parameters,
                key=lambda value: (_connection_key(value[0]), value[1].value),
            )
        ),
        tuple(sorted(forwarding.values(), key=_forwarding_key)),
        tuple(zone_edges[key] for key in sorted(zone_edges)),
    )


class _TcBpfRuntimeRealization:
    """Maintain Runner-private tc-BPF realization state across its phases."""

    def __init__(
        self,
        *,
        executor: _PrivilegedCommandExecutor,
        pin_root: str,
        bindings: Mapping[NodeId, object],
        telemetry_enabled: bool = False,
    ) -> None:
        self._executor = executor
        self._pin_root = pin_root
        self._bindings = dict(bindings)
        self._telemetry_enabled = telemetry_enabled
        self._telemetry_kernel: dict[str, Any] = {}
        self._lock = RLock()
        self._nodes: tuple[dict[str, Any], ...] = ()
        self._node_addresses: dict[NodeId, str] = {}
        self._node_address_range: IPv4Network | None = None
        self._owned_node_addresses: set[NodeId] = set()
        self._all_zone_addresses: dict[NodeId, set[str]] = {}
        self._prepared_resources: set[ConnectionId] = set()
        self._all_prepared_resources: set[ConnectionId] = set()
        self._generation_keys: dict[
            int, tuple[tuple[str, tuple[int, ...], tuple[int, ...]], ...]
        ] = {}
        self._max_resident_generations = 0
        self._map_capacities: dict[str, tuple[int, int]] = {}
        self._capacity_plan: dict[str, Any] = {}
        self._physical_resource_high_water = 0
        self._active: _ReadyDataplaneBoundary | None = None
        self._latest_ready: _ReadyDataplaneBoundary | None = None
        self._preparing: _PendingDataplaneBoundary | None = None
        self._realizations: dict[int, _ReadyDataplaneBoundary] = {}
        self._initialized = False

    def initialize(
        self,
        topology_signature: object,
        *,
        zone_prefixes: tuple[str, ...],
        capacity_boundaries: tuple[_NominalBoundary, ...],
        preparation_lookahead: timedelta | None,
        preparation_boundary_depth: int | None,
    ) -> dict[str, Any]:
        if self._initialized:
            raise RuntimeError("tc-BPF runtime realization is already initialized")
        topology_nodes = tuple(
            sorted(getattr(topology_signature, "node_ids"), key=_node_key)
        )
        if set(topology_nodes) != set(self._bindings):
            raise ValueError("runtime bindings do not match compiled topology")
        self._node_addresses, self._node_address_range = (
            _allocate_node_addresses(topology_nodes, tuple(zone_prefixes))
        )
        components = {
            node_id: _component("n-", node_id.value) for node_id in topology_nodes
        }
        if len(set(components.values())) != len(components):
            raise ValueError("default Runner node pin hash collision")
        self._nodes = tuple(
            {
                "key": node_id.value,
                "node_slot": node_slot,
                "pid": int(getattr(self._bindings[node_id], "pid")),
                "netns_inode": int(getattr(self._bindings[node_id], "netns_inode")),
                "pin_component": components[node_id],
                "node_address": self._node_addresses[node_id],
            }
            for node_slot, node_id in enumerate(topology_nodes)
        )
        self._all_zone_addresses = {node_id: set() for node_id in topology_nodes}
        capacity_plan = self._initial_capacity_plan(
            capacity_boundaries,
            preparation_lookahead=preparation_lookahead,
            preparation_boundary_depth=preparation_boundary_depth,
            zone_count=len(zone_prefixes),
        )
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
        self._nodes = tuple(
            {
                **node,
                "active_links_capacity": self._map_capacities[node["key"]][0],
                "routes_capacity": self._map_capacities[node["key"]][1],
            }
            for node in self._nodes
        )
        audit_request = {
            "nodes": self._nodes,
            "node_address_range": str(self._node_address_range),
            "zone_prefixes": tuple(zone_prefixes),
        }
        try:
            preliminary_audit = self._executor.execute(
                "audit_node_addresses", audit_request
            )
        except RuntimeError as error:
            raise _CapacityAuditError(
                "Runner node address collision audit failed before READY"
            ) from error
        self._validate_collision_audit(preliminary_audit)
        result = self._executor.execute(
            "initialize",
            {
                "pin_root": self._pin_root,
                "common_mac": _COMMON_MAC,
                "nodes": self._nodes,
                "inactive_generation": _NO_ACTIVE_GENERATION,
                "telemetry_enabled": self._telemetry_enabled,
                "node_address_range": str(self._node_address_range),
                "zone_prefixes": tuple(zone_prefixes),
            },
        )
        collision_audit = result.get("collision_audit")
        installed_keys = result.get("installed_node_addresses")
        telemetry_kernel = result.get("traffic_telemetry")
        expected_keys = {node["key"] for node in self._nodes}
        try:
            self._validate_collision_audit(collision_audit)
            if not isinstance(installed_keys, list) or set(installed_keys) != expected_keys:
                raise RuntimeError(
                    "privileged runtime command node address ownership evidence is incomplete"
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
            reported_keys = (
                set(installed_keys)
                if isinstance(installed_keys, list)
                and all(isinstance(key, str) for key in installed_keys)
                else set()
            )
            self._executor.execute(
                "cleanup",
                {
                    "pin_root": self._pin_root,
                    "nodes": [
                        {
                            **node,
                            "node_address_owned": node["key"] in reported_keys,
                        }
                        for node in self._nodes
                    ],
                    "connections": (),
                },
            )
            raise
        self._owned_node_addresses = set(topology_nodes)
        if self._telemetry_enabled:
            assert isinstance(telemetry_kernel, dict)
            self._telemetry_kernel = dict(telemetry_kernel)
        capacity_plan = {
            **capacity_plan,
            "collision_audit": collision_audit,
        }
        self._capacity_plan = capacity_plan
        self._initialized = True
        return {**result, "capacity_audit": capacity_plan}

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

    def working_set_counts(self) -> dict[str, int]:
        """Return bounded private realization counts for inspection evidence."""

        with self._lock:
            return {
                "resident_generation_keys": len(self._generation_keys),
                "retained_generation_realizations": len(self._realizations),
                "prepared_physical_resources": len(self._prepared_resources),
                "all_owned_physical_resources": len(self._all_prepared_resources),
                "preparation_in_flight": int(self._preparing is not None),
            }

    def telemetry_generation_identities(
        self, ready_value: object
    ) -> tuple[dict[str, Any], ...]:
        """Map one prepared generation's netns-local ifindexes durably."""

        ready = self._require_ready(ready_value)
        if not self._telemetry_enabled:
            return ()
        nodes = {
            str(node["key"]): node
            for node in ready.preparation_request["generation_nodes"]
        }
        keys = {key: indexes for key, indexes, _routes in ready.generation_keys}
        interface_connections: dict[str, dict[str, Any]] = {}
        for connection_id in ready.state.resources:
            identity = self._connection_identity(connection_id)
            interface_connections[identity["interface_a"]] = identity
            interface_connections[identity["interface_b"]] = identity
        result = []
        for node_id in sorted(nodes):
            node = nodes[node_id]
            names = tuple(node["active_interfaces"])
            indexes = keys[node_id]
            if len(names) != len(indexes):
                raise RuntimeError(
                    "traffic telemetry interface identity evidence is incomplete"
                )
            destinations_by_interface: dict[str, list[str]] = {}
            for route in node["routes"]:
                interface = route["interface"]
                if interface is not None:
                    destinations_by_interface.setdefault(interface, []).append(
                        str(route["destination"])
                    )
            for interface, ifindex in zip(names, indexes):
                connection = interface_connections[interface]
                if connection["node_a"] == node_id:
                    peer = connection["node_b"]
                elif connection["node_b"] == node_id:
                    peer = connection["node_a"]
                else:  # pragma: no cover - guarded by materialized resources
                    raise RuntimeError(
                        "traffic telemetry connection identity contradicts node"
                    )
                result.append(
                    {
                        "generation": ready.generation,
                        "boundary_timestamp": ready.boundary.nominal_time.isoformat(),
                        "node_slot": int(node["node_slot"]),
                        "node_id": node_id,
                        "ifindex": int(ifindex),
                        "interface": interface,
                        "connection_id": connection["connection"],
                        "peer_node_id": peer,
                        "route_destinations": tuple(
                            sorted(destinations_by_interface.get(interface, ()))
                        ),
                        "validity": "exact-generation",
                    }
                )
        return tuple(result)

    def _validate_collision_audit(self, audit: object) -> None:
        if (
            not isinstance(audit, dict)
            or audit.get("result") != "passed"
            or audit.get("selected_node_address_range")
            != str(self._node_address_range)
            or audit.get("audited_node_namespaces") != len(self._nodes)
        ):
            raise _CapacityAuditError(
                "privileged runtime command node address collision evidence is incomplete before READY"
            )

    def start_preparation(
        self,
        boundary: _NominalBoundary,
    ) -> tuple[object, dict[str, Any]]:
        """Materialize exactly once and reserve a private version identity."""

        with self._lock:
            if not self._initialized:
                raise RuntimeError("tc-BPF runtime realization is not initialized")
            if self._preparing is not None:
                raise RuntimeError(
                    "tc-BPF runtime realization preparation is already in flight"
                )
            if len(self._generation_keys) >= self._max_resident_generations:
                raise _CapacityAuditError(
                    "resident generation capacity is exhausted before preparation: "
                    f"resident={len(self._generation_keys)}, "
                    f"reserved_in_flight=0, capacity={self._max_resident_generations}"
                )
            previous = self._latest_ready
            previous_state = _empty_state() if previous is None else previous.state
            expected_active_generation = (
                _NO_ACTIVE_GENERATION if previous is None else previous.generation
            )
            generation = 0 if previous is None else previous.generation + 1
            pending = _PendingDataplaneBoundary(
                boundary=boundary,
                previous_state=previous_state,
                state=_materialize_boundary(previous_state, boundary),
                expected_active_generation=expected_active_generation,
                generation=generation,
                folded_forwarding_constraints=_validate_boundary_lowering(boundary),
            )
            self._preparing = pending
        return pending, {"generation": generation}

    def finish_preparation(
        self,
        pending_value: object,
        *,
        foundation_token: str,
        predecessor_time: object,
    ) -> tuple[object, dict[str, Any]]:
        """Validate, prepare, and return one opaque READY realization."""

        with self._lock:
            pending = self._require_pending(pending_value)
        boundary = pending.boundary
        previous_state = pending.previous_state
        state = pending.state
        expected_active_generation = pending.expected_active_generation
        generation = pending.generation
        new_resources = set(state.resources) - self._prepared_resources
        parameters = {
            (value.connection_id, value.direction): value for value in state.parameters
        }
        connections = [
            self._connection_request(value, parameters)
            for value in sorted(new_resources, key=_connection_key)
        ]
        generation_nodes = self._generation_nodes(state)
        try:
            capacity_audit = self._audit_generation_capacity(
                generation,
                generation_nodes=generation_nodes,
                state=state,
            )
            request = {
                "pin_root": self._pin_root,
                "common_mac": _COMMON_MAC,
                "nodes": self._nodes,
                "connections": connections,
                "generation": generation,
                "generation_nodes": generation_nodes,
                "boundary_timestamp": boundary.nominal_time.isoformat(),
                "operation_order_sha256": boundary.order_sha256,
            }
            result = {
                **self._executor.execute("prepare_boundary", request),
                "capacity_audit": capacity_audit,
            }
            for edge in state.zone_edges:
                self._all_zone_addresses[edge.service_satellite_id].add(
                    str(ip_interface(edge.packet_prefix).ip)
                )
            keys_value = result.get("generation_keys")
            if not isinstance(keys_value, list):
                raise RuntimeError(
                    "privileged runtime command omitted generation key evidence"
                )
            generation_keys = tuple(
                (
                    str(value["key"]),
                    tuple(int(item) for item in value["active_interface_indexes"]),
                    tuple(int(item) for item in value["route_keys"]),
                )
                for value in keys_value
            )
            expected_nodes = {value["key"]: value for value in generation_nodes}
            actual_nodes = {value[0]: value for value in generation_keys}
            if set(actual_nodes) != set(expected_nodes):
                raise RuntimeError(
                    "privileged runtime command generation evidence is incomplete"
                )
            for key, value in expected_nodes.items():
                actual = actual_nodes[key]
                if (
                    len(actual[1]) != len(value["active_interfaces"])
                    or len(actual[2]) != len(value["routes"])
                ):
                    raise RuntimeError(
                        "privileged runtime command generation preload is incomplete"
                    )
            activation = self._activation_request(
                boundary,
                previous_state=previous_state,
                state=state,
                expected_active_generation=expected_active_generation,
                generation=generation,
            )
            activation["folded_forwarding_constraints"] = (
                pending.folded_forwarding_constraints
            )
            evidence_payload = {
                "boundary": boundary.nominal_time.isoformat(),
                "foundation": foundation_token,
                "predecessor": (
                    None if predecessor_time is None else predecessor_time.isoformat()
                ),
                "expected_active_generation": expected_active_generation,
                "generation": generation,
                "preparation": result,
                "activation": activation,
            }
            readiness_sha256 = sha256(
                json.dumps(evidence_payload, sort_keys=True).encode("utf-8")
            ).hexdigest()
            ready = _ReadyDataplaneBoundary(
                boundary,
                foundation_token,
                predecessor_time,
                expected_active_generation,
                generation,
                state,
                request,
                activation,
                generation_keys,
                tuple(sorted(new_resources, key=_connection_key)),
                readiness_sha256,
                result,
            )
            with self._lock:
                self._prepared_resources.update(new_resources)
                self._all_prepared_resources.update(new_resources)
                self._physical_resource_high_water = max(
                    self._physical_resource_high_water,
                    len(self._prepared_resources),
                )
                self._generation_keys[generation] = generation_keys
                self._realizations[generation] = ready
                self._latest_ready = ready
            return ready, {
                "generation": generation,
                "expected_active_generation": expected_active_generation,
                "readiness_sha256": readiness_sha256,
                "prepared_resources": len(ready.prepared_resources),
                "capacity_audit": capacity_audit,
            }
        finally:
            with self._lock:
                if self._preparing is pending:
                    self._preparing = None

    def activate(self, ready_value: object) -> dict[str, Any]:
        with self._lock:
            ready = self._require_ready(ready_value)
            actual_predecessor = (
                _NO_ACTIVE_GENERATION if self._active is None else self._active.generation
            )
            if ready.expected_active_generation != actual_predecessor:
                raise RuntimeError(
                    "tc-BPF runtime realization READY predecessor evidence is stale"
                )
        result = self._executor.execute("activate_boundary", ready.activation_request)
        with self._lock:
            self._active = ready
        return {**result, "capacity": self._capacity_snapshot()}

    def retirement_handles(
        self,
        prior_value: object,
        active_value: object,
    ) -> tuple[object, object | None]:
        """Describe private forwarding and physical retirement after activation."""

        with self._lock:
            prior = self._require_ready(prior_value)
            active = self._require_ready(active_value)
            if self._active is not active:
                raise RuntimeError("tc-BPF runtime realization ACTIVE state is stale")
            protected = {
                resource
                for generation, realization in self._realizations.items()
                if generation > active.generation
                for resource in realization.state.resources
            }
            if self._preparing is not None:
                protected.update(self._preparing.state.resources)
            retired = tuple(
                sorted(
                    set(prior.state.resources) - set(active.state.resources) - protected,
                    key=_connection_key,
                )
            )
        return (
            _RetiredForwardingRealization(prior.generation),
            None if not retired else _RetiredPhysicalRealization(retired),
        )

    def reclaim_forwarding(self, retirement_value: object) -> dict[str, Any]:
        if not isinstance(retirement_value, _RetiredForwardingRealization):
            raise TypeError(
                "tc-BPF runtime realization forwarding retirement handle is invalid"
            )
        generation = retirement_value.generation
        with self._lock:
            keys = self._generation_keys.get(generation)
        if keys is None:
            return {"generation": generation, "deleted_entries": 0}
        result = self._executor.execute(
            "reclaim_generation",
            {
                "pin_root": self._pin_root,
                "generation": generation,
                "nodes": self._nodes,
                "generation_keys": [
                    {"key": key, "active_interface_indexes": active, "route_keys": routes}
                    for key, active, routes in keys
                ],
            },
        )
        with self._lock:
            self._generation_keys.pop(generation, None)
            self._realizations.pop(generation, None)
        return {**result, "capacity": self._capacity_snapshot()}

    def generation_capacity_status(self) -> dict[str, Any]:
        """Return private fixed-map residency facts for Maintenance scheduling."""

        with self._lock:
            resident = len(self._generation_keys)
            reserved = int(self._preparing is not None)
            capacity = self._max_resident_generations
        return {
            "resident_generations": resident,
            "reserved_in_flight_preparations": reserved,
            "resident_generation_capacity": capacity,
            "can_start_preparation": reserved == 0 and resident < capacity,
        }

    def reclaim_physical(self, retirement_value: object) -> dict[str, Any]:
        if not isinstance(retirement_value, _RetiredPhysicalRealization):
            raise TypeError(
                "tc-BPF runtime realization physical retirement handle is invalid"
            )
        resources = retirement_value.resources
        with self._lock:
            missing = tuple(
                value for value in resources if value not in self._prepared_resources
            )
        if missing:
            raise RuntimeError(
                "formal physical reclaim lost Runner realization ownership evidence"
            )
        result = self._executor.execute(
            "reclaim_resources",
            {"nodes": self._nodes, "connections": [self._connection_identity(value) for value in resources]},
        )
        if int(result.get("deleted_connections", -1)) != len(resources):
            raise RuntimeError(
                "formal physical reclaim did not delete every owned resource"
            )
        with self._lock:
            self._prepared_resources.difference_update(resources)
            self._all_prepared_resources.difference_update(resources)
        return {**result, "capacity": self._capacity_snapshot()}

    def _require_pending(self, value: object) -> _PendingDataplaneBoundary:
        if not isinstance(value, _PendingDataplaneBoundary) or value is not self._preparing:
            raise RuntimeError(
                "tc-BPF runtime realization preparation reservation is stale"
            )
        return value

    def _require_ready(self, value: object) -> _ReadyDataplaneBoundary:
        if not isinstance(value, _ReadyDataplaneBoundary):
            raise TypeError("tc-BPF runtime READY realization is invalid")
        if self._realizations.get(value.generation) is not value:
            raise RuntimeError("tc-BPF runtime READY realization is stale")
        return value

    def cleanup(self) -> dict[str, Any]:
        if not self._initialized:
            return {"deleted_connections": 0, "pin_root_removed": True, "errors": []}
        try:
            return self._executor.execute(
                "cleanup",
                {
                    "pin_root": self._pin_root,
                    "nodes": [
                        {
                            **node,
                            "node_address_owned": next(
                                node_id
                                for node_id in self._bindings
                                if node_id.value == node["key"]
                            )
                            in self._owned_node_addresses,
                            "zone_addresses": sorted(
                                self._all_zone_addresses[
                                    next(
                                        node_id
                                        for node_id in self._bindings
                                        if node_id.value == node["key"]
                                    )
                                ]
                            ),
                        }
                        for node in self._nodes
                    ],
                    "connections": [
                        self._connection_identity(value)
                        for value in sorted(self._all_prepared_resources, key=_connection_key)
                    ],
                },
            )
        finally:
            self._initialized = False
            self._telemetry_kernel.clear()
            self._prepared_resources.clear()
            self._all_prepared_resources.clear()
            self._generation_keys.clear()
            self._map_capacities.clear()
            self._capacity_plan.clear()
            self._physical_resource_high_water = 0
            self._active = None
            self._latest_ready = None
            self._preparing = None
            self._realizations.clear()
            self._all_zone_addresses.clear()
            self._owned_node_addresses.clear()

    def _initial_capacity_plan(
        self,
        boundaries: tuple[_NominalBoundary, ...],
        *,
        preparation_lookahead: timedelta | None,
        preparation_boundary_depth: int | None,
        zone_count: int,
    ) -> dict[str, Any]:
        if not boundaries:
            raise _CapacityAuditError(
                "Runner capacity audit requires known nominal boundaries"
            )
        states = []
        state = _empty_state()
        generation_nodes = []
        known_resources: set[ConnectionId] = set()
        for boundary in boundaries:
            state = _materialize_boundary(state, boundary)
            states.append(state)
            generation_nodes.append(self._generation_nodes(state))
            known_resources.update(state.resources)

        demands = [
            {
                node["key"]: (
                    len(node["active_interfaces"]),
                    len(node["routes"]),
                )
                for node in nodes
            }
            for nodes in generation_nodes
        ]
        rolling = preparation_lookahead is not None
        if rolling:
            if isinstance(preparation_boundary_depth, bool) or not isinstance(
                preparation_boundary_depth, int
            ):
                raise _CapacityAuditError(
                    "rolling capacity requires a positive preparation boundary depth"
                )
            if preparation_boundary_depth <= 0:
                raise _CapacityAuditError(
                    "rolling capacity requires a positive preparation boundary depth"
                )
            resident_generation_capacity = preparation_boundary_depth + 1
        else:
            if preparation_boundary_depth is not None:
                raise _CapacityAuditError(
                    "full-range capacity requires no preparation boundary depth"
                )
            resident_generation_capacity = len(states)

        satellite_count = sum(
            node_id.entity_kind == "satellite" for node_id in self._bindings
        )
        ground_station_count = sum(
            node_id.entity_kind == "ground-station" for node_id in self._bindings
        )
        topology_node_count = len(self._bindings)
        per_node = []
        for node in self._nodes:
            key = node["key"]
            node_id = next(
                value for value in self._bindings if value.value == key
            )
            if rolling:
                if node_id.entity_kind == "ground-station":
                    per_generation_active_bound = satellite_count
                    per_generation_route_bound = 1 + zone_count
                elif node_id.entity_kind == "satellite":
                    per_generation_active_bound = (
                        satellite_count - 1 + ground_station_count
                    )
                    per_generation_route_bound = (
                        1 + ground_station_count + zone_count
                    )
                else:
                    per_generation_active_bound = max(0, topology_node_count - 1)
                    per_generation_route_bound = (
                        1 + topology_node_count + zone_count
                    )
                required_active = (
                    per_generation_active_bound * resident_generation_capacity
                )
                required_routes = (
                    per_generation_route_bound * resident_generation_capacity
                )
                capacity_basis = "topology-upper-bound-times-resident-capacity"
            else:
                per_generation_active_bound = max(
                    value[key][0] for value in demands
                )
                per_generation_route_bound = max(
                    value[key][1] for value in demands
                )
                required_active = sum(value[key][0] for value in demands)
                required_routes = sum(value[key][1] for value in demands)
                capacity_basis = "bounded-full-range-exact-demand"
            active_capacity = max(1, required_active)
            route_capacity = max(1, required_routes)
            if (
                active_capacity > _MAX_BPF_MAP_ENTRIES
                or route_capacity > _MAX_BPF_MAP_ENTRIES
            ):
                raise _CapacityAuditError(
                    "calculated BPF map capacity exceeds the u32 max_entries limit; "
                    f"node={key}, active_links={active_capacity}, routes={route_capacity}"
                )
            per_node.append(
                {
                    "key": key,
                    "capacity_basis": capacity_basis,
                    "per_generation_active_link_upper_bound": (
                        per_generation_active_bound
                    ),
                    "per_generation_route_upper_bound": (
                        per_generation_route_bound
                    ),
                    "required_active_link_entries": required_active,
                    "configured_active_link_entries": active_capacity,
                    "required_route_entries": required_routes,
                    "configured_route_entries": route_capacity,
                }
            )

        interfaces_by_node = {node["key"]: 0 for node in self._nodes}
        self._assert_unique_resource_bindings(known_resources)
        for connection_id in known_resources:
            interfaces_by_node[connection_id.endpoint_a.value] += 1
            interfaces_by_node[connection_id.endpoint_b.value] += 1
        return {
            "mode": "bounded-full-range" if not rolling else "rolling",
            "known_generation_count": len(states),
            "resident_generation_capacity": resident_generation_capacity,
            "preparation_boundary_depth": preparation_boundary_depth,
            "lookahead": (
                "all" if preparation_lookahead is None else str(preparation_lookahead)
            ),
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
            "known_physical_resource_high_water": len(known_resources),
            "known_interface_binding_high_water": len(known_resources) * 2,
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
                {"key": key, "interfaces": interfaces_by_node[key]}
                for key in sorted(interfaces_by_node)
            ],
            "pinned_object_count": 1 + 3 * len(self._nodes),
        }

    def _audit_generation_capacity(
        self,
        generation: int,
        *,
        generation_nodes: list[dict[str, Any]],
        state: _MaterializedDataplaneState,
    ) -> dict[str, Any]:
        with self._lock:
            if generation in self._generation_keys:
                raise _CapacityAuditError(
                    f"generation {generation} is already resident"
                )
            resident = dict(self._generation_keys)
            prepared_resources = set(self._prepared_resources)
            reserved = int(self._preparing is not None)
            if len(resident) + reserved > self._max_resident_generations:
                raise _CapacityAuditError(
                    "resident generation invariant exceeded before READY: "
                    f"resident={len(resident)}, reserved_in_flight={reserved}, "
                    f"capacity={self._max_resident_generations}"
                )
        used = {key: [0, 0] for key in self._map_capacities}
        for keys in resident.values():
            for key, active, routes in keys:
                used[key][0] += len(active)
                used[key][1] += len(routes)
        per_node = []
        failures = []
        for node in generation_nodes:
            key = node["key"]
            active_capacity, route_capacity = self._map_capacities[key]
            active_required = len(node["active_interfaces"])
            route_required = len(node["routes"])
            active_remaining = active_capacity - used[key][0] - active_required
            route_remaining = route_capacity - used[key][1] - route_required
            if active_remaining < 0:
                failures.append(
                    f"{key}: active_links requires {used[key][0] + active_required}, "
                    f"capacity {active_capacity}"
                )
            if route_remaining < 0:
                failures.append(
                    f"{key}: routes requires {used[key][1] + route_required}, "
                    f"capacity {route_capacity}"
                )
            per_node.append(
                {
                    "key": key,
                    "active_link_entries_before": used[key][0],
                    "active_link_entries_required": active_required,
                    "active_link_entries_capacity": active_capacity,
                    "active_link_entries_remaining": active_remaining,
                    "route_entries_before": used[key][1],
                    "route_entries_required": route_required,
                    "route_entries_capacity": route_capacity,
                    "route_entries_remaining": route_remaining,
                }
            )
        if failures:
            raise _CapacityAuditError(
                "future generation cannot be represented before READY: "
                + "; ".join(failures)
            )
        predicted_resources = prepared_resources | set(state.resources)
        self._assert_unique_resource_bindings(predicted_resources)
        return {
            "generation": generation,
            "resident_generations_before": len(resident),
            "resident_generations_after": len(resident) + 1,
            "reserved_in_flight_preparations": reserved,
            "resident_generation_capacity": self._max_resident_generations,
            "per_node": per_node,
            "minimum_active_link_headroom_after": min(
                value["active_link_entries_remaining"] for value in per_node
            ),
            "minimum_route_headroom_after": min(
                value["route_entries_remaining"] for value in per_node
            ),
            "physical_prepared_resources_before": len(prepared_resources),
            "physical_prepared_resources_after": len(predicted_resources),
        }

    @staticmethod
    def _assert_unique_resource_bindings(
        resources: set[ConnectionId],
    ) -> None:
        names: dict[str, ConnectionId] = {}
        for connection_id in resources:
            for name in _interface_names(connection_id):
                prior = names.setdefault(name, connection_id)
                if prior != connection_id:
                    raise _CapacityAuditError(
                        "Runner interface-name capacity collision before READY: "
                        f"{name} identifies both {_connection_key(prior)} and "
                        f"{_connection_key(connection_id)}"
                    )

    def _capacity_snapshot(self) -> dict[str, Any]:
        with self._lock:
            resident = dict(self._generation_keys)
            prepared_resources = len(self._prepared_resources)
            high_water = self._physical_resource_high_water
        used = {key: [0, 0] for key in self._map_capacities}
        for keys in resident.values():
            for key, active, routes in keys:
                used[key][0] += len(active)
                used[key][1] += len(routes)
        per_node = []
        for key in sorted(self._map_capacities):
            active_capacity, route_capacity = self._map_capacities[key]
            per_node.append(
                {
                    "key": key,
                    "active_link_entries": used[key][0],
                    "active_link_capacity": active_capacity,
                    "active_link_headroom": active_capacity - used[key][0],
                    "route_entries": used[key][1],
                    "route_capacity": route_capacity,
                    "route_headroom": route_capacity - used[key][1],
                }
            )
        return {
            "resident_generations": len(resident),
            "reserved_in_flight_preparations": int(self._preparing is not None),
            "resident_generation_capacity": self._max_resident_generations,
            "physical_prepared_resources": prepared_resources,
            "physical_prepared_resource_high_water": high_water,
            "minimum_active_link_headroom": min(
                (value["active_link_headroom"] for value in per_node),
                default=0,
            ),
            "minimum_route_headroom": min(
                (value["route_headroom"] for value in per_node),
                default=0,
            ),
            "per_node": per_node,
        }

    def _connection_identity(self, connection_id: ConnectionId) -> dict[str, Any]:
        interface_a, interface_b = _interface_names(connection_id)
        return {
            "connection": _connection_key(connection_id),
            "node_a": connection_id.endpoint_a.value,
            "node_b": connection_id.endpoint_b.value,
            "interface_a": interface_a,
            "interface_b": interface_b,
        }

    def _connection_request(
        self,
        connection_id: ConnectionId,
        parameters: Mapping[tuple[ConnectionId, ConnectionDirection], _QdiscValue],
    ) -> dict[str, Any]:
        value = self._connection_identity(connection_id)
        for suffix, direction in (
            ("a", ConnectionDirection.ENDPOINT_A_TO_B),
            ("b", ConnectionDirection.ENDPOINT_B_TO_A),
        ):
            parameter = parameters[(connection_id, direction)]
            value[f"parameters_{suffix}"] = {
                "bandwidth_bps": parameter.bandwidth_bps,
                "delay_us": parameter.delay_us,
                "packet_loss_ppm": parameter.packet_loss_ppm,
            }
        return value

    def _interfaces(self, state: _MaterializedDataplaneState) -> dict[NodeId, list[str]]:
        result = {node_id: [] for node_id in self._bindings}
        for connection_id in state.resources:
            interface_a, interface_b = _interface_names(connection_id)
            result[connection_id.endpoint_a].append(interface_a)
            result[connection_id.endpoint_b].append(interface_b)
        return result

    def _routes(
        self, state: _MaterializedDataplaneState
    ) -> dict[NodeId, dict[str, str | None]]:
        result: dict[NodeId, dict[str, str | None]] = {
            node_id: {self._node_addresses[node_id]: None} for node_id in self._bindings
        }
        for edge in state.zone_edges:
            result[edge.service_satellite_id][str(ip_interface(edge.packet_prefix).ip)] = None
        for binding in state.forwarding:
            source, next_hop = _direction_endpoints(binding.connection_id, binding.direction)
            if source != binding.route_key.source or next_hop != binding.next_hop:
                raise ValueError("compiled forwarding contradicts its direction")
            target = binding.route_key.target
            if isinstance(target, GroundStationRouteTarget):
                destination = self._node_addresses[target.ground_station_id]
            elif isinstance(target, ZoneRouteTarget):
                if binding.packet_prefix is None:
                    raise ValueError("compiled Zone forwarding lacks packet identity")
                destination = str(ip_interface(binding.packet_prefix).ip)
            else:  # pragma: no cover
                raise TypeError("compiled forwarding has an unknown target")
            interface_a, interface_b = _interface_names(binding.connection_id)
            interface = interface_a if source == binding.connection_id.endpoint_a else interface_b
            prior = result[source].setdefault(destination, interface)
            if prior != interface:
                raise ValueError("compiled forwarding has conflicting destinations")
        return result

    def _ground_station_access_peer_routes(
        self, state: _MaterializedDataplaneState
    ) -> dict[NodeId, dict[str, str]]:
        """Derive G-side direct-peer routes from ACTIVE access resources."""

        result: dict[NodeId, dict[str, str]] = {
            node_id: {} for node_id in self._bindings
        }
        for connection_id in state.resources:
            if connection_id.kind is not ConnectionKind.GROUND_STATION_ACCESS:
                continue
            ground_station, satellite = _ground_station_access_endpoints(
                connection_id
            )
            names = _interface_names(connection_id)
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

    def _generation_nodes(
        self, state: _MaterializedDataplaneState
    ) -> list[dict[str, Any]]:
        interfaces = self._interfaces(state)
        routes = self._routes(state)
        by_key = {node["key"]: node for node in self._nodes}
        return [
            {
                **by_key[node_id.value],
                "active_interfaces": sorted(interfaces[node_id]),
                "routes": [
                    {"destination": destination, "interface": interface}
                    for destination, interface in sorted(routes[node_id].items())
                ],
            }
            for node_id in sorted(self._bindings, key=_node_key)
        ]

    def _activation_request(
        self,
        boundary: _NominalBoundary,
        *,
        previous_state: _MaterializedDataplaneState,
        state: _MaterializedDataplaneState,
        expected_active_generation: int,
        generation: int,
    ) -> dict[str, Any]:
        old_resources = set(previous_state.resources)
        new_resources = set(state.resources)
        old_routes = self._routes(previous_state)
        new_routes = self._routes(state)
        old_access_peers = self._ground_station_access_peer_routes(previous_state)
        new_access_peers = self._ground_station_access_peer_routes(state)
        old_edges = {value.zone_id: value for value in previous_state.zone_edges}
        new_edges = {value.zone_id: value for value in state.zone_edges}
        parameter_state = {
            (value.connection_id, value.direction): value for value in state.parameters
        }
        parameter_changes = []
        for operation in boundary.operations:
            intent = operation.intent
            if not isinstance(intent, _QdiscIntent) or intent.connection_id not in old_resources:
                continue
            value = parameter_state[(intent.connection_id, intent.direction)]
            source, _ = _direction_endpoints(value.connection_id, value.direction)
            names = _interface_names(value.connection_id)
            interface = names[0] if source == value.connection_id.endpoint_a else names[1]
            parameter_changes.append(
                {
                    "node": source.value,
                    "interface": interface,
                    "bandwidth_bps": value.bandwidth_bps,
                    "delay_us": value.delay_us,
                    "packet_loss_ppm": value.packet_loss_ppm,
                }
            )
        nodes = []
        by_key = {node["key"]: node for node in self._nodes}
        for node_id in sorted(self._bindings, key=_node_key):
            route_deletes = [
                {"destination": destination, "interface": interface}
                for destination, interface in sorted(old_routes[node_id].items())
                if interface is not None and new_routes[node_id].get(destination) != interface
            ]
            route_replaces = [
                {"destination": destination, "interface": interface}
                for destination, interface in sorted(new_routes[node_id].items())
                if interface is not None and old_routes[node_id].get(destination) != interface
            ]
            access_peer_route_deletes = [
                {"destination": destination, "interface": interface}
                for destination, interface in sorted(
                    old_access_peers[node_id].items()
                )
                if new_access_peers[node_id].get(destination) != interface
            ]
            access_peer_route_replaces = [
                {"destination": destination, "interface": interface}
                for destination, interface in sorted(
                    new_access_peers[node_id].items()
                )
                if old_access_peers[node_id].get(destination) != interface
            ]
            zone_delete = []
            zone_add = []
            for zone_id in set(old_edges) | set(new_edges):
                old = old_edges.get(zone_id)
                new = new_edges.get(zone_id)
                if old == new:
                    continue
                if old is not None and old.service_satellite_id == node_id:
                    zone_delete.append(str(ip_interface(old.packet_prefix).ip))
                if new is not None and new.service_satellite_id == node_id:
                    zone_add.append(str(ip_interface(new.packet_prefix).ip))
            nodes.append(
                {
                    **by_key[node_id.value],
                    "link_up": sorted(
                        name
                        for connection_id in new_resources - old_resources
                        for endpoint, name in zip(
                            (connection_id.endpoint_a, connection_id.endpoint_b),
                            _interface_names(connection_id),
                        )
                        if endpoint == node_id
                    ),
                    "link_down": sorted(
                        name
                        for connection_id in old_resources - new_resources
                        for endpoint, name in zip(
                            (connection_id.endpoint_a, connection_id.endpoint_b),
                            _interface_names(connection_id),
                        )
                        if endpoint == node_id
                    ),
                    "qdisc": [value for value in parameter_changes if value["node"] == node_id.value],
                    "route_delete": route_deletes,
                    "route_replace": route_replaces,
                    "access_peer_route_delete": access_peer_route_deletes,
                    "access_peer_route_replace": access_peer_route_replaces,
                    "zone_address_delete": sorted(set(zone_delete)),
                    "zone_address_add": sorted(set(zone_add)),
                }
            )
        return {
            "pin_root": self._pin_root,
            "common_mac": _COMMON_MAC,
            "boundary_timestamp": boundary.nominal_time.isoformat(),
            "expected_active_generation": expected_active_generation,
            "generation": generation,
            "nodes": nodes,
        }


__all__: list[str] = []
