#!/usr/bin/env python3
# encoding: utf-8

"""Orbit/position-driven Iteration 2 experiment topology pipeline."""

from __future__ import annotations

import ipaddress
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from seed_integration import MINI_INTERNET_STUB_AS_EXCHANGES

from contracts import TOPOLOGY_SNAPSHOT_SCHEMA_VERSION, TopologySnapshot
from topology_pipeline import (
    OrbitTopologyProducer,
    OrbitTopologySlice,
    TopologySlicer,
    TopologySnapshotPipeline,
    VisibilityModel,
    VisibilitySlice,
    ZoneGenerationInput,
    ZoneGenerator,
    ZoneProjection,
    _fingerprint,
)


EARTH_RADIUS_KM = 6371.0


@dataclass(frozen=True)
class ExperimentNodePosition:
    x_km: float
    y_km: float
    z_km: float


@dataclass(frozen=True)
class GroundLocation:
    latitude_deg: float
    longitude_deg: float
    altitude_km: float = 0.0


@dataclass(frozen=True)
class SemiRealOrbitalElements:
    altitude_km: float
    inclination_deg: float
    raan_deg: float
    phase_deg: float
    mean_motion_deg_per_second: float


@dataclass(frozen=True)
class ExperimentTimeSlice:
    slice_id: str
    relative_time_seconds: float
    duration_seconds: float


@dataclass(frozen=True)
class ExperimentProfile:
    profile_name: str
    ut_id: str
    gateway_id: str
    candidate_satellite_ids: Sequence[str]
    ping_target_ip: str
    zone_id: str = "zone-east"


@dataclass(frozen=True)
class ExperimentTerminalProfile:
    ut_id: str
    slot_name: str
    zone_id: str
    candidate_satellite_ids: Sequence[str]


@dataclass(frozen=True)
class ExperimentGatewayProfile:
    gateway_id: str
    slot_name: str
    candidate_satellite_ids: Sequence[str] | None = None


@dataclass(frozen=True)
class ExperimentZoneProfile:
    zone_id: str
    display_name: str
    region: str
    latitude_deg: float
    longitude_deg: float
    altitude_km: float = 0.0
    candidate_satellite_ids: Sequence[str] | None = None
    partition_policy: str = "explicit_zone_catalog"
    return_prefixes: Sequence[str] = ()
    service_role: str = "required_service"
    active_service: bool = True
    required_for_runtime: bool = True
    radius_km: float | None = None
    coverage_margin_deg: float = 2.0
    default_gateway: str | None = None
    preferred_gateway_ids: Sequence[str] = ()
    coordinate_gateway_ids: Sequence[str] = ()
    fallback_to_shortest_path: bool = True
    max_additional_satellite_hops_relative_to_best_candidate: int | None = None
    prefix_overrides: Sequence[Dict[str, Any]] = ()
    source: str = "explicit_zone_catalog"
    service_domain_source: str = "coverage_aware_zone_catalog"
    representative_ground_points: Sequence[Dict[str, Any]] = ()
    boundary_points: Sequence[Dict[str, Any]] = ()


@dataclass(frozen=True)
class ExperimentPrefixOverridePolicy:
    policy_name: str
    destination_prefix: str
    preferred_gateway_id: str | None
    target_node_id: str
    zone_id: str = ""
    preferred_gateway_ids: Sequence[str] = ()
    selection_mode: str = "target_affinity_shortest_path"
    fallback_to_shortest_path: bool = True


@dataclass(frozen=True)
class ExperimentScenario:
    scenario_name: str
    satellite_ids: Sequence[str]
    terminal_profiles: Sequence[ExperimentTerminalProfile]
    gateway_profiles: Sequence[ExperimentGatewayProfile]
    ping_target_ip: str
    target_host_node_id: str
    default_validation_target_node_ids: Sequence[str]
    special_prefix_override_policies: Sequence[ExperimentPrefixOverridePolicy]
    time_slices: Sequence[ExperimentTimeSlice]
    orbital_reference_time_seconds: float
    min_elevation_deg: float
    ground_locations: Dict[str, GroundLocation]
    orbital_elements: Dict[str, SemiRealOrbitalElements]
    zone_profiles: Sequence[ExperimentZoneProfile] = ()
    mini_internet_ix_offset: int = 0


DEFAULT_EXPERIMENT_PROFILE = ExperimentProfile(
    profile_name="ut1-direct-gateway-east",
    ut_id="ut1",
    gateway_id="gw1",
    candidate_satellite_ids=("sat-a1", "sat-a2", "sat-b1"),
    ping_target_ip="10.154.0.129",
)

DEFAULT_SEMI_REAL_TIME_SLICES = (
    ExperimentTimeSlice(
        slice_id="experiment-slice-semi-real-0001",
        relative_time_seconds=0.0,
        duration_seconds=360.0,
    ),
    ExperimentTimeSlice(
        slice_id="experiment-slice-semi-real-0002",
        relative_time_seconds=360.0,
        duration_seconds=360.0,
    ),
)

SEMI_REAL_GROUND_LOCATIONS = {
    "ut1": GroundLocation(latitude_deg=42.36, longitude_deg=-71.06),
    "ut2": GroundLocation(latitude_deg=39.95, longitude_deg=-75.16),
    "gw1": GroundLocation(latitude_deg=40.71, longitude_deg=-74.00),
    "gw2": GroundLocation(latitude_deg=40.00, longitude_deg=-95.00),
}

SEMI_REAL_ORBITAL_ELEMENTS = {
    "sat-a1": SemiRealOrbitalElements(
        altitude_km=550.0,
        inclination_deg=53.0,
        raan_deg=-140.0,
        phase_deg=0.0,
        mean_motion_deg_per_second=0.067,
    ),
    "sat-a2": SemiRealOrbitalElements(
        altitude_km=550.0,
        inclination_deg=53.0,
        raan_deg=-140.0,
        phase_deg=20.0,
        mean_motion_deg_per_second=0.067,
    ),
    "sat-a3": SemiRealOrbitalElements(
        altitude_km=550.0,
        inclination_deg=53.0,
        raan_deg=-140.0,
        phase_deg=55.0,
        mean_motion_deg_per_second=0.067,
    ),
    "sat-b1": SemiRealOrbitalElements(
        altitude_km=550.0,
        inclination_deg=53.0,
        raan_deg=-100.0,
        phase_deg=10.0,
        mean_motion_deg_per_second=0.067,
    ),
    "sat-b2": SemiRealOrbitalElements(
        altitude_km=550.0,
        inclination_deg=53.0,
        raan_deg=-100.0,
        phase_deg=35.0,
        mean_motion_deg_per_second=0.067,
    ),
}

SYNTHETIC_POSITION_MAP = {
    "ut1": ExperimentNodePosition(0.0, 0.0, 0.0),
    "gw1": ExperimentNodePosition(80.0, 0.0, 0.0),
    "sat-a1": ExperimentNodePosition(40.0, 0.0, 550.0),
    "sat-a2": ExperimentNodePosition(820.0, 0.0, 550.0),
    "sat-b1": ExperimentNodePosition(980.0, 160.0, 550.0),
}


SMALL_SCALE_SEMI_REAL_TIME_SLICES = (
    ExperimentTimeSlice(
        slice_id="small-scale-slice-0001",
        relative_time_seconds=-120.0,
        duration_seconds=100.0,
    ),
    ExperimentTimeSlice(
        slice_id="small-scale-slice-0002",
        relative_time_seconds=-20.0,
        duration_seconds=200.0,
    ),
    ExperimentTimeSlice(
        slice_id="small-scale-slice-0003",
        relative_time_seconds=180.0,
        duration_seconds=210.0,
    ),
    ExperimentTimeSlice(
        slice_id="small-scale-slice-0004",
        relative_time_seconds=390.0,
        duration_seconds=40.0,
    ),
    ExperimentTimeSlice(
        slice_id="small-scale-slice-0005",
        relative_time_seconds=430.0,
        duration_seconds=30.0,
    ),
)

DYNAMIC_CROSS_PLANE_MAX_DISTANCE_KM = 3300.0


SMALL_SCALE_EXPERIMENT_SCENARIO = ExperimentScenario(
    scenario_name="small-scale-semi-real-internet",
    satellite_ids=("sat-a1", "sat-a2", "sat-a3", "sat-b1", "sat-b2"),
    terminal_profiles=(
        ExperimentTerminalProfile(
            ut_id="ut1",
            slot_name="ut1-access-slot0",
            zone_id="zone-east",
            candidate_satellite_ids=("sat-a1", "sat-a2", "sat-b1"),
        ),
        ExperimentTerminalProfile(
            ut_id="ut2",
            slot_name="ut2-access-slot0",
            zone_id="zone-east-center",
            candidate_satellite_ids=("sat-a2", "sat-a3", "sat-b2"),
        ),
    ),
    gateway_profiles=(
        ExperimentGatewayProfile(
            gateway_id="gw1",
            slot_name="gw1-gateway-slot0",
            candidate_satellite_ids=("sat-a1", "sat-a2", "sat-a3", "sat-b1", "sat-b2"),
        ),
        ExperimentGatewayProfile(
            gateway_id="gw2",
            slot_name="gw2-gateway-slot0",
            candidate_satellite_ids=("sat-a1", "sat-a2", "sat-a3", "sat-b1", "sat-b2"),
        ),
    ),
    ping_target_ip="10.154.0.129",
    target_host_node_id="as154-host-new",
    default_validation_target_node_ids=("as154-host-new", "as160-host1"),
    special_prefix_override_policies=(
        ExperimentPrefixOverridePolicy(
            policy_name="asn160_via_west_gateway",
            destination_prefix="10.160.0.0/24",
            preferred_gateway_id="gw2",
            target_node_id="as160-host1",
        ),
    ),
    time_slices=SMALL_SCALE_SEMI_REAL_TIME_SLICES,
    orbital_reference_time_seconds=540.0,
    min_elevation_deg=5.0,
    ground_locations={
        "ut1": GroundLocation(latitude_deg=42.36, longitude_deg=-71.06),
        "ut2": GroundLocation(latitude_deg=39.95, longitude_deg=-75.16),
        "gw1": GroundLocation(latitude_deg=40.71, longitude_deg=-74.00),
        # Intentionally semi-real and offset from gw1 so one slice prefers a cross-orbit west-gateway path.
        "gw2": GroundLocation(latitude_deg=40.00, longitude_deg=-69.00),
    },
    orbital_elements={
        satellite_id: orbital_elements
        for satellite_id, orbital_elements in SEMI_REAL_ORBITAL_ELEMENTS.items()
        if satellite_id in {"sat-a1", "sat-a2", "sat-a3", "sat-b1", "sat-b2"}
    },
)


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _ground_position(location: GroundLocation) -> ExperimentNodePosition:
    latitude_rad = math.radians(location.latitude_deg)
    longitude_rad = math.radians(location.longitude_deg)
    radius_km = EARTH_RADIUS_KM + location.altitude_km
    return ExperimentNodePosition(
        x_km=radius_km * math.cos(latitude_rad) * math.cos(longitude_rad),
        y_km=radius_km * math.cos(latitude_rad) * math.sin(longitude_rad),
        z_km=radius_km * math.sin(latitude_rad),
    )


def _orbital_position(
    orbital_elements: SemiRealOrbitalElements,
    *,
    relative_time_seconds: float,
) -> ExperimentNodePosition:
    orbital_radius_km = EARTH_RADIUS_KM + orbital_elements.altitude_km
    argument_of_latitude_rad = math.radians(
        orbital_elements.phase_deg
        + orbital_elements.mean_motion_deg_per_second * relative_time_seconds
    )
    inclination_rad = math.radians(orbital_elements.inclination_deg)
    raan_rad = math.radians(orbital_elements.raan_deg)

    x_orbital = orbital_radius_km * math.cos(argument_of_latitude_rad)
    y_orbital = orbital_radius_km * math.sin(argument_of_latitude_rad)

    x_inclined = x_orbital
    y_inclined = y_orbital * math.cos(inclination_rad)
    z_inclined = y_orbital * math.sin(inclination_rad)

    return ExperimentNodePosition(
        x_km=x_inclined * math.cos(raan_rad) - y_inclined * math.sin(raan_rad),
        y_km=x_inclined * math.sin(raan_rad) + y_inclined * math.cos(raan_rad),
        z_km=z_inclined,
    )


