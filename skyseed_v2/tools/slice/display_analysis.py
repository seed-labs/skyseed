"""Derive non-authoritative Slice / Difference display data."""

from __future__ import annotations

from math import acos, atan2, ceil, cos, hypot, radians, sin
from statistics import median

from skyseed_v2.simulator.geometry import (
    EarthFixedPosition,
    ecef_distance_m,
    elevation_deg,
    geodetic_to_ecef,
)
from tools._display_geometry import (
    EARTH_REFERENCE_RADIUS_M,
    WGS84_SEMI_MAJOR_AXIS_M,
)


def add_slice_display_analysis(data: dict[str, object]) -> None:
    """Attach all display classifications, guides, explanations, and counts."""

    topology = data["topology"]
    slices = data["slices"]
    assert isinstance(topology, dict)
    assert isinstance(slices, list)
    nodes = topology["nodes"]
    orbits = topology["orbits"]
    zones = topology["zones"]
    assert isinstance(nodes, tuple)
    assert isinstance(orbits, tuple)
    assert isinstance(zones, tuple)
    satellite_parents = {
        str(node["node_id"]): (
            int(node["asn"]),
            str(node["shell_id"]),
            str(node["orbit_id"]),
        )
        for node in nodes
        if node["kind"] == "satellite"
    }
    node_kinds = {
        str(node["node_id"]): str(node["kind"])
        for node in nodes
    }
    ground_station_count = sum(
        node["kind"] == "ground-station" for node in nodes
    )
    adjacent_orbit_sides = _adjacent_orbit_sides(orbits)
    for slice_record in slices:
        connections = slice_record["connections"]
        assignments = slice_record["zone_service_assignments"]
        positions = slice_record["positions"]
        assert isinstance(connections, tuple)
        assert isinstance(assignments, tuple)
        assert isinstance(positions, tuple)
        for connection in connections:
            _add_connection_display_data(
                connection,
                satellite_parents,
                adjacent_orbit_sides,
            )
        for assignment in assignments:
            _add_zone_assignment_display_data(assignment)
        difference = slice_record["difference_from_previous"]
        if isinstance(difference, dict):
            added = difference["added_connections"]
            assert isinstance(added, list)
            for connection in added:
                _add_connection_display_data(
                    connection,
                    satellite_parents,
                    adjacent_orbit_sides,
                )
        slice_record["orbit_guides"] = _orbit_guide_data(
            orbits,
            positions,
        )
        connected_nodes = {
            str(endpoint)
            for connection in connections
            for endpoint in (
                connection["connection_id"]["endpoint_a"],
                connection["connection_id"]["endpoint_b"],
            )
        }
        slice_record["diagnostics"] = tuple(
            {
                "kind": "isolated-satellite",
                "node_id": node["node_id"],
                "message": "Satellite has no current Connection",
            }
            for node in nodes
            if node["kind"] == "satellite"
            and node["node_id"] not in connected_nodes
            and slice_record["slice_id"] != 0
        )
        slice_record["summary"] = _slice_summary(
            slice_record,
            node_kinds=node_kinds,
            total_zones=len(zones),
            total_ground_stations=ground_station_count,
        )

    data["inventory"] = {
        "nodes": len(nodes),
        "satellites": sum(
            node["kind"] == "satellite" for node in nodes
        ),
        "ground_stations": sum(
            node["kind"] == "ground-station" for node in nodes
        ),
        "orbits": len(orbits),
        "zones": len(zones),
        "slices": len(slices),
        "differences": len(slices) - 1,
        "route_entries": sum(len(item["routes"]) for item in slices),
    }
    data["behavior"] = _behavior_inventory(slices)
    data["model_context"] = _model_context(
        data["model_context"],
        nodes,
        orbits,
        zones,
    )
    data["slice_manifest"] = [
        {
            "slice_id": item["slice_id"],
            "timestamp": item["timestamp"],
            "summary": item["summary"],
            "chunk": f"chunks/slice-{int(item['slice_id']):06d}.js",
        }
        for item in slices
    ]
    data["display_notes"] = {
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
    }


