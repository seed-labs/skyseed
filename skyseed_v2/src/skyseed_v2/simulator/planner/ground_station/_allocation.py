"""Private architecture-neutral GroundStation access allocation kernel."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from heapq import heappop, heappush
from types import MappingProxyType
from typing import Protocol

from skyseed_v2.shared import NodeId


class _GroundAccessCandidate(Protocol):
    elevation_deg: float
    satellite_id: NodeId


@dataclass(frozen=True, slots=True)
class _GroundAccessAllocationPreference:
    ground_station_id: NodeId
    effective_limit: int
    candidates: tuple[_GroundAccessCandidate, ...]


def _allocate_continuous_ground_station_accesses(
    preferences: tuple[_GroundAccessAllocationPreference, ...],
    satellite_capacities: Mapping[NodeId, int | None],
    retained_pairs: frozenset[tuple[NodeId, NodeId]],
    *,
    work: dict[str, int] | None = None,
) -> Mapping[NodeId, frozenset[NodeId]]:
    """Lexicographic GS / satellite coverage / edges / continuity / rank.

    Unit-capacity pair arcs prevent duplicate links. The first station unit
    earns station coverage; the first satellite unit earns distinct access.
    Every useful edge earns a lower-priority cardinality reward. A retained reservation has the same continuity value
    as an operational incumbent. All costs are exact integers.
    """
    if work is None:
        work = {}
    work.clear()
    work.update(
        gs_solver_augmentations=0,
        gs_retained_optimum=0,
        gs_residual_vertices=0,
        gs_seeded_retained_assignments=0,
    )
    rows = tuple(sorted(preferences, key=lambda p: _node_key(p.ground_station_id)))
    feasible = {(row.ground_station_id, c.satellite_id)
                for row in rows if row.effective_limit for c in row.candidates}
    work["gs_candidate_edges"] = len(feasible)
    retained = retained_pairs & feasible
    selected = {row.ground_station_id: set() for row in rows}
    owners: dict[NodeId, set[NodeId]] = {}
    for station, satellite in retained:
        selected[station].add(satellite)
        owners.setdefault(satellite, set()).add(station)
    # These independent upper bounds certify all three cardinalities. They
    # are sufficient, not necessary: shortage components fall through to the
    # exact solver. Zero changes then uniquely fixes the retained edge set.
    retained_feasible = (
        all(len(selected[row.ground_station_id]) <= row.effective_limit for row in rows)
        and all(satellite_capacities[s] is None or len(stations) <= satellite_capacities[s]
                for s, stations in owners.items())
    )
    eligible_satellites = {sat for _, sat in feasible if satellite_capacities[sat] != 0}
    station_bound = sum(bool(row.effective_limit and any(
        c.satellite_id in eligible_satellites for c in row.candidates)) for row in rows)
    edge_bound = min(len(feasible), sum(row.effective_limit for row in rows),
                     sum(len(rows) if satellite_capacities[sat] is None else satellite_capacities[sat]
                         for sat in eligible_satellites))
    satellite_bound = min(len(eligible_satellites), edge_bound)
    if (retained_feasible and sum(bool(v) for v in selected.values()) == station_bound
            and len(owners) == satellite_bound and len(retained) == edge_bound):
        work["gs_retained_optimum"] = 1
        return MappingProxyType({station: frozenset(values)
                                 for station, values in selected.items()})

    satellites = tuple(sorted(satellite_capacities, key=_node_key))
    satellite_index = {node: i for i, node in enumerate(satellites)}
    ordered = tuple(tuple(sorted(row.candidates, key=lambda c: (
        -c.elevation_deg, _node_key(c.satellite_id)))) for row in rows)

    # Bound each lower-priority total before assigning the next weight.
    edge_count = len(feasible)
    quality_bound = (edge_count + 1) ** 2 * (len(satellites) + 1)
    change_weight = quality_bound + 1
    connection_weight = (2 * edge_count + 1) * change_weight
    satellite_weight = (edge_count + 1) * connection_weight
    coverage_weight = (len(satellites) + 1) * satellite_weight
    source = len(rows) + len(satellites)
    sink = source + 1
    graph: list[list[list[int]]] = [[] for _ in range(sink + 1)]

    def add(a: int, b: int, capacity: int, cost: int) -> list[int]:
        forward = [b, len(graph[b]), capacity, cost]
        reverse = [a, len(graph[a]), 0, -cost]
        graph[a].append(forward)
        graph[b].append(reverse)
        return forward

    pair_arcs = []
    station_arcs = {}
    satellite_arcs = {}

    for i, (row, candidates) in enumerate(zip(rows, ordered)):
        if not row.effective_limit:
            continue
        station_arcs[row.ground_station_id] = [add(source, i, 1, -coverage_weight)]
        if row.effective_limit > 1:
            station_arcs[row.ground_station_id].append(add(source, i, row.effective_limit - 1, 0))
        for rank, c in enumerate(candidates):
            j = satellite_index[c.satellite_id]
            pair = (row.ground_station_id, c.satellite_id)
            cost = (-change_weight if pair in retained else change_weight)
            cost += rank * (len(satellites) + 1) + j - connection_weight
            arc = add(i, len(rows) + j, 1, cost)
            pair_arcs.append((pair, arc))
    for j, satellite in enumerate(satellites):
        capacity = satellite_capacities[satellite]
        capacity = len(rows) if capacity is None else capacity
        satellite_arcs[satellite] = []
        if capacity:
            satellite_arcs[satellite].append(add(len(rows) + j, sink, 1, -satellite_weight))
        if capacity > 1:
            satellite_arcs[satellite].append(add(len(rows) + j, sink, capacity - 1, 0))

    if retained and retained_feasible:
        # Seed an exact circulation with all feasible incumbents. Negative
        # residual cycles are precisely the possible lexicographic improvements,
        # including swaps at fixed cardinality and paths that add/remove links.
        closing = add(sink, source, edge_count, 0)
        def push(arc):
            arc[2] -= 1
            graph[arc[0]][arc[1]][2] += 1
        for (station, satellite), arc in pair_arcs:
            if (station, satellite) not in retained:
                continue
            push(next(a for a in station_arcs[station] if a[2]))
            push(arc)
            push(next(a for a in satellite_arcs[satellite] if a[2]))
            push(closing)
        work["gs_seeded_retained_assignments"] = len(retained)
        work["gs_residual_vertices"] = len(graph)
        work["gs_residual_cycle_corrections"] = _cancel_negative_cycles(graph)
        work["gs_retained_optimum"] = int(work["gs_residual_cycle_corrections"] == 0)
        selected = {row.ground_station_id: set() for row in rows}
        for (station, satellite), arc in pair_arcs:
            if arc[2] == 0:
                selected[station].add(satellite)
        return MappingProxyType({station: frozenset(values) for station, values in selected.items()})

    # Initial shortest potentials use the forward DAG (negative rewards).
    potential = [0] * len(graph)
    for node in (source, *range(source)):
        for target, _, capacity, cost in graph[node]:
            if capacity:
                potential[target] = min(potential[target], potential[node] + cost)
    work["gs_residual_vertices"] = len(graph)
    while True:
        distance: list[int | None] = [None] * len(graph)
        previous = [None] * len(graph)
        distance[source] = 0
        queue = [(0, source)]
        while queue:
            current, node = heappop(queue)
            if distance[node] != current:
                continue
            if node == sink:
                break
            for index, (target, _, capacity, cost) in enumerate(graph[node]):
                if not capacity:
                    continue
                candidate = current + cost + potential[node] - potential[target]
                if distance[target] is None or candidate < distance[target]:
                    distance[target] = candidate
                    previous[target] = (node, index)
                    heappush(queue, (candidate, target))
        if distance[sink] is None or distance[sink] + potential[sink] - potential[source] >= 0:
            break
        # Truncate all potential increments at the settled sink distance.
        # This preserves feasible reduced costs without scanning the remaining
        # graph just to complete a shortest-distance tree for every augmentation.
        ceiling = distance[sink]
        for node, value in enumerate(distance):
            potential[node] += ceiling if value is None else min(value, ceiling)
        work["gs_solver_augmentations"] += 1
        node = sink
        while node != source:
            parent, index = previous[node]
            arc = graph[parent][index]
            arc[2] -= 1
            graph[node][arc[1]][2] += 1
            node = parent
    selected = {row.ground_station_id: set() for row in rows}
    for (station, satellite), arc in pair_arcs:
        if arc[2] == 0:
            selected[station].add(satellite)
    return MappingProxyType({station: frozenset(values)
                             for station, values in selected.items()})


def _cancel_negative_cycles(graph):
    """Improve a feasible integer circulation until residual optimality holds.

    Queue Bellman-Ford starts from an implicit zero-cost super-source. A pass
    with no negative cycle certifies global optimality, even when loose global
    cardinality bounds cannot certify a retained assignment.
    """
    size = len(graph)
    corrections = 0
    while True:
        distance = [0] * size
        previous = [None] * size
        depth = [0] * size
        pending = deque(range(size))
        queued = [True] * size
        cycle = None
        relaxations = 0
        while pending and cycle is None:
            node = pending.popleft()
            queued[node] = False
            for index, (target, _, capacity, cost) in enumerate(graph[node]):
                if capacity and distance[target] > distance[node] + cost:
                    distance[target] = distance[node] + cost
                    previous[target] = node, index
                    relaxations += 1
                    if relaxations % size == 0:
                        cycle = _negative_predecessor_cycle(graph, previous)
                        if cycle is not None:
                            break
                    depth[target] = depth[node] + 1
                    if depth[target] >= size:
                        cursor = target
                        for _ in range(size):
                            cursor = previous[cursor][0]
                        start = cursor
                        cycle = []
                        while True:
                            parent, edge = previous[cursor]
                            cycle.append((parent, edge))
                            cursor = parent
                            if cursor == start:
                                break
                        if sum(graph[n][i][3] for n, i in cycle) >= 0:
                            raise RuntimeError("residual cycle certificate is inconsistent")
                        break
                    if not queued[target]:
                        pending.append(target)
                        queued[target] = True
        if cycle is None:
            return corrections
        amount = min(graph[n][i][2] for n, i in cycle)
        for node, index in cycle:
            arc = graph[node][index]
            arc[2] -= amount
            graph[arc[0]][arc[1]][2] += amount
        corrections += 1


def _negative_predecessor_cycle(graph, previous):
    visited = set()
    for start in range(len(graph)):
        path, offsets = [], {}
        node = start
        while node not in visited and previous[node] is not None:
            offsets[node] = len(path)
            path.append(node)
            visited.add(node)
            node = previous[node][0]
        if node in offsets:
            cycle = [previous[n] for n in path[offsets[node]:]]
            if sum(graph[n][i][3] for n, i in cycle) < 0:
                return cycle
    return None


def _node_key(node_id: NodeId) -> tuple[int, str, str]:
    return node_id.asn, node_id.entity_kind, node_id.entity_id
