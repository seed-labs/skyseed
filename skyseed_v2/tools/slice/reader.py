"""Read formal Slice/Difference facts into a detached Tool snapshot."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from pathlib import Path

from skyseed_v2.shared import NodeId
from skyseed_v2.simulator import (
    Connection,
    ConnectionDirection,
    ConnectionId,
    ConnectionKind,
    ConnectionSlice,
    DirectionalConnectionParameters,
    DifferenceOperation,
    EarthFixedPosition,
    GroundStationRouteTarget,
    RouteKey,
    RouteSlice,
    RouteTarget,
    Slicer,
    Topology,
    TopologyDifference,
    TopologySlice,
    ZoneRouteTarget,
)
from skyseed_v2.simulator._history_codec import _difference_from_data
from tools._history import iter_jsonl_records, read_jsonl_records


@dataclass(frozen=True, slots=True)
class SliceDifferenceRead:
    """Hold only Tool-owned scalars and containers read from one Slicer."""

    snapshot: dict[str, Any]


def read_slice_history(path: Path) -> dict[str, Any]:
    """Read compact durable Position rows after rolling release."""

    records = read_jsonl_records(path)
    headers = tuple(
        value for value in records if value.get("record_type") == "history_header"
    )
    if len(headers) != 1 or headers[0].get("owner") != "Slicer":
        raise ValueError("durable Slice / Difference history header is invalid")
    slices = tuple(value for value in records if value.get("record_type") == "slice")
    differences = tuple(
        value for value in records if value.get("record_type") == "difference"
    )
    if tuple(value.get("slice_id") for value in slices) != tuple(range(len(slices))):
        raise ValueError("durable Slice history must be one continuous prefix")
    if differences and tuple(
        value.get("current_slice_id") for value in differences
    ) != tuple(range(1, len(slices))):
        raise ValueError("durable Difference history must align with Slices")
    if headers[0].get("format_version") == 2 and differences:
        raise ValueError("compact Slicer history must not duplicate Differences")
    return {
        "header": headers[0],
        "slices": slices,
        "differences": differences,
    }


def read_slice_differences(
    slicer: Slicer,
    *,
    model_context: Mapping[str, object] | None = None,
) -> SliceDifferenceRead:
    """Call every formal API and retain no Slicer/Topology/result object."""

    if not isinstance(slicer, Slicer):
        raise TypeError("slicer must be a Slicer")
    if model_context is not None and not isinstance(model_context, Mapping):
        raise TypeError("model_context must be a mapping or None")
    retained_ids = slicer.get_retained_slice_ids()
    if not retained_ids or retained_ids[0] != 0:
        raise ValueError("Slice / Difference inspection requires retained Slice 0")
    if retained_ids != tuple(range(retained_ids[-1] + 1)):
        raise ValueError("retained Slice IDs must form one continuous prefix")

    topology = slicer.get_topology()
    timeline = slicer.get_timeline()
    slices = {
        slice_id: slicer.get_slice(slice_id) for slice_id in retained_ids
    }
    differences = {
        slice_id: slicer.create_difference(slice_id - 1, slice_id)
        for slice_id in retained_ids
        if slice_id != 0
    }
    return _read_slice_values(
        topology,
        timeline,
        retained_ids,
        slices,
        differences,
        model_context=model_context,
        source="retained public results from one exact Slicer",
        difference_source="Slicer.create_difference",
    )


def read_durable_slice_differences(
    slice_history_path: Path,
    planner_history_path: Path,
    *,
    topology: Topology,
    timeline: object,
    model_context: Mapping[str, object] | None = None,
) -> SliceDifferenceRead:
    """Project released history without rerunning any domain Model."""

    history = read_slice_history(slice_history_path)
    differences = {
        int(value["current_slice_id"]): _difference_from_data(value["semantic"])
        for value in iter_jsonl_records(planner_history_path)
        if value.get("record_type") == "difference_consumed"
    }
    return _read_durable_slice_values(
        history,
        differences,
        topology=topology,
        timeline=timeline,
        model_context=model_context,
    )


def _read_durable_slice_values(
    history: Mapping[str, object],
    differences: Mapping[int, TopologyDifference],
    *,
    topology: Topology,
    timeline: object,
    model_context: Mapping[str, object] | None = None,
) -> SliceDifferenceRead:
    slice_records = tuple(history["slices"])
    if slice_records and "positions" in slice_records[0]:
        slices = _reconstruct_topology_slices(
            slice_records, differences, topology
        )
    else:
        slices = {
            int(value["slice_id"]): _topology_slice_from_semantic(
                value["semantic"]
            )
            for value in slice_records
        }
    retained_ids = tuple(sorted(slices))
    if retained_ids != tuple(range(len(retained_ids))):
        raise ValueError("durable Slice IDs must form one continuous prefix")
    if tuple(sorted(differences)) != retained_ids[1:]:
        raise ValueError("durable Planner Differences must align with Slice history")
    return _read_slice_values(
        topology,
        timeline,
        retained_ids,
        slices,
        differences,
        model_context=model_context,
        source="complete durable semantic Slicer history",
        difference_source="complete durable Planner-consumed Difference history",
    )


def _reconstruct_topology_slices(
    records: tuple[Mapping[str, object], ...],
    differences: Mapping[int, TopologyDifference],
    topology: Topology,
) -> dict[int, TopologySlice]:
    nodes_by_value = {
        node_id.value: node_id for node_id in _topology_node_ids(topology)
    }
    connections: dict[ConnectionId, Connection] = {}
    assignments: dict[str, NodeId] = {}
    routes: dict[RouteKey, NodeId] = {}
    slices: dict[int, TopologySlice] = {}
    for record in records:
        slice_id = int(record["slice_id"])
        if slice_id:
            _apply_topology_difference(
                differences[slice_id], connections, assignments, routes
            )
        timestamp = record.get("timestamp")
        positions = {}
        raw_positions = _mapping(record.get("positions"), "positions")
        for node_value, coordinates in raw_positions.items():
            try:
                node_id = nodes_by_value[str(node_value)]
            except KeyError as error:
                raise ValueError(
                    "durable Position identifies an unknown topology node"
                ) from error
            xyz = _sequence(coordinates, "position coordinates")
            if len(xyz) != 3:
                raise ValueError("durable Position must contain x/y/z")
            positions[node_id] = EarthFixedPosition(*(float(item) for item in xyz))
        slices[slice_id] = TopologySlice(
            slice_id=slice_id,
            timestamp=(
                None if timestamp is None else datetime.fromisoformat(str(timestamp))
            ),
            positions=positions,
            connection_slice=ConnectionSlice(
                connections.values(), assignments
            ),
            route_slice=RouteSlice(routes.items()),
        )
    return slices


def _apply_topology_difference(
    difference: TopologyDifference,
    connections: dict[ConnectionId, Connection],
    assignments: dict[str, NodeId],
    routes: dict[RouteKey, NodeId],
) -> None:
    for connection_id in difference.removed_connection_ids:
        connections.pop(connection_id)
    for connection in difference.added_connections:
        connections[connection.connection_id] = connection
    for patch in difference.connection_parameter_patches:
        connection = connections[patch.connection_id]
        field = (
            "endpoint_a_to_b"
            if patch.direction is ConnectionDirection.ENDPOINT_A_TO_B
            else "endpoint_b_to_a"
        )
        current = getattr(connection, field)
        changed = DirectionalConnectionParameters(
            bandwidth_bps=(
                current.bandwidth_bps
                if patch.bandwidth_bps is None
                else patch.bandwidth_bps
            ),
            delay_us=(
                current.delay_us if patch.delay_us is None else patch.delay_us
            ),
            packet_loss_ppm=(
                current.packet_loss_ppm
                if patch.packet_loss_ppm is None
                else patch.packet_loss_ppm
            ),
        )
        connections[patch.connection_id] = Connection(
            connection.connection_id,
            changed if field == "endpoint_a_to_b" else connection.endpoint_a_to_b,
            changed if field == "endpoint_b_to_a" else connection.endpoint_b_to_a,
        )
    for patch in difference.zone_service_assignment_patches:
        if patch.operation is DifferenceOperation.REMOVED:
            assignments.pop(patch.zone_id)
        else:
            assert patch.service_satellite_id is not None
            assignments[patch.zone_id] = patch.service_satellite_id
    for patch in difference.route_patches:
        if patch.operation is DifferenceOperation.REMOVED:
            routes.pop(patch.route_key)
        else:
            assert patch.next_hop is not None
            routes[patch.route_key] = patch.next_hop


def _topology_node_ids(topology: Topology) -> tuple[NodeId, ...]:
    values = []
    for autonomous_system in topology.autonomous_systems:
        for shell in autonomous_system.shells:
            for orbit in shell.orbits:
                values.extend(satellite.node_id for satellite in orbit.satellites)
        values.extend(station.node_id for station in autonomous_system.ground_stations)
    return tuple(values)


def _read_slice_values(
    topology: Topology,
    timeline: object,
    retained_ids: tuple[int, ...],
    slices: Mapping[int, TopologySlice],
    differences: Mapping[int, TopologyDifference],
    *,
    model_context: Mapping[str, object] | None,
    source: str,
    difference_source: str,
) -> SliceDifferenceRead:
    nodes, orbits = _read_static_nodes(topology)
    zones, formal_zones = _read_zones(topology)
    slice_records = []
    for slice_id in retained_ids:
        topology_slice = slices[slice_id]
        positions = tuple(
            {
                "node_id": node_id.value,
                "ecef_m": {
                    "x": position.x_m,
                    "y": position.y_m,
                    "z": position.z_m,
                },
                "geographic": {
                    "latitude_deg": position.latitude_deg,
                    "longitude_deg": position.longitude_deg,
                    "ellipsoidal_height_m": (
                        position.ellipsoidal_height_m
                    ),
                },
            }
            for node_id, position in topology_slice.positions.items()
        )
        connections = tuple(
            _read_connection(connection)
            for connection in topology_slice.connection_slice.connections
        )
        assignments = tuple(
            _read_zone_assignment(
                zone_id,
                satellite_id,
                topology_slice.positions,
                formal_zones,
            )
            for zone_id, satellite_id in (
                topology_slice.connection_slice.zone_service_assignments.items()
            )
        )
        routes = tuple(
            _read_route(RouteKey(source, target), next_hop)
            for source, targets in topology_slice.route_slice.routes.items()
            for target, next_hop in targets.items()
        )
        difference = (
            None
            if slice_id == 0
            else _read_difference(differences[slice_id])
        )
        slice_records.append(
            {
                "slice_id": topology_slice.slice_id,
                "timestamp": (
                    None
                    if topology_slice.timestamp is None
                    else topology_slice.timestamp.isoformat()
                ),
                "positions": positions,
                "connections": connections,
                "zone_service_assignments": assignments,
                "routes": routes,
                "difference_from_previous": difference,
            }
        )

    return SliceDifferenceRead(
        {
            "format_version": 1,
            "tool": "slice-difference-inspection",
            "authority": {
                "status": "non-authoritative-view",
                "source": source,
                "position": (
                    "EarthFixedPosition with formal geographic views"
                ),
                "paths": (
                    "selected browser display restoration from projected "
                    "formal next-hop facts"
                ),
                "differences": difference_source,
            },
            "timeline": {
                "retained_slice_ids": list(retained_ids),
                "slice_count_including_zero": len(retained_ids),
                "real_slice_count": len(retained_ids) - 1,
                "start_time": timeline.start_time.isoformat(),
                "end_time": (
                    None
                    if timeline.end_time is None
                    else timeline.end_time.isoformat()
                ),
                "slice_interval_seconds": (
                    timeline.slice_interval.total_seconds()
                ),
            },
            "topology": {
                "nodes": nodes,
                "orbits": orbits,
                "zones": zones,
            },
            "model_context": deepcopy(dict(model_context or {})),
            "slices": slice_records,
        }
    )


def _topology_slice_from_semantic(value: object) -> TopologySlice:
    semantic = _mapping(value, "TopologySlice semantic value")
    positions = {
        _node_id_from_semantic(item["key"]): _position_from_semantic(item["value"])
        for item in _semantic_mapping_items(semantic["positions"])
    }
    connection_value = _mapping(semantic["connection_slice"], "ConnectionSlice")
    connections = tuple(
        _connection_from_semantic(item)
        for item in _sequence(connection_value["connections"], "connections")
    )
    assignments = {
        str(zone_id): _node_id_from_semantic(node_id)
        for zone_id, node_id in _mapping(
            connection_value["zone_service_assignments"], "zone assignments"
        ).items()
        if zone_id != "$type"
    }
    route_value = _mapping(semantic["route_slice"], "RouteSlice")
    routes = []
    for source_item in _semantic_mapping_items(route_value["routes"]):
        source = _node_id_from_semantic(source_item["key"])
        for target_item in _semantic_mapping_items(source_item["value"]):
            routes.append(
                (
                    RouteKey(source, _route_target_from_semantic(target_item["key"])),
                    _node_id_from_semantic(target_item["value"]),
                )
            )
    timestamp = semantic["timestamp"]
    return TopologySlice(
        slice_id=int(semantic["slice_id"]),
        timestamp=(
            None
            if timestamp is None
            else datetime.fromisoformat(str(_mapping(timestamp, "timestamp")["value"]))
        ),
        positions=positions,
        connection_slice=ConnectionSlice(connections, assignments),
        route_slice=RouteSlice(routes),
    )


def _connection_from_semantic(value: object) -> Connection:
    semantic = _mapping(value, "Connection")
    return Connection(
        _connection_id_from_semantic(semantic["connection_id"]),
        _parameters_from_semantic(semantic["endpoint_a_to_b"]),
        _parameters_from_semantic(semantic["endpoint_b_to_a"]),
    )


def _connection_id_from_semantic(value: object) -> ConnectionId:
    identity = _mapping(value, "ConnectionId")
    kind = _mapping(identity["kind"], "ConnectionKind")
    return ConnectionId(
        ConnectionKind(str(kind["value"])),
        _node_id_from_semantic(identity["endpoint_a"]),
        _node_id_from_semantic(identity["endpoint_b"]),
    )


def _parameters_from_semantic(value: object) -> DirectionalConnectionParameters:
    semantic = _mapping(value, "DirectionalConnectionParameters")
    return DirectionalConnectionParameters(
        bandwidth_bps=int(semantic["bandwidth_bps"]),
        delay_us=int(semantic["delay_us"]),
        packet_loss_ppm=int(semantic["packet_loss_ppm"]),
    )


def _node_id_from_semantic(value: object) -> NodeId:
    semantic = _mapping(value, "NodeId")
    return NodeId(
        asn=int(semantic["asn"]),
        entity_kind=str(semantic["entity_kind"]),
        entity_id=str(semantic["entity_id"]),
    )


def _position_from_semantic(value: object) -> EarthFixedPosition:
    semantic = _mapping(value, "EarthFixedPosition")
    return EarthFixedPosition(
        float(semantic["x_m"]),
        float(semantic["y_m"]),
        float(semantic["z_m"]),
    )


def _route_target_from_semantic(value: object) -> RouteTarget:
    semantic = _mapping(value, "RouteTarget")
    type_name = str(semantic.get("$type", ""))
    if type_name.endswith("GroundStationRouteTarget"):
        return GroundStationRouteTarget(
            _node_id_from_semantic(semantic["ground_station_id"])
        )
    if type_name.endswith("ZoneRouteTarget"):
        return ZoneRouteTarget(str(semantic["zone_id"]))
    raise ValueError("durable RouteTarget semantic type is unsupported")


def _semantic_mapping_items(value: object) -> tuple[Mapping[str, object], ...]:
    semantic = _mapping(value, "semantic mapping")
    if not semantic:
        return ()
    if semantic.get("$type") != "mapping":
        raise ValueError("durable semantic mapping marker is invalid")
    return tuple(
        _mapping(item, "semantic mapping item")
        for item in _sequence(semantic.get("items"), "semantic mapping items")
    )


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return value


def _sequence(value: object, label: str) -> tuple[object, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    return tuple(value)


def _read_static_nodes(
    topology: Topology,
) -> tuple[
    tuple[dict[str, object], ...],
    tuple[dict[str, object], ...],
]:
    nodes = []
    orbits = []
    for autonomous_system in topology.autonomous_systems:
        for shell in autonomous_system.shells:
            for orbit in shell.orbits:
                orbit_ref = _orbit_reference(
                    autonomous_system.asn,
                    shell.shell_id,
                    orbit.orbit_id,
                )
                satellite_ids = []
                for satellite in orbit.satellites:
                    satellite_ids.append(satellite.node_id.value)
                    nodes.append(
                        {
                            "node_id": satellite.node_id.value,
                            "kind": "satellite",
                            "asn": autonomous_system.asn,
                            "shell_id": shell.shell_id,
                            "orbit_id": orbit.orbit_id,
                            "orbit_ref": orbit_ref,
                            "orbit_index": satellite.orbit_index,
                            "mean_anomaly_deg": satellite.mean_anomaly_deg,
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
                        "epoch_utc": orbit.epoch_utc.isoformat(),
                        "satellite_ids": satellite_ids,
                    }
                )
        for station in autonomous_system.ground_stations:
            nodes.append(
                {
                    "node_id": station.node_id.value,
                    "kind": "ground-station",
                    "asn": autonomous_system.asn,
                    "latitude_deg": station.latitude_deg,
                    "longitude_deg": station.longitude_deg,
                    "ellipsoidal_height_m": station.elevation_m,
                    "station_type": station.station_type,
                    "operator": station.operator,
                }
            )
    return (
        tuple(sorted(nodes, key=lambda item: str(item["node_id"]))),
        tuple(
            sorted(
                orbits,
                key=lambda item: (
                    int(item["asn"]),
                    str(item["shell_id"]),
                    str(item["orbit_id"]),
                ),
            )
        ),
    )


def _read_zones(
    topology: Topology,
) -> tuple[tuple[dict[str, object], ...], dict[str, Any]]:
    try:
        zone_layout = topology.get_zone_layout()
    except RuntimeError:
        return (), {}
    formal_zones = {
        zone.zone_id: zone for zone in zone_layout.zones
    }
    values = tuple(
        {
            "zone_id": zone.zone_id,
            "priority": zone.priority,
            "representative_point": {
                "latitude_deg": zone.representative_point[0],
                "longitude_deg": zone.representative_point[1],
            },
            "geometry": {
                "kind": "latitude-longitude-rectangle",
                "south_latitude_deg": zone.geometry.south_latitude_deg,
                "north_latitude_deg": zone.geometry.north_latitude_deg,
                "west_longitude_deg": zone.geometry.west_longitude_deg,
                "east_longitude_deg": zone.geometry.east_longitude_deg,
            },
        }
        for zone in zone_layout.zones
    )
    return values, formal_zones


def _read_zone_assignment(
    zone_id: str,
    satellite_id: NodeId,
    positions: Any,
    zones_by_id: dict[str, Any],
) -> dict[str, object]:
    zone = zones_by_id[zone_id]
    position = positions[satellite_id]
    return {
        "zone_id": zone_id,
        "service_satellite_id": satellite_id.value,
        "_display_input": {
            "representative_point": list(zone.representative_point),
            "satellite_ecef_m": [
                position.x_m,
                position.y_m,
                position.z_m,
            ],
            "subpoint_inside_zone": zone.contains(
                position.latitude_deg,
                position.longitude_deg,
            ),
        },
    }


def _read_connection(connection: Connection) -> dict[str, object]:
    identity = connection.connection_id
    return {
        "connection_id": _connection_id_data(identity),
        "connection_ref": _connection_reference(identity),
        "endpoint_a_to_b": _parameters_data(connection.endpoint_a_to_b),
        "endpoint_b_to_a": _parameters_data(connection.endpoint_b_to_a),
    }


def _connection_id_data(identity: ConnectionId) -> dict[str, str]:
    return {
        "kind": identity.kind.value,
        "endpoint_a": identity.endpoint_a.value,
        "endpoint_b": identity.endpoint_b.value,
    }


def _parameters_data(parameters: Any) -> dict[str, int]:
    return {
        "bandwidth_bps": parameters.bandwidth_bps,
        "delay_us": parameters.delay_us,
        "packet_loss_ppm": parameters.packet_loss_ppm,
    }


def _read_route(
    route_key: RouteKey,
    next_hop: NodeId,
) -> tuple[str, str, str]:
    return (
        route_key.source.value,
        _target_reference(route_key.target),
        next_hop.value,
    )


def _read_difference(
    difference: TopologyDifference,
) -> dict[str, object]:
    return {
        "previous_slice_id": difference.previous_slice_id,
        "previous_timestamp": (
            None
            if difference.previous_timestamp is None
            else difference.previous_timestamp.isoformat()
        ),
        "current_slice_id": difference.current_slice_id,
        "current_timestamp": difference.current_timestamp.isoformat(),
        "is_empty": difference.is_empty,
        "added_connections": [
            _read_connection(connection)
            for connection in difference.added_connections
        ],
        "removed_connection_ids": [
            {
                "connection_id": _connection_id_data(identity),
                "connection_ref": _connection_reference(identity),
            }
            for identity in difference.removed_connection_ids
        ],
        "connection_parameter_patches": [
            {
                "connection_ref": _connection_reference(
                    patch.connection_id
                ),
                "connection_id": _connection_id_data(patch.connection_id),
                "direction": patch.direction.value,
                "changed": {
                    key: value
                    for key, value in (
                        ("bandwidth_bps", patch.bandwidth_bps),
                        ("delay_us", patch.delay_us),
                        ("packet_loss_ppm", patch.packet_loss_ppm),
                    )
                    if value is not None
                },
            }
            for patch in difference.connection_parameter_patches
        ],
        "zone_service_assignment_patches": [
            {
                "zone_id": patch.zone_id,
                "operation": patch.operation.value,
                "service_satellite_id": (
                    None
                    if patch.service_satellite_id is None
                    else patch.service_satellite_id.value
                ),
            }
            for patch in difference.zone_service_assignment_patches
        ],
        "route_patches": [
            (
                patch.operation.value,
                patch.route_key.source.value,
                _target_reference(patch.route_key.target),
                None if patch.next_hop is None else patch.next_hop.value,
            )
            for patch in difference.route_patches
        ],
    }


def _connection_reference(identity: ConnectionId) -> str:
    return "|".join(
        (
            identity.kind.value,
            identity.endpoint_a.value,
            identity.endpoint_b.value,
        )
    )


def _target_reference(target: RouteTarget) -> str:
    return (
        f"G|{target.ground_station_id.value}"
        if isinstance(target, GroundStationRouteTarget)
        else f"Z|{target.zone_id}"
    )


def _orbit_reference(asn: int, shell_id: str, orbit_id: str) -> str:
    return f"{asn}|{shell_id}|{orbit_id}"


__all__ = [
    "SliceDifferenceRead",
    "read_slice_history",
    "read_slice_differences",
]
