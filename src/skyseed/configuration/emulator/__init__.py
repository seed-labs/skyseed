"""Immutable declarations and YAML loading for Emulator construction."""

from .attachments import (
    InternetExchangeAttachment,
    LocalNetworkAttachment,
)
from .ground_network import (
    EbgpPeeringConfig,
    EbgpRouterReference,
    InternetExchangeConfig,
    OrdinaryAutonomousSystemConfig,
    NetworkConfig,
    RouterConfig,
)
from .ground_station import GroundStationConfig
from skyseed.configuration.emulator.satellite_layout import (
    GeneratedOrbitLayout,
    InOrbitPhaseDistribution,
    OrbitConfig,
    RightAscensionDistribution,
    SatelliteConfig,
    ShellConfig,
)
from .static_emulator_config import (
    SkyseedAutonomousSystemConfig,
    StaticEmulatorConfig,
)
from .yaml import (
    GROUND_STATIONS_SCHEMA_VERSION,
    SCENARIO_SCHEMA_VERSION,
    SHELLS_SCHEMA_VERSION,
    StaticEmulatorConfigLoadError,
    load_static_emulator_config,
    load_shell_configs,
    load_ground_station_configs,
)

__all__ = [
    "EbgpPeeringConfig",
    "EbgpRouterReference",
    "GeneratedOrbitLayout",
    "GROUND_STATIONS_SCHEMA_VERSION",
    "GroundStationConfig",
    "InOrbitPhaseDistribution",
    "InternetExchangeAttachment",
    "InternetExchangeConfig",
    "LocalNetworkAttachment",
    "OrbitConfig",
    "OrdinaryAutonomousSystemConfig",
    "NetworkConfig",
    "RouterConfig",
    "RightAscensionDistribution",
    "SCENARIO_SCHEMA_VERSION",
    "SHELLS_SCHEMA_VERSION",
    "SatelliteConfig",
    "ShellConfig",
    "SkyseedAutonomousSystemConfig",
    "StaticEmulatorConfig",
    "StaticEmulatorConfigLoadError",
    "load_static_emulator_config",
    "load_shell_configs",
    "load_ground_station_configs",
]
