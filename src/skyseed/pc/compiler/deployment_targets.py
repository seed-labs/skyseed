"""Read SEED deployment identities and allocate stable, offline link targets."""
import hashlib
import ipaddress
import json
from pathlib import Path

import yaml


_META = "org.seedsecuritylabs.seedemu.meta."


class DeploymentTargets:
    """Assignments belong to one compiler, never to a running container."""

    def __init__(self, deployment_dir, *, node_address_pool, endpoint_address_pool,
                 link_address_pool):
        self.directory = Path(deployment_dir)
        with (self.directory / "docker-compose.yml").open() as stream:
            compose = yaml.safe_load(stream)
        self.nodes = {}
        existing_networks = []
        for network in compose.get("networks", {}).values():
            for configuration in network.get("ipam", {}).get("config", ()):
                if "subnet" in configuration:
                    existing_networks.append(ipaddress.ip_network(configuration["subnet"]))
        for service_name, service in compose["services"].items():
            labels = service.get("labels", {})
            if isinstance(labels, list):
                labels = dict(item.split("=", 1) for item in labels)
            identity = labels.get(_META + "skyseed.scene_id")
            if identity is None:
                continue
            if identity in self.nodes:
                raise ValueError(f"duplicate deployed Scene identity: {identity}")
            addresses = []
            for name, configuration in (service.get("networks") or {}).items():
                if configuration and "ipv4_address" in configuration:
                    addresses.append((name, configuration["ipv4_address"]))
            self.nodes[identity] = {
                "node_id": identity, "service": service_name,
                "kind": labels[_META + "skyseed.scene_kind"],
                "static_addresses": tuple(sorted(addresses)),
                "static_interfaces": tuple(sorted(value for key, value in labels.items()
                                                   if key.startswith(_META + "net.") and key.endswith(".name"))),
            }
        if not self.nodes:
            raise ValueError("deployment contains no SkySEED Scene identity labels")

        self.pools = tuple(ipaddress.IPv4Network(value) for value in
                           (node_address_pool, endpoint_address_pool, link_address_pool))
        for index, pool in enumerate(self.pools):
            for other in (*existing_networks, *self.pools[:index]):
                if other.version == 4 and pool.overlaps(other):
                    raise ValueError(f"compiler address pool {pool} overlaps deployment/pool {other}")
        for index, identity in enumerate(sorted(self.nodes), start=1):
            self.nodes[identity]["identity_address"] = self._address(0, index) + "/32"

        # Hash the deployment facts used by compilation, not unrelated node files.
        association = {"nodes": self.nodes, "networks": sorted(map(str, existing_networks)),
                       "pools": tuple(map(str, self.pools))}
        encoded = json.dumps(association, sort_keys=True, separators=(",", ":")).encode()
        self.deployment_id = hashlib.sha256(encoded).hexdigest()
        self.links = {}
        self.endpoints = {}
        self._interfaces = {(identity, name) for identity, node in self.nodes.items()
                            for name in node["static_interfaces"]}

    def _address(self, pool_index, offset):
        pool = self.pools[pool_index]
        if offset >= pool.num_addresses:
            raise ValueError(f"compiler address pool exhausted: {pool}")
        return str(pool.network_address + offset)

    def node(self, identity):
        try:
            return self.nodes[identity]
        except KeyError:
            raise ValueError(f"Scene node {identity!r} is absent from deployment") from None

    def link(self, identity):
        identity = tuple(sorted(identity))
        if identity in self.links:
            return self.links[identity]
        ordinal = len(self.links)
        endpoints = []
        for side, node_id in enumerate(identity):
            self.node(node_id)
            seed = json.dumps((identity, node_id), separators=(",", ":")).encode()
            digest = hashlib.sha256(seed).hexdigest()
            interface = "ss" + digest[:12]
            if (node_id, interface) in self._interfaces:
                raise ValueError(f"dynamic interface name collision at {node_id}: {interface}")
            address = self._address(2, ordinal * 2 + side)
            endpoints.append({"node_id": node_id, "interface": interface,
                              "address": address + "/31", "port": ordinal * 2 + side + 1,
                              "mac": "02:" + ":".join(digest[i:i + 2] for i in range(0, 10, 2))})
        for endpoint in endpoints:
            self._interfaces.add((endpoint["node_id"], endpoint["interface"]))
        target = {"link_id": identity, "endpoints": tuple(endpoints)}
        self.links[identity] = target
        return target

    def destination(self, destination):
        if destination.kind == "node":
            return self.node(destination.identity)["identity_address"]
        if destination.kind == "endpoint":
            if destination.identity not in self.endpoints:
                self.endpoints[destination.identity] = self._address(1, len(self.endpoints) + 1) + "/32"
            return self.endpoints[destination.identity]
        if destination.kind == "prefix":
            return str(ipaddress.IPv4Network(destination.identity))
        raise ValueError(f"unsupported compiled destination kind: {destination.kind!r}")
