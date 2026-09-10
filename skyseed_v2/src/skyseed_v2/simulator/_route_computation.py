"""Private grouped, demand-bounded route path computation engine."""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from multiprocessing import get_context
import os

from skyseed_v2.shared import NodeId

from ._routing_core import (
    _RoutingCoreContext,
    _required_ground_next_hops,
    _required_zone_next_hops,
)


_MAXIMUM_ROUTE_WORKERS = 8


@dataclass(frozen=True, slots=True)
class _RoutePathRequest:
    request_id: int
    ground_station_target: bool
    source: NodeId
    terminal: NodeId

    def __post_init__(self) -> None:
        if (
            isinstance(self.request_id, bool)
            or not isinstance(self.request_id, int)
            or self.request_id < 0
        ):
            raise ValueError("request_id must be a non-negative integer")
        if not isinstance(self.ground_station_target, bool):
            raise TypeError("ground_station_target must be a bool")
        if not isinstance(self.source, NodeId):
            raise TypeError("source must be a NodeId")
        if not isinstance(self.terminal, NodeId):
            raise TypeError("terminal must be a NodeId")


@dataclass(frozen=True, slots=True)
class _RouteWorkerActivity:
    """Describe actual non-empty work completed by one process."""

    pid: int
    non_empty_job_count: int
    request_count: int
    chunk_count: int


@dataclass(frozen=True, slots=True)
class _RouteComputeObservation:
    """Retain only the latest bounded Route-worker execution observation."""

    configured_maximum_workers: int
    selected_worker_count: int
    executor_generation: int
    executor_created: bool
    executor_reused: bool
    worker_activities: tuple[_RouteWorkerActivity, ...]
    reused_request_count: int = 0


@dataclass(frozen=True, slots=True)
class _RouteExecutorLifecycle:
    """Expose bounded scalar lifecycle facts for qualification evidence."""

    executor_generation: int
    executor_shutdown_count: int
    executor_live: bool


@dataclass(frozen=True, slots=True)
class _RouteWorkerResult:
    """Return unchanged paths plus exact process-local work accounting."""

    pid: int
    non_empty_job_count: int
    request_count: int
    paths: dict[int, tuple[NodeId, ...] | None]