def _distance_km(
    first: ExperimentNodePosition,
    second: ExperimentNodePosition,
) -> float:
    return math.sqrt(
        (first.x_km - second.x_km) ** 2
        + (first.y_km - second.y_km) ** 2
        + (first.z_km - second.z_km) ** 2
    )


def _elevation_deg(
    owner_position: ExperimentNodePosition,
    candidate_position: ExperimentNodePosition,
) -> float:
    los_x = candidate_position.x_km - owner_position.x_km
    los_y = candidate_position.y_km - owner_position.y_km
    los_z = candidate_position.z_km - owner_position.z_km
    los_norm = math.sqrt(los_x**2 + los_y**2 + los_z**2)
    owner_norm = math.sqrt(
        owner_position.x_km**2 + owner_position.y_km**2 + owner_position.z_km**2
    )
    if los_norm == 0.0 or owner_norm == 0.0:
        return -90.0
    vertical_component = (
        los_x * owner_position.x_km
        + los_y * owner_position.y_km
        + los_z * owner_position.z_km
    ) / (los_norm * owner_norm)
    bounded_vertical = max(-1.0, min(1.0, vertical_component))
    return math.degrees(math.asin(bounded_vertical))


def _topology_entry_by_id(
    entries: Iterable[Dict[str, Any]],
    key: str,
) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for entry in entries:
        entry_id = str(entry.get(key))
        result[entry_id] = dict(entry)
    return result


def _zone_profile_by_id(
    scenario: ExperimentScenario,
) -> Dict[str, ExperimentZoneProfile]:
    return {
        str(profile.zone_id): profile
        for profile in scenario.zone_profiles
    }


def _derived_zone_profiles_from_terminals(
    scenario: ExperimentScenario,
) -> Dict[str, ExperimentZoneProfile]:
    derived_profiles: Dict[str, ExperimentZoneProfile] = {}
    for terminal in scenario.terminal_profiles:
        zone_id = str(terminal.zone_id)
        if zone_id in derived_profiles:
            continue
        location = scenario.ground_locations.get(str(terminal.ut_id))
        if location is None:
            continue
        derived_profiles[zone_id] = ExperimentZoneProfile(
            zone_id=zone_id,
            display_name=zone_id,
            region=zone_id,
            latitude_deg=float(location.latitude_deg),
            longitude_deg=float(location.longitude_deg),
            altitude_km=float(location.altitude_km),
            candidate_satellite_ids=tuple(str(satellite_id) for satellite_id in scenario.satellite_ids),
            partition_policy="derived_from_ut_membership",
            source="derived_from_ut_membership",
            service_domain_source="ut_membership_fallback_not_control_plane_valid",
        )
    return derived_profiles


def _slot_candidates(
    *,
    profile: ExperimentProfile,
    topology: Dict[str, Any],
) -> List[Dict[str, Any]]:
    slot_inventory = topology.get("slot_inventory", {})
    ut_slot = next(
        slot
        for slot in slot_inventory.get("ut_access", [])
        if str(slot.get("slot_name")) == "ut1-access-slot0"
    )
    gw_slot = next(
        slot
        for slot in slot_inventory.get("gateway_access", [])
        if str(slot.get("slot_name")) == "gw1-gateway-slot0"
    )

    ut_candidates = []
    for satellite_id in profile.candidate_satellite_ids:
        ut_candidates.append(
            {
                "satellite_id": satellite_id,
                "binding": {
                    "ut_id": profile.ut_id,
                    "satellite_id": satellite_id,
                },
                "endpoints": [
                    dict(ut_slot.get("preconnected_endpoint", {})),
                    {
                        "node_id": satellite_id,
                        "node_type": "satellite",
                        "interface_name": ut_slot.get("dynamic_endpoint_template", {}).get(
                            "interface_name"
                        ),
                        "ip_address": ut_slot.get("dynamic_endpoint_template", {}).get(
                            "ip_address"
                        ),
                    },
                ],
            }
        )

    gw_candidates = []
    for satellite_id in profile.candidate_satellite_ids:
        gw_candidates.append(
            {
                "satellite_id": satellite_id,
                "binding": {
                    "gateway_id": profile.gateway_id,
                    "satellite_id": satellite_id,
                },
                "endpoints": [
                    dict(gw_slot.get("preconnected_endpoint", {})),
                    {
                        "node_id": satellite_id,
                        "node_type": "satellite",
                        "interface_name": gw_slot.get("dynamic_endpoint_template", {}).get(
                            "interface_name"
                        ),
                        "ip_address": gw_slot.get("dynamic_endpoint_template", {}).get(
                            "ip_address"
                        ),
                    },
                ],
            }
        )

    return [
        {
            "link_id": str(ut_slot.get("slot_name")),
            "slot_type": "ut_access",
            "subnet": ut_slot.get("subnet"),
            "network_name": ut_slot.get("network_name"),
            "docker_network_name": ut_slot.get("docker_network_name"),
            "visibility_relation": "ut_to_satellite_access",
            "visibility_owner_node_id": profile.ut_id,
            "candidate_bindings": ut_candidates,
        },
        {
            "link_id": str(gw_slot.get("slot_name")),
            "slot_type": "gw_access",
            "subnet": gw_slot.get("subnet"),
            "network_name": gw_slot.get("network_name"),
            "docker_network_name": gw_slot.get("docker_network_name"),
            "visibility_relation": "gateway_to_satellite_access",
            "visibility_owner_node_id": profile.gateway_id,
            "candidate_bindings": gw_candidates,
        },
    ]


def _planner_hints(profile: ExperimentProfile) -> Dict[str, Any]:
    return {
        "experiment_profile": profile.profile_name,
        "experiment_scope": "minimal_ut_to_gateway_ping",
        "gateway_ids": [profile.gateway_id],
        "internet_ping_target_ip": profile.ping_target_ip,
        "target_host_node_id": "as154-host-new",
    }


def _slot_inventory_entry(
    *,
    topology: Dict[str, Any],
    slot_name: str,
) -> Dict[str, Any]:
    for slot_group in topology.get("slot_inventory", {}).values():
        for slot in slot_group:
            if str(slot.get("slot_name")) == slot_name:
                return dict(slot)
    raise KeyError(f"unknown slot_name in topology inventory: {slot_name}")


def _scenario_candidate_links(
    *,
    scenario: ExperimentScenario,
    topology: Dict[str, Any],
) -> List[Dict[str, Any]]:
    candidate_links: List[Dict[str, Any]] = []
    scenario_satellite_ids = set(str(satellite_id) for satellite_id in scenario.satellite_ids)

    for terminal in scenario.terminal_profiles:
        slot = _slot_inventory_entry(topology=topology, slot_name=terminal.slot_name)
        ut_candidates = []
        for satellite_id in terminal.candidate_satellite_ids:
            satellite_text = str(satellite_id)
            if satellite_text not in scenario_satellite_ids:
                continue
            ut_candidates.append(
                {
                    "satellite_id": satellite_text,
                    "binding": {
                        "ut_id": terminal.ut_id,
                        "satellite_id": satellite_text,
                    },
                    "endpoints": [
                        dict(slot.get("preconnected_endpoint", {})),
                        {
                            "node_id": satellite_text,
                            "node_type": "satellite",
                            "interface_name": slot.get("dynamic_endpoint_template", {}).get(
                                "interface_name"
                            ),
                            "ip_address": slot.get("dynamic_endpoint_template", {}).get(
                                "ip_address"
                            ),
                        },
                    ],
                }
            )
        candidate_links.append(
            {
                "link_id": str(slot.get("slot_name")),
                "slot_type": "ut_access",
                "subnet": slot.get("subnet"),
                "network_name": slot.get("network_name"),
                "docker_network_name": slot.get("docker_network_name"),
                "visibility_relation": "ut_to_satellite_access",
                "visibility_owner_node_id": terminal.ut_id,
                "candidate_bindings": ut_candidates,
            }
        )

    for gateway in scenario.gateway_profiles:
        slot = _slot_inventory_entry(topology=topology, slot_name=gateway.slot_name)
        allowed_satellite_ids = set(
            str(satellite_id)
            for satellite_id in (
                gateway.candidate_satellite_ids
                if gateway.candidate_satellite_ids is not None
                else slot.get("candidate_satellite_ids", [])
            )
        )
        gateway_candidates = []
        for satellite_id in sorted(allowed_satellite_ids & scenario_satellite_ids):
            gateway_candidates.append(
                {
                    "satellite_id": satellite_id,
                    "binding": {
                        "gateway_id": gateway.gateway_id,
                        "satellite_id": satellite_id,
                    },
                    "endpoints": [
                        dict(slot.get("preconnected_endpoint", {})),
                        {
                            "node_id": satellite_id,
                            "node_type": "satellite",
                            "interface_name": slot.get("dynamic_endpoint_template", {}).get(
                                "interface_name"
                            ),
                            "ip_address": slot.get("dynamic_endpoint_template", {}).get(
                                "ip_address"
                            ),
                        },
                    ],
                }
            )
        candidate_links.append(
            {
                "link_id": str(slot.get("slot_name")),
                "slot_type": "gw_access",
                "subnet": slot.get("subnet"),
                "network_name": slot.get("network_name"),
                "docker_network_name": slot.get("docker_network_name"),
                "visibility_relation": "gateway_to_satellite_access",
                "visibility_owner_node_id": gateway.gateway_id,
                "candidate_bindings": gateway_candidates,
            }
        )

    for slot in topology.get("slot_inventory", {}).get("same_plane_isl", []):
        candidate_pairs = list(slot.get("candidate_pairs", []))
        if len(candidate_pairs) != 1:
            continue
        candidate_pair = dict(candidate_pairs[0])
        satellite_a_id = str(candidate_pair.get("satellite_a_id"))
        satellite_b_id = str(candidate_pair.get("satellite_b_id"))
        if {satellite_a_id, satellite_b_id} - scenario_satellite_ids:
            continue
        endpoint_a_template = dict(slot.get("endpoint_a_template", {}))
        endpoint_b_template = dict(slot.get("endpoint_b_template", {}))
        candidate_links.append(
            {
                "link_id": str(slot.get("slot_name")),
                "slot_type": "same_plane_isl",
                "subnet": slot.get("subnet"),
                "network_name": slot.get("network_name"),
                "docker_network_name": slot.get("docker_network_name"),
                "occupied": True,
                "binding": {
                    "satellite_a_id": satellite_a_id,
                    "satellite_b_id": satellite_b_id,
                    "satellite_a_direction": str(
                        candidate_pair.get("satellite_a_direction", "front")
                    ),
                    "satellite_b_direction": str(
                        candidate_pair.get("satellite_b_direction", "back")
                    ),
                    "orbit_scope": slot.get("orbit_scope"),
                },
                "endpoints": [
                    {
                        "node_id": satellite_a_id,
                        "node_type": "satellite",
                        "interface_name": endpoint_a_template.get("interface_name"),
                        "ip_address": endpoint_a_template.get("ip_address"),
                        "logical_direction": endpoint_a_template.get("logical_direction"),
                    },
                    {
                        "node_id": satellite_b_id,
                        "node_type": "satellite",
                        "interface_name": endpoint_b_template.get("interface_name"),
                        "ip_address": endpoint_b_template.get("ip_address"),
                        "logical_direction": endpoint_b_template.get("logical_direction"),
                    },
                ],
                "visibility_relation": "always_on_same_plane_satellite_to_satellite_isl",
                "activation_model": slot.get("activation_model"),
            }
        )

    for slot in topology.get("slot_inventory", {}).get("cross_plane_isl", []):
        orbit_a_satellite_ids = {
            str(satellite_id) for satellite_id in slot.get("orbit_a_satellite_ids", [])
        }
        orbit_b_satellite_ids = {
            str(satellite_id) for satellite_id in slot.get("orbit_b_satellite_ids", [])
        }
        if not orbit_a_satellite_ids.issubset(scenario_satellite_ids):
            continue
        if not orbit_b_satellite_ids.issubset(scenario_satellite_ids):
            continue
        endpoint_a_template = dict(slot.get("endpoint_a_template", {}))
        endpoint_b_template = dict(slot.get("endpoint_b_template", {}))
        candidate_bindings = []
        for candidate_pair in slot.get("candidate_pairs", []):
            satellite_a_id = str(candidate_pair.get("satellite_a_id"))
            satellite_b_id = str(candidate_pair.get("satellite_b_id"))
            if satellite_a_id not in scenario_satellite_ids:
                continue
            if satellite_b_id not in scenario_satellite_ids:
                continue
            candidate_bindings.append(
                {
                    "satellite_pair_id": f"{satellite_a_id}:{satellite_b_id}",
                    "binding": {
                        "satellite_a_id": satellite_a_id,
                        "satellite_b_id": satellite_b_id,
                        "satellite_a_direction": str(
                            candidate_pair.get("satellite_a_direction", "right")
                        ),
                        "satellite_b_direction": str(
                            candidate_pair.get("satellite_b_direction", "left")
                        ),
                        "orbit_pair": list(slot.get("orbit_pair", [])),
                    },
                    "endpoints": [
                        {
                            "node_id": satellite_a_id,
                            "node_type": endpoint_a_template.get("node_type"),
                            "interface_name": endpoint_a_template.get("interface_name"),
                            "ip_address": endpoint_a_template.get("ip_address"),
                            "logical_direction": candidate_pair.get(
                                "satellite_a_direction", "right"
                            ),
                        },
                        {
                            "node_id": satellite_b_id,
                            "node_type": endpoint_b_template.get("node_type"),
                            "interface_name": endpoint_b_template.get("interface_name"),
                            "ip_address": endpoint_b_template.get("ip_address"),
                            "logical_direction": candidate_pair.get(
                                "satellite_b_direction", "left"
                            ),
                        },
                    ],
                }
            )
        candidate_links.append(
            {
                "link_id": str(slot.get("slot_name")),
                "slot_type": "cross_plane_isl",
                "subnet": slot.get("subnet"),
                "network_name": slot.get("network_name"),
                "docker_network_name": slot.get("docker_network_name"),
                "bridge_gateway_ip": slot.get("bridge_gateway_ip"),
                "orbit_pair": list(slot.get("orbit_pair", [])),
                "visibility_relation": "dynamic_cross_plane_candidate_isl",
                "candidate_bindings": candidate_bindings,
                "activation_model": "distance_threshold_dynamic_cross_plane",
            }
        )
    return candidate_links