def _add_connection_display_data(
    connection: dict[str, object],
    satellite_parents: dict[str, tuple[int, str, str]],
    adjacent_orbit_sides: dict[
        tuple[tuple[int, str, str], tuple[int, str, str]],
        str,
    ],
) -> None:
    identity = connection["connection_id"]
    assert isinstance(identity, dict)
    kind = str(identity["kind"])
    display_kind = kind
    cross_plane_sides = None
    if kind == "inter-satellite":
        endpoint_a = str(identity["endpoint_a"])
        endpoint_b = str(identity["endpoint_b"])
        first_parent = satellite_parents[endpoint_a]
        second_parent = satellite_parents[endpoint_b]
        display_kind = (
            "same-orbit-isl"
            if first_parent == second_parent
            else "cross-orbit-isl"
        )
        if display_kind == "cross-orbit-isl":
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
    connection["display_kind"] = display_kind
    connection["cross_plane_sides"] = cross_plane_sides


def _add_zone_assignment_display_data(
    assignment: dict[str, object],
) -> None:
    inputs = assignment.pop("_display_input")
    assert isinstance(inputs, dict)
    representative_point = inputs["representative_point"]
    satellite_ecef = inputs["satellite_ecef_m"]
    assert isinstance(representative_point, list)
    assert isinstance(satellite_ecef, list)
    representative_position = geodetic_to_ecef(
        float(representative_point[0]),
        float(representative_point[1]),
        0.0,
    )
    satellite_position = EarthFixedPosition(
        float(satellite_ecef[0]),
        float(satellite_ecef[1]),
        float(satellite_ecef[2]),
    )
    assignment["display_explanation"] = {
        "representative_point_elevation_deg": elevation_deg(
            representative_position,
            satellite_position,
        ),
        "surface_separation_km": _surface_separation_km(
            float(representative_point[0]),
            float(representative_point[1]),
            satellite_position.latitude_deg,
            satellite_position.longitude_deg,
        ),
        "slant_distance_km": (
            ecef_distance_m(
                representative_position,
                satellite_position,
            )
            / 1_000.0
        ),
        "surface_separation_authority": (
            "Tool-derived IUGG mean-Earth spherical approximation"
        ),
        "slant_distance_authority": (
            "derived from formal representative-point and Satellite ECEF"
        ),
        "subpoint_inside_zone": bool(inputs["subpoint_inside_zone"]),
        "eligibility_basis": (
            "formal assignment uses representative-point visibility and "
            "minimum elevation; subpoint containment is not eligibility"
        ),
    }


def _surface_separation_km(
    first_latitude_deg: float,
    first_longitude_deg: float,
    second_latitude_deg: float,
    second_longitude_deg: float,
) -> float:
    first_latitude = radians(first_latitude_deg)
    second_latitude = radians(second_latitude_deg)
    delta_latitude = second_latitude - first_latitude
    delta_longitude = radians(
        second_longitude_deg - first_longitude_deg
    )
    haversine = sin(delta_latitude / 2.0) ** 2 + (
        cos(first_latitude)
        * cos(second_latitude)
        * sin(delta_longitude / 2.0) ** 2
    )
    bounded_haversine = min(1.0, max(0.0, haversine))
    central_angle = 2.0 * atan2(
        bounded_haversine ** 0.5,
        (1.0 - bounded_haversine) ** 0.5,
    )
    return EARTH_REFERENCE_RADIUS_M * central_angle / 1_000.0


def _adjacent_orbit_sides(
    orbits: tuple[dict[str, object], ...],
) -> dict[
    tuple[tuple[int, str, str], tuple[int, str, str]],
    str,
]:
    """Classify display-only normalized-RAAN adjacency per endpoint."""

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
            identity = _orbit_identity(orbit)
            predecessor = _orbit_identity(
                ordered[(index - 1) % len(ordered)]
            )
            successor = _orbit_identity(
                ordered[(index + 1) % len(ordered)]
            )
            if predecessor == successor:
                sides[(identity, predecessor)] = (
                    "predecessor-and-successor-orbit"
                )
            else:
                sides[(identity, predecessor)] = "predecessor-orbit"
                sides[(identity, successor)] = "successor-orbit"
    return sides


