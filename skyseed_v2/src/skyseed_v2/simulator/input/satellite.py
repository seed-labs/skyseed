"""Define immutable Satellite input for Simulator construction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from skyseed_v2.shared.time import normalize_utc_datetime
from skyseed_v2.shared import NodeId


_SATELLITE_ENTITY_KIND = "satellite"


@dataclass(frozen=True, slots=True)
class SatelliteSimulationInput:
    """Contain only static Satellite values needed beyond the Emulator.

    Layer: immutable Simulator static-input contract. The external input
    generation adapter creates it and Simulator construction reads it. It is not
    an Emulator entity, SEED Node or runtime position. It is public from the
    Simulator static-input package because it is the explicit static Satellite
    input contract.
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
    epoch_utc: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.node_id, NodeId):
            raise TypeError("node_id must be a NodeId")
        if self.node_id.entity_kind != _SATELLITE_ENTITY_KIND:
            raise ValueError(
                "SatelliteSimulationInput node_id kind must be satellite"
            )
        if self.node_id.entity_id != self.satellite_id:
            raise ValueError("satellite_id must match node_id.entity_id")
        object.__setattr__(
            self,
            "epoch_utc",
            normalize_utc_datetime(self.epoch_utc, "epoch_utc"),
        )
