"""Define the replaceable Position algorithm contract."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Protocol, runtime_checkable

from skyseed_v2.shared import NodeId

from ..geometry import EarthFixedPosition
from ..topology import Topology


@runtime_checkable
class PositionModel(Protocol):
    """Define the public one-timestamp Position algorithm boundary.

    Layer: replaceable Simulator algorithm contract. Slicer invokes an
    explicitly supplied implementation. It is not a Position owner, history,
    trajectory or fallback and is public from ``skyseed_v2.simulator``.
    """

    def compute_positions(
        self,
        topology: Topology,
        timestamp: datetime,
    ) -> Mapping[NodeId, EarthFixedPosition]:
        """Return every spatial node's Position for one Timeline timestamp."""


__all__ = ["PositionModel"]
