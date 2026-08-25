"""Share private transit eligibility across Route generation and validation."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from skyseed_v2.shared import NodeId

from .connection import ConnectionSlice
from .route import GroundStationRouteTarget, RouteTarget, ZoneRouteTarget
from ..topology import Topology


@dataclass(frozen=True, slots=True)
class _RoutingPolicyContext:
    nodes: frozenset[NodeId]
    satellites: frozenset[NodeId]
    ground_stations: frozenset[NodeId]
    graph: Mapping[NodeId, tuple[NodeId, ...]]


def eligible_shortest_next_hops(
    topology: Topology,
    connection_slice: ConnectionSlice,
    target: RouteTarget,
    terminal: NodeId,
) -> Mapping[NodeId, tuple[NodeId, ...]]:
    """Return reachable formal sources and shortest eligible next-hop choices."""

    return _eligible_shortest_next_hops(
        _prepare_routing_policy_context(topology, connection_slice),
        target,
        terminal,
    )


def _prepare_routing_policy_context(
    topology: Topology,
    connection_slice: ConnectionSlice,
) -> _RoutingPolicyContext:
    nodes, satellites, ground_stations = _topology_node_ids(topology)
    graph = _connection_graph(nodes, connection_slice)
    return _RoutingPolicyContext(
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


def _eligible_shortest_next_hops(
    context: _RoutingPolicyContext,
    target: RouteTarget,
    terminal: NodeId,
    *,
    required_sources: frozenset[NodeId] | None = None,
) -> Mapping[NodeId, tuple[NodeId, ...]]:
    graph = context.graph
    if isinstance(target, GroundStationRouteTarget):
        permitted = context.satellites | {terminal}
        distances = _reverse_distances(
            graph,
            terminal,
            permitted,
            required_sources,
        )
        result = {
            source: _closer_neighbors(graph, source, distances, permitted)
            for source in context.satellites
            if source in distances and source != terminal
        }
    elif isinstance(target, ZoneRouteTarget):
        ground_sources = (
            context.ground_stations
            if required_sources is None
            else context.ground_stations & required_sources
        )
        required_satellites = (
            None
            if required_sources is None
            else frozenset(
                neighbor
                for source in ground_sources
                for neighbor in graph[source]
                if neighbor in context.satellites
            )
        )
        distances = _reverse_distances(
            graph,
            terminal,
            context.satellites,
            required_satellites,
        )
        result = {
            source: _closer_neighbors(
                graph,
                source,
                distances,
                context.satellites,
            )
            for source in context.satellites
            if source in distances and source != terminal
        }
        for source in ground_sources:
            candidates = tuple(
                neighbor
                for neighbor in graph[source]
                if neighbor in context.satellites and neighbor in distances
            )
            if candidates:
                minimum_distance = min(
                    distances[neighbor] for neighbor in candidates
                )
                result[source] = tuple(
                    neighbor
                    for neighbor in candidates
                    if distances[neighbor] == minimum_distance
                )
    else:
        raise TypeError(
            "target must be a GroundStationRouteTarget or ZoneRouteTarget"
        )
    return dict(sorted(result.items(), key=lambda item: _node_key(item[0])))


def route_step_allows_transit(
    source: NodeId,
    current: NodeId,
    next_hop: NodeId,
    target: RouteTarget,
    terminal: NodeId,
) -> bool:
    """Return whether one connected step obeys the frozen transit rule."""

    if isinstance(target, GroundStationRouteTarget):
        return (
            source.entity_kind == "satellite"
            and current.entity_kind == "satellite"
            and (
                next_hop.entity_kind == "satellite"
                or next_hop == terminal
            )
        )
    if isinstance(target, ZoneRouteTarget):
        if current.entity_kind == "ground-station":
            return (
                current == source
                and next_hop.entity_kind == "satellite"
            )
        return (
            current.entity_kind == "satellite"
            and next_hop.entity_kind == "satellite"
        )
    raise TypeError(
        "target must be a GroundStationRouteTarget or ZoneRouteTarget"
    )


def _closer_neighbors(
    graph: Mapping[NodeId, tuple[NodeId, ...]],
    source: NodeId,
    distances: Mapping[NodeId, int],
    permitted: frozenset[NodeId],
) -> tuple[NodeId, ...]:
    return tuple(
        neighbor
        for neighbor in graph[source]
        if neighbor in permitted
        and distances.get(neighbor) == distances[source] - 1
    )


def _reverse_distances(
    graph: Mapping[NodeId, tuple[NodeId, ...]],
    terminal: NodeId,
    permitted: frozenset[NodeId],
    required_sources: frozenset[NodeId] | None = None,
) -> dict[NodeId, int]:
    if terminal not in permitted:
        return {}
    distances = {terminal: 0}
    unresolved = (
        None
        if required_sources is None
        else set(required_sources & permitted) - {terminal}
    )
    if unresolved is not None and not unresolved:
        return distances
    pending = deque([terminal])
    while pending:
        current = pending.popleft()
        for neighbor in graph[current]:
            if neighbor in permitted and neighbor not in distances:
                distances[neighbor] = distances[current] + 1
                if unresolved is not None:
                    unresolved.discard(neighbor)
                    if not unresolved:
                        return distances
                pending.append(neighbor)
    return distances


def _connection_graph(
    nodes: set[NodeId],
    connection_slice: ConnectionSlice,
) -> dict[NodeId, set[NodeId]]:
    graph = {node_id: set() for node_id in nodes}
    for connection in connection_slice.connections:
        first = connection.connection_id.endpoint_a
        second = connection.connection_id.endpoint_b
        if first not in graph or second not in graph:
            raise ValueError("Connection endpoint is foreign to Topology")
        graph[first].add(second)
        graph[second].add(first)
    return graph


def _topology_node_ids(
    topology: Topology,
) -> tuple[set[NodeId], set[NodeId], set[NodeId]]:
    nodes: set[NodeId] = set()
    satellites: set[NodeId] = set()
    ground_stations: set[NodeId] = set()
    for autonomous_system in topology.autonomous_systems:
        for shell in autonomous_system.shells:
            for orbit in shell.orbits:
                for satellite in orbit.satellites:
                    nodes.add(satellite.node_id)
                    satellites.add(satellite.node_id)
        for ground_station in autonomous_system.ground_stations:
            nodes.add(ground_station.node_id)
            ground_stations.add(ground_station.node_id)
    return nodes, satellites, ground_stations


def _node_key(node_id: NodeId) -> tuple[int, str, str]:
    return node_id.asn, node_id.entity_kind, node_id.entity_id
