"""Project accepted Dynamic Slice results into an offline inspection bundle."""

from __future__ import annotations

from hashlib import sha256
from math import atan2, cos, hypot, sin
from pathlib import Path
from typing import Any

from skyseed_v2.node_id import NodeId
from skyseed_v2.simulator import (
    Connection,
    ConnectionDirection,
    ConnectionId,
    ConnectionKind,
    DifferenceOperation,
    GroundStationRouteTarget,
    RouteKey,
    RouteTarget,
    Slicer,
    TopologyDifference,
    ZoneRouteTarget,
)
from skyseed_v2.simulator.coordinates import elevation_deg, geodetic_to_ecef

from ._dynamic_slice_ui import render_dynamic_slice_page
from ._html import write_json, write_text


def collect_dynamic_slice_data(slicer: Slicer) -> dict[str, Any]:
    """Read one Slicer's retained public results into a private view mapping."""

    if not isinstance(slicer, Slicer):
        raise TypeError("slicer must be a Slicer")
    topology = slicer.get_topology()
    retained_ids = slicer.get_retained_slice_ids()
    if not retained_ids or retained_ids[0] != 0:
        raise ValueError("Dynamic Slice inspection requires retained Slice 0")
    if retained_ids != tuple(range(retained_ids[-1] + 1)):
        raise ValueError("retained Slice IDs must form one continuous prefix")

    nodes, orbits, satellite_parents = _collect_static_nodes(slicer)
    adjacent_orbit_sides = _adjacent_orbit_sides(orbits)
    zones = _collect_zones(slicer)
    try:
        zone_values_by_id = {
            zone.zone_id: zone for zone in topology.get_zone_layout().zones
        }
    except RuntimeError:
        zone_values_by_id = {}
    slice_records = []
    for slice_id in retained_ids:
        topology_slice = slicer.get_slice(slice_id)
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
                    "ellipsoidal_height_m": position.ellipsoidal_height_m,
                },
            }
            for node_id, position in topology_slice.positions.items()
        )
        connections = tuple(
            _connection_data(
                connection,
                satellite_parents,
                adjacent_orbit_sides,
            )
            for connection in topology_slice.connection_slice.connections
        )
        assignments = tuple(
            _zone_assignment_data(
                zone_id,
                satellite_id,
                topology_slice.positions,
                zone_values_by_id,
            )
            for zone_id, satellite_id in (
                topology_slice.connection_slice.zone_service_assignments.items()
            )
        )
        routes = tuple(
            _route_data(RouteKey(source, target), next_hop)
            for source, targets in topology_slice.route_slice.routes.items()
            for target, next_hop in targets.items()
        )
        paths = tuple(
            _path_data(slicer, slice_id, source, target)
            for source, targets in topology_slice.route_slice.routes.items()
            for target in targets
        )
        orbit_guides = _orbit_guide_data(orbits, positions)
        difference = (
            None
            if slice_id == 0
            else _difference_data(
                slicer.create_difference(slice_id - 1, slice_id),
                satellite_parents,
                adjacent_orbit_sides,
            )
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
                "orbit_guides": orbit_guides,
                "connections": connections,
                "zone_service_assignments": assignments,
                "routes": routes,
                "paths": paths,
                "difference_from_previous": difference,
            }
        )

    behavior = _behavior_inventory(slice_records)
    return {
        "format_version": 1,
        "tool": "dynamic-slice-inspection",
        "authority": {
            "status": "non-authoritative-view",
            "source": "retained public results from one exact Slicer",
            "position": "EarthFixedPosition with formal geographic views",
            "paths": "Slicer.resolve_path",
            "differences": "Slicer.create_difference",
        },
        "timeline": {
            "retained_slice_ids": list(retained_ids),
            "slice_count_including_zero": len(retained_ids),
            "real_slice_count": len(retained_ids) - 1,
            "start_time": slicer.get_timeline().start_time.isoformat(),
            "end_time": (
                None
                if slicer.get_timeline().end_time is None
                else slicer.get_timeline().end_time.isoformat()
            ),
            "slice_interval_seconds": (
                slicer.get_timeline().slice_interval.total_seconds()
            ),
        },
        "topology": {
            "nodes": nodes,
            "orbits": orbits,
            "zones": zones,
        },
        "slices": slice_records,
        "inventory": {
            "nodes": len(nodes),
            "satellites": sum(node["kind"] == "satellite" for node in nodes),
            "ground_stations": sum(
                node["kind"] == "ground-station" for node in nodes
            ),
            "orbits": len(orbits),
            "zones": len(zones),
            "slices": len(slice_records),
            "differences": len(slice_records) - 1,
            "paths": sum(len(item["paths"]) for item in slice_records),
        },
        "behavior": behavior,
        "display_notes": {
            "projection": (
                "The rotatable globe is a non-authoritative display projection "
                "of formal geographic views."
            ),
            "connection_classification": (
                "same-orbit and cross-orbit are display classifications derived "
                "from public Topology ownership; ConnectionKind is unchanged."
            ),
            "slice_zero": (
                "Slice 0 has no timestamp, Position, Connection, assignment or "
                "Route state."
            ),
        },
    }


