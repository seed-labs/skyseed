"""Define the replaceable physical Position projection contract."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from skyseed_v2.shared import NodeId

from ..geometry import EarthFixedPosition

if TYPE_CHECKING:
    from .world import PhysicalWorld


@runtime_checkable
class PositionModel(Protocol):
    """Define the public one-timestamp physical Position algorithm boundary.

    Layer: replaceable Simulator physical algorithm contract. One ``PhysicalWorld``
    owns an explicitly supplied implementation and invokes it from
    ``frame_at()``. It is not a time cursor, Position owner, history,
    trajectory, operational-network model or fallback and is public from
    ``skyseed_v2.simulator.physical``.
    """

    def compute_positions(
        self,
        physical_world: PhysicalWorld,
        timestamp: datetime,
    ) -> Mapping[NodeId, EarthFixedPosition]:
        """Return every physical node's Position for one requested timestamp."""


__all__ = ["PositionModel"]
