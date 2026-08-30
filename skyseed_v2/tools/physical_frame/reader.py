"""Read one exact PhysicalFrame and its Topology into detached Tool values."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from skyseed_v2.simulator import PhysicalFrame, Topology


@dataclass(frozen=True, slots=True)
class PhysicalFrameRead:
    """Hold Tool-owned scalar/container values from one physical frame."""

    snapshot: dict[str, Any]


def read_physical_frame(
    topology: Topology,
    frame: PhysicalFrame,
) -> PhysicalFrameRead:
    """Read physical facts without invoking projection or retaining owners."""

    if not isinstance(topology, Topology):
        raise TypeError("topology must be a Topology")
    if not isinstance(frame, PhysicalFrame):
        raise TypeError("frame must be a PhysicalFrame")

    nodes: list[dict[str, object]] = []
    orbits: list[dict[str, object]] = []
    expected_node_ids = set()
    for autonomous_system in topology.autonomous_systems:
        for shell in autonomous_system.shells:
            for orbit in shell.orbits:
                orbit_ref = _orbit_reference(
                    autonomous_system.asn,
                    shell.shell_id,
                    orbit.orbit_id,
                )
                satellite_node_ids = []
                for satellite in orbit.satellites:
                    node_id = satellite.node_id
                    expected_node_ids.add(node_id)
                    satellite_node_ids.append(node_id.value)
                    nodes.append(
                        {
                            "node_id": node_id.value,
                            "kind": "satellite",
                            "asn": autonomous_system.asn,
                            "shell_id": shell.shell_id,
                            "orbit_id": orbit.orbit_id,
                            "orbit_ref": orbit_ref,
                            "orbit_index": satellite.orbit_index,
                            "max_simultaneous_ground_station_accesses": (
                                satellite.max_simultaneous_ground_station_accesses
                            ),
                        }
                    )
                orbits.append(
                    {
                        "asn": autonomous_system.asn,
                        "shell_id": shell.shell_id,
                        "orbit_id": orbit.orbit_id,
                        "orbit_ref": orbit_ref,
                        "altitude_km": orbit.altitude_km,
                        "inclination_deg": orbit.inclination_deg,
                        "right_ascension_deg": orbit.right_ascension_deg,
                        "satellite_node_ids": satellite_node_ids,
                    }
                )
        for ground_station in autonomous_system.ground_stations:
            node_id = ground_station.node_id
            expected_node_ids.add(node_id)
            nodes.append(
                {
                    "node_id": node_id.value,
                    "kind": "ground-station",
                    "asn": autonomous_system.asn,
                    "station_type": ground_station.station_type,
                    "operator": ground_station.operator,
                    "max_simultaneous_satellite_accesses": (
                        ground_station.max_simultaneous_satellite_accesses
                    ),
                }
            )

    if set(frame.positions) != expected_node_ids:
        raise ValueError(
            "PhysicalFrame positions must match the supplied Topology node set"
        )
    node_records = {str(value["node_id"]): value for value in nodes}
    for node_id, position in frame.positions.items():
        node_records[node_id.value]["position"] = {
            "ecef_m": {
                "x": position.x_m,
                "y": position.y_m,
                "z": position.z_m,
            },
            "geodetic": {
                "latitude_deg": position.latitude_deg,
                "longitude_deg": position.longitude_deg,
                "ellipsoidal_height_m": position.ellipsoidal_height_m,
            },
        }

    return PhysicalFrameRead(
        {
            "format_version": 1,
            "tool": "physical-frame",
            "authority": {
                "status": "non-authoritative-view",
                "source": "one exact PhysicalFrame and its Topology",
                "position": (
                    "formal EarthFixedPosition ECEF values and formal WGS84 "
                    "geodetic views"
                ),
            },
            "timestamp": frame.timestamp.isoformat(),
            "nodes": tuple(
                sorted(nodes, key=lambda value: str(value["node_id"]))
            ),
            "orbits": tuple(
                sorted(
                    orbits,
                    key=lambda value: (
                        int(value["asn"]),
                        str(value["shell_id"]),
                        str(value["orbit_id"]),
                    ),
                )
            ),
        }
    )


def _orbit_reference(asn: int, shell_id: str, orbit_id: str) -> str:
    return f"{asn}|{shell_id}|{orbit_id}"


__all__ = ["PhysicalFrameRead", "read_physical_frame"]
