"""Immutable Satellite views exposed to future Simulator code."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RuntimeNodeReference:
    """Stable Emulator-Simulator contract reference to a runtime node.

    ``registry_type`` currently maps the node identity used by the SEED
    integration. Whether that field remains in the long-term contract is a
    deferred contract-design decision.
    """

    asn: int
    registry_type: str
    node_name: str

    @property
    def value(self) -> str:
        return f"{self.asn}/{self.registry_type}/{self.node_name}"


@dataclass(frozen=True, slots=True)
class SatelliteDescriptor:
    """Read-only Satellite projection for future Simulator consumers."""

    satellite_id: str
    shell_id: str
    orbit_id: str
    orbit_index: int
    altitude_km: float
    inclination_deg: float
    right_ascension_deg: float
    mean_anomaly_deg: float
    runtime_node: RuntimeNodeReference
