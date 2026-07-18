"""Immutable, backend-independent specifications for static construction."""

from .ground_station import GroundStationSpecification, ground_station_id_for
from .network import (
    EbgpPeeringSpecification,
    EbgpRouterReferenceSpecification,
    InternetExchangeAttachmentSpecification,
    InternetExchangeSpecification,
    LocalNetworkAttachmentSpecification,
    OrdinaryAutonomousSystemSpecification,
    OrdinaryNetworkSpecification,
    OrdinaryRouterSpecification,
)
from .satellite import (
    InOrbitPhaseDistributionSpecification,
    OrbitGenerationSpecification,
    OrbitSpecification,
    RaanDistributionSpecification,
    SatelliteSpecification,
    ShellSpecification,
)
from .scenario import (
    SkyseedAutonomousSystemSpecification,
    StaticEmulatorScenarioSpecification,
)

__all__ = [
    "EbgpPeeringSpecification",
    "EbgpRouterReferenceSpecification",
    "GroundStationSpecification",
    "InOrbitPhaseDistributionSpecification",
    "InternetExchangeAttachmentSpecification",
    "InternetExchangeSpecification",
    "LocalNetworkAttachmentSpecification",
    "OrbitGenerationSpecification",
    "OrbitSpecification",
    "OrdinaryAutonomousSystemSpecification",
    "OrdinaryNetworkSpecification",
    "OrdinaryRouterSpecification",
    "RaanDistributionSpecification",
    "SatelliteSpecification",
    "ShellSpecification",
    "SkyseedAutonomousSystemSpecification",
    "StaticEmulatorScenarioSpecification",
    "ground_station_id_for",
]
