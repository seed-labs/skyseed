"""Record example-local evidence for one bounded dynamic S4 run."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from hashlib import sha256
import json
from math import ceil
from pathlib import Path
import resource
from statistics import median
from time import perf_counter
from typing import Iterable, Mapping

from skyseed_v2.shared import NodeId
from skyseed_v2.simulator import (
    Connection,
    ConnectionKind,
    DifferenceOperation,
    GroundStationRouteTarget,
    Simulator,
    Slicer,
    Topology,
    TopologyDifference,
    TopologySlice,
    ZoneRouteTarget,
)
from skyseed_v2.simulator.geometry import ecef_distance_m
from tools.slice import write_slice_difference_tool

from .simu import (
    BANDWIDTH_BPS_BY_KIND,
    DYNAMIC_REAL_SLICE_COUNT,
    DYNAMIC_SLICE_INTERVAL,
    GATEWAY_CANDIDATE_COUNT,
    GATEWAY_CANDIDATE_MAX_DISTANCE_M,
    GROUND_STATION_MAXIMUM_DISTANCE_M,
    GROUND_STATION_MINIMUM_ELEVATION_DEG,
    MAXIMUM_GROUND_STATION_ACCESS_COUNT,
    MAXIMUM_ISL_DISTANCE_M,
    PACKET_LOSS_PPM_BY_KIND,
    PROPAGATION_SPEED_M_PER_S,
    REFERENCE_EPOCH,
    ZONE_COVERAGE_MINIMUM_ELEVATION_DEG,
)


FIRST_SANITY_SLICE_ID = 2
TEN_MINUTE_SLICE_ID = 61
FINAL_SLICE_ID = DYNAMIC_REAL_SLICE_COUNT
HOP_CHECKPOINT_SLICE_IDS = frozenset({1, TEN_MINUTE_SLICE_ID, FINAL_SLICE_ID})


def run_s4_dynamic_qualification(
    simulator: Simulator,
    output_directory: Path,
    *,
    static_construction_seconds: float,
) -> dict[str, object]:
    """Advance the existing Slicer and write compact qualification evidence."""

    if not isinstance(simulator, Simulator):
        raise TypeError("simulator must be a Simulator")
    if static_construction_seconds < 0.0:
        raise ValueError("static_construction_seconds must be non-negative")

    output = Path(output_directory)
    evidence_directory = output / "qualification"
    evidence_directory.mkdir(parents=True, exist_ok=True)
    slice_metrics_path = evidence_directory / "slice-metrics.jsonl"
    difference_metrics_path = evidence_directory / "difference-metrics.jsonl"
    run_path = evidence_directory / "run.json"
    checksums_path = evidence_directory / "checksums.sha256"
    for path in (slice_metrics_path, difference_metrics_path):
        path.write_text("", encoding="utf-8")

    slicer = simulator.get_slicer()
    topology = simulator.get_topology()
    timeline = simulator.get_timeline()
    topology_identity = id(topology)
    context = _topology_context(topology)
    started = perf_counter()
    slice_records: list[dict[str, object]] = []
    difference_records: list[dict[str, object]] = []
    checkpoints: dict[str, dict[str, object]] = {}
    slice_tool_record: dict[str, object] | None = None

    try:
        while not slicer.is_exhausted():
            slice_started = perf_counter()
            topology_slice = slicer.create_next_slice()
            slice_generation_seconds = perf_counter() - slice_started
            _verify_slice(
                topology_slice,
                topology,
                topology_identity,
                slicer.get_topology(),
                context,
            )

            difference_started = perf_counter()
            difference = slicer.create_difference(
                topology_slice.slice_id - 1,
                topology_slice.slice_id,
            )
            difference_seconds = perf_counter() - difference_started

            analysis_started = perf_counter()
            slice_record = _slice_metrics(topology_slice, context)
            if topology_slice.slice_id in HOP_CHECKPOINT_SLICE_IDS:
                slice_record["route_hop_count_distribution"] = (
                    _route_hop_count_distribution(slicer, topology_slice)
                )
            memory = _memory_observation()
            slice_record.update(
                {
                    "slice_generation_seconds": slice_generation_seconds,
                    "retained_slice_count_including_zero": len(
                        slicer.get_retained_slice_ids()
                    ),
                    "process_rss_kib": memory["process_rss_kib"],
                    "process_peak_rss_kib": memory["process_peak_rss_kib"],
                    "host_memory_available_kib": memory[
                        "host_memory_available_kib"
                    ],
                }
            )
            difference_record = _difference_metrics(
                difference,
                slicer.get_slice(topology_slice.slice_id - 1),
                topology_slice,
                context,
            )
            difference_record["difference_generation_seconds"] = (
                difference_seconds
            )
            slice_record["evidence_analysis_seconds"] = (
                perf_counter() - analysis_started
            )
            slice_records.append(slice_record)
            difference_records.append(difference_record)
            _append_jsonl(slice_metrics_path, slice_record)
            _append_jsonl(difference_metrics_path, difference_record)

            if topology_slice.slice_id == FIRST_SANITY_SLICE_ID:
                tool_started = perf_counter()
                tool_result = write_slice_difference_tool(
                    slicer,
                    output / "inspection/slice",
                    model_context=_slice_tool_context(),
                )
                slice_tool_record = _tool_record(
                    tool_result,
                    output / "inspection/slice",
                    perf_counter() - tool_started,
                    topology_slice.slice_id,
                )
                checkpoints["first_dynamic_sanity_window"] = (
                    _checkpoint_record(topology_slice, started, memory)
                )
            if topology_slice.slice_id == TEN_MINUTE_SLICE_ID:
                checkpoints["ten_simulated_minutes"] = _checkpoint_record(
                    topology_slice, started, memory
                )
            if topology_slice.slice_id == FINAL_SLICE_ID:
                checkpoints["thirty_simulated_minutes"] = _checkpoint_record(
                    topology_slice, started, memory
                )

            print(
                "S4 dynamic "
                f"Slice {topology_slice.slice_id}/{DYNAMIC_REAL_SLICE_COUNT}: "
                f"{slice_generation_seconds:.3f}s Slice, "
                f"{difference_seconds:.3f}s Difference, "
                f"{slice_record['connections']['total']} Connections, "
                f"{slice_record['routes']['entries']} Routes, "
                f"RSS {memory['process_rss_kib'] / 1024:.1f} MiB",
                flush=True,
            )
            _raise_if_memory_exhaustion_is_imminent(memory)
            del difference

        if len(slice_records) != DYNAMIC_REAL_SLICE_COUNT:
            raise RuntimeError(
                "dynamic S4 run did not materialize exactly "
                f"{DYNAMIC_REAL_SLICE_COUNT} real Slices"
            )
        if not slicer.is_exhausted():
            raise RuntimeError("dynamic S4 Timeline is not exhausted")
    except BaseException as error:
        blocked = _blocked_record(
            error,
            simulator,
            static_construction_seconds,
            started,
            slice_records,
            difference_records,
            checkpoints,
            slice_tool_record,
        )
        _write_json(run_path, blocked)
        _write_checksums(
            checksums_path,
            (run_path, slice_metrics_path, difference_metrics_path),
            evidence_directory,
        )
        raise

    completed = _completed_record(
        simulator,
        static_construction_seconds,
        started,
        slice_records,
        difference_records,
        checkpoints,
        slice_tool_record,
        output,
    )
    _write_json(run_path, completed)
    _write_checksums(
        checksums_path,
        (run_path, slice_metrics_path, difference_metrics_path),
        evidence_directory,
    )
    return completed


def _topology_context(topology: Topology) -> dict[str, object]:
    satellites: list[NodeId] = []
    ground_stations: list[NodeId] = []
    orbit_by_satellite: dict[NodeId, str] = {}
    orbit_index_by_satellite: dict[NodeId, int] = {}
    expected_adjacent_pairs: set[str] = set()
    for autonomous_system in topology.autonomous_systems:
        ground_stations.extend(
            station.node_id for station in autonomous_system.ground_stations
        )
        for shell in autonomous_system.shells:
            orbit_ids = [orbit.orbit_id for orbit in shell.orbits]
            if len(orbit_ids) > 1:
                expected_adjacent_pairs.update(
                    _plane_pair(
                        orbit_ids[index],
                        orbit_ids[(index + 1) % len(orbit_ids)],
                    )
                    for index in range(len(orbit_ids))
                )
            for orbit in shell.orbits:
                for satellite in orbit.satellites:
                    satellites.append(satellite.node_id)
                    orbit_by_satellite[satellite.node_id] = orbit.orbit_id
                    orbit_index_by_satellite[satellite.node_id] = (
                        satellite.orbit_index
                    )
    return {
        "satellites": tuple(satellites),
        "satellite_set": frozenset(satellites),
        "ground_stations": tuple(ground_stations),
        "ground_station_set": frozenset(ground_stations),
        "known_nodes": frozenset((*satellites, *ground_stations)),
        "zone_ids": frozenset(
            zone.zone_id for zone in topology.get_zone_layout().zones
        ),
        "orbit_by_satellite": orbit_by_satellite,
        "orbit_index_by_satellite": orbit_index_by_satellite,
        "expected_adjacent_pairs": frozenset(expected_adjacent_pairs),
    }


def _verify_slice(
    topology_slice: TopologySlice,
    topology: Topology,
    topology_identity: int,
    current_topology: Topology,
    context: Mapping[str, object],
) -> None:
    if current_topology is not topology or id(current_topology) != topology_identity:
        raise RuntimeError("S4 static Topology identity changed during progression")
    expected_timestamp = REFERENCE_EPOCH + (
        topology_slice.slice_id - 1
    ) * DYNAMIC_SLICE_INTERVAL
    if topology_slice.timestamp != expected_timestamp:
        raise RuntimeError("S4 Slice timestamp does not match SimulationTimeline")
    known_nodes = context["known_nodes"]
    assert isinstance(known_nodes, frozenset)
    if frozenset(topology_slice.positions) != known_nodes:
        raise RuntimeError("S4 Position state is incomplete or contains unknown IDs")
    satellite_set = context["satellite_set"]
    ground_station_set = context["ground_station_set"]
    assert isinstance(satellite_set, frozenset)
    assert isinstance(ground_station_set, frozenset)
    if len(satellite_set) != 348 or len(ground_station_set) != 355:
        raise RuntimeError("S4 static node body changed during dynamic progression")
    for connection in topology_slice.connection_slice.connections:
        identifier = connection.connection_id
        if (
            identifier.endpoint_a not in known_nodes
            or identifier.endpoint_b not in known_nodes
        ):
            raise RuntimeError("S4 Connection references an unknown node")
    zone_ids = context["zone_ids"]
    assert isinstance(zone_ids, frozenset)
    if not set(
        topology_slice.connection_slice.zone_service_assignments
    ).issubset(zone_ids):
        raise RuntimeError("S4 service assignment references an unknown Zone")


def _slice_metrics(
    topology_slice: TopologySlice,
    context: Mapping[str, object],
) -> dict[str, object]:
    satellite_ids = context["satellites"]
    ground_station_ids = context["ground_stations"]
    orbit_by_satellite = context["orbit_by_satellite"]
    orbit_index_by_satellite = context["orbit_index_by_satellite"]
    expected_adjacent_pairs = context["expected_adjacent_pairs"]
    assert isinstance(satellite_ids, tuple)
    assert isinstance(ground_station_ids, tuple)
    assert isinstance(orbit_by_satellite, dict)
    assert isinstance(orbit_index_by_satellite, dict)
    assert isinstance(expected_adjacent_pairs, frozenset)

    total_degree = Counter({node_id: 0 for node_id in satellite_ids})
    total_degree.update({node_id: 0 for node_id in ground_station_ids})
    isl_degree = Counter({node_id: 0 for node_id in satellite_ids})
    satellite_gs_degree = Counter({node_id: 0 for node_id in satellite_ids})
    gs_degree = Counter({node_id: 0 for node_id in ground_station_ids})
    connection_counts = Counter()
    plane_pair_counts = Counter()
    phase_offset_counts = Counter()
    isl_distances: list[float] = []
    longest_inter_plane: list[dict[str, object]] = []

    for connection in topology_slice.connection_slice.connections:
        identifier = connection.connection_id
        first = identifier.endpoint_a
        second = identifier.endpoint_b
        total_degree[first] += 1
        total_degree[second] += 1
        if identifier.kind is ConnectionKind.GROUND_STATION_ACCESS:
            connection_counts["ground_station_access"] += 1
            ground_station = (
                first if first.entity_kind == "ground-station" else second
            )
            satellite = first if first.entity_kind == "satellite" else second
            gs_degree[ground_station] += 1
            satellite_gs_degree[satellite] += 1
            continue

        isl_degree[first] += 1
        isl_degree[second] += 1
        distance = ecef_distance_m(
            topology_slice.positions[first], topology_slice.positions[second]
        )
        isl_distances.append(distance)
        first_orbit = str(orbit_by_satellite[first])
        second_orbit = str(orbit_by_satellite[second])
        if first_orbit == second_orbit:
            connection_counts["intra_orbit_isl"] += 1
            continue
        connection_counts["inter_orbit_isl"] += 1
        pair = _plane_pair(first_orbit, second_orbit)
        plane_pair_counts[pair] += 1
        if first_orbit <= second_orbit:
            first_index = int(orbit_index_by_satellite[first])
            second_index = int(orbit_index_by_satellite[second])
        else:
            first_index = int(orbit_index_by_satellite[second])
            second_index = int(orbit_index_by_satellite[first])
        offset = (second_index - first_index) % 58
        phase_offset_counts[f"{pair}:offset-{offset:02d}"] += 1
        longest_inter_plane.append(
            {
                "endpoint_a": first.value,
                "endpoint_b": second.value,
                "plane_pair": pair,
                "orbit_index_offset_mod_58": offset,
                "distance_m": distance,
            }
        )

    assignments = topology_slice.connection_slice.zone_service_assignments
    zones_per_satellite = Counter({node_id: 0 for node_id in satellite_ids})
    zones_per_satellite.update(assignments.values())
    route_entries = sum(
        len(targets) for targets in topology_slice.route_slice.routes.values()
    )
    represented_targets = {
        _target_identity(target)
        for targets in topology_slice.route_slice.routes.values()
        for target in targets
    }
    longest_inter_plane.sort(
        key=lambda value: float(value["distance_m"]), reverse=True
    )

    return {
        "slice_id": topology_slice.slice_id,
        "timestamp": topology_slice.timestamp.isoformat(),
        "positions": {
            "total": len(topology_slice.positions),
            "satellites": sum(
                node_id.entity_kind == "satellite"
                for node_id in topology_slice.positions
            ),
            "ground_stations": sum(
                node_id.entity_kind == "ground-station"
                for node_id in topology_slice.positions
            ),
        },
        "connections": {
            "total": len(topology_slice.connection_slice.connections),
            "intra_orbit_isl": connection_counts["intra_orbit_isl"],
            "inter_orbit_isl": connection_counts["inter_orbit_isl"],
            "ground_station_access": connection_counts[
                "ground_station_access"
            ],
            "isl_distance_m": _numeric_statistics(isl_distances),
            "inter_orbit_plane_pair_counts": dict(
                sorted(plane_pair_counts.items())
            ),
            "expected_adjacent_plane_pairs_without_links": sorted(
                expected_adjacent_pairs - set(plane_pair_counts)
            ),
            "inter_orbit_index_offset_counts": dict(
                sorted(phase_offset_counts.items())
            ),
            "longest_inter_orbit_links": longest_inter_plane[:10],
        },
        "degrees": {
            "ground_station_access": _distribution(gs_degree.values()),
            "satellite_ground_station_access": _distribution(
                satellite_gs_degree.values()
            ),
            "satellite_total": _distribution(
                total_degree[node_id] for node_id in satellite_ids
            ),
            "satellite_isl": _distribution(
                isl_degree[node_id] for node_id in satellite_ids
            ),
            "isolated_satellites": sum(
                total_degree[node_id] == 0 for node_id in satellite_ids
            ),
        },
        "zones": {
            "served": len(assignments),
            "unserved": len(context["zone_ids"]) - len(assignments),
            "service_satellite_distribution": _distribution(
                zones_per_satellite.values()
            ),
        },
        "routes": {
            "entries": route_entries,
            "sources": len(topology_slice.route_slice.routes),
            "represented_targets": len(represented_targets),
            "demand_policy": (
                "fixed Zone/Gateway candidates; reachable directions only"
            ),
        },
    }


def _difference_metrics(
    difference: TopologyDifference,
    previous: TopologySlice,
    current: TopologySlice,
    context: Mapping[str, object],
) -> dict[str, object]:
    added_classes = Counter(
        _connection_class(connection, context)
        for connection in difference.added_connections
    )
    removed_classes = Counter(
        _connection_id_class(identifier, context)
        for identifier in difference.removed_connection_ids
    )
    parameter_classes = Counter(
        _connection_id_class(patch.connection_id, context)
        for patch in difference.connection_parameter_patches
    )
    zone_operations = Counter(
        patch.operation.value
        for patch in difference.zone_service_assignment_patches
    )
    route_operations = Counter(
        patch.operation.value for patch in difference.route_patches
    )
    route_target_operations = Counter(
        (
            "ground-station"
            if isinstance(patch.route_key.target, GroundStationRouteTarget)
            else "zone"
        )
        + ":"
        + patch.operation.value
        for patch in difference.route_patches
    )
    polar_added = sum(
        _connection_has_polar_satellite(connection, current)
        for connection in difference.added_connections
    )
    polar_removed = sum(
        _connection_id_has_polar_satellite(identifier, previous)
        for identifier in difference.removed_connection_ids
    )
    return {
        "previous_slice_id": difference.previous_slice_id,
        "current_slice_id": difference.current_slice_id,
        "current_timestamp": difference.current_timestamp.isoformat(),
        "connections": {
            "added": len(difference.added_connections),
            "removed": len(difference.removed_connection_ids),
            "directional_parameter_patches": len(
                difference.connection_parameter_patches
            ),
            "added_by_class": dict(sorted(added_classes.items())),
            "removed_by_class": dict(sorted(removed_classes.items())),
            "parameter_patches_by_class": dict(
                sorted(parameter_classes.items())
            ),
            "polar_observation_added": polar_added,
            "polar_observation_removed": polar_removed,
            "polar_observation_latitude_threshold_deg": 70.0,
        },
        "zones": {
            "patches": len(difference.zone_service_assignment_patches),
            "operations": dict(sorted(zone_operations.items())),
        },
        "routes": {
            "patches": len(difference.route_patches),
            "operations": dict(sorted(route_operations.items())),
            "target_operations": dict(sorted(route_target_operations.items())),
        },
        "is_empty": difference.is_empty,
    }


def _route_hop_count_distribution(
    slicer: Slicer,
    topology_slice: TopologySlice,
) -> dict[str, int]:
    hop_counts = Counter()
    for source, targets in topology_slice.route_slice.routes.items():
        for target in targets:
            path = slicer.resolve_path(topology_slice.slice_id, source, target)
            hop_counts[len(path) - 1] += 1
    return {str(key): hop_counts[key] for key in sorted(hop_counts)}


def _connection_class(
    connection: Connection, context: Mapping[str, object]
) -> str:
    return _connection_id_class(connection.connection_id, context)


def _connection_id_class(identifier, context: Mapping[str, object]) -> str:
    if identifier.kind is ConnectionKind.GROUND_STATION_ACCESS:
        return "ground_station_access"
    orbit_by_satellite = context["orbit_by_satellite"]
    assert isinstance(orbit_by_satellite, dict)
    if (
        orbit_by_satellite[identifier.endpoint_a]
        == orbit_by_satellite[identifier.endpoint_b]
    ):
        return "intra_orbit_isl"
    return "inter_orbit_isl"


def _connection_has_polar_satellite(
    connection: Connection, topology_slice: TopologySlice
) -> bool:
    return _connection_id_has_polar_satellite(
        connection.connection_id, topology_slice
    )


def _connection_id_has_polar_satellite(
    identifier, topology_slice: TopologySlice
) -> bool:
    return any(
        endpoint.entity_kind == "satellite"
        and abs(topology_slice.positions[endpoint].latitude_deg) >= 70.0
        for endpoint in (identifier.endpoint_a, identifier.endpoint_b)
    )


def _target_identity(target) -> str:
    if isinstance(target, GroundStationRouteTarget):
        return f"ground-station:{target.ground_station_id.value}"
    if isinstance(target, ZoneRouteTarget):
        return f"zone:{target.zone_id}"
    raise TypeError("RouteSlice contains an unknown RouteTarget")


def _plane_pair(first: str, second: str) -> str:
    return "::".join(sorted((first, second)))


def _distribution(values: Iterable[int]) -> dict[str, int]:
    counts = Counter(values)
    return {str(key): counts[key] for key in sorted(counts)}


def _numeric_statistics(values: Iterable[float]) -> dict[str, float | int | None]:
    ordered = sorted(values)
    if not ordered:
        return {
            "count": 0,
            "minimum": None,
            "median": None,
            "p95": None,
            "maximum": None,
        }
    return {
        "count": len(ordered),
        "minimum": ordered[0],
        "median": median(ordered),
        "p95": ordered[max(0, ceil(0.95 * len(ordered)) - 1)],
        "maximum": ordered[-1],
    }


def _memory_observation() -> dict[str, int]:
    status = _read_key_value_kib(Path("/proc/self/status"))
    host = _read_key_value_kib(Path("/proc/meminfo"))
    return {
        "process_rss_kib": status.get("VmRSS", 0),
        "process_peak_rss_kib": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),
        "host_memory_available_kib": host.get("MemAvailable", 0),
        "host_memory_total_kib": host.get("MemTotal", 0),
    }


def _read_key_value_kib(path: Path) -> dict[str, int]:
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, raw = line.partition(":")
        if not separator:
            continue
        fields = raw.strip().split()
        if fields and fields[0].isdigit():
            values[key] = int(fields[0])
    return values


def _raise_if_memory_exhaustion_is_imminent(memory: Mapping[str, int]) -> None:
    total = memory["host_memory_total_kib"]
    available = memory["host_memory_available_kib"]
    reserve = max(2 * 1024 * 1024, ceil(total * 0.05))
    if total > 0 and available < reserve:
        raise MemoryError(
            "host MemAvailable fell below the larger of 2 GiB and 5% of "
            "MemTotal; S4 progression stopped before host exhaustion"
        )


def _checkpoint_record(
    topology_slice: TopologySlice,
    started: float,
    memory: Mapping[str, int],
) -> dict[str, object]:
    return {
        "slice_id": topology_slice.slice_id,
        "timestamp": topology_slice.timestamp.isoformat(),
        "elapsed_wall_seconds": perf_counter() - started,
        "process_rss_kib": memory["process_rss_kib"],
        "process_peak_rss_kib": memory["process_peak_rss_kib"],
        "host_memory_available_kib": memory["host_memory_available_kib"],
    }


def _tool_record(
    result: Mapping[str, object],
    directory: Path,
    generation_seconds: float,
    through_slice_id: int,
) -> dict[str, object]:
    return {
        "status": result.get("status"),
        "message": result.get("message"),
        "through_slice_id": through_slice_id,
        "generation_seconds": generation_seconds,
        "inventory": result.get("inventory"),
        "behavior": result.get("behavior"),
        "files": _directory_sizes(directory),
    }


def _completed_record(
    simulator: Simulator,
    static_construction_seconds: float,
    started: float,
    slices: list[dict[str, object]],
    differences: list[dict[str, object]],
    checkpoints: Mapping[str, object],
    slice_tool: Mapping[str, object] | None,
    output: Path,
) -> dict[str, object]:
    slice_seconds = [float(value["slice_generation_seconds"]) for value in slices]
    difference_seconds = [
        float(value["difference_generation_seconds"]) for value in differences
    ]
    rss_values = [int(value["process_rss_kib"]) for value in slices]
    available_values = [int(value["host_memory_available_kib"]) for value in slices]
    zero_pair_slices = sum(
        bool(value["connections"]["expected_adjacent_plane_pairs_without_links"])
        for value in slices
    )
    isolated_satellite_slices = sum(
        int(value["degrees"]["isolated_satellites"]) > 0 for value in slices
    )
    return {
        "format_version": 1,
        "status": "COMPLETE",
        "scope": "Position -> Connection -> Route -> Slice -> Difference",
        "scenario": {
            "identity": "s4_satellite_internet",
            "satellites": 348,
            "ground_stations": 355,
            "dynamic_nodes": 703,
            "zones": 732,
        },
        "timeline": _timeline_record(simulator),
        "models": _model_record(),
        "formal_results": {
            "real_slice_count": len(slices),
            "difference_count": len(differences),
            "retained_slice_count_including_zero": len(
                simulator.get_slicer().get_retained_slice_ids()
            ),
            "topology_identity_preserved": True,
        },
        "checkpoints": checkpoints,
        "timing": {
            "static_construction_seconds": static_construction_seconds,
            "dynamic_qualification_wall_seconds": perf_counter() - started,
            "slice_generation_seconds": _numeric_statistics(slice_seconds),
            "slice_generation_total_seconds": sum(slice_seconds),
            "difference_generation_seconds": _numeric_statistics(
                difference_seconds
            ),
            "difference_generation_total_seconds": sum(difference_seconds),
            "stage_timing_limitation": (
                "Slicer publishes one atomic call boundary; per-Model timing "
                "would require invasive core instrumentation and was not added."
            ),
        },
        "resources": {
            "process_rss_kib": _numeric_statistics(rss_values),
            "minimum_host_memory_available_kib": min(available_values),
            "resource_stop_rule": (
                "stop before host exhaustion if MemAvailable falls below "
                "max(2 GiB, 5% of MemTotal)"
            ),
        },
        "evolution": {
            "connection_count": _numeric_statistics(
                int(value["connections"]["total"]) for value in slices
            ),
            "route_entry_count": _numeric_statistics(
                int(value["routes"]["entries"]) for value in slices
            ),
            "served_zone_count": _numeric_statistics(
                int(value["zones"]["served"]) for value in slices
            ),
            "connection_additions_total": sum(
                int(value["connections"]["added"]) for value in differences
            ),
            "connection_removals_total": sum(
                int(value["connections"]["removed"]) for value in differences
            ),
            "route_patches_total": sum(
                int(value["routes"]["patches"]) for value in differences
            ),
            "slices_with_missing_adjacent_plane_pair_links": zero_pair_slices,
            "slices_with_isolated_satellites": isolated_satellite_slices,
        },
        "manual_review_observations": {
            "missing_adjacent_plane_pair_link_slices": zero_pair_slices,
            "isolated_satellite_slices": isolated_satellite_slices,
            "interpretation": (
                "Counts are observations from formal Slices; no Connection or "
                "Route policy was tuned from them."
            ),
        },
        "slice_difference_tool": slice_tool,
        "artifacts_before_run_summary": _directory_sizes(output),
        "excluded_stages": [
            "Planner",
            "BPF Compiler",
            "BPF Runner",
            "Compose/runtime containers",
        ],
    }


def _blocked_record(
    error: BaseException,
    simulator: Simulator,
    static_construction_seconds: float,
    started: float,
    slices: list[dict[str, object]],
    differences: list[dict[str, object]],
    checkpoints: Mapping[str, object],
    slice_tool: Mapping[str, object] | None,
) -> dict[str, object]:
    return {
        "format_version": 1,
        "status": "BLOCKED",
        "scope": "Position -> Connection -> Route -> Slice -> Difference",
        "timeline": _timeline_record(simulator),
        "models": _model_record(),
        "static_construction_seconds": static_construction_seconds,
        "elapsed_wall_seconds": perf_counter() - started,
        "completed_real_slice_count": len(slices),
        "completed_difference_count": len(differences),
        "checkpoints": checkpoints,
        "slice_difference_tool": slice_tool,
        "blocker": {
            "type": type(error).__name__,
            "message": str(error),
        },
    }


def _timeline_record(simulator: Simulator) -> dict[str, object]:
    timeline = simulator.get_timeline()
    return {
        "start_time": timeline.start_time.isoformat(),
        "end_time": timeline.end_time.isoformat() if timeline.end_time else None,
        "slice_interval_seconds": timeline.slice_interval.total_seconds(),
        "target_duration_seconds": 30 * 60,
        "expected_real_slice_count_including_endpoints": DYNAMIC_REAL_SLICE_COUNT,
    }


def _model_record() -> dict[str, object]:
    return {
        "position": "CircularOrbitPositionModel",
        "connection": {
            "model": "GeometricConnectionModel",
            "maximum_isl_distance_m": MAXIMUM_ISL_DISTANCE_M,
            "ground_station_minimum_elevation_deg": (
                GROUND_STATION_MINIMUM_ELEVATION_DEG
            ),
            "maximum_ground_station_access_count": (
                MAXIMUM_GROUND_STATION_ACCESS_COUNT
            ),
            "satellite_max_simultaneous_ground_station_accesses": 2,
            "ground_station_maximum_distance_m": (
                GROUND_STATION_MAXIMUM_DISTANCE_M
            ),
            "zone_coverage_minimum_elevation_deg": (
                ZONE_COVERAGE_MINIMUM_ELEVATION_DEG
            ),
            "bandwidth_bps_by_kind": {
                key.value: value for key, value in BANDWIDTH_BPS_BY_KIND.items()
            },
            "packet_loss_ppm_by_kind": {
                key.value: value for key, value in PACKET_LOSS_PPM_BY_KIND.items()
            },
            "propagation_speed_m_per_s": PROPAGATION_SPEED_M_PER_S,
            "access_assignment": (
                "quality-first primary GroundStation access, then residual "
                "redundancy without displacing frozen primary selections"
            ),
            "parameter_authority": (
                "evidence-informed S4 model policy; individual site caps "
                "remain None"
            ),
        },
        "route": {
            "model": "GatewayHopCountRouteModel",
            "gateway_candidate_count": GATEWAY_CANDIDATE_COUNT,
            "gateway_candidate_max_distance_m": (
                GATEWAY_CANDIDATE_MAX_DISTANCE_M
            ),
            "candidate_selection": (
                "static representative-point geographic distance with "
                "stable GroundStation identity tie-break"
            ),
            "parameter_authority": "Skyseed S4 routing policy",
        },
    }


def _slice_tool_context() -> dict[str, object]:
    """Project S4 policy facts for non-authoritative Tool explanation."""

    return {
        "zone_service": {
            "minimum_elevation_deg": (
                ZONE_COVERAGE_MINIMUM_ELEVATION_DEG
            ),
        },
        "ground_station_access": {
            "minimum_elevation_deg": (
                GROUND_STATION_MINIMUM_ELEVATION_DEG
            ),
            "maximum_slant_distance_km": (
                GROUND_STATION_MAXIMUM_DISTANCE_M / 1_000.0
            ),
            "ground_station_capacity": (
                MAXIMUM_GROUND_STATION_ACCESS_COUNT
            ),
        },
        "isl": {
            "same_orbit_policy": "fixed predecessor/successor ring",
            "cross_plane_policy": (
                "RAAN-neighbour fixed cyclic pairing"
            ),
            "maximum_distance_km": MAXIMUM_ISL_DISTANCE_M / 1_000.0,
            "earth_clearance": "exact Earth-clear LOS required",
        },
        "routing": {
            "model": "GatewayHopCountRouteModel",
            "gateway_candidates_per_zone": GATEWAY_CANDIDATE_COUNT,
            "metric": "minimum hop count on the published Connection graph",
        },
        "interpretation_notes": [
            (
                "Unserved Zones are expected: this workload maps a global "
                "732-Zone surface onto only one 348-Satellite shell."
            ),
            (
                "The 25° Zone-service minimum rejects distant, "
                "low-elevation service merely used to increase nominal "
                "coverage."
            ),
            (
                "Cross-plane ISLs concentrate toward high latitude because "
                "60°-RAAN neighbour planes still require Earth-clear LOS."
            ),
            (
                "A long formal Route can remain the minimum-hop path under "
                "the published access-capacity and ISL graph."
            ),
        ],
    }


def _directory_sizes(directory: Path) -> list[dict[str, object]]:
    if not directory.exists():
        return []
    return [
        {
            "path": path.relative_to(directory).as_posix(),
            "size_bytes": path.stat().st_size,
        }
        for path in sorted(
            value for value in directory.rglob("*") if value.is_file()
        )
    ]


def _append_jsonl(path: Path, value: Mapping[str, object]) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, sort_keys=True, separators=(",", ":")))
        stream.write("\n")


def _write_json(path: Path, value: Mapping[str, object]) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_checksums(
    path: Path,
    files: Iterable[Path],
    root: Path,
) -> None:
    path.write_text(
        "".join(
            f"{_sha256(value)}  {value.relative_to(root).as_posix()}\n"
            for value in files
        ),
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = ["run_s4_dynamic_qualification"]
