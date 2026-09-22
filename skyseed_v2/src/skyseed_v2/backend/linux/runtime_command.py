"""Native IPv4 operations in the retained privileged command process."""
import errno
from ipaddress import IPv4Network
import os
import resource
from pathlib import Path
from time import monotonic_ns
from . import network
from .materials import _validate_foundation, _IPv4Ranges, _ROUTE_PROTOCOL, _ROUTE_PRIORITY


def _reserve_foundation_descriptors(node_count):
    # One retained namespace FD and one route socket per node; leave room for
    # the control channel, original namespace and transient /proc/sysctl files.
    required = 2 * node_count + 64
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if hard != resource.RLIM_INFINITY and required > hard:
        raise RuntimeError(
            f"Linux Runner foundation needs {required} file descriptors; hard limit is {hard}"
        )
    if soft != resource.RLIM_INFINITY and soft < required:
        resource.setrlimit(resource.RLIMIT_NOFILE, (required, hard))


class _LinuxKernel:
    def __init__(self):
        self.nodes = {}
        self.connections = {}
        self.routes = {}
        self.ordinal = -1
        self.initialized = False
        self.failed = False

    def initialize(self, request):
        if self.nodes or self.initialized:
            raise RuntimeError("Linux foundation is already initialized")
        foundation = _validate_foundation(request["dataplane"], tuple(n["key"] for n in request["nodes"]))
        try:
            _reserve_foundation_descriptors(len(request["nodes"]))
            network._retain_namespaces(request["nodes"], self.nodes)
            occupied = set()
            identities = _IPv4Ranges((IPv4Network(foundation["node_address_range"]),
                          *(IPv4Network(a + "/32") for a in foundation["zone_addresses"])))
            original = os.open("/proc/self/ns/net", os.O_RDONLY | os.O_CLOEXEC)
            try:
                for node in self.nodes.values():
                    channel = node["netlink"]
                    channel.initial_links(reserved_prefix="sv2", group_start=0, group_end=0)
                    observed = tuple(IPv4Network((int(a), prefix), strict=False)
                                     for a, prefix in channel.ipv4_address_facts()) + channel.ipv4_route_networks()
                    if any(identities.overlap_end(n) is not None for n in observed):
                        raise RuntimeError(f"Linux identity allocation collides with live namespace addresses/routes: {node['key']}")
                    occupied.update(str(n) for n in observed)
                    network._setns(node["namespace_fd"])
                    if Path("/proc/sys/net/ipv4/conf/all/rp_filter").read_text().strip() != "0":
                        raise RuntimeError("Linux forwarding requires SEED net.ipv4.conf.all.rp_filter=0; runtime will not overwrite static configuration")
            finally:
                network._setns(original)
                os.close(original)
            for node in self.nodes.values():
                network._create_identity(node, node["node_address"])
            self.initialized = True
            return {"backend": "linux", "initialization_outcome": "ready",
                    "identity_interface": foundation["identity_interface"],
                    "occupied_ipv4": sorted(occupied), "kernel_forwarding": "per-managed-interface"}
        except BaseException as error:
            self.failed = True
            try:
                self.cleanup({})
            except BaseException as cleanup_error:
                error.add_note(f"Linux initialization cleanup failed: {cleanup_error}")
            error.evidence = {"initialization_outcome": "partial" if self.nodes else "no-owned-resources",
                              "cleanup_ownership_retained": bool(self.nodes)}
            raise

    def activate_boundary(self, request):
        if not self.initialized or self.failed:
            raise RuntimeError("Linux foundation is not available for activation")
        if self.ordinal != request["previous_ordinal"]:
            raise RuntimeError("Linux activation predecessor differs")
        anchor = monotonic_ns()
        completed = created = removed = installed_routes = withdrawn_routes = 0
        mutated = False
        def evidence(complete):
            return {"backend": "linux", "activation_semantics": "ordered-linux-operations",
                    "boundary_timestamp": request["boundary_timestamp"],
                    "activation_ordinal": request["ordinal"], "completed_actions": completed,
                    "created_connections": created, "deleted_connections": removed,
                    "installed_routes": installed_routes, "withdrawn_route_bindings": withdrawn_routes,
                    "target_transition_complete": complete, "live_state_may_be_partial": mutated and not complete,
                    "generation_publication_occurred": False,
                    "activation_anchor_monotonic_ns": anchor}
        try:
            for action in request["actions"]:
                op = action["op"]
                if op == "create":
                    c = action["connection"]
                    if c["connection"] in self.connections:
                        raise RuntimeError("Linux activation creates an already owned connection")
                    self.connections[c["connection"]] = c
                    mutated = True
                    try:
                        network._create_connection(self.nodes, c)
                    except OSError as error:
                        if error.errno == errno.EEXIST:
                            del self.connections[c["connection"]]
                        raise
                    for side in ("a", "b"):
                        node = self.nodes[c[f"node_{side}"]]
                        channel = node["netlink"]
                        name = c[f"interface_{side}"]
                        index = channel.link_index(name)
                        node["indexes"][name] = index
                        channel.native_ipv4(index)
                        channel.address(index, c[f"address_{side}"], exclusive=True)
                        channel.netem(index, c[f"parameters_{side}"])
                    # Both endpoints have their parameters and addresses before link-up.
                    for side in ("a", "b"):
                        node = self.nodes[c[f"node_{side}"]]
                        node["netlink"].set_link(node["indexes"][c[f"interface_{side}"]], up=True)
                    created += 1
                elif op == "remove":
                    c = self.connections[action["connection"]["connection"]]
                    mutated = True
                    network._delete_connection(self.nodes, c)
                    del self.connections[c["connection"]]
                    removed += 1
                elif op == "route":
                    mutated = True
                    added, withdrawn = self._change_route(action)
                    installed_routes += added
                    withdrawn_routes += withdrawn
                else:
                    node = self.nodes[action["key"]]
                    mutated = True
                    if op == "qdisc":
                        node["netlink"].netem(node["indexes"][action["interface"]], action["parameters"])
                    elif op in {"address_add", "address_delete"}:
                        node["netlink"].address(node["identity"], action["address"],
                                               delete=op == "address_delete", exclusive=True)
                    else:
                        raise ValueError("unsupported native operation")
                completed += 1
            self.ordinal = request["ordinal"]
            return evidence(True)
        except BaseException as error:
            self.failed = True
            error.evidence = {**evidence(False), "cleanup_ownership_retained": True}
            raise

    def _route_command(self, bound, *, delete):
        route = bound["route"]
        self.nodes[route["key"]]["netlink"].native_route(
            route["destination"], kind=route["kind"], protocol=_ROUTE_PROTOCOL, priority=_ROUTE_PRIORITY,
            index=bound["index"], gateway=route.get("gateway"), prefsrc=route.get("prefsrc"), delete=delete)

    def _withdraw_route(self, key):
        bound = self.routes[key]
        try:
            self._route_command(bound, delete=True)
        except OSError as error:
            # Veth retirement or an external replacement can remove the exact
            # owned record. Never broaden the delete to another route at this prefix.
            if error.errno not in (errno.ESRCH, errno.ENOENT, errno.ENODEV):
                raise
        del self.routes[key]

    def _change_route(self, action):
        previous, current = action["previous"], action["current"]
        route = current if current is not None else previous
        key = route["key"], route["destination"]
        bound = self.routes.get(key)
        if (None if bound is None else bound["route"]) != previous:
            raise RuntimeError("Linux route ownership predecessor differs")
        if bound is not None:
            self._withdraw_route(key)
        if current is not None:
            index = None if current["kind"] == "blackhole" else self.nodes[current["key"]]["indexes"][current["interface"]]
            bound = {"route": current, "index": index}
            # Record the exact request before send: a lost ACK does not discard
            # ownership. A definite exclusive-create collision never grants it.
            self.routes[key] = bound
            try:
                self._route_command(bound, delete=False)
            except OSError as error:
                if error.errno == errno.EEXIST:
                    del self.routes[key]
                raise
        return int(current is not None), int(previous is not None)

    def cleanup(self, request):
        errors = []
        deleted = 0
        for key in tuple(self.routes):
            try:
                self._withdraw_route(key)
            except BaseException as error:
                errors.append(str(error))
        for key, connection in tuple(self.connections.items()):
            try:
                network._delete_connection(self.nodes, connection)
                del self.connections[key]
                deleted += 1
            except BaseException as error:
                errors.append(str(error))
        for node in self.nodes.values():
            try:
                network._delete_identity(node)
            except BaseException as error:
                errors.append(str(error))
        if errors:
            error = RuntimeError("Linux cleanup left owned resources: " + "; ".join(errors))
            error.evidence = {"cleanup_ownership_retained": True, "errors": errors}
            raise error
        for key, node in tuple(self.nodes.items()):
            try:
                node["netlink"].close()
                # Linux releases the descriptor even when close reports an error;
                # retrying its number could close an unrelated, reused descriptor.
                fd = node.pop("namespace_fd", None)
                if fd is not None:
                    os.close(fd)
                del self.nodes[key]
            except BaseException as error:
                errors.append(str(error))
        if errors:
            error = RuntimeError("Linux namespace release incomplete: " + "; ".join(errors))
            error.evidence = {"cleanup_ownership_retained": True, "errors": errors}
            raise error
        self.initialized = self.failed = False
        self.ordinal = -1
        return {"backend": "linux", "deleted_connections": deleted,
                "cleanup_confirmed": True, "identity_interfaces_removed": True, "errors": []}


_KERNEL = _LinuxKernel()


def _dispatch_action(action, request):
    if action not in {"initialize", "activate_boundary", "cleanup"}:
        raise ValueError("unknown native Runner command")
    return getattr(_KERNEL, action)(request)
