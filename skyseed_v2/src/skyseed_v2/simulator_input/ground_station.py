"""Immutable GroundStation data supplied to the future Simulator."""

from __future__ import annotations

from dataclasses import dataclass

from . import identity as _identity


@dataclass(frozen=True, slots=True)
class GroundStationSimulationInput:
    """Contain only static GroundStation values needed beyond the Emulator.

    Layer: immutable Emulator-to-Simulator boundary input. Application
    conversion creates it and future Simulator code reads it. It is not an
    Emulator entity, SEED Router or execution target. It is public from the
    simulator_input package because it is the explicit GroundStation input.
    """

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
                "GroundStationSimulationInput node_id kind must be ground-station"
            )
        if self.node_id.entity_id != self.ground_station_id:
            raise ValueError("ground_station_id must match node_id.entity_id")
