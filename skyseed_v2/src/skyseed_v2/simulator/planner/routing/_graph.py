"""Private architecture-neutral deterministic routing primitives.

The public Planner's Route models use this module for bounded shortest-path
work. It knows PhysicalWorld node roles and undirected operational edges, but
deliberately owns no planning state, publication values, or backend facts.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from skyseed_v2.shared import NodeId

from ...physical import PhysicalWorld


@dataclass(frozen=True, slots=True)
class _RoutingCoreContext:
    nodes: frozenset[NodeId]
    satellites: frozenset[NodeId]
    ground_stations: frozenset[NodeId]
    graph: Mapping[NodeId, tuple[NodeId, ...]]


def _prepare_routing_core_context(
    physical_world: PhysicalWorld,
    undirected_edges: Iterable[tuple[NodeId, NodeId]],
    *, previous: _RoutingCoreContext | None = None,
) -> _RoutingCoreContext:
    """Validate and index one operational graph in canonical order."""

    if not isinstance(physical_world, PhysicalWorld):
        raise TypeError("physical_world must be a PhysicalWorld")
    nodes, satellites, ground_stations = (_physical_world_node_ids(physical_world) if previous is None
        else (previous.nodes, previous.satellites, previous.ground_stations))
    graph = {node_id: set() for node_id in nodes}
    seen: set[tuple[NodeId, NodeId]] = set()
    for edge in undirected_edges:
        if not isinstance(edge, tuple) or len(edge) != 2:
            raise TypeError("undirected_edges must contain NodeId pairs")
        first, second = edge
        if not isinstance(first, NodeId) or not isinstance(second, NodeId):
            raise TypeError("undirected_edges must contain NodeId pairs")
        if first == second:
            raise ValueError("operational graph edges must have distinct endpoints")
        if first not in graph or second not in graph:
            raise ValueError("operational graph endpoint is foreign to PhysicalWorld")
        canonical = _canonical_edge(first, second)
        if canonical in seen:
            raise ValueError("duplicate operational graph edge")
        seen.add(canonical)
        graph[first].add(second)
        graph[second].add(first)
    return _RoutingCoreContext(
        nodes=frozenset(nodes),
        satellites=frozenset(satellites),
        ground_stations=frozenset(ground_stations),
        graph=MappingProxyType(
            {
                node_id: tuple(sorted(graph[node_id], key=_node_key))
                for node_id in sorted(graph, key=_node_key)
            }
        ),
    )


def _physical_world_node_ids(
    physical_world: PhysicalWorld,
) -> tuple[set[NodeId], set[NodeId], set[NodeId]]:
    nodes: set[NodeId] = set()
    satellites: set[NodeId] = set()
    ground_stations: set[NodeId] = set()
    for autonomous_system in physical_world.autonomous_systems:
        for shell in autonomous_system.shells:
            for orbit in shell.orbits:
                for satellite in orbit.satellites:
                    nodes.add(satellite.node_id)
                    satellites.add(satellite.node_id)
        for ground_station in autonomous_system.ground_stations:
            nodes.add(ground_station.node_id)
            ground_stations.add(ground_station.node_id)
    return nodes, satellites, ground_stations


def _canonical_edge(
    first: NodeId, second: NodeId
) -> tuple[NodeId, NodeId]:
    return (
        (first, second)
        if _node_key(first) < _node_key(second)
        else (second, first)
    )


def _node_key(node_id: NodeId) -> tuple[int, str, str]:
    return node_id.asn, node_id.entity_kind, node_id.entity_id
