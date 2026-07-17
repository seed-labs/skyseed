"""Immutable GroundStation view exposed to future Simulator code."""

from __future__ import annotations

from dataclasses import dataclass

from .satellite import RuntimeNodeReference


@dataclass(frozen=True, slots=True)
class GroundStationDescriptor:
    """Read-only projection of the static GroundStation contract."""

    ground_station_id: str
    latitude_deg: float
    longitude_deg: float
    elevation_m: float | None
    station_type: str
    operator: str
    runtime_node: RuntimeNodeReference