def write_dynamic_slice_tool(
    slicer: Slicer,
    directory: Path,
) -> dict[str, object]:
    """Write one dependency-free Dynamic Slice inspection view bundle."""

    data = collect_dynamic_slice_data(slicer)
    output = Path(directory)
    output.mkdir(parents=True, exist_ok=True)
    data_path = output / "dynamic-slice.json"
    page_path = output / "index.html"
    summary_path = output / "summary.txt"
    inventory_path = output / "inventory.json"
    checksum_path = output / "checksums.sha256"

    write_json(data_path, data)
    write_text(page_path, render_dynamic_slice_page(data))
    inventory = data["inventory"]
    behavior = data["behavior"]
    assert isinstance(inventory, dict)
    assert isinstance(behavior, dict)
    write_text(
        summary_path,
        "\n".join(
            (
                "status=PASS",
                "tool=dynamic-slice-inspection",
                "authority=non-authoritative-view",
                *(f"{key}={value}" for key, value in inventory.items()),
                *(
                    f"behavior_{key}={value}"
                    for key, value in behavior.items()
                ),
                "",
            )
        ),
    )
    payload_files = (page_path, data_path, summary_path)
    bundle_inventory = {
        "format_version": 1,
        "files": [
            _file_record(path, output)
            for path in payload_files
        ],
    }
    write_json(inventory_path, bundle_inventory)
    checksum_files = (*payload_files, inventory_path)
    write_text(
        checksum_path,
        "".join(
            f"{_sha256(path)}  {path.relative_to(output).as_posix()}\n"
            for path in checksum_files
        ),
    )
    return {
        "status": "PASS",
        "message": (
            f"{inventory['slices']} Slices / "
            f"{inventory['differences']} Differences / "
            f"{inventory['paths']} resolved paths"
        ),
        "directory": output,
        "files": tuple(
            path.name for path in (*checksum_files, checksum_path)
        ),
        "inventory": inventory,
        "behavior": behavior,
    }


def _collect_static_nodes(
    slicer: Slicer,
) -> tuple[
    tuple[dict[str, object], ...],
    tuple[dict[str, object], ...],
    dict[NodeId, tuple[int, str, str]],
]:
    topology = slicer.get_topology()
    nodes = []
    orbits = []
    satellite_parents = {}
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
                    satellite_parents[satellite.node_id] = (
                        autonomous_system.asn,
                        shell.shell_id,
                        orbit.orbit_id,
                    )
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
        satellite_parents,
    )