def _scenario_planner_hints(
    scenario: ExperimentScenario,
    topology: Dict[str, Any],
) -> Dict[str, Any]:
    def _great_circle_distance_km(
        first: GroundLocation,
        second: GroundLocation,
    ) -> float:
        first_lat = math.radians(float(first.latitude_deg))
        second_lat = math.radians(float(second.latitude_deg))
        delta_lat = second_lat - first_lat
        delta_lon = math.radians(float(second.longitude_deg) - float(first.longitude_deg))
        hav = (
            math.sin(delta_lat / 2.0) ** 2
            + math.cos(first_lat)
            * math.cos(second_lat)
            * math.sin(delta_lon / 2.0) ** 2
        )
        return 2.0 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(max(0.0, hav))))

    external_targets = {
        str(target.get("node_id")): {
            "node_id": str(target.get("node_id")),
            "ip_address": str(target.get("ip_address")),
            "asn": target.get("asn"),
            "description": target.get("description"),
        }
        for target in topology.get("mini_internet", {}).get("external_test_targets", [])
    }
    gateway_ix_peerings = {
        str(peering.get("gateway_id")): {
            "gateway_id": str(peering.get("gateway_id")),
            "ix_id": peering.get("ix_id"),
            "ip_address": peering.get("ip_address"),
        }
        for peering in topology.get("mini_internet", {}).get("gateway_ix_peerings", [])
    }
    gateways_by_id = _topology_entry_by_id(topology.get("gateways", []), "gateway_id")
    user_terminals_by_id = _topology_entry_by_id(topology.get("user_terminals", []), "ut_id")
    validation_targets = [
        dict(external_targets[target_node_id])
        for target_node_id in scenario.default_validation_target_node_ids
        if target_node_id in external_targets
    ]
    primary_target = (
        dict(validation_targets[0])
        if validation_targets
        else {
            "node_id": scenario.target_host_node_id,
            "ip_address": scenario.ping_target_ip,
        }
    )
    zone_membership: Dict[str, List[str]] = {}
    for terminal in scenario.terminal_profiles:
        zone_membership.setdefault(str(terminal.zone_id), []).append(str(terminal.ut_id))
    zone_profiles = _zone_profile_by_id(scenario)
    if not zone_profiles:
        zone_profiles = _derived_zone_profiles_from_terminals(scenario)

    zone_default_gateway_preferences: Dict[str, Dict[str, Any]] = {}
    for zone_id in sorted(set(zone_profiles) | set(zone_membership)):
        zone_ut_ids = list(zone_membership.get(zone_id, []))
        zone_profile = zone_profiles.get(zone_id)
        zone_regions: List[str] = []
        zone_locations: List[GroundLocation] = []
        if zone_profile is not None:
            zone_locations.append(
                GroundLocation(
                    latitude_deg=float(zone_profile.latitude_deg),
                    longitude_deg=float(zone_profile.longitude_deg),
                    altitude_km=float(zone_profile.altitude_km),
                )
            )
            zone_region = str(zone_profile.region).strip()
            if zone_region:
                zone_regions.append(zone_region)
        for ut_id in zone_ut_ids:
            ut_entry = dict(user_terminals_by_id.get(ut_id, {}))
            ut_location = scenario.ground_locations.get(ut_id)
            if ut_location is None:
                continue
            zone_locations.append(ut_location)
            ut_region = str(ut_entry.get("region", "")).strip()
            if ut_region and ut_region not in zone_regions:
                zone_regions.append(ut_region)
        if not zone_locations:
            continue
        ranked_gateways: List[Dict[str, Any]] = []
        for gateway_profile in scenario.gateway_profiles:
            gateway_id = str(gateway_profile.gateway_id)
            gateway_entry = dict(gateways_by_id.get(gateway_id, {}))
            gateway_coordinates = dict(gateway_entry.get("catalog_coordinates", {}))
            gateway_location = GroundLocation(
                latitude_deg=float(gateway_coordinates.get("latitude_deg", 0.0)),
                longitude_deg=float(gateway_coordinates.get("longitude_deg", 0.0)),
                altitude_km=float(gateway_coordinates.get("altitude_km", 0.0)),
            )
            gateway_region = str(gateway_entry.get("gateway_region", "")).strip()
            per_ut_distances = [
                _great_circle_distance_km(ut_location, gateway_location)
                for ut_location in zone_locations
            ]
            ranked_gateways.append(
                {
                    "gateway_id": gateway_id,
                    "gateway_region": gateway_region,
                    "distance_km": round(sum(per_ut_distances) / len(per_ut_distances), 3),
                    "min_distance_km": round(min(per_ut_distances), 3),
                    "max_distance_km": round(max(per_ut_distances), 3),
                    "region_match_rank": (
                        0
                        if zone_regions
                        and any(
                            gateway_region == zone_region
                            or gateway_region.startswith(f"{zone_region}-")
                            for zone_region in zone_regions
                        )
                        else 1
                    ),
                }
            )
        ranked_gateways.sort(
            key=lambda entry: (
                int(entry["region_match_rank"]),
                float(entry["distance_km"]),
                float(entry["min_distance_km"]),
                str(entry["gateway_id"]),
            )
        )
        ranked_gateway_ids = [
            str(entry["gateway_id"])
            for entry in ranked_gateways
        ]
        explicit_preferred_gateway_ids = []
        if zone_profile is not None:
            explicit_preferred_gateway_ids = [
                str(gateway_id)
                for gateway_id in (
                    list(zone_profile.preferred_gateway_ids)
                    or (
                        [zone_profile.default_gateway]
                        if zone_profile.default_gateway
                        else []
                    )
                )
                if str(gateway_id).strip()
            ]
        explicit_preferred_gateway_ids = [
            gateway_id
            for gateway_id in explicit_preferred_gateway_ids
            if gateway_id in ranked_gateway_ids
        ]
        gateway_order = [
            *explicit_preferred_gateway_ids,
            *[
                gateway_id
                for gateway_id in ranked_gateway_ids
                if gateway_id not in explicit_preferred_gateway_ids
            ],
        ]
        preferred_gateway_pool = [
            str(entry["gateway_id"])
            for entry in ranked_gateways[:2]
        ]
        if explicit_preferred_gateway_ids:
            preferred_gateway_pool = gateway_order[:2]
            ranked_gateway_ids = gateway_order
        zone_default_gateway_preferences[zone_id] = {
            "zone_id": zone_id,
            "ut_ids": list(zone_ut_ids),
            "zone_region": zone_regions[0] if zone_regions else None,
            "service_role": (
                zone_profile.service_role if zone_profile is not None else "required_service"
            ),
            "active_service": (
                bool(zone_profile.active_service) if zone_profile is not None else True
            ),
            "required_for_runtime": (
                bool(zone_profile.required_for_runtime)
                if zone_profile is not None
                else True
            ),
            "default_gateway": (
                zone_profile.default_gateway if zone_profile is not None else None
            ),
            "selection_mode": "stable_preferred_gateway_order",
            "fallback_to_shortest_path": (
                bool(zone_profile.fallback_to_shortest_path)
                if zone_profile is not None
                else True
            ),
            "max_additional_satellite_hops_relative_to_best_candidate": (
                zone_profile.max_additional_satellite_hops_relative_to_best_candidate
                if zone_profile is not None
                and zone_profile.max_additional_satellite_hops_relative_to_best_candidate
                is not None
                else 2
            ),
            "candidate_pool_policy": "zone_ranked_gateway_catalog",
            "preferred_gateway_ids": preferred_gateway_pool,
            "gateway_ids": ranked_gateway_ids,
            "regional_preferred_gateway_ids": preferred_gateway_pool,
            "ranked_candidates": ranked_gateways,
        }
    gateway_ix_id_offset = int(getattr(scenario, "mini_internet_ix_offset", 0))
    special_prefix_overrides = []
    for policy in scenario.special_prefix_override_policies:
        target = external_targets.get(policy.target_node_id)
        if target is None and str(policy.destination_prefix).strip():
            try:
                destination_network = ipaddress.ip_network(
                    str(policy.destination_prefix),
                    strict=False,
                )
            except ValueError:
                destination_network = None
            if destination_network is not None:
                for candidate in external_targets.values():
                    try:
                        candidate_ip = ipaddress.ip_address(
                            str(candidate.get("ip_address") or "")
                        )
                    except ValueError:
                        continue
                    if candidate_ip in destination_network:
                        target = dict(candidate)
                        break
        target_asn = None if target is None else target.get("asn")
        raw_target_exchange_id = MINI_INTERNET_STUB_AS_EXCHANGES.get(int(target_asn or 0))
        target_exchange_id = (
            int(raw_target_exchange_id) + int(gateway_ix_id_offset)
            if raw_target_exchange_id is not None
            else None
        )
        preferred_gateway_ids = [
            str(gateway_id)
            for gateway_id in policy.preferred_gateway_ids
            if str(gateway_id).strip()
        ]
        if (
            not preferred_gateway_ids
            and policy.preferred_gateway_id is not None
            and str(policy.preferred_gateway_id).strip()
        ):
            preferred_gateway_ids = [str(policy.preferred_gateway_id).strip()]
        if not preferred_gateway_ids:
            preferred_gateway_ids = [
                str(gateway_id)
                for gateway_id, peering in sorted(gateway_ix_peerings.items())
                if target_exchange_id is not None
                and int(peering.get("ix_id", -1)) == int(target_exchange_id)
            ]
        special_prefix_overrides.append(
            {
                "policy_name": policy.policy_name,
                "zone_id": policy.zone_id,
                "destination_prefix": policy.destination_prefix,
                "preferred_gateway_id": policy.preferred_gateway_id,
                "preferred_gateway_ids": preferred_gateway_ids,
                "selection_mode": policy.selection_mode,
                "fallback_to_shortest_path": bool(policy.fallback_to_shortest_path),
                "require_competitive_path_vs_default": True,
                "max_additional_satellite_hops_relative_to_default": 4,
                "target_asn": target_asn,
                "target_exchange_id": target_exchange_id,
                "raw_target_exchange_id": raw_target_exchange_id,
                "gateway_ix_id_offset": gateway_ix_id_offset,
                "target": dict(target or {}),
            }
        )
    return {
        "experiment_profile": scenario.scenario_name,
        "experiment_scope": "small_scale_ut_to_gateway_multi_target_validation",
        "gateway_ids": [gateway.gateway_id for gateway in scenario.gateway_profiles],
        "internet_ping_target_ip": primary_target.get("ip_address"),
        "target_host_node_id": primary_target.get("node_id"),
        "ut_ids": [terminal.ut_id for terminal in scenario.terminal_profiles],
        "internet_validation_targets": validation_targets,
        "zone_default_gateway_preferences": zone_default_gateway_preferences,
        "special_prefix_overrides": special_prefix_overrides,
        "gateway_ix_catalog": {
            gateway_id: {
                "gateway_region": gateways_by_id.get(gateway_id, {}).get("gateway_region"),
                "internet_uplink": dict(
                    gateways_by_id.get(gateway_id, {}).get("internet_uplink", {})
                ),
                "ix_peering": dict(gateway_ix_peerings.get(gateway_id, {})),
            }
            for gateway_id in [gateway.gateway_id for gateway in scenario.gateway_profiles]
        },
        "mini_internet_integration": {
            "inventory_source": "seed_generated_topology_json",
            "uses_existing_seed_mini_internet": True,
            "transit_as_count": len(topology.get("mini_internet", {}).get("transit_ases", [])),
            "stub_as_count": len(topology.get("mini_internet", {}).get("stub_ases", [])),
            "selected_validation_target_node_ids": [
                str(target.get("node_id")) for target in validation_targets
            ],
            "external_test_target_node_ids": sorted(external_targets),
            "gateway_ix_ids": {
                gateway_id: gateway_ix_peerings.get(gateway_id, {}).get("ix_id")
                for gateway_id in [gateway.gateway_id for gateway in scenario.gateway_profiles]
            },
            "satellite_internet_boundary": "gateway_nat_egress_to_seed_mini_internet",
            "visualization_expectation": (
                "reuses_seed_base_topology_and_phase1_ground_internet_structure"
            ),
        },
    }


