"""Detached native IPv4 material contract; no BPF or compilation dependencies."""
from bisect import bisect_left
from ipaddress import IPv4Address, IPv4Network

_IDENTITY_INTERFACE = "sv2identity"
_LINK_POOLS = ("172.16.0.0/12", "10.0.0.0/8", "192.168.0.0/16", "100.64.0.0/10")
# Private framework route identity. Protocol is metadata, not preference. A fixed
# nonzero metric makes Linux's delete matcher exact (zero would be a wildcard).
_ROUTE_PROTOCOL = 253
_ROUTE_PRIORITY = 1


class _IPv4Ranges:
    """Compact fixed IPv4 exclusions once; query the first overlapping interval."""
    def __init__(self, networks):
        ranges = []
        for start, end in sorted((int(n.network_address), int(n.broadcast_address)) for n in networks):
            if ranges and start <= ranges[-1][1] + 1:
                ranges[-1] = ranges[-1][0], max(end, ranges[-1][1])
            else:
                ranges.append((start, end))
        self._starts = tuple(start for start, _ in ranges)
        self._ends = tuple(end for _, end in ranges)

    def overlap_end(self, network):
        index = bisect_left(self._ends, int(network.network_address))
        if index < len(self._starts) and self._starts[index] <= int(network.broadcast_address):
            return self._ends[index]
        return None


def _validate_foundation(value, node_keys):
    if (not isinstance(value, dict) or set(value) != {
            "contract", "nodes", "node_address_range", "identity_interface", "link_address_pools", "zone_addresses",
            "route_protocol", "route_priority"}
            or value["contract"] != "skyseed-linux-ipv4/2"
            or type(value["route_protocol"]) is not int or type(value["route_priority"]) is not int
            or value["route_protocol"] != _ROUTE_PROTOCOL or value["route_priority"] != _ROUTE_PRIORITY
            or value["identity_interface"] != _IDENTITY_INTERFACE
            or value["link_address_pools"] != list(_LINK_POOLS)):
        raise ValueError("unsupported Linux IPv4 foundation")
    network = IPv4Network(value["node_address_range"])
    nodes = value["nodes"]
    if not isinstance(nodes, list) or any(set(n) != {"key", "node_address"} for n in nodes):
        raise ValueError("invalid Linux node identities")
    if set(n["key"] for n in nodes) != set(node_keys) or len(nodes) != len(node_keys):
        raise ValueError("Linux foundation node identities differ")
    addresses = [IPv4Address(n["node_address"]) for n in nodes]
    if len(set(addresses)) != len(addresses) or any(a not in network for a in addresses):
        raise ValueError("invalid Linux node address allocation")
    zones = [IPv4Address(a) for a in value["zone_addresses"]]
    if len(set(zones)) != len(zones) or any(a in network for a in zones):
        raise ValueError("Linux Zone addresses collide with node identities")
    return value


def _validate_demand(value, boundary_count):
    if (not isinstance(value, dict) or set(value) != {"boundary_count", "generation_count"}
            or type(value["boundary_count"]) is not int or value["boundary_count"] != boundary_count
            or value["generation_count"] != boundary_count):
        raise ValueError("Linux delivery activation count differs")
    return value


class _NativeDemand:
    """Count chronological activations without imposing BPF map reservations."""
    def __init__(self):
        self._total_generations = 0
        self.begin_batch()

    def begin_batch(self):
        self._count = 0

    def add(self, boundary):
        self._count += 1
        self._total_generations += 1

    def snapshot(self):
        return {"boundary_count": self._count, "generation_count": self._count}


