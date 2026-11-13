"""Shared physical query values. Coordinates are ECEF metres, times are UTC."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from math import isfinite
from types import MappingProxyType
from typing import Mapping, Any

from skyseed.scene import Scene


def utc_time(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _vector(value, name):
    result = tuple(float(component) for component in value)
    if len(result) != 3 or not all(isfinite(component) for component in result):
        raise ValueError(f"{name} must contain three finite ECEF components")
    return result


@dataclass(frozen=True, slots=True)
class NodeState:
    position_m: tuple[float, float, float]
    velocity_m_s: tuple[float, float, float] | None = None
    data: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "position_m", _vector(self.position_m, "position_m"))
        if self.velocity_m_s is not None:
            velocity = _vector(self.velocity_m_s, "velocity_m_s")
            object.__setattr__(self, "velocity_m_s", velocity)
        object.__setattr__(self, "data", MappingProxyType(dict(self.data)))


@dataclass(frozen=True, slots=True, eq=False)
class PhysicalPositionSnapshot:
    scene: Scene
    timestamp: datetime
    states: Mapping[str, NodeState]

    def __post_init__(self):
        object.__setattr__(self, "timestamp", utc_time(self.timestamp))
        object.__setattr__(self, "states", MappingProxyType(dict(self.states)))

    def __getitem__(self, node_id):
        return self.states[node_id]


@dataclass(frozen=True, slots=True)
class LinkCandidate:
    """One node's report of an opportunity; absent optional fields stay unknown."""
    node_id: str
    peer_id: str
    distance_m: float | None = None
    propagation_delay_s: float | None = None
    elevation_deg: float | None = None
    data: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.node_id == self.peer_id:
            raise ValueError("a candidate must connect distinct nodes")
        for name in ("distance_m", "propagation_delay_s", "elevation_deg"):
            value = getattr(self, name)
            if value is None:
                continue
            if not isfinite(value) or (name != "elevation_deg" and value < 0):
                raise ValueError(f"invalid candidate {name}: {value!r}")
        object.__setattr__(self, "data", MappingProxyType(dict(self.data)))
