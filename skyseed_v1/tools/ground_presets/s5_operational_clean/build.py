#!/usr/bin/env python3
"""Build the two-hour coverage-qualified S5 ground preset."""

from __future__ import annotations

import json
import math
from pathlib import Path
import tempfile
from typing import Any, Iterable

from skyseed_v1.visualization.export_ground_links import build_ground_links_computation


PROJECT_ROOT = Path(__file__).resolve().parents[3]
GROUND_ROOT = PROJECT_ROOT / "data" / "ground"
ORBIT_PATH = PROJECT_ROOT / "data" / "orbits" / "planned_shell_orbit_data_s1_s5.json"
SOURCE_BINDINGS = GROUND_ROOT / "s4_visualization_gateway_binding_12.json"
SOURCE_UTS = GROUND_ROOT / "s4_gz_rich_validation_ut_catalog_32.json"
SOURCE_ZONES = GROUND_ROOT / "s4_gz_rich_validation_zone_catalog_16.json"

OUTPUT_BINDINGS = GROUND_ROOT / "s5_operational_clean_gateway_binding_5.json"
OUTPUT_UTS = GROUND_ROOT / "s5_operational_clean_ut_catalog_9.json"
OUTPUT_POLICIES = GROUND_ROOT / "s5_operational_clean_ut_gateway_policy_9.json"
OUTPUT_ZONES = GROUND_ROOT / "s5_operational_clean_zone_catalog_6.json"
OUTPUT_REPORT = GROUND_ROOT / "s5_operational_clean_2h_coverage_report.json"

START_TIME = "2026-07-09T00:00:00Z"
INTERVAL_SECONDS = 10
DURATION_SECONDS = 7200
FRAME_COUNT = DURATION_SECONDS // INTERVAL_SECONDS
EXPECTED_SATELLITES = 172


