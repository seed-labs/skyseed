"""Construct an isolated Zone dataset directly from PhysicalWorld."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from skyseed.simulator import PhysicalWorld

from .artifact_writer import write_zone_artifacts
from .display_analysis import add_zone_display_analysis
from .renderer import render_zone_page


def collect_zone_data(physical_world: PhysicalWorld) -> dict[str, Any]:
    """Read world geometry into newly owned display data without copying it again."""
    if not isinstance(physical_world, PhysicalWorld):
        raise TypeError("physical_world must be a PhysicalWorld")
    try:
        layout = physical_world.get_zone_layout()
    except RuntimeError:
        return {
            "format_version": 1,
            "tool": "zone",
            "status": "PASS",
            "zone_layout_configured": False,
            "allow_overlap": None,
            "zones": [],
            "counts": {"zones": 0},
        }

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

    data = {
        "format_version": 1,
        "tool": "zone",
        "status": "PASS",
        "zone_layout_configured": True,
        "allow_overlap": layout.allow_overlap,
        "zones": zones,
        "counts": {},
    }
    add_zone_display_analysis(data)
    return data


def write_zone_tool(
    physical_world: PhysicalWorld,
    directory: Path,
) -> dict[str, object]:
    data = collect_zone_data(physical_world)
    return write_zone_artifacts(
        data,
        render_zone_page(data),
        directory,
    )


__all__ = ["collect_zone_data", "write_zone_tool"]
