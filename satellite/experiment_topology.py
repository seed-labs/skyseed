#!/usr/bin/env python3
# encoding: utf-8

"""Orbit/position-driven Iteration 2 experiment topology pipeline."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

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
    "gw1": GroundLocation(latitude_deg=40.71, longitude_deg=-74.00),
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
    "sat-b1": SemiRealOrbitalElements(
        altitude_km=550.0,
        inclination_deg=53.0,
        raan_deg=-90.0,
        phase_deg=80.0,
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
        positions = {
            node_id: ExperimentNodePosition(
                x_km=float(node.get("position_state", {}).get("x_km", 0.0)),
                y_km=float(node.get("position_state", {}).get("y_km", 0.0)),
                z_km=float(node.get("position_state", {}).get("z_km", 0.0)),
            )
            for node_id, node in orbit_slice.nodes.items()
        }
        for link in orbit_slice.candidate_links:
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
        positions = {
            node_id: ExperimentNodePosition(
                x_km=float(node.get("position_state", {}).get("x_km", 0.0)),
                y_km=float(node.get("position_state", {}).get("y_km", 0.0)),
                z_km=float(node.get("position_state", {}).get("z_km", 0.0)),
            )
            for node_id, node in orbit_slice.nodes.items()
        }
        for link in orbit_slice.candidate_links:
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
        )


class NearestVisibleZoneGenerator(ZoneGenerator):
    """Choose the nearest candidate satellite as the zone owner."""

    generator_name = "nearest_visible_zone_generator"

    def generate(self, orbit_slice: OrbitTopologySlice) -> ZoneProjection:
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
            ut_id = str(zone.get("ut_ids", [])[0])
            ut_position = positions[ut_id]
            candidates = [
                (
                    _distance_km(ut_position, positions[str(satellite_id)]),
                    str(satellite_id),
                )
                for satellite_id in zone.get("candidate_serving_satellite_ids", [])
                if str(satellite_id) in positions
            ]
            _, serving_satellite_id = min(candidates, key=lambda item: (item[0], item[1]))
            zones[zone_id] = {
                **dict(zone),
                "active_serving_satellite_id": serving_satellite_id,
            }
        return ZoneProjection(
            zones=zones,
            ut_zone_membership=dict(orbit_slice.zone_generation_input.ut_zone_membership),
            zone_generator=self.generator_name,
        )


class HighestElevationZoneGenerator(ZoneGenerator):
    """Choose the highest-elevation visible candidate satellite as the zone owner."""

    generator_name = "highest_elevation_zone_generator"

    def generate(self, orbit_slice: OrbitTopologySlice) -> ZoneProjection:
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
            ut_id = str(zone.get("ut_ids", [])[0])
            ut_position = positions[ut_id]
            candidates = []
            for satellite_id in zone.get("candidate_serving_satellite_ids", []):
                satellite_text = str(satellite_id)
                if satellite_text not in positions:
                    continue
                elevation_deg = _elevation_deg(ut_position, positions[satellite_text])
                candidates.append((elevation_deg, satellite_text))
            if not candidates:
                continue
            _, serving_satellite_id = max(candidates, key=lambda item: (item[0], item[1]))
            zones[zone_id] = {
                **dict(zone),
                "active_serving_satellite_id": serving_satellite_id,
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
        for link in visibility_slice.active_links:
            if str(link.get("slot_type")) != "ut_access":
                continue
            binding = dict(link.get("binding", {}))
            ut_id = str(binding.get("ut_id"))
            satellite_id = str(binding.get("satellite_id"))
            if ut_id in nodes:
                nodes[ut_id]["attachment"] = {
                    "state": "attached",
                    "slot_name": link.get("link_id"),
                    "active_serving_satellite_id": satellite_id,
                }

        snapshot_seed = {
            "slice_id": orbit_slice.time_slice.get("slice_id"),
            "active_links": visibility_slice.active_links,
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
            },
            nodes=nodes,
            active_links=[dict(link) for link in visibility_slice.active_links],
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
) -> TopologySnapshotPipeline:
    if input_profile == "semi-real-orbital-elements":
        orbit_producer: OrbitTopologyProducer = SemiRealOrbitPositionExperimentProducer(
            topology_path=topology_path,
            profile=profile,
        )
        visibility_model: VisibilityModel = SemiRealLineOfSightVisibilityModel()
        zone_generator: ZoneGenerator = HighestElevationZoneGenerator()
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