class _GroupedRouteEngine:
    """Reuse bounded worker processes across grouped route recomputations."""

    __slots__ = (
        "_context",
        "_satellite_graph",
        "_components",
        "_component_signatures",
        "_path_cache",
        "_indexed_payload",
        "_ordered_nodes",
        "_node_indexes",
        "_executor",
        "_executor_worker_count",
        "_worker_count",
        "_executor_generation",
        "_executor_shutdown_count",
        "_last_compute_observation",
    )

    def __init__(self) -> None:
        self._satellite_graph = None
        self._components = {}
        self._component_signatures = {}
        self._path_cache = {}
        self._context = None
        self._indexed_payload = None
        self._ordered_nodes = ()
        self._node_indexes = {}
        self._executor: ProcessPoolExecutor | None = None
        self._executor_worker_count = 0
        self._worker_count = 1
        self._executor_generation = 0
        self._executor_shutdown_count = 0
        self._last_compute_observation: _RouteComputeObservation | None = None

    def __del__(self) -> None:
        self.shutdown(wait=False)

    def compute(
        self,
        context: _RoutingCoreContext,
        requests: tuple[_RoutePathRequest, ...],
        *,
        choice_resolvers: tuple[object, object] | None = None,
    ) -> dict[int, tuple[NodeId, ...] | None]:
        if not isinstance(context, _RoutingCoreContext):
            raise TypeError("context must be a _RoutingCoreContext")
        values = tuple(requests)
        if any(not isinstance(value, _RoutePathRequest) for value in values):
            raise TypeError("requests must contain _RoutePathRequest values")
        identities = tuple(value.request_id for value in values)
        if len(identities) != len(set(identities)):
            raise ValueError("route path request IDs must be unique")
        cached_paths = {}
        dependencies = {}
        current_cache = {}
        if choice_resolvers is None:
            # GSs are endpoints, never transit nodes. Depend on complete ISL
            # components, including additions and splits, rather than old path
            # edges. Component tuples are shared by all their nodes/requests.
            satellite_graph = tuple(
                (node, tuple(neighbor for neighbor in context.graph[node]
                             if neighbor in context.satellites))
                for node in sorted(context.satellites, key=_node_key)
            )
            if satellite_graph != self._satellite_graph:
                graph = dict(satellite_graph)
                components = {}
                signatures = {}
                for node in graph:
                    if node in components:
                        continue
                    reached, pending = {node}, [node]
                    while pending:
                        for neighbor in graph[pending.pop()]:
                            if neighbor not in reached:
                                reached.add(neighbor)
                                pending.append(neighbor)
                    signature = tuple((n, graph[n]) for n in sorted(reached, key=_node_key))
                    previous = self._component_signatures.get(node)
                    token = previous[1] if previous is not None and previous[0] == signature else object()
                    signatures[node] = signature, token
                    for member in reached:
                        components[member] = token
                self._component_signatures = signatures
                self._components = components
                self._satellite_graph = satellite_graph
            for request in values:
                key = request.ground_station_target, request.terminal, request.source
                accesses = (context.graph[request.source] if request.source in context.ground_stations
                            else (request.source,))
                component_dependencies = tuple(dict.fromkeys(
                    self._components[n] for n in accesses if n in self._components))
                dependency = (
                    component_dependencies,
                    context.graph[request.terminal]
                    if request.ground_station_target else (),
                    context.graph[request.source]
                    if request.source in context.ground_stations else (),
                )
                dependencies[request.request_id] = key, dependency
                previous = self._path_cache.get(key)
                if previous is not None and previous[0] == dependency:
                    cached_paths[request.request_id] = previous[1]
                    current_cache[key] = previous
            values = tuple(r for r in values if r.request_id not in cached_paths)

        def finish(paths):
            if choice_resolvers is None:
                for identifier, path in paths.items():
                    key, dependency = dependencies[identifier]
                    current_cache[key] = dependency, path
                self._path_cache = current_cache
            return dict(sorted((cached_paths | paths).items()))

        grouped: dict[
            tuple[bool, NodeId], list[_RoutePathRequest]
        ] = {}
        for request in sorted(values, key=_request_key):
            grouped.setdefault(
                (request.ground_station_target, request.terminal), []
            ).append(request)
        jobs = tuple(
            (key[0], key[1], tuple(grouped[key]))
            for key in sorted(
                grouped,
                key=lambda value: (
                    0 if value[0] else 1,
                    _node_key(value[1]),
                ),
            )
        )
        worker_count = _bounded_route_worker_count(
            len(context.nodes), len(jobs)
        ) if jobs else 1
        self._worker_count = worker_count
        if choice_resolvers is not None and (
            not isinstance(choice_resolvers, tuple)
            or len(choice_resolvers) != 2
            or any(not callable(value) for value in choice_resolvers)
        ):
            raise TypeError("choice_resolvers must contain two callables")
        indexed = choice_resolvers is None
        if indexed:
            if self._context is not context:
                if context.nodes != self._node_indexes.keys():
                    self._ordered_nodes = tuple(sorted(context.nodes, key=_node_key))
                    self._node_indexes = {node: i for i, node in enumerate(self._ordered_nodes)}
                self._indexed_payload = (
                    tuple(tuple(self._node_indexes[n] for n in context.graph[node]) for node in self._ordered_nodes),
                    tuple(node in context.satellites for node in self._ordered_nodes),
                )
                self._context = context
            ordered_nodes, indexes = self._ordered_nodes, self._node_indexes
            jobs = tuple((ground, indexes[terminal], tuple(
                (r.request_id, indexes[r.source]) for r in requests))
                for ground, terminal, requests in jobs)
            payload = self._indexed_payload
            worker_function = _compute_indexed_route_jobs
        else:
            payload = (context.nodes, context.satellites,
                       context.ground_stations, dict(context.graph))
            worker_function = _compute_route_path_jobs
        def public_paths(paths):
            return {key: None if path is None else tuple(ordered_nodes[i] for i in path)
                    for key, path in paths.items()} if indexed else paths
        if worker_count == 1:
            worker_result = worker_function(
                (payload, jobs, choice_resolvers)
            )
            self._last_compute_observation = _route_compute_observation(
                worker_count,
                self._executor_generation,
                executor_created=False,
                executor_reused=False,
                worker_results=(worker_result,),
                reused_request_count=len(cached_paths),
            )
            return finish(public_paths(worker_result.paths))
        executor_created = False
        executor_reused = False
        if (
            self._executor is None
            or self._executor_worker_count != worker_count
        ):
            self.shutdown()
            self._executor = ProcessPoolExecutor(
                max_workers=worker_count,
                mp_context=get_context("spawn"),
            )
            self._executor_worker_count = worker_count
            self._executor_generation += 1
            executor_created = True
        else:
            executor_reused = True
        assert self._executor is not None
        partials = self._executor.map(
            worker_function,
            (
                (payload, chunk, choice_resolvers)
                for chunk in _balanced_chunks(jobs, worker_count)
            ),
        )
        result: dict[int, tuple[NodeId, ...] | None] = {}
        worker_results = tuple(partials)
        for partial in worker_results:
            overlap = set(result) & set(partial.paths)
            if overlap:
                raise ValueError("route workers returned duplicate requests")
            result.update(partial.paths)
        self._last_compute_observation = _route_compute_observation(
            worker_count,
            self._executor_generation,
            executor_created=executor_created,
            executor_reused=executor_reused,
            worker_results=worker_results,
            reused_request_count=len(cached_paths),
        )
        return finish(public_paths(result))

    def shutdown(self, *, wait: bool = True) -> None:
        executor = getattr(self, "_executor", None)
        if executor is None:
            return
        self._executor = None
        self._executor_worker_count = 0
        executor.shutdown(wait=wait, cancel_futures=True)
        self._executor_shutdown_count += 1

    @property
    def worker_count(self) -> int:
        return self._worker_count

    @property
    def last_compute_observation(self) -> _RouteComputeObservation | None:
        return self._last_compute_observation

    @property
    def executor_lifecycle(self) -> _RouteExecutorLifecycle:
        return _RouteExecutorLifecycle(
            self._executor_generation,
            self._executor_shutdown_count,
            self._executor is not None,
        )


