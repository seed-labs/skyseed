"""Immutable Satellite data supplied to the future Simulator."""

from __future__ import annotations

from dataclasses import dataclass

from . import identity as _identity


@dataclass(frozen=True, slots=True)
class SatelliteSimulationInput:
    """Contain only static Satellite values needed beyond the Emulator.

    Layer: immutable Emulator-to-Simulator boundary input. Application
    conversion creates it and future Simulator code reads it. It is not an
    Emulator entity, SEED Node or runtime position. It is public from the
    simulator_input package because it is the explicit static Satellite input.
    """

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
            raise ValueError(
                "SatelliteSimulationInput node_id kind must be satellite"
            )
        if self.node_id.entity_id != self.satellite_id:
            raise ValueError("satellite_id must match node_id.entity_id")