def _candidate_visibility_record(
    *,
    link: Dict[str, Any],
    candidate: Dict[str, Any],
    metric_name: str,
    metric_value: float,
) -> Dict[str, Any]:
    return {
        "satellite_id": str(candidate.get("satellite_id")),
        "binding": dict(candidate.get("binding", {})),
        "endpoints": [dict(endpoint) for endpoint in candidate.get("endpoints", [])],
        "slot_type": str(link.get("slot_type")),
        metric_name: round(metric_value, 3),
    }


def _zone_visible_candidate_sets(
    *,
    zone: Dict[str, Any],
    visibility_slice: VisibilitySlice,
) -> Dict[str, List[Dict[str, Any]]]:
    return {
        str(ut_id): [
            dict(candidate)
            for candidate in visibility_slice.candidate_visibility.get(str(ut_id), [])
            if str(candidate.get("slot_type")) == "ut_access"
        ]
        for ut_id in zone.get("ut_ids", [])
    }


def _satellite_adjacency_from_active_links(
    active_links: Sequence[Dict[str, Any]],
) -> Dict[str, set[str]]:
    adjacency: Dict[str, set[str]] = {}
    for link in active_links:
        if str(link.get("slot_type")) not in {"same_plane_isl", "fixed_isl", "cross_plane_isl"}:
            continue
        endpoint_ids = [
            str(endpoint.get("node_id"))
            for endpoint in list(link.get("endpoints", []))
            if str(endpoint.get("node_type", "satellite")) == "satellite"
        ]
        if len(endpoint_ids) != 2:
            continue
        first, second = endpoint_ids
        adjacency.setdefault(first, set()).add(second)
        adjacency.setdefault(second, set()).add(first)
    return adjacency


def _shortest_hop_count(
    *,
    adjacency: Dict[str, set[str]],
    start_node_id: str,
    goal_node_id: str,
) -> int | None:
    if start_node_id == goal_node_id:
        return 0
    frontier: List[tuple[str, int]] = [(start_node_id, 0)]
    visited = {start_node_id}
    while frontier:
        current, distance = frontier.pop(0)
        for neighbor in sorted(adjacency.get(current, set())):
            if neighbor in visited:
                continue
            if neighbor == goal_node_id:
                return distance + 1
            visited.add(neighbor)
            frontier.append((neighbor, distance + 1))
    return None


def _active_gateway_satellite_ids(
    visibility_slice: VisibilitySlice,
) -> Dict[str, str]:
    gateway_satellites: Dict[str, str] = {}
    for link in visibility_slice.active_links:
        if str(link.get("slot_type")) != "gw_access":
            continue
        gateway_id = ""
        satellite_id = ""
        for endpoint in list(link.get("endpoints", [])):
            endpoint_node_id = str(endpoint.get("node_id"))
            endpoint_type = str(endpoint.get("node_type"))
            if endpoint_type == "gateway":
                gateway_id = endpoint_node_id
            elif endpoint_type == "satellite":
                satellite_id = endpoint_node_id
        if gateway_id and satellite_id:
            gateway_satellites[gateway_id] = satellite_id
    return gateway_satellites


def _zone_position(zone: Dict[str, Any]) -> ExperimentNodePosition | None:
    position = dict(zone.get("position_state", {}))
    if not position:
        return None
    return ExperimentNodePosition(
        x_km=float(position.get("x_km", 0.0)),
        y_km=float(position.get("y_km", 0.0)),
        z_km=float(position.get("z_km", 0.0)),
    )


