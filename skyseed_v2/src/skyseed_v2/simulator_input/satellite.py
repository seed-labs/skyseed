"""Immutable Satellite data supplied to the future Simulator."""

from __future__ import annotations

from dataclasses import dataclass

from skyseed_v2.node_id import NodeId


_SATELLITE_ENTITY_KIND = "satellite"


@dataclass(frozen=True, slots=True)
class SatelliteSimulationInput:
    """Contain only static Satellite values needed beyond the Emulator.

    Layer: immutable Emulator-to-Simulator boundary input. Application
    conversion creates it and future Simulator code reads it. It is not an
    Emulator entity, SEED Node or runtime position. It is public from the
    simulator_input package because it is the explicit static Satellite input.
    """

    node_id: NodeId
    satellite_id: str
    shell_id: str
    orbit_id: str
    orbit_index: int
    altitude_km: float
    inclination_deg: float
    right_ascension_deg: float
    mean_anomaly_deg: float

    def __post_init__(self) -> None:
        if not isinstance(self.node_id, NodeId):
            raise TypeError("node_id must be a NodeId")
        if self.node_id.entity_kind != _SATELLITE_ENTITY_KIND:
            raise ValueError(
                "SatelliteSimulationInput node_id kind must be satellite"
            )
        if self.node_id.entity_id != self.satellite_id:
            raise ValueError("satellite_id must match node_id.entity_id")
