"""Immutable Position values and replaceable Position algorithm contract."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Protocol, runtime_checkable

from skyseed_v2.node_id import NodeId

from .topology import Topology


@dataclass(frozen=True, slots=True)
class EarthFixedPosition:
    """Represent one authoritative WGS84 Earth-fixed Cartesian Position.

    Layer: immutable Simulator dynamic value. PositionModel creates it and
    dynamic Slice consumers read it. It is not a geographic state, trajectory,
    velocity or third-party frame value; it is public from
    ``skyseed_v2.simulator`` as the sole formal Position representation.
    """

    x_m: float
    y_m: float
    z_m: float

    def __post_init__(self) -> None:
        for field_name in ("x_m", "y_m", "z_m"):
            value = float(getattr(self, field_name))
            if not isfinite(value):
                raise ValueError(f"{field_name} must be finite")
            object.__setattr__(self, field_name, value)

    @property
    def latitude_deg(self) -> float:
        from .coordinates import ecef_to_geodetic

        return ecef_to_geodetic(self)[0]

    @property
    def longitude_deg(self) -> float:
        from .coordinates import ecef_to_geodetic

        return ecef_to_geodetic(self)[1]

    @property
    def ellipsoidal_height_m(self) -> float:
        from .coordinates import ecef_to_geodetic

        return ecef_to_geodetic(self)[2]

    @property
    def geographic(self) -> tuple[float, float, float]:
        """Return the derived WGS84 latitude, longitude and ellipsoidal height."""

        from .coordinates import ecef_to_geodetic

        return ecef_to_geodetic(self)


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


__all__ = ["EarthFixedPosition", "PositionModel"]