class ZoneAnchorQualityGenerator(ZoneGenerator):
    """Choose a stable responsible satellite from zone-level geometry."""

    generator_name = "zone_anchor_quality_generator_v1"

    def __init__(
        self,
        *,
        hysteresis_score_delta: float = 12.0,
        coverage_loss_margin_deg: float = 2.0,
        gateway_hop_penalty: float = 3.0,
        min_anchor_dwell_seconds: float = 0.0,
    ) -> None:
        self.hysteresis_score_delta = float(hysteresis_score_delta)
        self.coverage_loss_margin_deg = float(coverage_loss_margin_deg)
        self.gateway_hop_penalty = float(gateway_hop_penalty)
        self.min_anchor_dwell_seconds = max(0.0, float(min_anchor_dwell_seconds))
        self._previous_anchor_by_zone: Dict[str, str] = {}
        self._previous_anchor_change_time_by_zone: Dict[str, float] = {}

    def _preferred_gateway_ids(
        self,
        *,
        orbit_slice: OrbitTopologySlice,
        zone_id: str,
    ) -> List[str]:
        policies = dict(orbit_slice.planner_hints.get("zone_default_gateway_preferences", {}))
        policy = dict(policies.get(zone_id, {}))
        gateway_ids = [
            str(gateway_id)
            for gateway_id in (
                policy.get("preferred_gateway_ids")
                or policy.get("regional_preferred_gateway_ids")
                or policy.get("gateway_ids")
                or []
            )
            if str(gateway_id).strip()
        ]
        return gateway_ids

    def _gateway_path_hops(
        self,
        *,
        satellite_id: str,
        preferred_gateway_ids: Sequence[str],
        gateway_satellite_ids: Dict[str, str],
        adjacency: Dict[str, set[str]],
    ) -> int | None:
        selected_gateway_ids = [
            str(gateway_id)
            for gateway_id in preferred_gateway_ids
            if str(gateway_id) in gateway_satellite_ids
        ]
        if not selected_gateway_ids:
            selected_gateway_ids = sorted(gateway_satellite_ids)
        hop_counts: List[int] = []
        for gateway_id in selected_gateway_ids:
            gateway_satellite_id = gateway_satellite_ids.get(gateway_id)
            if not gateway_satellite_id:
                continue
            hops = _shortest_hop_count(
                adjacency=adjacency,
                start_node_id=satellite_id,
                goal_node_id=str(gateway_satellite_id),
            )
            if hops is not None:
                hop_counts.append(int(hops))
        if not hop_counts:
            return None
        return min(hop_counts)

    def generate(
        self,
        orbit_slice: OrbitTopologySlice,
        visibility_slice: VisibilitySlice,
    ) -> ZoneProjection:
        positions = {
            node_id: ExperimentNodePosition(
                x_km=float(node.get("position_state", {}).get("x_km", 0.0)),
                y_km=float(node.get("position_state", {}).get("y_km", 0.0)),
                z_km=float(node.get("position_state", {}).get("z_km", 0.0)),
            )
            for node_id, node in orbit_slice.nodes.items()
            if node.get("node_type") == "satellite"
        }
        adjacency = _satellite_adjacency_from_active_links(visibility_slice.active_links)
        gateway_satellite_ids = _active_gateway_satellite_ids(visibility_slice)
        zones: Dict[str, Dict[str, Any]] = {}
        current_relative_time = float(
            orbit_slice.time_slice.get("relative_time_seconds", 0.0)
        )
        for zone_id, zone in orbit_slice.zone_generation_input.zones.items():
            zone_id = str(zone_id)
            service_role = str(zone.get("service_role", "required_service"))
            active_service = bool(
                zone.get(
                    "active_service",
                    service_role in {"required_service", "optional_service"},
                )
            )
            required_for_runtime = bool(
                zone.get("required_for_runtime", service_role == "required_service")
            )
            if service_role == "disabled":
                zones[zone_id] = {
                    **dict(zone),
                    "active_serving_satellite_id": None,
                    "resolution_state": "disabled",
                    "anchor_selection": {
                        "algorithm": self.generator_name,
                        "geometry_model": "zone_center",
                        "change_reason": "zone_disabled",
                    },
                    "service_planning": {
                        "service_role": service_role,
                        "active_service": active_service,
                        "required_for_runtime": required_for_runtime,
                        "install_gz_fec": False,
                    },
                }
                continue
            zone_center = _zone_position(dict(zone))
            min_elevation_deg = float(zone.get("min_elevation_deg", 0.0))
            required_coverage_margin_deg = float(
                zone.get("coverage_margin_deg", self.coverage_loss_margin_deg)
            )
            previous_anchor = self._previous_anchor_by_zone.get(zone_id)
            if zone_center is None:
                zones[zone_id] = {
                    **dict(zone),
                    "active_serving_satellite_id": None,
                    "resolution_state": "unresolved_missing_zone_geometry",
                    "anchor_selection": {
                        "algorithm": self.generator_name,
                        "geometry_model": "zone_center",
                        "change_reason": "missing_zone_position_state",
                    },
                    "service_planning": {
                        "service_role": service_role,
                        "active_service": active_service,
                        "required_for_runtime": required_for_runtime,
                        "install_gz_fec": (
                            active_service
                            and service_role in {"required_service", "optional_service"}
                        ),
                    },
                }
                continue
            preferred_gateways = self._preferred_gateway_ids(
                orbit_slice=orbit_slice,
                zone_id=zone_id,
            )
            visible_candidate_sets = _zone_visible_candidate_sets(
                zone=dict(zone),
                visibility_slice=visibility_slice,
            )
            zone_ut_ids = [str(ut_id) for ut_id in zone.get("ut_ids", [])]
            service_has_ut_probes = (
                active_service
                and service_role in {"required_service", "optional_service"}
                and bool(zone_ut_ids)
            )
            ut_attach_resolved_count = sum(
                1
                for ut_id in zone_ut_ids
                if bool(visible_candidate_sets.get(ut_id, []))
            )
            ut_ids_without_visible_candidate = [
                ut_id
                for ut_id in zone_ut_ids
                if not bool(visible_candidate_sets.get(ut_id, []))
            ]
            ut_attach_resolved = (
                not service_has_ut_probes
                or ut_attach_resolved_count == len(zone_ut_ids)
            )
            candidate_scores: List[Dict[str, Any]] = []
            rejected_candidates: List[Dict[str, Any]] = []
            for satellite_id in sorted(
                str(candidate_id)
                for candidate_id in zone.get("candidate_serving_satellite_ids", [])
            ):
                candidate_position = positions.get(satellite_id)
                if candidate_position is None:
                    rejected_candidates.append(
                        {
                            "satellite_id": satellite_id,
                            "reason": "missing_position",
                        }
                    )
                    continue
                elevation_deg = _elevation_deg(zone_center, candidate_position)
                coverage_margin_deg = elevation_deg - min_elevation_deg
                if coverage_margin_deg < required_coverage_margin_deg:
                    rejected_candidates.append(
                        {
                            "satellite_id": satellite_id,
                            "reason": (
                                "below_zone_coverage_margin"
                                if coverage_margin_deg >= 0.0
                                else "below_min_elevation"
                            ),
                            "zone_center_elevation_deg": round(elevation_deg, 3),
                            "coverage_margin_deg": round(coverage_margin_deg, 3),
                            "required_coverage_margin_deg": (
                                required_coverage_margin_deg
                            ),
                        }
                    )
                    continue
                gateway_hops = self._gateway_path_hops(
                    satellite_id=satellite_id,
                    preferred_gateway_ids=preferred_gateways,
                    gateway_satellite_ids=gateway_satellite_ids,
                    adjacency=adjacency,
                )
                gateway_penalty = (
                    float(gateway_hops) * self.gateway_hop_penalty
                    if gateway_hops is not None
                    else 1000.0
                )
                score = (coverage_margin_deg * 10.0) + elevation_deg - gateway_penalty
                candidate_scores.append(
                    {
                        "satellite_id": satellite_id,
                        "score": round(score, 6),
                        "zone_center_elevation_deg": round(elevation_deg, 3),
                        "coverage_margin_deg": round(coverage_margin_deg, 3),
                        "gateway_path_hops": gateway_hops,
                        "preferred_gateway_ids": list(preferred_gateways),
                    }
                )
            if not candidate_scores:
                zone_anchor_resolved = False
                zone_service_resolved = False
                zones[zone_id] = {
                    **dict(zone),
                    "active_serving_satellite_id": None,
                    "resolution_state": "unresolved_no_zone_visible_satellite",
                    "zone_anchor_resolved": zone_anchor_resolved,
                    "ut_attach_resolved": ut_attach_resolved,
                    "zone_service_resolved": zone_service_resolved,
                    "zone_service_resolution_state": (
                        "unresolved_zone_anchor"
                        if not zone_anchor_resolved
                        else "unresolved_ut_attach"
                    ),
                    "anchor_selection": {
                        "algorithm": self.generator_name,
                        "geometry_model": "zone_center",
                        "min_elevation_deg": min_elevation_deg,
                        "required_coverage_margin_deg": required_coverage_margin_deg,
                        "previous_anchor": previous_anchor,
                        "change_reason": "no_candidate_covers_zone_geometry",
                        "ut_attach_required": False,
                        "ut_attach_policy": "independent_workload_probe_visibility",
                        "required_ut_count": len(zone_ut_ids),
                        "rejected_candidates": rejected_candidates[:12],
                        "rejected_candidate_count": len(rejected_candidates),
                        "zone_resolution_action": "mark_unresolved_no_ut_specific_split",
                    },
                    "service_planning": {
                        "service_role": service_role,
                        "active_service": active_service,
                        "required_for_runtime": required_for_runtime,
                        "install_gz_fec": (
                            active_service
                            and service_role in {"required_service", "optional_service"}
                        ),
                    },
                    "ut_visibility_audit": {
                        "audit_role": "independent_workload_probe_visibility",
                        "ut_attach_resolved": ut_attach_resolved,
                        "ut_count": len(zone_ut_ids),
                        "ut_with_visible_candidate_count": ut_attach_resolved_count,
                        "ut_ids_without_visible_candidate": (
                            ut_ids_without_visible_candidate
                        ),
                    },
                }
                continue
            candidate_scores.sort(
                key=lambda entry: (
                    -float(entry["score"]),
                    -float(entry["coverage_margin_deg"]),
                    int(entry["gateway_path_hops"])
                    if entry.get("gateway_path_hops") is not None
                    else 9999,
                    str(entry["satellite_id"]),
                )
            )
            selected = dict(candidate_scores[0])
            change_reason = "best_zone_anchor_score"
            previous_score = next(
                (
                    dict(candidate)
                    for candidate in candidate_scores
                    if str(candidate["satellite_id"]) == str(previous_anchor)
                ),
                None,
            )
            if previous_score is not None and str(selected["satellite_id"]) != str(previous_anchor):
                score_delta = float(selected["score"]) - float(previous_score["score"])
                previous_margin = float(previous_score["coverage_margin_deg"])
                previous_change_time = self._previous_anchor_change_time_by_zone.get(
                    zone_id,
                    current_relative_time,
                )
                dwell_elapsed = max(0.0, current_relative_time - previous_change_time)
                if (
                    previous_margin > required_coverage_margin_deg
                    and self.min_anchor_dwell_seconds > 0.0
                    and dwell_elapsed < self.min_anchor_dwell_seconds
                ):
                    selected = previous_score
                    change_reason = "min_dwell_keep_current_anchor"
                elif (
                    previous_margin > required_coverage_margin_deg
                    and score_delta < self.hysteresis_score_delta
                ):
                    selected = previous_score
                    change_reason = "hysteresis_keep_current_anchor"
                elif previous_margin <= required_coverage_margin_deg:
                    change_reason = "previous_anchor_near_coverage_loss"
                else:
                    change_reason = "challenger_exceeded_hysteresis"
            elif previous_score is not None:
                change_reason = "stable_existing_anchor"
            selected_satellite_id = str(selected["satellite_id"])
            if selected_satellite_id != str(previous_anchor):
                self._previous_anchor_change_time_by_zone[zone_id] = current_relative_time
            else:
                self._previous_anchor_change_time_by_zone.setdefault(
                    zone_id,
                    current_relative_time,
                )
            self._previous_anchor_by_zone[zone_id] = selected_satellite_id
            visible_candidate_sets = _zone_visible_candidate_sets(
                zone=dict(zone),
                visibility_slice=visibility_slice,
            )
            selected_anchor_visible_to_ut_ids = [
                ut_id
                for ut_id, candidates in sorted(visible_candidate_sets.items())
                if any(
                    str(candidate.get("satellite_id")) == selected_satellite_id
                    for candidate in candidates
                )
            ]
            selected_anchor_visible_to_all_ut_probes = (
                not service_has_ut_probes
                or len(selected_anchor_visible_to_ut_ids) == len(zone_ut_ids)
            )
            ut_attach_resolved = selected_anchor_visible_to_all_ut_probes
            zone_anchor_resolved = True
            zone_service_resolved = zone_anchor_resolved and ut_attach_resolved
            zones[zone_id] = {
                **dict(zone),
                "active_serving_satellite_id": selected_satellite_id,
                "resolution_state": "resolved",
                "zone_anchor_resolved": zone_anchor_resolved,
                "ut_attach_resolved": ut_attach_resolved,
                "zone_service_resolved": zone_service_resolved,
                "zone_service_resolution_state": (
                    "resolved"
                    if zone_service_resolved
                    else "unresolved_ut_attach"
                ),
                "anchor_selection": {
                    "algorithm": self.generator_name,
                    "geometry_model": "zone_center",
                    "min_elevation_deg": min_elevation_deg,
                    "selected_satellite_id": selected_satellite_id,
                    "quality_score": selected["score"],
                    "coverage_margin_deg": selected["coverage_margin_deg"],
                    "gateway_path_hops": selected.get("gateway_path_hops"),
                    "previous_anchor": previous_anchor,
                    "change_reason": change_reason,
                    "hysteresis_score_delta": self.hysteresis_score_delta,
                    "coverage_loss_margin_deg": required_coverage_margin_deg,
                    "min_anchor_dwell_seconds": self.min_anchor_dwell_seconds,
                    "ut_attach_required": False,
                    "ut_attach_policy": (
                        "workload_probe_must_attach_to_zone_anchor_for_validation"
                    ),
                    "required_ut_count": len(zone_ut_ids),
                    "candidate_count": len(candidate_scores),
                    "rejected_candidate_count": len(rejected_candidates),
                    "rejected_candidates": rejected_candidates[:12],
                    "top_candidates": candidate_scores[:8],
                    "zone_resolution_action": (
                        "stable_single_anchor"
                        if selected_anchor_visible_to_all_ut_probes
                        else "mark_unresolved_no_ut_specific_split"
                    ),
                    "ut_sample_would_force_divergence": (
                        not selected_anchor_visible_to_all_ut_probes
                    ),
                },
                "service_planning": {
                    "service_role": service_role,
                    "active_service": active_service,
                    "required_for_runtime": required_for_runtime,
                    "install_gz_fec": (
                        active_service
                        and service_role in {"required_service", "optional_service"}
                    ),
                },
                "ut_visibility_audit": {
                    "audit_role": "independent_workload_probe_visibility",
                    "ut_attach_resolved": ut_attach_resolved,
                    "ut_count": len(list(zone.get("ut_ids", []))),
                    "ut_with_visible_candidate_count": ut_attach_resolved_count,
                    "ut_ids_without_visible_candidate": (
                        ut_ids_without_visible_candidate
                    ),
                    "selected_anchor_visible_to_ut_count": sum(
                        1
                        for candidates in visible_candidate_sets.values()
                        if any(
                            str(candidate.get("satellite_id")) == selected_satellite_id
                            for candidate in candidates
                        )
                    ),
                    "selected_anchor_visible_to_ut_ids": selected_anchor_visible_to_ut_ids,
                    "ut_ids_without_selected_anchor_visibility": [
                        ut_id
                        for ut_id, candidates in sorted(visible_candidate_sets.items())
                        if not any(
                            str(candidate.get("satellite_id")) == selected_satellite_id
                            for candidate in candidates
                        )
                    ],
                },
            }
        return ZoneProjection(
            zones=zones,
            ut_zone_membership=dict(orbit_slice.zone_generation_input.ut_zone_membership),
            zone_generator=self.generator_name,
        )


class SyntheticPositionExperimentOrbitProducer(OrbitTopologyProducer):
    """Produce one synthetic but position-driven experiment slice."""

    producer_name = "synthetic_position_experiment_orbit_producer"

    def __init__(
        self,
        *,
        topology_path: Path,
        profile: ExperimentProfile = DEFAULT_EXPERIMENT_PROFILE,
    ) -> None:
        self.topology = _load_json(topology_path)
        self.profile = profile

    def _experiment_nodes(self) -> Dict[str, Dict[str, Any]]:
        profile = self.profile
        satellites = _topology_entry_by_id(self.topology.get("satellites", []), "satellite_id")
        gateways = _topology_entry_by_id(self.topology.get("gateways", []), "gateway_id")
        uts = _topology_entry_by_id(self.topology.get("user_terminals", []), "ut_id")
        nodes: Dict[str, Dict[str, Any]] = {}

        for satellite_id in profile.candidate_satellite_ids:
            satellite = satellites[satellite_id]
            position = SYNTHETIC_POSITION_MAP[satellite_id]
            nodes[satellite_id] = {
                "node_id": satellite_id,
                "node_type": "satellite",
                "orbit_id": satellite.get("orbit_id"),
                "plane_index": satellite.get("plane_index"),
                "satellite_index_in_plane": satellite.get("satellite_index_in_plane"),
                "position_state": {
                    "model": "synthetic_cartesian_km",
                    "x_km": position.x_km,
                    "y_km": position.y_km,
                    "z_km": position.z_km,
                },
            }

        gateway = gateways[profile.gateway_id]
        gateway_position = SYNTHETIC_POSITION_MAP[profile.gateway_id]
        nodes[profile.gateway_id] = {
            "node_id": profile.gateway_id,
            "node_type": "gateway",
            "gateway_region": gateway.get("gateway_region"),
            "internet_uplink": dict(gateway.get("internet_uplink", {})),
            "position_state": {
                "model": "synthetic_cartesian_km",
                "x_km": gateway_position.x_km,
                "y_km": gateway_position.y_km,
                "z_km": gateway_position.z_km,
            },
        }

        ut = uts[profile.ut_id]
        ut_position = SYNTHETIC_POSITION_MAP[profile.ut_id]
        nodes[profile.ut_id] = {
            "node_id": profile.ut_id,
            "node_type": "user_terminal",
            "region": ut.get("region"),
            "identity_interface": dict(ut.get("identity_interface", {})),
            "candidate_serving_satellite_ids": list(profile.candidate_satellite_ids),
            "fixed_coordinates": {
                "coordinate_model": "synthetic_cartesian_km",
                "x_km": ut_position.x_km,
                "y_km": ut_position.y_km,
                "z_km": ut_position.z_km,
            },
            "position_state": {
                "model": "synthetic_cartesian_km",
                "x_km": ut_position.x_km,
                "y_km": ut_position.y_km,
                "z_km": ut_position.z_km,
            },
        }
        return nodes

    def iter_orbit_slices(self) -> Sequence[OrbitTopologySlice]:
        profile = self.profile
        zones = {
            profile.zone_id: {
                "zone_id": profile.zone_id,
                "membership_mode": "single_ut_experiment_zone",
                "ut_ids": [profile.ut_id],
                "candidate_serving_satellite_ids": list(profile.candidate_satellite_ids),
            }
        }
        orbit_slice = OrbitTopologySlice(
            time_slice={
                "slice_id": "experiment-slice-synthetic-0001",
                "state_model": "full_state",
                "time_semantics": "synthetic_position_experiment",
                "relative_time_seconds": 0,
                "duration_seconds": 60,
            },
            nodes=self._experiment_nodes(),
            candidate_links=_slot_candidates(profile=profile, topology=self.topology),
            zone_generation_input=ZoneGenerationInput(
                membership_mode="single_ut_experiment_zone",
                zones=zones,
                ut_zone_membership={profile.ut_id: profile.zone_id},
            ),
            planner_hints={
                **_planner_hints(profile),
                "orbit_input_kind": "synthetic_position_driven",
            },
            provenance={
                "orbit_producer": self.producer_name,
                "slice_profile": profile.profile_name,
                "position_model": "synthetic_cartesian_km",
            },
        )
        return (orbit_slice,)


