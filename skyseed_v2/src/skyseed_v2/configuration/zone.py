"""Immutable Zone layout construction definitions."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from numbers import Real

from skyseed_v2.identifier import validate_identifier


def _finite_number(value: Real, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{field_name} must be a finite number")
    converted = float(value)
    if not isfinite(converted):
        raise ValueError(f"{field_name} must be a finite number")
    return converted


def _canonical_longitude(value: Real, field_name: str) -> float:
    longitude = _finite_number(value, field_name)
    if not -180.0 <= longitude <= 180.0:
        raise ValueError(f"{field_name} must be between -180 and 180")
    return -180.0 if longitude == 180.0 else longitude


@dataclass(frozen=True, slots=True)
class LatitudeLongitudeRectangleDefinition:
    """Describe one rectangular Zone geometry construction input.

    Layer: immutable configuration definition. Callers or the YAML loader create
    it and Topology materialization consumes it. It is not runtime geometry,
    dynamic position state or a public generic geometry abstraction.
    """

    south_latitude_deg: float
    north_latitude_deg: float
    west_longitude_deg: float
    east_longitude_deg: float

    def __post_init__(self) -> None:
        south = _finite_number(self.south_latitude_deg, "south_latitude_deg")
        north = _finite_number(self.north_latitude_deg, "north_latitude_deg")
        west = _canonical_longitude(
            self.west_longitude_deg, "west_longitude_deg"
        )
        east = _canonical_longitude(
            self.east_longitude_deg, "east_longitude_deg"
        )
        if not -90.0 <= south < north <= 90.0:
            raise ValueError(
                "latitude bounds must satisfy -90 <= south < north <= 90"
            )
        if west == east:
            raise ValueError("longitude bounds must have non-zero width")
        object.__setattr__(self, "south_latitude_deg", south)
        object.__setattr__(self, "north_latitude_deg", north)
        object.__setattr__(self, "west_longitude_deg", west)
        object.__setattr__(self, "east_longitude_deg", east)


@dataclass(frozen=True, slots=True, kw_only=True)
class ZoneDefinition:
    """Describe one immutable Zone construction input.

    Layer: immutable configuration definition. A ZoneLayoutDefinition owns this
    input until Topology materializes the domain Zone. It is not a runtime Zone,
    owner, service assignment or public mutable registry.
    """

    zone_id: str
    priority: int = 0
    geometry: LatitudeLongitudeRectangleDefinition

    def __post_init__(self) -> None:
        validate_identifier(self.zone_id, "zone_id")
        if isinstance(self.priority, bool) or not isinstance(self.priority, int):
            raise TypeError("priority must be an integer")
        if not isinstance(self.geometry, LatitudeLongitudeRectangleDefinition):
            raise TypeError(
                "geometry must be a LatitudeLongitudeRectangleDefinition"
            )


@dataclass(frozen=True, slots=True)
class ZoneLayoutDefinition:
    """Contain the complete deterministic Zone layout construction input.

    Layer: immutable configuration definition and materialization boundary.
    Programmatic callers or the public YAML loader create it; Topology consumes
    it. It is not the query-owning ZoneLayout or runtime mutable state.
    """

    zones: tuple[ZoneDefinition, ...]
    allow_overlap: bool

    def __post_init__(self) -> None:
        if not isinstance(self.allow_overlap, bool):
            raise TypeError("allow_overlap must be a boolean")
        zones = tuple(self.zones)
        if not zones:
            raise ValueError("zones must contain at least one ZoneDefinition")
        if any(not isinstance(zone, ZoneDefinition) for zone in zones):
            raise TypeError("zones must contain ZoneDefinition values")
        if len({zone.zone_id for zone in zones}) != len(zones):
            raise ValueError("zones contains a duplicate zone_id")
        zones = tuple(sorted(zones, key=lambda zone: zone.zone_id))
        if not self.allow_overlap:
            _validate_no_positive_area_overlap(zones)
        object.__setattr__(self, "zones", zones)


def _validate_no_positive_area_overlap(
    zones: tuple[ZoneDefinition, ...],
) -> None:
    for index, left in enumerate(zones):
        for right in zones[index + 1 :]:
            if _rectangles_have_positive_area_overlap(
                left.geometry, right.geometry
            ):
                raise ValueError(
                    "zones have positive-area overlap while allow_overlap is "
                    f"false: {left.zone_id!r} and {right.zone_id!r}"
                )


def _rectangles_have_positive_area_overlap(
    left: LatitudeLongitudeRectangleDefinition,
    right: LatitudeLongitudeRectangleDefinition,
) -> bool:
    latitude_overlap = min(
        left.north_latitude_deg, right.north_latitude_deg
    ) - max(left.south_latitude_deg, right.south_latitude_deg)
    if latitude_overlap <= 0.0:
        return False
    return any(
        min(left_east, right_east) - max(left_west, right_west) > 0.0
        for left_west, left_east in _longitude_intervals(left)
        for right_west, right_east in _longitude_intervals(right)
    )


def _longitude_intervals(
    rectangle: LatitudeLongitudeRectangleDefinition,
) -> tuple[tuple[float, float], ...]:
    west = rectangle.west_longitude_deg
    east = rectangle.east_longitude_deg
    if west < east:
        return ((west, east),)
    return ((west, 180.0), (-180.0, east))


__all__ = [
    "LatitudeLongitudeRectangleDefinition",
    "ZoneDefinition",
    "ZoneLayoutDefinition",
]
