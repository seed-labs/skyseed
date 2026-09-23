"""Native IPv4 environment shared by compilation and static software authoring."""
from dataclasses import dataclass
from types import MappingProxyType
from collections.abc import Mapping
from ipaddress import IPv4Network

from skyseed.shared import NodeId
from skyseed.backend.execution_materials.mechanics import (
    _allocate_node_addresses, _interface_name_for_source,
)
from .materials import _IDENTITY_INTERFACE, _LINK_POOLS, _ROUTE_PROTOCOL, _ROUTE_PRIORITY


@dataclass(frozen=True, init=False)
class NativeRoutingEnvironment:
    """Read-only native identities and interface queries, with no routing policy.

    Constructed from the complete node set. Zone prefixes only validate collisions;
    future service addresses remain runtime additions to ``identity_interface``.
    """
    node_addresses: Mapping[NodeId, str]
    node_address_range: IPv4Network
    identity_interface: str = _IDENTITY_INTERFACE
    link_address_pools: tuple[str, ...] = _LINK_POOLS
    route_protocol: int = _ROUTE_PROTOCOL
    route_priority: int = _ROUTE_PRIORITY

    def __init__(self, nodes, *, zone_prefixes=()):
        addresses, network = _allocate_node_addresses(tuple(nodes), tuple(zone_prefixes))
        object.__setattr__(self, 'node_addresses', MappingProxyType(addresses))
        object.__setattr__(self, 'node_address_range', network)

    def interface_name(self, connection, node):
        """Name of a native dynamic interface at an endpoint, before it exists."""
        if (node not in (connection.endpoint_a, connection.endpoint_b)
                or connection.endpoint_a not in self.node_addresses
                or connection.endpoint_b not in self.node_addresses):
            raise ValueError('interface query must use known connection endpoints')
        return _interface_name_for_source(connection, node)
