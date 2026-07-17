"""Read-only contracts exposed beyond the static Emulator domain."""

from .catalog import SimulationEntityCatalog
from .ground_station import GroundStationDescriptor
from .identity import SimulationNodeId
from .satellite import SatelliteDescriptor

__all__ = [
    "GroundStationDescriptor",
    "SimulationEntityCatalog",
    "SimulationNodeId",
    "SatelliteDescriptor",
]