def _validate_request(request, node_keys, node_addresses, destinations):
    """Validate external material and retain parsed link subnets for resource checks."""
    link_networks = {}
    if (not isinstance(request, dict) or set(request) != {
            "backend", "ordinal", "previous_ordinal", "boundary_timestamp", "actions"}
            or request["backend"] != "linux" or type(request["ordinal"]) is not int
            or request["ordinal"] < 0 or request["previous_ordinal"] != request["ordinal"] - 1):
        raise ValueError("unsupported Linux activation request")
    for action in request["actions"]:
        op = action.get("op")
        if op in {"create", "remove"}:
            if set(action) != {"op", "connection"}:
                raise ValueError("invalid Linux connection operation")
            c = action["connection"]
            fields = {"connection", "node_a", "node_b", "interface_a", "interface_b", "address_a", "address_b"}
            if op == "create":
                fields |= {"parameters_a", "parameters_b"}
            if set(c) != fields or c["node_a"] not in node_keys or c["node_b"] not in node_keys or c["node_a"] == c["node_b"]:
                raise ValueError("invalid Linux connection endpoints")
            from ipaddress import IPv4Interface
            a, b = IPv4Interface(c["address_a"]), IPv4Interface(c["address_b"])
            if a.network.prefixlen != 31 or a.network != b.network or a.ip == b.ip:
                raise ValueError("Linux link endpoints require distinct addresses in one /31")
            if not any(a.network.subnet_of(IPv4Network(pool)) for pool in _LINK_POOLS):
                raise ValueError("Linux link address is outside the allocation pools")
            link_networks[c["address_a"], c["address_b"]] = a.network
            import re
            if any(re.fullmatch(r"sv2[0-9a-f]{10}[ab]", c[f"interface_{side}"]) is None for side in ("a", "b")):
                raise ValueError("invalid Linux managed interface name")
            if op == "create":
                for side in ("a", "b"):
                    _validate_parameters(c[f"parameters_{side}"])
        elif op == "qdisc":
            if set(action) != {"op", "key", "interface", "parameters"} or action["key"] not in node_keys:
                raise ValueError("invalid Linux qdisc operation")
            _validate_parameters(action["parameters"])
        elif op in {"address_add", "address_delete"}:
            if set(action) != {"op", "key", "address"} or action["key"] not in node_keys:
                raise ValueError("invalid Linux Zone address operation")
            IPv4Address(action["address"])
        elif op == "route":
            if set(action) != {"op", "previous", "current"} or action["previous"] == action["current"]:
                raise ValueError("invalid Linux route change")
            keys = set()
            for route in (action["previous"], action["current"]):
                if route is None:
                    continue
                _validate_route(route, node_keys, node_addresses, destinations)
                keys.add((route["key"], route["destination"]))
            if len(keys) != 1:
                raise ValueError("Linux route change must preserve one source/destination key")
        else:
            raise ValueError("unsupported Linux IPv4 operation")
    return link_networks


def _validate_route(route, node_keys, node_addresses, destinations):
    if not isinstance(route, dict) or route.get("kind") not in {"unicast", "blackhole"}:
        raise ValueError("unsupported Linux route kind")
    fields = {"key", "destination", "kind"}
    if route["kind"] == "unicast":
        fields |= {"connection", "interface", "gateway", "prefsrc"}
    if set(route) != fields or route["key"] not in node_keys or route["destination"] not in destinations:
        raise ValueError("invalid Linux route identity/destination")
    if route["kind"] == "unicast":
        import re
        if (not isinstance(route["connection"], str) or not route["connection"]
                or re.fullmatch(r"sv2[0-9a-f]{10}[ab]", route["interface"]) is None
                or route["prefsrc"] != node_addresses[route["key"]]):
            raise ValueError("invalid Linux route exit/preferred source")
        IPv4Address(route["gateway"])


def _validate_parameters(value):
    if (set(value) != {"bandwidth_bps", "delay_us", "packet_loss_ppm"}
            or any(type(v) is not int for v in value.values())
            or value["bandwidth_bps"] <= 0 or value["delay_us"] < 0
            or not 0 <= value["packet_loss_ppm"] <= 1_000_000):
        raise ValueError("invalid Linux directional link parameters")
