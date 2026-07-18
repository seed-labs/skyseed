"""Static Emulator entities and their closely bound value objects."""

from .ground_station import (
    GROUND_STATION_REGISTRY_TYPE,
    GROUND_STATION_ROLE_LABEL,
    GroundStation,
    GroundStationLocation,
)
from .orbit import Orbit
from .orbit_parameters import OrbitParameters
from .satellite import (
    SATELLITE_REGISTRY_TYPE,
    SATELLITE_ROLE_LABEL,
    Satellite,
    SatelliteRole,
)
from .shell import Shell
from .validation import validate_identifier

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
    "validate_identifier",
]
