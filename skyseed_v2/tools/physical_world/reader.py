"""Read one formal PhysicalWorld into a detached Tool-owned snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
from typing import Any

from skyseed_v2.shared import NodeId
from skyseed_v2.simulator import PhysicalFrame, Satellite, PhysicalWorld
from skyseed_v2.simulator.physical.physical_link import PhysicalLinkObservation


@dataclass(frozen=True, slots=True)
class PhysicalWorldRead:
    """Hold detached scalar/container values read from one exact PhysicalWorld."""

    snapshot: dict[str, Any]


def read_physical_world(
    physical_world: PhysicalWorld,
    *,
    physical_frames: tuple[dict[str, Any], ...] = (),
    physical_frame: PhysicalFrame | None = None,
    physical_link_observation: PhysicalLinkObservation | None = None,
    physical_link_model: object | None = None,
    model_descriptors: dict[str, object] | None = None,
) -> PhysicalWorldRead:
    """Call the formal PhysicalWorld API and retain no domain object."""

    if not isinstance(physical_world, PhysicalWorld):
        raise TypeError("physical_world must be a PhysicalWorld")
    checks: list[dict[str, object]] = []
    check_count = 0
    orbit_rings: list[dict[str, object]] = []
    ground_stations: list[dict[str, object]] = []
    zones: list[dict[str, object]] = []
    counts = {
        "autonomous_systems": 0,
        "shells": 0,
        "orbits": 0,
        "satellites": 0,
        "ground_stations": 0,
        "zones": 0,
    }

    def check(label: str, condition: bool) -> None:
        nonlocal check_count
        check_count += 1
        if not condition:
            checks.append({"label": label, "status": "FAIL"})

    for autonomous_system in physical_world.autonomous_systems:
        counts["autonomous_systems"] += 1
        check(
            f"AS{autonomous_system.asn} owner/facade identity",
            physical_world.get_autonomous_system(autonomous_system.asn)
            is autonomous_system,
        )
        for shell in autonomous_system.shells:
            counts["shells"] += 1
            check(
                f"AS{autonomous_system.asn}/{shell.shell_id} owner/facade identity",
                autonomous_system.get_shell(shell.shell_id) is shell
                and physical_world.get_shell(
                    autonomous_system.asn,
                    shell.shell_id,
                )
                is shell,
            )
            for orbit in shell.orbits:
                counts["orbits"] += 1
                check(
                    (
                        f"AS{autonomous_system.asn}/{shell.shell_id}/"
                        f"{orbit.orbit_id} owner/facade identity"
                    ),
                    shell.get_orbit(orbit.orbit_id) is orbit
                    and physical_world.get_orbit(
                        autonomous_system.asn,
                        shell.shell_id,
                        orbit.orbit_id,
                    )
                    is orbit,
                )
                for satellite in orbit.satellites:
                    counts["satellites"] += 1
                    check(
                        f"{satellite.node_id.value} owner/facade identity",
                        orbit.get_satellite(satellite.node_id) is satellite
                        and physical_world.get_satellite(satellite.node_id)
                        is satellite
                        and physical_world.get_node(satellite.node_id) is satellite,
                    )
                ring = {
                    "asn": orbit.asn,
                    "shell_id": orbit.shell_id,
                    "orbit_id": orbit.orbit_id,
                    "altitude_km": orbit.altitude_km,
                    "inclination_deg": orbit.inclination_deg,
                    "right_ascension_deg": orbit.right_ascension_deg,
                    "epoch_utc": orbit.epoch_utc.isoformat(),
                    "satellite_count": len(orbit.satellites),
                    "satellite_ids": [
                        satellite.satellite_id
                        for satellite in orbit.satellites
                    ],
                    "satellites": [
                        _satellite_record(satellite)
                        for satellite in orbit.satellites
                    ],
                }
                orbit_rings.append(ring)

        for ground_station in autonomous_system.ground_stations:
            counts["ground_stations"] += 1
            check(
                f"{ground_station.node_id.value} owner/facade identity",
                autonomous_system.get_ground_station(ground_station.node_id)
                is ground_station
                and physical_world.get_ground_station(ground_station.node_id)
                is ground_station
                and physical_world.get_node(ground_station.node_id)
                is ground_station,
            )
            record = {
                "asn": autonomous_system.asn,
                "ground_station_id": ground_station.ground_station_id,
                "node_id": ground_station.node_id.value,
                "latitude_deg": ground_station.latitude_deg,
                "longitude_deg": ground_station.longitude_deg,
                "elevation_m": ground_station.elevation_m,
                "station_type": ground_station.station_type,
                "operator": ground_station.operator,
                "max_simultaneous_satellite_accesses": (
                    ground_station.max_simultaneous_satellite_accesses
                ),
            }
            ground_stations.append(record)

    try:
        zone_layout = physical_world.get_zone_layout()
    except RuntimeError:
        zone_layout = None
    if zone_layout is not None:
        check(
            "PhysicalWorld/ZoneLayout facade identity",
            physical_world.get_zone_layout() is zone_layout,
        )
        counts["zones"] = len(zone_layout.zones)
        for zone in zone_layout.zones:
            check(
                f"Zone {zone.zone_id} owner/facade identity",
                zone_layout.get_zone(zone.zone_id) is zone
                and physical_world.get_zone(zone.zone_id) is zone,
            )
            zones.append(
                {
                    "zone_id": zone.zone_id,
                    "priority": zone.priority,
                    "geometry": {
                        "south_latitude_deg": (
                            zone.geometry.south_latitude_deg
                        ),
                        "north_latitude_deg": (
                            zone.geometry.north_latitude_deg
                        ),
                        "west_longitude_deg": (
                            zone.geometry.west_longitude_deg
                        ),
                        "east_longitude_deg": (
                            zone.geometry.east_longitude_deg
                        ),
                        "representative_center": tuple(
                            zone.geometry.representative_center
                        ),
                    },
                }
            )

    expected = {
        "autonomous_systems": len(physical_world.autonomous_systems),
        "shells": sum(
            len(autonomous_system.shells)
            for autonomous_system in physical_world.autonomous_systems
        ),
        "orbits": sum(
            len(shell.orbits)
            for autonomous_system in physical_world.autonomous_systems
            for shell in autonomous_system.shells
        ),
        "satellites": sum(
            len(orbit.satellites)
            for autonomous_system in physical_world.autonomous_systems
            for shell in autonomous_system.shells
            for orbit in shell.orbits
        ),
        "ground_stations": sum(
            len(autonomous_system.ground_stations)
            for autonomous_system in physical_world.autonomous_systems
        ),
        "zones": 0 if zone_layout is None else len(zone_layout.zones),
    }
    check("public tuple enumeration counts", counts == expected)
    status = (
        "PASS"
        if not checks
        else "FAIL"
    )
    candidate_snapshot = None
    if physical_link_observation is not None and physical_frame is None:
        raise ValueError(
            "physical_link_observation requires its exact physical_frame"
        )
    if physical_frame is not None:
        frame_snapshot, candidate_snapshot = _read_exact_frame_input(
            physical_world,
            physical_frame,
            physical_link_observation,
        )
        if not physical_frames:
            physical_frames = (frame_snapshot,)

    return PhysicalWorldRead(
        {
            "format_version": 2,
            "tool": "physical-world",
            "status": status,
            "physical_world_created": True,
            "zone_layout_configured": zone_layout is not None,
            "counts": counts,
            "orbit_rings": orbit_rings,
            "ground_stations": ground_stations,
            "zones": zones,
            "physical_frames": physical_frames,
            "candidate_overlay": candidate_snapshot,
            "model_provenance": {
                "position_model": _type_identity(
                    physical_world.get_position_model()
                ),
                "physical_link_model": _type_identity(physical_link_model),
                "zone_membership_model": (
                    None
                    if zone_layout is None
                    else _type_identity(zone_layout.get_membership_model())
                ),
                "configuration": {
                    "source": "materialized public PhysicalWorld values",
                    "orbit_parameters": (
                        "altitude, inclination, right ascension and capacities"
                    ),
                    "ground_station_parameters": (
                        "location, type, operator and access capacity"
                    ),
                    "zone_parameters": (
                        "rectangle, priority and overlap policy"
                    ),
                    "unexposed_model_parameters": (
                        "not persisted or inspected through private fields"
                    ),
                },
                "descriptors": (
                    None
                    if model_descriptors is None
                    else deepcopy(model_descriptors)
                ),
            },
            "consistency": {
                "status": status,
                "checks": checks,
                "passed": check_count - len(checks),
                "failed": len(checks),
            },
        }
    )


def _satellite_record(satellite: Satellite) -> dict[str, object]:
    capability = satellite.inter_satellite_link_capability
    return {
        "node_id": satellite.node_id.value,
        "satellite_id": satellite.satellite_id,
        "orbit_index": satellite.orbit_index,
        "mean_anomaly_deg": satellite.mean_anomaly_deg,
        "max_simultaneous_ground_station_accesses": (
            satellite.max_simultaneous_ground_station_accesses
        ),
        "inter_satellite_link_capability": (
            None
            if capability is None
            else {
                "maximum_simultaneous_links": (
                    capability.maximum_simultaneous_links
                )
            }
        ),
    }


def _read_exact_frame_input(
    physical_world: PhysicalWorld,
    frame: PhysicalFrame,
    observation: PhysicalLinkObservation | None,
) -> tuple[dict[str, object], dict[str, object] | None]:
    """Detach one exact frame for the PhysicalWorld Tool only."""

    if not isinstance(frame, PhysicalFrame):
        raise TypeError("physical_frame must be a PhysicalFrame")
    if observation is not None:
        if not isinstance(observation, PhysicalLinkObservation):
            raise TypeError(
                "physical_link_observation must be a "
                "PhysicalLinkObservation or None"
            )
        if observation.timestamp != frame.timestamp:
            raise ValueError(
                "PhysicalLinkObservation timestamp must match PhysicalFrame"
            )
    node_kinds = {
        satellite.node_id: "satellite"
        for autonomous_system in physical_world.autonomous_systems
        for shell in autonomous_system.shells
        for orbit in shell.orbits
        for satellite in orbit.satellites
    }
    node_kinds.update(
        {
            station.node_id: "ground-station"
            for autonomous_system in physical_world.autonomous_systems
            for station in autonomous_system.ground_stations
        }
    )
    if set(frame.positions) != set(node_kinds):
        raise ValueError(
            "PhysicalFrame positions must match the supplied PhysicalWorld node set"
        )
    snapshot = {
        "timestamp": frame.timestamp.isoformat(),
        "positions": tuple(
            {
                "node_id": node_id.value,
                "kind": node_kinds[node_id],
                "ecef_m": {
                    "x": position.x_m,
                    "y": position.y_m,
                    "z": position.z_m,
                },
            }
            for node_id, position in sorted(
                frame.positions.items(), key=lambda item: item[0].value
            )
        ),
    }
    return snapshot, (
        None
        if observation is None
        else _physical_link_snapshot(
            physical_world, observation, set(node_kinds)
        )
    )


def _physical_link_snapshot(
    physical_world: PhysicalWorld,
    observation: PhysicalLinkObservation,
    expected_node_ids: set[NodeId],
) -> dict[str, object]:
    try:
        zone_ids = {
            zone.zone_id for zone in physical_world.get_zone_layout().zones
        }
    except RuntimeError:
        zone_ids = set()

    inter_satellite = []
    for candidate in observation.inter_satellite_candidates:
        _require_known_node(candidate.endpoint_a, expected_node_ids)
        _require_known_node(candidate.endpoint_b, expected_node_ids)
        inter_satellite.append(
            {
                "endpoint_a": candidate.endpoint_a.value,
                "endpoint_b": candidate.endpoint_b.value,
                "relation": (
                    "same-plane" if candidate.same_plane else "cross-plane"
                ),
                "cyclic_plane_separation": candidate.cyclic_plane_separation,
                "distance_m": candidate.distance_m,
                "propagation_delay_us": candidate.propagation_delay_us,
            }
        )

    ground_station = []
    for candidate in observation.ground_station_access_candidates:
        _require_known_node(candidate.ground_station_id, expected_node_ids)
        _require_known_node(candidate.satellite_id, expected_node_ids)
        ground_station.append(
            {
                "ground_station_id": candidate.ground_station_id.value,
                "satellite_id": candidate.satellite_id.value,
                "distance_m": candidate.distance_m,
                "elevation_deg": candidate.elevation_deg,
                "propagation_delay_us": candidate.propagation_delay_us,
            }
        )

    zone_service = []
    for candidate in observation.zone_service_candidates:
        if candidate.zone_id not in zone_ids:
            raise ValueError(
                "PhysicalLinkObservation Zone must belong to supplied PhysicalWorld"
            )
        _require_known_node(candidate.satellite_id, expected_node_ids)
        zone_service.append(
            {
                "zone_id": candidate.zone_id,
                "satellite_id": candidate.satellite_id.value,
                "distance_m": candidate.distance_m,
                "elevation_deg": candidate.elevation_deg,
                "propagation_delay_us": candidate.propagation_delay_us,
            }
        )

    counts = observation.work_counts
    return {
        "timestamp": observation.timestamp.isoformat(),
        "interpretation": (
            "physically feasible candidates; not operational Connections, "
            "service winners, or Routes"
        ),
        "inter_satellite_candidates": tuple(inter_satellite),
        "ground_station_access_candidates": tuple(ground_station),
        "zone_service_candidates": tuple(zone_service),
        "work_counts": {
            name: getattr(counts, name)
            for name in (
                "inter_satellite_pairs_requested",
                "inter_satellite_pairs_evaluated",
                "inter_satellite_pairs_feasible",
                "ground_station_pairs_requested",
                "ground_station_pairs_evaluated",
                "ground_station_pairs_feasible",
                "zone_service_pairs_requested",
                "zone_service_pairs_evaluated",
                "zone_service_pairs_feasible",
            )
        },
    }


def _require_known_node(
    node_id: NodeId, expected_node_ids: set[NodeId]
) -> None:
    if node_id not in expected_node_ids:
        raise ValueError(
            "PhysicalLinkObservation node must belong to supplied PhysicalWorld"
        )


def _type_identity(value: object | None) -> str | None:
    if value is None:
        return None
    kind = type(value)
    return f"{kind.__module__}.{kind.__qualname__}"


__all__ = ["PhysicalWorldRead", "read_physical_world"]
