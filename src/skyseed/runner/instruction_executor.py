"""Runtime namespace resources and the physical instruction interpreter."""
import ctypes
import errno
from ipaddress import IPv4Network
import json
import os
from pathlib import Path
import secrets
import time

from .netlink import RouteSocket
from .bpf.versions import LabelVersions


def _setns(fd):
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.setns(fd, 0x40000000) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


def _resource(link):
    return "link:" + json.dumps(link["link_id"], ensure_ascii=False, separators=(",", ":"))


class InstructionExecutor:
    """One helper owns all descriptors and only its explicitly created resources."""
    def __init__(self, backend, program=None):
        self.backend = backend
        self.nodes = {}
        self.links = {}
        self.routes = {}
        self.routes_by_resource = {}
        self.group = secrets.randbelow(0xFFFFFFFE) + 1
        self.labels = LabelVersions(program) if backend == "bpf" else None
        self.published_local = {}

    def publish(self, generation):
        following = self.labels.local_generations[generation]
        for identity, prefixes in following.items():
            for prefix in prefixes - self.published_local.get(identity, set()):
                self._local(identity, prefix, True)
        previous = self.labels.publish(generation)
        activated = time.monotonic()
        for identity, prefixes in self.published_local.items():
            for prefix in prefixes - following.get(identity, set()):
                self._local(identity, prefix, False)
        self.published_local = following
        return {"previous": previous, "activated": activated}

    def add_nodes(self, targets):
        for target in targets:
            identity = target["node_id"]
            if identity in self.nodes:
                continue
            original = os.open("/proc/self/ns/net", os.O_RDONLY)
            namespace = os.open(f"/proc/{target['pid']}/ns/net", os.O_RDONLY)
            try:
                _setns(namespace)
                channel = RouteSocket()
                # BPF local injection requires this existing SEED setting.
                if self.labels and int(Path("/proc/sys/net/ipv4/conf/all/rp_filter").read_text()):
                    channel.close()
                    raise RuntimeError(f"{identity}: SEED must disable all.rp_filter for local BPF ingress")
            except BaseException:
                os.close(namespace)
                raise
            finally:
                _setns(original)
                os.close(original)
            node = {"target": target, "namespace": namespace, "socket": channel,
                    "interfaces": {}, "local": {}, "injection": set(), "identity_index": None}
            self.nodes[identity] = node
            channel.create_dummy("ssidentity")
            index = channel.link_index("ssidentity")
            node["identity_index"] = index
            channel.address(index, target["identity_address"], exclusive=True)
            channel.set_link(index, up=True)
            if self.labels:
                self.labels.add_node(identity)
                self._local_ingress(identity)

    def _local_ingress(self, identity):
        node = self.nodes[identity]
        channel = node["socket"]
        mac = "02:53:4b:59:00:01"
        channel.create_veth("sslocalout", "sslocalin", node["namespace"], mac=mac,
                            group=self.group, peer_group=self.group)
        output, ingress = channel.link_index("sslocalout"), channel.link_index("sslocalin")
        node["local_output"] = output
        for index in (output, ingress):
            channel.local_ingress_ipv4(index)
            channel.set_link(index, up=True)
        channel.neighbour(output, "169.254.0.1", mac)
        channel.attach_label_program(ingress, self.labels.objects[identity].programs)

    def inject_prefixes(self, prefixes):
        # Called during preparation, before rules use a new business prefix.
        for identity, node in self.nodes.items():
            for prefix in prefixes:
                if prefix in node["injection"]:
                    continue
                channel = node["socket"]
                channel.local_output_route(node["local_output"], prefix, table=0x5353,
                    gateway="169.254.0.1", prefsrc=node["target"]["identity_address"].split("/")[0], protocol=253)
                channel.local_output_rule(prefix, table=0x5353, priority=1, protocol=253)
                node["injection"].add(prefix)

    def apply(self, instructions):
        for instruction in instructions:
            self._apply(instruction)

    def _apply(self, instruction):
        opcode, parameters = instruction["opcode"], instruction["parameters"]
        identity = instruction["node_id"]
        node = self.nodes.get(identity)
        if opcode == "veth.create":
            left, right = parameters["endpoints"]
            source, target = self.nodes[left["node_id"]], self.nodes[right["node_id"]]
            source["socket"].create_veth(left["interface"], right["interface"], target["namespace"],
                                         group=self.group, peer_group=self.group)
            self.links[_resource(parameters)] = parameters
            for endpoint in (left, right):
                owner = self.nodes[endpoint["node_id"]]
                owner["interfaces"][endpoint["interface"]] = owner["socket"].link_index(endpoint["interface"])
        elif opcode == "interface.configure":
            channel, index = node["socket"], node["interfaces"][parameters["interface"]]
            channel.set_link(index, mac=parameters["mac"], mtu=parameters["mtu"])
            channel.native_ipv4(index)
            channel.address(index, parameters["address"])
            channel.neighbour(index, parameters["peer_address"], parameters["peer_mac"])
            if self.labels:
                channel.attach_label_program(index, self.labels.objects[identity].programs)
        elif opcode == "netem.replace":
            node["socket"].netem(node["interfaces"][parameters["interface"]], parameters)
        elif opcode == "link.activate":
            for side, endpoint in enumerate(parameters["endpoints"]):
                peer = parameters["endpoints"][1 - side]
                owner = self.nodes[endpoint["node_id"]]
                index = owner["interfaces"][endpoint["interface"]]
                owner["socket"].set_link(index, up=True)
                if self.labels:
                    self.labels.bind(endpoint["node_id"], endpoint["port"], index, endpoint["mac"], peer["mac"])
            if not self.labels:
                self._restore_resource(_resource(parameters))
        elif opcode == "link.unbind":
            resource = parameters["resource"]
            if self.labels:
                for endpoint in self.links[resource]["endpoints"]:
                    self.labels.unbind(endpoint["node_id"], endpoint["port"])
            else:
                for key in self.routes_by_resource.get(resource, ()):
                    self._install_route(key, available=False)
        elif opcode == "veth.delete":
            first = parameters["endpoints"][0]
            owner = self.nodes[first["node_id"]]
            owner["socket"].delete_link(owner["interfaces"][first["interface"]])
            for endpoint in parameters["endpoints"]:
                del self.nodes[endpoint["node_id"]]["interfaces"][endpoint["interface"]]
            del self.links[_resource(parameters)]
        elif opcode == "linux.route":
            self._route_intent(identity, parameters)
        else:
            raise ValueError(f"unsupported physical instruction: {opcode}")

    def _local(self, identity, prefix, add):
        node = self.nodes[identity]
        current = node["local"].get(prefix, 0)
        def configure(delete=False):
            if IPv4Network(prefix).prefixlen == 32:
                node["socket"].address(node["identity_index"], prefix, delete=delete, exclusive=True)
            else:
                node["socket"].local_route(node["identity_index"], prefix, delete=delete)
        if add:
            if current == 0 and prefix != node["target"]["identity_address"]:
                configure()
            node["local"][prefix] = current + 1
        elif current:
            if current == 1:
                if prefix != node["target"]["identity_address"]:
                    configure(delete=True)
                del node["local"][prefix]
            else:
                node["local"][prefix] = current - 1

    def _route_intent(self, identity, parameters):
        key = (identity, parameters["prefix"])
        previous = self.routes.pop(key, None)
        if previous:
            resource = previous["resource"]
            if resource:
                self.routes_by_resource[resource].discard(key)
            if previous["action"] == "deliver":
                self._local(identity, previous["prefix"], False)
            elif parameters["kind"] == "withdraw" or parameters["action"] == "deliver":
                self.nodes[identity]["socket"].route(previous["prefix"], delete=True,
                    table=previous["table"], protocol=previous["protocol"], priority=previous["priority"])
        if parameters["kind"] == "withdraw":
            return
        self.routes[key] = parameters
        if parameters["action"] == "deliver":
            self._local(identity, parameters["prefix"], True)
        else:
            self.routes_by_resource.setdefault(parameters["resource"], set()).add(key)
            self._install_route(key, available=parameters["resource"] in self.links)

    def _install_route(self, key, *, available):
        identity, prefix = key
        parameters, node = self.routes[key], self.nodes[identity]
        fields = {"table": parameters["table"], "protocol": parameters["protocol"],
                  "priority": parameters["priority"]}
        if available:
            fields.update(index=node["interfaces"][parameters["interface"]], gateway=parameters["gateway"],
                          source=node["target"]["identity_address"].split("/")[0])
        node["socket"].route(prefix, **fields)

    def _restore_resource(self, resource):
        for key in self.routes_by_resource.get(resource, ()):
            self._install_route(key, available=True)

    def close(self):
        errors = []
        for (identity, prefix), parameters in self.routes.items():
            if parameters["action"] == "deliver":
                continue
            try:
                self.nodes[identity]["socket"].route(prefix, table=parameters["table"],
                    protocol=parameters["protocol"], priority=parameters["priority"], delete=True)
            except OSError as error:
                if error.errno != errno.ESRCH:
                    errors.append(error)
        for node in self.nodes.values():
            channel = node["socket"]
            for prefix in node["injection"]:
                try:
                    channel.local_output_rule(prefix, table=0x5353, priority=1, protocol=253, delete=True)
                except OSError as error:
                    if error.errno not in (errno.ENOENT, errno.ESRCH):
                        errors.append(error)
            try:
                channel.delete_group(self.group)
            except OSError as error:
                if error.errno not in (errno.ENOENT, errno.ENODEV):
                    errors.append(error)
            if node["identity_index"] is not None:
                try:
                    channel.delete_link(node["identity_index"])
                except OSError as error:
                    if error.errno not in (errno.ENOENT, errno.ENODEV):
                        errors.append(error)
            channel.close()
            os.close(node["namespace"])
        if self.labels:
            self.labels.close()
        self.nodes.clear()
        if errors:
            raise ExceptionGroup("Runner resource cleanup failed", errors)
