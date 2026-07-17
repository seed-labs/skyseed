"""SEED integration for static Skyseed Satellites."""

from .autonomous_system import (
    GroundStationAutonomousSystem,
    SatelliteAutonomousSystem,
)
from .base import SatelliteBase
from .compiler import SatelliteDockerCompiler
from skyseed_v2.domain import (
    GROUND_STATION_REGISTRY_TYPE,
    GROUND_STATION_ROLE_LABEL,
    SATELLITE_REGISTRY_TYPE,
    SATELLITE_ROLE_LABEL,
)

__all__ = [
    "GROUND_STATION_REGISTRY_TYPE",
    "GROUND_STATION_ROLE_LABEL",
    "GroundStationAutonomousSystem",
    "SATELLITE_REGISTRY_TYPE",
    "SATELLITE_ROLE_LABEL",
    "SatelliteAutonomousSystem",
    "SatelliteBase",
    "SatelliteDockerCompiler",
]