class SemiRealOrbitPositionExperimentProducer(OrbitTopologyProducer):
    """Produce semi-real orbital-element slices for the first non-legacy experiment."""

    producer_name = "semi_real_orbital_elements_experiment_producer"

    def __init__(
        self,
        *,
        topology_path: Path,
        profile: ExperimentProfile = DEFAULT_EXPERIMENT_PROFILE,
        time_slices: Sequence[ExperimentTimeSlice] = DEFAULT_SEMI_REAL_TIME_SLICES,
        orbital_reference_time_seconds: float = 720.0,
    ) -> None:
        self.topology = _load_json(topology_path)
        self.profile = profile
        self.time_slices = tuple(time_slices)
        self.orbital_reference_time_seconds = orbital_reference_time_seconds

    def _experiment_nodes(
        self,
        *,
        absolute_orbit_time_seconds: float,
    ) -> Dict[str, Dict[str, Any]]:
        profile = self.profile
        satellites = _topology_entry_by_id(self.topology.get("satellites", []), "satellite_id")
        gateways = _topology_entry_by_id(self.topology.get("gateways", []), "gateway_id")
        uts = _topology_entry_by_id(self.topology.get("user_terminals", []), "ut_id")
        nodes: Dict[str, Dict[str, Any]] = {}

        for satellite_id in profile.candidate_satellite_ids:
            satellite = satellites[satellite_id]
            orbital_elements = SEMI_REAL_ORBITAL_ELEMENTS[satellite_id]
            position = _orbital_position(
                orbital_elements,
                relative_time_seconds=absolute_orbit_time_seconds,
            )
            nodes[satellite_id] = {
                "node_id": satellite_id,
                "node_type": "satellite",
                "orbit_id": satellite.get("orbit_id"),
                "plane_index": satellite.get("plane_index"),
                "satellite_index_in_plane": satellite.get("satellite_index_in_plane"),
                "position_state": {
                    "model": "semi_real_orbital_elements_eci_km",
                    "x_km": round(position.x_km, 6),
                    "y_km": round(position.y_km, 6),
                    "z_km": round(position.z_km, 6),
                },
            }

        gateway = gateways[profile.gateway_id]
        gateway_position = _ground_position(SEMI_REAL_GROUND_LOCATIONS[profile.gateway_id])
        nodes[profile.gateway_id] = {
            "node_id": profile.gateway_id,
            "node_type": "gateway",
            "gateway_region": gateway.get("gateway_region"),
            "internet_uplink": dict(gateway.get("internet_uplink", {})),
            "fixed_coordinates": {
                "coordinate_model": "earth_surface_lat_lon_alt",
                "latitude_deg": SEMI_REAL_GROUND_LOCATIONS[profile.gateway_id].latitude_deg,
                "longitude_deg": SEMI_REAL_GROUND_LOCATIONS[profile.gateway_id].longitude_deg,
                "altitude_km": SEMI_REAL_GROUND_LOCATIONS[profile.gateway_id].altitude_km,
            },
            "position_state": {
                "model": "earth_centered_fixed_km",
                "x_km": round(gateway_position.x_km, 6),
                "y_km": round(gateway_position.y_km, 6),
                "z_km": round(gateway_position.z_km, 6),
            },
        }

        ut = uts[profile.ut_id]
        ut_position = _ground_position(SEMI_REAL_GROUND_LOCATIONS[profile.ut_id])
        nodes[profile.ut_id] = {
            "node_id": profile.ut_id,
            "node_type": "user_terminal",
            "region": ut.get("region"),
            "identity_interface": dict(ut.get("identity_interface", {})),
            "candidate_serving_satellite_ids": list(profile.candidate_satellite_ids),
            "fixed_coordinates": {
                "coordinate_model": "earth_surface_lat_lon_alt",
                "latitude_deg": SEMI_REAL_GROUND_LOCATIONS[profile.ut_id].latitude_deg,
                "longitude_deg": SEMI_REAL_GROUND_LOCATIONS[profile.ut_id].longitude_deg,
                "altitude_km": SEMI_REAL_GROUND_LOCATIONS[profile.ut_id].altitude_km,
            },
            "position_state": {
                "model": "earth_centered_fixed_km",
                "x_km": round(ut_position.x_km, 6),
                "y_km": round(ut_position.y_km, 6),
                "z_km": round(ut_position.z_km, 6),
            },
        }
        return nodes

    def iter_orbit_slices(self) -> Sequence[OrbitTopologySlice]:
        profile = self.profile
        zones = {
            profile.zone_id: {
                "zone_id": profile.zone_id,
                "membership_mode": "single_ut_experiment_zone",
                "ut_ids": [profile.ut_id],
                "candidate_serving_satellite_ids": list(profile.candidate_satellite_ids),
            }
        }
        slices: List[OrbitTopologySlice] = []
        for time_slice in self.time_slices:
            absolute_orbit_time_seconds = (
                self.orbital_reference_time_seconds + time_slice.relative_time_seconds
            )
            slices.append(
                OrbitTopologySlice(
                    time_slice={
                        "slice_id": time_slice.slice_id,
                        "state_model": "full_state",
                        "time_semantics": "semi_real_orbital_elements",
                        "relative_time_seconds": time_slice.relative_time_seconds,
                        "duration_seconds": time_slice.duration_seconds,
                    },
                    nodes=self._experiment_nodes(
                        absolute_orbit_time_seconds=absolute_orbit_time_seconds
                    ),
                    candidate_links=_slot_candidates(profile=profile, topology=self.topology),
                    zone_generation_input=ZoneGenerationInput(
                        membership_mode="single_ut_experiment_zone",
                        zones=zones,
                        ut_zone_membership={profile.ut_id: profile.zone_id},
                    ),
                    planner_hints={
                        **_planner_hints(profile),
                        "orbit_input_kind": "semi_real_orbital_elements",
                    },
                    provenance={
                        "orbit_producer": self.producer_name,
                        "slice_profile": profile.profile_name,
                        "position_model": "semi_real_orbital_elements_eci_km",
                        "orbital_reference_time_seconds": self.orbital_reference_time_seconds,
                    },
                )
            )
        return tuple(slices)


class ScenarioSemiRealOrbitPositionExperimentProducer(OrbitTopologyProducer):
    """Produce a small multi-entity semi-real experiment from orbital elements."""

    producer_name = "scenario_semi_real_orbital_elements_experiment_producer"

    def __init__(
        self,
        *,
        topology_path: Path,
        scenario: ExperimentScenario,
    ) -> None:
        self.topology = _load_json(topology_path)
        self.scenario = scenario

    def _experiment_nodes(
        self,
        *,
        absolute_orbit_time_seconds: float,
    ) -> Dict[str, Dict[str, Any]]:
        satellites = _topology_entry_by_id(self.topology.get("satellites", []), "satellite_id")
        gateways = _topology_entry_by_id(self.topology.get("gateways", []), "gateway_id")
        uts = _topology_entry_by_id(self.topology.get("user_terminals", []), "ut_id")
        scenario = self.scenario
        nodes: Dict[str, Dict[str, Any]] = {}

        for satellite_id in scenario.satellite_ids:
            satellite = satellites[str(satellite_id)]
            orbital_elements = scenario.orbital_elements[str(satellite_id)]
            position = _orbital_position(
                orbital_elements,
                relative_time_seconds=absolute_orbit_time_seconds,
            )
            nodes[str(satellite_id)] = {
                "node_id": str(satellite_id),
                "node_type": "satellite",
                "orbit_id": satellite.get("orbit_id"),
                "plane_index": satellite.get("plane_index"),
                "satellite_index_in_plane": satellite.get("satellite_index_in_plane"),
                "position_state": {
                    "model": "semi_real_orbital_elements_eci_km",
                    "x_km": round(position.x_km, 6),
                    "y_km": round(position.y_km, 6),
                    "z_km": round(position.z_km, 6),
                },
            }

        for gateway_profile in scenario.gateway_profiles:
            gateway = gateways[gateway_profile.gateway_id]
            gateway_location = scenario.ground_locations[gateway_profile.gateway_id]
            gateway_position = _ground_position(gateway_location)
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
                    "model": "earth_centered_fixed_km",
                    "x_km": round(gateway_position.x_km, 6),
                    "y_km": round(gateway_position.y_km, 6),
                    "z_km": round(gateway_position.z_km, 6),
                },
            }

        for terminal_profile in scenario.terminal_profiles:
            ut = uts[terminal_profile.ut_id]
            ut_location = scenario.ground_locations[terminal_profile.ut_id]
            ut_position = _ground_position(ut_location)
            nodes[terminal_profile.ut_id] = {
                "node_id": terminal_profile.ut_id,
                "node_type": "user_terminal",
                "region": ut.get("region"),
                "identity_interface": dict(ut.get("identity_interface", {})),
                "candidate_serving_satellite_ids": list(
                    terminal_profile.candidate_satellite_ids
                ),
                "fixed_coordinates": {
                    "coordinate_model": "earth_surface_lat_lon_alt",
                    "latitude_deg": ut_location.latitude_deg,
                    "longitude_deg": ut_location.longitude_deg,
                    "altitude_km": ut_location.altitude_km,
                },
                "position_state": {
                    "model": "earth_centered_fixed_km",
                    "x_km": round(ut_position.x_km, 6),
                    "y_km": round(ut_position.y_km, 6),
                    "z_km": round(ut_position.z_km, 6),
                },
            }
        return nodes

    def iter_orbit_slices(self) -> Sequence[OrbitTopologySlice]:
        scenario = self.scenario
        explicit_zone_profiles = _zone_profile_by_id(scenario)
        if not explicit_zone_profiles:
            explicit_zone_profiles = _derived_zone_profiles_from_terminals(scenario)
        ut_zone_membership = {
            terminal.ut_id: terminal.zone_id for terminal in scenario.terminal_profiles
        }
        slices: List[OrbitTopologySlice] = []
        for time_slice in scenario.time_slices:
            absolute_orbit_time_seconds = (
                scenario.orbital_reference_time_seconds
                + time_slice.relative_time_seconds
            )
            zones: Dict[str, Dict[str, Any]] = {}
            for zone_id, profile in sorted(explicit_zone_profiles.items()):
                zone_location = GroundLocation(
                    latitude_deg=float(profile.latitude_deg),
                    longitude_deg=float(profile.longitude_deg),
                    altitude_km=float(profile.altitude_km),
                )
                zone_position = _ground_position(zone_location)
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
                        "coverage_margin_deg": profile.coverage_margin_deg,
                    },
                    "ut_ids": [],
                    "candidate_serving_satellite_ids": [
                        str(candidate_satellite_id)
                        for candidate_satellite_id in candidate_satellite_ids
                    ],
                    "coordinate_gateway_ids": list(profile.coordinate_gateway_ids),
                    "fallback_to_shortest_path": bool(profile.fallback_to_shortest_path),
                    "max_additional_satellite_hops_relative_to_best_candidate": (
                        profile.max_additional_satellite_hops_relative_to_best_candidate
                    ),
                    "fixed_coordinates": {
                        "coordinate_model": "earth_surface_lat_lon_alt",
                        "latitude_deg": float(profile.latitude_deg),
                        "longitude_deg": float(profile.longitude_deg),
                        "altitude_km": float(profile.altitude_km),
                    },
                    "position_state": {
                        "model": "earth_centered_fixed_km",
                        "x_km": round(zone_position.x_km, 6),
                        "y_km": round(zone_position.y_km, 6),
                        "z_km": round(zone_position.z_km, 6),
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
                        "time_semantics": "semi_real_orbital_elements",
                        "relative_time_seconds": time_slice.relative_time_seconds,
                        "duration_seconds": time_slice.duration_seconds,
                    },
                    nodes=self._experiment_nodes(
                        absolute_orbit_time_seconds=absolute_orbit_time_seconds
                    ),
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
                        "orbit_input_kind": "semi_real_orbital_elements",
                    },
                    provenance={
                        "orbit_producer": self.producer_name,
                        "slice_profile": scenario.scenario_name,
                        "position_model": "semi_real_orbital_elements_eci_km",
                        "orbital_reference_time_seconds": (
                            scenario.orbital_reference_time_seconds
                        ),
                    },
                )
            )
        return tuple(slices)


