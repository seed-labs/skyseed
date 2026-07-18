"""Immutable static input contracts owned by the future Simulator."""

from .ground_station import GroundStationSimulationInput
from .satellite import SatelliteSimulationInput
from .static_input import SimulatorStaticInput

__all__ = [
    "GroundStationSimulationInput",
    "SatelliteSimulationInput",
    "SimulatorStaticInput",
]
