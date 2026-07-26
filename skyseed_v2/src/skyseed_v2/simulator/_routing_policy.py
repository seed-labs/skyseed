"""Private shared transit eligibility for Route generation and validation."""

from __future__ import annotations

from collections import deque
from typing import Mapping

from skyseed_v2.node_id import NodeId

from .connection import ConnectionSlice
from .route import GroundStationRouteTarget, RouteTarget, ZoneRouteTarget
from .topology import Topology


def eligible_shortest_next_hops(
    topology: Topology,
    connection_slice: ConnectionSlice,
    target: RouteTarget,
    terminal: NodeId,
) -> Mapping[NodeId, tuple[NodeId, ...]]:
    """Return reachable formal sources and shortest eligible next-hop choices."""

    nodes, satellites, ground_stations = _topology_node_ids(topology)
    graph = _connection_graph(nodes, connection_slice)
    if isinstance(target, GroundStationRouteTarget):
        permitted = satellites | {terminal}
        distances = _reverse_distances(
            _restricted_graph(graph, permitted),
            terminal,
        )
        result = {
            source: _closer_neighbors(graph, source, distances, permitted)
            for source in satellites
            if source in distances and source != terminal
        }
    elif isinstance(target, ZoneRouteTarget):
        distances = _reverse_distances(
            _restricted_graph(graph, satellites),
            terminal,
        )
        result = {
            source: _closer_neighbors(
                graph,
                source,
                distances,
                satellites,
            )
            for source in satellites
            if source in distances and source != terminal
        }
        for source in ground_stations:
            candidates = tuple(
                neighbor
                for neighbor in graph[source]
                if neighbor in satellites and neighbor in distances
            )
            if candidates:
                minimum_distance = min(
                    distances[neighbor] for neighbor in candidates
                )
                result[source] = tuple(
                    sorted(
                        (
                            neighbor
                            for neighbor in candidates
                            if distances[neighbor] == minimum_distance
                        ),
                        key=_node_key,
                    )
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
    graph: Mapping[NodeId, set[NodeId]],
    source: NodeId,
    distances: Mapping[NodeId, int],
    permitted: set[NodeId],
) -> tuple[NodeId, ...]:
    return tuple(
        sorted(
            (
                neighbor
                for neighbor in graph[source]
                if neighbor in permitted
                and distances.get(neighbor) == distances[source] - 1
            ),
            key=_node_key,
        )
    )


def _restricted_graph(
    graph: Mapping[NodeId, set[NodeId]],
    permitted: set[NodeId],
) -> dict[NodeId, set[NodeId]]:
    return {
        node_id: {
            neighbor
            for neighbor in graph[node_id]
            if neighbor in permitted
        }
        for node_id in permitted
    }


def _reverse_distances(
    graph: Mapping[NodeId, set[NodeId]],
    terminal: NodeId,
) -> dict[NodeId, int]:
    if terminal not in graph:
        return {}
    distances = {terminal: 0}
    pending = deque([terminal])
    while pending:
        current = pending.popleft()
        for neighbor in sorted(graph[current], key=_node_key):
            if neighbor not in distances:
                distances[neighbor] = distances[current] + 1
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