def _orbit_identity(
    orbit: dict[str, object],
) -> tuple[int, str, str]:
    return (
        int(orbit["asn"]),
        str(orbit["shell_id"]),
        str(orbit["orbit_id"]),
    )


def _orbit_guide_data(
    orbits: tuple[dict[str, object], ...],
    positions: tuple[dict[str, object], ...],
) -> tuple[dict[str, object], ...]:
    """Fit display-only Earth-fixed planes from formal ECEF snapshots."""

    positions_by_node = {
        str(position["node_id"]): position for position in positions
    }
    guides = []
    for orbit in orbits:
        satellite_ids = orbit["satellite_ids"]
        assert isinstance(satellite_ids, list)
        vectors = tuple(
            _ecef_vector(positions_by_node[node_id])
            for node_id in satellite_ids
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
                "satellite_ids": tuple(satellite_ids),
                "sample_count": len(vectors),
                "plane_normal_ecef_unit": _vector_data(normal),
                "basis_u_ecef_unit": _vector_data(basis_u),
                "basis_v_ecef_unit": _vector_data(basis_v),
                "mean_radius_m": mean_radius_m,
                "max_unit_plane_residual": residual,
            }
        )
    return tuple(guides)


def _ecef_vector(
    position: dict[str, object],
) -> tuple[float, float, float]:
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
    """Return a deterministic least-squares plane through Earth center."""

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
    normal = _canonical_unit(
        eigenvectors[min(range(3), key=eigenvalues.__getitem__)]
    )
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
    residual = max(
        abs(_dot(normal, vector)) for vector in unit_vectors
    )
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
            eigenvectors[other][row] = (
                cosine * old_row - sine * old_column
            )
            eigenvectors[other][column] = (
                sine * old_row + cosine * old_column
            )
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
    dominant_index = max(
        range(3),
        key=lambda index: abs(unit[index]),
    )
    if unit[dominant_index] < 0.0:
        unit = tuple(-component for component in unit)
    return unit


def _vector_data(
    vector: tuple[float, float, float],
) -> dict[str, float]:
    return {"x": vector[0], "y": vector[1], "z": vector[2]}


def _behavior_inventory(
    slices: list[dict[str, object]],
) -> dict[str, int]:
    differences = [
        item["difference_from_previous"] for item in slices[2:]
    ]
    values = [
        value for value in differences if isinstance(value, dict)
    ]
    return {
        "later_nonempty_differences": sum(
            not bool(value["is_empty"]) for value in values
        ),
        "connection_additions": sum(
            len(value["added_connections"]) for value in values
        ),
        "connection_removals": sum(
            len(value["removed_connection_ids"]) for value in values
        ),
        "directional_parameter_patches": sum(
            len(value["connection_parameter_patches"])
            for value in values
        ),
        "zone_assignment_additions": _operation_count(
            values,
            "zone_service_assignment_patches",
            "added",
        ),
        "zone_assignment_changes": _operation_count(
            values,
            "zone_service_assignment_patches",
            "changed",
        ),
        "zone_assignment_removals": _operation_count(
            values,
            "zone_service_assignment_patches",
            "removed",
        ),
        "route_additions": _operation_count(
            values,
            "route_patches",
            "added",
        ),
        "route_changes": _operation_count(
            values,
            "route_patches",
            "changed",
        ),
        "route_removals": _operation_count(
            values,
            "route_patches",
            "removed",
        ),
        "route_patches": sum(
            len(value["route_patches"]) for value in values
        ),
    }


