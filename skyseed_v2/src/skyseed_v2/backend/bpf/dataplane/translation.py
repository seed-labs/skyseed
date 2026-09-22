"""Incremental pure translation from BPF intentions to executable deltas."""

from __future__ import annotations

from skyseed_v2.backend.execution_materials.actions import _intent_phase
from .materials import _RETIREMENT_GROUP_BASE

from collections import defaultdict
from dataclasses import dataclass, field
from ipaddress import ip_interface
from typing import Any, Mapping
from skyseed_v2.shared import NodeId
from skyseed_v2.simulator import (
    GroundStationRouteDemandTarget, SatelliteRouteDemandTarget,
    OperationalConnectionId,
    ZoneRouteDemandTarget,
)
from skyseed_v2.backend.execution_materials.actions import (
    _ForwardingIntent,
    _QdiscIntent,
    _ZoneEdgeState,
)
from skyseed_v2.backend.execution_materials.mechanics import (
    _CapacityAuditError,
    _MaterializedDataplaneState,
    _node_key,
    _connection_key,
    _component,
    _cached_interface_names,
    _interface_name_for_source,
    _empty_state,
    _allocate_node_addresses,
    _BoundaryStateIndexes,
)


_COMMON_MAC = "02:53:4b:59:00:01"
_NO_ACTIVE_GENERATION = 0xFFFFFFFF

@dataclass(frozen=True, slots=True)
class _DerivedQdiscOperation:
    interface: str
    bandwidth_bps: int
    delay_us: int
    packet_loss_ppm: int


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
    zone_address_delete: tuple[str, ...]
    zone_address_add: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _BoundaryBuild:
    """Batch preparation scratch state; never passed to formal Execution."""

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
    generation_by_node: Mapping[NodeId, _DerivedGenerationNode] = field(default_factory=dict)
    operation_nodes: tuple[_DerivedOperationNode, ...] = ()
    routes: Mapping[NodeId, dict[str, str | None]] = field(default_factory=dict)


def _validate_boundary_lowering(boundary: _NominalBoundary) -> int:
    """Prove explicit edges survive the private phased Execution lowering."""

    if not boundary.constraint_edges:
        return 0
    operations = {value.reference: value.intent for value in boundary.operations}

    folded_forwarding_edges = 0
    for predecessor, successor, _ in boundary.constraint_edges:
        predecessor_intent = operations[predecessor]
        successor_intent = operations[successor]
        predecessor_phase = _intent_phase(predecessor_intent)
        successor_phase = _intent_phase(successor_intent)
        if predecessor_phase > successor_phase:
            raise ValueError("compiled constraint is incompatible with Execution phases")
        if predecessor_phase == successor_phase:
            if not (
                isinstance(predecessor_intent, _ForwardingIntent)
                and isinstance(successor_intent, _ForwardingIntent)
            ):
                raise ValueError("Execution batch would erase a non-forwarding constraint")
            folded_forwarding_edges += 1
    return folded_forwarding_edges