def _collect_zones(slicer: Slicer) -> tuple[dict[str, object], ...]:
    try:
        zone_layout = slicer.get_topology().get_zone_layout()
    except RuntimeError:
        return ()
    return tuple(
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


def _adjacent_orbit_sides(
    orbits: tuple[dict[str, object], ...],
) -> dict[
    tuple[tuple[int, str, str], tuple[int, str, str]],
    str,
]:
    """Classify display-only normalized-RAAN adjacency for each endpoint."""

    grouped: dict[tuple[int, str], list[dict[str, object]]] = {}
    for orbit in orbits:
        grouped.setdefault(
            (int(orbit["asn"]), str(orbit["shell_id"])),
            [],
        ).append(orbit)

    sides = {}
    for shell_orbits in grouped.values():
        ordered = sorted(
            shell_orbits,
            key=lambda orbit: (
                float(orbit["right_ascension_deg"]),
                str(orbit["orbit_id"]),
            ),
        )
        if len(ordered) <= 1:
            continue
        for index, orbit in enumerate(ordered):
            identity = (
                int(orbit["asn"]),
                str(orbit["shell_id"]),
                str(orbit["orbit_id"]),
            )
            predecessor = ordered[(index - 1) % len(ordered)]
            successor = ordered[(index + 1) % len(ordered)]
            predecessor_identity = (
                int(predecessor["asn"]),
                str(predecessor["shell_id"]),
                str(predecessor["orbit_id"]),
            )
            successor_identity = (
                int(successor["asn"]),
                str(successor["shell_id"]),
                str(successor["orbit_id"]),
            )
            if predecessor_identity == successor_identity:
                sides[(identity, predecessor_identity)] = (
                    "predecessor-and-successor-orbit"
                )
            else:
                sides[(identity, predecessor_identity)] = "predecessor-orbit"
                sides[(identity, successor_identity)] = "successor-orbit"
    return sides


def _zone_assignment_data(
    zone_id: str,
    satellite_id: NodeId,
    positions: Any,
    zones_by_id: dict[str, Any],
) -> dict[str, object]:
    """Add non-authoritative explanation to one formal assignment."""

    zone = zones_by_id[zone_id]
    satellite_position = positions[satellite_id]
    representative_position = geodetic_to_ecef(
        zone.representative_point[0],
        zone.representative_point[1],
        0.0,
    )
    return {
        "zone_id": zone_id,
        "service_satellite_id": satellite_id.value,
        "display_explanation": {
            "representative_point_elevation_deg": elevation_deg(
                representative_position,
                satellite_position,
            ),
            "subpoint_inside_zone": zone.contains(
                satellite_position.latitude_deg,
                satellite_position.longitude_deg,
            ),
            "eligibility_basis": (
                "formal assignment uses representative-point visibility and "
                "minimum elevation; subpoint containment is not eligibility"
            ),
        },
    }


def _connection_data(
    connection: Connection,
    satellite_parents: dict[NodeId, tuple[int, str, str]],
    adjacent_orbit_sides: dict[
        tuple[tuple[int, str, str], tuple[int, str, str]],
        str,
    ],
) -> dict[str, object]:
    identity = connection.connection_id
    display_kind = identity.kind.value
    cross_plane_sides = None
    if identity.kind is ConnectionKind.INTER_SATELLITE:
        display_kind = (
            "same-orbit-isl"
            if satellite_parents[identity.endpoint_a]
            == satellite_parents[identity.endpoint_b]
            else "cross-orbit-isl"
        )
        if display_kind == "cross-orbit-isl":
            first_parent = satellite_parents[identity.endpoint_a]
            second_parent = satellite_parents[identity.endpoint_b]
            cross_plane_sides = {
                "endpoint_a": adjacent_orbit_sides.get(
                    (first_parent, second_parent),
                    "non-adjacent-orbit",
                ),
                "endpoint_b": adjacent_orbit_sides.get(
                    (second_parent, first_parent),
                    "non-adjacent-orbit",
                ),
            }
    return {
        "connection_id": _connection_id_data(identity),
        "connection_ref": _connection_reference(identity),
        "display_kind": display_kind,
        "cross_plane_sides": cross_plane_sides,
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


def _route_data(
    route_key: RouteKey,
    next_hop: NodeId,
) -> dict[str, object]:
    return {
        "route_ref": _route_reference(route_key),
        "source": route_key.source.value,
        "target": _target_data(route_key.target),
        "next_hop": next_hop.value,
    }


def _path_data(
    slicer: Slicer,
    slice_id: int,
    source: NodeId,
    target: RouteTarget,
) -> dict[str, object]:
    route_key = RouteKey(source, target)
    path = slicer.resolve_path(slice_id, source, target)
    return {
        "path_id": _route_reference(route_key),
        "source": source.value,
        "target": _target_data(target),
        "nodes": [node_id.value for node_id in path],
        "hop_count": len(path) - 1,
    }


def _target_data(target: RouteTarget) -> dict[str, str]:
    if isinstance(target, GroundStationRouteTarget):
        return {
            "kind": "G",
            "ground_station_id": target.ground_station_id.value,
        }
    if isinstance(target, ZoneRouteTarget):
        return {"kind": "Z", "zone_id": target.zone_id}
    raise TypeError("unsupported RouteTarget")


def _difference_data(
    difference: TopologyDifference,
    satellite_parents: dict[NodeId, tuple[int, str, str]],
    adjacent_orbit_sides: dict[
        tuple[tuple[int, str, str], tuple[int, str, str]],
        str,
    ],
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
            _connection_data(
                connection,
                satellite_parents,
                adjacent_orbit_sides,
            )
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
                "connection_ref": _connection_reference(patch.connection_id),
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
            {
                "route_ref": _route_reference(patch.route_key),
                "source": patch.route_key.source.value,
                "target": _target_data(patch.route_key.target),
                "operation": patch.operation.value,
                "next_hop": (
                    None if patch.next_hop is None else patch.next_hop.value
                ),
            }
            for patch in difference.route_patches
        ],
    }


def _behavior_inventory(
    slices: list[dict[str, object]],
) -> dict[str, int]:
    later_differences = [
        item["difference_from_previous"]
        for item in slices[2:]
    ]
    differences = [
        value for value in later_differences if isinstance(value, dict)
    ]
    return {
        "later_nonempty_differences": sum(
            not bool(value["is_empty"]) for value in differences
        ),
        "connection_additions": sum(
            len(value["added_connections"]) for value in differences
        ),
        "connection_removals": sum(
            len(value["removed_connection_ids"]) for value in differences
        ),
        "directional_parameter_patches": sum(
            len(value["connection_parameter_patches"])
            for value in differences
        ),
        "zone_assignment_additions": _operation_count(
            differences,
            "zone_service_assignment_patches",
            DifferenceOperation.ADDED,
        ),
        "zone_assignment_changes": _operation_count(
            differences,
            "zone_service_assignment_patches",
            DifferenceOperation.CHANGED,
        ),
        "zone_assignment_removals": _operation_count(
            differences,
            "zone_service_assignment_patches",
            DifferenceOperation.REMOVED,
        ),
        "route_additions": _operation_count(
            differences,
            "route_patches",
            DifferenceOperation.ADDED,
        ),
        "route_changes": _operation_count(
            differences,
            "route_patches",
            DifferenceOperation.CHANGED,
        ),
        "route_removals": _operation_count(
            differences,
            "route_patches",
            DifferenceOperation.REMOVED,
        ),
        "maximum_path_hops": max(
            (
                len(path["nodes"]) - 1
                for item in slices
                for path in item["paths"]
            ),
            default=0,
        ),
    }


def _operation_count(
    differences: list[dict[str, object]],
    field_name: str,
    operation: DifferenceOperation,
) -> int:
    return sum(
        patch["operation"] == operation.value
        for difference in differences
        for patch in difference[field_name]
    )


def _connection_reference(identity: ConnectionId) -> str:
    return "|".join(
        (
            identity.kind.value,
            identity.endpoint_a.value,
            identity.endpoint_b.value,
        )
    )


def _route_reference(route_key: RouteKey) -> str:
    target = route_key.target
    target_value = (
        f"G|{target.ground_station_id.value}"
        if isinstance(target, GroundStationRouteTarget)
        else f"Z|{target.zone_id}"
    )
    return f"{route_key.source.value}|{target_value}"


def _orbit_reference(asn: int, shell_id: str, orbit_id: str) -> str:
    return f"{asn}|{shell_id}|{orbit_id}"


def _orbit_guide_data(
    orbits: tuple[dict[str, object], ...],
    positions: tuple[dict[str, object], ...],
) -> tuple[dict[str, object], ...]:
    """Fit display-only Earth-fixed Orbit planes from formal ECEF positions."""

    positions_by_node = {
        str(position["node_id"]): position for position in positions
    }
    guides = []
    for orbit in orbits:
        vectors = tuple(
            _ecef_vector(positions_by_node[node_id])
            for node_id in orbit["satellite_ids"]
            if node_id in positions_by_node
        )
        fitted = _fit_origin_plane(vectors)
        if fitted is None:
            continue
        normal, basis_u, basis_v, mean_radius_m, residual = fitted
        guides.append(
            {
                "orbit_ref": orbit["orbit_ref"],
                "source": "formal-ecef-positions",
                "satellite_ids": tuple(orbit["satellite_ids"]),
                "sample_count": len(vectors),
                "plane_normal_ecef_unit": _vector_data(normal),
                "basis_u_ecef_unit": _vector_data(basis_u),
                "basis_v_ecef_unit": _vector_data(basis_v),
                "mean_radius_m": mean_radius_m,
                "max_unit_plane_residual": residual,
            }
        )
    return tuple(guides)


def _ecef_vector(position: dict[str, object]) -> tuple[float, float, float]:
    ecef = position["ecef_m"]
    assert isinstance(ecef, dict)
    return float(ecef["x"]), float(ecef["y"]), float(ecef["z"])


def _fit_origin_plane(
    vectors: tuple[tuple[float, float, float], ...],
) -> (
    tuple[
        tuple[float, float, float],
        tuple[float, float, float],
        tuple[float, float, float],
        float,
        float,
    ]
    | None
):
    """Return a deterministic least-squares plane through the Earth center."""

    if len(vectors) < 2:
        return None
    magnitudes = tuple(_magnitude(vector) for vector in vectors)
    if any(value <= 0.0 for value in magnitudes):
        return None
    unit_vectors = tuple(
        tuple(component / magnitude for component in vector)
        for vector, magnitude in zip(vectors, magnitudes, strict=True)
    )
    scatter = [
        [
            sum(vector[row] * vector[column] for vector in unit_vectors)
            for column in range(3)
        ]
        for row in range(3)
    ]
    eigenvalues, eigenvectors = _symmetric_eigendecomposition(scatter)
    normal = _canonical_unit(eigenvectors[min(range(3), key=eigenvalues.__getitem__)])
    if normal is None:
        return None
    reference_axis = min(
        ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
        key=lambda axis: abs(_dot(normal, axis)),
    )
    basis_u = _canonical_unit(_cross(normal, reference_axis))
    if basis_u is None:
        return None
    basis_v = _canonical_unit(_cross(normal, basis_u))
    if basis_v is None:
        return None
    residual = max(abs(_dot(normal, vector)) for vector in unit_vectors)
    return (
        normal,
        basis_u,
        basis_v,
        sum(magnitudes) / len(magnitudes),
        residual,
    )


def _symmetric_eigendecomposition(
    source: list[list[float]],
) -> tuple[list[float], list[tuple[float, float, float]]]:
    """Jacobi eigen decomposition for one real symmetric 3x3 matrix."""

    matrix = [row[:] for row in source]
    eigenvectors = [
        [1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
        [0.0, 0.0, 1.0],
    ]
    for _ in range(32):
        row, column = max(
            ((0, 1), (0, 2), (1, 2)),
            key=lambda item: abs(matrix[item[0]][item[1]]),
        )
        off_diagonal = matrix[row][column]
        if abs(off_diagonal) <= 1e-15:
            break
        angle = 0.5 * atan2(
            2.0 * off_diagonal,
            matrix[column][column] - matrix[row][row],
        )
        cosine = cos(angle)
        sine = sin(angle)
        diagonal_row = matrix[row][row]
        diagonal_column = matrix[column][column]
        matrix[row][row] = (
            cosine * cosine * diagonal_row
            - 2.0 * sine * cosine * off_diagonal
            + sine * sine * diagonal_column
        )
        matrix[column][column] = (
            sine * sine * diagonal_row
            + 2.0 * sine * cosine * off_diagonal
            + cosine * cosine * diagonal_column
        )
        matrix[row][column] = matrix[column][row] = 0.0
        for other in range(3):
            if other in (row, column):
                continue
            old_row = matrix[other][row]
            old_column = matrix[other][column]
            matrix[other][row] = matrix[row][other] = (
                cosine * old_row - sine * old_column
            )
            matrix[other][column] = matrix[column][other] = (
                sine * old_row + cosine * old_column
            )
        for other in range(3):
            old_row = eigenvectors[other][row]
            old_column = eigenvectors[other][column]
            eigenvectors[other][row] = cosine * old_row - sine * old_column
            eigenvectors[other][column] = sine * old_row + cosine * old_column
    return (
        [matrix[index][index] for index in range(3)],
        [
            tuple(eigenvectors[row][column] for row in range(3))
            for column in range(3)
        ],
    )


def _magnitude(vector: tuple[float, float, float]) -> float:
    return hypot(*vector)


def _dot(
    first: tuple[float, float, float],
    second: tuple[float, float, float],
) -> float:
    return sum(a * b for a, b in zip(first, second, strict=True))


def _cross(
    first: tuple[float, float, float],
    second: tuple[float, float, float],
) -> tuple[float, float, float]:
    return (
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    )


def _canonical_unit(
    vector: tuple[float, float, float],
) -> tuple[float, float, float] | None:
    magnitude = _magnitude(vector)
    if magnitude <= 1e-15:
        return None
    unit = tuple(component / magnitude for component in vector)
    dominant_index = max(range(3), key=lambda index: abs(unit[index]))
    if unit[dominant_index] < 0.0:
        unit = tuple(-component for component in unit)
    return unit


def _vector_data(
    vector: tuple[float, float, float],
) -> dict[str, float]:
    return {"x": vector[0], "y": vector[1], "z": vector[2]}


def _file_record(path: Path, root: Path) -> dict[str, object]:
    return {
        "path": path.relative_to(root).as_posix(),
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = ["collect_dynamic_slice_data", "write_dynamic_slice_tool"]
