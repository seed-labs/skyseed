"""Expose the Simulator-owned PhysicalWorld domain and Zone layout."""

from skyseed_v2.simulator.physical.world.capability import InterSatelliteLinkCapability
from skyseed_v2.simulator.physical.queries.position import PhysicalPositionSnapshot
from skyseed_v2.simulator.physical.queries.physical_link import (
    GroundStationAccessCandidate,
    InterSatelliteLinkCandidate,
    PhysicalLinkQueryResult,
    PhysicalLinkRequest,
    PhysicalContactWindow,
    PhysicalLinkWorkCounts,
    ZoneServiceCandidate,
)
from .models import PositionModel, PhysicalLinkModel, ZoneMembershipModel, DefaultZoneMembershipModel

from .world import (
    PhysicalGroundStation,
    PhysicalOrbit,
    PhysicalSatellite,
    PhysicalShell,
    PhysicalAutonomousSystem,
    PhysicalWorld,
)
from skyseed_v2.simulator.physical.world.zone import (
    LatitudeLongitudeRectangle,
    SphericalCap,
    Zone,
    ZoneLayout,
)

__all__ = [
    "DefaultZoneMembershipModel",
    "PhysicalGroundStation",
    "GroundStationAccessCandidate",
    "InterSatelliteLinkCapability",
    "InterSatelliteLinkCandidate",
    "LatitudeLongitudeRectangle",
    "PhysicalOrbit",
    "PhysicalPositionSnapshot",
    "PhysicalLinkModel",
    "PhysicalLinkQueryResult",
    "PhysicalLinkRequest",
    "PhysicalContactWindow",
    "PhysicalLinkWorkCounts",
    "PositionModel",
    "PhysicalSatellite",
    "PhysicalShell",
    "PhysicalAutonomousSystem",
    "SphericalCap",
    "PhysicalWorld",
    "Zone",
    "ZoneLayout",
    "ZoneMembershipModel",
    "ZoneServiceCandidate",
]
