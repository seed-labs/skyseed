"""Private architecture-neutral GroundStation access allocation kernel."""

from __future__ import annotations

from bisect import insort
from collections.abc import Mapping
from dataclasses import dataclass
from heapq import heappop, heappush
from types import MappingProxyType

from skyseed_v2.shared import NodeId


@dataclass(frozen=True, slots=True)
class _GroundAccessChoice:
    elevation_deg: float
    satellite_id: NodeId


@dataclass(frozen=True, slots=True)
class _GroundAccessAllocationPreference:
    ground_station_id: NodeId
    effective_limit: int
    candidates: tuple[_GroundAccessChoice, ...]


_IndexedCandidate = tuple[int, int]


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
