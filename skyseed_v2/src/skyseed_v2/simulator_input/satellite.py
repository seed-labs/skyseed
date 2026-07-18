"""Immutable Satellite data supplied to the future Simulator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Self

from skyseed_v2.domain import Satellite

from . import identity as _identity


@dataclass(frozen=True, slots=True)
class SatelliteSimulationInput:
    """Contain only static Satellite values needed beyond the Emulator.

    Layer: immutable Emulator-to-Simulator boundary input. Its classmethod
    copies one Satellite and future Simulator code reads the result. It is not
    an Emulator entity, SEED Node or runtime position. It is public because it
    is the explicit static Satellite input accepted by the future Simulator.
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

    @classmethod
    def from_satellite(cls, satellite: Satellite) -> Self:
        """Copy approved scalar values from one Emulator Satellite."""

        if not isinstance(satellite, Satellite):
            raise TypeError("satellite must be a Satellite")
        parameters = satellite.orbit_parameters
        return cls(
            node_id=_identity.SimulationNodeId(
                asn=satellite.getAsn(),
                entity_kind=_identity.SATELLITE_ENTITY_KIND,
                entity_id=satellite.satellite_id,
            ),
            satellite_id=satellite.satellite_id,
            shell_id=satellite.shell_id,
            orbit_id=satellite.orbit_id,
            orbit_index=satellite.orbit_index,
            altitude_km=parameters.altitude_km,
            inclination_deg=parameters.inclination_deg,
            right_ascension_deg=parameters.right_ascension_deg,
            mean_anomaly_deg=satellite.mean_anomaly_deg,
        )
