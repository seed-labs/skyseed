"""Read formal static Zone layout APIs into a detached Tool-owned snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from skyseed_v2.simulator import PhysicalWorld


@dataclass(frozen=True, slots=True)
class ZoneRead:
    """Hold detached values returned by one exact PhysicalWorld/Zone read."""

    snapshot: dict[str, Any]


def read_zones(physical_world: PhysicalWorld) -> ZoneRead:
    """Read owned Zone layout fields without executing membership Models."""

    if not isinstance(physical_world, PhysicalWorld):
        raise TypeError("physical_world must be a PhysicalWorld")
    try:
        layout = physical_world.get_zone_layout()
    except RuntimeError:
        return ZoneRead(
            {
                "format_version": 1,
                "tool": "zone",
                "status": "PASS",
                "zone_layout_configured": False,
                "allow_overlap": None,
                "zones": [],
                "counts": {},
            }
        )

    zones = [
        {
            "zone_id": zone.zone_id,
            "priority": zone.priority,
            "geometry": {
                "south_latitude_deg": zone.geometry.south_latitude_deg,
                "north_latitude_deg": zone.geometry.north_latitude_deg,
                "west_longitude_deg": zone.geometry.west_longitude_deg,
                "east_longitude_deg": zone.geometry.east_longitude_deg,
                "wrapped": (
                    zone.geometry.west_longitude_deg
                    > zone.geometry.east_longitude_deg
                ),
                "representative_center": list(
                    zone.geometry.representative_center
                ),
            },
        }
        for zone in layout.zones
    ]

    return ZoneRead(
        {
            "format_version": 1,
            "tool": "zone",
            "status": "PASS",
            "zone_layout_configured": True,
            "allow_overlap": layout.allow_overlap,
            "zones": zones,
            "counts": {},
        }
    )


__all__ = ["ZoneRead", "read_zones"]
