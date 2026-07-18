"""Immutable static data exported from the Emulator to the Simulator."""

from .ground_station import GroundStationSimulationInput
from .satellite import SatelliteSimulationInput
from .static_input import SimulatorStaticInput

__all__ = [
    "GroundStationSimulationInput",
    "SatelliteSimulationInput",
    "SimulatorStaticInput",
]
