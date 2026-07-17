"""Static Emulator domain objects."""

from .ground_station import (
    GROUND_STATION_REGISTRY_TYPE,
    GROUND_STATION_ROLE_LABEL,
    GroundStation,
    GroundStationLocation,
)
from .satellite import (
    SATELLITE_REGISTRY_TYPE,
    SATELLITE_ROLE_LABEL,
    Orbit,
    OrbitParameters,
    Satellite,
    SatelliteRole,
    Shell,
)

__all__ = [
    "GROUND_STATION_REGISTRY_TYPE",
    "GROUND_STATION_ROLE_LABEL",
    "GroundStation",
    "GroundStationLocation",
    "SATELLITE_REGISTRY_TYPE",
    "SATELLITE_ROLE_LABEL",
    "Orbit",
    "OrbitParameters",
    "Satellite",
    "SatelliteRole",
    "Shell",
]
