"""Private architecture-neutral GroundStation access allocation kernel."""

from __future__ import annotations

from bisect import insort
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from heapq import heappop, heappush
from types import MappingProxyType
from typing import Protocol

from skyseed_v2.shared import NodeId


@dataclass(frozen=True, slots=True)
class _GroundAccessChoice:
    elevation_deg: float
    satellite_id: NodeId


class _GroundAccessCandidate(Protocol):
    elevation_deg: float
    satellite_id: NodeId


@dataclass(frozen=True, slots=True)
class _GroundAccessAllocationPreference:
    ground_station_id: NodeId
    effective_limit: int
    candidates: tuple[_GroundAccessCandidate, ...]


_IndexedCandidate = tuple[int, int]


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


def _allocate_ground_station_accesses(
    preferences: tuple[_GroundAccessAllocationPreference, ...],
    satellite_capacities: Mapping[NodeId, int | None],
) -> Mapping[NodeId, frozenset[NodeId]]:
    """Allocate maximum-cardinality quality primaries, then redundancy."""

    ordered_preferences = tuple(
        sorted(preferences, key=lambda value: _node_key(value.ground_station_id))
    )
    capacities = dict(satellite_capacities)
    if all(value is None for value in capacities.values()):
        return MappingProxyType(
            {
                value.ground_station_id: frozenset(
                    candidate.satellite_id
                    for candidate in sorted(
                        value.candidates,
                        key=lambda candidate: (
                            -candidate.elevation_deg,
                            _node_key(candidate.satellite_id),
                        ),
                    )[: value.effective_limit]
                )
                for value in ordered_preferences
            }
        )

    ordered_candidates_by_ground_station = {
        value.ground_station_id: tuple(
            sorted(
                value.candidates,
                key=lambda candidate: (
                    -candidate.elevation_deg,
                    _node_key(candidate.satellite_id),
                ),
            )
        )
        for value in ordered_preferences
    }
    finite_default = len(ordered_preferences)
    capacity_by_satellite = {
        node_id: finite_default if value is None else value
        for node_id, value in capacities.items()
    }
    (
        ground_station_ids,
        satellite_ids,
        indexed_candidates,
        indexed_capacities,
    ) = _index_primary_problem(
        ordered_candidates_by_ground_station, capacity_by_satellite
    )
    primary_satellite_by_ground_station = _select_primary_indexes(
        indexed_candidates, indexed_capacities
    )

    ground_station_count = len(ground_station_ids)
    selected: list[set[int]] = [set() for _ in ground_station_ids]
    frozen_primary_count_by_satellite = [0] * len(satellite_ids)
    for ground_station, satellite in enumerate(
        primary_satellite_by_ground_station
    ):
        if satellite == -1:
            continue
        selected[ground_station].add(satellite)
        frozen_primary_count_by_satellite[satellite] += 1

    assigned_redundancy: dict[int, int] = {}
    redundant_requests_by_satellite: list[list[int]] = [
        [] for _ in satellite_ids
    ]
    used_by_ground_station = [set(values) for values in selected]

    def move(request: int, satellite: int) -> None:
        ground_station = request % ground_station_count
        previous = assigned_redundancy.get(request)
        if previous is not None:
            redundant_requests_by_satellite[previous].remove(request)
            used_by_ground_station[ground_station].remove(previous)
        assigned_redundancy[request] = satellite
        insort(redundant_requests_by_satellite[satellite], request)
        used_by_ground_station[ground_station].add(satellite)

    def augment(
        request: int,
        visited_requests: set[int],
        visited_satellites: set[int],
    ) -> bool:
        if request in visited_requests:
            return False
        visited_requests.add(request)
        ground_station = request % ground_station_count
        current = assigned_redundancy.get(request)
        for satellite, _cost in indexed_candidates[ground_station]:
            if (
                satellite == current
                or satellite in visited_satellites
                or satellite in used_by_ground_station[ground_station]
            ):
                continue
            visited_satellites.add(satellite)
            occupants = redundant_requests_by_satellite[satellite]
            if (
                frozen_primary_count_by_satellite[satellite] + len(occupants)
                < indexed_capacities[satellite]
            ):
                move(request, satellite)
                return True
            for occupant in reversed(occupants):
                if augment(occupant, visited_requests, visited_satellites):
                    move(request, satellite)
                    return True
        return False

    maximum_limit = max(
        (value.effective_limit for value in ordered_preferences), default=0
    )
    for slot in range(1, maximum_limit):
        for ground_station in range(ground_station_count - 1, -1, -1):
            if (
                slot < ordered_preferences[ground_station].effective_limit
                and primary_satellite_by_ground_station[ground_station] != -1
            ):
                augment(
                    slot * ground_station_count + ground_station, set(), set()
                )

    for request, satellite in assigned_redundancy.items():
        selected[request % ground_station_count].add(satellite)
    return MappingProxyType(
        {
            ground_station_id: frozenset(
                satellite_ids[satellite]
                for satellite in selected[ground_station]
            )
            for ground_station, ground_station_id in enumerate(
                ground_station_ids
            )
        }
    )


