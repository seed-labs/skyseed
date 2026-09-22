"""Bind sealed native deliveries; lifecycle and timing belong to the shared Runner."""
from dataclasses import dataclass
from ipaddress import IPv4Network
import json
from time import monotonic_ns
from .materials import _validate_foundation, _IPv4Ranges


@dataclass(frozen=True, slots=True)
class _PreparedBoundary:
    nominal_time: object
    request: str
    ordinal: int
    connection_changes: tuple = ()


class _LinuxRuntime:
    def __init__(self, *, executor, bindings):
        self._executor = executor
        self._bindings = bindings
        self._addresses = {}
        self._cleanup_required = False
        self._prepared_ordinal = -1
        self._active_ordinal = -1
        self._planned_connections = {}
        self._allocations = {}
        self._names = {}
        self._active_zones = set()
        self._planned_routes = {}
        self._routes_by_connection = {}
        self._timings = {}
        self._occupied = _IPv4Ranges(())
        self._zone_addresses = frozenset()

    def initialize(self, projection, *, zone_prefixes, runtime_capacity_demand, dataplane):
        if set(projection.node_ids) != self._bindings.keys():
            raise ValueError("Linux runtime bindings differ from material nodes")
        self._foundation = _validate_foundation(dataplane, tuple(n.value for n in projection.node_ids))
        self._zone_addresses = frozenset(dataplane["zone_addresses"])
        by_key = {n["key"]: n for n in dataplane["nodes"]}
        self._address_by_key = {key: n["node_address"] for key, n in by_key.items()}
        self._addresses = {n: by_key[n.value]["node_address"] for n in projection.node_ids}
        nodes = [{**by_key[n.value], **{key: int(getattr(binding, key))
                  for key in ("pid", "process_start_time_ticks", "netns_inode")}}
                 for n, binding in self._bindings.items()]
        self._cleanup_required = True
        result = self._executor.execute("initialize", {"dataplane": dataplane, "nodes": nodes})
        occupied = [IPv4Network(p) for p in result.pop("occupied_ipv4", ())]
        occupied += [IPv4Network(dataplane["node_address_range"])]
        occupied += [IPv4Network(a + "/32") for a in dataplane["zone_addresses"]]
        self._occupied = _IPv4Ranges(occupied)
        return result

    def _audit_connection(self, c, network):
        identity = tuple(c[k] for k in ("node_a", "node_b", "interface_a", "interface_b", "address_a", "address_b"))
        old = self._allocations.get(c["connection"])
        if old is not None:
            if old != identity:
                raise ValueError("Linux connection identity/address changed across deliveries")
            return
        if self._occupied.overlap_end(network) is not None:
            raise ValueError(f"native link address {network} collides with static/identity IPv4 resources")
        for side in ("a", "b"):
            key = c[f"node_{side}"], c[f"interface_{side}"]
            if self._names.setdefault(key, c["connection"]) != c["connection"]:
                raise ValueError("Linux material interface identity collision")
        # /31 keys admit O(1) collision checks, including links reserved by future preparation.
        subnet_key = int(network.network_address)
        if self._names.setdefault(subnet_key, c["connection"]) != c["connection"]:
            raise ValueError("Linux material link address collision")
        self._allocations[c["connection"]] = identity

    def prepare_all(self, boundaries, *, work_checkpoint=None, progress=None, **unused):
        """Consume the artifact reader's validated boundaries; check live continuity."""
        plans = []
        decode_ns = 0
        link_networks = boundaries.native_link_networks
        for index in range(len(boundaries)):
            if work_checkpoint is not None:
                work_checkpoint()
            started = monotonic_ns()
            boundary = boundaries[index]
            decode_ns += monotonic_ns() - started
            request = boundary.executable["request"]
            if request["previous_ordinal"] != self._prepared_ordinal:
                raise ValueError("Linux prepared delivery is not a chronological suffix")
            retiring_zones = {(a["key"], a["address"]) for a in request["actions"] if a["op"] == "address_delete"}
            for action in request["actions"]:
                op = action["op"]
                if op in {"create", "remove"}:
                    c = action["connection"]
                    self._audit_connection(c, link_networks[c["address_a"], c["address_b"]])
                    if op == "create":
                        if c["connection"] in self._planned_connections:
                            raise ValueError("Linux material creates an active connection")
                        self._planned_connections[c["connection"]] = c
                    else:
                        if self._routes_by_connection.get(c["connection"]):
                            raise ValueError("Linux link retirement precedes owned route withdrawal")
                        if self._planned_connections.pop(c["connection"], None) is None:
                            raise ValueError("Linux material removes an inactive connection")
                elif op == "qdisc":
                    owner = self._names.get((action["key"], action["interface"]))
                    if owner not in self._planned_connections:
                        raise ValueError("Linux material updates parameters of an inactive link")
                elif op == "route":
                    previous, current = action["previous"], action["current"]
                    route = current if current is not None else previous
                    key = route["key"], route["destination"]
                    if self._planned_routes.get(key) != previous:
                        raise ValueError("Linux material route predecessor differs")
                    if current is not None:
                        if (current["destination"] == self._address_by_key[current["key"]]
                                or key in self._active_zones and key not in retiring_zones):
                            raise ValueError("Linux route cannot override a local destination")
                        if current["kind"] == "unicast":
                            c = self._planned_connections.get(current["connection"])
                            if c is None:
                                raise ValueError("Linux route uses an inactive connection")
                            side = "a" if c["node_a"] == current["key"] else "b"
                            peer = "b" if side == "a" else "a"
                            if (c["node_" + side] != current["key"] or c["interface_" + side] != current["interface"]
                                    or c["address_" + peer].split("/", 1)[0] != current["gateway"]):
                                raise ValueError("Linux route exit/gateway differs from its prepared connection")
                    if previous is not None and previous["kind"] == "unicast":
                        owners = self._routes_by_connection[previous["connection"]]
                        owners.remove(key)
                        if not owners:
                            del self._routes_by_connection[previous["connection"]]
                    if current is None:
                        self._planned_routes.pop(key)
                    else:
                        self._planned_routes[key] = current
                        if current["kind"] == "unicast":
                            self._routes_by_connection.setdefault(current["connection"], set()).add(key)
                else:
                    key = action["key"], action["address"]
                    if action["address"] not in self._zone_addresses:
                        raise ValueError("Linux material modifies an undeclared Zone identity")
                    if op == "address_add":
                        if key in self._active_zones:
                            raise ValueError("Linux material adds an active Zone address")
                        self._active_zones.add(key)
                    elif key not in self._active_zones:
                        raise ValueError("Linux material deletes an inactive Zone address")
                    else:
                        self._active_zones.remove(key)
            for action in request["actions"]:
                if action["op"] == "address_add" and (action["key"], action["address"]) in self._planned_routes:
                    raise ValueError("Linux route cannot override a newly local Zone destination")
            self._prepared_ordinal = request["ordinal"]
            plans.append(_PreparedBoundary(boundary.nominal_time, json.dumps(
                {"action": "activate_boundary", "request": request}, separators=(",", ":")) + "\n", self._prepared_ordinal,
                tuple({'op': a['op'], **{k: a['connection'][k] for k in
                    ('connection', 'node_a', 'node_b', 'interface_a', 'interface_b')}}
                    for a in request['actions'] if a['op'] in ('create', 'remove'))))
        self._timings = {"boundary_decode_seconds": decode_ns / 1e9, "network_mutations": 0}
        return tuple(plans)

    def activate(self, plan):
        if plan.ordinal != self._active_ordinal + 1:
            raise RuntimeError("Linux activation is out of order")
        try:
            result = self._executor.execute("activate_boundary", plan.request)
        except BaseException as error:
            error.evidence = getattr(error, "remote_evidence", None) or getattr(error, "evidence", {})
            raise
        if result.get("target_transition_complete") is not True:
            error = RuntimeError("Linux activation did not complete")
            error.evidence = result
            raise error
        self._active_ordinal = plan.ordinal
        result["connection_changes"] = plan.connection_changes
        return result

    def admit_boundary_count(self, count):
        pass

    def preparation_timings(self):
        return dict(self._timings)

    def reclaim(self):
        return {}

    def working_set_counts(self):
        return {"prepared_activation_ordinal": self._prepared_ordinal,
                "completed_activation_ordinal": self._active_ordinal,
                "connection_identities": len(self._allocations)}

    def runtime_node_addresses(self):
        return dict(self._addresses)

    def cleanup(self):
        if not self._cleanup_required:
            return {"cleanup_confirmed": True, "errors": []}
        result = self._executor.execute("cleanup", {})
        if result.get("cleanup_confirmed") is not True:
            raise RuntimeError("Linux cleanup did not confirm owned resource removal")
        self._cleanup_required = False
        self._planned_connections.clear()
        self._allocations.clear()
        self._names.clear()
        self._active_zones.clear()
        self._planned_routes.clear()
        self._routes_by_connection.clear()
        return result
