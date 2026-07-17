"""Immutable Satellite views exposed to future Simulator code."""

from __future__ import annotations

from dataclasses import dataclass

from . import runtime as _runtime


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
    runtime_node: _runtime.RuntimeNodeReference