def _select_primary_ground_station_accesses(
    candidates_by_ground_station: Mapping[
        NodeId, tuple[_GroundAccessChoice, ...]
    ],
    capacity_by_satellite: Mapping[NodeId, int],
) -> Mapping[NodeId, NodeId]:
    """Select accepted deterministic capacitated-assignment primaries."""

    (
        ground_station_ids,
        satellite_ids,
        indexed_candidates,
        indexed_capacities,
    ) = _index_primary_problem(
        candidates_by_ground_station, capacity_by_satellite
    )
    selected = _select_primary_indexes(
        indexed_candidates, indexed_capacities
    )
    return MappingProxyType(
        {
            ground_station_ids[ground_station]: satellite_ids[satellite]
            for ground_station, satellite in enumerate(selected)
            if satellite != -1
        }
    )


def _index_primary_problem(
    candidates_by_ground_station: Mapping[
        NodeId, tuple[_GroundAccessChoice, ...]
    ],
    capacity_by_satellite: Mapping[NodeId, int],
) -> tuple[
    tuple[NodeId, ...],
    tuple[NodeId, ...],
    tuple[tuple[_IndexedCandidate, ...], ...],
    tuple[int, ...],
]:
    ground_station_ids = tuple(
        sorted(candidates_by_ground_station, key=_node_key)
    )
    satellite_ids = tuple(sorted(capacity_by_satellite, key=_node_key))
    satellite_index = {
        node_id: index for index, node_id in enumerate(satellite_ids)
    }
    rank_scale = len(ground_station_ids) * max(1, len(satellite_ids)) + 1
    indexed_candidates: list[tuple[_IndexedCandidate, ...]] = []
    for ground_station_id in ground_station_ids:
        row: list[_IndexedCandidate] = []
        for rank, candidate in enumerate(
            candidates_by_ground_station[ground_station_id], start=1
        ):
            try:
                satellite = satellite_index[candidate.satellite_id]
            except KeyError:
                raise KeyError(
                    "missing Satellite capacity: "
                    f"{candidate.satellite_id.value}"
                ) from None
            row.append((satellite, rank * rank_scale + satellite))
        indexed_candidates.append(tuple(row))
    return (
        ground_station_ids,
        satellite_ids,
        tuple(indexed_candidates),
        tuple(capacity_by_satellite[node_id] for node_id in satellite_ids),
    )


def _select_primary_indexes(
    candidates_by_ground_station: tuple[tuple[_IndexedCandidate, ...], ...],
    capacity_by_satellite: tuple[int, ...],
) -> tuple[int, ...]:
    """Solve the accepted global sparse capacitated assignment.

    The objective is maximum cardinality, then minimum aggregate candidate
    rank, then minimum aggregate global Satellite order. Remaining equal-cost
    optima retain the accepted successive-shortest-path identity: global node
    order, strict distance improvement, and full reachable-potential updates.
    """

    direct = _direct_primary_indexes(
        candidates_by_ground_station, capacity_by_satellite
    )
    if direct is not None:
        return direct
    return _run_primary_successive_shortest_paths(
        candidates_by_ground_station, capacity_by_satellite
    )


def _direct_primary_indexes(
    candidates_by_ground_station: tuple[tuple[_IndexedCandidate, ...], ...],
    capacity_by_satellite: tuple[int, ...],
) -> tuple[int, ...] | None:
    """Return the unique all-first-choice optimum when no cap conflicts."""

    selected = [-1] * len(candidates_by_ground_station)
    used = [0] * len(capacity_by_satellite)
    for ground_station, candidates in enumerate(candidates_by_ground_station):
        if not candidates:
            continue
        satellite = candidates[0][0]
        used[satellite] += 1
        if used[satellite] > capacity_by_satellite[satellite]:
            return None
        selected[ground_station] = satellite
    return tuple(selected)


