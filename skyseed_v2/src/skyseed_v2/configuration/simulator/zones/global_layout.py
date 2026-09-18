"""Pure deterministic global Zone layout construction; no file round trip."""
from math import cos, radians
from skyseed_v2.configuration.simulator.zones.definition import LatitudeLongitudeRectangleDefinition, ZoneDefinition, ZoneLayoutDefinition

_ALIGNMENTS = ("antimeridian-centered", "prime-meridian-centered")

def _canonical_longitude(value: float) -> float:
    canonical = (value + 180.0) % 360.0 - 180.0
    return 0.0 if canonical == 0.0 else canonical


def build_global_zone_layout(
    *,
    latitude_bands: int = 24,
    equatorial_cells: int = 48,
    alignment: str = "antimeridian-centered",
) -> ZoneLayoutDefinition:
    """Construct a validated cosine-scaled whole-globe grid definition."""

    if isinstance(latitude_bands, bool) or not isinstance(latitude_bands, int):
        raise TypeError("latitude_bands must be an integer")
    if latitude_bands <= 0:
        raise ValueError("latitude_bands must be positive")
    if isinstance(equatorial_cells, bool) or not isinstance(
        equatorial_cells, int
    ):
        raise TypeError("equatorial_cells must be an integer")
    if equatorial_cells < 2:
        raise ValueError("equatorial_cells must be at least 2")
    if alignment not in _ALIGNMENTS:
        raise ValueError(f"alignment must be one of {_ALIGNMENTS!r}")

    latitude_height = 180.0 / latitude_bands
    alignment_center = -180.0 if alignment == "antimeridian-centered" else 0.0
    zones = []
    for band_index in range(latitude_bands):
        south = -90.0 + latitude_height * band_index
        north = -90.0 + latitude_height * (band_index + 1)
        midpoint = (south + north) / 2.0
        longitude_count = max(
            2,
            round(equatorial_cells * cos(radians(midpoint))),
        )
        boundaries = tuple(
            _canonical_longitude(
                alignment_center
                + 360.0 * (boundary_index + 0.5) / longitude_count
            )
            for boundary_index in range(longitude_count)
        )
        for cell_index in range(longitude_count):
            zones.append(
                ZoneDefinition(
                    zone_id=f"zone-b{band_index:02d}-c{cell_index:03d}",
                    priority=0,
                    geometry=LatitudeLongitudeRectangleDefinition(
                        south_latitude_deg=south,
                        north_latitude_deg=north,
                        west_longitude_deg=boundaries[cell_index - 1],
                        east_longitude_deg=boundaries[cell_index],
                    ),
                )
            )
    return ZoneLayoutDefinition(zones=tuple(zones), allow_overlap=False)


__all__ = ["build_global_zone_layout"]
