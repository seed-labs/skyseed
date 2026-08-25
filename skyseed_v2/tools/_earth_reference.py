"""Load the frozen Tool-private Natural Earth coastline display reference."""

from __future__ import annotations

from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path


_ASSET = Path(__file__).with_name("_assets") / "natural_earth_110m_coastline.json"
_ASSET_SHA256 = "f28998a2a1d144098245d9877be7188a35a950c37232443a068fe53cd7e2ca9f"


@lru_cache(maxsize=1)
def _natural_earth_coastline_lines(
) -> tuple[tuple[tuple[float, float], ...], ...]:
    """Return frozen latitude/longitude lines for Tool display only."""

    content = _ASSET.read_bytes()
    digest = sha256(content).hexdigest()
    if digest != _ASSET_SHA256:
        raise RuntimeError(
            "Natural Earth Tool display asset SHA-256 mismatch: "
            f"expected {_ASSET_SHA256}, got {digest}"
        )
    payload = json.loads(content)
    if not isinstance(payload, dict) or set(payload) != {
        "coordinate_order",
        "lines",
    }:
        raise RuntimeError("Natural Earth Tool display asset root is invalid")
    if payload["coordinate_order"] != "latitude_longitude_degrees":
        raise RuntimeError("Natural Earth Tool display coordinate order is invalid")
    lines = payload["lines"]
    if not isinstance(lines, list) or not lines:
        raise RuntimeError("Natural Earth Tool display lines are invalid")

    normalized = []
    for line in lines:
        if not isinstance(line, list) or len(line) < 2:
            raise RuntimeError("Natural Earth Tool display line is invalid")
        normalized_line = []
        for coordinate in line:
            if not isinstance(coordinate, list) or len(coordinate) != 2:
                raise RuntimeError("Natural Earth Tool display coordinate is invalid")
            latitude, longitude = coordinate
            if (
                isinstance(latitude, bool)
                or isinstance(longitude, bool)
                or not isinstance(latitude, (int, float))
                or not isinstance(longitude, (int, float))
                or not -90.0 <= latitude <= 90.0
                or not -180.0 <= longitude <= 180.0
            ):
                raise RuntimeError("Natural Earth Tool display coordinate is invalid")
            normalized_line.append((float(latitude), float(longitude)))
        normalized.append(tuple(normalized_line))
    return tuple(normalized)


__all__: list[str] = []
