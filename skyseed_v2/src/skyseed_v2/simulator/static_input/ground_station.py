"""Immutable GroundStation data supplied to Simulator construction."""

from __future__ import annotations

from dataclasses import dataclass

from skyseed_v2.node_id import NodeId


_GROUND_STATION_ENTITY_KIND = "ground-station"


@dataclass(frozen=True, slots=True)
class GroundStationSimulationInput:
    """Contain only static GroundStation values needed beyond the Emulator.

    Layer: immutable Simulator static-input contract. The external input
    generation adapter creates it and Simulator construction reads it. It is not
    an Emulator entity, SEED Router or execution target. It is public from the
    Simulator static-input package because it is the explicit GroundStation
    input contract.
    """

    node_id: NodeId
    ground_station_id: str
    latitude_deg: float
    longitude_deg: float
    elevation_m: float | None
    station_type: str
    operator: str

    def __post_init__(self) -> None:
        if not isinstance(self.node_id, NodeId):
            raise TypeError("node_id must be a NodeId")
        if self.node_id.entity_kind != _GROUND_STATION_ENTITY_KIND:
            raise ValueError(
                "GroundStationSimulationInput node_id kind must be ground-station"
            )
        if self.node_id.entity_id != self.ground_station_id:
            raise ValueError("ground_station_id must match node_id.entity_id")
