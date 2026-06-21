#!/usr/bin/env python3
# encoding: utf-8

"""Build a preview-only realistic Skyseed workload catalog and compact plan."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from _path_setup import bootstrap_app_import_path

SATELLITE_DIR = bootstrap_app_import_path()

from starlink_shell import (  # noqa: E402
    PLANNED_STARLINK_PHASE1_SHELL_PRESETS,
    generate_planned_shell_records,
    planned_shell_preset,
    propagate_planned_shell_records,
)


EARTH_RADIUS_KM = 6371.0
DEFAULT_OUTPUT_DIR = SATELLITE_DIR / "configs" / "starlink_shell_ground"
CATALOG_SOURCE = "skyseed_goal18_realistic_preview_catalog"
SHELL_USAGE_BY_ID = {
    "S1": "Tier 2 high-pressure preview and final 1000+ satellite stress target",
    "S2": "Tier 2 alternate 1000+ satellite pressure profile",
    "S3": "Tier 1 fallback intermediate profile if 300+ satellite coverage is insufficient",
    "S4": "Tier 1 preferred intermediate 300+ satellite larger-shell preview target",
    "S5": "Tier 0 canonical 172-satellite baseline and BPF experimental backend A/B target",
}


@dataclass(frozen=True)
class GatewaySpec:
    key: str
    name: str
    region: str
    role: str
    latitude_deg: float
    longitude_deg: float


@dataclass(frozen=True)
class UtSpec:
    key: str
    display_name: str
    placement_role: str
    latitude_deg: float
    longitude_deg: float
    gateway_affinity: str
    override_flow: bool = False


@dataclass(frozen=True)
class ZoneSpec:
    key: str
    display_name: str
    region: str
    service_role: str
    latitude_deg: float
    longitude_deg: float
    radius_km: float
    default_gateway_key: str | None
    secondary_gateway_key: str | None
    ut_specs: Sequence[UtSpec]
    coverage_margin_deg: float = 2.0


REGION_GATEWAYS: Sequence[GatewaySpec] = (
    GatewaySpec("naw-default", "Seattle Default GW", "north-america-west", "default", 47.6062, -122.3321),
    GatewaySpec("naw-secondary", "Denver Secondary GW", "north-america-west", "secondary", 39.7392, -104.9903),
    GatewaySpec("nae-default", "New York Default GW", "north-america-east", "default", 40.7128, -74.0060),
    GatewaySpec("nae-secondary", "Atlanta Secondary GW", "north-america-east", "secondary", 33.7490, -84.3880),
    GatewaySpec("eur-default", "Frankfurt Default GW", "europe-west", "default", 50.1109, 8.6821),
    GatewaySpec("eur-secondary", "Madrid Secondary GW", "europe-west", "secondary", 40.4168, -3.7038),
    GatewaySpec("mes-default", "Dubai Default GW", "middle-east-south-asia", "default", 25.2048, 55.2708),
    GatewaySpec("mes-secondary", "Mumbai Secondary GW", "middle-east-south-asia", "secondary", 19.0760, 72.8777),
    GatewaySpec("eap-default", "Tokyo Default GW", "east-asia-pacific", "default", 35.6762, 139.6503),
    GatewaySpec("eap-secondary", "Singapore Secondary GW", "east-asia-pacific", "secondary", 1.3521, 103.8198),
    GatewaySpec("sam-default", "Sao Paulo Default GW", "south-america", "default", -23.5505, -46.6333),
    GatewaySpec("sam-secondary", "Santiago Secondary GW", "south-america", "secondary", -33.4489, -70.6693),
)


ZONE_SPECS: Sequence[ZoneSpec] = (
    ZoneSpec(
        "north-america-west-wide",
        "North America West Wide",
        "north-america-west",
        "required_service",
        44.5,
        -113.0,
        1250.0,
        "naw-default",
        "naw-secondary",
        (
            UtSpec("naw-default-edge", "NA West Default-Edge UT", "default_gateway_near", 47.61, -122.20, "naw-default"),
            UtSpec("naw-secondary-edge", "NA West Secondary-Edge UT", "secondary_gateway_near", 39.76, -104.84, "naw-secondary", True),
            UtSpec("naw-boundary", "NA West Boundary UT", "zone_boundary", 49.25, -123.10, "naw-default"),
            UtSpec("naw-remote-return", "NA West Remote Return UT", "remote_return_path", 45.52, -111.04, "naw-default"),
        ),
    ),
    ZoneSpec(
        "north-america-east-wide",
        "North America East Wide",
        "north-america-east",
        "required_service",
        38.2,
        -82.5,
        1150.0,
        "nae-default",
        "nae-secondary",
        (
            UtSpec("nae-default-edge", "NA East Default-Edge UT", "default_gateway_near", 40.73, -73.95, "nae-default"),
            UtSpec("nae-secondary-edge", "NA East Secondary-Edge UT", "secondary_gateway_near", 33.78, -84.36, "nae-secondary", True),
            UtSpec("nae-boundary", "NA East Boundary UT", "zone_boundary", 42.33, -83.05, "nae-default"),
            UtSpec("nae-remote-return", "NA East Remote Return UT", "remote_return_path", 25.76, -80.19, "nae-default"),
        ),
    ),
    ZoneSpec(
        "europe-west-wide",
        "Europe West Wide",
        "europe-west",
        "required_service",
        47.7,
        2.0,
        1200.0,
        "eur-default",
        "eur-secondary",
        (
            UtSpec("eur-default-edge", "Europe Default-Edge UT", "default_gateway_near", 50.12, 8.65, "eur-default"),
            UtSpec("eur-secondary-edge", "Europe Secondary-Edge UT", "secondary_gateway_near", 40.43, -3.68, "eur-secondary", True),
            UtSpec("eur-boundary", "Europe Boundary UT", "zone_boundary", 51.50, -0.12, "eur-default"),
            UtSpec("eur-remote-return", "Europe Remote Return UT", "remote_return_path", 59.91, 10.75, "eur-default"),
        ),
    ),
    ZoneSpec(
        "middle-east-south-asia-wide",
        "Middle East South Asia Wide",
        "middle-east-south-asia",
        "required_service",
        22.8,
        64.5,
        1350.0,
        "mes-default",
        "mes-secondary",
        (
            UtSpec("mes-default-edge", "ME/SA Default-Edge UT", "default_gateway_near", 25.21, 55.31, "mes-default"),
            UtSpec("mes-secondary-edge", "ME/SA Secondary-Edge UT", "secondary_gateway_near", 19.09, 72.86, "mes-secondary", True),
            UtSpec("mes-boundary", "ME/SA Boundary UT", "zone_boundary", 24.86, 67.01, "mes-secondary"),
            UtSpec("mes-remote-return", "ME/SA Remote Return UT", "remote_return_path", 28.61, 77.21, "mes-secondary"),
        ),
    ),
    ZoneSpec(
        "east-asia-pacific-wide",
        "East Asia Pacific Wide",
        "east-asia-pacific",
        "required_service",
        20.0,
        122.0,
        1900.0,
        "eap-default",
        "eap-secondary",
        (
            UtSpec("eap-default-edge", "EAP Default-Edge UT", "default_gateway_near", 35.68, 139.70, "eap-default"),
            UtSpec("eap-secondary-edge", "EAP Secondary-Edge UT", "secondary_gateway_near", 1.36, 103.83, "eap-secondary", True),
            UtSpec("eap-boundary", "EAP Boundary UT", "zone_boundary", 14.60, 120.98, "eap-secondary"),
            UtSpec("eap-remote-return", "EAP Remote Return UT", "remote_return_path", 37.57, 126.98, "eap-default"),
        ),
    ),
    ZoneSpec(
        "south-america-wide",
        "South America Wide",
        "south-america",
        "required_service",
        -25.5,
        -59.0,
        1600.0,
        "sam-default",
        "sam-secondary",
        (
            UtSpec("sam-default-edge", "South America Default-Edge UT", "default_gateway_near", -23.56, -46.64, "sam-default"),
            UtSpec("sam-secondary-edge", "South America Secondary-Edge UT", "secondary_gateway_near", -33.45, -70.66, "sam-secondary", True),
            UtSpec("sam-boundary", "South America Boundary UT", "zone_boundary", -12.05, -77.04, "sam-secondary"),
            UtSpec("sam-remote-return", "South America Remote Return UT", "remote_return_path", -8.05, -34.90, "sam-default"),
        ),
    ),
    ZoneSpec(
        "africa-west-optional",
        "Africa West Optional",
        "africa-west",
        "optional_service",
        6.5,
        3.4,
        1000.0,
        "eur-default",
        "eur-secondary",
        (
            UtSpec("afw-default-observe", "Africa West Default UT", "default_gateway_near", 6.52, 3.38, "eur-default"),
            UtSpec("afw-secondary-observe", "Africa West Secondary UT", "secondary_gateway_near", 14.69, -17.45, "eur-secondary", True),
        ),
    ),
    ZoneSpec(
        "africa-east-optional",
        "Africa East Optional",
        "africa-east",
        "optional_service",
        -1.3,
        36.8,
        1100.0,
        "mes-default",
        "mes-secondary",
        (
            UtSpec("afe-default-observe", "Africa East Default UT", "default_gateway_near", -1.29, 36.82, "mes-default"),
            UtSpec("afe-secondary-observe", "Africa East Secondary UT", "secondary_gateway_near", -6.79, 39.21, "mes-secondary", True),
        ),
    ),
    ZoneSpec(
        "australia-east-optional",
        "Australia East Optional",
        "australia-east",
        "optional_service",
        -31.0,
        145.0,
        1250.0,
        "eap-default",
        "eap-secondary",
        (
            UtSpec("aue-default-observe", "Australia East Default UT", "default_gateway_near", -33.87, 151.21, "eap-default"),
            UtSpec("aue-secondary-observe", "Australia East Secondary UT", "secondary_gateway_near", -27.47, 153.03, "eap-secondary", True),
        ),
    ),
    ZoneSpec(
        "central-asia-optional",
        "Central Asia Optional",
        "central-asia",
        "optional_service",
        43.2,
        76.9,
        1150.0,
        "mes-default",
        "mes-secondary",
        (
            UtSpec("cas-default-observe", "Central Asia Default UT", "default_gateway_near", 43.24, 76.93, "mes-default"),
            UtSpec("cas-secondary-observe", "Central Asia Secondary UT", "secondary_gateway_near", 41.31, 69.24, "mes-secondary", True),
        ),
    ),
    ZoneSpec("arctic-atlantic-observation", "Arctic Atlantic Observation", "arctic", "observation", 64.2, -51.7, 900.0, None, None, ()),
    ZoneSpec("north-atlantic-observation", "North Atlantic Observation", "north-atlantic", "observation", 45.0, -35.0, 1200.0, None, None, ()),
    ZoneSpec("north-pacific-observation", "North Pacific Observation", "north-pacific", "observation", 35.0, -160.0, 1500.0, None, None, ()),
    ZoneSpec("south-pacific-observation", "South Pacific Observation", "south-pacific", "observation", -20.0, -145.0, 1500.0, None, None, ()),
    ZoneSpec("indian-ocean-observation", "Indian Ocean Observation", "indian-ocean", "observation", -15.0, 80.0, 1400.0, None, None, ()),
    ZoneSpec("southern-ocean-observation", "Southern Ocean Observation", "southern-ocean", "observation", -50.0, 120.0, 1300.0, None, None, ()),
)


def _gateway_id(index: int) -> str:
    return f"gw{200 + index:03d}"


def _ut_id(index: int) -> str:
    return f"ut{2000 + index:04d}"


def _zone_id(key: str) -> str:
    return f"zone-{key}"


def _prefix(index: int) -> str:
    return f"10.18{index // 256}.{index % 256}.0/24"


def _ground_position(*, latitude_deg: float, longitude_deg: float, altitude_km: float = 0.0) -> tuple[float, float, float]:
    latitude_rad = math.radians(latitude_deg)
    longitude_rad = math.radians(longitude_deg)
    radius_km = EARTH_RADIUS_KM + altitude_km
    return (
        radius_km * math.cos(latitude_rad) * math.cos(longitude_rad),
        radius_km * math.cos(latitude_rad) * math.sin(longitude_rad),
        radius_km * math.sin(latitude_rad),
    )


def _elevation_deg(owner_position: tuple[float, float, float], candidate_position: tuple[float, float, float]) -> float:
    los = tuple(candidate_position[index] - owner_position[index] for index in range(3))
    los_norm = math.sqrt(sum(component * component for component in los))
    owner_norm = math.sqrt(sum(component * component for component in owner_position))
    if los_norm == 0.0 or owner_norm == 0.0:
        return -90.0
    vertical_component = sum(los[index] * owner_position[index] for index in range(3)) / (los_norm * owner_norm)
    return math.degrees(math.asin(max(-1.0, min(1.0, vertical_component))))


def _distance_km(first: Dict[str, Any], second: Dict[str, Any]) -> float:
    first_lat = math.radians(float(first["latitude_deg"]))
    second_lat = math.radians(float(second["latitude_deg"]))
    delta_lat = second_lat - first_lat
    delta_lon = math.radians(float(second["longitude_deg"]) - float(first["longitude_deg"]))
    hav = (
        math.sin(delta_lat / 2.0) ** 2
        + math.cos(first_lat) * math.cos(second_lat) * math.sin(delta_lon / 2.0) ** 2
    )
    return 2.0 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(max(0.0, hav))))


def build_catalogs() -> Dict[str, List[Dict[str, Any]]]:
    gateway_key_to_id: Dict[str, str] = {}
    gateways: List[Dict[str, Any]] = []
    for index, spec in enumerate(REGION_GATEWAYS, start=1):
        gateway_id = _gateway_id(index)
        gateway_key_to_id[spec.key] = gateway_id
        ix_id = 100 + index - 1
        gateways.append(
            {
                "gateway_id": gateway_id,
                "gateway_index": 200 + index,
                "name": spec.name,
                "region": spec.region,
                "gateway_role": spec.role,
                "latitude_deg": spec.latitude_deg,
                "longitude_deg": spec.longitude_deg,
                "altitude_km": 0.0,
                "type": "manual_backhaul_gateway",
                "source": CATALOG_SOURCE,
                "confidence": "synthetic_preview",
                "unofficial": True,
                "preview_only": True,
                "ix_id": ix_id,
                "ix_ip_address": f"10.{ix_id}.0.250",
                "upstream_asn": (2, 3, 4, 11, 12)[(index - 1) % 5],
            }
        )

    zones: List[Dict[str, Any]] = []
    uts: List[Dict[str, Any]] = []
    ut_index = 1
    for zone_index, spec in enumerate(ZONE_SPECS, start=1):
        zone_id = _zone_id(spec.key)
        active_service = spec.service_role in {"required_service", "optional_service"}
        default_gateway = gateway_key_to_id.get(str(spec.default_gateway_key))
        secondary_gateway = gateway_key_to_id.get(str(spec.secondary_gateway_key))
        preferred_gateway_ids = [
            gateway_id
            for gateway_id in (default_gateway, secondary_gateway)
            if gateway_id is not None
        ]
        prefix_overrides = []
        if active_service and secondary_gateway:
            prefix_overrides.append(
                {
                    "prefix": _prefix(zone_index),
                    "gateway": secondary_gateway,
                    "policy_name": f"{spec.key}_secondary_gateway_preview",
                    "purpose": "secondary_gateway_exception_and_return_path_preview",
                }
            )
        zone_ut_ids: List[str] = []
        for ut_spec in spec.ut_specs:
            ut_id = _ut_id(ut_index)
            zone_ut_ids.append(ut_id)
            uts.append(
                {
                    "ut_id": ut_id,
                    "ut_index": 2000 + ut_index,
                    "display_name": ut_spec.display_name,
                    "zone_id": zone_id,
                    "region": spec.region,
                    "latitude_deg": ut_spec.latitude_deg,
                    "longitude_deg": ut_spec.longitude_deg,
                    "altitude_km": 0.0,
                    "source": CATALOG_SOURCE,
                    "type": "synthetic_realistic_preview_ut",
                    "preview_only": True,
                    "placement_role": ut_spec.placement_role,
                    "gateway_affinity": gateway_key_to_id[ut_spec.gateway_affinity],
                    "uses_prefix_override": bool(ut_spec.override_flow),
                }
            )
            ut_index += 1
        zones.append(
            {
                "zone_id": zone_id,
                "zone_index": 200 + zone_index,
                "display_name": spec.display_name,
                "region": spec.region,
                "latitude_deg": spec.latitude_deg,
                "longitude_deg": spec.longitude_deg,
                "altitude_km": 0.0,
                "radius_km": spec.radius_km,
                "coverage_margin_deg": spec.coverage_margin_deg,
                "service_role": spec.service_role,
                "active_service": active_service,
                "required_for_runtime": spec.service_role == "required_service",
                "default_gateway": default_gateway,
                "secondary_gateway": secondary_gateway,
                "preferred_gateway_ids": preferred_gateway_ids,
                "prefix_overrides": prefix_overrides,
                "synthetic_ut_count": len(zone_ut_ids),
                "ut_ids": zone_ut_ids,
                "partition_policy": "goal18_realistic_preview_wide_region_zone",
                "source": CATALOG_SOURCE,
                "preview_only": True,
            }
        )
    return {"gateways": gateways, "zones": zones, "uts": uts}


def _best_anchor_by_zone(
    *,
    zones: Sequence[Dict[str, Any]],
    propagated_slices: Sequence[Any],
    min_elevation_deg: float,
) -> Dict[str, List[Dict[str, Any]]]:
    zone_positions = {
        str(zone["zone_id"]): _ground_position(
            latitude_deg=float(zone["latitude_deg"]),
            longitude_deg=float(zone["longitude_deg"]),
            altitude_km=float(zone.get("altitude_km", 0.0)),
        )
        for zone in zones
    }
    rows_by_zone: Dict[str, List[Dict[str, Any]]] = {str(zone["zone_id"]): [] for zone in zones}
    for orbit_slice in propagated_slices:
        satellite_positions = {
            node_id: (state.x_km, state.y_km, state.z_km)
            for node_id, state in orbit_slice.satellite_states.items()
        }
        for zone_id, zone_position in zone_positions.items():
            best_satellite_id = None
            best_elevation = -90.0
            for satellite_id, satellite_position in satellite_positions.items():
                elevation = _elevation_deg(zone_position, satellite_position)
                if elevation > best_elevation:
                    best_elevation = elevation
                    best_satellite_id = satellite_id
            rows_by_zone[zone_id].append(
                {
                    "relative_time_seconds": orbit_slice.relative_time_seconds,
                    "anchor_satellite_id": (
                        best_satellite_id
                        if best_satellite_id is not None and best_elevation >= min_elevation_deg
                        else None
                    ),
                    "best_elevation_deg": round(best_elevation, 3),
                }
            )
    return rows_by_zone


def _dwell_summary(rows: Sequence[Dict[str, Any]], *, step_seconds: float) -> Dict[str, Any]:
    interval_anchors = [row.get("anchor_satellite_id") for row in rows[:-1]]
    interval_count = len(interval_anchors)
    if interval_count == 0:
        return {
            "availability_ratio": 0.0,
            "unresolved_duration_seconds": 0.0,
            "anchor_change_count": 0,
            "dwell_seconds": {"min": 0.0, "avg": 0.0, "max": 0.0},
        }
    unresolved_count = sum(1 for anchor in interval_anchors if anchor is None)
    dwell_lengths: List[int] = []
    previous_anchor = interval_anchors[0]
    current_length = 1
    anchor_change_count = 0
    for anchor in interval_anchors[1:]:
        if anchor == previous_anchor:
            current_length += 1
            continue
        if previous_anchor is not None:
            dwell_lengths.append(current_length)
        if anchor is not None and previous_anchor is not None:
            anchor_change_count += 1
        previous_anchor = anchor
        current_length = 1
    if previous_anchor is not None:
        dwell_lengths.append(current_length)
    dwell_seconds = [length * step_seconds for length in dwell_lengths]
    return {
        "availability_ratio": round((interval_count - unresolved_count) / interval_count, 6),
        "unresolved_duration_seconds": round(unresolved_count * step_seconds, 6),
        "anchor_change_count": anchor_change_count,
        "dwell_seconds": {
            "min": round(min(dwell_seconds), 6) if dwell_seconds else 0.0,
            "avg": round(sum(dwell_seconds) / len(dwell_seconds), 6) if dwell_seconds else 0.0,
            "max": round(max(dwell_seconds), 6) if dwell_seconds else 0.0,
        },
    }


def _role_counts(entries: Iterable[Dict[str, Any]], field: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for entry in entries:
        value = str(entry.get(field, "unknown"))
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _distribution_summary(catalogs: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    uts = catalogs["uts"]
    gateways = catalogs["gateways"]
    zones = catalogs["zones"]
    active_zones = [zone for zone in zones if zone.get("active_service")]
    zone_by_id = {str(zone["zone_id"]): zone for zone in zones}
    gateway_by_id = {str(gateway["gateway_id"]): gateway for gateway in gateways}
    default_distances: List[float] = []
    secondary_distances: List[float] = []
    for ut in uts:
        zone = zone_by_id[str(ut["zone_id"])]
        default_gateway = gateway_by_id.get(str(zone.get("default_gateway")))
        secondary_gateway = gateway_by_id.get(str(zone.get("secondary_gateway")))
        if default_gateway is not None:
            default_distances.append(_distance_km(ut, default_gateway))
        if secondary_gateway is not None:
            secondary_distances.append(_distance_km(ut, secondary_gateway))
    latitudes = [float(entry["latitude_deg"]) for entry in [*uts, *gateways, *zones]]
    longitudes = [float(entry["longitude_deg"]) for entry in [*uts, *gateways, *zones]]
    return {
        "ut_roles": _role_counts(uts, "placement_role"),
        "gateway_roles": _role_counts(gateways, "gateway_role"),
        "zone_roles": _role_counts(zones, "service_role"),
        "active_service_zone_count": len(active_zones),
        "latitude_window_deg": [round(min(latitudes), 3), round(max(latitudes), 3)],
        "longitude_window_deg": [round(min(longitudes), 3), round(max(longitudes), 3)],
        "ut_to_default_gateway_distance_km": _stat(default_distances),
        "ut_to_secondary_gateway_distance_km": _stat(secondary_distances),
    }


def _stat(values: Sequence[float]) -> Dict[str, float]:
    if not values:
        return {"min": 0.0, "avg": 0.0, "max": 0.0}
    return {
        "min": round(min(values), 3),
        "avg": round(sum(values) / len(values), 3),
        "max": round(max(values), 3),
    }


def _planned_shell_profiles() -> List[Dict[str, Any]]:
    profiles: List[Dict[str, Any]] = []
    for shell_id, preset in sorted(PLANNED_STARLINK_PHASE1_SHELL_PRESETS.items()):
        profiles.append(
            {
                "shell_id": shell_id,
                "plane_count": preset.plane_count,
                "satellites_per_plane": preset.satellites_per_plane,
                "satellite_count": preset.satellite_count,
                "altitude_km": preset.altitude_km,
                "inclination_deg": preset.inclination_deg,
                "suggested_usage": SHELL_USAGE_BY_ID.get(
                    shell_id,
                    "planned-shell profile available; usage needs follow-up inspection",
                ),
            }
        )
    return profiles


def _staged_acceptance_plan() -> Dict[str, Any]:
    return {
        "current_preview_shell_id": "S1",
        "current_preview_role": "high_pressure_upper_tier_cardinality_estimate",
        "immediate_next_real_runtime_target": "intermediate_shell_first_not_s1",
        "canonical_runtime_remains": "S5 172-satellite + 6-zone metadata",
        "tiers": [
            {
                "tier": 0,
                "shell_ids": ["S5"],
                "satellite_scale": "172",
                "purpose": "canonical baseline and BPF experimental backend A/B",
            },
            {
                "tier": 1,
                "shell_ids": ["S4", "S3"],
                "satellite_scale": "300+ preferred, 700+ fallback",
                "purpose": (
                    "next larger-shell coverage/cardinality preview and real-runtime "
                    "acceptance candidate"
                ),
                "promotion_rule": (
                    "try S4 first; move to S3 only if required-zone coverage or "
                    "availability is insufficient"
                ),
            },
            {
                "tier": 2,
                "shell_ids": ["S1", "S2"],
                "satellite_scale": "1000+",
                "purpose": "final pressure/stress target, not first larger-shell runtime target",
            },
        ],
    }


def analyze_workload(
    *,
    catalogs: Dict[str, List[Dict[str, Any]]],
    shell_id: str,
    start_time: datetime,
    horizon_seconds: float,
    step_seconds: float,
    min_elevation_deg: float,
) -> Dict[str, Any]:
    preset = planned_shell_preset(shell_id)
    records = generate_planned_shell_records(shell_id=preset.shell_id)
    slice_count = int(math.floor(horizon_seconds / step_seconds)) + 1
    propagated_slices = propagate_planned_shell_records(
        records,
        start_time=start_time,
        step_seconds=step_seconds,
        slice_count=slice_count,
        slice_id_prefix="goal18-realistic-preview-slice",
    )
    anchor_rows = _best_anchor_by_zone(
        zones=catalogs["zones"],
        propagated_slices=propagated_slices,
        min_elevation_deg=min_elevation_deg,
    )
    dwell_by_zone = {
        zone_id: _dwell_summary(rows, step_seconds=step_seconds)
        for zone_id, rows in sorted(anchor_rows.items())
    }
    zones_by_id = {str(zone["zone_id"]): zone for zone in catalogs["zones"]}
    required_zone_ids = [
        zone_id
        for zone_id, zone in zones_by_id.items()
        if str(zone.get("service_role")) == "required_service"
    ]
    optional_zone_ids = [
        zone_id
        for zone_id, zone in zones_by_id.items()
        if str(zone.get("service_role")) == "optional_service"
    ]
    observation_zone_ids = [
        zone_id
        for zone_id, zone in zones_by_id.items()
        if str(zone.get("service_role")) == "observation"
    ]
    active_zone_ids = required_zone_ids + optional_zone_ids
    gateway_classes_by_zone = {
        zone_id: 1 + (1 if zones_by_id[zone_id].get("secondary_gateway") else 0)
        for zone_id in active_zone_ids
    }
    total_gateway_classes = sum(gateway_classes_by_zone.values())
    ut_by_zone: Dict[str, List[str]] = {}
    for ut in catalogs["uts"]:
        ut_by_zone.setdefault(str(ut["zone_id"]), []).append(str(ut["ut_id"]))

    runtime_transition_rows: List[Dict[str, Any]] = []
    interval_count = max(0, slice_count - 1)
    for index in range(1, interval_count):
        changed_zones = []
        affected_nodes = set()
        for zone_id in active_zone_ids:
            previous_anchor = anchor_rows[zone_id][index - 1].get("anchor_satellite_id")
            current_anchor = anchor_rows[zone_id][index].get("anchor_satellite_id")
            if previous_anchor == current_anchor:
                continue
            changed_zones.append(zone_id)
            affected_nodes.update(ut_by_zone.get(zone_id, []))
            zone = zones_by_id[zone_id]
            if zone.get("default_gateway"):
                affected_nodes.add(str(zone["default_gateway"]))
            if zone.get("secondary_gateway"):
                affected_nodes.add(str(zone["secondary_gateway"]))
            if previous_anchor:
                affected_nodes.add(str(previous_anchor))
            if current_anchor:
                affected_nodes.add(str(current_anchor))
        if not changed_zones:
            continue
        changed_gateway_classes = sum(gateway_classes_by_zone[zone_id] for zone_id in changed_zones)
        runtime_transition_rows.append(
            {
                "transition_index": index,
                "changed_zone_count": len(changed_zones),
                "changed_gateway_class_count": changed_gateway_classes,
                "changed_g_fec_count": changed_gateway_classes,
                "skipped_g_fec_count": max(0, total_gateway_classes - changed_gateway_classes),
                "changed_gz_fec_count": changed_gateway_classes,
                "skipped_gz_fec_count": max(0, total_gateway_classes - changed_gateway_classes),
                "affected_node_count": len(affected_nodes),
            }
        )

    access_edge_count = len(catalogs["uts"]) + len(catalogs["gateways"])
    fixed_isl_edge_count = preset.satellite_count
    cross_plane_edge_count = max(0, (preset.plane_count - 1) * preset.satellites_per_plane)
    active_edge_count = access_edge_count + fixed_isl_edge_count + cross_plane_edge_count
    changed_parameter_link_estimate = (
        access_edge_count
        + math.ceil(fixed_isl_edge_count * 0.04)
        + math.ceil(cross_plane_edge_count * 0.10)
    )
    default_flow_count = sum(1 for ut in catalogs["uts"] if not ut.get("uses_prefix_override"))
    override_flow_count = sum(1 for ut in catalogs["uts"] if ut.get("uses_prefix_override"))
    required_unresolved = [
        dwell_by_zone[zone_id]["unresolved_duration_seconds"]
        for zone_id in required_zone_ids
    ]
    all_dwell_values = [
        float(summary["dwell_seconds"]["avg"])
        for summary in dwell_by_zone.values()
        if float(summary["dwell_seconds"]["avg"]) > 0.0
    ]
    return {
        "goal": "Skyseed Goal 18 realistic workload and catalog preparation",
        "preview_only": True,
        "canonical_runtime_changed": False,
        "real_runtime_executed": False,
        "shell_profile": {
            "shell_id": preset.shell_id,
            "plane_count": preset.plane_count,
            "satellites_per_plane": preset.satellites_per_plane,
            "satellite_count": preset.satellite_count,
            "altitude_km": preset.altitude_km,
            "inclination_deg": preset.inclination_deg,
            "preview_role": "high_pressure_upper_tier_cardinality_estimate",
            "immediate_next_real_runtime_target": False,
        },
        "planned_shell_profiles": _planned_shell_profiles(),
        "staged_shell_acceptance_plan": _staged_acceptance_plan(),
        "sampling": {
            "start_time_utc": start_time.isoformat().replace("+00:00", "Z"),
            "horizon_seconds": horizon_seconds,
            "step_seconds": step_seconds,
            "slice_count": slice_count,
            "min_elevation_deg": min_elevation_deg,
        },
        "catalog_counts": {
            "ut_count": len(catalogs["uts"]),
            "gateway_count": len(catalogs["gateways"]),
            "zone_count": len(catalogs["zones"]),
            "required_zone_count": len(required_zone_ids),
            "optional_zone_count": len(optional_zone_ids),
            "observation_zone_count": len(observation_zone_ids),
        },
        "distribution": _distribution_summary(catalogs),
        "coverage": {
            "required_unresolved_duration_seconds": {
                "max": max(required_unresolved) if required_unresolved else 0.0,
                "per_zone": {
                    zone_id: dwell_by_zone[zone_id]["unresolved_duration_seconds"]
                    for zone_id in required_zone_ids
                },
            },
            "zone_anchor_dwell_seconds": {
                "avg_of_zone_avg": round(sum(all_dwell_values) / len(all_dwell_values), 6) if all_dwell_values else 0.0,
                "per_zone": {
                    zone_id: dwell_by_zone[zone_id]["dwell_seconds"]
                    for zone_id in sorted(dwell_by_zone)
                },
            },
            "zone_availability_ratio": {
                zone_id: dwell_by_zone[zone_id]["availability_ratio"]
                for zone_id in sorted(dwell_by_zone)
            },
            "anchor_change_count": {
                zone_id: dwell_by_zone[zone_id]["anchor_change_count"]
                for zone_id in sorted(dwell_by_zone)
            },
        },
        "update_cardinality_estimate": {
            "active_edge_count_estimate": active_edge_count,
            "access_edge_count": access_edge_count,
            "fixed_isl_edge_count": fixed_isl_edge_count,
            "cross_plane_edge_count": cross_plane_edge_count,
            "changed_parameter_link_estimate_per_parameter_epoch": changed_parameter_link_estimate,
            "qdisc_update_estimate_per_parameter_epoch": changed_parameter_link_estimate,
            "qdisc_update_full_refresh_upper_bound": active_edge_count,
            "runtime_epoch_estimate_count": len(runtime_transition_rows),
            "affected_nodes_estimate_per_runtime_epoch": _stat(
                [float(row["affected_node_count"]) for row in runtime_transition_rows]
            ),
            "changed_g_fec_estimate": _stat(
                [float(row["changed_g_fec_count"]) for row in runtime_transition_rows]
            ),
            "skipped_g_fec_estimate": _stat(
                [float(row["skipped_g_fec_count"]) for row in runtime_transition_rows]
            ),
            "changed_gz_fec_estimate": _stat(
                [float(row["changed_gz_fec_count"]) for row in runtime_transition_rows]
            ),
            "skipped_gz_fec_estimate": _stat(
                [float(row["skipped_gz_fec_count"]) for row in runtime_transition_rows]
            ),
            "cold_start_changed_g_fec_estimate": total_gateway_classes,
            "cold_start_changed_gz_fec_estimate": total_gateway_classes,
            "default_gateway_flow_count": default_flow_count,
            "override_flow_count": override_flow_count,
            "gateway_class_count": total_gateway_classes,
        },
        "default_secondary_gateway_plan": {
            "major_region_count": len({gateway["region"] for gateway in catalogs["gateways"]}),
            "default_gateway_count": sum(1 for gateway in catalogs["gateways"] if gateway.get("gateway_role") == "default"),
            "secondary_gateway_count": sum(1 for gateway in catalogs["gateways"] if gateway.get("gateway_role") == "secondary"),
            "prefix_override_count": sum(len(zone.get("prefix_overrides", [])) for zone in catalogs["zones"]),
            "active_zone_gateway_classes": total_gateway_classes,
        },
        "runtime_entry_blockers": [
            "preview catalog has not been validated against SEED Mini Internet IX capacity for 12 gateways",
            "coverage and update cardinality are sampled estimates, not real-runtime timing evidence",
            "observation zones intentionally have no service UTs and must stay out of required runtime gates",
            "S1 cardinality is an upper-tier pressure estimate; next real-runtime should start with an intermediate shell",
            "staged S4/S3 larger-shell A/B criteria are needed before any S1 pressure run or canonical promotion",
        ],
    }


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate the Goal 18 realistic Skyseed preview workload catalog and compact summary."
    )
    parser.add_argument("--shell-id", default="S1", help="Planned shell preset used for preview analysis.")
    parser.add_argument("--horizon-seconds", type=float, default=7200.0)
    parser.add_argument("--step-seconds", type=float, default=300.0)
    parser.add_argument("--min-elevation-deg", type=float, default=10.0)
    parser.add_argument(
        "--start-time-utc",
        default="2026-01-01T00:00:00Z",
        help="Preview sampling start time in UTC.",
    )
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--no-write", action="store_true", help="Print summary without writing catalog files.")
    return parser.parse_args()


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def main() -> int:
    args = parse_args()
    catalogs = build_catalogs()
    summary = analyze_workload(
        catalogs=catalogs,
        shell_id=str(args.shell_id),
        start_time=_parse_utc(str(args.start_time_utc)),
        horizon_seconds=float(args.horizon_seconds),
        step_seconds=float(args.step_seconds),
        min_elevation_deg=float(args.min_elevation_deg),
    )
    output_dir = Path(args.output_dir).resolve()
    paths = {
        "gateway_catalog": output_dir / "realistic_preview_gateway_catalog_12.json",
        "ut_catalog": output_dir / "realistic_preview_ut_catalog_32.json",
        "zone_catalog": output_dir / "realistic_preview_zone_catalog_16.json",
        "summary": output_dir / "realistic_preview_workload_summary.json",
    }
    if not args.no_write:
        _write_json(paths["gateway_catalog"], catalogs["gateways"])
        _write_json(paths["ut_catalog"], catalogs["uts"])
        _write_json(paths["zone_catalog"], catalogs["zones"])
        _write_json(paths["summary"], summary)
    print(
        json.dumps(
            {
                "preview_only": True,
                "shell_id": summary["shell_profile"]["shell_id"],
                "catalog_counts": summary["catalog_counts"],
                "distribution": summary["distribution"],
                "coverage": {
                    "required_unresolved_max_seconds": summary["coverage"][
                        "required_unresolved_duration_seconds"
                    ]["max"],
                    "anchor_dwell_avg_seconds": summary["coverage"][
                        "zone_anchor_dwell_seconds"
                    ]["avg_of_zone_avg"],
                },
                "staged_acceptance": {
                    "current_preview_role": summary["staged_shell_acceptance_plan"][
                        "current_preview_role"
                    ],
                    "immediate_next_real_runtime_target": summary[
                        "staged_shell_acceptance_plan"
                    ]["immediate_next_real_runtime_target"],
                },
                "update_cardinality_estimate": summary["update_cardinality_estimate"],
                "paths": {key: str(path) for key, path in paths.items()} if not args.no_write else {},
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
