"""Built-in deterministic minimum-hop Route algorithm."""

from __future__ import annotations

from collections import deque
from datetime import datetime

from skyseed_v2._time import normalize_utc_datetime
from skyseed_v2.node_id import NodeId

from ..connection import ConnectionSlice
from ..route import (
    GroundStationRouteTarget,
    RouteKey,
    RouteSlice,
    RouteTarget,
    ZoneRouteTarget,
)
from ..topology import GroundStation, Satellite, Topology


class HopCountRouteModel:
    """Compute deterministic sparse next hops with reverse BFS.

    Layer: built-in stateless Simulator Model. Users explicitly create it and
    one Slicer invokes it. It is not a Connection source, full-path store,
    routing backend or fallback and is public from ``skyseed_v2.simulator``.
    """

    __slots__ = ()

    def compute_route_slice(
        self,
        topology: Topology,
        timestamp: datetime,
        connection_slice: ConnectionSlice,
        previous_route_slice: RouteSlice,
    ) -> RouteSlice:
        if not isinstance(topology, Topology):
            raise TypeError("topology must be a Topology")
        normalize_utc_datetime(timestamp, "timestamp")
        if not isinstance(connection_slice, ConnectionSlice):
            raise TypeError("connection_slice must be a ConnectionSlice")
        if not isinstance(previous_route_slice, RouteSlice):
            raise TypeError("previous_route_slice must be a RouteSlice")

        nodes, satellites, ground_stations = _topology_nodes(topology)
        graph = {node_id: set() for node_id in nodes}
        for connection in connection_slice.connections:
            first = connection.connection_id.endpoint_a
            second = connection.connection_id.endpoint_b
            if first not in graph or second not in graph:
                raise ValueError("Connection endpoint is foreign to Topology")
            graph[first].add(second)
            graph[second].add(first)

        target_terminals: list[tuple[RouteTarget, NodeId, bool]] = []
        for ground_station in ground_stations:
            target_terminals.append(
                (
                    GroundStationRouteTarget(ground_station.node_id),
                    ground_station.node_id,
                    True,
                )
            )
        for zone_id, service_satellite in (
            connection_slice.zone_service_assignments.items()
        ):
            if service_satellite not in satellites:
                raise ValueError(
                    "Zone service Satellite is foreign to Topology"
                )
            target_terminals.append(
                (ZoneRouteTarget(zone_id), service_satellite, False)
            )

        entries: list[tuple[RouteKey, NodeId]] = []
        for target, terminal, ground_station_target in target_terminals:
            distances = _reverse_distances(graph, terminal)
            allowed_sources = (
                satellites
                if ground_station_target
                else nodes
            )
            for source in sorted(allowed_sources, key=_node_key):
                if source == terminal or source not in distances:
                    continue
                closer = tuple(
                    neighbor
                    for neighbor in graph[source]
                    if distances.get(neighbor) == distances[source] - 1
                )
                if not closer:
                    raise ValueError("reverse BFS produced a corrupt route tree")
                entries.append(
                    (RouteKey(source, target), min(closer, key=_node_key))
                )
        return RouteSlice(entries)


def _reverse_distances(
    graph: dict[NodeId, set[NodeId]],
    terminal: NodeId,
) -> dict[NodeId, int]:
    distances = {terminal: 0}
    pending = deque([terminal])
    while pending:
        current = pending.popleft()
        for neighbor in sorted(graph[current], key=_node_key):
            if neighbor not in distances:
                distances[neighbor] = distances[current] + 1
                pending.append(neighbor)
    return distances


def _topology_nodes(
    topology: Topology,
) -> tuple[set[NodeId], set[NodeId], tuple[GroundStation, ...]]:
    nodes: set[NodeId] = set()
    satellites: set[NodeId] = set()
    ground_stations: list[GroundStation] = []
    for autonomous_system in topology.autonomous_systems:
        for shell in autonomous_system.shells:
            for orbit in shell.orbits:
                for satellite in orbit.satellites:
                    nodes.add(satellite.node_id)
                    satellites.add(satellite.node_id)
        for ground_station in autonomous_system.ground_stations:
            nodes.add(ground_station.node_id)
            ground_stations.append(ground_station)
    return nodes, satellites, tuple(ground_stations)


def _node_key(node_id: NodeId) -> tuple[int, str, str]:
    return node_id.asn, node_id.entity_kind, node_id.entity_id


__all__ = ["HopCountRouteModel"]