def _run_primary_successive_shortest_paths(
    candidates_by_ground_station: tuple[tuple[_IndexedCandidate, ...], ...],
    capacity_by_satellite: tuple[int, ...],
) -> tuple[int, ...]:
    """Run the accepted min-cost flow as an indexed bipartite residual graph."""

    ground_station_count = len(candidates_by_ground_station)
    satellite_count = len(capacity_by_satellite)
    source = ground_station_count + satellite_count
    sink = source + 1
    node_count = sink + 1
    selected_satellite_by_ground_station = [-1] * ground_station_count
    selected_cost_by_ground_station = [0] * ground_station_count
    selected_ground_stations_by_satellite: list[list[int]] = [
        [] for _ in range(satellite_count)
    ]
    used_satellites: list[int] = []
    potential = [0] * node_count
    infinity = 1 << 60

    while True:
        distances = [infinity] * node_count
        predecessor_ground_station = [-2] * ground_station_count
        predecessor_satellite = [-1] * satellite_count
        predecessor_satellite_cost = [0] * satellite_count
        predecessor_sink = -1
        distances[source] = 0
        pending = [(0, source)]
        while pending:
            distance, node = heappop(pending)
            if distance != distances[node]:
                continue
            if node == source:
                for ground_station in range(ground_station_count):
                    if selected_satellite_by_ground_station[ground_station] != -1:
                        continue
                    candidate_distance = (
                        distance
                        + potential[source]
                        - potential[ground_station]
                    )
                    if candidate_distance >= distances[ground_station]:
                        continue
                    distances[ground_station] = candidate_distance
                    predecessor_ground_station[ground_station] = -1
                    heappush(
                        pending, (candidate_distance, ground_station)
                    )
                continue
            if node < ground_station_count:
                ground_station = node
                for satellite, cost in candidates_by_ground_station[
                    ground_station
                ]:
                    if (
                        selected_satellite_by_ground_station[ground_station]
                        == satellite
                    ):
                        continue
                    target = ground_station_count + satellite
                    candidate_distance = (
                        distance
                        + cost
                        + potential[ground_station]
                        - potential[target]
                    )
                    if candidate_distance >= distances[target]:
                        continue
                    distances[target] = candidate_distance
                    predecessor_satellite[satellite] = ground_station
                    predecessor_satellite_cost[satellite] = cost
                    heappush(pending, (candidate_distance, target))
                continue
            if node < source:
                satellite = node - ground_station_count
                occupants = selected_ground_stations_by_satellite[satellite]
                if len(occupants) < capacity_by_satellite[satellite]:
                    candidate_distance = (
                        distance + potential[node] - potential[sink]
                    )
                    if candidate_distance < distances[sink]:
                        distances[sink] = candidate_distance
                        predecessor_sink = satellite
                        heappush(pending, (candidate_distance, sink))
                for ground_station in occupants:
                    candidate_distance = (
                        distance
                        - selected_cost_by_ground_station[ground_station]
                        + potential[node]
                        - potential[ground_station]
                    )
                    if candidate_distance >= distances[ground_station]:
                        continue
                    distances[ground_station] = candidate_distance
                    predecessor_ground_station[ground_station] = satellite
                    heappush(
                        pending, (candidate_distance, ground_station)
                    )
                continue

            for satellite in used_satellites:
                target = ground_station_count + satellite
                candidate_distance = (
                    distance + potential[sink] - potential[target]
                )
                if candidate_distance >= distances[target]:
                    continue
                distances[target] = candidate_distance
                predecessor_satellite[satellite] = -2
                heappush(pending, (candidate_distance, target))

        if predecessor_sink == -1:
            return tuple(selected_satellite_by_ground_station)
        for node, distance in enumerate(distances):
            if distance != infinity:
                potential[node] += distance

        satellite = predecessor_sink
        newly_used_satellite = not selected_ground_stations_by_satellite[
            satellite
        ]
        while True:
            ground_station = predecessor_satellite[satellite]
            if ground_station < 0:
                raise RuntimeError(
                    "GroundStation augmenting path traversed sink internally"
                )
            previous_satellite = selected_satellite_by_ground_station[
                ground_station
            ]
            if previous_satellite != -1:
                selected_ground_stations_by_satellite[
                    previous_satellite
                ].remove(ground_station)
            selected_satellite_by_ground_station[ground_station] = satellite
            selected_cost_by_ground_station[ground_station] = (
                predecessor_satellite_cost[satellite]
            )
            insort(
                selected_ground_stations_by_satellite[satellite],
                ground_station,
            )
            if previous_satellite == -1:
                if predecessor_ground_station[ground_station] != -1:
                    raise RuntimeError(
                        "GroundStation augmenting path did not reach source"
                    )
                break
            if (
                predecessor_ground_station[ground_station]
                != previous_satellite
            ):
                raise RuntimeError(
                    "GroundStation alternating predecessor mismatch"
                )
            satellite = previous_satellite
        if newly_used_satellite:
            insort(used_satellites, predecessor_sink)


def _node_key(node_id: NodeId) -> tuple[int, str, str]:
    return node_id.asn, node_id.entity_kind, node_id.entity_id
