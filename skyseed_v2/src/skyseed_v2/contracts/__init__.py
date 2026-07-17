"""Read-only contracts exposed beyond the static Emulator domain."""

from .ground_station import GroundStationDescriptor
from .satellite import RuntimeNodeReference, SatelliteDescriptor

__all__ = [
    "GroundStationDescriptor",
    "RuntimeNodeReference",
    "SatelliteDescriptor",
]