def _compute_route_path_jobs(payload_and_jobs) -> _RouteWorkerResult:
    payload, jobs, choice_resolvers = payload_and_jobs
    nodes, satellites, ground_stations, graph = payload
    context = _RoutingCoreContext(
        nodes, satellites, ground_stations, graph
    )
    results: dict[int, tuple[NodeId, ...] | None] = {}
    for ground_target, terminal, requests in jobs:
        sources = frozenset(value.source for value in requests)
        if choice_resolvers is None:
            choices = (
                _required_ground_next_hops(context, terminal, sources)
                if ground_target
                else _required_zone_next_hops(context, terminal, sources)
            )
        else:
            resolver = choice_resolvers[0 if ground_target else 1]
            choices = resolver(context, terminal, sources)
        for request in requests:
            results[request.request_id] = _resolve_path(
                request.source, terminal, choices
            )
    return _RouteWorkerResult(
        pid=os.getpid(),
        non_empty_job_count=len(jobs),
        request_count=sum(len(requests) for _, _, requests in jobs),
        paths=results,
    )


def _compute_indexed_route_jobs(payload_and_jobs):
    """Execute the same hop-count/first-canonical-neighbor policy on integers."""
    from collections import deque
    (graph, satellite), jobs, _ = payload_and_jobs
    result = {}
    for ground_target, terminal, requests in jobs:
        required = set()
        for _, source in requests:
            if satellite[source]:
                required.add(source)
            elif not ground_target:
                required.update(n for n in graph[source] if satellite[n])
        distance = [-1] * len(graph)
        distance[terminal] = 0
        required.discard(terminal)
        pending = deque([terminal])
        while pending and required:
            current = pending.popleft()
            for neighbor in graph[current]:
                if (satellite[neighbor] or (ground_target and neighbor == terminal)) and distance[neighbor] < 0:
                    distance[neighbor] = distance[current] + 1
                    required.discard(neighbor)
                    pending.append(neighbor)
        branches = {terminal: (terminal,)}
        def branch(source):
            if distance[source] < 0:
                return None
            walked = []
            current = source
            while current not in branches:
                walked.append(current)
                current = next(n for n in graph[current]
                    if (satellite[n] or (ground_target and n == terminal))
                    and distance[n] == distance[current]-1)
            path = branches[current]
            for node in reversed(walked):
                path = (node,) + path
                branches[node] = path
            return branches[source]
        for identifier, source in requests:
            if source == terminal:
                path = (source,)
            elif satellite[source]:
                path = branch(source)
            elif ground_target:
                path = None
            else:
                choices = [n for n in graph[source] if satellite[n] and distance[n] >= 0]
                neighbor = min(choices, key=lambda n: (distance[n], n)) if choices else None
                path = None if neighbor is None else (source,) + branch(neighbor)
            result[identifier] = path
    return _RouteWorkerResult(os.getpid(), len(jobs),
        sum(len(requests) for _, _, requests in jobs), result)


