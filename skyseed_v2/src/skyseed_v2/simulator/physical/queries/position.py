"""Define one immutable timestamped projection of the physical world."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType

from skyseed_v2.shared import NodeId
from skyseed_v2.shared.time import normalize_utc_datetime

from skyseed_v2.simulator.geometry import EarthFixedPosition


@dataclass(frozen=True, slots=True, init=False)
class PhysicalPositionSnapshot:
    """Hold physical node Positions observed at one exact timestamp.

    Layer: immutable Simulator physical-domain value. ``PhysicalWorld.frame_at()``
    creates it and physical readers consume it. It is not operational state,
    identity, Connection/Route/Zone-serving decision, Plan, or realized runtime
    state and is public from ``skyseed_v2.simulator.physical``.
    """

    timestamp: datetime
    positions: Mapping[NodeId, EarthFixedPosition] = field(
        repr=False,
        compare=True,
        hash=False,
    )

    _node_inventory: object = field(repr=False, compare=False, hash=False)

    def __init__(self) -> None:
        raise TypeError("PhysicalPositionSnapshot values are created by PhysicalWorld.frame_at()")

    @classmethod
    def _create(
        cls,
        *,
        timestamp: datetime,
        positions: Mapping[NodeId, EarthFixedPosition],
        expected_node_ids: Mapping[NodeId, object],
    ) -> PhysicalPositionSnapshot:
        if not isinstance(positions, Mapping):
            raise TypeError("PositionModel must return a Mapping")
        if len(positions) != len(expected_node_ids):
            raise ValueError("PositionModel must return exactly every PhysicalWorld physical node")

        def checked_items():
            for node_id, position in positions.items():
                if not isinstance(node_id, NodeId):
                    raise TypeError("PositionModel keys must be NodeId values")
                if not isinstance(position, EarthFixedPosition):
                    raise TypeError("PositionModel values must be EarthFixedPosition values")
                if node_id not in expected_node_ids:
                    raise ValueError("PositionModel must return exactly every PhysicalWorld physical node")
                yield node_id, position

        # One validation traversal, one canonical sort and one owned mapping.
        copied = dict(sorted(checked_items(), key=lambda item: _node_key(item[0])))
        instance = object.__new__(cls)
        object.__setattr__(instance, "_node_inventory", expected_node_ids)
        object.__setattr__(
            instance,
            "timestamp",
            normalize_utc_datetime(timestamp, "timestamp"),
        )
        object.__setattr__(
            instance,
            "positions",
            MappingProxyType(copied),
        )
        return instance

    def get_position(self, node_id: NodeId) -> EarthFixedPosition:
        if not isinstance(node_id, NodeId):
            raise TypeError("node_id must be a NodeId")
        try:
            return self.positions[node_id]
        except KeyError as error:
            raise KeyError(f"Position not found: {node_id.value}") from error


def _node_key(node_id: NodeId) -> tuple[int, str, str]:
    return node_id.asn, node_id.entity_kind, node_id.entity_id


__all__ = ["PhysicalPositionSnapshot"]
