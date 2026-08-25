"""Define sparse changes between adjacent published TopologySlices."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from skyseed_v2.shared.time import normalize_utc_datetime
from skyseed_v2.shared.identifier import validate_identifier
from skyseed_v2.shared import NodeId

from .connection import Connection, ConnectionId
from .route import (
    GroundStationRouteTarget,
    RouteKey,
    RouteTarget,
    ZoneRouteTarget,
)
from .slice import TopologySlice


class DifferenceOperation(Enum):
    """Classify one Zone-service or Route patch.

    Layer: immutable Simulator Difference patch component. Zone-service and
    Route patches use it to distinguish addition, change and removal. It is
    not a rollback command, update operation or backend action and is public
    from ``skyseed_v2.simulator``.
    """

    ADDED = "added"
    CHANGED = "changed"
    REMOVED = "removed"


class ConnectionDirection(Enum):
    """Identify one normalized Connection direction in a parameter patch.

    Layer: immutable Simulator Difference patch component. A directional
    parameter patch owns it. It is not a directed Connection identity,
    interface direction or backend resource and is public from
    ``skyseed_v2.simulator``.
    """

    ENDPOINT_A_TO_B = "endpoint-a-to-b"
    ENDPOINT_B_TO_A = "endpoint-b-to-a"


@dataclass(frozen=True, slots=True)
class DirectionalConnectionParametersPatch:
    """Carry only changed new values for one Connection direction.

    Layer: immutable Simulator Difference patch value. TopologyDifference owns
    it and consumers apply its non-None fields to the identified direction.
    It is not a complete parameter value, old-value log or backend operation
    and is public from ``skyseed_v2.simulator``.
    """

    connection_id: ConnectionId
    direction: ConnectionDirection
    bandwidth_bps: int | None = None
    delay_us: int | None = None
    packet_loss_ppm: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.connection_id, ConnectionId):
            raise TypeError("connection_id must be a ConnectionId")
        if not isinstance(self.direction, ConnectionDirection):
            raise TypeError("direction must be a ConnectionDirection")
        if (
            self.bandwidth_bps is None
            and self.delay_us is None
            and self.packet_loss_ppm is None
        ):
            raise ValueError(
                "directional parameter patch must contain a changed value"
            )
        if self.bandwidth_bps is not None and (
            isinstance(self.bandwidth_bps, bool)
            or not isinstance(self.bandwidth_bps, int)
            or self.bandwidth_bps <= 0
        ):
            raise ValueError(
                "bandwidth_bps patch must be a positive integer"
            )
        if self.delay_us is not None and (
            isinstance(self.delay_us, bool)
            or not isinstance(self.delay_us, int)
            or self.delay_us < 0
        ):
            raise ValueError(
                "delay_us patch must be a non-negative integer"
            )
        if self.packet_loss_ppm is not None and (
            isinstance(self.packet_loss_ppm, bool)
            or not isinstance(self.packet_loss_ppm, int)
            or not 0 <= self.packet_loss_ppm <= 1_000_000
        ):
            raise ValueError(
                "packet_loss_ppm patch must be between 0 and 1000000"
            )


@dataclass(frozen=True, slots=True)
class ZoneServiceAssignmentPatch:
    """Describe one Zone service assignment addition, change or removal.

    Layer: immutable Simulator Difference patch value. TopologyDifference owns
    it and consumers read the new service Satellite or explicit unassignment.
    It is not coverage evidence, an old assignment or service-selection
    algorithm and is public from ``skyseed_v2.simulator``.
    """

    zone_id: str
    operation: DifferenceOperation
    service_satellite_id: NodeId | None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "zone_id", validate_identifier(self.zone_id, "zone_id")
        )
        if not isinstance(self.operation, DifferenceOperation):
            raise TypeError("operation must be a DifferenceOperation")
        if self.operation is DifferenceOperation.REMOVED:
            if self.service_satellite_id is not None:
                raise ValueError(
                    "removed Zone assignment must not retain a Satellite"
                )
            return
        if not isinstance(self.service_satellite_id, NodeId):
            raise TypeError(
                "added or changed Zone assignment requires a NodeId"
            )
        if self.service_satellite_id.entity_kind != "satellite":
            raise ValueError(
                "Zone service assignment must identify a Satellite"
            )


@dataclass(frozen=True, slots=True)
class RoutePatch:
    """Describe one sparse Route entry addition, change or removal.

    Layer: immutable Simulator Difference patch value. TopologyDifference owns
    it and consumers read the new next hop or explicit removal. It is not a
    full path, old next hop, metric or backend route operation and is public
    from ``skyseed_v2.simulator``.
    """

    route_key: RouteKey
    operation: DifferenceOperation
    next_hop: NodeId | None

    def __post_init__(self) -> None:
        if not isinstance(self.route_key, RouteKey):
            raise TypeError("route_key must be a RouteKey")
        if not isinstance(self.operation, DifferenceOperation):
            raise TypeError("operation must be a DifferenceOperation")
        if self.operation is DifferenceOperation.REMOVED:
            if self.next_hop is not None:
                raise ValueError(
                    "removed Route patch must not retain a next hop"
                )
            return
        if not isinstance(self.next_hop, NodeId):
            raise TypeError("added or changed Route patch requires a NodeId")


@dataclass(frozen=True, slots=True, init=False)
class TopologyDifference:
    """Aggregate one deterministic sparse change between adjacent Slices.

    Layer: independent immutable Simulator dynamic result. Slicer creates it
    from two retained adjacent Slices and callers consume it without changing
    either Slice. It is not retained history, a complete Slice, Position
    change, Planner input window or backend operation and is public from
    ``skyseed_v2.simulator``.
    """

    previous_slice_id: int
    previous_timestamp: datetime | None
    current_slice_id: int
    current_timestamp: datetime
    added_connections: tuple[Connection, ...]
    removed_connection_ids: tuple[ConnectionId, ...]
    connection_parameter_patches: tuple[
        DirectionalConnectionParametersPatch, ...
    ]
    zone_service_assignment_patches: tuple[
        ZoneServiceAssignmentPatch, ...
    ]
    route_patches: tuple[RoutePatch, ...]

    def __init__(
        self,
        *,
        previous_slice_id: int,
        previous_timestamp: datetime | None,
        current_slice_id: int,
        current_timestamp: datetime,
        added_connections: Iterable[Connection] = (),
        removed_connection_ids: Iterable[ConnectionId] = (),
        connection_parameter_patches: Iterable[
            DirectionalConnectionParametersPatch
        ] = (),
        zone_service_assignment_patches: Iterable[
            ZoneServiceAssignmentPatch
        ] = (),
        route_patches: Iterable[RoutePatch] = (),
    ) -> None:
        _validate_slice_id(previous_slice_id, "previous_slice_id")
        _validate_slice_id(current_slice_id, "current_slice_id")
        if current_slice_id != previous_slice_id + 1:
            raise ValueError(
                "TopologyDifference requires strictly adjacent Slice IDs"
            )
        if previous_slice_id == 0:
            if previous_timestamp is not None:
                raise ValueError("synthetic Slice 0 timestamp must be None")
            normalized_previous_timestamp = None
        else:
            if previous_timestamp is None:
                raise ValueError(
                    "previous real Slice timestamp must be a datetime"
                )
            normalized_previous_timestamp = normalize_utc_datetime(
                previous_timestamp, "previous_timestamp"
            )
        normalized_current_timestamp = normalize_utc_datetime(
            current_timestamp, "current_timestamp"
        )
        if (
            normalized_previous_timestamp is not None
            and normalized_current_timestamp
            <= normalized_previous_timestamp
        ):
            raise ValueError(
                "current_timestamp must be later than previous_timestamp"
            )

        added = _typed_tuple(
            added_connections, Connection, "added_connections"
        )
        removed = _typed_tuple(
            removed_connection_ids,
            ConnectionId,
            "removed_connection_ids",
        )
        parameter_patches = _typed_tuple(
            connection_parameter_patches,
            DirectionalConnectionParametersPatch,
            "connection_parameter_patches",
        )
        zone_patches = _typed_tuple(
            zone_service_assignment_patches,
            ZoneServiceAssignmentPatch,
            "zone_service_assignment_patches",
        )
        route_patch_values = _typed_tuple(
            route_patches, RoutePatch, "route_patches"
        )

        added = tuple(
            sorted(
                added,
                key=lambda value: _connection_key(value.connection_id),
            )
        )
        removed = tuple(sorted(removed, key=_connection_key))
        parameter_patches = tuple(
            sorted(
                parameter_patches,
                key=lambda value: (
                    _connection_key(value.connection_id),
                    value.direction.value,
                ),
            )
        )
        zone_patches = tuple(
            sorted(zone_patches, key=lambda value: value.zone_id)
        )
        route_patch_values = tuple(
            sorted(
                route_patch_values,
                key=lambda value: _route_key(value.route_key),
            )
        )
        _validate_patch_identities(
            added,
            removed,
            parameter_patches,
            zone_patches,
            route_patch_values,
        )

        object.__setattr__(self, "previous_slice_id", previous_slice_id)
        object.__setattr__(
            self, "previous_timestamp", normalized_previous_timestamp
        )
        object.__setattr__(self, "current_slice_id", current_slice_id)
        object.__setattr__(
            self, "current_timestamp", normalized_current_timestamp
        )
        object.__setattr__(self, "added_connections", added)
        object.__setattr__(self, "removed_connection_ids", removed)
        object.__setattr__(
            self, "connection_parameter_patches", parameter_patches
        )
        object.__setattr__(
            self, "zone_service_assignment_patches", zone_patches
        )
        object.__setattr__(self, "route_patches", route_patch_values)

    @property
    def is_empty(self) -> bool:
        """Return whether this adjacent transition contains no formal change."""

        return not (
            self.added_connections
            or self.removed_connection_ids
            or self.connection_parameter_patches
            or self.zone_service_assignment_patches
            or self.route_patches
        )


def _compare_topology_slices(
    previous: TopologySlice,
    current: TopologySlice,
) -> TopologyDifference:
    """Return the sole pure adjacent-Slice comparison result."""

    if not isinstance(previous, TopologySlice):
        raise TypeError("previous must be a TopologySlice")
    if not isinstance(current, TopologySlice):
        raise TypeError("current must be a TopologySlice")
    if current.slice_id != previous.slice_id + 1:
        raise ValueError(
            "TopologyDifference requires strictly adjacent Slice IDs"
        )
    if previous.slice_id == 0:
        if previous.timestamp is not None:
            raise ValueError("synthetic Slice 0 timestamp must be None")
    elif previous.timestamp is None:
        raise ValueError("previous real Slice timestamp is missing")
    if current.timestamp is None:
        raise ValueError("current real Slice timestamp is missing")

    previous_connections = {
        connection.connection_id: connection
        for connection in previous.connection_slice.connections
    }
    current_connections = {
        connection.connection_id: connection
        for connection in current.connection_slice.connections
    }
    previous_ids = set(previous_connections)
    current_ids = set(current_connections)

    added_connections = tuple(
        current_connections[connection_id]
        for connection_id in current_ids - previous_ids
    )
    removed_connection_ids = tuple(previous_ids - current_ids)
    parameter_patches: list[DirectionalConnectionParametersPatch] = []
    for connection_id in previous_ids & current_ids:
        previous_connection = previous_connections[connection_id]
        current_connection = current_connections[connection_id]
        for direction, field_name in (
            (
                ConnectionDirection.ENDPOINT_A_TO_B,
                "endpoint_a_to_b",
            ),
            (
                ConnectionDirection.ENDPOINT_B_TO_A,
                "endpoint_b_to_a",
            ),
        ):
            old_parameters = getattr(previous_connection, field_name)
            new_parameters = getattr(current_connection, field_name)
            changed = {
                parameter_name: (
                    getattr(new_parameters, parameter_name)
                    if getattr(old_parameters, parameter_name)
                    != getattr(new_parameters, parameter_name)
                    else None
                )
                for parameter_name in (
                    "bandwidth_bps",
                    "delay_us",
                    "packet_loss_ppm",
                )
            }
            if any(value is not None for value in changed.values()):
                parameter_patches.append(
                    DirectionalConnectionParametersPatch(
                        connection_id=connection_id,
                        direction=direction,
                        **changed,
                    )
                )

    previous_assignments = (
        previous.connection_slice.zone_service_assignments
    )
    current_assignments = current.connection_slice.zone_service_assignments
    zone_patches: list[ZoneServiceAssignmentPatch] = []
    for zone_id in set(previous_assignments) | set(current_assignments):
        if zone_id not in previous_assignments:
            zone_patches.append(
                ZoneServiceAssignmentPatch(
                    zone_id,
                    DifferenceOperation.ADDED,
                    current_assignments[zone_id],
                )
            )
        elif zone_id not in current_assignments:
            zone_patches.append(
                ZoneServiceAssignmentPatch(
                    zone_id,
                    DifferenceOperation.REMOVED,
                    None,
                )
            )
        elif previous_assignments[zone_id] != current_assignments[zone_id]:
            zone_patches.append(
                ZoneServiceAssignmentPatch(
                    zone_id,
                    DifferenceOperation.CHANGED,
                    current_assignments[zone_id],
                )
            )

    previous_routes = previous.route_slice.routes
    current_routes = current.route_slice.routes
    route_patches: list[RoutePatch] = []
    for source in set(previous_routes) | set(current_routes):
        previous_targets = previous_routes.get(source, {})
        current_targets = current_routes.get(source, {})
        for target in set(previous_targets) | set(current_targets):
            route_key = RouteKey(source, target)
            if target not in previous_targets:
                route_patches.append(
                    RoutePatch(
                        route_key,
                        DifferenceOperation.ADDED,
                        current_targets[target],
                    )
                )
            elif target not in current_targets:
                route_patches.append(
                    RoutePatch(
                        route_key,
                        DifferenceOperation.REMOVED,
                        None,
                    )
                )
            elif previous_targets[target] != current_targets[target]:
                route_patches.append(
                    RoutePatch(
                        route_key,
                        DifferenceOperation.CHANGED,
                        current_targets[target],
                    )
                )

    return TopologyDifference(
        previous_slice_id=previous.slice_id,
        previous_timestamp=previous.timestamp,
        current_slice_id=current.slice_id,
        current_timestamp=current.timestamp,
        added_connections=added_connections,
        removed_connection_ids=removed_connection_ids,
        connection_parameter_patches=parameter_patches,
        zone_service_assignment_patches=zone_patches,
        route_patches=route_patches,
    )


def _validate_patch_identities(
    added: tuple[Connection, ...],
    removed: tuple[ConnectionId, ...],
    parameter_patches: tuple[DirectionalConnectionParametersPatch, ...],
    zone_patches: tuple[ZoneServiceAssignmentPatch, ...],
    route_patches: tuple[RoutePatch, ...],
) -> None:
    added_ids = tuple(value.connection_id for value in added)
    _require_unique(added_ids, "added Connection identity")
    _require_unique(removed, "removed Connection identity")
    parameter_keys = tuple(
        (value.connection_id, value.direction)
        for value in parameter_patches
    )
    _require_unique(parameter_keys, "Connection parameter patch identity")
    updated_ids = {value.connection_id for value in parameter_patches}
    if (
        set(added_ids) & set(removed)
        or set(added_ids) & updated_ids
        or set(removed) & updated_ids
    ):
        raise ValueError(
            "one Connection identity cannot be added, removed and updated"
        )
    _require_unique(
        tuple(value.zone_id for value in zone_patches),
        "Zone service patch identity",
    )
    _require_unique(
        tuple(value.route_key for value in route_patches),
        "Route patch identity",
    )


def _typed_tuple(
    values: Iterable[object],
    expected_type: type,
    field_name: str,
) -> tuple:
    try:
        result = tuple(values)
    except TypeError as error:
        raise TypeError(f"{field_name} must be iterable") from error
    if any(not isinstance(value, expected_type) for value in result):
        raise TypeError(
            f"{field_name} must contain {expected_type.__name__} values"
        )
    return result


def _require_unique(values: tuple, description: str) -> None:
    if len(set(values)) != len(values):
        raise ValueError(f"duplicate {description}")


def _validate_slice_id(value: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")


def _node_key(node_id: NodeId) -> tuple[int, str, str]:
    return node_id.asn, node_id.entity_kind, node_id.entity_id


def _connection_key(
    connection_id: ConnectionId,
) -> tuple[str, tuple[int, str, str], tuple[int, str, str]]:
    return (
        connection_id.kind.value,
        _node_key(connection_id.endpoint_a),
        _node_key(connection_id.endpoint_b),
    )


def _target_key(
    target: RouteTarget,
) -> tuple[str, int | str, str, str]:
    if isinstance(target, GroundStationRouteTarget):
        node = target.ground_station_id
        return "G", node.asn, node.entity_kind, node.entity_id
    if isinstance(target, ZoneRouteTarget):
        return "Z", target.zone_id, "", ""
    raise TypeError("RouteKey contains an invalid RouteTarget")


def _route_key(
    route_key: RouteKey,
) -> tuple[tuple[int, str, str], tuple[str, int | str, str, str]]:
    return _node_key(route_key.source), _target_key(route_key.target)


__all__ = [
    "ConnectionDirection",
    "DifferenceOperation",
    "DirectionalConnectionParametersPatch",
    "RoutePatch",
    "TopologyDifference",
    "ZoneServiceAssignmentPatch",
]
