"""Add non-authoritative Zone display geometry, focus, and summaries."""

from __future__ import annotations

from .probe_read_plan import (
    RectangleDisplaySnapshot,
    canonical_longitude,
)


def sample_rectangle_boundary(
    geometry: RectangleDisplaySnapshot,
) -> list[list[float]]:
    """Sample rectangle bounds for display, including wrapping."""

    if not isinstance(geometry, RectangleDisplaySnapshot):
        raise TypeError("geometry must be a RectangleDisplaySnapshot")
    longitude_steps = 32
    latitude_steps = 16
    span = (
        geometry.east_longitude_deg - geometry.west_longitude_deg
    ) % 360.0
    eastward = [
        canonical_longitude(
            geometry.west_longitude_deg + span * step / longitude_steps
        )
        for step in range(longitude_steps + 1)
    ]
    north = [
        [geometry.north_latitude_deg, longitude] for longitude in eastward
    ]
    east = [
        [
            geometry.north_latitude_deg
            - (
                geometry.north_latitude_deg
                - geometry.south_latitude_deg
            )
            * step
            / latitude_steps,
            geometry.east_longitude_deg,
        ]
        for step in range(1, latitude_steps + 1)
    ]
    south = [
        [geometry.south_latitude_deg, longitude]
        for longitude in reversed(eastward[:-1])
    ]
    west = [
        [
            geometry.south_latitude_deg
            + (
                geometry.north_latitude_deg
                - geometry.south_latitude_deg
            )
            * step
            / latitude_steps,
            geometry.west_longitude_deg,
        ]
        for step in range(1, latitude_steps)
    ]
    return north + east + south + west


def add_zone_display_analysis(data: dict[str, object]) -> None:
    """Add display boundaries, focus visibility, and display counts."""

    zones = data["zones"]
    probes = data["probes"]
    assert isinstance(zones, list)
    assert isinstance(probes, list)
    for zone in zones:
        geometry = zone["geometry"]
        assert isinstance(geometry, dict)
        display_geometry = RectangleDisplaySnapshot(
            south_latitude_deg=float(geometry["south_latitude_deg"]),
            north_latitude_deg=float(geometry["north_latitude_deg"]),
            west_longitude_deg=float(geometry["west_longitude_deg"]),
            east_longitude_deg=float(geometry["east_longitude_deg"]),
        )
        zone["display_boundary"] = sample_rectangle_boundary(
            display_geometry
        )
    _annotate_focus_visibility(probes)
    counts = _zone_counts(zones, probes)
    data["counts"] = counts
    data["status"] = "PASS" if counts["failed"] == 0 else "FAIL"


def _annotate_focus_visibility(
    probes: list[dict[str, object]],
) -> None:
    ordinary_by_zone: dict[str, list[dict[str, object]]] = {}
    for probe in probes:
        reasons: list[str] = []
        categories = tuple(str(value) for value in probe["categories"])
        origins = probe["origins"]
        assert isinstance(origins, list)
        physical_matches = probe["physical_matches"]
        assert isinstance(physical_matches, list)
        if probe["invariant_status"] == "FAIL":
            reasons.append("FAIL")
        if len(physical_matches) > 1:
            reasons.append("multi-match")
        if any(
            origin["origin_kind"] == "tool-selected relationship candidate"
            for origin in origins
        ):
            reasons.append("zone-pair-relation")
        if any("pole" in category for category in categories):
            reasons.append("pole")
        if any("dateline" in category for category in categories):
            reasons.append("dateline")
        if "representative-center" in categories:
            reasons.append("representative-center")
        probe["focus_reasons"] = reasons
        probe["focus_visible"] = bool(reasons)

        source_zone_ids = probe["source_zone_ids"]
        assert isinstance(source_zone_ids, list)
        if (
            not reasons
            and probe["invariant_status"] == "PASS"
            and len(source_zone_ids) == 1
        ):
            ordinary_by_zone.setdefault(str(source_zone_ids[0]), []).append(
                probe
            )

    def ordinary_key(probe: dict[str, object]) -> tuple[object, ...]:
        categories = tuple(str(value) for value in probe["categories"])
        category_rank = (
            0
            if "interior-grid" in categories
            else 1
            if any("edge-midpoint" in value for value in categories)
            else 2
        )
        return (
            category_rank,
            categories,
            float(probe["latitude_deg"]),
            float(probe["longitude_deg"]),
        )

    for zone_id in sorted(ordinary_by_zone):
        for probe in sorted(
            ordinary_by_zone[zone_id],
            key=ordinary_key,
        )[:2]:
            probe["focus_visible"] = True
            reasons = probe["focus_reasons"]
            assert isinstance(reasons, list)
            reasons.append("deterministic-zone-sample")


def _zone_counts(
    zones: list[dict[str, object]],
    probes: list[dict[str, object]],
) -> dict[str, int]:
    def has_category(probe: dict[str, object], fragment: str) -> bool:
        categories = probe["categories"]
        assert isinstance(categories, list)
        return any(fragment in str(category) for category in categories)

    return {
        "zones": len(zones),
        "probes": len(probes),
        "focus_visible": sum(
            bool(probe["focus_visible"]) for probe in probes
        ),
        "passed": sum(
            probe["invariant_status"] == "PASS" for probe in probes
        ),
        "failed": sum(
            probe["invariant_status"] == "FAIL" for probe in probes
        ),
        "multi_match": sum(
            len(probe["physical_matches"]) > 1 for probe in probes
        ),
        "pole": sum(has_category(probe, "pole") for probe in probes),
        "dateline": sum(
            has_category(probe, "dateline") for probe in probes
        ),
        "relationship_candidates": sum(
            any(
                origin["origin_kind"]
                == "tool-selected relationship candidate"
                for origin in probe["origins"]
            )
            for probe in probes
        ),
        "overlap_candidates": sum(
            has_category(probe, "overlap") for probe in probes
        ),
    }


__all__ = [
    "add_zone_display_analysis",
    "sample_rectangle_boundary",
]
