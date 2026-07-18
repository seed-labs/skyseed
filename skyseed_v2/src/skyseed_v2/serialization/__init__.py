"""Serialization adapters for versioned static Emulator inputs."""

from .static_emulator_yaml_loader import (
    GROUND_STATIONS_SCHEMA_VERSION,
    SCENARIO_SCHEMA_VERSION,
    StaticEmulatorScenarioLoadError,
    StaticEmulatorScenarioLoader,
)

__all__ = [
    "GROUND_STATIONS_SCHEMA_VERSION",
    "SCENARIO_SCHEMA_VERSION",
    "StaticEmulatorScenarioLoadError",
    "StaticEmulatorScenarioLoader",
]
