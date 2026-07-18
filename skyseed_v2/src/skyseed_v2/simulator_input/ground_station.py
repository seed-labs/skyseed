"""Immutable GroundStation data supplied to the future Simulator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Self

from skyseed_v2.domain import GroundStation

from . import identity as _identity


@dataclass(frozen=True, slots=True)
class GroundStationSimulationInput:
    """Contain only static GroundStation values needed beyond the Emulator.

    Layer: immutable Emulator-to-Simulator boundary input. Its classmethod
    copies one GroundStation and future Simulator code reads the result. It is
    not an Emulator entity, SEED Router or execution target. It is public
    because it is the explicit static GroundStation input for the Simulator.
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

    @classmethod
    def from_ground_station(cls, ground_station: GroundStation) -> Self:
        """Copy approved scalar values from one Emulator GroundStation."""

        if not isinstance(ground_station, GroundStation):
            raise TypeError("ground_station must be a GroundStation")
        location = ground_station.location
        return cls(
            node_id=_identity.SimulationNodeId(
                asn=ground_station.getAsn(),
                entity_kind=_identity.GROUND_STATION_ENTITY_KIND,
                entity_id=ground_station.ground_station_id,
            ),
            ground_station_id=ground_station.ground_station_id,
            latitude_deg=location.latitude_deg,
            longitude_deg=location.longitude_deg,
            elevation_m=location.elevation_m,
            station_type=ground_station.station_type,
            operator=ground_station.operator,
        )