def _operation_count(
    differences: list[dict[str, object]],
    field_name: str,
    operation: str,
) -> int:
    return sum(
        (
            patch[0] == operation
            if isinstance(patch, tuple)
            else patch["operation"] == operation
        )
        for difference in differences
        for patch in difference[field_name]
    )


def _slice_summary(
    slice_record: dict[str, object],
    *,
    node_kinds: dict[str, str],
    total_zones: int,
    total_ground_stations: int,
) -> dict[str, object]:
    connections = slice_record["connections"]
    assignments = slice_record["zone_service_assignments"]
    assert isinstance(connections, tuple)
    assert isinstance(assignments, tuple)
    state_available = slice_record["slice_id"] != 0
    connection_counts = {
        "same_orbit_isl": 0,
        "cross_plane_isl": 0,
        "ground_station_access": 0,
    }
    covered_ground_stations: set[str] = set()
    for connection in connections:
        display_kind = connection["display_kind"]
        if display_kind == "same-orbit-isl":
            connection_counts["same_orbit_isl"] += 1
        elif display_kind == "cross-orbit-isl":
            connection_counts["cross_plane_isl"] += 1
        elif display_kind == "ground-station-access":
            connection_counts["ground_station_access"] += 1
            identity = connection["connection_id"]
            assert isinstance(identity, dict)
            for endpoint in (
                str(identity["endpoint_a"]),
                str(identity["endpoint_b"]),
            ):
                if node_kinds.get(endpoint) == "ground-station":
                    covered_ground_stations.add(endpoint)

    explanations = [
        assignment["display_explanation"] for assignment in assignments
    ]
    elevation_statistics = _numeric_statistics(
        [
            float(value["representative_point_elevation_deg"])
            for value in explanations
        ],
        ("p5", 0.05),
        ("median", 0.5),
    )
    surface_statistics = _numeric_statistics(
        [float(value["surface_separation_km"]) for value in explanations],
        ("median", 0.5),
        ("p95", 0.95),
        ("max", 1.0),
    )
    slant_statistics = _numeric_statistics(
        [float(value["slant_distance_km"]) for value in explanations],
        ("median", 0.5),
        ("p95", 0.95),
        ("max", 1.0),
    )
    difference = slice_record["difference_from_previous"]
    difference_counts = None
    if isinstance(difference, dict):
        difference_counts = {
            "connection_additions": len(difference["added_connections"]),
            "connection_removals": len(difference["removed_connection_ids"]),
            "connection_parameter_patches": len(
                difference["connection_parameter_patches"]
            ),
            "zone_patches": len(
                difference["zone_service_assignment_patches"]
            ),
            "route_patches": len(difference["route_patches"]),
        }
    return {
        "state_available": state_available,
        "positions": len(slice_record["positions"]),
        "connections": len(slice_record["connections"]),
        "served_zones": len(slice_record["zone_service_assignments"]),
        "unserved_zones": (
            total_zones - len(slice_record["zone_service_assignments"])
        ),
        "total_zones": total_zones,
        "distinct_service_satellites": len(
            {
                assignment["service_satellite_id"]
                for assignment in assignments
            }
        ),
        "covered_ground_stations": len(covered_ground_stations),
        "total_ground_stations": total_ground_stations,
        "connection_counts": connection_counts,
        "route_entries": len(slice_record["routes"]),
        "zone_service_geometry": {
            "elevation_deg": elevation_statistics,
            "surface_separation_km": surface_statistics,
            "slant_distance_km": slant_statistics,
        },
        "diagnostics": len(slice_record["diagnostics"]),
        "difference": difference_counts,
    }


def _numeric_statistics(
    values: list[float],
    *requested: tuple[str, float],
) -> dict[str, float | None]:
    if not values:
        return {label: None for label, _ in requested}
    ordered = sorted(values)
    result: dict[str, float | None] = {}
    for label, percentile in requested:
        if percentile == 0.5:
            result[label] = median(ordered)
            continue
        index = max(0, min(len(ordered) - 1, ceil(percentile * len(ordered)) - 1))
        result[label] = ordered[index]
    return result