class _BpfTranslation:
    """Calculation cursor; never passed to Runner or imported by its adapter."""

    def __init__(self, projection, zone_prefixes, *, continued=False, dataplane_program=None):
        self._node_ids = tuple(sorted(projection.node_ids, key=_node_key))
        self._node_addresses, self._node_address_range = _allocate_node_addresses(self._node_ids, zone_prefixes)
        self._route_destinations = {(node, address): {None: 1} for node, address in self._node_addresses.items()}
        self._connection_names = {}
        self._packet_addresses = {}
        self._all_zone_addresses = {node: set() for node in self._node_ids}
        self._state_indexes = _BoundaryStateIndexes()
        self._previous = None
        self._versions = {}
        self._encoded = {}
        self._value_cache = {}
        self._definitions = {}
        self._port_by_interface = {}
        self._foundation = None
        self._dataplane_program = dataplane_program
        self._continued = continued
        self._retirement_active = {}
        self._retirement_cohorts = []
        self._retirement_serial = 0
        self.begin_delivery()

    def begin_delivery(self):
        self._retirement_bindings = {}
        self._retirement_batches = {}
        self._retirement_ordinal = 0

    def retirements(self):
        # Finite cohorts are fully known at the delivery seal. Continued lifetimes
        # use the stable logical-port group chosen at creation.
        if not self._continued:
            self._assign_retirement_cohort([(value, None) for value in self._retirement_active.values()],
                                           _RETIREMENT_GROUP_BASE + self._retirement_ordinal + 1)
            for value in self._retirement_cohorts + list(self._retirement_active.values()):
                self._retirement_bindings[value["retirement_binding"]] = [value["retirement_node"], value["retirement_group"]]
        return {"bindings": self._retirement_bindings, "batches": self._retirement_batches}

    def _retirement_plan(self, request):
        self._retirement_ordinal += 1
        for value in request["connections"]:
            reference = str(self._retirement_serial)
            self._retirement_serial += 1
            value["retirement_binding"] = reference
            self._retirement_active[value["connection"]] = dict(value)
            if self._continued:
                self._retirement_bindings[reference] = [value["node_a"], _RETIREMENT_GROUP_BASE + value["logical_port_a"]]
        removed = request.pop("removed_connections")
        cohort = [(self._retirement_active.pop(value["connection"]), value) for value in removed]
        if self._continued:
            batches = [{"key": value["node_a"], "group": _RETIREMENT_GROUP_BASE + value["logical_port_a"], "connections": [value]} for value in removed]
        else:
            batches = self._assign_retirement_cohort(cohort, _RETIREMENT_GROUP_BASE + self._retirement_ordinal)
            self._retirement_cohorts.extend(created for created, _ in cohort)
        if batches:
            self._retirement_batches[request["boundary_timestamp"]] = batches


    def foundation(self):
        from .program import _prepare_program
        from .scopes import DataPlane
        from .materials import _DATAPLANE_CONTRACT
        from .data import RouteData
        from copy import deepcopy
        if self._foundation is not None:
            return self._foundation
        selection = self._dataplane_program
        explicit = isinstance(selection, DataPlane)
        bindings = selection.bindings if explicit else (None,)
        programs, node_programs = [], {n: None for n in self._node_ids}
        program_ids = {}
        for binding in bindings:
            selected = binding.program if explicit else selection
            if selected not in program_ids:
                index = len(programs)
                program_ids[selected] = index
                program = deepcopy(_prepare_program(selected))
                programs.append(program)
                spec = program['declaration'].get('data')
                self._definitions[index] = None if spec is None else RouteData.from_data(spec)
            index = program_ids[selected]
            for node in binding.scope.nodes if explicit else self._node_ids:
                if node not in node_programs:
                    raise ValueError('attachment node is foreign to the PhysicalWorld')
                if node_programs[node] is not None:
                    raise ValueError('conflicting DataPlane node selections')
                node_programs[node] = index
        self._foundation = {'contract': _DATAPLANE_CONTRACT, 'byteorder': 'little', 'programs': programs,
            'node_address_range': str(self._node_address_range),
            'nodes': [{'key': node.value, 'node_slot': slot, 'node_address': self._node_addresses[node],
                       'pin_component': _component('n-', node.value), 'program': node_programs[node]}
                      for slot, node in enumerate(self._node_ids)]}
        self._program_by_node = {n['key']: n['program'] for n in self._foundation['nodes']}
        return self._foundation

    def _encode_node(self, node, ports):
        import struct
        from ipaddress import IPv4Address
        from skyseed_v2.simulator import RouteDisposition
        active, routes = {}, {}
        previous_values = self._value_cache.get(node.key, {})
        current_values = {}
        def bind_exit(value):
            if value.source != node.node_id:
                raise ValueError('logical exit belongs to another node')
            return ports[node.key, _interface_name_for_source(value.connection, value.source, self._connection_names)]
        def bind_node(value):
            # Node references have the same network-order packet representation as route keys.
            return int.from_bytes(IPv4Address(self._node_addresses[value]).packed, 'little')
        index = self._program_by_node[node.key]
        if index is None:
            return active, routes
        declaration = self._foundation['programs'][index]['declaration']
        if not declaration['managed_tables']:
            return active, routes
        for name in node.active_interfaces:
            active[index, name] = struct.pack('<II', ports[node.key, name], 1)
        definition = self._definitions[index]
        for address, value in node.routes:
            identity = index, address
            old = previous_values.get(identity)
            if old is not None and old[0] == value:
                routes[identity] = old[1]
                current_values[identity] = old
                continue
            arrangement = getattr(value, 'arrangement', None)
            if arrangement is not None:
                interface = None
                if definition is not None:
                    encoded = definition.encode(arrangement, bind_exit=bind_exit, bind_node=bind_node)
                else:
                    if arrangement.data.fields or any(c.data.fields for c in arrangement.candidates) or len(arrangement.candidates) > 1:
                        raise ValueError('default program needs a custom RouteData/program for policy fields or multiple candidates')
                    disposition = arrangement.disposition
                    port = (0xfffffffd if disposition is RouteDisposition.PASS else
                            0xfffffffe if disposition is RouteDisposition.DROP or not arrangement.candidates else bind_exit(arrangement.candidates[0].exit))
                    encoded = struct.pack('<I', port)
            elif definition is None:
                interface = value
                encoded = struct.pack('<I', 0 if value is None else ports[node.key, value])
            else:
                # Custom structured programs receive only explicitly declared local arrangements.
                # A complete-path projection needs a schema-compatible local record, never invented fields.
                if value is not None:
                    raise ValueError('custom RouteData requires LocalRouteOutcome values; complete-path forwarding supplies no declared fields')
                continue
            routes[identity] = (interface, IPv4Address(address).packed + encoded)
            current_values[identity] = (value, routes[identity])
        self._value_cache[node.key] = current_values
        return active, routes

    def translate(self, boundary, logical_ports):
        from dataclasses import replace
        import struct
        from ipaddress import IPv4Address
        self.foundation()
        prior = self._previous
        draft = self._derive_boundary_plan(boundary, predecessor=prior,
            previous_state=_empty_state() if prior is None else prior.state,
            state_indexes=self._state_indexes)
        ports = self._port_by_interface
        for connection in draft.new_resources:
            pair = logical_ports[connection]
            for endpoint, name, port in zip((connection.endpoint_a, connection.endpoint_b),
                    _cached_interface_names(connection, self._connection_names), pair):
                ports[endpoint.value, name] = port
        request = self._activation_request(draft, expected_active_generation=draft.expected_active_generation,
                                          generation=draft.generation, generation_changed=draft.generation_changed)
        request["connections"] = [self._derived_connection_request(v) for v in draft.new_connection_requests]
        request["removed_connections"] = [self._connection_identity(v) for v in draft.removed_resources]
        request["last_completed_active_frontier"] = None if prior is None else prior.boundary.nominal_time.isoformat()
        for connection in (*request["connections"], *request["removed_connections"]):
            connection["logical_port_a"] = ports[connection["node_a"], connection["interface_a"]]
            connection["logical_port_b"] = ports[connection["node_b"], connection["interface_b"]]
        self._retirement_plan(request)
        patches = []
        for node in draft.generation_nodes if draft.generation_changed else ():
            old = self._versions.get(node.key)
            if old is not None and old[0] is node:
                continue
            old_active, old_routes = self._encoded.get(node.key, ({}, {}))
            active, routes = self._encode_node(node, ports)
            # Logical changes outside covered resources do not create a new table version.
            if old is not None and active == old_active and routes == old_routes:
                self._versions[node.key] = node, old[1]
                continue
            base = None if old is None else old[1]
            patches.append({'key': node.key, 'base': base, 'version': draft.generation, 'prefix': struct.pack('<I', draft.generation),
                'active_delete': sorted(old_active.keys() - active.keys()),
                'active_put': [[i, name, data] for (i, name), data in sorted(active.items()) if old_active.get((i, name)) != data],
                'route_delete': sorted(old_routes.keys() - routes.keys()),
                'route_put': [[i, address, interface, data] for (i, address), (interface, data) in sorted(routes.items()) if old_routes.get((i, address)) != (interface, data)]})
            self._versions[node.key] = node, draft.generation
            self._encoded[node.key] = active, routes
        self.last_work = {
            "node_versions_prepared": len(patches),
            "node_versions_reused": len(draft.generation_nodes) - len(patches) if draft.generation_changed else 0,
            "active_entry_patches": sum(len(p["active_put"]) + len(p["active_delete"]) for p in patches),
            "route_entry_patches": sum(len(p["route_put"]) + len(p["route_delete"]) for p in patches),
            "action_nodes": len(request["operation_nodes"]),
        }
        self._previous = draft
        return replace(boundary, executable={"request": request, "node_patches": patches,
            "created": draft.new_resources, "removed": draft.removed_resources})

    def _delta_routes(self, previous, forwarding_changes, zone_changes, indexes):
        """Update only changed destinations, retaining shared-binding conflicts."""
        if not forwarding_changes and not zone_changes:
            return previous
        touched = set()
        def address(prefix):
            if prefix not in self._packet_addresses:
                self._packet_addresses[prefix] = str(ip_interface(prefix).ip)
            return self._packet_addresses[prefix]
        def entry(value):
            if isinstance(value, _ZoneEdgeState):
                return value.service_satellite_id, address(value.packet_prefix), None
            source, peer, connection = value.route_key.source, value.next_hop, value.connection_id
            if value.arrangement is None and not ((source == connection.endpoint_a and peer == connection.endpoint_b) or
                    (source == connection.endpoint_b and peer == connection.endpoint_a)):
                raise ValueError('compiled forwarding contradicts its Connection')
            target = value.route_key.target
            if isinstance(target, GroundStationRouteDemandTarget):
                destination = self._node_addresses[target.ground_station_id]
            elif isinstance(target, SatelliteRouteDemandTarget):
                destination = self._node_addresses[target.satellite_id]
            elif isinstance(target, ZoneRouteDemandTarget):
                if value.packet_prefix is None:
                    raise ValueError('compiled Zone forwarding lacks packet identity')
                destination = address(value.packet_prefix)
            else:
                raise TypeError('compiled forwarding has an unknown target')
            return source, destination, (value if value.arrangement is not None else _interface_name_for_source(connection, source, self._connection_names))
        def change(value, amount):
            node, destination, interface = entry(value)
            key = node, destination
            touched.add(key)
            counts = self._route_destinations.setdefault(key, {})
            count = counts.get(interface, 0) + amount
            if count < 0:
                raise ValueError('compiled route reference count is inconsistent')
            if count:
                counts[interface] = count
            else:
                del counts[interface]
            if not counts:
                del self._route_destinations[key]
        for changes in (forwarding_changes, zone_changes):
            for value in changes.values():
                if value is not None:
                    change(value, -1)
        for changes, current in ((forwarding_changes, indexes.forwarding), (zone_changes, indexes.zone_edges)):
            for key in changes:
                if key in current:
                    change(current[key], 1)
        result = previous
        changed_nodes = set()
        absent = object()
        for node, destination in touched:
            counts = self._route_destinations.get((node, destination), {})
            if any(getattr(v, "arrangement", None) is not None for v in counts):
                counts = {v: n for v, n in counts.items() if v is not None}
            if len(counts) > 1:
                raise ValueError('compiled forwarding has conflicting destinations')
            interface = next(iter(counts), absent)
            old = previous[node].get(destination, absent)
            if old == interface:
                continue
            if node not in changed_nodes:
                if result is previous:
                    result = dict(previous)
                changed_nodes.add(node)
                result[node] = dict(previous[node])
            if interface is absent:
                result[node].pop(destination, None)
            else:
                result[node][destination] = interface
        return result

    def _derive_boundary_plan(
        self,
        boundary: _NominalBoundary,
        *,
        predecessor: _BoundaryBuild | None,
        previous_state: _MaterializedDataplaneState,
        state_indexes: _BoundaryStateIndexes,
    ) -> _BoundaryBuild:
        """Derive a finite plan only during complete batch preparation."""

        _validate_boundary_lowering(boundary)
        resource_changes, forwarding_changes, zone_changes = state_indexes.apply(boundary)
        state = state_indexes.snapshot(ordered=False)
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
        added_resources = tuple(sorted((key for key, prior in resource_changes.items() if prior is None), key=_connection_key))
        removed_resources = tuple(sorted((key for key, prior in resource_changes.items() if prior is not None), key=_connection_key))
        prior_nodes = {} if predecessor is None else predecessor.generation_by_node
        new_interfaces = {}
        old_routes = (predecessor.routes if predecessor is not None else
                      {node: {self._node_addresses[node]: None} for node in node_ids})
        new_routes = self._delta_routes(old_routes, forwarding_changes, zone_changes, state_indexes)
        added_resource_set = set(added_resources)
        touched_interfaces = {}
        for connections, added in ((removed_resources, False), (added_resources, True)):
            for connection in connections:
                for endpoint, name in zip((connection.endpoint_a, connection.endpoint_b),
                                           _cached_interface_names(connection, self._connection_names)):
                    if endpoint not in touched_interfaces:
                        touched_interfaces[endpoint] = set(prior_nodes[endpoint].active_interfaces if endpoint in prior_nodes else ())
                    if added:
                        touched_interfaces[endpoint].add(name)
                    else:
                        touched_interfaces[endpoint].remove(name)
        for node, names in touched_interfaces.items():
            new_interfaces[node] = tuple(sorted(names))
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
        # Only affected nodes and operation kinds allocate collections.
        mutable = defaultdict(lambda: defaultdict(list))
        for zone_id, old in zone_changes.items():
            new = state_indexes.zone_edges.get(zone_id)
            if old is not None:
                mutable[old.service_satellite_id][
                    "zone_address_delete"
                ].append(str(ip_interface(old.packet_prefix).ip))
            if new is not None:
                address = self._packet_addresses[new.packet_prefix]
                mutable[new.service_satellite_id]['zone_address_add'].append(address)
                self._all_zone_addresses[new.service_satellite_id].add(address)
        for operation in boundary.operations:
            intent = operation.intent
            if (
                not isinstance(intent, _QdiscIntent)
                or intent.connection_id in added_resource_set
                or intent.connection_id not in state_indexes.resources
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
        if not generation_changed:
            generation_nodes = predecessor.generation_nodes
        else:
            generation_nodes = tuple(
                prior_nodes[node_id]
                if node_id in prior_nodes
                and new_interfaces.get(node_id, prior_nodes[node_id].active_interfaces) == prior_nodes[node_id].active_interfaces
                and new_routes[node_id] is old_routes[node_id]
                else _DerivedGenerationNode(
                    node_id=node_id,
                    key=node_id.value,
                    active_interfaces=new_interfaces.get(node_id, prior_nodes[node_id].active_interfaces if node_id in prior_nodes else ()),
                    routes=(prior_nodes[node_id].routes
                            if node_id in prior_nodes and new_routes[node_id] is old_routes[node_id]
                            else tuple(sorted(new_routes[node_id].items()))),
                )
                for node_id in node_ids
            )
        operation_nodes = []
        for node_id in sorted(mutable, key=_node_key):
            values = mutable[node_id]
            operation_nodes.append(
                _DerivedOperationNode(
                    node_id=node_id,
                    key=node_id.value,
                    qdisc=tuple(
                        sorted(values.get("qdisc", ()), key=lambda value: value.interface)
                    ),
                    zone_address_delete=tuple(
                        sorted(set(values.get("zone_address_delete", ())))
                    ),
                    zone_address_add=tuple(
                        sorted(set(values.get("zone_address_add", ())))
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
            generation_by_node=(predecessor.generation_by_node if not generation_changed
                                else {node.node_id: node for node in generation_nodes}),
            operation_nodes=tuple(operation_nodes),
            routes=new_routes,
        )

    def _activation_request(
        self, draft: _BoundaryBuild, *, expected_active_generation: int,
        generation: int, generation_changed: bool = True,
    ) -> dict[str, Any]:
        operation_nodes = []
        for node in draft.operation_nodes:
            operation_nodes.append({
                "key": node.key,
                "qdisc": [{"interface": value.interface, "bandwidth_bps": value.bandwidth_bps,
                           "delay_us": value.delay_us, "packet_loss_ppm": value.packet_loss_ppm}
                          for value in node.qdisc],
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
