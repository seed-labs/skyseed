"""Read-only contracts exposed beyond the static Emulator domain."""

from .ground_station import GroundStationDescriptor
from .runtime import RuntimeNodeReference
from .satellite import SatelliteDescriptor

__all__ = [
    "GroundStationDescriptor",
    "RuntimeNodeReference",
    "SatelliteDescriptor",
]
