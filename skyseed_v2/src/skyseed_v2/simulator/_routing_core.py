"""Private architecture-neutral deterministic routing primitives.

The public Planner's Route models use this module for bounded shortest-path
work. It knows PhysicalWorld node roles and undirected operational edges, but
deliberately owns no planning state, publication values, or backend facts.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from skyseed_v2.shared import NodeId

from .physical import PhysicalWorld


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


def _eligible_ground_next_hops(
    context: _RoutingCoreContext,
    terminal: NodeId,
    *,
    required_sources: frozenset[NodeId] | None = None,
) -> Mapping[NodeId, tuple[NodeId, ...]]:
    """Return shortest choices for Satellite sources to one GS terminal."""

    _validate_terminal(context, terminal, "ground-station")
    permitted = context.satellites | {terminal}
    sources = (
        context.satellites
        if required_sources is None
        else context.satellites & required_sources
    )
    distances = _reverse_distances(
        context.graph,
        terminal,
        permitted,
        None if required_sources is None else sources,
    )
    result = {
        source: _closer_neighbors(
            context.graph, source, distances, permitted
        )
        for source in sources
        if source in distances and source != terminal
    }
    return dict(sorted(result.items(), key=lambda item: _node_key(item[0])))


def _eligible_zone_next_hops(
    context: _RoutingCoreContext,
    terminal: NodeId,
    *,
    required_sources: frozenset[NodeId] | None = None,
) -> Mapping[NodeId, tuple[NodeId, ...]]:
    """Return shortest choices to one Zone's service-Satellite terminal."""

    _validate_terminal(context, terminal, "satellite")
    ground_sources = (
        context.ground_stations
        if required_sources is None
        else context.ground_stations & required_sources
    )
    satellite_sources = (
        context.satellites
        if required_sources is None
        else context.satellites & required_sources
    )
    required_satellites = (
        None
        if required_sources is None
        else frozenset(
            satellite_sources
            | {
                neighbor
                for source in ground_sources
                for neighbor in context.graph[source]
                if neighbor in context.satellites
            }
        )
    )
    distances = _reverse_distances(
        context.graph,
        terminal,
        context.satellites,
        required_satellites,
    )
    result = {
        source: _closer_neighbors(
            context.graph,
            source,
            distances,
            context.satellites,
        )
        for source in satellite_sources
        if source in distances and source != terminal
    }
    for source in ground_sources:
        candidates = tuple(
            neighbor
            for neighbor in context.graph[source]
            if neighbor in context.satellites and neighbor in distances
        )
        if candidates:
            minimum_distance = min(distances[value] for value in candidates)
            result[source] = tuple(
                value
                for value in candidates
                if distances[value] == minimum_distance
            )
    return dict(sorted(result.items(), key=lambda item: _node_key(item[0])))


def _required_ground_next_hops(
    context: _RoutingCoreContext,
    terminal: NodeId,
    required_sources: frozenset[NodeId],
) -> Mapping[NodeId, tuple[NodeId, ...]]:
    """Materialize only demanded Satellite-to-GS tree branches."""

    _validate_required_sources(required_sources)
    _validate_terminal(context, terminal, "ground-station")
    permitted = context.satellites | {terminal}
    sources = required_sources & context.satellites
    distances = _reverse_distances(
        context.graph, terminal, permitted, sources
    )
    result: dict[NodeId, tuple[NodeId, ...]] = {}
    for source in sorted(sources, key=_node_key):
        if source in distances:
            _materialize_required_branch(
                result,
                context.graph,
                source,
                terminal,
                distances,
                permitted,
            )
    return dict(sorted(result.items(), key=lambda item: _node_key(item[0])))


def _required_zone_next_hops(
    context: _RoutingCoreContext,
    terminal: NodeId,
    required_sources: frozenset[NodeId],
) -> Mapping[NodeId, tuple[NodeId, ...]]:
    """Materialize only demanded GS-to-served-Zone tree branches."""

    _validate_required_sources(required_sources)
    _validate_terminal(context, terminal, "satellite")
    ground_sources = required_sources & context.ground_stations
    satellite_sources = required_sources & context.satellites
    required_satellites = frozenset(
        satellite_sources
        | {
            neighbor
            for source in ground_sources
            for neighbor in context.graph[source]
            if neighbor in context.satellites
        }
    )
    distances = _reverse_distances(
        context.graph,
        terminal,
        context.satellites,
        required_satellites,
    )
    result: dict[NodeId, tuple[NodeId, ...]] = {}
    for source in sorted(satellite_sources, key=_node_key):
        if source in distances and source != terminal:
            _materialize_required_branch(
                result,
                context.graph,
                source,
                terminal,
                distances,
                context.satellites,
            )
    for source in sorted(ground_sources, key=_node_key):
        candidates = tuple(
            neighbor
            for neighbor in context.graph[source]
            if neighbor in context.satellites and neighbor in distances
        )
        if not candidates:
            continue
        minimum_distance = min(distances[value] for value in candidates)
        choices = tuple(
            value
            for value in candidates
            if distances[value] == minimum_distance
        )
        result[source] = choices
        _materialize_required_branch(
            result,
            context.graph,
            choices[0],
            terminal,
            distances,
            context.satellites,
        )
    return dict(sorted(result.items(), key=lambda item: _node_key(item[0])))


def _route_step_allows_transit(
    *,
    source: NodeId,
    current: NodeId,
    next_hop: NodeId,
    terminal: NodeId,
    ground_station_target: bool,
) -> bool:
    if ground_station_target:
        return (
            source.entity_kind == "satellite"
            and current.entity_kind == "satellite"
            and (
                next_hop.entity_kind == "satellite" or next_hop == terminal
            )
        )
    if current.entity_kind == "ground-station":
        return current == source and next_hop.entity_kind == "satellite"
    return (
        current.entity_kind == "satellite"
        and next_hop.entity_kind == "satellite"
    )


def _materialize_required_branch(
    result: dict[NodeId, tuple[NodeId, ...]],
    graph: Mapping[NodeId, tuple[NodeId, ...]],
    source: NodeId,
    terminal: NodeId,
    distances: Mapping[NodeId, int],
    permitted: frozenset[NodeId],
) -> None:
    current = source
    while current != terminal:
        choices = _closer_neighbors(graph, current, distances, permitted)
        if not choices:
            return
        existing = result.get(current)
        if existing is not None and existing != choices:
            raise ValueError("minimum-hop routing tree is inconsistent")
        result[current] = choices
        current = choices[0]


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


def _validate_terminal(
    context: _RoutingCoreContext, terminal: NodeId, entity_kind: str
) -> None:
    if not isinstance(context, _RoutingCoreContext):
        raise TypeError("context must be a _RoutingCoreContext")
    if not isinstance(terminal, NodeId):
        raise TypeError("terminal must be a NodeId")
    expected = (
        context.ground_stations
        if entity_kind == "ground-station"
        else context.satellites
    )
    if terminal not in expected:
        raise ValueError(f"terminal must identify a PhysicalWorld {entity_kind}")


def _validate_required_sources(required_sources: frozenset[NodeId]) -> None:
    if not isinstance(required_sources, frozenset):
        raise TypeError("required_sources must be a frozenset")
    if any(not isinstance(value, NodeId) for value in required_sources):
        raise TypeError("required_sources must contain NodeId values")


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
