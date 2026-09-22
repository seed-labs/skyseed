"""Incremental compilation of supported Plan bindings into native IPv4 operations."""
from dataclasses import replace
from ipaddress import IPv4Address, IPv4Network, ip_interface
from skyseed_v2.backend.execution_materials.actions import (
    _ResourceIntent, _ResourceOperation, _QdiscIntent, _ZoneEdgeIntent, _ZoneEdgePhase, _intent_phase,
)
from skyseed_v2.backend.execution_materials.mechanics import (
    _node_key, _connection_key,
    _interface_name_for_source, _BoundaryStateIndexes,
)
from skyseed_v2.simulator import GroundStationRouteDemandTarget, SatelliteRouteDemandTarget, ZoneRouteDemandTarget, RouteDisposition
from .materials import _IPv4Ranges
from .environment import NativeRoutingEnvironment


class _LinuxTranslation:
    def __init__(self, projection, zone_prefixes):
        self._nodes = tuple(sorted(projection.node_ids, key=_node_key))
        environment = NativeRoutingEnvironment(self._nodes, zone_prefixes=zone_prefixes)
        self._environment = environment
        addresses, network = environment.node_addresses, environment.node_address_range
        self._foundation = {
            "contract": "skyseed-linux-ipv4/2", "route_protocol": environment.route_protocol, "route_priority": environment.route_priority, "node_address_range": str(network),
            "identity_interface": environment.identity_interface, "link_address_pools": list(environment.link_address_pools),
            "nodes": [{"key": n.value, "node_address": addresses[n]} for n in self._nodes],
            "zone_addresses": sorted(str(ip_interface(p).ip) for p in zone_prefixes),
        }
        self._excluded = _IPv4Ranges((network, *(ip_interface(p).network for p in zone_prefixes)))
        self._pools = iter(IPv4Network(p) for p in environment.link_address_pools)
        self._pool = next(self._pools)
        self._next_ip = int(self._pool.network_address)
        self._node_addresses = addresses
        self._packet_addresses = {}
        self._routes = {}
        self._names = {}
        self._used_names = {}
        self._connections = {}
        self._indexes = _BoundaryStateIndexes()
        self._ordinal = -1
        self.last_work = {}

    def foundation(self):
        return self._foundation

    def begin_delivery(self):
        pass

    def retirements(self):
        return {}

    def _allocate_link(self):
        while True:
            if self._next_ip + 1 > int(self._pool.broadcast_address):
                self._pool = next(self._pools, None)
                if self._pool is None:
                    raise ValueError("native IPv4 link address pools are exhausted")
                self._next_ip = int(self._pool.network_address)
            network = IPv4Network((self._next_ip, 31))
            self._next_ip += 2
            overlap = self._excluded.overlap_end(network)
            if overlap is not None:
                self._next_ip = (overlap + 2) & ~1
                continue
            return str(network.network_address) + "/31", str(network.broadcast_address) + "/31"

    def _connection(self, c):
        if c not in self._connections:
            names = self._names[c] = tuple(self._environment.interface_name(c, node)
                                          for node in (c.endpoint_a, c.endpoint_b))
            for node, name in zip((c.endpoint_a, c.endpoint_b), names):
                if self._used_names.setdefault((node, name), c) != c:
                    raise ValueError("managed interface name collision during native preparation")
            a, b = self._allocate_link()
            self._connections[c] = {
                "connection": _connection_key(c), "node_a": c.endpoint_a.value, "node_b": c.endpoint_b.value,
                "interface_a": names[0], "interface_b": names[1], "address_a": a, "address_b": b,
            }
        return self._connections[c]

    @staticmethod
    def _parameters(value):
        return {key: getattr(value, key) for key in ("bandwidth_bps", "delay_us", "packet_loss_ppm")}

    def translate(self, boundary, logical_ports):
        changes, forwarding, zones = self._indexes.apply(boundary)
        created = tuple(c for c, before in changes.items() if before is None)
        removed = tuple(c for c, before in changes.items() if before is not None)
        created_set = set(created)
        # Allocate newly established links in the same normalized first-appearance order,
        # before deriving gateways. This does not create any runtime resource.
        for operation in boundary.operations:
            intent = operation.intent
            if isinstance(intent, _ResourceIntent):
                self._connection(intent.connection_id)
        route_actions = self._route_changes(forwarding, zones)
        actions = []
        routes_inserted = False
        # Preserve the normalized dependency order. Creation includes both directions'
        # initial parameters; later parameter changes operate on the retained device.
        for operation in boundary.operations:
            intent = operation.intent
            if not routes_inserted and _intent_phase(intent) >= 3:
                actions.extend(route_actions)
                routes_inserted = True
            if isinstance(intent, _ResourceIntent):
                c = intent.connection_id
                record = self._connection(c)
                if intent.operation is _ResourceOperation.ESTABLISH:
                    record = {**record, **{f"parameters_{side}": self._parameters(self._indexes.parameters[c, source, target])
                        for side, source, target in (("a", c.endpoint_a, c.endpoint_b), ("b", c.endpoint_b, c.endpoint_a))}}
                    actions.append({"op": "create", "connection": record})
                else:
                    actions.append({"op": "remove", "connection": record})
            elif isinstance(intent, _QdiscIntent):
                c = intent.connection_id
                if c not in created_set and c in self._indexes.resources:
                    actions.append({"op": "qdisc", "key": intent.source.value,
                        "interface": _interface_name_for_source(c, intent.source, self._names),
                        "parameters": self._parameters(self._indexes.parameters[c, intent.source, intent.target])})
            elif isinstance(intent, _ZoneEdgeIntent):
                if intent.phase is _ZoneEdgePhase.READINESS:
                    actions.append({"op": "address_add", "key": intent.service_satellite_id.value,
                                    "address": str(ip_interface(intent.packet_prefix).ip)})
                elif intent.previous_service_satellite_id is not None:
                    actions.append({"op": "address_delete", "key": intent.previous_service_satellite_id.value,
                                    "address": str(ip_interface(intent.packet_prefix).ip)})
        if not routes_inserted:
            actions.extend(route_actions)
        self._ordinal += 1
        request = {"backend": "linux", "ordinal": self._ordinal, "previous_ordinal": self._ordinal - 1,
                   "boundary_timestamp": boundary.nominal_time.isoformat(), "actions": actions}
        self.last_work = {"native_actions": len(actions), "new_connections": len(created), "native_route_changes": len(route_actions)}
        return replace(boundary, executable={"request": request, "created": created, "removed": removed})

    def _destination(self, binding):
        target = binding.route_key.target
        if isinstance(target, GroundStationRouteDemandTarget):
            return self._node_addresses[target.ground_station_id]
        if isinstance(target, SatelliteRouteDemandTarget):
            return self._node_addresses[target.satellite_id]
        if isinstance(target, ZoneRouteDemandTarget):
            prefix = binding.packet_prefix
            if prefix not in self._packet_addresses:
                self._packet_addresses[prefix] = str(ip_interface(prefix).ip)
            return self._packet_addresses[prefix]
        raise ValueError("unsupported native route destination")

    def _route(self, binding):
        source = binding.route_key.source
        destination = self._destination(binding)
        key = source.value, destination
        arrangement = binding.arrangement
        if arrangement is not None:
            if arrangement.data.fields or any(c.data.fields for c in arrangement.candidates):
                raise ValueError("unsupported native route lowering: nonempty outcome/candidate PolicyRecord data")
            if arrangement.disposition is RouteDisposition.PASS:
                return key, None
            if arrangement.disposition is RouteDisposition.FORWARD and len(arrangement.candidates) != 1:
                raise ValueError("unsupported native route lowering: FORWARD requires exactly one unconditional candidate")
        target = binding.route_key.target
        local = destination == self._node_addresses[source]
        if isinstance(target, ZoneRouteDemandTarget):
            owner = self._indexes.zone_edges.get(target.service_key)
            local |= owner is not None and owner.service_satellite_id == source
        if local:
            raise ValueError("unsupported native route lowering: FORWARD/DROP cannot override a destination local to its source")
        if arrangement is None or arrangement.disposition is RouteDisposition.FORWARD:
            if arrangement is None:
                connection, peer = binding.connection_id, binding.next_hop
            else:
                exit = arrangement.candidates[0].exit
                connection, peer = exit.connection, exit.peer
            if connection not in self._indexes.resources or source not in (connection.endpoint_a, connection.endpoint_b):
                raise ValueError("native route references a non-operational logical exit")
            record = self._connections[connection]
            side, other = ("a", "b") if source == connection.endpoint_a else ("b", "a")
            if peer.value != record["node_" + other]:
                raise ValueError("native route peer differs from its logical exit")
            return key, {"connection": record["connection"], "destination": destination,
                         "gateway": record["address_" + other].split("/", 1)[0],
                         "interface": record["interface_" + side], "key": source.value,
                         "kind": "unicast", "prefsrc": self._node_addresses[source]}
        return key, {"destination": destination, "key": source.value, "kind": "blackhole"}

    def _route_changes(self, forwarding, zones):
        changes = {}
        # Shared compilation has already resolved demand ownership. Each supported
        # logical target has a unique native destination, so retain only its route.
        for logical_key, before in forwarding.items():
            binding = self._indexes.forwarding.get(logical_key)
            if binding == before:
                continue
            if binding is None:
                key, current = (logical_key.source.value, self._destination(before)), None
            else:
                key, current = self._route(binding)
            previous = self._routes.get(key)
            if current == previous:
                continue
            changes[key] = {"op": "route", "previous": previous, "current": current}
            if current is None:
                del self._routes[key]
            else:
                self._routes[key] = current
        # An unchanged arrangement can become unrepresentable when its destination
        # becomes local after a Zone move. Query only the newly local destinations.
        for service_key in zones:
            owner = self._indexes.zone_edges.get(service_key)
            if owner is not None:
                destination = str(ip_interface(owner.packet_prefix).ip)
                if (owner.service_satellite_id.value, destination) in self._routes:
                    raise ValueError("unsupported native route lowering: FORWARD/DROP cannot override a destination local to its source")
        return [changes[key] for key in sorted(changes)]
