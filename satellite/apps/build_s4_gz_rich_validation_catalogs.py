#!/usr/bin/env python3
# encoding: utf-8

"""Build richer S4 G/Z offline validation catalogs.

The generated catalogs intentionally separate coverage-coordinate placement from
gateway policy selection. UTs are workload probes only; zone service domains are
catalog-defined coverage domains.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple


SATELLITE_ROOT = Path(__file__).resolve().parents[1]
CONFIG_ROOT = SATELLITE_ROOT / "configs" / "starlink_shell_ground"
DEFAULT_ZONE_INPUT = CONFIG_ROOT / "realistic_preview_zone_catalog_16.json"
DEFAULT_ZONE_OUTPUT = CONFIG_ROOT / "s4_gz_rich_validation_zone_catalog_16.json"
DEFAULT_UT_OUTPUT = CONFIG_ROOT / "s4_gz_rich_validation_ut_catalog_32.json"


COORDINATE_GATEWAY_IDS: Sequence[Tuple[str, str]] = (
    ("gw201", "gw202"),
    ("gw203", "gw204"),
    ("gw205", "gw206"),
    ("gw207", "gw208"),
    ("gw209", "gw210"),
    ("gw211", "gw212"),
    ("gw205", "gw206"),
    ("gw207", "gw208"),
    ("gw209", "gw210"),
    ("gw207", "gw208"),
    ("gw201", "gw202"),
    ("gw203", "gw204"),
    ("gw205", "gw206"),
    ("gw207", "gw208"),
    ("gw209", "gw210"),
    ("gw211", "gw212"),
)


POLICY_GATEWAY_IDS: Sequence[Tuple[str, str]] = (
    ("gw201", "gw202"),
    ("gw207", "gw208"),
    ("gw205", "gw206"),
    ("gw211", "gw212"),
    ("gw209", "gw210"),
    ("gw203", "gw204"),
    ("gw201", "gw202"),
    ("gw207", "gw208"),
    ("gw205", "gw206"),
    ("gw211", "gw212"),
    ("gw209", "gw210"),
    ("gw201", "gw202"),
    ("gw207", "gw208"),
    ("gw203", "gw204"),
    ("gw205", "gw206"),
    ("gw207", "gw208"),
)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _point(
    *,
    role: str,
    center_lat: float,
    center_lon: float,
    lat_offset: float = 0.0,
    lon_offset: float = 0.0,
) -> Dict[str, Any]:
    return {
        "role": role,
        "latitude_deg": round(center_lat + lat_offset, 6),
        "longitude_deg": round(center_lon + lon_offset, 6),
        "altitude_km": 0.0,
    }


def _service_points(zone: Dict[str, Any], radius_km: float) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    center_lat = float(zone.get("latitude_deg", 0.0))
    center_lon = float(zone.get("longitude_deg", 0.0))
    lat_delta = min(4.0, radius_km / 111.0)
    lon_delta = min(4.0, radius_km / 85.0)
    representatives = [
        _point(role="center", center_lat=center_lat, center_lon=center_lon),
        _point(
            role="north_sentinel",
            center_lat=center_lat,
            center_lon=center_lon,
            lat_offset=lat_delta * 0.45,
        ),
        _point(
            role="east_sentinel",
            center_lat=center_lat,
            center_lon=center_lon,
            lon_offset=lon_delta * 0.45,
        ),
    ]
    boundaries = [
        _point(
            role="north_boundary",
            center_lat=center_lat,
            center_lon=center_lon,
            lat_offset=lat_delta,
        ),
        _point(
            role="south_boundary",
            center_lat=center_lat,
            center_lon=center_lon,
            lat_offset=-lat_delta,
        ),
        _point(
            role="east_boundary",
            center_lat=center_lat,
            center_lon=center_lon,
            lon_offset=lon_delta,
        ),
        _point(
            role="west_boundary",
            center_lat=center_lat,
            center_lon=center_lon,
            lon_offset=-lon_delta,
        ),
    ]
    return representatives, boundaries


def _zone_record(
    *,
    source_zone: Dict[str, Any],
    index: int,
    ut_ids: Sequence[str],
) -> Dict[str, Any]:
    coordinate_gateways = list(COORDINATE_GATEWAY_IDS[index])
    policy_gateways = list(POLICY_GATEWAY_IDS[index])
    radius_km = max(360.0, min(float(source_zone.get("radius_km") or 600.0), 720.0))
    representatives, boundaries = _service_points(source_zone, radius_km)
    zone_id = str(source_zone["zone_id"])
    default_gateway = policy_gateways[0]
    secondary_gateway = policy_gateways[1]
    return {
        "zone_id": zone_id,
        "zone_index": int(source_zone.get("zone_index") or 260 + index),
        "display_name": f"{source_zone.get('display_name', zone_id)} G/Z Rich",
        "region": source_zone.get("region"),
        "latitude_deg": float(source_zone.get("latitude_deg", 0.0)),
        "longitude_deg": float(source_zone.get("longitude_deg", 0.0)),
        "altitude_km": float(source_zone.get("altitude_km", 0.0)),
        "preview_only": True,
        "active_service": True,
        "required_for_runtime": True,
        "service_role": "required_service",
        "partition_policy": "s4_gz_rich_validation_coverage_zone",
        "source": "skyseed_s4_gz_rich_validation_catalog",
        "service_domain_source": "coverage_aware_s4_gz_rich_validation_zone_catalog",
        "synthetic_ut_count": len(ut_ids),
        "ut_ids": list(ut_ids),
        "radius_km": radius_km,
        "coverage_margin_deg": 2.0,
        "minimum_dwell_seconds": 30.0,
        "hysteresis_switch_penalty": 0.15,
        "coordinate_gateway_ids": coordinate_gateways,
        "default_gateway": default_gateway,
        "secondary_gateway": secondary_gateway,
        "preferred_gateway_ids": policy_gateways,
        "fallback_to_shortest_path": True,
        "max_additional_satellite_hops_relative_to_best_candidate": 2,
        "prefix_overrides": [
            {
                "prefix": "10.160.0.0/24",
                "gateway": secondary_gateway,
                "preferred_gateway_ids": [secondary_gateway, default_gateway],
                "policy_name": f"{zone_id}_asn160_remote_override",
                "purpose": "default_plus_override_gateway_class_validation",
            }
        ],
        "service_domain": {
            "source": "coverage_aware_s4_gz_rich_validation_zone_catalog",
            "center": {
                "latitude_deg": float(source_zone.get("latitude_deg", 0.0)),
                "longitude_deg": float(source_zone.get("longitude_deg", 0.0)),
                "altitude_km": float(source_zone.get("altitude_km", 0.0)),
            },
            "radius_km": radius_km,
            "coverage_margin_deg": 2.0,
            "minimum_dwell_seconds": 30.0,
            "hysteresis_switch_penalty": 0.15,
            "representative_ground_points": representatives,
            "boundary_points": boundaries,
            "ut_sample_dependency": "none_workload_probes_only",
        },
        "representative_ground_points": representatives,
        "boundary_points": boundaries,
    }


def _ut_record(
    *,
    source_zone: Dict[str, Any],
    index: int,
    role: str,
    ut_id: str,
    gateway_affinity: str,
) -> Dict[str, Any]:
    center_lat = float(source_zone.get("latitude_deg", 0.0))
    center_lon = float(source_zone.get("longitude_deg", 0.0))
    if role == "zone_boundary":
        lat = center_lat + 1.75
        lon = center_lon
    else:
        lat = center_lat + 0.05
        lon = center_lon - 0.05
    return {
        "ut_id": ut_id,
        "ut_index": 2001 + index,
        "display_name": f"{source_zone.get('display_name', source_zone['zone_id'])} {role} probe",
        "type": "synthetic_s4_gz_rich_validation_ut",
        "source": "skyseed_s4_gz_rich_validation_catalog",
        "zone_id": str(source_zone["zone_id"]),
        "region": source_zone.get("region"),
        "latitude_deg": round(lat, 6),
        "longitude_deg": round(lon, 6),
        "altitude_km": 0.0,
        "preview_only": True,
        "placement_role": role,
        "gateway_affinity": gateway_affinity,
        "uses_prefix_override": role == "zone_boundary",
        "workload_probe": True,
        "zone_membership_semantics": "workload_probe_affinity_not_zone_definition",
    }


def build_catalogs(zone_input: Path) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    source_zones = list(_read_json(zone_input))
    if len(source_zones) != 16:
        raise ValueError(f"expected 16 zones, found {len(source_zones)} in {zone_input}")

    zones: List[Dict[str, Any]] = []
    uts: List[Dict[str, Any]] = []
    for zone_index, source_zone in enumerate(source_zones):
        ut_ids = [f"ut{2001 + zone_index * 2}", f"ut{2002 + zone_index * 2}"]
        zones.append(
            _zone_record(source_zone=source_zone, index=zone_index, ut_ids=ut_ids)
        )
        policy_gateways = POLICY_GATEWAY_IDS[zone_index]
        uts.append(
            _ut_record(
                source_zone=source_zone,
                index=zone_index * 2,
                role="default_gateway_near",
                ut_id=ut_ids[0],
                gateway_affinity=policy_gateways[0],
            )
        )
        uts.append(
            _ut_record(
                source_zone=source_zone,
                index=zone_index * 2 + 1,
                role="zone_boundary",
                ut_id=ut_ids[1],
                gateway_affinity=policy_gateways[1],
            )
        )
    return zones, uts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zone-input", type=Path, default=DEFAULT_ZONE_INPUT)
    parser.add_argument("--zone-output", type=Path, default=DEFAULT_ZONE_OUTPUT)
    parser.add_argument("--ut-output", type=Path, default=DEFAULT_UT_OUTPUT)
    args = parser.parse_args()

    zones, uts = build_catalogs(args.zone_input)
    _write_json(args.zone_output, zones)
    _write_json(args.ut_output, uts)
    print(
        json.dumps(
            {
                "zone_output": str(args.zone_output),
                "ut_output": str(args.ut_output),
                "zone_count": len(zones),
                "ut_count": len(uts),
                "ut_semantics": "workload_probe_only_not_control_plane_input",
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
