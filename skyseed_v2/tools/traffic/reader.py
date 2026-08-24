"""Read durable traffic, Position, and compiled boundary evidence."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from ipaddress import IPv4Interface, ip_interface
import json
from math import ceil
from pathlib import Path
from typing import Any


_COMPILED_DOMAINS = ("resources", "parameters", "forwarding", "zone_edges")


@dataclass(frozen=True, slots=True)
class TrafficReplayRead:
    """Hold detached JSON-compatible facts read from post-run artifacts."""

    snapshot: dict[str, Any]


def read_traffic_replay(
    telemetry_path: Path,
    slice_path: Path,
    bpf_compiler_path: Path,
    workload_path: Path | None = None,
) -> TrafficReplayRead:
    """Read and structurally validate one replay evidence set."""

    telemetry = Path(telemetry_path)
    dynamic_path = Path(slice_path)
    compiler_path = Path(bpf_compiler_path)
    workload_path = None if workload_path is None else Path(workload_path)
    records = _read_json_lines(telemetry)
    if not records or records[0].get("record_type") != "telemetry_header":
        raise ValueError("traffic telemetry must begin with one header")
    headers = _records_of_type(records, "telemetry_header")
    anchors = _records_of_type(records, "simulation_clock_anchor")
    if len(headers) != 1:
        raise ValueError("traffic telemetry must contain exactly one header")
    if len(anchors) != 1:
        raise ValueError(
            "traffic telemetry must contain exactly one simulation clock anchor"
        )
    header = headers[0]
    anchor = anchors[0]
    _validate_clock_anchor(anchor)

    node_records = _records_of_type(records, "node_identity")
    nodes_by_slot: dict[int, dict[str, Any]] = {}
    slots_by_ipv4: dict[str, int] = {}
    node_ids: set[str] = set()
    for value in node_records:
        slot = _non_negative_int(value.get("node_slot"), "node_slot")
        node_id = _non_empty_string(value.get("node_id"), "node_id")
        address = _non_empty_string(value.get("runtime_ipv4"), "runtime_ipv4")
        if slot in nodes_by_slot or node_id in node_ids or address in slots_by_ipv4:
            raise ValueError("traffic telemetry node identity is not unique")
        nodes_by_slot[slot] = dict(value)
        slots_by_ipv4[address] = slot
        node_ids.add(node_id)
    if not nodes_by_slot:
        raise ValueError("traffic telemetry contains no node identities")

    interface_records = _records_of_type(records, "interface_identity")
    interfaces: dict[tuple[int, int, int], dict[str, Any]] = {}
    for value in interface_records:
        generation = _non_negative_int(value.get("generation"), "generation")
        slot = _non_negative_int(value.get("node_slot"), "node_slot")
        ifindex = _positive_int(value.get("ifindex"), "ifindex")
        node = nodes_by_slot.get(slot)
        if node is None:
            raise ValueError("interface identity references an unknown node slot")
        if value.get("node_id") != node["node_id"]:
            raise ValueError("interface identity contradicts its node slot")
        _parse_timestamp(value.get("boundary_timestamp"), "boundary_timestamp")
        _non_empty_string(value.get("connection_id"), "connection_id")
        peer = _non_empty_string(value.get("peer_node_id"), "peer_node_id")
        if peer not in node_ids:
            raise ValueError("interface identity references an unknown peer node")
        if value.get("validity") != "exact-generation":
            raise ValueError("interface identity is not exact-generation evidence")
        key = generation, slot, ifindex
        if key in interfaces:
            raise ValueError("traffic telemetry interface identity is not unique")
        interfaces[key] = dict(value)
    generation_networks = _read_generation_networks(interfaces, nodes_by_slot)

    packets = []
    network_by_generation = {
        int(value["generation"]): value for value in generation_networks
    }
    for record_index, value in enumerate(records):
        if value.get("record_type") != "packet":
            continue
        packet = dict(value)
        packet["record_index"] = record_index
        _positive_int(packet.get("kernel_monotonic_ns"), "kernel_monotonic_ns")
        slot = _non_negative_int(packet.get("node_slot"), "node_slot")
        if slot not in nodes_by_slot:
            raise ValueError("packet references an unknown node slot")
        generation = _non_negative_int(packet.get("generation"), "generation")
        if generation not in network_by_generation:
            raise ValueError("packet references a generation without identity evidence")
        ingress = _positive_int(packet.get("ingress_ifindex"), "ingress_ifindex")
        ingress_identity = interfaces.get((generation, slot, ingress))
        if ingress_identity is None:
            raise ValueError("packet lacks exact-generation ingress identity")
        if ingress_identity["node_id"] != nodes_by_slot[slot]["node_id"]:
            raise ValueError("packet ingress identity contradicts its node slot")
        egress = packet.get("selected_egress_ifindex")
        if egress is not None:
            egress = _positive_int(egress, "selected_egress_ifindex")
            if (generation, slot, egress) not in interfaces:
                raise ValueError("packet lacks exact-generation egress identity")
        decision = _non_empty_string(packet.get("decision"), "decision")
        if (decision == "redirect") != (egress is not None):
            raise ValueError("packet decision contradicts selected egress evidence")
        _non_empty_string(packet.get("source_ipv4"), "source_ipv4")
        _non_empty_string(packet.get("destination_ipv4"), "destination_ipv4")
        _non_negative_int(packet.get("protocol"), "protocol")
        if not isinstance(packet.get("icmp"), dict):
            raise ValueError("packet ICMP evidence must be a mapping")
        packets.append(packet)

    loss_records = _records_of_type(records, "telemetry_loss")
    for value in loss_records:
        slot = _non_negative_int(value.get("node_slot"), "node_slot")
        if slot not in nodes_by_slot:
            raise ValueError("telemetry loss references an unknown node slot")
        _non_negative_int(value.get("lost_events"), "lost_events")
    recorder_ends = _records_of_type(records, "recorder_end")
    if len(recorder_ends) > 1:
        raise ValueError("traffic telemetry contains multiple recorder endings")

    dynamic = _read_json(dynamic_path, "Slice / Difference")
    if dynamic.get("tool") != "slice-difference-inspection":
        raise ValueError("traffic replay requires Slice / Difference inspection data")
    timeline = dict(dynamic.get("timeline", {}))
    dynamic_origin = _parse_timestamp(
        timeline.get("start_time"), "Slice / Difference timeline start"
    )
    dynamic_end = _parse_timestamp(
        timeline.get("end_time"), "Slice / Difference timeline end"
    )
    telemetry_origin = _parse_timestamp(
        anchor.get("nominal_origin"), "nominal_origin"
    )
    if dynamic_origin != telemetry_origin:
        raise ValueError("traffic and Slice / Difference simulation origins do not match")
    dynamic_nodes = {
        _non_empty_string(value.get("node_id"), "dynamic node_id"): dict(value)
        for value in dynamic.get("topology", {}).get("nodes", ())
    }
    if set(dynamic_nodes) != node_ids:
        raise ValueError(
            "traffic and Slice / Difference node identities do not describe one topology"
        )
    dynamic_zones = _read_dynamic_zones(dynamic)
    position_samples = _read_position_samples(dynamic, node_ids)

    compiler = _read_json(compiler_path, "BPF Compiler")
    compiler_evidence = _read_compiler_evidence(
        compiler,
        dynamic_origin=dynamic_origin,
        dynamic_end=dynamic_end,
        dynamic_nodes=dynamic_nodes,
        dynamic_zone_ids=set(dynamic_zones),
    )
    generation_networks = _align_generation_networks(
        generation_networks,
        compiler_evidence["nominal_boundaries"],
        compiler_evidence["zone_events"],
    )
    workload = (
        None
        if workload_path is None
        else _read_workload(
            workload_path,
            dynamic_origin=dynamic_origin,
            dynamic_end=dynamic_end,
            dynamic_nodes=dynamic_nodes,
            dynamic_zones=dynamic_zones,
            zone_prefixes={
                value["zone_id"]: value["packet_prefix"]
                for value in compiler_evidence["zone_prefixes"]
            },
        )
    )

    return TrafficReplayRead(
        {
            "format_version": 3,
            "header": dict(header),
            "clock_anchor": dict(anchor),
            "nodes": tuple(nodes_by_slot[key] for key in sorted(nodes_by_slot)),
            "dynamic_nodes": tuple(dynamic_nodes[key] for key in sorted(dynamic_nodes)),
            "dynamic_zones": tuple(dynamic_zones[key] for key in sorted(dynamic_zones)),
            "interfaces": tuple(interfaces[key] for key in sorted(interfaces)),
            "generation_networks": generation_networks,
            "packets": tuple(packets),
            "loss_records": tuple(dict(value) for value in loss_records),
            "recorder_end": None if not recorder_ends else dict(recorder_ends[0]),
            "position_samples": position_samples,
            "timeline": timeline,
            "compiler": compiler_evidence,
            "workload": workload,
            "raw_records": tuple(dict(value) for value in records),
        }
    )


def _read_generation_networks(
    interfaces: dict[tuple[int, int, int], dict[str, Any]],
    nodes_by_slot: dict[int, dict[str, Any]],
) -> tuple[dict[str, Any], ...]:
    by_generation: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for (generation, _slot, _ifindex), value in interfaces.items():
        by_generation[generation].append(value)
    if not by_generation:
        raise ValueError("traffic telemetry contains no generation identities")
    generations = sorted(by_generation)
    if generations != list(range(generations[-1] + 1)):
        raise ValueError("traffic telemetry generation identities are not continuous")

    result = []
    for generation in generations:
        values = by_generation[generation]
        boundaries = {value["boundary_timestamp"] for value in values}
        if len(boundaries) != 1:
            raise ValueError("one generation maps to multiple nominal boundaries")
        by_connection: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for value in values:
            by_connection[str(value["connection_id"])].append(value)
        connections = []
        for connection_id in sorted(by_connection):
            pair = by_connection[connection_id]
            if len(pair) != 2:
                raise ValueError("generation Connection identity lacks two endpoints")
            first, second = pair
            first_node = str(first["node_id"])
            second_node = str(second["node_id"])
            if (
                first_node == second_node
                or first["peer_node_id"] != second_node
                or second["peer_node_id"] != first_node
            ):
                raise ValueError("generation Connection endpoint pair is contradictory")
            encoded = connection_id.split("|")
            if len(encoded) != 3 or set(encoded[1:]) != {first_node, second_node}:
                raise ValueError("generation Connection identity contradicts its endpoints")
            if encoded[0] not in {"inter-satellite", "ground-station-access"}:
                raise ValueError("generation Connection kind is unsupported")
            endpoints = sorted((first_node, second_node))
            connections.append(
                {
                    "connection_id": connection_id,
                    "kind": encoded[0],
                    "endpoint_a": endpoints[0],
                    "endpoint_b": endpoints[1],
                    "interfaces": tuple(
                        {
                            "node_id": str(item["node_id"]),
                            "peer_node_id": str(item["peer_node_id"]),
                            "ifindex": int(item["ifindex"]),
                            "interface": str(item["interface"]),
                        }
                        for item in sorted(pair, key=lambda current: current["node_id"])
                    ),
                }
            )
        result.append(
            {
                "generation": generation,
                "boundary_timestamp": next(iter(boundaries)),
                "connections": tuple(connections),
                "node_count": len(nodes_by_slot),
            }
        )
    return tuple(result)


def _read_compiler_evidence(
    value: dict[str, Any],
    *,
    dynamic_origin: datetime,
    dynamic_end: datetime,
    dynamic_nodes: dict[str, dict[str, Any]],
    dynamic_zone_ids: set[str],
) -> dict[str, Any]:
    if value.get("tool") != "bpf-compiler-inspection":
        raise ValueError("traffic replay requires BPF Compiler inspection data")
    intervals = value.get("intervals")
    if not isinstance(intervals, list) or not intervals:
        raise ValueError("BPF Compiler inspection contains no intervals")

    stable_prefixes: dict[str, str] | None = None
    nominal_boundaries: set[datetime] = set()
    zone_events: list[dict[str, Any]] = []
    zone_state: dict[str, tuple[str, str]] = {}
    previous_end: datetime | None = None
    for index, interval in enumerate(intervals):
        if not isinstance(interval, dict):
            raise ValueError("BPF Compiler interval is not a mapping")
        start_value = interval.get("start_timestamp")
        start = None if start_value is None else _parse_timestamp(
            start_value, "BPF Compiler interval start"
        )
        end = _parse_timestamp(interval.get("end_timestamp"), "BPF Compiler interval end")
        if index == 0:
            if start is not None:
                raise ValueError("BPF Compiler bootstrap interval has a start timestamp")
        elif start != previous_end:
            raise ValueError("BPF Compiler intervals are not continuous")
        previous_end = end

        compiled = interval.get("compiled")
        if not isinstance(compiled, dict):
            raise ValueError("BPF Compiler interval omits compiled evidence")
        for domain in _COMPILED_DOMAINS:
            domain_values = compiled.get(domain)
            if not isinstance(domain_values, list):
                raise ValueError(f"BPF Compiler {domain} evidence is not a list")
            for operation in domain_values:
                if not isinstance(operation, dict):
                    raise ValueError("BPF Compiler operation is not a mapping")
                timestamp = _parse_timestamp(
                    operation.get("nominal_time"), "compiled nominal time"
                )
                if timestamp > end or (start is not None and timestamp <= start):
                    raise ValueError("compiled operation falls outside interval coverage")
                nominal_boundaries.add(timestamp)

        end_state = interval.get("end_state")
        if not isinstance(end_state, dict):
            raise ValueError("BPF Compiler interval omits intended end state")
        prefixes = _read_zone_prefixes(end_state.get("zone_prefixes"))
        if stable_prefixes is None:
            stable_prefixes = prefixes
            if set(prefixes) != dynamic_zone_ids:
                raise ValueError("BPF Compiler and Slice / Difference Zone identities differ")
        elif prefixes != stable_prefixes:
            raise ValueError("BPF Compiler Zone prefixes are unstable across realizations")

        events = sorted(
            compiled["zone_edges"],
            key=lambda item: (item["nominal_time"], item["zone_id"]),
        )
        for event in events:
            zone_id = _non_empty_string(event.get("zone_id"), "Zone event zone_id")
            if zone_id not in prefixes:
                raise ValueError("compiled Zone event references an unknown Zone")
            if event.get("packet_prefix") != prefixes[zone_id]:
                raise ValueError("compiled Zone event contradicts its stable prefix")
            timestamp = _parse_timestamp(event.get("nominal_time"), "Zone event time")
            operation = event.get("operation")
            if operation not in {"added", "changed", "removed"}:
                raise ValueError("compiled Zone event has an unknown operation")
            prior = zone_state.get(zone_id)
            prior_service = None if prior is None else prior[1]
            if event.get("previous_service_satellite") != prior_service:
                raise ValueError("compiled Zone event predecessor is contradictory")
            service = event.get("service_satellite")
            if operation == "removed":
                if prior is None or service is not None:
                    raise ValueError("compiled Zone removal is contradictory")
                del zone_state[zone_id]
            else:
                service = _non_empty_string(service, "Zone service satellite")
                metadata = dynamic_nodes.get(service)
                if metadata is None or metadata.get("kind") != "satellite":
                    raise ValueError("compiled Zone service is not a known Satellite")
                if (operation == "added") != (prior is None):
                    raise ValueError("compiled Zone update operation is contradictory")
                zone_state[zone_id] = (prefixes[zone_id], service)
            zone_events.append(
                {
                    "nominal_time": timestamp.isoformat(),
                    "operation": operation,
                    "zone_id": zone_id,
                    "packet_prefix": prefixes[zone_id],
                    "previous_service_satellite": prior_service,
                    "service_satellite": service,
                }
            )

        expected_state = {
            _non_empty_string(item.get("zone_id"), "Zone end-state zone_id"): (
                _non_empty_string(item.get("packet_prefix"), "Zone end-state prefix"),
                _non_empty_string(
                    item.get("service_satellite"), "Zone end-state service"
                ),
            )
            for item in end_state.get("zone_edges", ())
        }
        if expected_state != zone_state:
            raise ValueError("compiled Zone events do not reconstruct intended end state")

    assert stable_prefixes is not None
    if not nominal_boundaries:
        raise ValueError("BPF Compiler inspection contains no nominal boundaries")
    ordered_boundaries = tuple(sorted(nominal_boundaries))
    if ordered_boundaries[0] != dynamic_origin or ordered_boundaries[-1] != dynamic_end:
        raise ValueError("BPF Compiler and Slice / Difference timelines differ")
    if previous_end != dynamic_end:
        raise ValueError("BPF Compiler final coverage differs from Slice / Difference")
    return {
        "nominal_boundaries": tuple(item.isoformat() for item in ordered_boundaries),
        "zone_prefixes": tuple(
            {"zone_id": zone_id, "packet_prefix": stable_prefixes[zone_id]}
            for zone_id in sorted(stable_prefixes)
        ),
        "zone_events": tuple(zone_events),
    }


def _read_zone_prefixes(value: object) -> dict[str, str]:
    if not isinstance(value, list):
        raise ValueError("BPF Compiler Zone prefixes are not a list")
    result: dict[str, str] = {}
    addresses: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("BPF Compiler Zone prefix is not a mapping")
        zone_id = _non_empty_string(item.get("zone_id"), "Zone prefix zone_id")
        prefix = _non_empty_string(item.get("packet_prefix"), "Zone packet prefix")
        try:
            parsed = ip_interface(prefix)
        except ValueError as error:
            raise ValueError("BPF Compiler Zone prefix is invalid") from error
        if not isinstance(parsed, IPv4Interface) or parsed.network.prefixlen != 32:
            raise ValueError("BPF Compiler Zone identity must be one IPv4 /32")
        address = str(parsed.ip)
        if zone_id in result or address in addresses:
            raise ValueError("BPF Compiler Zone prefix identity is conflicting")
        result[zone_id] = prefix
        addresses.add(address)
    return result


def _align_generation_networks(
    generation_networks: tuple[dict[str, Any], ...],
    nominal_boundaries: tuple[str, ...],
    zone_events: tuple[dict[str, Any], ...],
) -> tuple[dict[str, Any], ...]:
    if len(generation_networks) != len(nominal_boundaries):
        raise ValueError("generation and nominal boundary counts differ")
    events_by_time: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in zone_events:
        events_by_time[event["nominal_time"]].append(event)
    services: dict[str, str] = {}
    result = []
    for index, (network, boundary) in enumerate(
        zip(generation_networks, nominal_boundaries, strict=True)
    ):
        if network["generation"] != index or network["boundary_timestamp"] != boundary:
            raise ValueError("generation-to-boundary mapping contradicts Compiler evidence")
        for event in events_by_time.get(boundary, ()):
            if event["operation"] == "removed":
                services.pop(event["zone_id"], None)
            else:
                services[event["zone_id"]] = event["service_satellite"]
        result.append(
            {
                **network,
                "zone_services": tuple(
                    {"zone_id": zone_id, "service_satellite": services[zone_id]}
                    for zone_id in sorted(services)
                ),
            }
        )
    return tuple(result)


def _read_dynamic_zones(dynamic: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result = {}
    for value in dynamic.get("topology", {}).get("zones", ()):
        if not isinstance(value, dict):
            raise ValueError("Slice / Difference Zone is not a mapping")
        zone_id = _non_empty_string(value.get("zone_id"), "dynamic zone_id")
        if zone_id in result:
            raise ValueError("Slice / Difference Zone identity is duplicated")
        geometry = value.get("geometry")
        if not isinstance(geometry, dict) or geometry.get("kind") != (
            "latitude-longitude-rectangle"
        ):
            raise ValueError("Traffic replay requires rectangular Zone geometry")
        south = _finite_number(
            geometry.get("south_latitude_deg"), "Zone south latitude"
        )
        north = _finite_number(
            geometry.get("north_latitude_deg"), "Zone north latitude"
        )
        west = _finite_number(
            geometry.get("west_longitude_deg"), "Zone west longitude"
        )
        east = _finite_number(
            geometry.get("east_longitude_deg"), "Zone east longitude"
        )
        if not -90 <= south < north <= 90 or not (
            -180 <= west <= 180 and -180 <= east <= 180
        ):
            raise ValueError("Slice / Difference Zone rectangle is out of range")
        representative = value.get("representative_point")
        if not isinstance(representative, dict):
            raise ValueError("Slice / Difference Zone omits its representative point")
        result[zone_id] = {
            **dict(value),
            "geometry": {
                "kind": "latitude-longitude-rectangle",
                "south_latitude_deg": south,
                "north_latitude_deg": north,
                "west_longitude_deg": west,
                "east_longitude_deg": east,
            },
            "representative_point": {
                "latitude_deg": _finite_number(
                    representative.get("latitude_deg"),
                    "Zone representative latitude",
                ),
                "longitude_deg": _finite_number(
                    representative.get("longitude_deg"),
                    "Zone representative longitude",
                ),
            },
        }
    return result


def _read_workload(
    path: Path,
    *,
    dynamic_origin: datetime,
    dynamic_end: datetime,
    dynamic_nodes: dict[str, dict[str, Any]],
    dynamic_zones: dict[str, dict[str, Any]],
    zone_prefixes: dict[str, str],
) -> dict[str, Any]:
    value = _read_json(path, "Traffic workload")
    if value.get("format_version") != 1 or value.get("artifact_type") != (
        "canonical-traffic-workload-schedule"
    ):
        raise ValueError("traffic replay requires canonical workload schedule data")
    authority = value.get("authority")
    if not isinstance(authority, dict) or authority.get("status") != (
        "planned-example-workload"
    ):
        raise ValueError("traffic workload authority is invalid")
    for forbidden_proof in (
        "proves_command_execution",
        "proves_packet_emission",
        "proves_timeout",
        "proves_packet_loss",
    ):
        if authority.get(forbidden_proof) is not False:
            raise ValueError("traffic workload claims unsupported runtime evidence")
    simulation = value.get("simulation")
    if not isinstance(simulation, dict):
        raise ValueError("traffic workload omits its simulation range")
    start = _parse_timestamp(simulation.get("start_time"), "workload start")
    end = _parse_timestamp(simulation.get("end_time"), "workload end")
    if start != dynamic_origin or end != dynamic_end:
        raise ValueError("traffic workload and Slice / Difference timelines differ")
    if abs(
        _finite_number(simulation.get("duration_seconds"), "workload duration")
        - (end - start).total_seconds()
    ) > 1e-9:
        raise ValueError("traffic workload duration is contradictory")

    raw_series = value.get("series")
    if not isinstance(raw_series, list):
        raise ValueError("traffic workload series is not a list")
    result = []
    seen = set()
    total_slots = 0
    concurrency_points = []
    addresses = {
        zone_id: str(ip_interface(prefix).ip)
        for zone_id, prefix in zone_prefixes.items()
    }
    for item in raw_series:
        if not isinstance(item, dict):
            raise ValueError("traffic workload series is not a mapping")
        series_id = _non_empty_string(item.get("series_id"), "series_id")
        if series_id in seen:
            raise ValueError("traffic workload series identity is duplicated")
        seen.add(series_id)
        direction = item.get("direction")
        if direction not in {
            "ground-station-to-zone",
            "zone-to-ground-station",
        }:
            raise ValueError("traffic workload direction is invalid")
        ground = item.get("ground_station")
        zone = item.get("zone")
        schedule = item.get("schedule")
        if not all(isinstance(current, dict) for current in (ground, zone, schedule)):
            raise ValueError("traffic workload series facts are incomplete")
        ground_id = _non_empty_string(ground.get("node_id"), "workload GroundStation")
        metadata = dynamic_nodes.get(ground_id)
        if metadata is None or metadata.get("kind") != "ground-station":
            raise ValueError("traffic workload GroundStation is not in the topology")
        zone_id = _non_empty_string(zone.get("zone_id"), "workload Zone")
        if zone_id not in dynamic_zones:
            raise ValueError("traffic workload Zone is not in the topology")
        if zone.get("packet_ipv4") != addresses.get(zone_id):
            raise ValueError("traffic workload Zone packet identity is contradictory")
        series_start = _parse_timestamp(schedule.get("start_time"), "series start")
        series_end = _parse_timestamp(schedule.get("end_time"), "series end")
        if not start <= series_start < series_end <= end:
            raise ValueError("traffic workload series falls outside the simulation")
        start_offset = _finite_number(
            schedule.get("start_offset_seconds"), "series start offset"
        )
        end_offset = _finite_number(
            schedule.get("end_offset_seconds"), "series end offset"
        )
        interval = _finite_number(
            schedule.get("interval_seconds"), "series interval"
        )
        phase = _finite_number(schedule.get("phase_seconds"), "series phase")
        slots = _positive_int(schedule.get("scheduled_slots"), "scheduled_slots")
        if schedule.get("interval_semantics") != "[start,end)":
            raise ValueError("traffic workload interval semantics are invalid")
        if interval <= 0 or not 0 <= phase < interval:
            raise ValueError("traffic workload cadence is invalid")
        if abs(start_offset - (series_start - start).total_seconds()) > 1e-6 or abs(
            end_offset - (series_end - start).total_seconds()
        ) > 1e-6:
            raise ValueError("traffic workload relative and absolute times differ")
        expected_slots = ceil((end_offset - start_offset) / interval - 1e-9)
        if expected_slots != slots:
            raise ValueError("traffic workload scheduled slot count is contradictory")
        total_slots += slots
        concurrency_points.extend(((series_start, 1), (series_end, -1)))
        result.append(
            {
                **dict(item),
                "ground_station": dict(ground),
                "zone": dict(zone),
                "schedule": dict(schedule),
            }
        )
    inventory = value.get("inventory")
    if not isinstance(inventory, dict):
        raise ValueError("traffic workload inventory is missing")
    active = maximum = 0
    for _timestamp, delta in sorted(
        concurrency_points, key=lambda current: (current[0], current[1])
    ):
        active += delta
        maximum = max(maximum, active)
    if (
        inventory.get("series") != len(result)
        or inventory.get("scheduled_slots") != total_slots
        or inventory.get("maximum_concurrent_series") != maximum
    ):
        raise ValueError("traffic workload inventory is contradictory")
    static_expectation = value.get("static_expectation")
    if static_expectation is not None:
        if not isinstance(static_expectation, dict) or static_expectation.get(
            "status"
        ) != "pre-runtime-estimate-only":
            raise ValueError("traffic workload static expectation is invalid")
        for key in (
            "paired_transactions",
            "scheduled_slots_without_tc_bpf_observation",
            "packet_events",
        ):
            _non_negative_int(
                static_expectation.get(key), f"static expectation {key}"
            )
    return {
        "format_version": 1,
        "authority": dict(authority),
        "simulation": dict(simulation),
        "inventory": dict(inventory),
        "static_expectation": (
            None if static_expectation is None else dict(static_expectation)
        ),
        "series": tuple(result),
    }


def _read_position_samples(
    dynamic: dict[str, Any], expected_node_ids: set[str]
) -> tuple[dict[str, Any], ...]:
    samples = []
    previous: datetime | None = None
    for value in dynamic.get("slices", ()):
        timestamp = value.get("timestamp")
        if timestamp is None:
            continue
        parsed = _parse_timestamp(timestamp, "Slice timestamp")
        if previous is not None and parsed <= previous:
            raise ValueError("Slice / Difference timestamps must strictly increase")
        previous = parsed
        positions: dict[str, dict[str, Any]] = {}
        for position in value.get("positions", ()):
            node_id = _non_empty_string(position.get("node_id"), "position node_id")
            if node_id in positions:
                raise ValueError("Slice / Difference position node is duplicated")
            ecef = position.get("ecef_m")
            if not isinstance(ecef, dict):
                raise ValueError("Slice / Difference position omits ECEF evidence")
            positions[node_id] = {
                "ecef_m": {
                    axis: _finite_number(ecef.get(axis), f"ECEF {axis}")
                    for axis in ("x", "y", "z")
                },
                "geographic": dict(position.get("geographic", {})),
            }
        if set(positions) != expected_node_ids:
            raise ValueError("Slice / Difference position coverage is incomplete")
        samples.append(
            {
                "slice_id": _positive_int(value.get("slice_id"), "slice_id"),
                "timestamp": parsed.isoformat(),
                "positions": positions,
            }
        )
    if not samples:
        raise ValueError("Slice / Difference inspection contains no real Positions")
    return tuple(samples)


def _read_json_lines(path: Path) -> tuple[dict[str, Any], ...]:
    values = []
    try:
        stream = path.open(encoding="utf-8")
    except OSError as error:
        raise RuntimeError(f"cannot read traffic telemetry: {path}") from error
    with stream:
        for line_number, line in enumerate(stream, 1):
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"traffic telemetry line {line_number} is not valid JSON"
                ) from error
            if not isinstance(value, dict) or not isinstance(
                value.get("record_type"), str
            ):
                raise ValueError(f"traffic telemetry line {line_number} is not a record")
            values.append(value)
    return tuple(values)


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise RuntimeError(f"cannot read {label} evidence: {path}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"{label} evidence is not valid JSON") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} evidence must be a mapping")
    return value


def _records_of_type(
    records: tuple[dict[str, Any], ...], record_type: str
) -> tuple[dict[str, Any], ...]:
    return tuple(value for value in records if value["record_type"] == record_type)


def _validate_clock_anchor(value: dict[str, Any]) -> None:
    _parse_timestamp(value.get("nominal_origin"), "nominal_origin")
    _non_negative_int(value.get("monotonic_origin_ns"), "monotonic_origin_ns")
    if value.get("clock_domain") != "host-kernel-monotonic":
        raise ValueError("traffic telemetry clock domain is unsupported")
    if value.get("nominal_pacing") != "1:1":
        raise ValueError("traffic telemetry nominal pacing is unsupported")


def _parse_timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{label} must be an ISO timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return parsed


def _non_empty_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _non_negative_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _positive_int(value: object, label: str) -> int:
    result = _non_negative_int(value, label)
    if result == 0:
        raise ValueError(f"{label} must be a positive integer")
    return result


def _finite_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    result = float(value)
    if result != result or result in (float("inf"), float("-inf")):
        raise ValueError(f"{label} must be finite")
    return result


__all__ = ["TrafficReplayRead", "read_traffic_replay"]
