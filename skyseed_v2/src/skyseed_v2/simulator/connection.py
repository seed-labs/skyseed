"""Immutable dynamic Connection values and replaceable Model contract."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from skyseed_v2.identifier import validate_identifier
from skyseed_v2.node_id import NodeId

from .position import EarthFixedPosition
from .topology import Topology


class ConnectionKind(Enum):
    """Classify one formal effective Connection.

    Layer: immutable Simulator dynamic identity component. ConnectionId owns
    it and callers use it for queries. It is not a candidate source, backend
    link type or mutable state and is public from ``skyseed_v2.simulator``.
    """

    INTER_SATELLITE = "inter-satellite"
    GROUND_STATION_ACCESS = "ground-station-access"


@dataclass(frozen=True, slots=True)
class ConnectionId:
    """Identify one undirected Connection independently of Slice time.

    Layer: immutable Simulator dynamic identity value. Connection owns it and
    ConnectionSlice indexes it. It is not a resource ID, timestamp or directed
    edge and is public from ``skyseed_v2.simulator``.
    """

    kind: ConnectionKind
    endpoint_a: NodeId
    endpoint_b: NodeId

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ConnectionKind):
            raise TypeError("kind must be a ConnectionKind")
        _require_node_id(self.endpoint_a, "endpoint_a")
        _require_node_id(self.endpoint_b, "endpoint_b")
        if self.endpoint_a == self.endpoint_b:
            raise ValueError("Connection endpoints must be distinct")
        if _node_key(self.endpoint_b) < _node_key(self.endpoint_a):
            first, second = self.endpoint_b, self.endpoint_a
            object.__setattr__(self, "endpoint_a", first)
            object.__setattr__(self, "endpoint_b", second)


@dataclass(frozen=True, slots=True)
class DirectionalConnectionParameters:
    """Hold one effective direction's forwarding parameters.

    Layer: immutable Simulator dynamic value. Connection owns two instances
    and Route or future execution consumers read them. It is not geometry,
    visibility evidence or backend state and is public from
    ``skyseed_v2.simulator``.
    """

    bandwidth_bps: int
    delay_us: int
    packet_loss_ppm: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.bandwidth_bps, bool)
            or not isinstance(self.bandwidth_bps, int)
            or self.bandwidth_bps <= 0
        ):
            raise ValueError("bandwidth_bps must be a positive integer")
        if (
            isinstance(self.delay_us, bool)
            or not isinstance(self.delay_us, int)
            or self.delay_us < 0
        ):
            raise ValueError("delay_us must be a non-negative integer")
        if (
            isinstance(self.packet_loss_ppm, bool)
            or not isinstance(self.packet_loss_ppm, int)
            or not 0 <= self.packet_loss_ppm <= 1_000_000
        ):
            raise ValueError("packet_loss_ppm must be between 0 and 1000000")


@dataclass(frozen=True, slots=True)
class Connection:
    """Represent one currently effective bidirectional Connection.

    Layer: immutable Simulator dynamic value. ConnectionModel creates it and
    ConnectionSlice owns it. It is not a candidate, visibility record or
    backend link and is public from ``skyseed_v2.simulator``.
    """

    connection_id: ConnectionId
    endpoint_a_to_b: DirectionalConnectionParameters
    endpoint_b_to_a: DirectionalConnectionParameters

    def __post_init__(self) -> None:
        if not isinstance(self.connection_id, ConnectionId):
            raise TypeError("connection_id must be a ConnectionId")
        for field_name in ("endpoint_a_to_b", "endpoint_b_to_a"):
            if not isinstance(
                getattr(self, field_name), DirectionalConnectionParameters
            ):
                raise TypeError(
                    f"{field_name} must be DirectionalConnectionParameters"
                )

    def parameters_from(
        self,
        source: NodeId,
    ) -> DirectionalConnectionParameters:
        if source == self.connection_id.endpoint_a:
            return self.endpoint_a_to_b
        if source == self.connection_id.endpoint_b:
            return self.endpoint_b_to_a
        raise KeyError(f"Node is not a Connection endpoint: {source.value}")


@dataclass(frozen=True, slots=True, init=False)
class ConnectionSlice:
    """Own one Slice's deeply immutable effective Connection result.

    Layer: immutable Simulator dynamic aggregate. ConnectionModel creates it
    and TopologySlice retains it. It is not candidate evidence, a Slice
    identity or service history and is public from ``skyseed_v2.simulator``.
    """

    connections: tuple[Connection, ...]
    zone_service_assignments: Mapping[str, NodeId] = field(
        repr=False, compare=True, hash=False
    )
    _by_id: Mapping[ConnectionId, Connection] = field(
        repr=False, compare=False, hash=False
    )

    def __init__(
        self,
        connections: Iterable[Connection] = (),
        zone_service_assignments: Mapping[str, NodeId] | Iterable[tuple[str, NodeId]] = (),
    ) -> None:
        connection_values = tuple(connections)
        if any(not isinstance(value, Connection) for value in connection_values):
            raise TypeError("connections must contain Connection values")
        ordered = tuple(
            sorted(connection_values, key=lambda value: _connection_key(value.connection_id))
        )
        by_id: dict[ConnectionId, Connection] = {}
        for connection in ordered:
            if connection.connection_id in by_id:
                raise ValueError(
                    f"duplicate ConnectionId: {connection.connection_id!r}"
                )
            by_id[connection.connection_id] = connection

        raw_assignments = (
            zone_service_assignments.items()
            if isinstance(zone_service_assignments, Mapping)
            else zone_service_assignments
        )
        assignments: dict[str, NodeId] = {}
        for zone_id, node_id in raw_assignments:
            zone_id = validate_identifier(zone_id, "zone_id")
            _require_node_id(node_id, "service satellite")
            if node_id.entity_kind != "satellite":
                raise ValueError("Zone service assignment must identify a Satellite")
            if zone_id in assignments:
                raise ValueError(f"duplicate Zone service assignment: {zone_id}")
            assignments[zone_id] = node_id

        object.__setattr__(self, "connections", ordered)
        object.__setattr__(
            self,
            "zone_service_assignments",
            MappingProxyType(dict(sorted(assignments.items()))),
        )
        object.__setattr__(self, "_by_id", MappingProxyType(by_id))

    def get_connection(self, connection_id: ConnectionId) -> Connection:
        if not isinstance(connection_id, ConnectionId):
            raise TypeError("connection_id must be a ConnectionId")
        try:
            return self._by_id[connection_id]
        except KeyError as error:
            raise KeyError(f"Connection not found: {connection_id!r}") from error

    def get_connections_for_node(self, node_id: NodeId) -> tuple[Connection, ...]:
        _require_node_id(node_id, "node_id")
        return tuple(
            connection
            for connection in self.connections
            if node_id
            in (
                connection.connection_id.endpoint_a,
                connection.connection_id.endpoint_b,
            )
        )

    def get_connection_for_endpoints(
        self,
        kind: ConnectionKind,
        first: NodeId,
        second: NodeId,
    ) -> Connection:
        return self.get_connection(ConnectionId(kind, first, second))

    def get_service_satellite(self, zone_id: str) -> NodeId:
        if not isinstance(zone_id, str):
            raise TypeError("zone_id must be a string")
        try:
            return self.zone_service_assignments[zone_id]
        except KeyError as error:
            raise KeyError(f"Zone is not served: {zone_id}") from error

    def get_served_zones(self, satellite_id: NodeId) -> tuple[str, ...]:
        _require_node_id(satellite_id, "satellite_id")
        return tuple(
            zone_id
            for zone_id, assigned in self.zone_service_assignments.items()
            if assigned == satellite_id
        )


@runtime_checkable
class ConnectionModel(Protocol):
    """Define the public one-timestamp Connection algorithm boundary.

    Layer: replaceable Simulator algorithm contract. Slicer invokes an
    explicitly supplied implementation. It is not a Connection owner, fallback
    or Position source and is public from ``skyseed_v2.simulator``.
    """

    def compute_connection_slice(
        self,
        topology: Topology,
        timestamp: datetime,
        positions: Mapping[NodeId, EarthFixedPosition],
        previous_connection_slice: ConnectionSlice,
    ) -> ConnectionSlice:
        """Return one complete effective Connection result."""


def _require_node_id(node_id: NodeId, field_name: str) -> None:
    if not isinstance(node_id, NodeId):
        raise TypeError(f"{field_name} must be a NodeId")


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


__all__ = [
    "Connection",
    "ConnectionId",
    "ConnectionKind",
    "ConnectionModel",
    "ConnectionSlice",
    "DirectionalConnectionParameters",
]
