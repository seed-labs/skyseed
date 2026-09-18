"""External projections between the Emulator and receiver-owned inputs."""

from .emulator_to_simulator import (
    create_ground_station_simulation_input, create_satellite_simulation_input,
    create_simulator_static_input,
)

__all__ = ["create_ground_station_simulation_input", "create_satellite_simulation_input",
           "create_simulator_static_input"]
