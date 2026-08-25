"""Add non-authoritative Zone display geometry and summaries."""

from __future__ import annotations

from typing import Any


def _canonical_longitude(longitude_deg: float) -> float:
    canonical = (longitude_deg + 180.0) % 360.0 - 180.0
    return 0.0 if abs(canonical) < 1e-10 else canonical


def sample_rectangle_boundary(
    geometry: dict[str, Any],
) -> list[list[float]]:
    """Sample rectangle bounds for display, including wrapping."""

    if not isinstance(geometry, dict):
        raise TypeError("geometry must be a mapping")
    south_value = float(geometry["south_latitude_deg"])
    north_value = float(geometry["north_latitude_deg"])
    west_value = float(geometry["west_longitude_deg"])
    east_value = float(geometry["east_longitude_deg"])
    longitude_steps = 32
    latitude_steps = 16
    span = (east_value - west_value) % 360.0
    eastward = [
        _canonical_longitude(
            west_value + span * step / longitude_steps
        )
        for step in range(longitude_steps + 1)
    ]
    north = [
        [north_value, longitude] for longitude in eastward
    ]
    east = [
        [
            north_value - (north_value - south_value) * step / latitude_steps,
            east_value,
        ]
        for step in range(1, latitude_steps + 1)
    ]
    south = [
        [south_value, longitude]
        for longitude in reversed(eastward[:-1])
    ]
    west = [
        [
            south_value
            + (north_value - south_value) * step / latitude_steps,
            west_value,
        ]
        for step in range(1, latitude_steps)
    ]
    return north + east + south + west


def add_zone_display_analysis(data: dict[str, object]) -> None:
    """Add display boundaries and layout counts."""

    zones = data["zones"]
    assert isinstance(zones, list)
    for zone in zones:
        geometry = zone["geometry"]
        assert isinstance(geometry, dict)
        zone["display_boundary"] = sample_rectangle_boundary(geometry)
    data["counts"] = {"zones": len(zones)}


__all__ = [
    "add_zone_display_analysis",
    "sample_rectangle_boundary",
]