def _read(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError(f"expected JSON list: {path}")
    return [dict(item) for item in value]


def _write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _station(entry: dict[str, Any], identifier: str) -> dict[str, Any]:
    return {
        "groundStationId": str(entry[identifier]),
        "latitude_deg": float(entry["latitude_deg"]),
        "longitude_deg": float(entry["longitude_deg"]),
        "altitude_km": float(entry.get("altitude_km", 0.0)),
    }


def _maximum_gap_frames(values: Iterable[bool]) -> int:
    maximum = current = 0
    for visible in values:
        current = 0 if visible else current + 1
        maximum = max(maximum, current)
    return maximum


def _coverage(
    *,
    candidates: list[dict[str, Any]],
    identifier: str,
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    station_rows = [_station(entry, identifier) for entry in candidates]
    with tempfile.TemporaryDirectory(prefix="skyseed-s5-ground-") as tmp:
        station_path = Path(tmp) / "stations.json"
        _write(station_path, station_rows)
        computation = build_ground_links_computation(
            planned_shell="S5",
            planned_orbit_data=ORBIT_PATH,
            ground_station_catalog=station_path,
            start_timestamp=START_TIME,
            interval=f"{INTERVAL_SECONDS}s",
            steps=None,
            duration=f"{DURATION_SECONDS}s",
            max_satellites=EXPECTED_SATELLITES,
            satellite_id_format="node-id",
        )
        ids = [str(entry[identifier]) for entry in candidates]
        frames_by_id: dict[str, list[bool]] = {item: [] for item in ids}
        observed_frames = 0
        for links in computation.samples:
            observed_frames += 1
            visible = {str(link["groundStationId"]) for link in links}
            for item in ids:
                frames_by_id[item].append(item in visible)
    if observed_frames != FRAME_COUNT:
        raise ValueError(
            f"S5 coverage frame count mismatch: {observed_frames} != {FRAME_COUNT}"
        )
    report = {
        item: {
            "visible_frame_count": sum(frames_by_id[item]),
            "frame_count": observed_frames,
            "coverage_ratio": round(sum(frames_by_id[item]) / observed_frames, 9),
            "maximum_unserviceable_gap_seconds": (
                _maximum_gap_frames(frames_by_id[item]) * INTERVAL_SECONDS
            ),
            "continuously_serviceable": all(frames_by_id[item]),
        }
        for item in ids
    }
    selected = sorted(item for item in ids if report[item]["continuously_serviceable"])
    return selected, report


def _distance_km(a: dict[str, Any], b: dict[str, Any]) -> float:
    lat_a = math.radians(float(a["latitude_deg"]))
    lon_a = math.radians(float(a["longitude_deg"]))
    lat_b = math.radians(float(b["latitude_deg"]))
    lon_b = math.radians(float(b["longitude_deg"]))
    central = math.acos(
        max(
            -1.0,
            min(
                1.0,
                math.sin(lat_a) * math.sin(lat_b)
                + math.cos(lat_a) * math.cos(lat_b) * math.cos(lon_a - lon_b),
            ),
        )
    )
    return 6371.0 * central


def _ordered_gateways(point: dict[str, Any], bindings: list[dict[str, Any]]) -> list[str]:
    return [
        str(entry["gateway_id"])
        for entry in sorted(
            bindings,
            key=lambda entry: (_distance_km(point, entry), str(entry["gateway_id"])),
        )
    ]


def build() -> dict[str, Any]:
    source_bindings = _read(SOURCE_BINDINGS)
    source_uts = _read(SOURCE_UTS)
    source_zones = _read(SOURCE_ZONES)
    gateway_ids, gateway_coverage = _coverage(
        candidates=source_bindings,
        identifier="gateway_id",
    )
    ut_ids, ut_coverage = _coverage(candidates=source_uts, identifier="ut_id")
    if len(gateway_ids) != 5 or len(ut_ids) != 9:
        raise ValueError(
            "S5 frozen coverage selection changed; review orbit/link semantics before "
            f"accepting gateways={gateway_ids}, uts={ut_ids}"
        )

    bindings = [
        {
            **entry,
            "coverage_source": "s5_planned_shell_two_hour_10s_all_frames",
            "policy_reason": (
                str(entry.get("policy_reason") or "")
                + "; retained by S5 720/720-frame serviceability gate"
            ),
        }
        for entry in source_bindings
        if str(entry["gateway_id"]) in set(gateway_ids)
    ]
    uts = [
        {
            **entry,
            "type": "synthetic_s5_operational_clean_ut",
            "source": "skyseed_s5_two_hour_coverage_selection",
        }
        for entry in source_uts
        if str(entry["ut_id"]) in set(ut_ids)
    ]
    selected_ut_ids_by_zone: dict[str, list[str]] = {}
    for entry in uts:
        selected_ut_ids_by_zone.setdefault(str(entry["zone_id"]), []).append(
            str(entry["ut_id"])
        )

    zones: list[dict[str, Any]] = []
    for source_zone in source_zones:
        zone_id = str(source_zone["zone_id"])
        selected_zone_uts = sorted(selected_ut_ids_by_zone.get(zone_id, []))
        if not selected_zone_uts:
            continue
        ordered = _ordered_gateways(source_zone, bindings)
        zones.append(
            {
                **source_zone,
                "display_name": str(source_zone.get("display_name") or zone_id).replace(
                    "G/Z Rich", "S5 Operational Clean"
                ),
                "partition_policy": "s5_two_hour_coverage_qualified_zone",
                "source": "skyseed_s5_two_hour_coverage_selection",
                "service_domain_source": "s5_two_hour_coverage_qualified_zone_catalog",
                "coverage_analysis_duration_seconds": DURATION_SECONDS,
                "ut_ids": selected_zone_uts,
                "synthetic_ut_count": len(selected_zone_uts),
                "coordinate_gateway_ids": ordered[:2],
                "default_gateway": ordered[0],
                "secondary_gateway": ordered[1],
                "preferred_gateway_ids": ordered[:2],
            }
        )

    zones_by_id = {str(entry["zone_id"]): entry for entry in zones}
    bindings_by_id = {str(entry["gateway_id"]): entry for entry in bindings}
    policies: list[dict[str, Any]] = []
    finalized_uts: list[dict[str, Any]] = []
    for ut in uts:
        zone = zones_by_id[str(ut["zone_id"])]
        default_id = str(zone["default_gateway"])
        special_id = next(
            item
            for item in _ordered_gateways(ut, bindings)
            if item != default_id
        )
        default = bindings_by_id[default_id]
        special = bindings_by_id[special_id]
        finalized_uts.append({**ut, "gateway_affinity": default_id})
        policies.append(
            {
                "ut_id": str(ut["ut_id"]),
                "zone_id": str(ut["zone_id"]),
                "selection_model": "longest_prefix_match",
                "immutable_for_run": True,
                "policy_source": "s5_two_hour_coverage_qualified_default_plus_nearest",
                "rules": [
                    {
                        "prefix": "0.0.0.0/0",
                        "gateway_id": default_id,
                        "gateway_selector_label": int(default["gateway_selector_label"]),
                        "rule_role": "default",
                    },
                    {
                        "prefix": str(special["gateway_service_ipv4"]),
                        "gateway_id": special_id,
                        "gateway_selector_label": int(special["gateway_selector_label"]),
                        "rule_role": "special",
                    },
                ],
            }
        )

    report = {
        "schema_version": "skyseed-s5-ground-coverage-selection-v1",
        "planned_shell": "S5",
        "start_time_utc": START_TIME,
        "interval_seconds": INTERVAL_SECONDS,
        "duration_seconds": DURATION_SECONDS,
        "frame_count": FRAME_COUNT,
        "satellite_count": EXPECTED_SATELLITES,
        "selection_rule": (
            "visible in every one of 720 frozen link frames; this qualifies only "
            "the declared two-hour analysis window and is not a periodicity claim"
        ),
        "selected_gateway_ids": gateway_ids,
        "selected_ut_ids": ut_ids,
        "selected_zone_ids": sorted(zones_by_id),
        "gateway_coverage": gateway_coverage,
        "ut_coverage": ut_coverage,
    }
    _write(OUTPUT_BINDINGS, bindings)
    _write(OUTPUT_UTS, finalized_uts)
    _write(OUTPUT_POLICIES, policies)
    _write(OUTPUT_ZONES, zones)
    _write(OUTPUT_REPORT, report)
    return report


if __name__ == "__main__":
    result = build()
    print(json.dumps({key: result[key] for key in (
        "selected_gateway_ids", "selected_ut_ids", "selected_zone_ids"
    )}, indent=2, sort_keys=True))
