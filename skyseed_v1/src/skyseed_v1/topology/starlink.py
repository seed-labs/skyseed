#!/usr/bin/env python3
# encoding: utf-8

"""Minimal Starlink-shell topology construction and preview pipeline helpers."""

from __future__ import annotations

import json
import math
import ipaddress
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from skyseed_v1.topology.experiment import (
    EARTH_RADIUS_KM,
    ExperimentGatewayProfile,
    ExperimentNodePosition,
    ExperimentPrefixOverridePolicy,
    ExperimentScenario,
    ExperimentTerminalProfile,
    ExperimentTimeSlice,
    ExperimentTopologySlicer,
    ExperimentZoneProfile,
    FrozenVisualizationLinkVisibilityModel,
    GroundLocation,
    SemiRealLineOfSightVisibilityModel,
    ZoneAnchorQualityGenerator,
    _scenario_candidate_links,
    _scenario_planner_hints,
    _topology_entry_by_id,
)
from skyseed_v1.models.ground import DEFAULT_GATEWAY_CATALOG, DEFAULT_UT_CATALOG
from skyseed_v1.adapters.seedemu.integration import (
    EXTERNAL_TEST_TARGETS,
    MINI_INTERNET_HOSTS_PER_STUB_AS,
    MINI_INTERNET_IX_DISPLAY_NAMES,
    MINI_INTERNET_STUB_AS_EXCHANGES,
    MINI_INTERNET_TRANSIT_AS_LAYOUT,
    SATELLITE_ASN,
    SATELLITE_OPERATOR_ASN,
    build_mini_internet,
    enable_mpls_readiness,
    resolve_platform,
    resolve_ut_identity_address,
)
from seedemu.compiler import Docker
from seedemu.core import Emulator
from seedemu.layers import Base, Ebgp, Ibgp, Ospf, PeerRelationship, Routing
from skyseed_v1.topology.shell import PropagatedOrbitSlice, ShellSelectionResult, satellite_node_id
from skyseed_v1.topology.pipeline import (
    OrbitTopologyProducer,
    OrbitTopologySlice,
    TopologySnapshotPipeline,
    ZoneGenerationInput,
)


DEFAULT_STARLINK_PREVIEW_GROUND_LOCATIONS = {
    "ut1": GroundLocation(latitude_deg=42.36, longitude_deg=-71.06),
    "ut2": GroundLocation(latitude_deg=39.95, longitude_deg=-75.16),
    "gw1": GroundLocation(latitude_deg=40.71, longitude_deg=-74.00),
    "gw2": GroundLocation(latitude_deg=40.00, longitude_deg=-69.00),
}

STARLINK_MINI_INTERNET_TRANSIT_ASNS = (2, 3, 4, 11, 12)
STARLINK_MINI_INTERNET_STUB_ASNS = (154, 160)

SKYSEED_STARTUP_PROFILE_SATELLITE = "skyseed_satellite_mpls_only"
SKYSEED_STARTUP_PROFILE_UT_ENDPOINT = "skyseed_ut_endpoint"
SKYSEED_STARTUP_PROFILE_UT_PLACEHOLDER = "skyseed_ut_placeholder_carrier_only"
SKYSEED_STARTUP_PROFILE_GATEWAY = "seed_router_with_gateway_uplink_routing"
SKYSEED_STARTUP_PROFILE_MINI_INTERNET = "seed_mini_internet_default"

SKYSEED_NODE_STARTUP_PROFILES = {
    "satellite": SKYSEED_STARTUP_PROFILE_SATELLITE,
    "user_terminal": SKYSEED_STARTUP_PROFILE_UT_ENDPOINT,
    "ut_placeholder_router": SKYSEED_STARTUP_PROFILE_UT_PLACEHOLDER,
    "gateway_ground_station": SKYSEED_STARTUP_PROFILE_GATEWAY,
    "mini_internet_router_or_host": SKYSEED_STARTUP_PROFILE_MINI_INTERNET,
}

SKYSEED_NODE_ROLE_FRAMEWORK = {
    "version": "skyseed-node-role-framework-v1",
    "roles": {
        "satellite": {
            "control_plane_model": "skyseed_timed_update_agent",
            "dataplane_model": "mpls_direct_runtime_links",
            "startup_profile": SKYSEED_STARTUP_PROFILE_SATELLITE,
            "routing_daemon_required": True,
            "notes": (
                "Uses the proven router-style startup path today while Skyseed keeps "
                "runtime link mutation and timed dataplane control."
            ),
        },
        "user_terminal": {
            "control_plane_model": "endpoint",
            "dataplane_model": "host_endpoint",
            "startup_profile": SKYSEED_STARTUP_PROFILE_UT_ENDPOINT,
            "routing_daemon_required": False,
        },
        "ut_placeholder_router": {
            "control_plane_model": "carrier_only_bootstrap",
            "dataplane_model": "compile_time_container_carrier",
            "startup_profile": SKYSEED_STARTUP_PROFILE_UT_PLACEHOLDER,
            "routing_daemon_required": False,
        },
        "gateway_ground_station": {
            "control_plane_model": "border_router",
            "dataplane_model": "mini_internet_gateway_border",
            "startup_profile": SKYSEED_STARTUP_PROFILE_GATEWAY,
            "routing_daemon_required": True,
        },
        "mini_internet_router_or_host": {
            "control_plane_model": "seed_default",
            "dataplane_model": "seed_default",
            "startup_profile": SKYSEED_STARTUP_PROFILE_MINI_INTERNET,
            "routing_daemon_required": True,
        },
    },
}



