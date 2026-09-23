"""Tool-private spherical display geometry shared by inspection renderers."""

from __future__ import annotations


# IUGG mean Earth radius. The 3D inspection globes are spherical display
# surfaces, while formal Simulator coordinates remain WGS84/ECEF facts.
EARTH_REFERENCE_RADIUS_M = 6_371_008.8
# WGS84 semi-major axis. Display analysis uses it only to explain the radial
# reference used by the current planned circular-orbit presentation; formal
# Position and Connection geometry remains the Simulator's ECEF/WGS84 result.
WGS84_SEMI_MAJOR_AXIS_M = 6_378_137.0
SURFACE_MARKER_OFFSET_RADII = 0.004
ZONE_MARKER_OFFSET_RADII = 0.006
ROUTE_MARKER_OFFSET_RADII = 0.002


def physical_display_radius(height_m: float) -> float:
    """Map formal height to a proportional radius on the unit-Earth globe."""

    return 1.0 + float(height_m) / EARTH_REFERENCE_RADIUS_M


def display_geometry_policy() -> dict[str, float | str]:
    """Return detached constants suitable for embedding in Tool HTML."""

    return {
        "earth_reference_radius_m": EARTH_REFERENCE_RADIUS_M,
        "surface_marker_offset_radii": SURFACE_MARKER_OFFSET_RADII,
        "zone_marker_offset_radii": ZONE_MARKER_OFFSET_RADII,
        "route_marker_offset_radii": ROUTE_MARKER_OFFSET_RADII,
        "authority": "tool-private-spherical-display-only",
    }


__all__ = [
    "EARTH_REFERENCE_RADIUS_M",
    "ROUTE_MARKER_OFFSET_RADII",
    "SURFACE_MARKER_OFFSET_RADII",
    "ZONE_MARKER_OFFSET_RADII",
    "WGS84_SEMI_MAJOR_AXIS_M",
    "display_geometry_policy",
    "physical_display_radius",
]
