"""Dynamic Slice orchestration, atomic publication and retained queries."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import datetime
from threading import RLock
from types import MappingProxyType
from weakref import WeakValueDictionary

from skyseed_v2._time import normalize_utc_datetime
from skyseed_v2.node_id import NodeId

from .connection import (
    ConnectionKind,
    ConnectionModel,
    ConnectionSlice,
)
from .difference import TopologyDifference, _compare_topology_slices
from .position import EarthFixedPosition, PositionModel
from ._routing_policy import (
    eligible_shortest_next_hops,
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
from .timeline import SimulationTimeline
from .topology import GroundStation, Satellite, Topology


_MODEL_OWNERS: WeakValueDictionary[int, Slicer] = WeakValueDictionary()
_MODEL_OWNER_LOCK = RLock()


class Slicer:
    """Coordinate the sole dynamic Model chain and publish complete Slices.

    Layer: Simulator-owned dynamic orchestration facade. Simulator explicitly
    creates at most one and callers request/query Slices through it. It is not
    a Topology owner, Model algorithm, fallback, Planner or backend and is
    public from ``skyseed_v2.simulator``.
    """

    __slots__ = (
        "__weakref__",
        "_topology",
        "_timeline",
        "_position_model",
        "_connection_model",
        "_route_model",
        "_slices",
        "_slices_by_timestamp",
        "_latest_slice",
        "_timestamp_iterator",
        "_pending_timestamp",
        "_next_slice_id",
    )

    def __init__(self) -> None:
        raise TypeError("Slicer is created explicitly by Simulator")

    @classmethod
    def _create(
        cls,
        *,
        topology: Topology,
        timeline: SimulationTimeline,
        position_model: PositionModel,
        connection_model: ConnectionModel,
        route_model: RouteModel,
    ) -> "Slicer":
        if not isinstance(topology, Topology):
            raise TypeError("topology must be a Topology")
        if not isinstance(timeline, SimulationTimeline):
            raise TypeError("timeline must be a SimulationTimeline")
        if not isinstance(position_model, PositionModel):
            raise TypeError("position_model must implement PositionModel")
        if not isinstance(connection_model, ConnectionModel):
            raise TypeError("connection_model must implement ConnectionModel")
        if not isinstance(route_model, RouteModel):
            raise TypeError("route_model must implement RouteModel")
        models = (position_model, connection_model, route_model)
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
        instance._position_model = position_model
        instance._connection_model = connection_model
        instance._route_model = route_model
        timestamp_iterator = timeline.iter_timestamps()
        instance._slices = {0: slice_zero}
        instance._slices_by_timestamp = {}
        instance._latest_slice = slice_zero
        instance._timestamp_iterator = timestamp_iterator
        instance._pending_timestamp = _next_timeline_timestamp(
            timestamp_iterator
        )
        instance._next_slice_id = 1
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

    def create_next_slice(self) -> TopologySlice:
        timestamp = self.get_next_timestamp()
        if timestamp is None:
            raise StopIteration("SimulationTimeline is exhausted")
        slice_id = self._next_slice_id
        previous = self._latest_slice

        raw_positions = self._position_model.compute_positions(
            self._topology, timestamp
        )
        positions = _validated_positions(self._topology, raw_positions)
        if previous.slice_id == 0:
            prepare_connection_model = getattr(
                self._connection_model, "_prepare_for_slicer", None
            )
            if callable(prepare_connection_model):
                prepare_connection_model(
                    self,
                    self._topology,
                    timestamp,
                    positions,
                )
        connection_slice = self._connection_model.compute_connection_slice(
            self._topology,
            timestamp,
            positions,
            previous.connection_slice,
        )
        _validate_connection_slice(self._topology, connection_slice)
        route_slice = self._route_model.compute_route_slice(
            self._topology,
            timestamp,
            connection_slice,
            previous.route_slice,
        )
        _validate_route_slice(
            self._topology, connection_slice, route_slice
        )
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
        self._latest_slice = candidate
        self._next_slice_id = slice_id + 1
        self._pending_timestamp = next_timestamp
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

        previous = self.get_slice(previous_slice_id)
        current = self.get_slice(current_slice_id)
        if current_slice_id != previous_slice_id + 1:
            raise ValueError(
                "TopologyDifference requires strictly adjacent Slice IDs"
            )

        # Both values came from this Slicer's owner-scoped retained window, so
        # no foreign-lineage Slice can enter the private comparator.
        _validate_retained_slice(
            self._topology,
            self._timeline,
            previous,
            previous_slice_id,
        )
        _validate_retained_slice(
            self._topology,
            self._timeline,
            current,
            current_slice_id,
        )
        return _compare_topology_slices(previous, current)

    def get_topology(self) -> Topology:
        return self._topology

    def get_timeline(self) -> SimulationTimeline:
        return self._timeline

    def get_position_model(self) -> PositionModel:
        return self._position_model

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


def _validated_positions(
    topology: Topology,
    positions: Mapping[NodeId, EarthFixedPosition],
) -> Mapping[NodeId, EarthFixedPosition]:
    if not isinstance(positions, Mapping):
        raise TypeError("PositionModel must return a Mapping")
    expected = _topology_node_ids(topology)
    if set(positions) != expected:
        raise ValueError(
            "PositionModel must return exactly every Topology spatial node"
        )
    copied = {}
    for node_id, position in positions.items():
        if not isinstance(node_id, NodeId):
            raise TypeError("PositionModel keys must be NodeId values")
        if not isinstance(position, EarthFixedPosition):
            raise TypeError(
                "PositionModel values must be EarthFixedPosition values"
            )
        copied[node_id] = position
    return MappingProxyType(
        dict(sorted(copied.items(), key=lambda item: _node_key(item[0])))
    )


def _validate_retained_slice(
    topology: Topology,
    timeline: SimulationTimeline,
    topology_slice: TopologySlice,
    expected_slice_id: int,
) -> None:
    if not isinstance(topology_slice, TopologySlice):
        raise TypeError("retained value must be a TopologySlice")
    if topology_slice.slice_id != expected_slice_id:
        raise ValueError("retained Slice ID metadata is inconsistent")
    expected_timestamp = _timeline_timestamp_for_slice_id(
        timeline, expected_slice_id
    )
    if topology_slice.timestamp != expected_timestamp:
        raise ValueError("retained Slice timestamp metadata is inconsistent")
    if expected_slice_id == 0:
        if (
            topology_slice.positions
            or topology_slice.connection_slice.connections
            or topology_slice.connection_slice.zone_service_assignments
            or topology_slice.route_slice.routes
        ):
            raise ValueError("synthetic Slice 0 dynamic state is inconsistent")
    else:
        _validated_positions(topology, topology_slice.positions)
    _validate_connection_slice(topology, topology_slice.connection_slice)
    _validate_route_slice(
        topology,
        topology_slice.connection_slice,
        topology_slice.route_slice,
    )


def _validate_connection_slice(
    topology: Topology,
    connection_slice: ConnectionSlice,
) -> None:
    if not isinstance(connection_slice, ConnectionSlice):
        raise TypeError("ConnectionModel must return a ConnectionSlice")
    known_nodes = _topology_node_ids(topology)
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
    known_nodes = _topology_node_ids(topology)
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
            if not _has_connection(connection_slice, source, next_hop):
                raise ValueError(
                    "Route next hop has no current Connection direction"
                )
            terminal = _resolve_terminal(topology, connection_slice, target)
            if source == terminal:
                raise ValueError("RouteSlice contains an entry at its terminal")
            _validate_route_chain(
                topology,
                connection_slice,
                route_slice,
                source,
                target,
                terminal,
            )

    ground_station_ids = {
        node_id for node_id in known_nodes
        if node_id.entity_kind == "ground-station"
    }
    required_targets: list[tuple[RouteTarget, NodeId]] = [
        (
            GroundStationRouteTarget(ground_station_id),
            ground_station_id,
        )
        for ground_station_id in sorted(
            ground_station_ids, key=_node_key
        )
    ]
    required_targets.extend(
        (
            ZoneRouteTarget(zone_id),
            service_satellite,
        )
        for zone_id, service_satellite in (
            connection_slice.zone_service_assignments.items()
        )
    )
    for target, terminal in required_targets:
        reachable_sources = eligible_shortest_next_hops(
            topology,
            connection_slice,
            target,
            terminal,
        )
        for source in reachable_sources:
            try:
                route_slice.get_next_hop(source, target)
            except KeyError as error:
                raise ValueError(
                    "RouteSlice omits a reachable source-target route"
                ) from error


def _validate_route_chain(
    topology: Topology,
    connection_slice: ConnectionSlice,
    route_slice: RouteSlice,
    source: NodeId,
    target: RouteTarget,
    terminal: NodeId,
) -> None:
    current = source
    seen = {source}
    while current != terminal:
        try:
            next_hop = route_slice.get_next_hop(current, target)
        except KeyError as error:
            if _is_other_terminal(
                topology, connection_slice, current, terminal
            ):
                raise ValueError("Route chain reaches the wrong terminal") from error
            raise ValueError("Route chain is incomplete") from error
        if not _has_connection(connection_slice, current, next_hop):
            raise ValueError("Route chain uses an absent current Connection")
        if (
            isinstance(target, GroundStationRouteTarget)
            and next_hop.entity_kind == "ground-station"
            and next_hop != terminal
        ):
            raise ValueError("Route chain reaches the wrong terminal")
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
        seen.add(next_hop)
        current = next_hop


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
    return any(
        {
            connection.connection_id.endpoint_a,
            connection.connection_id.endpoint_b,
        }
        == {first, second}
        for connection in connection_slice.get_connections_for_node(first)
    )


def _claim_model_owners(
    models: tuple[object, object, object],
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
    models: tuple[object, object, object],
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


def _next_timeline_timestamp(
    timestamps: Iterator[datetime],
) -> datetime | None:
    try:
        timestamp = next(timestamps)
    except StopIteration:
        return None
    return normalize_utc_datetime(timestamp, "Timeline timestamp")


def _timeline_timestamp_for_slice_id(
    timeline: SimulationTimeline,
    slice_id: int,
) -> datetime | None:
    if slice_id == 0:
        return None
    for current_slice_id, timestamp in enumerate(
        timeline.iter_timestamps(),
        start=1,
    ):
        if current_slice_id == slice_id:
            return timestamp
    raise ValueError("retained Slice ID is outside SimulationTimeline")


__all__ = ["Slicer"]