def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _subnet_octets(base_second_octet: int, counter: int) -> Tuple[int, int]:
    second_octet = base_second_octet + (counter // 256)
    third_octet = counter % 256
    if second_octet >= 255:
        raise ValueError("subnet counter overflowed the available private address range")
    return second_octet, third_octet


def _prefix(base_second_octet: int, counter: int, prefix_length: int) -> str:
    second_octet, third_octet = _subnet_octets(base_second_octet, counter)
    return f"10.{second_octet}.{third_octet}.0/{prefix_length}"


def _host_ip(base_second_octet: int, counter: int, host_octet: int, prefix_length: int) -> str:
    second_octet, third_octet = _subnet_octets(base_second_octet, counter)
    return f"10.{second_octet}.{third_octet}.{host_octet}/{prefix_length}"


def _host_address(base_second_octet: int, counter: int, host_octet: int) -> str:
    second_octet, third_octet = _subnet_octets(base_second_octet, counter)
    return f"10.{second_octet}.{third_octet}.{host_octet}"


SATELLITE_RUNTIME_MGMT_NETWORK_NAME = "skyseed-sat-runtime-mgmt"
SATELLITE_RUNTIME_MGMT_SUBNET = "10.240.0.0/16"


def _subnet_host_address(subnet: str, host_offset: int) -> str:
    network = ipaddress.ip_network(subnet, strict=False)
    if network.version != 4:
        raise ValueError(f"unsupported non-IPv4 subnet: {subnet}")
    max_offset = int(network.num_addresses) - 2
    if int(host_offset) < 1 or int(host_offset) > max_offset:
        raise ValueError(
            f"host offset {host_offset} does not fit inside subnet {subnet}"
        )
    return str(network.network_address + int(host_offset))


def _subnet_host_ip(subnet: str, host_offset: int) -> str:
    network = ipaddress.ip_network(subnet, strict=False)
    return f"{_subnet_host_address(subnet, host_offset)}/{network.prefixlen}"


def _fixed_shell_network_name(link_index: int) -> str:
    return f"fs{int(link_index):04d}"


def _gateway_bootstrap_network_name(gateway_id: str) -> str:
    return f"{gateway_id}-bootstrap"


def _shift_ix_ip_address(ip_address: str, ix_id_offset: int) -> str:
    if int(ix_id_offset) == 0:
        return str(ip_address)
    interface = ipaddress.ip_interface(str(ip_address))
    octets = str(interface.ip).split(".")
    if len(octets) != 4:
        raise ValueError(f"unsupported non-IPv4 ix_ip_address: {ip_address}")
    shifted_second_octet = int(octets[1]) + int(ix_id_offset)
    shifted_ip = ipaddress.ip_address(
        ".".join([octets[0], str(shifted_second_octet), octets[2], octets[3]])
    )
    if interface.network.prefixlen == 32:
        return str(shifted_ip)
    return f"{shifted_ip}/{interface.network.prefixlen}"


def _shift_gateway_definitions_for_ix_offset(
    gateway_definitions: Sequence[Dict[str, Any]],
    *,
    ix_id_offset: int,
) -> List[Dict[str, Any]]:
    if int(ix_id_offset) == 0:
        return [dict(entry) for entry in gateway_definitions]
    shifted_entries: List[Dict[str, Any]] = []
    for entry in gateway_definitions:
        shifted = dict(entry)
        shifted["ix_id"] = int(entry["ix_id"]) + int(ix_id_offset)
        shifted["ix_ip_address"] = _shift_ix_ip_address(
            str(entry["ix_ip_address"]),
            int(ix_id_offset),
        )
        shifted_entries.append(shifted)
    return shifted_entries


def _gateway_transit_ixs_for_mini_internet(
    gateway_definitions: Sequence[Dict[str, Any]],
    *,
    ix_id_offset: int,
) -> Dict[int, List[int]]:
    transit_ixs: Dict[int, set[int]] = {}
    for definition in gateway_definitions:
        upstream_asn = int(definition["upstream_asn"])
        raw_ix_id = int(definition["ix_id"]) - int(ix_id_offset)
        transit_ixs.setdefault(upstream_asn, set()).add(raw_ix_id)
    return {
        asn: sorted(ix_ids)
        for asn, ix_ids in sorted(transit_ixs.items())
    }


def _mini_internet_exchange_records(
    gateway_definitions: Sequence[Dict[str, Any]],
    *,
    ix_id_offset: int,
) -> List[Dict[str, Any]]:
    ix_ids = {
        int(ix_id) + int(ix_id_offset)
        for ix_id in MINI_INTERNET_IX_DISPLAY_NAMES
    }
    ix_ids.update(int(definition["ix_id"]) for definition in gateway_definitions)
    return [
        {
            "ix_id": ix_id,
            "network_name": f"ix{ix_id}",
            "display_name": str(
                MINI_INTERNET_IX_DISPLAY_NAMES.get(
                    ix_id - int(ix_id_offset),
                    f"Gateway-IX-{ix_id}",
                )
            ),
        }
        for ix_id in sorted(ix_ids)
    ]


def _strip_prefix(ip_address: str) -> str:
    return str(ipaddress.ip_interface(str(ip_address)).ip)


def _zone_return_prefixes_from_ut_slots(
    *,
    terminal_profiles: Sequence[ExperimentTerminalProfile],
    slot_inventory: Dict[str, Any],
) -> Dict[str, List[str]]:
    zone_by_ut_id = {
        str(terminal.ut_id): str(terminal.zone_id)
        for terminal in terminal_profiles
    }
    addresses_by_zone: Dict[str, List[ipaddress.IPv4Address]] = {}
    for slot in list(slot_inventory.get("ut_access", [])):
        ut_id = str(slot.get("ut_id") or "")
        zone_id = zone_by_ut_id.get(ut_id)
        if not zone_id:
            continue
        endpoint = dict(slot.get("preconnected_endpoint", {}))
        ip_address = _strip_prefix(str(endpoint.get("ip_address") or ""))
        addresses_by_zone.setdefault(zone_id, []).append(
            ipaddress.ip_address(ip_address)
        )
    prefixes_by_zone: Dict[str, List[str]] = {}
    for zone_id, addresses in sorted(addresses_by_zone.items()):
        unique_addresses = sorted(set(addresses))
        if len(unique_addresses) == 1:
            prefixes_by_zone[zone_id] = [f"{unique_addresses[0]}/32"]
            continue
        first_address = int(unique_addresses[0])
        last_address = int(unique_addresses[-1])
        common_prefix_len = 32 - (first_address ^ last_address).bit_length()
        prefixes_by_zone[zone_id] = [
            str(
                ipaddress.ip_network(
                    f"{unique_addresses[0]}/{common_prefix_len}",
                    strict=False,
                )
            )
        ]
    return prefixes_by_zone


def _bootstrap_inventory(
    *,
    node_id: str,
    node_type: str,
    network_name: str,
    subnet: str,
    ip_address: str,
    purpose: str,
) -> Dict[str, Any]:
    return {
        "network_name": network_name,
        "docker_network_name": network_name,
        "subnet": subnet,
        "purpose": purpose,
        "endpoint": {
            "node_id": node_id,
            "node_type": node_type,
            "network_name": network_name,
            "docker_network_name": network_name,
            "interface_name": network_name,
            "ip_address": ip_address,
        },
    }


def _endpoint_record(
    *,
    node_id: str,
    node_type: str,
    interface_name: str,
    ip_address: str,
    logical_direction: str | None = None,
) -> Dict[str, Any]:
    record: Dict[str, Any] = {
        "node_id": node_id,
        "node_type": node_type,
        "interface_name": interface_name,
        "ip_address": ip_address,
    }
    if logical_direction is not None:
        record["logical_direction"] = logical_direction
    return record


def _satellite_index_in_name(node_id: str) -> int:
    return int(node_id.rsplit("-", 1)[-1])


def _gmst_deg(timestamp: datetime) -> float:
    utc_value = timestamp.astimezone(timezone.utc)
    unix_seconds = utc_value.timestamp()
    julian_day = (unix_seconds / 86400.0) + 2440587.5
    centuries = (julian_day - 2451545.0) / 36525.0
    gmst = (
        280.46061837
        + 360.98564736629 * (julian_day - 2451545.0)
        + 0.000387933 * (centuries**2)
        - (centuries**3) / 38710000.0
    )
    return gmst % 360.0


def _ground_position_inertial(
    location: GroundLocation,
    *,
    timestamp: datetime,
) -> ExperimentNodePosition:
    latitude_rad = math.radians(location.latitude_deg)
    theta_rad = math.radians((_gmst_deg(timestamp) + location.longitude_deg) % 360.0)
    radius_km = EARTH_RADIUS_KM + location.altitude_km
    return ExperimentNodePosition(
        x_km=radius_km * math.cos(latitude_rad) * math.cos(theta_rad),
        y_km=radius_km * math.cos(latitude_rad) * math.sin(theta_rad),
        z_km=radius_km * math.sin(latitude_rad),
    )


def _ground_subpoint_from_inertial(
    position: ExperimentNodePosition,
    *,
    timestamp: datetime,
) -> GroundLocation:
    radius_km = math.sqrt(
        position.x_km**2 + position.y_km**2 + position.z_km**2
    )
    latitude_deg = math.degrees(
        math.asin(max(-1.0, min(1.0, position.z_km / radius_km)))
    )
    inertial_longitude_deg = math.degrees(math.atan2(position.y_km, position.x_km))
    longitude_deg = ((inertial_longitude_deg - _gmst_deg(timestamp) + 540.0) % 360.0) - 180.0
    return GroundLocation(
        latitude_deg=round(latitude_deg, 6),
        longitude_deg=round(longitude_deg, 6),
        altitude_km=0.0,
    )


def _offset_ground_location(
    base: GroundLocation,
    *,
    north_km: float = 0.0,
    east_km: float = 0.0,
) -> GroundLocation:
    latitude_deg = float(base.latitude_deg) + (float(north_km) / 111.0)
    bounded_latitude = max(-85.0, min(85.0, latitude_deg))
    cos_latitude = max(0.2, math.cos(math.radians(bounded_latitude)))
    longitude_deg = (
        float(base.longitude_deg) + (float(east_km) / (111.0 * cos_latitude))
    )
    wrapped_longitude = ((longitude_deg + 540.0) % 360.0) - 180.0
    return GroundLocation(
        latitude_deg=round(bounded_latitude, 6),
        longitude_deg=round(wrapped_longitude, 6),
        altitude_km=float(base.altitude_km),
    )


def derive_starlink_preview_ground_locations(
    propagated_slice: PropagatedOrbitSlice,
    *,
    ut_ids: Sequence[str] = ("ut1", "ut2"),
    gateway_ids: Sequence[str] = ("gw1", "gw2"),
) -> Dict[str, GroundLocation]:
    ordered_states = sorted(
        propagated_slice.satellite_states.values(),
        key=lambda state: (state.norad_id, state.satellite_node_id),
    )
    if not ordered_states:
        return dict(DEFAULT_STARLINK_PREVIEW_GROUND_LOCATIONS)
    timestamp = datetime.fromisoformat(
        propagated_slice.timestamp_utc.replace("Z", "+00:00")
    )
    if not ut_ids and not gateway_ids:
        return dict(DEFAULT_STARLINK_PREVIEW_GROUND_LOCATIONS)
    locations: Dict[str, GroundLocation] = {}
    derived_ut_locations: List[GroundLocation] = []
    for index, node_id in enumerate(ut_ids):
        state_index = round(index * (len(ordered_states) - 1) / max(1, len(ut_ids) - 1))
        state = ordered_states[state_index]
        location = _ground_subpoint_from_inertial(
            ExperimentNodePosition(
                x_km=state.x_km,
                y_km=state.y_km,
                z_km=state.z_km,
            ),
            timestamp=timestamp,
        )
        derived_ut_locations.append(location)
        locations[str(node_id)] = location
    if not derived_ut_locations:
        for index, node_id in enumerate(gateway_ids):
            state_index = round(index * (len(ordered_states) - 1) / max(1, len(gateway_ids) - 1))
            state = ordered_states[state_index]
            locations[str(node_id)] = _ground_subpoint_from_inertial(
                ExperimentNodePosition(
                    x_km=state.x_km,
                    y_km=state.y_km,
                    z_km=state.z_km,
                ),
                timestamp=timestamp,
        )
        return locations
    for index, node_id in enumerate(gateway_ids):
        locations[str(node_id)] = derived_ut_locations[index % len(derived_ut_locations)]
    return locations


def derive_starlink_coordinate_consistent_ground_model(
    propagated_slice: PropagatedOrbitSlice,
    *,
    ut_definitions: Sequence[Dict[str, Any]],
    gateway_definitions: Sequence[Dict[str, Any]],
    zone_definitions: Sequence[Dict[str, Any]],
    coordinate_policy: str = "shell_aware_consistent",
) -> Dict[str, Any]:
    """Build a parameterized preview ground model with zone/UT/GW coordinates aligned."""

    policy = str(coordinate_policy or "shell_aware_consistent").strip()
    if policy not in {
        "real_geographic",
        "shell_aware_consistent",
        "synthetic_shell_aware",
    }:
        raise ValueError(f"unsupported coordinate policy: {coordinate_policy}")
    if policy == "real_geographic":
        return {
            "ground_locations": _ground_locations_from_catalogs(
                ut_definitions=ut_definitions,
                gateway_definitions=gateway_definitions,
            ),
            "ut_definitions": [
                {
                    **dict(entry),
                    "catalog_coordinates": {
                        "latitude_deg": entry.get("latitude_deg"),
                        "longitude_deg": entry.get("longitude_deg"),
                        "altitude_km": entry.get("altitude_km", 0.0),
                    },
                    "effective_coordinates": {
                        "latitude_deg": entry.get("latitude_deg"),
                        "longitude_deg": entry.get("longitude_deg"),
                        "altitude_km": entry.get("altitude_km", 0.0),
                    },
                    "zone_membership_semantics": (
                        "workload_probe_affinity_not_zone_definition"
                    ),
                }
                for entry in ut_definitions
            ],
            "gateway_definitions": [
                {
                    **dict(entry),
                    "catalog_coordinates": {
                        "latitude_deg": entry.get("latitude_deg"),
                        "longitude_deg": entry.get("longitude_deg"),
                        "altitude_km": entry.get("altitude_km", 0.0),
                    },
                    "effective_coordinates": {
                        "latitude_deg": entry.get("latitude_deg"),
                        "longitude_deg": entry.get("longitude_deg"),
                        "altitude_km": entry.get("altitude_km", 0.0),
                    },
                    "coordinate_source": "real_catalog",
                    "provenance": {
                        "source": entry.get("source"),
                        "version": entry.get("version", "unversioned_catalog"),
                        "synthetic": bool(entry.get("synthetic", False)),
                    },
                }
                for entry in gateway_definitions
            ],
            "zone_definitions": [
                {
                    **dict(entry),
                    "catalog_coordinates": {
                        "latitude_deg": entry.get("latitude_deg"),
                        "longitude_deg": entry.get("longitude_deg"),
                        "altitude_km": entry.get("altitude_km", 0.0),
                    },
                    "effective_coordinates": {
                        "latitude_deg": entry.get("latitude_deg"),
                        "longitude_deg": entry.get("longitude_deg"),
                        "altitude_km": entry.get("altitude_km", 0.0),
                    },
                    "zone_generation_policy": {
                        "policy": "real_geographic",
                        "zone_semantics": "service_region_not_defined_by_uts",
                    },
                }
                for entry in zone_definitions
            ],
            "coordinate_semantics": {
                "mode": "real_geographic",
                "original_catalog_coordinates": "latitude_deg/longitude_deg in input catalogs",
                "effective_runtime_coordinates": "same as catalog coordinates",
                "coverage_coordinates": "effective_runtime_coordinates",
                "zone_anchor_coordinates": "effective_runtime_coordinates",
                "ut_attach_coordinates": "effective_runtime_coordinates",
                "gw_selection_coordinates": "effective_runtime_coordinates",
                "gz_fec_coordinates": "effective_runtime_coordinates",
                "mixing_relocated_ground_with_fixed_zone_centers": False,
            },
        }

    ordered_states = sorted(
        propagated_slice.satellite_states.values(),
        key=lambda state: (state.norad_id, state.satellite_node_id),
    )
    if not ordered_states:
        return {
            "ground_locations": _ground_locations_from_catalogs(
                ut_definitions=ut_definitions,
                gateway_definitions=gateway_definitions,
            ),
            "ut_definitions": [dict(entry) for entry in ut_definitions],
            "gateway_definitions": [dict(entry) for entry in gateway_definitions],
            "zone_definitions": [dict(entry) for entry in zone_definitions],
            "coordinate_semantics": {
                "mode": "catalog_fallback_no_satellite_states",
                "coverage_coordinates": "effective_runtime_coordinates",
                "zone_anchor_coordinates": "effective_runtime_coordinates",
                "ut_attach_coordinates": "effective_runtime_coordinates",
                "gw_selection_coordinates": "effective_runtime_coordinates",
                "gz_fec_coordinates": "effective_runtime_coordinates",
                "mixing_relocated_ground_with_fixed_zone_centers": False,
            },
        }

    timestamp = datetime.fromisoformat(
        propagated_slice.timestamp_utc.replace("Z", "+00:00")
    )
    zones = [dict(entry) for entry in zone_definitions]
    uts = [dict(entry) for entry in ut_definitions]
    gateways = [dict(entry) for entry in gateway_definitions]
    ut_by_id = {str(entry["ut_id"]): dict(entry) for entry in uts}
    gateway_by_id = {str(entry["gateway_id"]): dict(entry) for entry in gateways}
    locations: Dict[str, GroundLocation] = {}
    effective_zones: List[Dict[str, Any]] = []
    gateway_anchor_locations: Dict[str, GroundLocation] = {}
    gateway_primary_zone: Dict[str, str] = {}
    zone_reuse_counts: Dict[str, int] = {}

    ordered_zones = sorted(
        zones,
        key=lambda entry: (
            0 if bool(entry.get("required_for_runtime", False)) else 1,
            0 if bool(entry.get("active_service", True)) else 1,
            str(entry.get("zone_id")),
        ),
    )
    anchor_count = max(1, len(ordered_zones))

    def state_subpoint(anchor_index: int) -> GroundLocation:
        state_index = round(anchor_index * (len(ordered_states) - 1) / max(1, anchor_count - 1))
        state = ordered_states[state_index]
        return _ground_subpoint_from_inertial(
            ExperimentNodePosition(
                x_km=state.x_km,
                y_km=state.y_km,
                z_km=state.z_km,
            ),
            timestamp=timestamp,
        )

    def gateway_ids_for_zone(zone: Dict[str, Any]) -> List[str]:
        ids: List[str] = []
        for gateway_id in list(zone.get("coordinate_gateway_ids", [])):
            gateway_id = str(gateway_id).strip()
            if gateway_id and gateway_id not in ids:
                ids.append(gateway_id)
        if ids:
            return [gateway_id for gateway_id in ids if gateway_id in gateway_by_id]
        for key in ("default_gateway", "secondary_gateway"):
            gateway_id = str(zone.get(key) or "").strip()
            if gateway_id and gateway_id not in ids:
                ids.append(gateway_id)
        for gateway_id in list(zone.get("preferred_gateway_ids", [])):
            gateway_id = str(gateway_id).strip()
            if gateway_id and gateway_id not in ids:
                ids.append(gateway_id)
        return [gateway_id for gateway_id in ids if gateway_id in gateway_by_id]

    for anchor_index, zone in enumerate(ordered_zones):
        zone_id = str(zone["zone_id"])
        zone_gateway_ids = gateway_ids_for_zone(zone)
        shared_gateway_id = next(
            (
                gateway_id
                for gateway_id in zone_gateway_ids
                if gateway_id in gateway_anchor_locations
            ),
            None,
        )
        if shared_gateway_id is None:
            base_location = state_subpoint(anchor_index)
        else:
            base_location = gateway_anchor_locations[shared_gateway_id]
            reuse_key = gateway_primary_zone.get(shared_gateway_id, shared_gateway_id)
            reuse_count = zone_reuse_counts.get(reuse_key, 0) + 1
            zone_reuse_counts[reuse_key] = reuse_count
            base_location = _offset_ground_location(
                base_location,
                north_km=90.0 * reuse_count,
                east_km=45.0 * reuse_count,
            )

        effective_zone = dict(zone)
        effective_zone["catalog_coordinates"] = {
            "latitude_deg": zone.get("latitude_deg"),
            "longitude_deg": zone.get("longitude_deg"),
            "altitude_km": zone.get("altitude_km", 0.0),
        }
        effective_zone["latitude_deg"] = base_location.latitude_deg
        effective_zone["longitude_deg"] = base_location.longitude_deg
        effective_zone["altitude_km"] = base_location.altitude_km
        effective_zone["effective_coordinates"] = {
            "latitude_deg": base_location.latitude_deg,
            "longitude_deg": base_location.longitude_deg,
            "altitude_km": base_location.altitude_km,
        }
        effective_zone["zone_generation_policy"] = {
            "policy": "hybrid_region_coverage",
            "coordinate_policy": policy,
            "zone_semantics": "service_region_not_defined_by_uts",
            "representative_point_source": (
                "shell_subpoint_cluster_anchor"
                if policy == "synthetic_shell_aware"
                else "shell_subpoint_representative_for_catalog_region"
            ),
            "original_region": zone.get("region"),
            "required_for_runtime": bool(zone.get("required_for_runtime", False)),
            "service_role": zone.get("service_role"),
        }
        effective_zone["coordinate_semantics"] = {
            "catalog_coordinates_preserved": True,
            "effective_coordinates_source": (
                "synthetic_shell_aware_zone_cluster_anchor"
                if policy == "synthetic_shell_aware"
                else "shell_aware_consistent_zone_cluster_anchor"
            ),
            "coverage_coordinates": "effective_coordinates",
            "zone_anchor_coordinates": "effective_coordinates",
            "ut_attach_coordinates": "effective_coordinates",
            "gw_selection_coordinates": "effective_coordinates",
            "gz_fec_coordinates": "effective_coordinates",
        }
        effective_zones.append(effective_zone)

        ut_offsets = {
            "default_gateway_near": (-12.0, -12.0),
            "secondary_gateway_near": (12.0, 12.0),
            "zone_boundary": (45.0, 0.0),
            "remote_return_path": (-36.0, 36.0),
        }
        for ut_offset_index, ut_id in enumerate(list(zone.get("ut_ids", []))):
            ut_id = str(ut_id)
            if ut_id not in ut_by_id:
                continue
            role = str(ut_by_id[ut_id].get("placement_role", "")).strip()
            north_km, east_km = ut_offsets.get(
                role,
                (18.0 * (ut_offset_index + 1), -18.0 * (ut_offset_index + 1)),
            )
            locations[ut_id] = _offset_ground_location(
                base_location,
                north_km=north_km,
                east_km=east_km,
            )

        gateway_offsets = [(-8.0, -8.0), (8.0, 8.0), (0.0, 16.0), (16.0, 0.0)]
        for gateway_offset_index, gateway_id in enumerate(zone_gateway_ids):
            if gateway_id in locations:
                continue
            north_km, east_km = gateway_offsets[
                gateway_offset_index % len(gateway_offsets)
            ]
            gateway_location = _offset_ground_location(
                base_location,
                north_km=north_km,
                east_km=east_km,
            )
            locations[gateway_id] = gateway_location
            gateway_anchor_locations[gateway_id] = base_location
            gateway_primary_zone[gateway_id] = zone_id

    for ut in uts:
        ut_id = str(ut["ut_id"])
        if ut_id not in locations:
            locations[ut_id] = state_subpoint(len(locations) % anchor_count)
    for gateway in gateways:
        gateway_id = str(gateway["gateway_id"])
        if gateway_id not in locations:
            locations[gateway_id] = state_subpoint(len(locations) % anchor_count)

    effective_uts: List[Dict[str, Any]] = []
    for ut in uts:
        ut_id = str(ut["ut_id"])
        location = locations[ut_id]
        effective = dict(ut)
        effective["catalog_coordinates"] = {
            "latitude_deg": ut.get("latitude_deg"),
            "longitude_deg": ut.get("longitude_deg"),
            "altitude_km": ut.get("altitude_km", 0.0),
        }
        effective["latitude_deg"] = location.latitude_deg
        effective["longitude_deg"] = location.longitude_deg
        effective["altitude_km"] = location.altitude_km
        effective["effective_coordinates"] = {
            "latitude_deg": location.latitude_deg,
            "longitude_deg": location.longitude_deg,
            "altitude_km": location.altitude_km,
        }
        effective["coordinate_semantics"] = {
            "catalog_coordinates_preserved": True,
            "effective_coordinates_source": (
                "synthetic_shell_aware_probe"
                if policy == "synthetic_shell_aware"
                else "shell_aware_consistent_workload_probe"
            ),
        }
        effective["zone_membership_semantics"] = (
            "workload_probe_affinity_not_zone_definition"
        )
        effective["workload_probe"] = True
        effective_uts.append(effective)

    effective_gateways: List[Dict[str, Any]] = []
    for gateway in gateways:
        gateway_id = str(gateway["gateway_id"])
        location = locations[gateway_id]
        effective = dict(gateway)
        effective["catalog_coordinates"] = {
            "latitude_deg": gateway.get("latitude_deg"),
            "longitude_deg": gateway.get("longitude_deg"),
            "altitude_km": gateway.get("altitude_km", 0.0),
        }
        effective["latitude_deg"] = location.latitude_deg
        effective["longitude_deg"] = location.longitude_deg
        effective["altitude_km"] = location.altitude_km
        effective["effective_coordinates"] = {
            "latitude_deg": location.latitude_deg,
            "longitude_deg": location.longitude_deg,
            "altitude_km": location.altitude_km,
        }
        effective["coordinate_semantics"] = {
            "catalog_coordinates_preserved": True,
            "effective_coordinates_source": (
                "synthetic_shell_aware_gateway"
                if policy == "synthetic_shell_aware"
                else "shell_aware_consistent_gateway"
            ),
        }
        effective["coordinate_source"] = (
            "synthetic_shell_aware"
            if policy == "synthetic_shell_aware"
            else "real_catalog_with_shell_aware_effective_runtime_coordinates"
        )
        effective["provenance"] = {
            "source": gateway.get("source"),
            "version": gateway.get("version", "unversioned_catalog"),
            "synthetic": policy == "synthetic_shell_aware"
            or bool(gateway.get("synthetic", False)),
            "fallback_reason": (
                "explicit synthetic_shell_aware coordinate policy"
                if policy == "synthetic_shell_aware"
                else None
            ),
        }
        effective_gateways.append(effective)

    return {
        "ground_locations": locations,
        "ut_definitions": effective_uts,
        "gateway_definitions": effective_gateways,
        "zone_definitions": effective_zones,
        "coordinate_semantics": {
            "mode": policy,
            "zone_generation_policy": "hybrid_region_coverage",
            "original_catalog_coordinates": "preserved in catalog_coordinates fields",
            "effective_runtime_coordinates": "latitude_deg/longitude_deg plus effective_coordinates fields",
            "coverage_coordinates": "effective_runtime_coordinates",
            "zone_anchor_coordinates": "effective_runtime_coordinates",
            "ut_attach_coordinates": "effective_runtime_coordinates",
            "gw_selection_coordinates": "effective_runtime_coordinates",
            "gz_fec_coordinates": "effective_runtime_coordinates",
            "mixing_relocated_ground_with_fixed_zone_centers": False,
            "source_slice_id": propagated_slice.slice_id,
            "source_timestamp_utc": propagated_slice.timestamp_utc,
        },
    }


def _ground_locations_from_catalogs(
    *,
    ut_definitions: Sequence[Dict[str, Any]],
    gateway_definitions: Sequence[Dict[str, Any]],
) -> Dict[str, GroundLocation]:
    ground_locations: Dict[str, GroundLocation] = {}
    for definition in ut_definitions:
        ground_locations[str(definition["ut_id"])] = GroundLocation(
            latitude_deg=float(definition["latitude_deg"]),
            longitude_deg=float(definition["longitude_deg"]),
            altitude_km=float(definition.get("altitude_km", 0.0)),
        )
    for definition in gateway_definitions:
        ground_locations[str(definition["gateway_id"])] = GroundLocation(
            latitude_deg=float(definition["latitude_deg"]),
            longitude_deg=float(definition["longitude_deg"]),
            altitude_km=float(definition.get("altitude_km", 0.0)),
        )
    return ground_locations


@dataclass(frozen=True)
class StarlinkPlaneLayout:
    orbit_id: str
    plane_label: str
    plane_index: int
    satellite_ids: Sequence[str]
    norad_ids: Sequence[int]
    mean_raan_deg: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StarlinkSatelliteLayout:
    satellite_id: str
    satellite_name: str
    norad_id: int
    orbit_id: str
    plane_index: int
    plane_label: str
    satellite_index_in_plane: int
    inclination_deg: float
    raan_deg: float
    mean_anomaly_deg: float
    mean_motion_rev_per_day: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StarlinkShellLayout:
    scenario_name: str
    planes: Sequence[StarlinkPlaneLayout]
    satellites: Sequence[StarlinkSatelliteLayout]
    fixed_links: Sequence[Dict[str, Any]]
    cross_plane_slot_groups: Sequence[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scenario_name": self.scenario_name,
            "planes": [plane.to_dict() for plane in self.planes],
            "satellites": [satellite.to_dict() for satellite in self.satellites],
            "fixed_links": [dict(link) for link in self.fixed_links],
            "cross_plane_slot_groups": [dict(group) for group in self.cross_plane_slot_groups],
        }


def build_starlink_shell_layout(
    selection: ShellSelectionResult,
    *,
    scenario_name: str = "minimal-starlink-shell-preview",
) -> StarlinkShellLayout:
    selected_norad_ids = {record.norad_id for record in selection.selected_records}
    satellite_layouts: List[StarlinkSatelliteLayout] = []
    plane_layouts: List[StarlinkPlaneLayout] = []
    fixed_links: List[Dict[str, Any]] = []
    cross_plane_groups: List[Dict[str, Any]] = []

    for plane in selection.selected_planes:
        plane_records = [
            record
            for record in plane.records
            if record.norad_id in selected_norad_ids
        ]
        ordered_records = sorted(
            plane_records,
            key=lambda record: (record.mean_anomaly_deg, record.norad_id),
        )
        satellite_ids = [satellite_node_id(record.norad_id) for record in ordered_records]
        plane_layouts.append(
            StarlinkPlaneLayout(
                orbit_id=plane.plane_id,
                plane_label=f"Shell Plane {plane.plane_index}",
                plane_index=plane.plane_index,
                satellite_ids=tuple(satellite_ids),
                norad_ids=tuple(record.norad_id for record in ordered_records),
                mean_raan_deg=plane.mean_raan_deg,
            )
        )
        for satellite_index, record in enumerate(ordered_records, start=1):
            satellite_layouts.append(
                StarlinkSatelliteLayout(
                    satellite_id=satellite_node_id(record.norad_id),
                    satellite_name=record.satellite_name,
                    norad_id=record.norad_id,
                    orbit_id=plane.plane_id,
                    plane_index=plane.plane_index,
                    plane_label=f"Shell Plane {plane.plane_index}",
                    satellite_index_in_plane=satellite_index,
                    inclination_deg=record.inclination_deg,
                    raan_deg=record.raan_deg,
                    mean_anomaly_deg=record.mean_anomaly_deg,
                    mean_motion_rev_per_day=record.mean_motion_rev_per_day,
                )
            )
        if len(ordered_records) >= 2:
            for left_index, left_record in enumerate(ordered_records):
                right_record = ordered_records[(left_index + 1) % len(ordered_records)]
                fixed_links.append(
                    {
                        "left_satellite_id": satellite_node_id(left_record.norad_id),
                        "right_satellite_id": satellite_node_id(right_record.norad_id),
                        "left_norad_id": left_record.norad_id,
                        "right_norad_id": right_record.norad_id,
                        "orbit_id": plane.plane_id,
                        "plane_index": plane.plane_index,
                        "left_direction": "front",
                        "right_direction": "back",
                    }
                )

    for left_plane, right_plane in zip(plane_layouts, plane_layouts[1:]):
        left_ids = list(left_plane.satellite_ids)
        right_ids = list(right_plane.satellite_ids)
        pair_count = min(len(left_ids), len(right_ids))
        if pair_count == 0:
            continue
        candidate_pairs = []
        for pair_index in range(pair_count):
            candidate_pairs.append(
                {
                    "satellite_a_id": left_ids[pair_index],
                    "satellite_b_id": right_ids[pair_index],
                    "satellite_a_direction": "right",
                    "satellite_b_direction": "left",
                }
            )
        cross_plane_groups.append(
            {
                "pair_key": f"p{left_plane.plane_index:03d}p{right_plane.plane_index:03d}",
                "display_label": (
                    f"{left_plane.plane_label} <-> {right_plane.plane_label}"
                ),
                "orbit_a_id": left_plane.orbit_id,
                "orbit_b_id": right_plane.orbit_id,
                "orbit_a_satellite_ids": list(left_plane.satellite_ids),
                "orbit_b_satellite_ids": list(right_plane.satellite_ids),
                "candidate_pairs": candidate_pairs,
            }
        )

    return StarlinkShellLayout(
        scenario_name=scenario_name,
        planes=tuple(plane_layouts),
        satellites=tuple(satellite_layouts),
        fixed_links=tuple(fixed_links),
        cross_plane_slot_groups=tuple(cross_plane_groups),
    )


def build_starlink_preview_scenario(
    *,
    layout: StarlinkShellLayout,
    time_slices: Sequence[ExperimentTimeSlice],
    ut_definitions: Sequence[Dict[str, Any]] = DEFAULT_UT_CATALOG,
    gateway_definitions: Sequence[Dict[str, Any]] = DEFAULT_GATEWAY_CATALOG,
    zone_definitions: Sequence[Dict[str, Any]] | None = None,
    ground_locations: Dict[str, GroundLocation] | None = None,
    mini_internet_ix_offset: int = 0,
) -> ExperimentScenario:
    candidate_satellite_ids = tuple(
        satellite.satellite_id for satellite in layout.satellites
    )
    selected_ground_locations = (
        dict(ground_locations)
        if ground_locations is not None
        else _ground_locations_from_catalogs(
            ut_definitions=ut_definitions,
            gateway_definitions=gateway_definitions,
        )
    )
    explicit_zone_profiles: List[ExperimentZoneProfile] = []
    if zone_definitions:
        explicit_zone_profiles = [
            ExperimentZoneProfile(
                zone_id=str(definition["zone_id"]),
                display_name=str(definition.get("display_name", definition["zone_id"])),
                region=str(definition.get("region", definition["zone_id"])),
                latitude_deg=float(definition["latitude_deg"]),
                longitude_deg=float(definition["longitude_deg"]),
                altitude_km=float(definition.get("altitude_km", 0.0)),
                candidate_satellite_ids=candidate_satellite_ids,
                partition_policy=str(
                    definition.get("partition_policy", "explicit_zone_catalog")
                ),
                return_prefixes=tuple(
                    str(prefix)
                    for prefix in (
                        definition.get("return_prefixes")
                        or (
                            [definition["return_prefix"]]
                            if definition.get("return_prefix")
                            else []
                        )
                    )
                ),
                service_role=str(definition.get("service_role", "required_service")),
                active_service=bool(definition.get("active_service", True)),
                required_for_runtime=bool(
                    definition.get(
                        "required_for_runtime",
                        str(definition.get("service_role", "required_service"))
                        == "required_service",
                    )
                ),
                radius_km=(
                    None
                    if definition.get("radius_km") is None
                    else float(definition.get("radius_km", 0.0))
                ),
                coverage_margin_deg=float(definition.get("coverage_margin_deg", 2.0)),
                default_gateway=(
                    None
                    if definition.get("default_gateway") is None
                    else str(definition.get("default_gateway"))
                ),
                preferred_gateway_ids=tuple(
                    str(gateway_id)
                    for gateway_id in list(definition.get("preferred_gateway_ids", []))
                    if str(gateway_id).strip()
                ),
                coordinate_gateway_ids=tuple(
                    str(gateway_id)
                    for gateway_id in list(definition.get("coordinate_gateway_ids", []))
                    if str(gateway_id).strip()
                ),
                fallback_to_shortest_path=bool(
                    definition.get("fallback_to_shortest_path", True)
                ),
                max_additional_satellite_hops_relative_to_best_candidate=(
                    None
                    if definition.get(
                        "max_additional_satellite_hops_relative_to_best_candidate"
                    )
                    is None
                    else int(
                        definition[
                            "max_additional_satellite_hops_relative_to_best_candidate"
                        ]
                    )
                ),
                prefix_overrides=tuple(
                    dict(override)
                    for override in list(definition.get("prefix_overrides", []))
                ),
                source=str(definition.get("source", "explicit_zone_catalog")),
                service_domain_source=str(
                    definition.get("service_domain_source")
                    or definition.get("source")
                    or "coverage_aware_zone_catalog"
                ),
                representative_ground_points=tuple(
                    dict(point)
                    for point in list(
                        definition.get("representative_ground_points", [])
                    )
                ),
                boundary_points=tuple(
                    dict(point)
                    for point in list(definition.get("boundary_points", []))
                ),
            )
            for definition in zone_definitions
        ]
    catalog_prefix_override_policies: List[ExperimentPrefixOverridePolicy] = []
    for zone_profile in explicit_zone_profiles:
        for index, override in enumerate(zone_profile.prefix_overrides):
            destination_prefix = str(
                override.get("destination_prefix") or override.get("prefix") or ""
            ).strip()
            if not destination_prefix:
                continue
            preferred_gateway_ids = tuple(
                str(gateway_id)
                for gateway_id in list(override.get("preferred_gateway_ids", []))
                if str(gateway_id).strip()
            )
            preferred_gateway_id = override.get("preferred_gateway_id") or override.get(
                "gateway"
            )
            policy_name = str(
                override.get("policy_name")
                or f"{zone_profile.zone_id}_prefix_override_{index}"
            )
            catalog_prefix_override_policies.append(
                ExperimentPrefixOverridePolicy(
                    policy_name=policy_name,
                    destination_prefix=destination_prefix,
                    preferred_gateway_id=(
                        None
                        if preferred_gateway_id is None
                        else str(preferred_gateway_id)
                    ),
                    target_node_id=str(override.get("target_node_id") or ""),
                    zone_id=str(zone_profile.zone_id),
                    preferred_gateway_ids=preferred_gateway_ids,
                    selection_mode=str(
                        override.get(
                            "selection_mode",
                            "stable_preferred_gateway_order",
                        )
                    ),
                    fallback_to_shortest_path=bool(
                        override.get("fallback_to_shortest_path", False)
                    ),
                )
            )
    default_prefix_override_policies: Tuple[ExperimentPrefixOverridePolicy, ...] = (
        (
            ExperimentPrefixOverridePolicy(
                policy_name="asn160_via_selected_secondary_gateway",
                destination_prefix="10.160.0.0/24",
                preferred_gateway_id=None,
                target_node_id="as160-host1",
                selection_mode="target_affinity_shortest_path",
                fallback_to_shortest_path=False,
            ),
        )
        if len(gateway_definitions) >= 2 and not catalog_prefix_override_policies
        else ()
    )
    return ExperimentScenario(
        scenario_name=layout.scenario_name,
        satellite_ids=candidate_satellite_ids,
        terminal_profiles=tuple(
            ExperimentTerminalProfile(
                ut_id=str(definition["ut_id"]),
                slot_name=f"{definition['ut_id']}-access-slot0",
                zone_id=str(definition["zone_id"]),
                candidate_satellite_ids=candidate_satellite_ids,
            )
            for definition in ut_definitions
        ),
        gateway_profiles=tuple(
            ExperimentGatewayProfile(
                gateway_id=str(definition["gateway_id"]),
                slot_name=f"{definition['gateway_id']}-gateway-slot0",
                candidate_satellite_ids=candidate_satellite_ids,
            )
            for definition in gateway_definitions
        ),
        ping_target_ip="10.154.0.129",
        target_host_node_id="as154-host-new",
        default_validation_target_node_ids=("as154-host-new", "as160-host1"),
        special_prefix_override_policies=tuple(
            catalog_prefix_override_policies
        )
        + default_prefix_override_policies,
        time_slices=tuple(time_slices),
        orbital_reference_time_seconds=0.0,
        min_elevation_deg=5.0,
        ground_locations=selected_ground_locations,
        orbital_elements={},
        zone_profiles=tuple(explicit_zone_profiles),
        mini_internet_ix_offset=int(mini_internet_ix_offset),
    )


def build_starlink_shell_emulator(
    layout: StarlinkShellLayout,
    *,
    ut_definitions: Sequence[Dict[str, Any]] = DEFAULT_UT_CATALOG,
    gateway_definitions: Sequence[Dict[str, Any]] = DEFAULT_GATEWAY_CATALOG,
    mini_internet_node_prefix: str = "",
    mini_internet_ix_offset: int = 0,
    include_mini_internet: bool = True,
) -> Emulator:
    emu = Emulator()
    base = Base()
    routing = Routing()
    ebgp = Ebgp()
    ibgp = Ibgp()
    ospf = Ospf()

    if include_mini_internet:
        build_mini_internet(
            base,
            ebgp,
            node_name_prefix=str(mini_internet_node_prefix),
            ix_id_offset=int(mini_internet_ix_offset),
            transit_asns=STARLINK_MINI_INTERNET_TRANSIT_ASNS,
            stub_asns=STARLINK_MINI_INTERNET_STUB_ASNS,
            extra_transit_ixs=_gateway_transit_ixs_for_mini_internet(
                gateway_definitions,
                ix_id_offset=int(mini_internet_ix_offset),
            ),
        )

    satellite_as = base.createAutonomousSystem(SATELLITE_ASN)
    operator_as = base.createAutonomousSystem(SATELLITE_OPERATOR_ASN)
    satellite_as.createNetwork(
        SATELLITE_RUNTIME_MGMT_NETWORK_NAME,
        SATELLITE_RUNTIME_MGMT_SUBNET,
    )

    satellite_routers: Dict[str, Any] = {}
    for index, satellite in enumerate(layout.satellites, start=1):
        satellite_routers[satellite.satellite_id] = satellite_as.createRouter(
            satellite.satellite_id
        )
        satellite_routers[satellite.satellite_id].joinNetwork(
            SATELLITE_RUNTIME_MGMT_NETWORK_NAME,
            _subnet_host_address(SATELLITE_RUNTIME_MGMT_SUBNET, index + 9),
        )
        satellite_routers[satellite.satellite_id].addSoftware("tcpdump")
        enable_mpls_readiness(satellite_routers[satellite.satellite_id])
        satellite_routers[satellite.satellite_id].appendStartCommand(
            f"ip link set {SATELLITE_RUNTIME_MGMT_NETWORK_NAME} down || true",
            isPostConfigCommand=False,
        )

    for definition in ut_definitions:
        ut_id = str(definition["ut_id"])
        ut_index = int(definition["ut_index"])
        placeholder_router = satellite_as.createRouter(f"{ut_id}-placeholder-rtr")
        placeholder_router.joinNetwork(
            SATELLITE_RUNTIME_MGMT_NETWORK_NAME,
            _subnet_host_address(
                SATELLITE_RUNTIME_MGMT_SUBNET,
                200 + (ut_index * 2) - 1,
            ),
        )
        placeholder_router.addSoftware("tcpdump")
        placeholder_router.appendStartCommand(
            f"ip link set {SATELLITE_RUNTIME_MGMT_NETWORK_NAME} down || true",
            isPostConfigCommand=False,
        )
        ut = satellite_as.createHost(ut_id)
        ut.joinNetwork(
            SATELLITE_RUNTIME_MGMT_NETWORK_NAME,
            _subnet_host_address(
                SATELLITE_RUNTIME_MGMT_SUBNET,
                200 + (ut_index * 2),
            ),
        )
        ut.appendStartCommand("ip route del default || true", isPostConfigCommand=True)
        ut.appendStartCommand("ip link add dummy0 type dummy", isPostConfigCommand=True)
        ut.appendStartCommand("ip link set dummy0 up", isPostConfigCommand=True)
        ut.appendStartCommand(
            f"ip addr add {resolve_ut_identity_address(ut_id)}/32 dev dummy0",
            isPostConfigCommand=True,
        )
        ut.appendStartCommand(
            f"ip link set {SATELLITE_RUNTIME_MGMT_NETWORK_NAME} down || true",
            isPostConfigCommand=False,
        )
        ut.addSoftware("curl")
        ut.addSoftware("tcpdump")

    gateway_by_id = {
        str(definition["gateway_id"]): dict(definition)
        for definition in gateway_definitions
    }
    gateway_routers: Dict[str, Any] = {}
    for definition in gateway_definitions:
        gateway_id = str(definition["gateway_id"])
        gateway_index = int(definition["gateway_index"])
        gateway_bootstrap_network = _gateway_bootstrap_network_name(gateway_id)
        operator_as.createNetwork(
            gateway_bootstrap_network,
            _prefix(242, gateway_index, 24),
        )
        gateway_routers[gateway_id] = operator_as.createRouter(gateway_id)
        gateway_routers[gateway_id].joinNetwork(
            gateway_bootstrap_network,
            _host_address(242, gateway_index, 2),
        )
        gateway_routers[gateway_id].addSoftware("tcpdump")
        enable_mpls_readiness(gateway_routers[gateway_id])
        if include_mini_internet:
            gateway_routers[gateway_id].joinNetwork(
                f"ix{definition['ix_id']}",
                str(definition["ix_ip_address"]),
            )
            ebgp.addPrivatePeering(
                int(definition["ix_id"]),
                int(definition["upstream_asn"]),
                SATELLITE_OPERATOR_ASN,
                PeerRelationship.Provider,
            )

    ospf.maskAsn(SATELLITE_ASN)
    ospf.maskAsn(SATELLITE_OPERATOR_ASN)
    ibgp.maskAsn(SATELLITE_ASN)
    ibgp.maskAsn(SATELLITE_OPERATOR_ASN)

    emu.addLayer(base)
    emu.addLayer(routing)
    emu.addLayer(ebgp)
    emu.addLayer(ibgp)
    emu.addLayer(ospf)
    return emu


def build_starlink_shell_slot_inventory(
    layout: StarlinkShellLayout,
    *,
    ut_definitions: Sequence[Dict[str, Any]] = DEFAULT_UT_CATALOG,
    gateway_definitions: Sequence[Dict[str, Any]] = DEFAULT_GATEWAY_CATALOG,
    ut_slots_per_terminal: int = 1,
    gateway_slots_per_gateway: int = 1,
    cross_plane_slot_model: str = "candidate_pair",
    visualization_link_payload: Dict[str, Any] | None = None,
) -> Dict[str, List[Dict[str, Any]]]:
    candidate_satellite_ids = [satellite.satellite_id for satellite in layout.satellites]
    ut_slot_count = max(1, int(ut_slots_per_terminal))
    gateway_slot_count = max(1, int(gateway_slots_per_gateway))
    cross_plane_model = str(cross_plane_slot_model)
    same_plane_slots: List[Dict[str, Any]] = []
    for slot_index, fixed_link in enumerate(layout.fixed_links, start=1):
        network_name = _fixed_shell_network_name(slot_index)
        same_plane_slots.append(
            {
                "slot_name": f"fixed-shell-{slot_index:04d}",
                "slot_type": "same_plane_isl",
                "intent_type": "dynamic_same_plane_isl",
                "runtime_realization": "skyseed_direct_backend",
                "activation_model": "selected_shell_same_plane_always_available",
                "network_name": network_name,
                "docker_network_name": network_name,
                "subnet": _prefix(243, slot_index, 30),
                "bridge_gateway_ip": None,
                "slot_index": slot_index,
                "orbit_scope": fixed_link["orbit_id"],
                "endpoint_a_template": _endpoint_record(
                    node_id=str(fixed_link["left_satellite_id"]),
                    node_type="satellite",
                    interface_name=network_name,
                    ip_address=_host_ip(243, slot_index, 1, 30),
                    logical_direction=str(fixed_link["left_direction"]),
                ),
                "endpoint_b_template": _endpoint_record(
                    node_id=str(fixed_link["right_satellite_id"]),
                    node_type="satellite",
                    interface_name=network_name,
                    ip_address=_host_ip(243, slot_index, 2, 30),
                    logical_direction=str(fixed_link["right_direction"]),
                ),
                "candidate_pairs": [
                    {
                        "satellite_a_id": str(fixed_link["left_satellite_id"]),
                        "satellite_b_id": str(fixed_link["right_satellite_id"]),
                        "satellite_a_direction": str(fixed_link["left_direction"]),
                        "satellite_b_direction": str(fixed_link["right_direction"]),
                    }
                ],
            }
        )

    ut_slots: List[Dict[str, Any]] = []
    for definition in ut_definitions:
        ut_id = str(definition["ut_id"])
        ut_index = int(definition["ut_index"])
        for slot_offset in range(ut_slot_count):
            network_counter = (ut_index * ut_slot_count) + slot_offset
            interface_name = f"ua-u{ut_index}s{slot_offset}"
            slot_name = f"{ut_id}-access-slot{slot_offset}"
            ut_slots.append(
                {
                    "slot_name": slot_name,
                    "slot_type": "ut_access",
                    "network_name": slot_name,
                    "docker_network_name": slot_name,
                    "subnet": _prefix(90, network_counter, 29),
                    "bridge_gateway_ip": _host_address(90, network_counter, 6),
                    "ut_id": ut_id,
                    "region": definition["region"],
                    "slot_index": slot_offset,
                    "candidate_satellite_ids": list(candidate_satellite_ids),
                    "preconnected_endpoint": _endpoint_record(
                        node_id=ut_id,
                        node_type="user_terminal",
                        interface_name=interface_name,
                        ip_address=_host_ip(90, network_counter, 2, 29),
                    ),
                    "dynamic_endpoint_template": _endpoint_record(
                        node_id="<runtime-satellite>",
                        node_type="satellite",
                        interface_name=interface_name,
                        ip_address=_host_ip(90, network_counter, 1, 29),
                    ),
                }
            )

    gateway_slots: List[Dict[str, Any]] = []
    for definition in gateway_definitions:
        gateway_id = str(definition["gateway_id"])
        gateway_index = int(definition["gateway_index"])
        for slot_offset in range(gateway_slot_count):
            network_counter = (gateway_index * gateway_slot_count) + slot_offset
            interface_name = f"ga-g{gateway_index}s{slot_offset}"
            slot_name = f"{gateway_id}-gateway-slot{slot_offset}"
            gateway_slots.append(
                {
                    "slot_name": slot_name,
                    "slot_type": "gw_access",
                    "network_name": slot_name,
                    "docker_network_name": slot_name,
                    "subnet": _prefix(91, network_counter, 29),
                    "bridge_gateway_ip": _host_address(91, network_counter, 6),
                    "gateway_id": gateway_id,
                    "gateway_region": definition["region"],
                    "slot_index": slot_offset,
                    "candidate_satellite_ids": list(candidate_satellite_ids),
                    "preconnected_endpoint": _endpoint_record(
                        node_id=gateway_id,
                        node_type="gateway",
                        interface_name=interface_name,
                        ip_address=_host_ip(91, network_counter, 2, 29),
                    ),
                    "dynamic_endpoint_template": _endpoint_record(
                        node_id="<runtime-satellite>",
                        node_type="satellite",
                        interface_name=interface_name,
                        ip_address=_host_ip(91, network_counter, 1, 29),
                    ),
                }
            )

    def _visualization_cross_plane_pairs() -> List[Dict[str, Any]]:
        if visualization_link_payload is None:
            return []
        satellite_by_id = {
            str(satellite.satellite_id): satellite for satellite in layout.satellites
        }
        pairs_by_key: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for frame in list(dict(visualization_link_payload).get("links") or []):
            for raw_link in list(dict(frame).get("satelliteLinks") or []):
                link = dict(raw_link)
                first_id = str(link.get("satelliteAId") or "")
                second_id = str(link.get("satelliteBId") or "")
                if not first_id or not second_id:
                    continue
                first_satellite = satellite_by_id.get(first_id)
                second_satellite = satellite_by_id.get(second_id)
                if first_satellite is None or second_satellite is None:
                    continue
                if first_satellite.orbit_id == second_satellite.orbit_id:
                    continue
                ordered = sorted(
                    (first_satellite, second_satellite),
                    key=lambda satellite: (
                        int(satellite.plane_index),
                        int(satellite.satellite_index_in_plane),
                        str(satellite.satellite_id),
                    ),
                )
                left, right = ordered[0], ordered[1]
                pair_key = (str(left.satellite_id), str(right.satellite_id))
                if pair_key in pairs_by_key:
                    continue
                pairs_by_key[pair_key] = {
                    "satellite_a_id": str(left.satellite_id),
                    "satellite_b_id": str(right.satellite_id),
                    "satellite_a_direction": "right",
                    "satellite_b_direction": "left",
                    "orbit_a_id": str(left.orbit_id),
                    "orbit_b_id": str(right.orbit_id),
                    "orbit_a_plane_index": int(left.plane_index),
                    "orbit_b_plane_index": int(right.plane_index),
                    "orbit_a_label": str(left.plane_label),
                    "orbit_b_label": str(right.plane_label),
                }
        return [
            dict(pair)
            for _, pair in sorted(
                pairs_by_key.items(),
                key=lambda item: (
                    int(item[1]["orbit_a_plane_index"]),
                    int(item[1]["orbit_b_plane_index"]),
                    str(item[1]["satellite_a_id"]),
                    str(item[1]["satellite_b_id"]),
                ),
            )
        ]

    cross_plane_slots: List[Dict[str, Any]] = []
    slot_index = 1
    visualization_cross_plane_pairs = _visualization_cross_plane_pairs()
    if visualization_cross_plane_pairs:
        for pair in visualization_cross_plane_pairs:
            pair_key = (
                f"p{int(pair['orbit_a_plane_index']):03d}"
                f"p{int(pair['orbit_b_plane_index']):03d}"
            )
            pair_suffix = (
                f"{pair['satellite_a_id']}-{pair['satellite_b_id']}"
            ).replace("_", "-")
            slot_name = f"{pair_key}-{pair_suffix}-cross-slot0"
            interface_a = f"xp{slot_index}a"
            interface_b = f"xp{slot_index}b"
            cross_plane_slots.append(
                {
                    "slot_name": slot_name,
                    "slot_type": "cross_plane_isl",
                    "network_name": slot_name,
                    "docker_network_name": slot_name,
                    "subnet": _prefix(93, slot_index, 29),
                    "bridge_gateway_ip": _host_address(93, slot_index, 6),
                    "slot_index": slot_index,
                    "cross_plane_slot_model": "visualization_frozen_pair_resident",
                    "pair_key": pair_key,
                    "display_label": (
                        f"{pair['orbit_a_label']} <-> {pair['orbit_b_label']}"
                    ),
                    "orbit_pair": [pair["orbit_a_id"], pair["orbit_b_id"]],
                    "orbit_a_id": pair["orbit_a_id"],
                    "orbit_b_id": pair["orbit_b_id"],
                    "orbit_a_satellite_ids": [pair["satellite_a_id"]],
                    "orbit_b_satellite_ids": [pair["satellite_b_id"]],
                    "endpoint_a_template": _endpoint_record(
                        node_id=f"<runtime-{pair['orbit_a_id']}-satellite>",
                        node_type="satellite",
                        interface_name=interface_a,
                        ip_address=_host_ip(93, slot_index, 1, 29),
                    ),
                    "endpoint_b_template": _endpoint_record(
                        node_id=f"<runtime-{pair['orbit_b_id']}-satellite>",
                        node_type="satellite",
                        interface_name=interface_b,
                        ip_address=_host_ip(93, slot_index, 2, 29),
                    ),
                    "candidate_pairs": [
                        {
                            "satellite_a_id": pair["satellite_a_id"],
                            "satellite_b_id": pair["satellite_b_id"],
                            "satellite_a_direction": pair["satellite_a_direction"],
                            "satellite_b_direction": pair["satellite_b_direction"],
                        }
                    ],
                }
            )
            slot_index += 1

    for group in (() if visualization_cross_plane_pairs else layout.cross_plane_slot_groups):
        if cross_plane_model == "candidate_pair":
            candidate_pairs = [dict(pair) for pair in group["candidate_pairs"]]
        elif cross_plane_model == "plane_pair_group":
            candidate_pairs = [list(group["candidate_pairs"])]
        else:
            raise ValueError(f"unsupported cross-plane slot model: {cross_plane_model}")
        for pair_offset, pair_group in enumerate(candidate_pairs):
            if cross_plane_model == "candidate_pair":
                pair = dict(pair_group)
                pair_suffix = (
                    f"{pair['satellite_a_id']}-{pair['satellite_b_id']}"
                ).replace("_", "-")
                slot_name = f"{group['pair_key']}-{pair_suffix}-cross-slot0"
                slot_candidate_pairs = [pair]
                orbit_a_satellite_ids = [pair["satellite_a_id"]]
                orbit_b_satellite_ids = [pair["satellite_b_id"]]
                slot_model = "candidate_pair_resident"
            else:
                slot_name = f"{group['pair_key']}-cross-slot0"
                slot_candidate_pairs = [dict(pair) for pair in pair_group]
                orbit_a_satellite_ids = list(group["orbit_a_satellite_ids"])
                orbit_b_satellite_ids = list(group["orbit_b_satellite_ids"])
                slot_model = "plane_pair_group"
            interface_a = f"xp{slot_index}a"
            interface_b = f"xp{slot_index}b"
            cross_plane_slots.append(
                {
                    "slot_name": slot_name,
                    "slot_type": "cross_plane_isl",
                    "network_name": slot_name,
                    "docker_network_name": slot_name,
                    "subnet": _prefix(93, slot_index, 29),
                    "bridge_gateway_ip": _host_address(93, slot_index, 6),
                    "slot_index": slot_index if cross_plane_model == "candidate_pair" else pair_offset,
                    "cross_plane_slot_model": slot_model,
                    "pair_key": group["pair_key"],
                    "display_label": group["display_label"],
                    "orbit_pair": [group["orbit_a_id"], group["orbit_b_id"]],
                    "orbit_a_id": group["orbit_a_id"],
                    "orbit_b_id": group["orbit_b_id"],
                    "orbit_a_satellite_ids": orbit_a_satellite_ids,
                    "orbit_b_satellite_ids": orbit_b_satellite_ids,
                    "endpoint_a_template": _endpoint_record(
                        node_id=f"<runtime-{group['orbit_a_id']}-satellite>",
                        node_type="satellite",
                        interface_name=interface_a,
                        ip_address=_host_ip(93, slot_index, 1, 29),
                    ),
                    "endpoint_b_template": _endpoint_record(
                        node_id=f"<runtime-{group['orbit_b_id']}-satellite>",
                        node_type="satellite",
                        interface_name=interface_b,
                        ip_address=_host_ip(93, slot_index, 2, 29),
                    ),
                    "candidate_pairs": slot_candidate_pairs,
                }
            )
            slot_index += 1

    return {
        "same_plane_isl": same_plane_slots,
        "ut_access": ut_slots,
        "gateway_access": gateway_slots,
        "cross_plane_isl": cross_plane_slots,
    }


def build_starlink_shell_topology_inventory(
    layout: StarlinkShellLayout,
    *,
    ut_definitions: Sequence[Dict[str, Any]] = DEFAULT_UT_CATALOG,
    gateway_definitions: Sequence[Dict[str, Any]] = DEFAULT_GATEWAY_CATALOG,
    orbit_input_kind: str = "real_tle_sgp4",
    orbit_source: str = "celestrak_starlink_gp_tle",
    mini_internet_ix_offset: int = 0,
    ut_slots_per_terminal: int = 1,
    gateway_slots_per_gateway: int = 1,
    cross_plane_slot_model: str = "candidate_pair",
    visualization_link_payload: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    gateway_by_id = {
        str(definition["gateway_id"]): dict(definition)
        for definition in gateway_definitions
    }
    ut_by_id = {
        str(definition["ut_id"]): dict(definition)
        for definition in ut_definitions
    }
    slot_inventory = build_starlink_shell_slot_inventory(
        layout,
        ut_definitions=ut_definitions,
        gateway_definitions=gateway_definitions,
        ut_slots_per_terminal=ut_slots_per_terminal,
        gateway_slots_per_gateway=gateway_slots_per_gateway,
        cross_plane_slot_model=cross_plane_slot_model,
        visualization_link_payload=visualization_link_payload,
    )

    topology = {
        "schema_version": "satellite-starlink-shell-preview-topology-v1",
        "description": "Minimal Starlink-shell preview topology generated from planned-shell or real-catalog orbit input.",
        "logical_port_labels": ["front", "back", "left", "right"],
        "skyseed_runtime_model": {
            "base_topology_mode": "seed_stable_infrastructure_plus_skyseed_direct_satellite_runtime",
            "strict_active_link_semantics": True,
            "seed_keeps": [
                "container_generation",
                "mini_internet",
                "internet_exchanges",
                "stable_gateway_to_mini_internet_attachments",
                "management_bootstrap_networks",
            ],
            "skyseed_offloads": [
                "ut_access",
                "gateway_gsl",
                "same_plane_isl",
                "cross_plane_isl",
                "runtime_link_parameters",
                "runtime_mpls_route_mutation",
            ],
            "node_startup_profiles": dict(SKYSEED_NODE_STARTUP_PROFILES),
            "ut_slots_per_terminal": max(1, int(ut_slots_per_terminal)),
            "gateway_slots_per_gateway": max(1, int(gateway_slots_per_gateway)),
            "cross_plane_slot_semantic_model": (
                "visualization_frozen_pair_resident"
                if visualization_link_payload is not None
                else str(cross_plane_slot_model)
            ),
        },
        "skyseed_node_role_framework": dict(SKYSEED_NODE_ROLE_FRAMEWORK),
        "orbit_input": {
            "kind": orbit_input_kind,
            "source": orbit_source,
        },
        "prototype_scope": {
            "orbital_planes": len(layout.planes),
            "satellites": len(layout.satellites),
            "gateways": len(gateway_definitions),
            "user_terminals": len(ut_definitions),
            "same_plane_isl_intent_count": len(slot_inventory.get("same_plane_isl", [])),
            "cross_plane_slot_count": len(slot_inventory.get("cross_plane_isl", [])),
            "internet_exchanges": len(
                _mini_internet_exchange_records(
                    gateway_definitions,
                    ix_id_offset=int(mini_internet_ix_offset),
                )
            ),
            "mini_internet_transit_ases": len(STARLINK_MINI_INTERNET_TRANSIT_ASNS),
            "mini_internet_stub_ases": len(STARLINK_MINI_INTERNET_STUB_ASNS),
            "example_profile": layout.scenario_name,
        },
        "orbital_planes": [plane.to_dict() for plane in layout.planes],
        "internet_exchanges": _mini_internet_exchange_records(
            gateway_definitions,
            ix_id_offset=int(mini_internet_ix_offset),
        ),
        "satellites": [
            {
                "satellite_id": satellite.satellite_id,
                "satellite_name": satellite.satellite_name,
                "norad_id": satellite.norad_id,
                "skyseed_role": "satellite",
                "orbit_id": satellite.orbit_id,
                "plane_label": satellite.plane_label,
                "plane_index": satellite.plane_index,
                "satellite_index_in_plane": satellite.satellite_index_in_plane,
                "tle_summary": {
                    "inclination_deg": satellite.inclination_deg,
                    "raan_deg": satellite.raan_deg,
                    "mean_anomaly_deg": satellite.mean_anomaly_deg,
                    "mean_motion_rev_per_day": satellite.mean_motion_rev_per_day,
                },
                "logical_ports": {"front": None, "back": None, "left": None, "right": None},
                "interface_role_separation": {
                    "same_plane_isl": ["front", "back"],
                    "cross_plane_isl": ["left", "right"],
                    "ut_facing": "runtime_slot",
                    "gw_facing": "runtime_slot",
                },
                "startup_profile": SKYSEED_STARTUP_PROFILE_SATELLITE,
                "bootstrap_network": _bootstrap_inventory(
                    node_id=satellite.satellite_id,
                    node_type="satellite",
                    network_name=SATELLITE_RUNTIME_MGMT_NETWORK_NAME,
                    subnet=SATELLITE_RUNTIME_MGMT_SUBNET,
                    ip_address=_subnet_host_ip(
                        SATELLITE_RUNTIME_MGMT_SUBNET,
                        index + 9,
                    ),
                    purpose="compile_time_container_carrier_interface_down",
                ),
            }
            for index, satellite in enumerate(layout.satellites, start=1)
        ],
        "gateways": [
            {
                "gateway_id": gateway_id,
                "role": "satellite_operator_border_gateway",
                "skyseed_role": "gateway_ground_station",
                "asn": SATELLITE_OPERATOR_ASN,
                "gateway_region": gateway_by_id[gateway_id]["region"],
                "display_name": gateway_by_id[gateway_id]["name"],
                "gateway_index": gateway_by_id[gateway_id]["gateway_index"],
                "gateway_selector_label": gateway_by_id[gateway_id].get(
                    "gateway_selector_label"
                ),
                "gateway_service_ipv4": gateway_by_id[gateway_id].get(
                    "gateway_service_ipv4"
                ),
                "catalog_coordinates": {
                    "latitude_deg": gateway_by_id[gateway_id]["latitude_deg"],
                    "longitude_deg": gateway_by_id[gateway_id]["longitude_deg"],
                    "altitude_km": gateway_by_id[gateway_id].get("altitude_km", 0.0),
                },
                "source": gateway_by_id[gateway_id].get("source"),
                "type": gateway_by_id[gateway_id].get("type"),
                "confidence": gateway_by_id[gateway_id].get("confidence"),
                "unofficial": bool(gateway_by_id[gateway_id].get("unofficial", False)),
                "startup_profile": SKYSEED_STARTUP_PROFILE_GATEWAY,
                "bootstrap_network": _bootstrap_inventory(
                    node_id=gateway_id,
                    node_type="gateway",
                    network_name=_gateway_bootstrap_network_name(gateway_id),
                    subnet=_prefix(242, int(gateway_by_id[gateway_id]["gateway_index"]), 24),
                    ip_address=_host_ip(
                        242,
                        int(gateway_by_id[gateway_id]["gateway_index"]),
                        2,
                        24,
                    ),
                    purpose="compile_time_gateway_nat_identity_bootstrap_network",
                ),
                "internet_uplink": {
                    "ix_id": gateway_by_id[gateway_id]["ix_id"],
                    "network_name": f"ix{gateway_by_id[gateway_id]['ix_id']}",
                    "ip_address": gateway_by_id[gateway_id]["ix_ip_address"],
                    "peering_asn": SATELLITE_OPERATOR_ASN,
                    "region": gateway_by_id[gateway_id]["region"],
                },
            }
            for gateway_id in [entry["gateway_id"] for entry in gateway_definitions]
        ],
        "user_terminals": [
            {
                "ut_id": ut_id,
                "display_name": ut_by_id[ut_id]["display_name"],
                "skyseed_role": "user_terminal",
                "identity_interface": {
                    "if_name": "dummy0",
                    "ip_address": f"{resolve_ut_identity_address(ut_id)}/32",
                    "semantics": "stable_internal_identity_not_publicly_advertised",
                },
                "region": ut_by_id[ut_id]["region"],
                "zone_id": ut_by_id[ut_id]["zone_id"],
                "catalog_coordinates": {
                    "latitude_deg": ut_by_id[ut_id]["latitude_deg"],
                    "longitude_deg": ut_by_id[ut_id]["longitude_deg"],
                    "altitude_km": ut_by_id[ut_id].get("altitude_km", 0.0),
                },
                "source": ut_by_id[ut_id].get("source"),
                "type": ut_by_id[ut_id].get("type"),
                "startup_profile": SKYSEED_STARTUP_PROFILE_UT_ENDPOINT,
                "placeholder_router_startup_profile": SKYSEED_STARTUP_PROFILE_UT_PLACEHOLDER,
                "candidate_serving_satellite_ids": [
                    satellite.satellite_id for satellite in layout.satellites
                ],
                "bootstrap_network": _bootstrap_inventory(
                    node_id=ut_id,
                    node_type="user_terminal",
                    network_name=SATELLITE_RUNTIME_MGMT_NETWORK_NAME,
                    subnet=SATELLITE_RUNTIME_MGMT_SUBNET,
                    ip_address=_subnet_host_ip(
                        SATELLITE_RUNTIME_MGMT_SUBNET,
                        200 + (int(ut_by_id[ut_id]["ut_index"]) * 2),
                    ),
                    purpose="compile_time_container_carrier_interface_down",
                ),
            }
            for ut_id in [entry["ut_id"] for entry in ut_definitions]
        ],
        "fixed_links": {"satellite_satellite": []},
        "mini_internet": {
            "transit_ases": [
                {
                    "asn": asn,
                    "exchange_ids": [
                        int(ix_id) + int(mini_internet_ix_offset)
                        for ix_id in layout_definition["exchanges"]
                    ],
                    "internal_links": [
                        [
                            int(left_ix) + int(mini_internet_ix_offset),
                            int(right_ix) + int(mini_internet_ix_offset),
                        ]
                        for left_ix, right_ix in layout_definition["links"]
                    ],
                }
                for asn, layout_definition in sorted(MINI_INTERNET_TRANSIT_AS_LAYOUT.items())
                if asn in STARLINK_MINI_INTERNET_TRANSIT_ASNS
            ],
            "stub_ases": [
                {
                    "asn": asn,
                    "exchange_id": int(exchange) + int(mini_internet_ix_offset),
                    "host_node_ids": [
                        f"as{asn}-host{host_index}"
                        for host_index in range(MINI_INTERNET_HOSTS_PER_STUB_AS)
                    ] + (["as154-host-new"] if asn == 154 else []),
                }
                for asn, exchange in sorted(MINI_INTERNET_STUB_AS_EXCHANGES.items())
                if asn in STARLINK_MINI_INTERNET_STUB_ASNS
            ],
            "external_test_targets": [
                dict(target)
                for target in EXTERNAL_TEST_TARGETS
                if int(target.get("asn", -1)) in STARLINK_MINI_INTERNET_STUB_ASNS
            ],
            "gateway_ix_peerings": [
                {
                    "gateway_id": gateway_id,
                    "ix_id": gateway_by_id[gateway_id]["ix_id"],
                    "ip_address": gateway_by_id[gateway_id]["ix_ip_address"],
                }
                for gateway_id in [entry["gateway_id"] for entry in gateway_definitions]
            ],
        },
        "skyseed_link_intents": {
            "same_plane_isl": {
                "deployment_mode": "offloaded_from_compose_to_direct_backend",
                "count": len(slot_inventory.get("same_plane_isl", [])),
                "slot_names": [
                    str(slot.get("slot_name"))
                    for slot in slot_inventory.get("same_plane_isl", [])
                ],
            },
            "cross_plane_isl": {
                "deployment_mode": "offloaded_from_compose_to_direct_backend",
                "count": len(slot_inventory.get("cross_plane_isl", [])),
                "slot_names": [
                    str(slot.get("slot_name"))
                    for slot in slot_inventory.get("cross_plane_isl", [])
                ],
            },
            "gateway_gsl": {
                "deployment_mode": "offloaded_from_compose_to_direct_backend",
                "count": len(slot_inventory.get("gateway_access", [])),
                "slot_names": [
                    str(slot.get("slot_name"))
                    for slot in slot_inventory.get("gateway_access", [])
                ],
            },
            "ut_access": {
                "deployment_mode": "offloaded_from_compose_to_direct_backend",
                "count": len(slot_inventory.get("ut_access", [])),
                "slot_names": [
                    str(slot.get("slot_name"))
                    for slot in slot_inventory.get("ut_access", [])
                ],
            },
        },
        "slot_inventory": slot_inventory,
    }
    return topology


def build_starlink_shell_initial_link_state(
    topology: Dict[str, Any],
) -> Dict[str, Any]:
    slot_inventory = dict(topology.get("slot_inventory", {}))
    slots: List[Dict[str, Any]] = []

    for slot in slot_inventory.get("same_plane_isl", []):
        slots.append(
            {
                "slot_name": slot.get("slot_name"),
                "slot_type": "same_plane_isl",
                "network_name": slot.get("network_name"),
                "docker_network_name": slot.get("docker_network_name"),
                "subnet": slot.get("subnet"),
                "bridge_gateway_ip": slot.get("bridge_gateway_ip"),
                "owner_node_id": None,
                "preconnected_endpoints": [],
                "dynamic_endpoints": [],
                "occupied": False,
                "binding": None,
            }
        )

    for slot in slot_inventory.get("ut_access", []):
        slots.append(
            {
                "slot_name": slot.get("slot_name"),
                "slot_type": "ut_access",
                "network_name": slot.get("network_name"),
                "docker_network_name": slot.get("docker_network_name"),
                "subnet": slot.get("subnet"),
                "bridge_gateway_ip": slot.get("bridge_gateway_ip"),
                "owner_node_id": slot.get("ut_id"),
                "preconnected_endpoints": [dict(slot.get("preconnected_endpoint", {}))],
                "dynamic_endpoints": [],
                "occupied": False,
                "binding": None,
            }
        )

    for slot in slot_inventory.get("gateway_access", []):
        slots.append(
            {
                "slot_name": slot.get("slot_name"),
                "slot_type": "gw_access",
                "network_name": slot.get("network_name"),
                "docker_network_name": slot.get("docker_network_name"),
                "subnet": slot.get("subnet"),
                "bridge_gateway_ip": slot.get("bridge_gateway_ip"),
                "owner_node_id": slot.get("gateway_id"),
                "preconnected_endpoints": [dict(slot.get("preconnected_endpoint", {}))],
                "dynamic_endpoints": [],
                "occupied": False,
                "binding": None,
            }
        )

    for slot in slot_inventory.get("cross_plane_isl", []):
        slots.append(
            {
                "slot_name": slot.get("slot_name"),
                "slot_type": "cross_plane_isl",
                "network_name": slot.get("network_name"),
                "docker_network_name": slot.get("docker_network_name"),
                "subnet": slot.get("subnet"),
                "bridge_gateway_ip": slot.get("bridge_gateway_ip"),
                "owner_node_id": None,
                "preconnected_endpoints": [],
                "dynamic_endpoints": [],
                "occupied": False,
                "binding": None,
            }
        )

    return {
        "schema_version": "satellite-phase2-slot-state-v1",
        "runtime_initialized": True,
        "topology_schema_version": topology.get("schema_version"),
        "slots": sorted(slots, key=lambda entry: str(entry.get("slot_name", ""))),
    }


class PropagatedStarlinkScenarioOrbitProducer(OrbitTopologyProducer):
    """Produce orbit slices from pre-propagated Starlink satellite positions."""

    producer_name = "propagated_starlink_scenario_orbit_producer"

    def __init__(
        self,
        *,
        topology_path: Path,
        scenario: ExperimentScenario,
        propagated_slices: Sequence[PropagatedOrbitSlice],
    ) -> None:
        self.topology = _load_json(topology_path)
        self.scenario = scenario
        self.propagated_slices = tuple(propagated_slices)

    def _nodes_for_slice(self, *, propagated_slice: PropagatedOrbitSlice) -> Dict[str, Dict[str, Any]]:
        satellites = _topology_entry_by_id(self.topology.get("satellites", []), "satellite_id")
        gateways = _topology_entry_by_id(self.topology.get("gateways", []), "gateway_id")
        uts = _topology_entry_by_id(self.topology.get("user_terminals", []), "ut_id")
        scenario = self.scenario
        timestamp = datetime.fromisoformat(propagated_slice.timestamp_utc.replace("Z", "+00:00"))
        nodes: Dict[str, Dict[str, Any]] = {}

        for satellite_id in scenario.satellite_ids:
            satellite = satellites[str(satellite_id)]
            propagated_state = propagated_slice.satellite_states[str(satellite_id)]
            nodes[str(satellite_id)] = {
                "node_id": str(satellite_id),
                "node_type": "satellite",
                "satellite_name": satellite.get("satellite_name"),
                "norad_id": satellite.get("norad_id"),
                "orbit_id": satellite.get("orbit_id"),
                "plane_index": satellite.get("plane_index"),
                "satellite_index_in_plane": satellite.get("satellite_index_in_plane"),
                "position_state": {
                    "model": "sgp4_teme_km",
                    "x_km": propagated_state.x_km,
                    "y_km": propagated_state.y_km,
                    "z_km": propagated_state.z_km,
                    "vx_km_s": propagated_state.vx_km_s,
                    "vy_km_s": propagated_state.vy_km_s,
                    "vz_km_s": propagated_state.vz_km_s,
                    "timestamp_utc": propagated_slice.timestamp_utc,
                },
            }

        for gateway_profile in scenario.gateway_profiles:
            gateway = gateways[gateway_profile.gateway_id]
            gateway_location = scenario.ground_locations[gateway_profile.gateway_id]
            position = _ground_position_inertial(gateway_location, timestamp=timestamp)
            nodes[gateway_profile.gateway_id] = {
                "node_id": gateway_profile.gateway_id,
                "node_type": "gateway",
                "gateway_region": gateway.get("gateway_region"),
                "internet_uplink": dict(gateway.get("internet_uplink", {})),
                "fixed_coordinates": {
                    "coordinate_model": "earth_surface_lat_lon_alt",
                    "latitude_deg": gateway_location.latitude_deg,
                    "longitude_deg": gateway_location.longitude_deg,
                    "altitude_km": gateway_location.altitude_km,
                },
                "position_state": {
                    "model": "earth_centered_inertial_km",
                    "x_km": round(position.x_km, 6),
                    "y_km": round(position.y_km, 6),
                    "z_km": round(position.z_km, 6),
                    "timestamp_utc": propagated_slice.timestamp_utc,
                },
            }

        for terminal_profile in scenario.terminal_profiles:
            ut = uts[terminal_profile.ut_id]
            ut_location = scenario.ground_locations[terminal_profile.ut_id]
            position = _ground_position_inertial(ut_location, timestamp=timestamp)
            nodes[terminal_profile.ut_id] = {
                "node_id": terminal_profile.ut_id,
                "node_type": "user_terminal",
                "region": ut.get("region"),
                "identity_interface": dict(ut.get("identity_interface", {})),
                "candidate_serving_satellite_ids": list(terminal_profile.candidate_satellite_ids),
                "fixed_coordinates": {
                    "coordinate_model": "earth_surface_lat_lon_alt",
                    "latitude_deg": ut_location.latitude_deg,
                    "longitude_deg": ut_location.longitude_deg,
                    "altitude_km": ut_location.altitude_km,
                },
                "position_state": {
                    "model": "earth_centered_inertial_km",
                    "x_km": round(position.x_km, 6),
                    "y_km": round(position.y_km, 6),
                    "z_km": round(position.z_km, 6),
                    "timestamp_utc": propagated_slice.timestamp_utc,
                },
            }
        return nodes

    def iter_orbit_slices(self) -> Sequence[OrbitTopologySlice]:
        scenario = self.scenario
        zone_return_prefixes = _zone_return_prefixes_from_ut_slots(
            terminal_profiles=scenario.terminal_profiles,
            slot_inventory=dict(self.topology.get("slot_inventory", {})),
        )
        explicit_zone_profiles = {
            str(profile.zone_id): profile for profile in scenario.zone_profiles
        }
        if not explicit_zone_profiles:
            for terminal in scenario.terminal_profiles:
                zone_id = str(terminal.zone_id)
                if zone_id in explicit_zone_profiles:
                    continue
                zone_location = scenario.ground_locations.get(str(terminal.ut_id))
                if zone_location is None:
                    continue
                explicit_zone_profiles[zone_id] = ExperimentZoneProfile(
                    zone_id=zone_id,
                    display_name=zone_id,
                    region=zone_id,
                    latitude_deg=float(zone_location.latitude_deg),
                    longitude_deg=float(zone_location.longitude_deg),
                    altitude_km=float(zone_location.altitude_km),
                    candidate_satellite_ids=tuple(
                        str(satellite_id) for satellite_id in scenario.satellite_ids
                    ),
                    partition_policy="derived_from_ut_membership",
                    return_prefixes=tuple(zone_return_prefixes.get(zone_id, [])),
                    service_role="required_service",
                    active_service=True,
                    required_for_runtime=True,
                    source="derived_from_ut_membership",
                    service_domain_source=(
                        "ut_membership_fallback_not_control_plane_valid"
                    ),
                )
        ut_zone_membership = {
            terminal.ut_id: terminal.zone_id
            for terminal in scenario.terminal_profiles
        }
        slices: List[OrbitTopologySlice] = []
        for time_slice, propagated_slice in zip(scenario.time_slices, self.propagated_slices):
            orbit_input = dict(self.topology.get("orbit_input", {}))
            zones: Dict[str, Dict[str, Any]] = {}
            for zone_id, profile in sorted(explicit_zone_profiles.items()):
                zone_location = GroundLocation(
                    latitude_deg=float(profile.latitude_deg),
                    longitude_deg=float(profile.longitude_deg),
                    altitude_km=float(profile.altitude_km),
                )
                zone_position = _ground_position_inertial(
                    zone_location,
                    timestamp=datetime.fromisoformat(
                        str(propagated_slice.timestamp_utc).replace("Z", "+00:00")
                    ),
                )
                candidate_satellite_ids = (
                    list(profile.candidate_satellite_ids)
                    if profile.candidate_satellite_ids is not None
                    else list(scenario.satellite_ids)
                )
                zones[zone_id] = {
                    "zone_id": zone_id,
                    "display_name": profile.display_name,
                    "region": profile.region,
                    "partition_policy": profile.partition_policy,
                    "membership_mode": "surface_partition_zone",
                    "source": profile.source,
                    "service_domain_source": profile.service_domain_source,
                    "service_domain": {
                        "source": profile.service_domain_source,
                        "construction_order": (
                            "orbit_shell_geometry_to_coverage_zone_to_anchor_to_fec"
                        ),
                        "center": {
                            "latitude_deg": float(profile.latitude_deg),
                            "longitude_deg": float(profile.longitude_deg),
                            "altitude_km": float(profile.altitude_km),
                        },
                        "radius_km": profile.radius_km,
                        "representative_ground_points": [
                            dict(point)
                            for point in profile.representative_ground_points
                        ],
                        "boundary_points": [
                            dict(point) for point in profile.boundary_points
                        ],
                        "coverage_margin_deg": float(profile.coverage_margin_deg),
                    },
                    "service_role": profile.service_role,
                    "active_service": bool(profile.active_service),
                    "required_for_runtime": bool(profile.required_for_runtime),
                    "radius_km": profile.radius_km,
                    "coverage_margin_deg": float(profile.coverage_margin_deg),
                    "default_gateway": profile.default_gateway,
                    "preferred_gateway_ids": list(profile.preferred_gateway_ids),
                    "coordinate_gateway_ids": list(profile.coordinate_gateway_ids),
                    "fallback_to_shortest_path": bool(profile.fallback_to_shortest_path),
                    "max_additional_satellite_hops_relative_to_best_candidate": (
                        profile.max_additional_satellite_hops_relative_to_best_candidate
                    ),
                    "prefix_overrides": [
                        dict(override) for override in profile.prefix_overrides
                    ],
                    "return_prefixes": list(
                        profile.return_prefixes
                        or zone_return_prefixes.get(zone_id, [])
                    ),
                    "ut_ids": [],
                    "candidate_serving_satellite_ids": [
                        str(candidate_satellite_id)
                        for candidate_satellite_id in candidate_satellite_ids
                    ],
                    "fixed_coordinates": {
                        "coordinate_model": "earth_surface_lat_lon_alt",
                        "latitude_deg": float(profile.latitude_deg),
                        "longitude_deg": float(profile.longitude_deg),
                        "altitude_km": float(profile.altitude_km),
                    },
                    "position_state": {
                        "model": "earth_centered_inertial_km",
                        "x_km": round(zone_position.x_km, 6),
                        "y_km": round(zone_position.y_km, 6),
                        "z_km": round(zone_position.z_km, 6),
                        "timestamp_utc": propagated_slice.timestamp_utc,
                    },
                    "min_elevation_deg": float(scenario.min_elevation_deg),
                }
            for terminal in scenario.terminal_profiles:
                zone_id = str(terminal.zone_id)
                zone_entry = zones.setdefault(
                    zone_id,
                    {
                        "zone_id": zone_id,
                        "display_name": zone_id,
                        "region": zone_id,
                        "partition_policy": "derived_from_ut_membership",
                        "membership_mode": "surface_partition_zone",
                        "source": "derived_from_ut_membership",
                        "service_domain_source": (
                            "ut_membership_fallback_not_control_plane_valid"
                        ),
                        "service_domain": {
                            "source": "ut_membership_fallback_not_control_plane_valid",
                            "construction_order": "invalid_ut_membership_fallback",
                            "center": None,
                            "radius_km": None,
                            "representative_ground_points": [],
                            "boundary_points": [],
                            "coverage_margin_deg": None,
                        },
                        "service_role": "required_service",
                        "active_service": True,
                        "required_for_runtime": True,
                        "radius_km": None,
                        "coverage_margin_deg": 2.0,
                        "default_gateway": None,
                        "preferred_gateway_ids": [],
                        "prefix_overrides": [],
                        "return_prefixes": list(zone_return_prefixes.get(zone_id, [])),
                        "ut_ids": [],
                        "candidate_serving_satellite_ids": [],
                        "min_elevation_deg": float(scenario.min_elevation_deg),
                    },
                )
                if str(terminal.ut_id) not in zone_entry["ut_ids"]:
                    zone_entry["ut_ids"].append(str(terminal.ut_id))
                existing_candidates = set(
                    str(candidate_satellite_id)
                    for candidate_satellite_id in zone_entry["candidate_serving_satellite_ids"]
                )
                for candidate_satellite_id in terminal.candidate_satellite_ids:
                    candidate_id = str(candidate_satellite_id)
                    if candidate_id in existing_candidates:
                        continue
                    zone_entry["candidate_serving_satellite_ids"].append(candidate_id)
                    existing_candidates.add(candidate_id)
            slices.append(
                OrbitTopologySlice(
                    time_slice={
                        "slice_id": time_slice.slice_id,
                        "state_model": "full_state",
                        "time_semantics": str(orbit_input.get("kind", "real_tle_sgp4")),
                        "relative_time_seconds": time_slice.relative_time_seconds,
                        "duration_seconds": time_slice.duration_seconds,
                        "absolute_time_utc": propagated_slice.timestamp_utc,
                    },
                    nodes=self._nodes_for_slice(propagated_slice=propagated_slice),
                    candidate_links=_scenario_candidate_links(
                        scenario=scenario,
                        topology=self.topology,
                    ),
                    zone_generation_input=ZoneGenerationInput(
                        membership_mode="surface_partition_zone",
                        zones=zones,
                        ut_zone_membership=ut_zone_membership,
                    ),
                    planner_hints={
                        **_scenario_planner_hints(scenario, self.topology),
                        "orbit_input_kind": str(orbit_input.get("kind", "real_tle_sgp4")),
                        "orbit_source": str(orbit_input.get("source", "celestrak_starlink_gp_tle")),
                    },
                    provenance={
                        "orbit_producer": self.producer_name,
                        "slice_profile": scenario.scenario_name,
                        "position_model": str(orbit_input.get("position_model", "sgp4_teme_km")),
                        "absolute_time_utc": propagated_slice.timestamp_utc,
                    },
                )
            )
        return tuple(slices)


def build_starlink_topology_snapshot_pipeline(
    *,
    topology_path: Path,
    scenario: ExperimentScenario,
    propagated_slices: Sequence[PropagatedOrbitSlice],
    visualization_link_payload: Dict[str, Any] | None = None,
    visualization_link_frame_offset: int = 0,
) -> TopologySnapshotPipeline:
    visibility_model = (
        FrozenVisualizationLinkVisibilityModel(
            link_payload=visualization_link_payload,
            ut_min_elevation_deg=scenario.min_elevation_deg,
            frame_offset=int(visualization_link_frame_offset),
        )
        if visualization_link_payload is not None
        else SemiRealLineOfSightVisibilityModel(
            min_elevation_deg=scenario.min_elevation_deg
        )
    )
    return TopologySnapshotPipeline(
        orbit_producer=PropagatedStarlinkScenarioOrbitProducer(
            topology_path=topology_path,
            scenario=scenario,
            propagated_slices=propagated_slices,
        ),
        visibility_model=visibility_model,
        zone_generator=ZoneAnchorQualityGenerator(),
        topology_slicer=ExperimentTopologySlicer(),
    )


def write_starlink_shell_artifacts(
    *,
    topology_json_path: Path,
    link_state_json_path: Path,
    topology_inventory: Dict[str, Any],
    link_state: Dict[str, Any],
) -> None:
    _write_json(topology_json_path, topology_inventory)
    _write_json(link_state_json_path, link_state)
