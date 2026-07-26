"""Build the Tool-owned deterministic coordinate plan for formal Zone reads."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Iterable


_TOLERANCE = 1e-10


@dataclass(frozen=True, slots=True)
class RectangleDisplaySnapshot:
    """Tool-owned rectangle scalars used only for display sampling."""

    south_latitude_deg: float
    north_latitude_deg: float
    west_longitude_deg: float
    east_longitude_deg: float

    @property
    def representative_center(self) -> tuple[float, float]:
        latitude = (
            self.south_latitude_deg + self.north_latitude_deg
        ) / 2.0
        span = (
            self.east_longitude_deg - self.west_longitude_deg
        ) % 360.0
        return (
            latitude,
            canonical_longitude(self.west_longitude_deg + span / 2.0),
        )


@dataclass(frozen=True, slots=True)
class ZoneDisplaySnapshot:
    """Tool-owned Zone identity and rectangle used by display analysis."""

    zone_id: str
    geometry: RectangleDisplaySnapshot


def canonical_longitude(longitude_deg: float) -> float:
    canonical = (longitude_deg + 180.0) % 360.0 - 180.0
    return 0.0 if abs(canonical) < _TOLERANCE else canonical


def _longitude_at(
    geometry: RectangleDisplaySnapshot,
    fraction: float,
) -> float:
    span = (
        geometry.east_longitude_deg - geometry.west_longitude_deg
    ) % 360.0
    return canonical_longitude(geometry.west_longitude_deg + span * fraction)


def _latitude_at(
    geometry: RectangleDisplaySnapshot,
    fraction: float,
) -> float:
    return geometry.south_latitude_deg + (
        geometry.north_latitude_deg - geometry.south_latitude_deg
    ) * fraction


def _longitude_segments(
    geometry: RectangleDisplaySnapshot,
) -> tuple[tuple[float, float], ...]:
    west = geometry.west_longitude_deg
    east = geometry.east_longitude_deg
    if west < east:
        return ((west, east),)
    return ((west, 180.0), (-180.0, east))


def select_zone_probes(
    zones: Iterable[ZoneDisplaySnapshot],
) -> list[dict[str, object]]:
    """Return deterministic coordinates with all deduplicated probe origins."""

    zone_values = tuple(zones)
    if any(not isinstance(zone, ZoneDisplaySnapshot) for zone in zone_values):
        raise TypeError("zones must contain ZoneDisplaySnapshot values")
    zone_values = tuple(
        sorted(zone_values, key=lambda value: value.zone_id)
    )
    probes_by_coordinate: dict[tuple[float, float], dict[str, object]] = {}

    def add(
        latitude_deg: float,
        longitude_deg: float,
        *,
        category: str,
        source_zones: tuple[ZoneDisplaySnapshot, ...],
        origin_kind: str,
        note: str,
    ) -> None:
        latitude = float(latitude_deg)
        longitude = (
            0.0
            if abs(abs(latitude) - 90.0) < _TOLERANCE
            else canonical_longitude(float(longitude_deg))
        )
        key = (round(latitude, 10), round(longitude, 10))
        probe = probes_by_coordinate.setdefault(
            key,
            {
                "latitude_deg": latitude,
                "longitude_deg": longitude,
                "origins": [],
            },
        )
        origins = probe["origins"]
        assert isinstance(origins, list)
        origin = {
            "category": category,
            "origin_kind": origin_kind,
            "source_zone_ids": [zone.zone_id for zone in source_zones],
            "note": note,
        }
        if origin not in origins:
            origins.append(origin)

    for zone in zone_values:
        geometry = zone.geometry
        center_latitude, center_longitude = geometry.representative_center
        add(
            center_latitude,
            center_longitude,
            category="representative-center",
            source_zones=(zone,),
            origin_kind="zone",
            note="Zone representative_center",
        )
        for latitude_fraction in (0.25, 0.5, 0.75):
            for longitude_fraction in (0.25, 0.5, 0.75):
                if (
                    latitude_fraction == 0.5
                    and longitude_fraction == 0.5
                ):
                    continue
                add(
                    _latitude_at(geometry, latitude_fraction),
                    _longitude_at(geometry, longitude_fraction),
                    category="interior-grid",
                    source_zones=(zone,),
                    origin_kind="zone",
                    note=(
                        "deterministic 3x3 interior grid "
                        f"({latitude_fraction:g}, {longitude_fraction:g})"
                    ),
                )

        edge_coordinates = (
            (
                geometry.north_latitude_deg,
                center_longitude,
                "north-edge-midpoint",
            ),
            (
                geometry.south_latitude_deg,
                center_longitude,
                "south-edge-midpoint",
            ),
            (
                center_latitude,
                geometry.west_longitude_deg,
                "west-edge-midpoint",
            ),
            (
                center_latitude,
                geometry.east_longitude_deg,
                "east-edge-midpoint",
            ),
        )
        for latitude, longitude, category in edge_coordinates:
            add(
                latitude,
                longitude,
                category=category,
                source_zones=(zone,),
                origin_kind="zone",
                note="closed rectangle boundary",
            )
        for latitude, latitude_label in (
            (geometry.north_latitude_deg, "north"),
            (geometry.south_latitude_deg, "south"),
        ):
            for longitude, longitude_label in (
                (geometry.west_longitude_deg, "west"),
                (geometry.east_longitude_deg, "east"),
            ):
                add(
                    latitude,
                    longitude,
                    category=f"{latitude_label}-{longitude_label}-corner",
                    source_zones=(zone,),
                    origin_kind="zone",
                    note="closed rectangle corner",
                )

        if geometry.north_latitude_deg == 90.0:
            add(
                90.0,
                0.0,
                category="north-pole",
                source_zones=(zone,),
                origin_kind="zone",
                note="Zone includes the North Pole",
            )
        if geometry.south_latitude_deg == -90.0:
            add(
                -90.0,
                0.0,
                category="south-pole",
                source_zones=(zone,),
                origin_kind="zone",
                note="Zone includes the South Pole",
            )
        if geometry.west_longitude_deg > geometry.east_longitude_deg:
            span = (
                geometry.east_longitude_deg
                - geometry.west_longitude_deg
            ) % 360.0
            epsilon = min(1.0, max(span / 20.0, 1e-6))
            add(
                center_latitude,
                180.0 - epsilon,
                category="wrapped-dateline-side",
                source_zones=(zone,),
                origin_kind="zone",
                note="wrapped Zone interior on the +180 side",
            )
            add(
                center_latitude,
                -180.0 + epsilon,
                category="wrapped-dateline-side",
                source_zones=(zone,),
                origin_kind="zone",
                note="wrapped Zone interior on the -180 side",
            )

    for first, second in combinations(zone_values, 2):
        first_geometry = first.geometry
        second_geometry = second.geometry
        source_pair = (first, second)
        south = max(
            first_geometry.south_latitude_deg,
            second_geometry.south_latitude_deg,
        )
        north = min(
            first_geometry.north_latitude_deg,
            second_geometry.north_latitude_deg,
        )
        if south > north + _TOLERANCE:
            continue

        intersections: list[tuple[float, float]] = []
        for first_segment in _longitude_segments(first_geometry):
            for second_segment in _longitude_segments(second_geometry):
                west = max(first_segment[0], second_segment[0])
                east = min(first_segment[1], second_segment[1])
                if west <= east + _TOLERANCE:
                    segment = (west, east)
                    if segment not in intersections:
                        intersections.append(segment)

        for west, east in intersections:
            latitude_width = north - south
            longitude_width = east - west
            if (
                latitude_width > _TOLERANCE
                and longitude_width > _TOLERANCE
            ):
                add(
                    (south + north) / 2.0,
                    (west + east) / 2.0,
                    category="positive-overlap-representative",
                    source_zones=source_pair,
                    origin_kind="tool-selected relationship candidate",
                    note="positive-area rectangle intersection candidate",
                )
            elif (
                latitude_width > _TOLERANCE
                or longitude_width > _TOLERANCE
            ):
                add(
                    (south + north) / 2.0,
                    (west + east) / 2.0,
                    category="shared-edge-midpoint",
                    source_zones=source_pair,
                    origin_kind="tool-selected relationship candidate",
                    note="shared rectangle edge candidate",
                )
            else:
                category = (
                    "shared-pole"
                    if abs(abs(south) - 90.0) < _TOLERANCE
                    else "shared-corner"
                )
                add(
                    south,
                    west,
                    category=category,
                    source_zones=source_pair,
                    origin_kind="tool-selected relationship candidate",
                    note=f"{category} candidate",
                )

        for pole in (-90.0, 90.0):
            if (
                first_geometry.south_latitude_deg
                <= pole
                <= first_geometry.north_latitude_deg
                and second_geometry.south_latitude_deg
                <= pole
                <= second_geometry.north_latitude_deg
            ):
                add(
                    pole,
                    0.0,
                    category="shared-pole",
                    source_zones=source_pair,
                    origin_kind="tool-selected relationship candidate",
                    note="both closed rectangles include the pole",
                )

        if intersections:
            center_latitude = (south + north) / 2.0
            dateline_segments = [
                segment
                for segment in intersections
                if abs(segment[0] + 180.0) < _TOLERANCE
                or abs(segment[1] - 180.0) < _TOLERANCE
            ]
            if dateline_segments:
                add(
                    center_latitude,
                    -180.0,
                    category="dateline-relation",
                    source_zones=source_pair,
                    origin_kind="tool-selected relationship candidate",
                    note="rectangle relation touches or crosses the dateline",
                )

            wrapped = (
                first_geometry.west_longitude_deg
                > first_geometry.east_longitude_deg
                or second_geometry.west_longitude_deg
                > second_geometry.east_longitude_deg
            )
            has_east_side = any(
                east == 180.0 and east - west > _TOLERANCE
                for west, east in intersections
            )
            has_west_side = any(
                west == -180.0 and east - west > _TOLERANCE
                for west, east in intersections
            )
            if (
                wrapped
                and north - south > _TOLERANCE
                and has_east_side
                and has_west_side
            ):
                east_width = max(
                    180.0 - west
                    for west, east in intersections
                    if east == 180.0
                )
                west_width = max(
                    east + 180.0
                    for west, east in intersections
                    if west == -180.0
                )
                epsilon = min(1.0, east_width / 2.0, west_width / 2.0)
                for longitude in (180.0 - epsilon, -180.0 + epsilon):
                    add(
                        center_latitude,
                        longitude,
                        category="wrapped-positive-overlap-side",
                        source_zones=source_pair,
                        origin_kind="tool-selected relationship candidate",
                        note=(
                            "wrapped positive-overlap candidate on one "
                            "dateline side"
                        ),
                    )

    return sorted(
        probes_by_coordinate.values(),
        key=lambda value: (
            float(value["latitude_deg"]),
            float(value["longitude_deg"]),
        ),
    )


__all__ = [
    "RectangleDisplaySnapshot",
    "ZoneDisplaySnapshot",
    "canonical_longitude",
    "select_zone_probes",
]