class SyntheticDistanceVisibilityModel(VisibilityModel):
    """Choose the nearest visible satellite for each access relation."""

    model_name = "synthetic_distance_visibility_model"

    def __init__(
        self,
        *,
        max_access_distance_km: float = 650.0,
    ) -> None:
        self.max_access_distance_km = max_access_distance_km

    def evaluate(self, orbit_slice: OrbitTopologySlice) -> VisibilitySlice:
        active_links: List[Dict[str, Any]] = []
        candidate_visibility: Dict[str, List[Dict[str, Any]]] = {}
        positions = {
            node_id: ExperimentNodePosition(
                x_km=float(node.get("position_state", {}).get("x_km", 0.0)),
                y_km=float(node.get("position_state", {}).get("y_km", 0.0)),
                z_km=float(node.get("position_state", {}).get("z_km", 0.0)),
            )
            for node_id, node in orbit_slice.nodes.items()
        }
        for link in orbit_slice.candidate_links:
            slot_type = str(link.get("slot_type"))
            if slot_type == "cross_plane_isl":
                visible_candidates = []
                for candidate in link.get("candidate_bindings", []):
                    endpoints = [dict(endpoint) for endpoint in candidate.get("endpoints", [])]
                    if len(endpoints) != 2:
                        continue
                    first_node_id = str(endpoints[0].get("node_id"))
                    second_node_id = str(endpoints[1].get("node_id"))
                    first_position = positions[first_node_id]
                    second_position = positions[second_node_id]
                    distance_km = _distance_km(first_position, second_position)
                    if distance_km > DYNAMIC_CROSS_PLANE_MAX_DISTANCE_KM:
                        continue
                    visible_candidates.append((distance_km, candidate))
                candidate_visibility[str(link.get("link_id"))] = [
                    {
                        "satellite_pair_id": str(candidate.get("satellite_pair_id")),
                        "binding": dict(candidate.get("binding", {})),
                        "endpoints": [
                            dict(endpoint) for endpoint in candidate.get("endpoints", [])
                        ],
                        "slot_type": slot_type,
                        "cross_plane_distance_km": round(distance_km, 3),
                    }
                    for distance_km, candidate in sorted(
                        visible_candidates,
                        key=lambda item: (item[0], str(item[1].get("satellite_pair_id"))),
                    )
                ]
                if not visible_candidates:
                    continue
                distance_km, candidate = min(
                    visible_candidates,
                    key=lambda item: (item[0], str(item[1].get("satellite_pair_id"))),
                )
                active_links.append(
                    {
                        "link_id": link.get("link_id"),
                        "slot_type": slot_type,
                        "subnet": link.get("subnet"),
                        "network_name": link.get("network_name"),
                        "docker_network_name": link.get("docker_network_name"),
                        "bridge_gateway_ip": link.get("bridge_gateway_ip"),
                        "occupied": True,
                        "binding": dict(candidate.get("binding", {})),
                        "endpoints": [
                            dict(endpoint) for endpoint in candidate.get("endpoints", [])
                        ],
                        "visibility_relation": link.get("visibility_relation"),
                        "cross_plane_distance_km": round(distance_km, 3),
                        "activation_model": link.get("activation_model"),
                    }
                )
                continue
            if slot_type not in {"ut_access", "gw_access"}:
                active_links.append(
                    {
                        key: value
                        for key, value in link.items()
                        if key not in {"visibility_owner_node_id", "candidate_bindings"}
                    }
                )
                continue
            owner_node_id = str(link.get("visibility_owner_node_id"))
            owner_position = positions[owner_node_id]
            visible_candidates = []
            for candidate in link.get("candidate_bindings", []):
                satellite_id = str(candidate.get("satellite_id"))
                candidate_position = positions[satellite_id]
                distance_km = _distance_km(owner_position, candidate_position)
                if distance_km > self.max_access_distance_km:
                    continue
                visible_candidates.append((distance_km, satellite_id, candidate))
            candidate_visibility[owner_node_id] = [
                _candidate_visibility_record(
                    link=link,
                    candidate=candidate,
                    metric_name="visibility_distance_km",
                    metric_value=distance_km,
                )
                for distance_km, _, candidate in sorted(
                    visible_candidates,
                    key=lambda item: (item[0], item[1]),
                )
            ]
            if not visible_candidates:
                continue
            _, satellite_id, candidate = min(
                visible_candidates,
                key=lambda item: (item[0], item[1]),
            )
            active_links.append(
                {
                    "link_id": link.get("link_id"),
                    "slot_type": link.get("slot_type"),
                    "subnet": link.get("subnet"),
                    "network_name": link.get("network_name"),
                    "docker_network_name": link.get("docker_network_name"),
                    "occupied": True,
                    "binding": dict(candidate.get("binding", {})),
                    "endpoints": [dict(endpoint) for endpoint in candidate.get("endpoints", [])],
                    "visibility_relation": link.get("visibility_relation"),
                    "visibility_distance_km": round(
                        _distance_km(owner_position, positions[satellite_id]),
                        3,
                    ),
                }
            )
        return VisibilitySlice(
            active_links=sorted(active_links, key=lambda entry: str(entry.get("link_id"))),
            visibility_model=self.model_name,
            candidate_visibility=candidate_visibility,
        )


class SemiRealLineOfSightVisibilityModel(VisibilityModel):
    """Choose the highest-elevation visible satellite for each access relation."""

    model_name = "semi_real_line_of_sight_visibility_model"

    def __init__(
        self,
        *,
        min_elevation_deg: float = 10.0,
    ) -> None:
        self.min_elevation_deg = min_elevation_deg

    def evaluate(self, orbit_slice: OrbitTopologySlice) -> VisibilitySlice:
        active_links: List[Dict[str, Any]] = []
        candidate_visibility: Dict[str, List[Dict[str, Any]]] = {}
        positions = {
            node_id: ExperimentNodePosition(
                x_km=float(node.get("position_state", {}).get("x_km", 0.0)),
                y_km=float(node.get("position_state", {}).get("y_km", 0.0)),
                z_km=float(node.get("position_state", {}).get("z_km", 0.0)),
            )
            for node_id, node in orbit_slice.nodes.items()
        }
        for link in orbit_slice.candidate_links:
            slot_type = str(link.get("slot_type"))
            if slot_type == "cross_plane_isl":
                visible_candidates = []
                for candidate in link.get("candidate_bindings", []):
                    endpoints = [dict(endpoint) for endpoint in candidate.get("endpoints", [])]
                    if len(endpoints) != 2:
                        continue
                    first_node_id = str(endpoints[0].get("node_id"))
                    second_node_id = str(endpoints[1].get("node_id"))
                    first_position = positions[first_node_id]
                    second_position = positions[second_node_id]
                    distance_km = _distance_km(first_position, second_position)
                    if distance_km > DYNAMIC_CROSS_PLANE_MAX_DISTANCE_KM:
                        continue
                    visible_candidates.append((distance_km, candidate))
                candidate_visibility[str(link.get("link_id"))] = [
                    {
                        "satellite_pair_id": str(candidate.get("satellite_pair_id")),
                        "binding": dict(candidate.get("binding", {})),
                        "endpoints": [
                            dict(endpoint) for endpoint in candidate.get("endpoints", [])
                        ],
                        "slot_type": slot_type,
                        "cross_plane_distance_km": round(distance_km, 3),
                    }
                    for distance_km, candidate in sorted(
                        visible_candidates,
                        key=lambda item: (item[0], str(item[1].get("satellite_pair_id"))),
                    )
                ]
                if not visible_candidates:
                    continue
                distance_km, candidate = min(
                    visible_candidates,
                    key=lambda item: (item[0], str(item[1].get("satellite_pair_id"))),
                )
                active_links.append(
                    {
                        "link_id": link.get("link_id"),
                        "slot_type": slot_type,
                        "subnet": link.get("subnet"),
                        "network_name": link.get("network_name"),
                        "docker_network_name": link.get("docker_network_name"),
                        "bridge_gateway_ip": link.get("bridge_gateway_ip"),
                        "occupied": True,
                        "binding": dict(candidate.get("binding", {})),
                        "endpoints": [
                            dict(endpoint) for endpoint in candidate.get("endpoints", [])
                        ],
                        "visibility_relation": link.get("visibility_relation"),
                        "cross_plane_distance_km": round(distance_km, 3),
                        "activation_model": link.get("activation_model"),
                    }
                )
                continue
            if slot_type not in {"ut_access", "gw_access"}:
                active_links.append(
                    {
                        key: value
                        for key, value in link.items()
                        if key not in {"visibility_owner_node_id", "candidate_bindings"}
                    }
                )
                continue
            owner_node_id = str(link.get("visibility_owner_node_id"))
            owner_position = positions[owner_node_id]
            visible_candidates = []
            for candidate in link.get("candidate_bindings", []):
                satellite_id = str(candidate.get("satellite_id"))
                candidate_position = positions[satellite_id]
                elevation_deg = _elevation_deg(owner_position, candidate_position)
                if elevation_deg < self.min_elevation_deg:
                    continue
                visible_candidates.append((elevation_deg, satellite_id, candidate))
            candidate_visibility[owner_node_id] = [
                _candidate_visibility_record(
                    link=link,
                    candidate=candidate,
                    metric_name="visibility_elevation_deg",
                    metric_value=elevation_deg,
                )
                for elevation_deg, _, candidate in sorted(
                    visible_candidates,
                    key=lambda item: (-item[0], item[1]),
                )
            ]
            if not visible_candidates:
                continue
            elevation_deg, _, candidate = max(
                visible_candidates,
                key=lambda item: (item[0], item[1]),
            )
            active_links.append(
                {
                    "link_id": link.get("link_id"),
                    "slot_type": link.get("slot_type"),
                    "subnet": link.get("subnet"),
                    "network_name": link.get("network_name"),
                    "docker_network_name": link.get("docker_network_name"),
                    "occupied": True,
                    "binding": dict(candidate.get("binding", {})),
                    "endpoints": [dict(endpoint) for endpoint in candidate.get("endpoints", [])],
                    "visibility_relation": link.get("visibility_relation"),
                    "visibility_elevation_deg": round(elevation_deg, 3),
                }
            )
        return VisibilitySlice(
            active_links=sorted(active_links, key=lambda entry: str(entry.get("link_id"))),
            visibility_model=self.model_name,
            candidate_visibility=candidate_visibility,
        )


