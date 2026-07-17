"""Immutable Satellite views exposed to future Simulator code."""

from __future__ import annotations

from dataclasses import dataclass

from . import identity as _identity


@dataclass(frozen=True, slots=True)
class SatelliteDescriptor:
    """Read-only Satellite projection for future Simulator consumers."""

    node_id: _identity.SimulationNodeId
    satellite_id: str
    shell_id: str
    orbit_id: str
    orbit_index: int
    altitude_km: float
    inclination_deg: float
    right_ascension_deg: float
    mean_anomaly_deg: float

    def __post_init__(self) -> None:
        if not isinstance(self.node_id, _identity.SimulationNodeId):
            raise TypeError("node_id must be a SimulationNodeId")
        if self.node_id.entity_kind != _identity.SATELLITE_ENTITY_KIND:
            raise ValueError("SatelliteDescriptor node_id kind must be satellite")
        if self.node_id.entity_id != self.satellite_id:
            raise ValueError("satellite_id must match node_id.entity_id")
