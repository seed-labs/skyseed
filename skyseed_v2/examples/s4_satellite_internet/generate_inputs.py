"""Generate and verify the deterministic S4-local frozen inputs."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from math import cos, radians
from pathlib import Path
from typing import Any

from yaml import safe_load


EXAMPLE_ROOT = Path(__file__).resolve().parent
GROUND_STATIONS_FILE = EXAMPLE_ROOT / "ground_stations.yaml"
ZONE_LAYOUT_FILE = EXAMPLE_ROOT / "zones.yaml"

REVIEWED_GROUND_STATION_EVIDENCE_COUNT = 358
FROZEN_GROUND_STATION_COUNT = 355
REVIEWED_GROUND_STATION_EVIDENCE_SHA256 = (
    "c606d31ae7ec28f714886fdc8c534f52dac69c494f682ee38756fc4abeb5df1e"
)
FROZEN_GROUND_STATIONS_SHA256 = (
    "1b65f9bec40b65324454659100713b74e9108e63a0f4c162f877dc3d353cec2e"
)
LATITUDE_BAND_HEIGHT_DEG = 7.5
LONGITUDE_COUNTS = (
    3,
    9,
    15,
    21,
    27,
    32,
    36,
    40,
    43,
    45,
    47,
    48,
    48,
    47,
    45,
    43,
    40,
    36,
    32,
    27,
    21,
    15,
    9,
    3,
)


def _quoted(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _number(value: float) -> str:
    if value == 0.0:
        value = 0.0
    result = f"{value:.12f}".rstrip("0").rstrip(".")
    return f"{result}.0" if "." not in result else result


def _canonical_longitude(value: float) -> float:
    canonical = (value + 180.0) % 360.0 - 180.0
    return 0.0 if canonical == 0.0 else canonical


def _longitude_number(value: float) -> str:
    """Preserve the exact canonical float used by formal Zone resolution."""

    result = repr(_canonical_longitude(value))
    return f"{result}.0" if "." not in result else result


def _load_reviewed_ground_station_sites(source: Path) -> list[dict[str, Any]]:
    content = Path(source).read_bytes()
    digest = sha256(content).hexdigest()
    if digest != REVIEWED_GROUND_STATION_EVIDENCE_SHA256:
        raise ValueError(
            "reviewed Ground Station evidence SHA-256 mismatch: "
            f"expected {REVIEWED_GROUND_STATION_EVIDENCE_SHA256}, got {digest}"
        )
    payload = json.loads(content)
    if not isinstance(payload, dict) or payload.get("record_type") != (
        "skyseed-external-ground-station-evidence"
    ):
        raise ValueError("reviewed Ground Station evidence root is unexpected")
    if payload.get("record_version") != 1:
        raise ValueError("reviewed Ground Station evidence version is unexpected")
    records = payload.get("sites")
    if not isinstance(records, list) or len(records) != (
        REVIEWED_GROUND_STATION_EVIDENCE_COUNT
    ):
        raise ValueError(
            "reviewed Ground Station evidence must contain 358 site records"
        )
    selected = [
        record
        for record in records
        if isinstance(record, dict)
        and record.get("included_in_proposed_benchmark") is True
    ]
    if len(selected) != FROZEN_GROUND_STATION_COUNT:
        raise ValueError("reviewed Ground Station selection must contain 355 sites")
    if payload.get("proposed_benchmark", {}).get("site_count") != len(selected):
        raise ValueError("reviewed Ground Station selection count is inconsistent")
    ids = [record.get("research_id") for record in selected]
    if any(not isinstance(value, str) or not value for value in ids):
        raise ValueError("reviewed Ground Station IDs must be non-empty strings")
    if len(set(ids)) != len(ids):
        raise ValueError("reviewed Ground Station selection contains duplicate IDs")
    for record in selected:
        names = record.get("names")
        latitude = record.get("latitude_deg")
        longitude = record.get("longitude_deg")
        if (
            not isinstance(names, list)
            or not names
            or any(not isinstance(name, str) or not name for name in names)
        ):
            raise ValueError("reviewed Ground Station names are unexpected")
        if not isinstance(latitude, (int, float)) or not -90.0 <= latitude <= 90.0:
            raise ValueError("reviewed Ground Station latitude is unexpected")
        if not isinstance(longitude, (int, float)) or not -180.0 <= longitude <= 180.0:
            raise ValueError("reviewed Ground Station longitude is unexpected")
        if record.get("coordinate_evidence_class") not in {"A", "B", "C", "D"}:
            raise ValueError("reviewed Ground Station evidence class is unexpected")
        if not {"Ka", "E"}.intersection(record.get("bands", ())):
            raise ValueError("reviewed Ground Station has no supported gateway band")
        if record.get("operational_status") not in {
            "current",
            "historical",
            "planned",
        }:
            raise ValueError("reviewed Ground Station status is unexpected")
    return sorted(selected, key=lambda record: record["research_id"])


def _ground_station_display_name(record: dict[str, Any]) -> str:
    names = record["names"]
    name = max(names, key=lambda value: ("," in value, len(value), value))
    if "," not in name:
        name = f"{name}, {record['country_or_territory']}"
    return f"Starlink Gateway - {name}"


def _render_ground_stations(source: Path) -> str:
    records = _load_reviewed_ground_station_sites(source)
    lines = [
        "# Generated by examples/s4_satellite_internet/generate_inputs.py.",
        "# Frozen from the reviewed 2026-08-25 research evidence selection.",
        f"# Research source SHA-256: {REVIEWED_GROUND_STATION_EVIDENCE_SHA256}",
        "# Includes supported current, planned, and historical Ka/E gateway sites.",
        "# This is qualification geometry, not a simultaneous operational snapshot.",
        "schema_version: skyseed-ground-stations/v1",
        "ground_stations:",
    ]
    for record in records:
        lines.extend(
            (
                f"  - ground_station_id: {record['research_id']}",
                f"    display_name: {_quoted(_ground_station_display_name(record))}",
                f"    latitude_deg: {_number(float(record['latitude_deg']))}",
                f"    longitude_deg: {_number(float(record['longitude_deg']))}",
                "    elevation_m: 0.0",
                "    operator: null",
                "    station_type: gateway",
            )
        )
    return "\n".join(lines) + "\n"


def _generated_longitude_counts() -> tuple[int, ...]:
    return tuple(
        max(
            1,
            round(
                48
                * cos(
                    radians(
                        -90.0
                        + LATITUDE_BAND_HEIGHT_DEG * band_index
                        + LATITUDE_BAND_HEIGHT_DEG / 2.0
                    )
                )
            ),
        )
        for band_index in range(24)
    )


def _render_zones() -> str:
    counts = _generated_longitude_counts()
    if counts != LONGITUDE_COUNTS:
        raise AssertionError(f"unexpected longitude counts: {counts!r}")
    if sum(counts) != 732:
        raise AssertionError("global Zone layout must contain exactly 732 Zones")

    lines = [
        "# Generated by examples/s4_satellite_internet/generate_inputs.py.",
        "# 24 independent 7.5-degree latitude bands; longitude widths follow",
        "# max(1, round(48 * cos(radians(latitude-band midpoint)))).",
        "# Cell zero is centered on the antimeridian and formally wraps it.",
        "allow_overlap: false",
        "zones:",
    ]
    for band_index, longitude_count in enumerate(counts):
        south = -90.0 + LATITUDE_BAND_HEIGHT_DEG * band_index
        north = south + LATITUDE_BAND_HEIGHT_DEG
        boundaries = tuple(
            _canonical_longitude(
                -180.0
                + 360.0 * (boundary_index + 0.5) / longitude_count
            )
            for boundary_index in range(longitude_count)
        )
        for cell_index in range(longitude_count):
            west = boundaries[cell_index - 1]
            east = boundaries[cell_index]
            lines.extend(
                (
                    f"  - zone_id: zone-b{band_index:02d}-c{cell_index:03d}",
                    "    geometry:",
                    "      kind: latitude-longitude-rectangle",
                    f"      south_latitude_deg: {_number(south)}",
                    f"      north_latitude_deg: {_number(north)}",
                    f"      west_longitude_deg: {_longitude_number(west)}",
                    f"      east_longitude_deg: {_longitude_number(east)}",
                )
            )
    return "\n".join(lines) + "\n"


def _validate_frozen_ground_stations() -> None:
    content = GROUND_STATIONS_FILE.read_bytes()
    digest = sha256(content).hexdigest()
    if digest != FROZEN_GROUND_STATIONS_SHA256:
        raise ValueError(
            "frozen Ground Station input differs from its reviewed SHA-256: "
            f"expected {FROZEN_GROUND_STATIONS_SHA256}, got {digest}"
        )
    payload = safe_load(content)
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "ground_stations",
    }:
        raise ValueError("frozen Ground Station YAML root is unexpected")
    if payload["schema_version"] != "skyseed-ground-stations/v1":
        raise ValueError("frozen Ground Station schema version is unexpected")
    records = payload["ground_stations"]
    if not isinstance(records, list) or len(records) != (
        FROZEN_GROUND_STATION_COUNT
    ):
        raise ValueError("frozen Ground Station YAML must contain 355 records")
    allowed = {
        "ground_station_id",
        "display_name",
        "latitude_deg",
        "longitude_deg",
        "elevation_m",
        "operator",
        "station_type",
    }
    if any(not isinstance(record, dict) or set(record) != allowed for record in records):
        raise ValueError("frozen Ground Station YAML contains unexpected fields")
    if any(record["operator"] is not None for record in records):
        raise ValueError("frozen S4 Ground Station operators must be explicit nulls")


def _check() -> None:
    expected_zones = _render_zones()
    if ZONE_LAYOUT_FILE.read_text(encoding="utf-8") != expected_zones:
        raise ValueError("zones.yaml is stale; regenerate the S4 inputs")
    _validate_frozen_ground_stations()


def _write(source: Path) -> None:
    GROUND_STATIONS_FILE.write_text(
        _render_ground_stations(source),
        encoding="utf-8",
    )
    ZONE_LAYOUT_FILE.write_text(_render_zones(), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--write", action="store_true")
    action.add_argument("--check", action="store_true")
    parser.add_argument("--research-ground-stations", type=Path)
    arguments = parser.parse_args()

    if arguments.write:
        if arguments.research_ground_stations is None:
            parser.error("--write requires --research-ground-stations")
        _write(arguments.research_ground_stations)
        return 0
    if arguments.research_ground_stations is not None:
        parser.error("--research-ground-stations is valid only with --write")
    _check()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
