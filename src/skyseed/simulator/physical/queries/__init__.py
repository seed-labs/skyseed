"""Immutable physical query requests and returned facts; no query manager."""
from .position import PhysicalPositionSnapshot
from .physical_link import (PhysicalLinkRequest, PhysicalLinkQueryResult,
    PhysicalContactWindow, PhysicalLinkWorkCounts, InterSatelliteLinkCandidate,
    GroundStationAccessCandidate, ZoneServiceCandidate)

__all__ = ["PhysicalPositionSnapshot", "PhysicalLinkRequest", "PhysicalLinkQueryResult",
           "PhysicalContactWindow", "PhysicalLinkWorkCounts", "InterSatelliteLinkCandidate",
           "GroundStationAccessCandidate", "ZoneServiceCandidate"]