def _route_compute_observation(
    worker_count: int,
    executor_generation: int,
    *,
    executor_created: bool,
    executor_reused: bool,
    worker_results: tuple[_RouteWorkerResult, ...],
    reused_request_count: int = 0,
) -> _RouteComputeObservation:
    activity_by_pid: dict[int, list[int]] = {}
    for result in worker_results:
        if result.non_empty_job_count == 0:
            continue
        counts = activity_by_pid.setdefault(result.pid, [0, 0, 0])
        counts[0] += result.non_empty_job_count
        counts[1] += result.request_count
        counts[2] += 1
    return _RouteComputeObservation(
        configured_maximum_workers=_MAXIMUM_ROUTE_WORKERS,
        selected_worker_count=worker_count,
        executor_generation=executor_generation,
        executor_created=executor_created,
        executor_reused=executor_reused,
        reused_request_count=reused_request_count,
        worker_activities=tuple(
            _RouteWorkerActivity(pid, counts[0], counts[1], counts[2])
            for pid, counts in sorted(activity_by_pid.items())
        ),
    )


def _resolve_path(
    source: NodeId,
    terminal: NodeId,
    choices,
) -> tuple[NodeId, ...] | None:
    if source == terminal:
        return (source,)
    path = [source]
    seen = {source}
    current = source
    while current != terminal:
        available = choices.get(current, ())
        if not available:
            return None
        current = available[0]
        if current in seen:
            raise ValueError("minimum-hop routing tree contains a loop")
        seen.add(current)
        path.append(current)
    return tuple(path)


def _balanced_chunks(values: tuple, count: int) -> tuple[tuple, ...]:
    size, extra = divmod(len(values), count)
    chunks = []
    start = 0
    for index in range(count):
        end = start + size + (1 if index < extra else 0)
        if end > start:
            chunks.append(values[start:end])
        start = end
    return tuple(chunks)


def _bounded_route_worker_count(node_count: int, job_count: int) -> int:
    try:
        available_cpus = len(os.sched_getaffinity(0))
    except AttributeError:
        available_cpus = os.cpu_count() or 1
    if node_count < 256 or job_count < 32 or available_cpus < 4:
        return 1
    return min(
        _MAXIMUM_ROUTE_WORKERS,
        max(2, available_cpus // 4),
        job_count,
    )


def _request_key(request: _RoutePathRequest) -> tuple:
    return (
        0 if request.ground_station_target else 1,
        _node_key(request.terminal),
        _node_key(request.source),
        request.request_id,
    )


def _node_key(node_id: NodeId) -> tuple[int, str, str]:
    return node_id.asn, node_id.entity_kind, node_id.entity_id
