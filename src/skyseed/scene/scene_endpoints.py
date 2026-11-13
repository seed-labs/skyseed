"""Stable access destinations and the built-in geographic partition."""
from copy import deepcopy
from dataclasses import dataclass, field
from math import cos, radians

from .scene_entities import _identifier, _number, _validate_data


@dataclass(frozen=True, slots=True)
class AccessEndpoint:
    endpoint_id: str
    kind: str
    data: dict = field(default_factory=dict)

    def __post_init__(self):
        _identifier(self.endpoint_id, "endpoint_id")
        _identifier(self.kind, "endpoint kind")
        self.validate()
        object.__setattr__(self, "data", deepcopy(self.data))

    def validate(self):
        _validate_data(self.data, f"endpoint {self.endpoint_id!r}.data")
        if self.kind == "geographic-zone/v1":
            for key, lower, upper in (("latitude_deg", -90, 90), ("longitude_deg", -180, 180)):
                _number(self.data[key], key, lower, upper)
            bounds = self.data.get("bounds")
            if bounds is not None:
                for key in ("south_deg", "north_deg"):
                    _number(bounds[key], key, -90, 90)
                for key in ("west_deg", "east_deg"):
                    _number(bounds[key], key, -180, 180)
                if bounds["south_deg"] >= bounds["north_deg"]:
                    raise ValueError("zone south_deg must be below north_deg")


def default_zones():
    """Fixed cosine-scaled 24-band partition, with antimeridian-centred cells.

    Geometry is Scene data. Coverage thresholds and assignment rules belong to
    service models. A west longitude greater than east crosses the antimeridian.
    """
    zones = []
    for band in range(24):
        south = -90 + band * 7.5
        north = south + 7.5
        latitude = (south + north) / 2
        count = max(2, round(48 * cos(radians(latitude))))
        width = 360 / count
        for cell in range(count):
            longitude = -180 + cell * width
            west = (longitude - width / 2 + 180) % 360 - 180
            east = (longitude + width / 2 + 180) % 360 - 180
            zones.append(AccessEndpoint(f"zone-b{band:02d}-c{cell:03d}", "geographic-zone/v1", {
                "latitude_deg": latitude, "longitude_deg": longitude,
                "bounds": {"south_deg": south, "north_deg": north,
                           "west_deg": west, "east_deg": east},
            }))
    return tuple(zones)
