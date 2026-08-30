"""Read one formal Topology into a detached Tool-owned snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from skyseed_v2.simulator import Topology


@dataclass(frozen=True, slots=True)
class TopologyRead:
    """Hold detached scalar/container values read from one exact Topology."""

    snapshot: dict[str, Any]


def read_topology(topology: Topology) -> TopologyRead:
    """Call the formal Topology API and retain no domain object."""

    if not isinstance(topology, Topology):
        raise TypeError("topology must be a Topology")
    checks: list[dict[str, object]] = []
    check_count = 0
    orbit_rings: list[dict[str, object]] = []
    ground_stations: list[dict[str, object]] = []
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

    for autonomous_system in topology.autonomous_systems:
        counts["autonomous_systems"] += 1
        check(
            f"AS{autonomous_system.asn} owner/facade identity",
            topology.get_autonomous_system(autonomous_system.asn)
            is autonomous_system,
        )
        for shell in autonomous_system.shells:
            counts["shells"] += 1
            check(
                f"AS{autonomous_system.asn}/{shell.shell_id} owner/facade identity",
                autonomous_system.get_shell(shell.shell_id) is shell
                and topology.get_shell(
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
                    and topology.get_orbit(
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
                        and topology.get_satellite(satellite.node_id)
                        is satellite
                        and topology.get_node(satellite.node_id) is satellite,
                    )
                ring = {
                    "asn": orbit.asn,
                    "shell_id": orbit.shell_id,
                    "orbit_id": orbit.orbit_id,
                    "altitude_km": orbit.altitude_km,
                    "inclination_deg": orbit.inclination_deg,
                    "right_ascension_deg": orbit.right_ascension_deg,
                    "satellite_count": len(orbit.satellites),
                    "satellite_ids": [
                        satellite.satellite_id
                        for satellite in orbit.satellites
                    ],
                    "satellites": [
                        {
                            "node_id": satellite.node_id.value,
                            "satellite_id": satellite.satellite_id,
                            "orbit_index": satellite.orbit_index,
                            "max_simultaneous_ground_station_accesses": (
                                satellite.max_simultaneous_ground_station_accesses
                            ),
                        }
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
                and topology.get_ground_station(ground_station.node_id)
                is ground_station
                and topology.get_node(ground_station.node_id)
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
        zone_layout = topology.get_zone_layout()
    except RuntimeError:
        zone_layout = None
    if zone_layout is not None:
        check(
            "Topology/ZoneLayout facade identity",
            topology.get_zone_layout() is zone_layout,
        )
        counts["zones"] = len(zone_layout.zones)
        for zone in zone_layout.zones:
            check(
                f"Zone {zone.zone_id} owner/facade identity",
                zone_layout.get_zone(zone.zone_id) is zone
                and topology.get_zone(zone.zone_id) is zone,
            )

    expected = {
        "autonomous_systems": len(topology.autonomous_systems),
        "shells": sum(
            len(autonomous_system.shells)
            for autonomous_system in topology.autonomous_systems
        ),
        "orbits": sum(
            len(shell.orbits)
            for autonomous_system in topology.autonomous_systems
            for shell in autonomous_system.shells
        ),
        "satellites": sum(
            len(orbit.satellites)
            for autonomous_system in topology.autonomous_systems
            for shell in autonomous_system.shells
            for orbit in shell.orbits
        ),
        "ground_stations": sum(
            len(autonomous_system.ground_stations)
            for autonomous_system in topology.autonomous_systems
        ),
        "zones": 0 if zone_layout is None else len(zone_layout.zones),
    }
    check("public tuple enumeration counts", counts == expected)
    status = (
        "PASS"
        if not checks
        else "FAIL"
    )
    return TopologyRead(
        {
            "format_version": 1,
            "tool": "topology",
            "status": status,
            "topology_created": True,
            "zone_layout_configured": zone_layout is not None,
            "counts": counts,
            "orbit_rings": orbit_rings,
            "ground_stations": ground_stations,
            "consistency": {
                "status": status,
                "checks": checks,
                "passed": check_count - len(checks),
                "failed": len(checks),
            },
        }
    )


__all__ = ["TopologyRead", "read_topology"]
