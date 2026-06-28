#!/usr/bin/env python3
# encoding: utf-8

"""Generate deterministic Starlink service-zone catalogs for preview studies."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Sequence

from _path_setup import bootstrap_app_import_path

SATELLITE_DIR = bootstrap_app_import_path()

from ground_model import load_gateway_catalog


DEFAULT_OUTPUT_DIR = SATELLITE_DIR / "configs" / "starlink_shell_ground"
DEFAULT_GATEWAY_CATALOG = (
    SATELLITE_DIR
    / "configs"
    / "starlink_shell_ground"
    / "one_shell_gateway_catalog_valid6.json"
)


@dataclass(frozen=True)
class ZoneSpec:
    key: str
    display_name: str
    region: str
    latitude_deg: float
    longitude_deg: float
    service_role: str
    synthetic_ut_count: int
    radius_km: float = 25.0
    coverage_margin_deg: float = 2.0
    active_service: bool = True


BASE_ZONE_SPECS: Sequence[ZoneSpec] = (
    ZoneSpec("north-america-west-01", "North America West 01", "north-america-west", 47.61, -122.33, "required_service", 2),
    ZoneSpec("north-america-central-01", "North America Central 01", "north-america-central", 39.74, -104.99, "required_service", 2),
    ZoneSpec("north-america-east-01", "North America East 01", "north-america-east", 40.71, -74.00, "required_service", 2),
    ZoneSpec("europe-west-01", "Europe West 01", "europe-west", 51.51, -0.13, "required_service", 2),
    ZoneSpec("europe-central-01", "Europe Central 01", "europe-central", 50.11, 8.68, "required_service", 2),
    ZoneSpec("mediterranean-01", "Mediterranean 01", "mediterranean", 35.90, 14.51, "required_service", 2),
    ZoneSpec("middle-east-01", "Middle East 01", "middle-east", 25.20, 55.27, "required_service", 2),
    ZoneSpec("south-asia-01", "South Asia 01", "south-asia", 19.08, 72.88, "required_service", 2),
    ZoneSpec("east-asia-01", "East Asia 01", "east-asia", 35.68, 139.76, "required_service", 2),
    ZoneSpec("southeast-asia-01", "Southeast Asia 01", "southeast-asia", 1.35, 103.82, "required_service", 2),
    ZoneSpec("south-america-east-01", "South America East 01", "south-america-east", -23.55, -46.63, "optional_service", 1),
    ZoneSpec("south-america-west-01", "South America West 01", "south-america-west", -33.45, -70.66, "optional_service", 1),
    ZoneSpec("africa-west-01", "Africa West 01", "africa-west", 6.52, 3.38, "optional_service", 1),
    ZoneSpec("africa-east-01", "Africa East 01", "africa-east", -1.29, 36.82, "optional_service", 1),
    ZoneSpec("australia-east-01", "Australia East 01", "australia-east", -33.87, 151.21, "optional_service", 1),
    ZoneSpec("atlantic-west-01", "Atlantic West 01", "atlantic-west", 32.29, -64.78, "optional_service", 1),
    ZoneSpec("arctic-atlantic-obs", "Arctic Atlantic Observation", "arctic", 64.18, -51.72, "observation", 0, radius_km=75.0, active_service=False),
    ZoneSpec("antarctic-south-obs", "Antarctic South Observation", "antarctic", -54.80, -68.30, "observation", 0, radius_km=75.0, active_service=False),
    ZoneSpec("north-pacific-obs", "North Pacific Observation", "north-pacific", 35.00, -160.00, "observation", 0, radius_km=100.0, active_service=False),
    ZoneSpec("south-pacific-obs", "South Pacific Observation", "south-pacific", -20.00, -145.00, "observation", 0, radius_km=100.0, active_service=False),
    ZoneSpec("north-atlantic-obs", "North Atlantic Observation", "north-atlantic", 45.00, -35.00, "observation", 0, radius_km=100.0, active_service=False),
    ZoneSpec("south-atlantic-obs", "South Atlantic Observation", "south-atlantic", -20.00, -20.00, "observation", 0, radius_km=100.0, active_service=False),
    ZoneSpec("indian-ocean-obs", "Indian Ocean Observation", "indian-ocean", -15.00, 80.00, "observation", 0, radius_km=100.0, active_service=False),
    ZoneSpec("central-asia-obs", "Central Asia Observation", "central-asia", 43.20, 76.90, "observation", 0, radius_km=75.0, active_service=False),
    ZoneSpec("north-africa-obs", "North Africa Observation", "north-africa", 30.04, 31.24, "observation", 0, radius_km=75.0, active_service=False),
    ZoneSpec("southern-africa-obs", "Southern Africa Observation", "southern-africa", -26.20, 28.04, "observation", 0, radius_km=75.0, active_service=False),
    ZoneSpec("east-pacific-obs", "East Pacific Observation", "east-pacific", 10.00, -120.00, "observation", 0, radius_km=100.0, active_service=False),
    ZoneSpec("west-pacific-obs", "West Pacific Observation", "west-pacific", 15.00, 150.00, "observation", 0, radius_km=100.0, active_service=False),
    ZoneSpec("scandinavia-obs", "Scandinavia Observation", "scandinavia", 59.91, 10.75, "observation", 0, radius_km=75.0, active_service=False),
    ZoneSpec("north-asia-obs", "North Asia Observation", "north-asia", 55.75, 37.62, "observation", 0, radius_km=75.0, active_service=False),
    ZoneSpec("new-zealand-obs", "New Zealand Observation", "new-zealand", -41.29, 174.78, "observation", 0, radius_km=75.0, active_service=False),
    ZoneSpec("greenland-obs", "Greenland Observation", "greenland", 64.15, -21.94, "observation", 0, radius_km=75.0, active_service=False),
)


def _zone_specs_for_count(zone_count: int) -> List[ZoneSpec]:
    required = [spec for spec in BASE_ZONE_SPECS if spec.service_role == "required_service"]
    optional = [spec for spec in BASE_ZONE_SPECS if spec.service_role == "optional_service"]
    observation = [spec for spec in BASE_ZONE_SPECS if spec.service_role == "observation"]
    if zone_count == 24:
        return [*required[:8], *optional[:4], *observation[:12]]
    if zone_count == 32:
        return [*required[:10], *optional[:6], *observation[:16]]
    raise ValueError("unsupported zone_count")


ZONE_COUNT_LIMITS = {
    24: 24,
    32: 32,
}


def _distance_km(first: Dict[str, Any], lat: float, lon: float) -> float:
    radius_km = 6371.0
    first_lat = math.radians(float(first["latitude_deg"]))
    second_lat = math.radians(lat)
    dlat = second_lat - first_lat
    dlon = math.radians(lon - float(first["longitude_deg"]))
    hav = (
        math.sin(dlat / 2.0) ** 2
        + math.cos(first_lat) * math.cos(second_lat) * math.sin(dlon / 2.0) ** 2
    )
    return 2.0 * radius_km * math.asin(min(1.0, math.sqrt(max(0.0, hav))))


def _gateway_order(
    gateways: Sequence[Dict[str, Any]],
    *,
    latitude_deg: float,
    longitude_deg: float,
) -> List[str]:
    ranked = sorted(
        gateways,
        key=lambda gateway: (
            _distance_km(gateway, latitude_deg, longitude_deg),
            str(gateway["gateway_id"]),
        ),
    )
    return [str(gateway["gateway_id"]) for gateway in ranked]


def _ut_coordinates(
    *,
    latitude_deg: float,
    longitude_deg: float,
    index: int,
) -> Dict[str, float]:
    offsets = (
        (0.035, -0.035),
        (-0.035, 0.035),
        (0.045, 0.045),
        (-0.045, -0.045),
    )
    lat_offset, lon_offset = offsets[index % len(offsets)]
    return {
        "latitude_deg": round(latitude_deg + lat_offset, 6),
        "longitude_deg": round(longitude_deg + lon_offset, 6),
    }


def build_catalogs(
    *,
    zone_count: int,
    gateways: Sequence[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    if zone_count not in ZONE_COUNT_LIMITS:
        raise ValueError(f"unsupported zone_count {zone_count}; expected one of {sorted(ZONE_COUNT_LIMITS)}")
    zone_specs = _zone_specs_for_count(zone_count)
    zones: List[Dict[str, Any]] = []
    uts: List[Dict[str, Any]] = []
    ut_index = 1001
    for ordinal, spec in enumerate(zone_specs, start=1):
        zone_id = f"zone-{spec.key}"
        gateway_order = _gateway_order(
            gateways,
            latitude_deg=spec.latitude_deg,
            longitude_deg=spec.longitude_deg,
        )
        default_gateway = gateway_order[0] if gateway_order else None
        active_service = bool(spec.active_service and spec.service_role in {"required_service", "optional_service"})
        required_for_runtime = bool(spec.service_role == "required_service" and active_service)
        zone = {
            "zone_id": zone_id,
            "zone_index": ordinal,
            "display_name": spec.display_name,
            "region": spec.region,
            "latitude_deg": spec.latitude_deg,
            "longitude_deg": spec.longitude_deg,
            "altitude_km": 0.0,
            "radius_km": spec.radius_km,
            "coverage_margin_deg": spec.coverage_margin_deg,
            "service_role": spec.service_role,
            "active_service": active_service,
            "required_for_runtime": required_for_runtime,
            "default_gateway": default_gateway,
            "preferred_gateway_ids": gateway_order[:2],
            "prefix_overrides": [
                {
                    "prefix": "10.160.0.0/24",
                    "gateway": gateway_order[1],
                    "policy_name": "asn160_secondary_gateway_preview",
                }
            ] if len(gateway_order) > 1 and active_service else [],
            "synthetic_ut_count": spec.synthetic_ut_count,
            "ut_ids": [],
            "partition_policy": "deterministic_global_preview_zone_catalog",
            "source": f"generated_starlink_zone_catalog_{zone_count}",
            "service_domain_source": "coverage_aware_generated_zone_catalog",
            "service_domain": {
                "source": "coverage_aware_generated_zone_catalog",
                "construction_order": (
                    "orbit_shell_geometry_to_coverage_zone_to_anchor_to_fec"
                ),
                "center": {
                    "latitude_deg": spec.latitude_deg,
                    "longitude_deg": spec.longitude_deg,
                    "altitude_km": 0.0,
                },
                "radius_km": spec.radius_km,
                "representative_ground_points": [],
                "boundary_points": [],
                "coverage_margin_deg": spec.coverage_margin_deg,
            },
        }
        for ut_offset in range(spec.synthetic_ut_count):
            ut_id = f"ut{ut_index}"
            coords = _ut_coordinates(
                latitude_deg=spec.latitude_deg,
                longitude_deg=spec.longitude_deg,
                index=ut_offset,
            )
            zone["ut_ids"].append(ut_id)
            uts.append(
                {
                    "ut_id": ut_id,
                    "ut_index": ut_index,
                    "display_name": f"{spec.display_name} UT {ut_offset + 1}",
                    "zone_id": zone_id,
                    "region": spec.region,
                    "latitude_deg": coords["latitude_deg"],
                    "longitude_deg": coords["longitude_deg"],
                    "altitude_km": 0.0,
                    "source": f"generated_starlink_zone_catalog_{zone_count}",
                    "type": "synthetic_zone_service_ut",
                }
            )
            ut_index += 1
        zones.append(zone)
    return {"zones": zones, "uts": uts}


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate deterministic zone/UT catalogs")
    parser.add_argument("--zone-count", type=int, choices=tuple(sorted(ZONE_COUNT_LIMITS)), required=True)
    parser.add_argument("--gateway-catalog", default=str(DEFAULT_GATEWAY_CATALOG))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--zone-output", default=None)
    parser.add_argument("--ut-output", default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    gateways = load_gateway_catalog(str(args.gateway_catalog))
    catalogs = build_catalogs(zone_count=int(args.zone_count), gateways=gateways)
    output_dir = Path(args.output_dir).resolve()
    zone_output = (
        Path(args.zone_output).resolve()
        if args.zone_output
        else output_dir / f"deterministic_zone_catalog_{args.zone_count}.json"
    )
    ut_output = (
        Path(args.ut_output).resolve()
        if args.ut_output
        else output_dir / f"deterministic_ut_catalog_{args.zone_count}.json"
    )
    _write_json(zone_output, catalogs["zones"])
    _write_json(ut_output, catalogs["uts"])
    print(
        json.dumps(
            {
                "zone_count": len(catalogs["zones"]),
                "ut_count": len(catalogs["uts"]),
                "zone_output": str(zone_output),
                "ut_output": str(ut_output),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