def _model_context(
    raw_context: object,
    nodes: tuple[dict[str, object], ...],
    orbits: tuple[dict[str, object], ...],
    zones: tuple[dict[str, object], ...],
) -> dict[str, object]:
    """Combine supplied policy facts with Tool-derived topology context."""

    if not isinstance(raw_context, dict):
        raise TypeError("model_context must be a dictionary after reading")
    context = dict(raw_context)
    grouped_orbits: dict[tuple[int, str], list[dict[str, object]]] = {}
    for orbit in orbits:
        grouped_orbits.setdefault(
            (int(orbit["asn"]), str(orbit["shell_id"])),
            [],
        ).append(orbit)
    shells = []
    for (asn, shell_id), shell_orbits in sorted(grouped_orbits.items()):
        satellite_counts = {
            len(orbit["satellite_ids"]) for orbit in shell_orbits
        }
        altitudes = {float(orbit["altitude_km"]) for orbit in shell_orbits}
        inclinations = {
            float(orbit["inclination_deg"]) for orbit in shell_orbits
        }
        shells.append(
            {
                "asn": asn,
                "shell_id": shell_id,
                "satellite_count": sum(
                    len(orbit["satellite_ids"]) for orbit in shell_orbits
                ),
                "orbit_count": len(shell_orbits),
                "satellites_per_orbit": (
                    next(iter(satellite_counts))
                    if len(satellite_counts) == 1
                    else None
                ),
                "altitude_km": (
                    next(iter(altitudes)) if len(altitudes) == 1 else None
                ),
                "inclination_deg": (
                    next(iter(inclinations))
                    if len(inclinations) == 1
                    else None
                ),
            }
        )
    context["satellite_shells"] = shells
    context["zone_count"] = len(zones)

    satellite_capacities = {
        int(node["max_simultaneous_ground_station_accesses"])
        for node in nodes
        if node["kind"] == "satellite"
        and node["max_simultaneous_ground_station_accesses"] is not None
    }
    access_context = context.get("ground_station_access")
    if isinstance(access_context, dict) and len(satellite_capacities) == 1:
        access_context = dict(access_context)
        access_context["satellite_capacity"] = next(
            iter(satellite_capacities)
        )
        context["ground_station_access"] = access_context

    zone_context = context.get("zone_service")
    if isinstance(zone_context, dict) and len(shells) == 1:
        altitude_km = shells[0]["altitude_km"]
        minimum_elevation_deg = zone_context.get("minimum_elevation_deg")
        if altitude_km is not None and minimum_elevation_deg is not None:
            raw_radius_km = _service_envelope_surface_radius_km(
                float(altitude_km),
                float(minimum_elevation_deg),
            )
            zone_context = dict(zone_context)
            zone_context.update(
                {
                    "approx_service_envelope_surface_radius_km": (
                        ceil(raw_radius_km / 10.0) * 10.0
                    ),
                    "unrounded_service_envelope_surface_radius_km": (
                        raw_radius_km
                    ),
                    "service_envelope_authority": (
                        "Tool-derived spherical/WGS84 explanatory geometry; "
                        "formal ECEF elevation and LOS remain authoritative"
                    ),
                }
            )
            context["zone_service"] = zone_context
    context["authority"] = {
        "projected": "scenario/model facts supplied at artifact generation",
        "derived": "Tool-private explanatory geometry and counts",
    }
    return context


def _service_envelope_surface_radius_km(
    altitude_km: float,
    minimum_elevation_deg: float,
) -> float:
    elevation = radians(minimum_elevation_deg)
    orbital_radius_m = WGS84_SEMI_MAJOR_AXIS_M + altitude_km * 1_000.0
    central_angle = acos(
        EARTH_REFERENCE_RADIUS_M / orbital_radius_m * cos(elevation)
    ) - elevation
    return EARTH_REFERENCE_RADIUS_M * central_angle / 1_000.0


__all__ = ["add_slice_display_analysis"]
