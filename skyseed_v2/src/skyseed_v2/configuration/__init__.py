"""Immutable static Emulator Config values and their YAML read function."""

from .ground_station import GroundStationConfig, ground_station_id_for
from .network import (
    EbgpPeeringConfig,
    EbgpRouterReferenceConfig,
    InternetExchangeAttachmentConfig,
    InternetExchangeConfig,
    LocalNetworkAttachmentConfig,
    OrdinaryAutonomousSystemConfig,
    NetworkConfig,
    RouterConfig,
)
from .satellite import (
    InOrbitPhaseDistributionConfig,
    OrbitGenerationConfig,
    OrbitConfig,
    RaanDistributionConfig,
    SatelliteConfig,
    ShellConfig,
)
from .static_emulator import (
    SkyseedAutonomousSystemConfig,
    StaticEmulatorConfig,
)
from .yaml import (
    GROUND_STATIONS_SCHEMA_VERSION,
    SCENARIO_SCHEMA_VERSION,
    StaticEmulatorConfigLoadError,
    load_static_emulator_config,
)

__all__ = [
    "EbgpPeeringConfig",
    "EbgpRouterReferenceConfig",
    "GROUND_STATIONS_SCHEMA_VERSION",
    "GroundStationConfig",
    "InOrbitPhaseDistributionConfig",
    "InternetExchangeAttachmentConfig",
    "InternetExchangeConfig",
    "LocalNetworkAttachmentConfig",
    "OrbitGenerationConfig",
    "OrbitConfig",
    "OrdinaryAutonomousSystemConfig",
    "NetworkConfig",
    "RouterConfig",
    "RaanDistributionConfig",
    "SCENARIO_SCHEMA_VERSION",
    "SatelliteConfig",
    "ShellConfig",
    "SkyseedAutonomousSystemConfig",
    "StaticEmulatorConfig",
    "StaticEmulatorConfigLoadError",
    "ground_station_id_for",
    "load_static_emulator_config",
]
