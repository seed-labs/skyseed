"""SEED integration for static Skyseed Satellites."""

from .autonomous_system import SatelliteAutonomousSystem
from .base import SatelliteBase
from .compiler import SatelliteDockerCompiler
from skyseed_v2.domain import SATELLITE_REGISTRY_TYPE, SATELLITE_ROLE_LABEL

__all__ = [
    "SATELLITE_REGISTRY_TYPE",
    "SATELLITE_ROLE_LABEL",
    "SatelliteAutonomousSystem",
    "SatelliteBase",
    "SatelliteDockerCompiler",
]
