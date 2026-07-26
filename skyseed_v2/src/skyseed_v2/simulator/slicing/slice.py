"""Define the immutable published TopologySlice aggregate."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType

from skyseed_v2.shared.time import normalize_utc_datetime
from skyseed_v2.shared import NodeId

from .connection import ConnectionSlice
from ..geometry import EarthFixedPosition
from .route import RouteSlice


@dataclass(frozen=True, slots=True, init=False)
class TopologySlice:
    """Aggregate one atomically published dynamic network state.

    Layer: immutable Simulator dynamic result. Slicer creates and retains it
    and callers query it. It is not a Topology owner, path resolver, Model,
    Planner or backend state and is public from ``skyseed_v2.simulator``.
    """

    slice_id: int
    timestamp: datetime | None
    positions: Mapping[NodeId, EarthFixedPosition] = field(
        repr=False, compare=True, hash=False
    )
    connection_slice: ConnectionSlice
    route_slice: RouteSlice

    def __init__(
        self,
        *,
        slice_id: int,
        timestamp: datetime | None,
        positions: Mapping[NodeId, EarthFixedPosition],
        connection_slice: ConnectionSlice,
        route_slice: RouteSlice,
    ) -> None:
        if (
            isinstance(slice_id, bool)
            or not isinstance(slice_id, int)
            or slice_id < 0
        ):
            raise ValueError("slice_id must be a non-negative integer")
        if not isinstance(positions, Mapping):
            raise TypeError("positions must be a Mapping")
        copied_positions = {}
        for node_id, position in positions.items():
            if not isinstance(node_id, NodeId):
                raise TypeError("positions keys must be NodeId values")
            if not isinstance(position, EarthFixedPosition):
                raise TypeError(
                    "positions values must be EarthFixedPosition values"
                )
            copied_positions[node_id] = position
        if not isinstance(connection_slice, ConnectionSlice):
            raise TypeError("connection_slice must be a ConnectionSlice")
        if not isinstance(route_slice, RouteSlice):
            raise TypeError("route_slice must be a RouteSlice")

        if slice_id == 0:
            if timestamp is not None:
                raise ValueError("synthetic Slice 0 timestamp must be None")
            if copied_positions or connection_slice.connections or route_slice.routes:
                raise ValueError("synthetic Slice 0 dynamic results must be empty")
            if connection_slice.zone_service_assignments:
                raise ValueError("synthetic Slice 0 Zone assignments must be empty")
            normalized_timestamp = None
        else:
            if timestamp is None:
                raise ValueError("real Slice timestamp must be a datetime")
            normalized_timestamp = normalize_utc_datetime(
                timestamp, "timestamp"
            )

        object.__setattr__(self, "slice_id", slice_id)
        object.__setattr__(self, "timestamp", normalized_timestamp)
        object.__setattr__(
            self,
            "positions",
            MappingProxyType(
                dict(
                    sorted(
                        copied_positions.items(),
                        key=lambda item: _node_key(item[0]),
                    )
                )
            ),
        )
        object.__setattr__(self, "connection_slice", connection_slice)
        object.__setattr__(self, "route_slice", route_slice)

    def get_position(self, node_id: NodeId) -> EarthFixedPosition:
        if not isinstance(node_id, NodeId):
            raise TypeError("node_id must be a NodeId")
        try:
            return self.positions[node_id]
        except KeyError as error:
            raise KeyError(f"Position not found: {node_id.value}") from error


def _node_key(node_id: NodeId) -> tuple[int, str, str]:
    return node_id.asn, node_id.entity_kind, node_id.entity_id


__all__ = ["TopologySlice"]
