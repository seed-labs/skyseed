"""Immutable GroundStation view exposed to future Simulator code."""

from __future__ import annotations

from dataclasses import dataclass

from . import identity as _identity


@dataclass(frozen=True, slots=True)
class GroundStationDescriptor:
    """Read-only projection of the static GroundStation contract."""

    node_id: _identity.SimulationNodeId
    ground_station_id: str
    latitude_deg: float
    longitude_deg: float
    elevation_m: float | None
    station_type: str
    operator: str

    def __post_init__(self) -> None:
        if not isinstance(self.node_id, _identity.SimulationNodeId):
            raise TypeError("node_id must be a SimulationNodeId")
        if self.node_id.entity_kind != _identity.GROUND_STATION_ENTITY_KIND:
            raise ValueError(
                "GroundStationDescriptor node_id kind must be ground-station"
            )
        if self.node_id.entity_id != self.ground_station_id:
            raise ValueError("ground_station_id must match node_id.entity_id")
