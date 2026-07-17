"""SEED integration for static Skyseed entities."""

from .autonomous_system import SkyseedAutonomousSystem
from .base import SatelliteBase
from .compiler import SkyseedDockerCompiler
from skyseed_v2.domain import (
    GROUND_STATION_REGISTRY_TYPE,
    GROUND_STATION_ROLE_LABEL,
    SATELLITE_REGISTRY_TYPE,
    SATELLITE_ROLE_LABEL,
)

__all__ = [
    "GROUND_STATION_REGISTRY_TYPE",
    "GROUND_STATION_ROLE_LABEL",
    "SkyseedAutonomousSystem",
    "SATELLITE_REGISTRY_TYPE",
    "SATELLITE_ROLE_LABEL",
    "SatelliteBase",
    "SkyseedDockerCompiler",
]