class NearestVisibleZoneGenerator(ZoneGenerator):
    """Choose the nearest candidate satellite as the zone owner."""

    generator_name = "nearest_visible_zone_generator"

    def generate(
        self,
        orbit_slice: OrbitTopologySlice,
        visibility_slice: VisibilitySlice,
    ) -> ZoneProjection:
        positions = {
            node_id: ExperimentNodePosition(
                x_km=float(node.get("position_state", {}).get("x_km", 0.0)),
                y_km=float(node.get("position_state", {}).get("y_km", 0.0)),
                z_km=float(node.get("position_state", {}).get("z_km", 0.0)),
            )
            for node_id, node in orbit_slice.nodes.items()
        }
        zones = {}
        for zone_id, zone in orbit_slice.zone_generation_input.zones.items():
            visible_candidate_sets = _zone_visible_candidate_sets(
                zone=dict(zone),
                visibility_slice=visibility_slice,
            )
            common_candidates = set.intersection(
                *[
                    {
                        str(candidate.get("satellite_id"))
                        for candidate in candidates
                    }
                    for candidates in visible_candidate_sets.values()
                ]
            ) if visible_candidate_sets and all(visible_candidate_sets.values()) else set()
            if not common_candidates:
                zones[zone_id] = {
                    **dict(zone),
                    "active_serving_satellite_id": None,
                    "resolution_state": "unresolved_no_common_visible_satellite",
                    "visible_candidate_sets_by_ut": {
                        ut_id: [
                            str(candidate.get("satellite_id"))
                            for candidate in candidates
                        ]
                        for ut_id, candidates in sorted(visible_candidate_sets.items())
                    },
                }
                continue
            ut_id = str(zone.get("ut_ids", [])[0])
            ut_position = positions[ut_id]
            candidates = [
                (
                    _distance_km(ut_position, positions[satellite_id]),
                    satellite_id,
                )
                for satellite_id in sorted(common_candidates)
                if satellite_id in positions
            ]
            _, serving_satellite_id = min(candidates, key=lambda item: (item[0], item[1]))
            zones[zone_id] = {
                **dict(zone),
                "active_serving_satellite_id": serving_satellite_id,
                "resolution_state": "resolved",
                "visible_candidate_sets_by_ut": {
                    ut_id: [
                        str(candidate.get("satellite_id"))
                        for candidate in candidates
                    ]
                    for ut_id, candidates in sorted(visible_candidate_sets.items())
                },
            }
        return ZoneProjection(
            zones=zones,
            ut_zone_membership=dict(orbit_slice.zone_generation_input.ut_zone_membership),
            zone_generator=self.generator_name,
        )


class HighestElevationZoneGenerator(ZoneGenerator):
    """Choose the highest-elevation visible candidate satellite as the zone owner."""

    generator_name = "highest_elevation_zone_generator"

    def generate(
        self,
        orbit_slice: OrbitTopologySlice,
        visibility_slice: VisibilitySlice,
    ) -> ZoneProjection:
        positions = {
            node_id: ExperimentNodePosition(
                x_km=float(node.get("position_state", {}).get("x_km", 0.0)),
                y_km=float(node.get("position_state", {}).get("y_km", 0.0)),
                z_km=float(node.get("position_state", {}).get("z_km", 0.0)),
            )
            for node_id, node in orbit_slice.nodes.items()
        }
        zones = {}
        for zone_id, zone in orbit_slice.zone_generation_input.zones.items():
            visible_candidate_sets = _zone_visible_candidate_sets(
                zone=dict(zone),
                visibility_slice=visibility_slice,
            )
            common_candidates = set.intersection(
                *[
                    {
                        str(candidate.get("satellite_id"))
                        for candidate in candidates
                    }
                    for candidates in visible_candidate_sets.values()
                ]
            ) if visible_candidate_sets and all(visible_candidate_sets.values()) else set()
            if not common_candidates:
                zones[zone_id] = {
                    **dict(zone),
                    "active_serving_satellite_id": None,
                    "resolution_state": "unresolved_no_common_visible_satellite",
                    "visible_candidate_sets_by_ut": {
                        ut_id: [
                            str(candidate.get("satellite_id"))
                            for candidate in candidates
                        ]
                        for ut_id, candidates in sorted(visible_candidate_sets.items())
                    },
                }
                continue

            candidate_scores = []
            for satellite_text in sorted(common_candidates):
                elevation_values = [
                    float(candidate.get("visibility_elevation_deg", -90.0))
                    for candidates in visible_candidate_sets.values()
                    for candidate in candidates
                    if str(candidate.get("satellite_id")) == satellite_text
                ]
                if not elevation_values:
                    continue
                candidate_scores.append(
                    (
                        min(elevation_values),
                        sum(elevation_values) / len(elevation_values),
                        satellite_text,
                    )
                )
            if not candidate_scores:
                zones[zone_id] = {
                    **dict(zone),
                    "active_serving_satellite_id": None,
                    "resolution_state": "unresolved_no_common_visible_satellite",
                    "visible_candidate_sets_by_ut": {
                        ut_id: [
                            str(candidate.get("satellite_id"))
                            for candidate in candidates
                        ]
                        for ut_id, candidates in sorted(visible_candidate_sets.items())
                    },
                }
                continue
            _, _, serving_satellite_id = max(
                candidate_scores,
                key=lambda item: (item[0], item[1], item[2]),
            )
            zones[zone_id] = {
                **dict(zone),
                "active_serving_satellite_id": serving_satellite_id,
                "resolution_state": "resolved",
                "visible_candidate_sets_by_ut": {
                    ut_id: [
                        str(candidate.get("satellite_id"))
                        for candidate in candidates
                    ]
                    for ut_id, candidates in sorted(visible_candidate_sets.items())
                },
            }
        return ZoneProjection(
            zones=zones,
            ut_zone_membership=dict(orbit_slice.zone_generation_input.ut_zone_membership),
            zone_generator=self.generator_name,
        )


class ExperimentTopologySlicer(TopologySlicer):
    """Inject attachment facts derived from visibility into the experiment snapshot."""

    slicer_name = "experiment_topology_slicer"

    def build_snapshot(
        self,
        orbit_slice: OrbitTopologySlice,
        visibility_slice: VisibilitySlice,
        zone_projection: ZoneProjection,
    ) -> TopologySnapshot:
        nodes = {node_id: dict(node) for node_id, node in orbit_slice.nodes.items()}
        zone_by_ut = {
            ut_id: zone_projection.zones.get(zone_id, {})
            for ut_id, zone_id in zone_projection.ut_zone_membership.items()
        }
        active_links: List[Dict[str, Any]] = []
        for link in visibility_slice.active_links:
            link_copy = dict(link)
            if str(link.get("slot_type")) != "ut_access":
                active_links.append(link_copy)
                continue
            binding = dict(link.get("binding", {}))
            ut_id = str(binding.get("ut_id"))
            zone = zone_by_ut.get(ut_id, {})
            zone_anchor_satellite_id = zone.get("active_serving_satellite_id")
            resolution_state = str(zone.get("resolution_state", "resolved"))
            visible_candidates = visibility_slice.candidate_visibility.get(ut_id, [])
            selected_candidate = None
            if zone_anchor_satellite_id:
                selected_candidate = next(
                    (
                        dict(candidate)
                        for candidate in visible_candidates
                        if str(candidate.get("satellite_id") or "")
                        == str(zone_anchor_satellite_id)
                    ),
                    None,
                )
            if selected_candidate is None and visible_candidates:
                selected_candidate = dict(visible_candidates[0])
            if selected_candidate is None:
                if ut_id in nodes:
                    nodes[ut_id]["attachment"] = {
                        "state": "unresolved_ut_visibility",
                        "zone_id": zone_projection.ut_zone_membership.get(ut_id),
                        "zone_resolution_state": resolution_state,
                        "zone_anchor_resolved": bool(
                            zone.get("zone_anchor_resolved")
                        ),
                        "zone_service_resolved": bool(
                            zone.get("zone_service_resolved")
                        ),
                    }
                continue
            selected_satellite_id = str(selected_candidate.get("satellite_id") or "")
            if (
                zone_anchor_satellite_id
                and selected_satellite_id != str(zone_anchor_satellite_id)
            ):
                if ut_id in nodes:
                    nodes[ut_id]["attachment"] = {
                        "state": "unresolved_zone_anchor_not_visible_to_ut",
                        "zone_id": zone_projection.ut_zone_membership.get(ut_id),
                        "zone_resolution_state": resolution_state,
                        "zone_anchor_satellite_id": zone_anchor_satellite_id,
                        "visible_candidate_satellite_ids": [
                            str(candidate.get("satellite_id") or "")
                            for candidate in visible_candidates
                        ],
                        "zone_anchor_resolved": bool(
                            zone.get("zone_anchor_resolved")
                        ),
                        "zone_service_resolved": bool(
                            zone.get("zone_service_resolved")
                        ),
                    }
                continue

            link_copy["binding"] = dict(selected_candidate.get("binding", {}))
            link_copy["endpoints"] = [
                dict(endpoint)
                for endpoint in selected_candidate.get("endpoints", [])
            ]
            active_links.append(link_copy)
            ut_serving_satellite_id = str(selected_candidate.get("satellite_id") or "")
            if ut_id in nodes:
                nodes[ut_id]["attachment"] = {
                    "state": "attached",
                    "slot_name": link_copy.get("link_id"),
                    "active_serving_satellite_id": ut_serving_satellite_id,
                    "zone_anchor_satellite_id": zone_anchor_satellite_id,
                    "zone_resolution_state": resolution_state,
                    "zone_anchor_resolved": bool(zone.get("zone_anchor_resolved")),
                    "ut_attach_resolved": bool(zone.get("ut_attach_resolved")),
                    "zone_service_resolved": bool(zone.get("zone_service_resolved")),
                    "attachment_policy": "independent_workload_probe_visibility",
                    "matches_zone_anchor": (
                        bool(zone_anchor_satellite_id)
                        and ut_serving_satellite_id == str(zone_anchor_satellite_id)
                    ),
                }

        snapshot_seed = {
            "slice_id": orbit_slice.time_slice.get("slice_id"),
            "active_links": active_links,
            "zone_serving": {
                zone_id: zone.get("active_serving_satellite_id")
                for zone_id, zone in sorted(zone_projection.zones.items())
            },
        }
        snapshot_id = f"topsnap-{_fingerprint(snapshot_seed)}"
        return TopologySnapshot(
            schema_version=TOPOLOGY_SNAPSHOT_SCHEMA_VERSION,
            snapshot_id=snapshot_id,
            time_slice=dict(orbit_slice.time_slice),
            provenance={
                "source": self.slicer_name,
                "orbit_producer": orbit_slice.provenance.get("orbit_producer"),
                "visibility_model": visibility_slice.visibility_model,
                "zone_generator": zone_projection.zone_generator,
                "zone_membership_mode": orbit_slice.zone_generation_input.membership_mode,
                "slice_profile": orbit_slice.provenance.get("slice_profile"),
                "zone_visibility_alignment": "independent_zone_anchor_and_ut_attach",
            },
            nodes=nodes,
            active_links=[dict(link) for link in active_links],
            zones={zone_id: dict(zone) for zone_id, zone in zone_projection.zones.items()},
            ut_zone_membership=dict(zone_projection.ut_zone_membership),
            planner_hints=dict(orbit_slice.planner_hints),
        )


def build_experiment_topology_snapshot_pipeline(
    *,
    topology_path: Path,
    profile: ExperimentProfile = DEFAULT_EXPERIMENT_PROFILE,
    topology_slicer: TopologySlicer | None = None,
    input_profile: str = "semi-real-orbital-elements",
    scenario_name: str = "single-ut-direct",
) -> TopologySnapshotPipeline:
    if input_profile == "semi-real-orbital-elements":
        if scenario_name == "small-scale-semi-real-internet":
            scenario = SMALL_SCALE_EXPERIMENT_SCENARIO
            orbit_producer = ScenarioSemiRealOrbitPositionExperimentProducer(
                topology_path=topology_path,
                scenario=scenario,
            )
            visibility_model = SemiRealLineOfSightVisibilityModel(
                min_elevation_deg=scenario.min_elevation_deg
            )
        elif scenario_name == "single-ut-direct":
            orbit_producer = SemiRealOrbitPositionExperimentProducer(
                topology_path=topology_path,
                profile=profile,
            )
            visibility_model = SemiRealLineOfSightVisibilityModel()
        else:
            raise ValueError(f"unsupported scenario_name: {scenario_name}")
        zone_generator: ZoneGenerator = ZoneAnchorQualityGenerator()
    elif input_profile == "synthetic-position":
        orbit_producer = SyntheticPositionExperimentOrbitProducer(
            topology_path=topology_path,
            profile=profile,
        )
        visibility_model = SyntheticDistanceVisibilityModel()
        zone_generator = NearestVisibleZoneGenerator()
    else:
        raise ValueError(f"unsupported experiment input profile: {input_profile}")

    return TopologySnapshotPipeline(
        orbit_producer=orbit_producer,
        visibility_model=visibility_model,
        zone_generator=zone_generator,
        topology_slicer=ExperimentTopologySlicer()
        if topology_slicer is None
        else topology_slicer,
    )
