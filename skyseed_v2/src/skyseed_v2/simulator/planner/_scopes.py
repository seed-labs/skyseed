"""Validate fixed Model scope declarations against a bound world."""
from types import MappingProxyType
from skyseed_v2.shared import NodeId

def _validated_satellite_scopes(values):
    if values is None:
        return MappingProxyType({})
    from .values import _validate_network_scope
    result = dict(values)
    for node, scope in result.items():
        if not isinstance(node, NodeId) or node.entity_kind != "satellite":
            raise ValueError("satellite_scopes keys must identify satellites")
        _validate_network_scope(scope)
        if not scope:
            raise ValueError("configured network scopes must be non-empty")
    if not result:
        raise ValueError("configured satellite_scopes must not be empty")
    return MappingProxyType(result)


def _validate_scope_world(world, scopes):
    if scopes:
        actual = {sat.node_id for system in world.autonomous_systems
                  for shell in system.shells for orbit in shell.orbits for sat in orbit.satellites}
        if set(scopes) != actual:
            raise ValueError("satellite_scopes must cover exactly the PhysicalWorld satellites")


def _connections_by_scope(connections, scopes):
    grouped = {}
    for connection in connections:
        link = connection.connection_id
        a, b = link.endpoint_a, link.endpoint_b
        scope = scopes[a] if a.entity_kind == "satellite" else scopes[b]
        if a.entity_kind == b.entity_kind == "satellite" and scopes[a] != scopes[b]:
            continue
        grouped.setdefault(scope, []).append(connection)
    return grouped


def _group_by_scope(values, key):
    grouped = {}
    for value in values:
        grouped.setdefault(key(value), []).append(value)
    return grouped
