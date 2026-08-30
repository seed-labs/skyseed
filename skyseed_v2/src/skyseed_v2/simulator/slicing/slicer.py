"""Orchestrate the temporary legacy operational Slice bridge."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import datetime
from pathlib import Path
from threading import RLock
from time import perf_counter
from weakref import WeakValueDictionary

from skyseed_v2.shared.time import normalize_utc_datetime
from skyseed_v2.shared import NodeId
from skyseed_v2.shared._execution_observation import _ExecutionObserver

from .connection import (
    ConnectionKind,
    ConnectionModel,
    ConnectionSlice,
)
from .difference import TopologyDifference, _compare_topology_slices
from ._routing_policy import (
    _prepare_routing_policy_context,
    route_step_allows_transit,
)
from .route import (
    GroundStationRouteTarget,
    RouteModel,
    RouteSlice,
    RouteTarget,
    ZoneRouteTarget,
)
from .slice import TopologySlice
from ..timeline import SimulationTimeline
from ..topology import GroundStation, Satellite, Topology
from skyseed_v2.shared._durable_evidence import (
    _append_jsonl,
    _reset_jsonl,
)


_MODEL_OWNERS: WeakValueDictionary[int, Slicer] = WeakValueDictionary()
_MODEL_OWNER_LOCK = RLock()


class _PublishedSliceHistoryError(RuntimeError):
    """Report archive failure without implying Slice publication rolled back."""

    def __init__(self, slice_id: int) -> None:
        self.slice_id = slice_id
        super().__init__(
            f"Slice {slice_id} is already published, but its durable history "
            "append failed; Slicer operations cannot continue and that Slice "
            "must not be retried"
        )


class Slicer:
    """Temporarily bridge physical frames into legacy operational Slices.

    Layer: temporary Simulator-owned legacy operational implementation.
    Simulator explicitly creates at most one; it consumes Topology-owned
    ``PhysicalFrame`` positions, runs the still-unmigrated Connection/Route
    models, and publishes old ``TopologySlice`` values. It is not a physical
    projection owner, accepted alternative architecture, compatibility mode,
    fallback, Planner or backend and remains public only while those existing
    operational consumers are migrated.
    """

    __slots__ = (
        "__weakref__",
        "_topology",
        "_timeline",
        "_connection_model",
        "_route_model",
        "_slices",
        "_slices_by_timestamp",
        "_retained_publication_timestamps",
        "_latest_slice",
        "_timestamp_iterator",
        "_pending_timestamp",
        "_next_slice_id",
        "_durable_history_path",
        "_durable_history_failed_after_slice_id",
        "_archived_difference_through",
        "_execution_observer",
    )

    def __init__(self) -> None:
        raise TypeError("Slicer is created explicitly by Simulator")

    @classmethod
    def _create(
        cls,
        *,
        topology: Topology,
        timeline: SimulationTimeline,
        connection_model: ConnectionModel,
        route_model: RouteModel,
        execution_observer: _ExecutionObserver | None = None,
    ) -> "Slicer":
        if not isinstance(topology, Topology):
            raise TypeError("topology must be a Topology")
        if not isinstance(timeline, SimulationTimeline):
            raise TypeError("timeline must be a SimulationTimeline")
        if not isinstance(connection_model, ConnectionModel):
            raise TypeError("connection_model must implement ConnectionModel")
        if not isinstance(route_model, RouteModel):
            raise TypeError("route_model must implement RouteModel")
        models = (connection_model, route_model)
        if len({id(model) for model in models}) != len(models):
            raise ValueError("each Model role requires a distinct instance")

        empty_connection_slice = ConnectionSlice()
        empty_route_slice = RouteSlice()
        slice_zero = TopologySlice(
            slice_id=0,
            timestamp=None,
            positions={},
            connection_slice=empty_connection_slice,
            route_slice=empty_route_slice,
        )
        instance = object.__new__(cls)
        instance._topology = topology
        instance._timeline = timeline
        instance._connection_model = connection_model
        instance._route_model = route_model
        timestamp_iterator = timeline.iter_timestamps()
        instance._slices = {0: slice_zero}
        instance._slices_by_timestamp = {}
        instance._retained_publication_timestamps = {0: None}
        instance._latest_slice = slice_zero
        instance._timestamp_iterator = timestamp_iterator
        instance._pending_timestamp = _next_timeline_timestamp(
            timestamp_iterator
        )
        instance._next_slice_id = 1
        instance._durable_history_path = None
        instance._durable_history_failed_after_slice_id = None
        instance._archived_difference_through = 0
        instance._execution_observer = execution_observer
        _claim_model_owners(models, instance)
        connection_context_claimed = False
        try:
            claim_context = getattr(
                connection_model, "_claim_slicer_context", None
            )
            if callable(claim_context):
                connection_context_claimed = True
                claim_context(
                    instance,
                    topology,
                    timeline.start_time,
                )
        except Exception:
            try:
                release_context = getattr(
                    connection_model, "_release_slicer_context", None
                )
                if connection_context_claimed and callable(release_context):
                    release_context(instance)
            finally:
                _release_model_owners(models, instance)
            raise
        return instance

    def _set_execution_observer(self, observer: _ExecutionObserver) -> None:
        if not isinstance(observer, _ExecutionObserver):
            raise TypeError("observer must be an _ExecutionObserver")
        if self._execution_observer is not None:
            raise RuntimeError("Slicer execution observation is already enabled")
        self._execution_observer = observer

    def create_next_slice(self) -> TopologySlice:
        self._require_durable_history_healthy()
        timestamp = self.get_next_timestamp()
        if timestamp is None:
            raise StopIteration("SimulationTimeline is exhausted")
        slice_id = self._next_slice_id
        previous = self._latest_slice
        observer = self._execution_observer
        phase = "bootstrap" if slice_id == 1 else "steady"
        scope = (
            {"slice_id": slice_id, "timestamp": timestamp.isoformat()}
            if observer is not None
            else {}
        )
        physical_frame = self._topology.frame_at(timestamp)
        positions = physical_frame.positions
        total_started = perf_counter() if observer is not None else 0.0
        if previous.slice_id == 0:
            prepare_connection_model = getattr(
                self._connection_model, "_prepare_for_slicer", None
            )
            if callable(prepare_connection_model):
                stage_started = perf_counter() if observer is not None else 0.0
                prepare_connection_model(
                    self,
                    self._topology,
                    timestamp,
                    positions,
                )
                stage_elapsed = (
                    perf_counter() - stage_started
                    if observer is not None
                    else 0.0
                )
                _record_slicer_observation(
                    observer,
                    "connection-preparation",
                    phase,
                    stage_elapsed,
                    scope,
                    {"input_positions": len(positions)},
                )
        stage_started = perf_counter() if observer is not None else 0.0
        connection_slice = self._connection_model.compute_connection_slice(
            self._topology,
            timestamp,
            positions,
            previous.connection_slice,
        )
        stage_elapsed = (
            perf_counter() - stage_started if observer is not None else 0.0
        )
        connection_work = {
            "previous_connections": len(previous.connection_slice.connections),
            "emitted_connections": len(connection_slice.connections),
            "emitted_zone_assignments": len(
                connection_slice.zone_service_assignments
            ),
        }
        _record_slicer_observation(
            observer,
            "connection-compute",
            phase,
            stage_elapsed,
            scope,
            connection_work,
        )
        stage_started = perf_counter() if observer is not None else 0.0
        _validate_connection_slice(self._topology, connection_slice)
        stage_elapsed = (
            perf_counter() - stage_started if observer is not None else 0.0
        )
        _record_slicer_observation(
            observer,
            "connection-validation",
            phase,
            stage_elapsed,
            scope,
            {
                "validated_connections": len(connection_slice.connections),
                "validated_zone_assignments": len(
                    connection_slice.zone_service_assignments
                ),
            },
        )
        stage_started = perf_counter() if observer is not None else 0.0
        route_slice = self._route_model.compute_route_slice(
            self._topology,
            timestamp,
            connection_slice,
            previous.route_slice,
        )
        stage_elapsed = (
            perf_counter() - stage_started if observer is not None else 0.0
        )
        route_entries = 0
        if observer is not None:
            route_entries = sum(
                len(targets) for targets in route_slice.routes.values()
            )
            _record_slicer_observation(
                observer,
                "route-compute",
                phase,
                stage_elapsed,
                scope,
                {"emitted_route_entries": route_entries},
            )
        stage_started = perf_counter() if observer is not None else 0.0
        _validate_route_slice(
            self._topology, connection_slice, route_slice
        )
        stage_elapsed = (
            perf_counter() - stage_started if observer is not None else 0.0
        )
        if observer is not None:
            _record_slicer_observation(
                observer,
                "route-validation",
                phase,
                stage_elapsed,
                scope,
                {"validated_route_entries": route_entries},
            )
        stage_started = perf_counter() if observer is not None else 0.0
        candidate = TopologySlice(
            slice_id=slice_id,
            timestamp=timestamp,
            positions=positions,
            connection_slice=connection_slice,
            route_slice=route_slice,
        )

        # Publication is the only mutation and happens after the whole candidate
        # passes construction and cross-result validation.
        next_timestamp = _next_timeline_timestamp(self._timestamp_iterator)
        self._slices[slice_id] = candidate
        self._slices_by_timestamp[timestamp] = candidate
        self._retained_publication_timestamps[slice_id] = timestamp
        self._latest_slice = candidate
        self._next_slice_id = slice_id + 1
        self._pending_timestamp = next_timestamp
        if self._durable_history_path is not None:
            try:
                _append_jsonl(
                    self._durable_history_path,
                    {
                        "record_type": "slice",
                        "slice_id": candidate.slice_id,
                        "timestamp": candidate.timestamp.isoformat(),
                        "positions": _position_history_data(
                            candidate.positions
                        ),
                    },
                )
            except Exception as error:
                self._durable_history_failed_after_slice_id = slice_id
                raise _PublishedSliceHistoryError(slice_id) from error
        publication_elapsed = (
            perf_counter() - stage_started if observer is not None else 0.0
        )
        total_elapsed = (
            perf_counter() - total_started if observer is not None else 0.0
        )
        if observer is not None:
            complete_work = {
                "positions": len(positions),
                "connections": len(connection_slice.connections),
                "zone_assignments": len(
                    connection_slice.zone_service_assignments
                ),
                "route_entries": route_entries,
                "route_compute_workers": getattr(
                    self._route_model,
                    "_active_compute_worker_count",
                    lambda: 1,
                )(),
            }
            _record_slicer_observation(
                observer,
                "slice-publication",
                phase,
                publication_elapsed,
                scope,
                complete_work,
            )
            _record_slicer_observation(
                observer,
                "complete-slice",
                phase,
                total_elapsed,
                scope,
                complete_work,
                duration_role="rollup",
            )
        if next_timestamp is None:
            shutdown_workers = getattr(
                self._route_model, "_shutdown_compute_workers", None
            )
            if callable(shutdown_workers):
                shutdown_workers()
        return candidate

    def create_slices(self, count: int) -> tuple[TopologySlice, ...]:
        if (
            isinstance(count, bool)
            or not isinstance(count, int)
            or count < 0
        ):
            raise ValueError("count must be a non-negative integer")
        return tuple(self.create_next_slice() for _ in range(count))

    def create_all_slices(self) -> tuple[TopologySlice, ...]:
        if not self._timeline.is_bounded:
            raise RuntimeError(
                "create_all_slices requires a bounded SimulationTimeline"
            )
        created = []
        while not self.is_exhausted():
            created.append(self.create_next_slice())
        return tuple(created)

    def get_slice(self, slice_id: int) -> TopologySlice:
        if (
            isinstance(slice_id, bool)
            or not isinstance(slice_id, int)
            or slice_id < 0
        ):
            raise ValueError("slice_id must be a non-negative integer")
        try:
            return self._slices[slice_id]
        except KeyError as error:
            raise KeyError(f"Slice not retained: {slice_id}") from error

    def get_slice_at(self, timestamp: datetime) -> TopologySlice:
        utc = normalize_utc_datetime(timestamp, "timestamp")
        try:
            return self._slices_by_timestamp[utc]
        except KeyError as error:
            raise KeyError(f"Slice timestamp not retained: {utc.isoformat()}") from error

    def get_latest_slice(self) -> TopologySlice:
        return self._latest_slice

    def get_latest_slice_id(self) -> int:
        return self._latest_slice.slice_id

    def get_latest_timestamp(self) -> datetime | None:
        return self._latest_slice.timestamp

    def get_next_timestamp(self) -> datetime | None:
        return self._pending_timestamp

    def is_exhausted(self) -> bool:
        return self.get_next_timestamp() is None

    def get_retained_slice_ids(self) -> tuple[int, ...]:
        return tuple(sorted(self._slices))

    def get_retained_slice_id_range(self) -> tuple[int, int]:
        retained_ids = self.get_retained_slice_ids()
        return retained_ids[0], retained_ids[-1]

    def create_difference(
        self,
        previous_slice_id: int,
        current_slice_id: int,
    ) -> TopologyDifference:
        """Compare two retained adjacent Slices without retaining the result."""

        self._require_durable_history_healthy()
        observer = self._execution_observer
        started = perf_counter() if observer is not None else 0.0
        previous = self.get_slice(previous_slice_id)
        current = self.get_slice(current_slice_id)
        if current_slice_id != previous_slice_id + 1:
            raise ValueError(
                "TopologyDifference requires strictly adjacent Slice IDs"
            )

        # Publication validation plus immutable owner-scoped retention makes
        # the contents authoritative. Difference checks only cheap lineage
        # metadata before comparing the two retained values.
        try:
            previous_timestamp = self._retained_publication_timestamps[
                previous_slice_id
            ]
            current_timestamp = self._retained_publication_timestamps[
                current_slice_id
            ]
        except KeyError as error:
            raise RuntimeError(
                "retained Slice publication metadata is missing"
            ) from error
        _validate_retained_slice_metadata(
            previous,
            previous_slice_id,
            previous_timestamp,
        )
        _validate_retained_slice_metadata(
            current,
            current_slice_id,
            current_timestamp,
        )
        difference = _compare_topology_slices(previous, current)
        if (
            self._durable_history_path is not None
            and current_slice_id > self._archived_difference_through
        ):
            if current_slice_id != self._archived_difference_through + 1:
                raise RuntimeError(
                    "durable Difference history must advance continuously"
                )
            # Planner owns the one canonical durable Difference stream.  The
            # Slicer history keeps only Position facts, which Difference does
            # not contain, so Tool persistence never duplicates dynamic state.
            self._archived_difference_through = current_slice_id
        elapsed = perf_counter() - started if observer is not None else 0.0
        if observer is not None:
            _record_slicer_observation(
                observer,
                "difference",
                "bootstrap" if current_slice_id == 1 else "steady",
                elapsed,
                {
                    "previous_slice_id": previous_slice_id,
                    "current_slice_id": current_slice_id,
                    "timestamp": difference.current_timestamp.isoformat(),
                },
                {
                    "connection_additions": len(difference.added_connections),
                    "connection_removals": len(
                        difference.removed_connection_ids
                    ),
                    "parameter_patches": len(
                        difference.connection_parameter_patches
                    ),
                    "zone_assignment_patches": len(
                        difference.zone_service_assignment_patches
                    ),
                    "route_patches": len(difference.route_patches),
                },
            )
        return difference

    def _enable_durable_history(self, path: Path) -> None:
        """Enable private append-only history before rolling progression."""

        if self._latest_slice.slice_id != 0 or self._durable_history_path is not None:
            raise RuntimeError(
                "Slicer durable history must be enabled before progression"
            )
        durable_history_path = _reset_jsonl(
            path,
            {
                "record_type": "history_header",
                "format_version": 2,
                "owner": "Slicer",
                "storage": "positions plus canonical Planner Differences",
                "timeline": {
                    "start_time": self._timeline.start_time.isoformat(),
                    "end_time": (
                        None
                        if self._timeline.end_time is None
                        else self._timeline.end_time.isoformat()
                    ),
                    "slice_interval_seconds": (
                        self._timeline.slice_interval.total_seconds()
                    ),
                },
            },
        )
        _append_jsonl(
            durable_history_path,
            {
                "record_type": "slice",
                "slice_id": 0,
                "timestamp": None,
                "positions": {},
            },
        )
        self._durable_history_path = durable_history_path

    def _require_durable_history_healthy(self) -> None:
        failed_slice_id = self._durable_history_failed_after_slice_id
        if failed_slice_id is not None:
            raise _PublishedSliceHistoryError(failed_slice_id)

    def _release_before(self, retained_from_slice_id: int) -> dict[str, int]:
        """Release old indexed Slices after every consumer crossed a frontier."""

        if (
            isinstance(retained_from_slice_id, bool)
            or not isinstance(retained_from_slice_id, int)
            or retained_from_slice_id < 0
        ):
            raise ValueError("retained_from_slice_id must be non-negative")
        if retained_from_slice_id not in self._slices:
            raise KeyError(
                f"retention frontier is not retained: {retained_from_slice_id}"
            )
        released = tuple(
            slice_id
            for slice_id in self._slices
            if slice_id < retained_from_slice_id
        )
        for slice_id in released:
            value = self._slices.pop(slice_id)
            self._retained_publication_timestamps.pop(slice_id, None)
            if value.timestamp is not None:
                self._slices_by_timestamp.pop(value.timestamp, None)
        return {
            "released_slice_count": len(released),
            "retained_slice_count": len(self._slices),
            "timestamp_index_count": len(self._slices_by_timestamp),
        }

    def get_topology(self) -> Topology:
        return self._topology

    def get_timeline(self) -> SimulationTimeline:
        return self._timeline

    def get_connection_model(self) -> ConnectionModel:
        return self._connection_model

    def get_route_model(self) -> RouteModel:
        return self._route_model

    def resolve_path(
        self,
        slice_id: int,
        source: NodeId,
        target: RouteTarget,
    ) -> tuple[NodeId, ...]:
        topology_slice = self.get_slice(slice_id)
        _require_known_node(self._topology, source)
        terminal = _resolve_terminal(
            self._topology, topology_slice.connection_slice, target
        )
        if source == terminal:
            return (source,)

        path = [source]
        seen = {source}
        current = source
        while current != terminal:
            try:
                next_hop = topology_slice.route_slice.get_next_hop(
                    current, target
                )
            except KeyError as error:
                if len(path) == 1:
                    raise LookupError(
                        f"Target is currently unreachable from {source.value}"
                    ) from error
                if _is_other_terminal(
                    self._topology,
                    topology_slice.connection_slice,
                    current,
                    terminal,
                ):
                    raise ValueError(
                        f"Route chain reached wrong terminal: {current.value}"
                    ) from error
                raise ValueError(
                    f"Route chain is corrupt after {current.value}"
                ) from error
            _require_known_node(self._topology, next_hop)
            if not _has_connection(
                topology_slice.connection_slice, current, next_hop
            ):
                raise ValueError(
                    "Route chain uses an absent current Connection"
                )
            if (
                isinstance(target, GroundStationRouteTarget)
                and next_hop.entity_kind == "ground-station"
                and next_hop != terminal
            ):
                raise ValueError(
                    f"Route chain reached wrong terminal: {next_hop.value}"
                )
            if not route_step_allows_transit(
                source,
                current,
                next_hop,
                target,
                terminal,
            ):
                raise ValueError(
                    "Route chain uses ineligible GroundStation transit"
                )
            if next_hop in seen:
                raise ValueError("Route chain contains a loop")
            path.append(next_hop)
            seen.add(next_hop)
            current = next_hop
        return tuple(path)


def _validate_retained_slice_metadata(
    topology_slice: TopologySlice,
    expected_slice_id: int,
    expected_timestamp: datetime | None,
) -> None:
    if not isinstance(topology_slice, TopologySlice):
        raise TypeError("retained value must be a TopologySlice")
    if topology_slice.slice_id != expected_slice_id:
        raise ValueError("retained Slice ID metadata is inconsistent")
    if topology_slice.timestamp != expected_timestamp:
        raise ValueError("retained Slice timestamp metadata is inconsistent")


def _validate_connection_slice(
    topology: Topology,
    connection_slice: ConnectionSlice,
) -> None:
    if not isinstance(connection_slice, ConnectionSlice):
        raise TypeError("ConnectionModel must return a ConnectionSlice")
    known_nodes = _topology_node_ids(topology)
    ground_station_access_counts: dict[NodeId, int] = {}
    satellite_ground_station_access_counts: dict[NodeId, int] = {}
    for connection in connection_slice.connections:
        identifier = connection.connection_id
        first, second = identifier.endpoint_a, identifier.endpoint_b
        if first not in known_nodes or second not in known_nodes:
            raise ValueError("Connection endpoint is foreign to Topology")
        kinds = {first.entity_kind, second.entity_kind}
        if (
            identifier.kind is ConnectionKind.INTER_SATELLITE
            and kinds != {"satellite"}
        ):
            raise ValueError("INTER_SATELLITE requires two Satellites")
        if (
            identifier.kind is ConnectionKind.GROUND_STATION_ACCESS
            and kinds != {"satellite", "ground-station"}
        ):
            raise ValueError(
                "GROUND_STATION_ACCESS requires Satellite and GroundStation"
            )
        if identifier.kind is ConnectionKind.GROUND_STATION_ACCESS:
            ground_station_id = (
                first if first.entity_kind == "ground-station" else second
            )
            satellite_id = (
                first if first.entity_kind == "satellite" else second
            )
            count = ground_station_access_counts.get(ground_station_id, 0) + 1
            ground_station_access_counts[ground_station_id] = count
            capability = topology.get_ground_station(
                ground_station_id
            ).max_simultaneous_satellite_accesses
            if capability is not None and count > capability:
                raise ValueError(
                    "ConnectionModel output exceeds GroundStation "
                    "max_simultaneous_satellite_accesses for "
                    f"{ground_station_id.value}"
                )
            satellite_count = (
                satellite_ground_station_access_counts.get(satellite_id, 0)
                + 1
            )
            satellite_ground_station_access_counts[satellite_id] = (
                satellite_count
            )
            satellite_capability = topology.get_satellite(
                satellite_id
            ).max_simultaneous_ground_station_accesses
            if (
                satellite_capability is not None
                and satellite_count > satellite_capability
            ):
                raise ValueError(
                    "ConnectionModel output exceeds Satellite "
                    "max_simultaneous_ground_station_accesses for "
                    f"{satellite_id.value}"
                )
    try:
        zone_layout = topology.get_zone_layout()
    except RuntimeError:
        if connection_slice.zone_service_assignments:
            raise ValueError(
                "Zone assignments require Topology ZoneLayout"
            )
        return
    for zone_id, satellite_id in (
        connection_slice.zone_service_assignments.items()
    ):
        zone_layout.get_zone(zone_id)
        node = topology.get_node(satellite_id)
        if not isinstance(node, Satellite):
            raise ValueError("Zone service assignment must identify Satellite")


def _validate_route_slice(
    topology: Topology,
    connection_slice: ConnectionSlice,
    route_slice: RouteSlice,
) -> None:
    if not isinstance(route_slice, RouteSlice):
        raise TypeError("RouteModel must return a RouteSlice")
    routing_context = _prepare_routing_policy_context(
        topology, connection_slice
    )
    known_nodes = routing_context.nodes
    terminals: dict[RouteTarget, NodeId] = {}
    routes_by_target: dict[RouteTarget, dict[NodeId, NodeId]] = {}
    for source, target_routes in route_slice.routes.items():
        if source not in known_nodes:
            raise ValueError("Route source is foreign to Topology")
        for target, next_hop in target_routes.items():
            if (
                isinstance(target, GroundStationRouteTarget)
                and source.entity_kind == "ground-station"
            ):
                raise ValueError(
                    "GroundStations do not publish routes to G targets"
                )
            if next_hop not in known_nodes:
                raise ValueError("Route next hop is foreign to Topology")
            if next_hop not in routing_context.graph[source]:
                raise ValueError(
                    "Route next hop has no current Connection direction"
                )
            if target not in terminals:
                terminals[target] = _resolve_terminal(
                    topology, connection_slice, target
                )
            terminal = terminals[target]
            if source == terminal:
                raise ValueError("RouteSlice contains an entry at its terminal")
            if (
                isinstance(target, GroundStationRouteTarget)
                and next_hop.entity_kind == "ground-station"
                and next_hop != terminal
            ):
                raise ValueError("Route chain reaches the wrong terminal")
            if not route_step_allows_transit(
                source,
                source,
                next_hop,
                target,
                terminal,
            ):
                raise ValueError(
                    "Route chain uses ineligible GroundStation transit"
                )
            routes_by_target.setdefault(target, {})[source] = next_hop

    other_terminals = routing_context.ground_stations | frozenset(
        connection_slice.zone_service_assignments.values()
    )
    for target, target_routes in routes_by_target.items():
        _validate_route_tree(
            target_routes,
            terminals[target],
            other_terminals,
        )


def _validate_route_tree(
    routes: Mapping[NodeId, NodeId],
    terminal: NodeId,
    other_terminals: frozenset[NodeId],
) -> None:
    # Each target is one functional graph. Publication-time adjacency and
    # transit checks above validated every edge once; these memoized walks now
    # prove that every published source reaches the exact terminal.
    resolved = {terminal}
    state: dict[NodeId, int] = {terminal: 2}
    for source in routes:
        if state.get(source) == 2:
            continue
        current = source
        chain: list[NodeId] = []
        while current not in resolved:
            if state.get(current) == 1:
                raise ValueError("Route chain contains a loop")
            state[current] = 1
            chain.append(current)
            try:
                next_hop = routes[current]
            except KeyError as error:
                if current in other_terminals and current != terminal:
                    raise ValueError(
                        "Route chain reaches the wrong terminal"
                    ) from error
                raise ValueError("Route chain is incomplete") from error
            if state.get(next_hop) == 1:
                raise ValueError("Route chain contains a loop")
            current = next_hop
        for node_id in chain:
            state[node_id] = 2
            resolved.add(node_id)


def _resolve_terminal(
    topology: Topology,
    connection_slice: ConnectionSlice,
    target: RouteTarget,
) -> NodeId:
    if isinstance(target, GroundStationRouteTarget):
        try:
            topology.get_ground_station(target.ground_station_id)
        except KeyError as error:
            raise KeyError(
                f"Unknown GroundStation: {target.ground_station_id.value}"
            ) from error
        return target.ground_station_id
    if isinstance(target, ZoneRouteTarget):
        try:
            topology.get_zone(target.zone_id)
        except KeyError as error:
            raise KeyError(f"Unknown Zone: {target.zone_id}") from error
        try:
            return connection_slice.get_service_satellite(target.zone_id)
        except KeyError as error:
            raise LookupError(
                f"Zone is currently unserved: {target.zone_id}"
            ) from error
    raise TypeError(
        "target must be a GroundStationRouteTarget or ZoneRouteTarget"
    )


def _is_other_terminal(
    topology: Topology,
    connection_slice: ConnectionSlice,
    node_id: NodeId,
    expected_terminal: NodeId,
) -> bool:
    if node_id == expected_terminal:
        return False
    try:
        node = topology.get_node(node_id)
    except KeyError:
        return False
    return isinstance(node, GroundStation) or node_id in set(
        connection_slice.zone_service_assignments.values()
    )


def _has_connection(
    connection_slice: ConnectionSlice,
    first: NodeId,
    second: NodeId,
) -> bool:
    for kind in ConnectionKind:
        try:
            connection_slice.get_connection_for_endpoints(
                kind, first, second
            )
        except KeyError:
            continue
        return True
    return False


def _claim_model_owners(
    models: tuple[object, ...],
    owner: Slicer,
) -> None:
    with _MODEL_OWNER_LOCK:
        if any(id(model) in _MODEL_OWNERS for model in models):
            raise ValueError("Model instance already belongs to another Slicer")
        claimed_ids = []
        try:
            for model in models:
                model_id = id(model)
                _MODEL_OWNERS[model_id] = owner
                claimed_ids.append(model_id)
        except Exception:
            for model_id in claimed_ids:
                if _MODEL_OWNERS.get(model_id) is owner:
                    del _MODEL_OWNERS[model_id]
            raise


def _release_model_owners(
    models: tuple[object, ...],
    owner: Slicer,
) -> None:
    with _MODEL_OWNER_LOCK:
        for model in models:
            model_id = id(model)
            if _MODEL_OWNERS.get(model_id) is owner:
                del _MODEL_OWNERS[model_id]


def _require_known_node(topology: Topology, node_id: NodeId) -> None:
    if not isinstance(node_id, NodeId):
        raise TypeError("source must be a NodeId")
    try:
        topology.get_node(node_id)
    except KeyError as error:
        raise KeyError(f"Unknown node: {node_id.value}") from error


def _topology_node_ids(topology: Topology) -> set[NodeId]:
    result = set()
    for autonomous_system in topology.autonomous_systems:
        for shell in autonomous_system.shells:
            for orbit in shell.orbits:
                result.update(
                    satellite.node_id for satellite in orbit.satellites
                )
        result.update(
            station.node_id
            for station in autonomous_system.ground_stations
        )
    return result


def _node_key(node_id: NodeId) -> tuple[int, str, str]:
    return node_id.asn, node_id.entity_kind, node_id.entity_id


def _position_history_data(
    positions: Mapping[NodeId, object],
) -> dict[str, list[float]]:
    """Encode only irreducible per-Slice position facts compactly."""

    return {
        node_id.value: [
            float(position.x_m),
            float(position.y_m),
            float(position.z_m),
        ]
        for node_id, position in sorted(
            positions.items(), key=lambda item: _node_key(item[0])
        )
    }


def _record_slicer_observation(
    observer: _ExecutionObserver | None,
    stage: str,
    phase: str,
    duration_seconds: float,
    scope: Mapping[str, object],
    work: Mapping[str, int | float],
    *,
    duration_role: str = "stage",
) -> None:
    if observer is None:
        return
    observer.record(
        component="slicer",
        stage=stage,
        phase=phase,
        duration_seconds=duration_seconds,
        duration_role=duration_role,
        scope=scope,
        work=work,
    )


def _next_timeline_timestamp(
    timestamps: Iterator[datetime],
) -> datetime | None:
    try:
        timestamp = next(timestamps)
    except StopIteration:
        return None
    return normalize_utc_datetime(timestamp, "Timeline timestamp")


__all__ = ["Slicer"]
