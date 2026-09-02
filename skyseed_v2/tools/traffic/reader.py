"""Read traffic telemetry, PhysicalFrame history, and planned intent evidence."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
from pathlib import Path
from typing import Any

from skyseed_v2.simulator import (
    PlannedOperationalState,
    ReachableRouteOutcome,
    apply_plan_transition,
    planned_operational_state_fingerprint,
)
from skyseed_v2.shared._domain_history import (
    _DomainHistoryManifest,
    _iter_verified_domain_history_stream,
    _read_domain_history_manifest,
)
from skyseed_v2.simulator.planning import (
    plan_transition_to_data,
    planned_operational_state_to_data,
)
from tools.bpf_compiler.reader import read_bpf_compiler_history
from tools.planner.reader import iter_planner_history
from tools.runner.reader import read_runner_event_history


@dataclass(frozen=True, slots=True)
class TrafficReplayRead:
    """Hold detached JSON-compatible post-run evidence."""

    snapshot: dict[str, Any]


def read_traffic_replay(
    telemetry_path: Path,
    physical_frames_path: Path,
    planning_path: Path,
    bpf_compiler_path: Path,
    workload_path: Path | None = None,
    probe_plan_path: Path | None = None,
    probe_execution_path: Path | None = None,
    runner_events_path: Path | None = None,
) -> TrafficReplayRead:
    """Read and fail closed on inconsistent physical/planned/runtime evidence."""

    qualification_paths = (
        probe_plan_path,
        probe_execution_path,
        runner_events_path,
    )
    if any(value is not None for value in qualification_paths) and not all(
        value is not None for value in qualification_paths
    ):
        raise ValueError(
            "Traffic qualification requires plan, execution, and Runner events"
        )
    telemetry = _read_telemetry(Path(telemetry_path))
    history_manifest = _read_domain_history_manifest(
        Path(planning_path), minimum_through="compiler"
    )
    _require_history_stream_path(
        Path(physical_frames_path),
        history_manifest,
        "physical_frames",
    )
    compiler_path = Path(bpf_compiler_path)
    if compiler_path.suffix == ".jsonl":
        _require_history_stream_path(
            compiler_path, history_manifest, "compiler"
        )
    physical = _read_physical_frames(
        Path(physical_frames_path), history_manifest
    )
    planning = _read_planning(Path(planning_path))
    if tuple(value["timestamp"] for value in physical["frames"]) != tuple(
        value["timestamp"] for value in planning["formal_transitions"]
    ):
        raise ValueError(
            "PhysicalFrame and planned formal timestamp coverage differ"
        )
    _validate_interface_generation_boundaries(
        telemetry["interfaces"], planning["formal_transitions"]
    )
    if physical["frames"][0]["timestamp"] != telemetry["nominal_origin"]:
        raise ValueError("telemetry and formal PhysicalFrame origins differ")
    physical_nodes = {
        value["node_id"] for value in physical["frames"][0]["positions"]
    }
    telemetry_nodes = {value["node_id"] for value in telemetry["nodes"]}
    if physical_nodes != telemetry_nodes:
        raise ValueError(
            "telemetry identities and PhysicalFrame nodes describe different worlds"
        )
    compiler = _read_compiler(compiler_path, planning=planning)
    workload = (
        None
        if workload_path is None
        else _read_workload(Path(workload_path))
    )
    qualification = None
    if probe_plan_path is not None:
        qualification = {
            "plan": _read_json(Path(probe_plan_path), "Traffic probe plan"),
            "execution": _read_jsonl(Path(probe_execution_path)),
            "runner_events": read_runner_event_history(
                Path(runner_events_path)
            ),
        }
        if not qualification["execution"]:
            raise ValueError("Traffic probe execution is empty")
    return TrafficReplayRead(
        {
            "format_version": 4,
            "tool": "traffic-path-replay",
            "authority": {
                "status": "non-authoritative-view",
                "physical_world": (
                    "Plan provenance fingerprint plus formal PhysicalFrame positions"
                ),
                "planned_reachability": (
                    "explicit RouteOutcome values replayed from self-contained Plans"
                ),
                "compiled": "already-produced realization lineage and intended state",
                "runtime": "detached packet and Runner records",
            },
            "physical_world_fingerprint": planning[
                "physical_world_fingerprint"
            ],
            "physical_frames": physical["frames"],
            "planning": planning,
            "compiler": compiler,
            "telemetry": telemetry,
            "workload": workload,
            "qualification": qualification,
        }
    )


def _read_physical_frames(
    path: Path, manifest: _DomainHistoryManifest
) -> dict[str, object]:
    del path
    rows = _iter_verified_domain_history_stream(
        manifest, "physical_frames"
    )
    try:
        header = next(rows)
    except StopIteration as error:
        raise ValueError("PhysicalFrame evidence stream is empty") from error
    if header != {
        "record_type": "physical_frame_history_header",
        "format_version": 1,
        "coordinate_system": "WGS84-ECEF-metres",
        "formal_frames_only": True,
    }:
        raise ValueError("PhysicalFrame evidence header is invalid")
    frames = []
    prior_timestamp = None
    node_set = None
    for row in rows:
        if set(row) != {"record_type", "timestamp", "positions"}:
            raise ValueError("PhysicalFrame evidence row has unknown fields")
        if row["record_type"] != "physical_frame":
            raise ValueError("PhysicalFrame evidence record type is invalid")
        timestamp = _timestamp(row["timestamp"], "PhysicalFrame timestamp")
        if prior_timestamp is not None and timestamp <= prior_timestamp:
            raise ValueError("PhysicalFrame timestamps must be strictly increasing")
        positions = []
        seen = set()
        raw_positions = row["positions"]
        if not isinstance(raw_positions, list):
            raise TypeError("PhysicalFrame positions must be a list")
        for value in raw_positions:
            if not isinstance(value, dict) or set(value) != {
                "node",
                "x_m",
                "y_m",
                "z_m",
            }:
                raise ValueError("PhysicalFrame position is malformed")
            node = _node(value["node"])
            node_id = _node_id(node)
            if node_id in seen:
                raise ValueError("PhysicalFrame contains duplicate node positions")
            seen.add(node_id)
            positions.append(
                {
                    "node": node,
                    "node_id": node_id,
                    "ecef_m": {
                        "x": _number(value["x_m"], "x_m"),
                        "y": _number(value["y_m"], "y_m"),
                        "z": _number(value["z_m"], "z_m"),
                    },
                }
            )
        if node_set is None:
            node_set = seen
        elif seen != node_set:
            raise ValueError("PhysicalFrame node coverage changes across history")
        frames.append(
            {
                "timestamp": timestamp.isoformat(),
                "positions": tuple(
                    sorted(positions, key=lambda value: value["node_id"])
                ),
            }
        )
        prior_timestamp = timestamp
    if not frames:
        raise ValueError("PhysicalFrame evidence contains no formal frame")
    return {"frames": tuple(frames)}


def _require_history_stream_path(
    path: Path,
    manifest: _DomainHistoryManifest,
    stream_name: str,
) -> None:
    raw = Path(path)
    if raw.is_symlink():
        raise ValueError("Traffic domain-history stream must not be a symlink")
    try:
        resolved = raw.resolve(strict=True)
    except OSError as error:
        raise ValueError("Traffic domain-history stream is unavailable") from error
    if resolved != manifest.stream_path(stream_name):
        raise ValueError(
            "Traffic inputs must reference one sealed domain-history root"
        )


def _read_planning(path: Path) -> dict[str, object]:
    state = None
    formal_transitions = []
    plans = []
    physical_world_fingerprint = None
    for plan_index, value in enumerate(iter_planner_history(path)):
        plan = value.plan
        if physical_world_fingerprint is None:
            physical_world_fingerprint = plan.physical_world_fingerprint
        for transition_index, transition in enumerate(plan.transitions):
            state = apply_plan_transition(state, transition)
            formal_transitions.append(
                {
                    "plan_index": plan_index,
                    "timestamp": transition.timestamp.isoformat(),
                    "plan_id": plan.plan_id,
                    "transition_index": transition_index,
                    "transition_fingerprint": value.transition_fingerprints[
                        transition_index
                    ],
                    "state_fingerprint": planned_operational_state_fingerprint(
                        state
                    ),
                    "state_summary": _planning_state_summary(state),
                    "transition": plan_transition_to_data(transition),
                }
            )
        plans.append(
            {
                "plan_id": plan.plan_id,
                "previous_plan_id": plan.previous_plan_id,
                "input_state_fingerprint": plan.input_state_fingerprint,
                "output_state_fingerprint": plan.output_state_fingerprint,
                "start_timestamp": plan.start_timestamp.isoformat(),
                "end_timestamp": plan.end_timestamp.isoformat(),
                "activation_timestamps": tuple(
                    item.timestamp.isoformat() for item in plan.transitions
                ),
            }
        )
    assert state is not None
    assert physical_world_fingerprint is not None
    return {
        "physical_world_fingerprint": physical_world_fingerprint,
        "plans": tuple(plans),
        "formal_transitions": tuple(formal_transitions),
        "final_state": planned_operational_state_to_data(state),
        "final_state_fingerprint": planned_operational_state_fingerprint(state),
    }


def _planning_state_summary(value: PlannedOperationalState) -> dict[str, int]:
    assignments = value.zone_service_assignments
    reachable = sum(
        isinstance(outcome, ReachableRouteOutcome)
        for outcome in value.route_outcomes
    )
    served = sum(
        assignment.satellite_id is not None for assignment in assignments
    )
    return {
        "operational_connection_count": len(value.operational_connections),
        "zone_assignment_count": len(assignments),
        "zones_served": served,
        "zones_unserved": len(assignments) - served,
        "reachable_route_count": reachable,
        "unreachable_route_count": len(value.route_outcomes) - reachable,
    }


def _read_compiler(
    path: Path, *, planning: dict[str, object]
) -> dict[str, object]:
    if path.suffix == ".jsonl":
        records = read_bpf_compiler_history(path)["realizations"]
        realizations = tuple(
            {
                "realization_index": value["realization_index"],
                "source_plan_id": value["source_plan_id"],
                "source_previous_plan_id": value["source_previous_plan_id"],
                "physical_world_fingerprint": value[
                    "physical_world_fingerprint"
                ],
                "input_state_fingerprint": value["input_state_fingerprint"],
                "output_state_fingerprint": value["output_state_fingerprint"],
                "activation_timestamps": tuple(value["activation_timestamps"]),
                "intent_counts": dict(value["intent_counts"]),
                "intended_state_timestamp": None,
            }
            for value in records
        )
        source_kind = "compact-producer-lineage"
    else:
        artifact = _read_json(path, "Compiler inspection")
        if artifact.get("tool") != "bpf-compiler-inspection":
            raise ValueError("Compiler inspection artifact is incompatible")
        raw = artifact.get("realizations")
        if not isinstance(raw, list):
            raise TypeError("Compiler inspection realizations must be a list")
        if artifact.get("format_version") == 3:
            realizations = tuple(
                {
                    "realization_index": value["realization_index"],
                    **value["lineage"],
                    "activation_timestamps": tuple(
                        value["activation_timestamps"]
                    ),
                    "intent_counts": {
                        "resource": value["summary"]["resource_intents"],
                        "parameter": value["summary"]["parameter_intents"],
                        "forwarding": value["summary"]["forwarding_intents"],
                        "zone_edge": value["summary"]["zone_edge_intents"],
                        "constraints": value["summary"]["constraints"],
                    },
                    "intended_state_timestamp": value[
                        "intended_state_timestamp"
                    ],
                    "zone_prefixes": value["zone_prefixes"],
                }
                for value in raw
            )
            source_kind = "compact-compiler-inspection-v3"
        else:
            realizations = tuple(
                {
                    "realization_index": value["realization_index"],
                    **value["lineage"],
                    "activation_timestamps": tuple(
                        item["timestamp"]
                        for item in value["activation_boundaries"]
                    ),
                    "intent_counts": {
                        name: len(rows)
                        for name, rows in value["compiled_intents"].items()
                    },
                    "intended_state_timestamp": value["intended_state"][
                        "semantic"
                    ]["timestamp"],
                    "zone_prefixes": value["intended_state"]["zone_prefixes"],
                }
                for value in raw
            )
            source_kind = "full-compiler-inspection"
    plans = planning["plans"]
    if len(realizations) != len(plans):
        raise ValueError("Compiler and Planner publication counts differ")
    for plan, realization in zip(plans, realizations, strict=True):
        for compiler_name, plan_name in (
            ("source_plan_id", "plan_id"),
            ("source_previous_plan_id", "previous_plan_id"),
            ("input_state_fingerprint", "input_state_fingerprint"),
            ("output_state_fingerprint", "output_state_fingerprint"),
            ("activation_timestamps", "activation_timestamps"),
        ):
            if realization[compiler_name] != plan[plan_name]:
                raise ValueError(
                    f"Compiler {compiler_name} differs from Planner lineage"
                )
        if realization["physical_world_fingerprint"] != planning[
            "physical_world_fingerprint"
        ]:
            raise ValueError("Compiler PhysicalWorld provenance differs")
        intended_state_timestamp = realization.get("intended_state_timestamp")
        if (
            intended_state_timestamp is not None
            and intended_state_timestamp != plan["end_timestamp"]
        ):
            raise ValueError("Compiler intended state has the wrong boundary")
    return {"source_kind": source_kind, "realizations": realizations}


def _read_telemetry(path: Path) -> dict[str, object]:
    rows = _read_jsonl(path)
    if not rows or rows[0].get("record_type") != "telemetry_header":
        raise ValueError("traffic telemetry must begin with one header")
    headers = _records(rows, "telemetry_header")
    anchors = _records(rows, "simulation_clock_anchor")
    if len(headers) != 1 or len(anchors) != 1:
        raise ValueError("traffic telemetry header/clock anchor cardinality is invalid")
    anchor = anchors[0]
    nominal_origin = _timestamp(anchor.get("nominal_origin"), "nominal_origin")
    monotonic_origin = _integer(
        anchor.get("monotonic_origin_ns"), "monotonic_origin_ns"
    )
    nodes = _records(rows, "node_identity")
    by_slot = {}
    by_address = {}
    by_node = {}
    for value in nodes:
        slot = _integer(value.get("node_slot"), "node_slot")
        node_id = _string(value.get("node_id"), "node_id")
        address = _string(value.get("runtime_ipv4"), "runtime_ipv4")
        if slot in by_slot or address in by_address or node_id in by_node:
            raise ValueError("traffic telemetry node identity is not unique")
        by_slot[slot] = dict(value)
        by_address[address] = dict(value)
        by_node[node_id] = dict(value)
    if not nodes:
        raise ValueError("traffic telemetry contains no node identities")
    packets = []
    for value in _records(rows, "packet"):
        packet = dict(value)
        monotonic = _integer(
            packet.get("kernel_monotonic_ns"), "kernel_monotonic_ns"
        )
        slot = _integer(packet.get("node_slot"), "node_slot")
        if slot not in by_slot:
            raise ValueError("packet references an unknown node slot")
        if not isinstance(packet.get("icmp"), dict):
            raise TypeError("packet ICMP evidence must be an object")
        for name in ("source_ipv4", "destination_ipv4"):
            _string(packet.get(name), name)
        packet["simulation_timestamp"] = (
            nominal_origin
            + timedelta(
                seconds=(monotonic - monotonic_origin) / 1_000_000_000
            )
        ).isoformat()
        packet["node_id"] = by_slot[slot]["node_id"]
        packets.append(packet)
    losses = _records(rows, "telemetry_loss")
    loss_total = sum(
        _integer(value.get("lost_events"), "lost_events") for value in losses
    )
    endings = _records(rows, "recorder_end")
    if len(endings) > 1:
        raise ValueError("traffic telemetry contains multiple recorder endings")
    complete = bool(
        len(endings) == 1
        and endings[0].get("complete") is True
        and loss_total == 0
    )
    interfaces = _read_interfaces(rows, by_slot=by_slot, by_node=by_node)
    return {
        "header": dict(headers[0]),
        "nominal_origin": nominal_origin.isoformat(),
        "monotonic_origin_ns": monotonic_origin,
        "nodes": tuple(sorted(by_slot.values(), key=lambda value: value["node_slot"])),
        "interfaces": interfaces,
        "packets": tuple(packets),
        "ping_transactions": _ping_transactions(
            tuple(packets), nodes_by_address=by_address
        ),
        "loss_records": tuple(dict(value) for value in losses),
        "recorder_end": None if not endings else dict(endings[0]),
        "telemetry_complete": complete,
        "telemetry_losses": loss_total,
        "raw_record_count": len(rows),
    }


def _read_interfaces(
    rows: tuple[dict[str, Any], ...],
    *,
    by_slot: dict[int, dict[str, Any]],
    by_node: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], ...]:
    interfaces = {}
    connection_ends: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(
        list
    )
    physical_identities: dict[tuple[int, int], tuple[str, str]] = {}
    for raw in _records(rows, "interface_identity"):
        value = dict(raw)
        generation = _integer(value.get("generation"), "generation")
        slot = _integer(value.get("node_slot"), "node_slot")
        ifindex = _integer(value.get("ifindex"), "ifindex")
        if ifindex == 0:
            raise ValueError("ifindex must be positive")
        node = by_slot.get(slot)
        if node is None:
            raise ValueError("interface identity references an unknown node slot")
        if value.get("node_id") != node["node_id"]:
            raise ValueError("interface identity contradicts its node slot")
        _timestamp(value.get("boundary_timestamp"), "boundary_timestamp")
        connection_id = _string(value.get("connection_id"), "connection_id")
        peer = _string(value.get("peer_node_id"), "peer_node_id")
        if peer not in by_node:
            raise ValueError("interface identity references an unknown peer node")
        if value.get("validity") != "exact-generation":
            raise ValueError("interface identity is not exact-generation evidence")
        key = generation, slot, ifindex
        if key in interfaces:
            raise ValueError("traffic telemetry interface identity is not unique")
        physical_key = slot, ifindex
        identity = connection_id, peer
        previous_identity = physical_identities.setdefault(
            physical_key, identity
        )
        if previous_identity != identity:
            raise ValueError(
                "physical interface identity reuse is ambiguous across generations"
            )
        interfaces[key] = value
        connection_ends[(generation, connection_id)].append(value)
    for ends in connection_ends.values():
        if len(ends) != 2:
            raise ValueError("interface Connection lacks two endpoints")
        first, second = ends
        if (
            first["node_id"] != second["peer_node_id"]
            or second["node_id"] != first["peer_node_id"]
        ):
            raise ValueError("interface Connection endpoints contradict peers")
    return tuple(interfaces[key] for key in sorted(interfaces))


def _validate_interface_generation_boundaries(
    interfaces: tuple[dict[str, Any], ...],
    formal_transitions: tuple[dict[str, Any], ...],
) -> None:
    boundaries: dict[int, str] = {}
    for value in interfaces:
        generation = value["generation"]
        boundary = value["boundary_timestamp"]
        previous = boundaries.setdefault(generation, boundary)
        if previous != boundary:
            raise ValueError("interface generation-to-boundary mapping conflicts")
    for generation, boundary in boundaries.items():
        if (
            generation >= len(formal_transitions)
            or formal_transitions[generation]["timestamp"] != boundary
        ):
            raise ValueError("interface generation-to-boundary mapping differs")


def _ping_transactions(
    packets: tuple[dict[str, Any], ...],
    *,
    nodes_by_address: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, object], ...]:
    grouped: dict[tuple[int, int, str, str], list[dict[str, Any]]] = {}
    for packet in packets:
        icmp = packet["icmp"]
        if packet.get("protocol") != 1 or not icmp.get("valid"):
            continue
        identifier = icmp.get("identifier")
        sequence = icmp.get("sequence")
        if not isinstance(identifier, int) or not isinstance(sequence, int):
            continue
        icmp_type = icmp.get("type")
        if icmp_type == 8:
            request_source = packet["source_ipv4"]
            request_destination = packet["destination_ipv4"]
        elif icmp_type == 0:
            request_source = packet["destination_ipv4"]
            request_destination = packet["source_ipv4"]
        else:
            continue
        grouped.setdefault(
            (
                identifier,
                sequence,
                request_source,
                request_destination,
            ),
            [],
        ).append(packet)
    transactions = []
    for (identifier, sequence, _, _), values in sorted(grouped.items()):
        ordered = sorted(values, key=lambda item: item["kernel_monotonic_ns"])
        if nodes_by_address is not None:
            filtered = []
            for icmp_type in (8, 0):
                direction = [
                    value
                    for value in ordered
                    if value["icmp"].get("type") == icmp_type
                ]
                anchors = [
                    value
                    for value in direction
                    if _is_endpoint_packet(value, nodes_by_address)
                ]
                filtered.extend(anchors or direction)
            ordered = sorted(
                filtered, key=lambda item: item["kernel_monotonic_ns"]
            )
        pending_requests = []
        for packet in ordered:
            if packet["icmp"].get("type") == 8:
                pending_requests.append(packet)
                continue
            if not pending_requests:
                transactions.append(
                    _ping_transaction(
                        identifier,
                        sequence,
                        request_packet=None,
                        reply_packet=packet,
                        pairing_status="unpaired",
                    )
                )
                continue
            for ambiguous in pending_requests[:-1]:
                transactions.append(
                    _ping_transaction(
                        identifier,
                        sequence,
                        request_packet=ambiguous,
                        reply_packet=None,
                        pairing_status="ambiguous",
                    )
                )
            transactions.append(
                _ping_transaction(
                    identifier,
                    sequence,
                    request_packet=pending_requests[-1],
                    reply_packet=packet,
                    pairing_status="paired",
                )
            )
            pending_requests.clear()
        transactions.extend(
            _ping_transaction(
                identifier,
                sequence,
                request_packet=request,
                reply_packet=None,
                pairing_status="unpaired",
            )
            for request in pending_requests
        )
    return tuple(transactions)


def _is_endpoint_packet(
    packet: dict[str, Any],
    nodes_by_address: dict[str, dict[str, Any]] | None,
) -> bool:
    if nodes_by_address is None:
        return True
    endpoint_address = packet["destination_ipv4"]
    endpoint = nodes_by_address.get(endpoint_address)
    return endpoint is not None and packet.get("node_id") == endpoint["node_id"]


def _ping_transaction(
    identifier: int,
    sequence: int,
    *,
    request_packet: dict[str, Any] | None,
    reply_packet: dict[str, Any] | None,
    pairing_status: str,
) -> dict[str, object]:
    return {
        "identifier": identifier,
        "sequence": sequence,
        "request": _ping_direction(request_packet),
        "reply": _ping_direction(reply_packet),
        "observed_reply": reply_packet is not None,
        "pairing_status": pairing_status,
        "packet_observations": int(request_packet is not None)
        + int(reply_packet is not None),
    }


def _ping_direction(value: dict[str, Any] | None) -> dict[str, str] | None:
    if value is None:
        return None
    return {
        "source_ipv4": value["source_ipv4"],
        "destination_ipv4": value["destination_ipv4"],
        "simulation_timestamp": value["simulation_timestamp"],
    }


def _read_jsonl(path: Path) -> tuple[dict[str, Any], ...]:
    rows = []
    for line_number, line in enumerate(
        Path(path).read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid JSON on line {line_number}: {path}") from error
        if not isinstance(value, dict):
            raise TypeError(f"JSONL rows must be objects: {path}")
        rows.append(value)
    return tuple(rows)


def _read_workload(path: Path) -> dict[str, Any]:
    value = _read_json(path, "Traffic workload")
    if (
        value.get("format_version") != 1
        or value.get("artifact_type")
        != "canonical-traffic-workload-schedule"
    ):
        raise ValueError("traffic replay requires canonical workload schedule data")
    authority = value.get("authority")
    if (
        not isinstance(authority, dict)
        or authority.get("status") != "planned-example-workload"
        or authority.get("packet_observation_authority")
        != "traffic-telemetry.jsonl"
    ):
        raise ValueError("traffic workload authority is invalid")
    for name in (
        "proves_command_execution",
        "proves_packet_emission",
        "proves_timeout",
        "proves_packet_loss",
    ):
        if authority.get(name) is not False:
            raise ValueError(
                "traffic workload claims unsupported runtime evidence"
            )
    return value


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"{label} is invalid JSON") from error
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be a JSON object")
    return value


def _records(
    rows: tuple[dict[str, Any], ...], record_type: str
) -> tuple[dict[str, Any], ...]:
    return tuple(value for value in rows if value.get("record_type") == record_type)


def _node(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {
        "asn",
        "entity_kind",
        "entity_id",
    }:
        raise ValueError("node identity is malformed")
    return {
        "asn": _integer(value["asn"], "asn"),
        "entity_kind": _string(value["entity_kind"], "entity_kind"),
        "entity_id": _string(value["entity_id"], "entity_id"),
    }


def _node_id(value: dict[str, object]) -> str:
    return f"{value['asn']}/{value['entity_kind']}/{value['entity_id']}"


def _timestamp(value: object, label: str) -> datetime:
    text = _string(value, label)
    try:
        return datetime.fromisoformat(text)
    except ValueError as error:
        raise ValueError(f"{label} must be an ISO timestamp") from error


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be numeric")
    converted = float(value)
    if converted != converted or converted in {float("inf"), float("-inf")}:
        raise ValueError(f"{label} must be finite")
    return converted


__all__ = ["TrafficReplayRead", "read_traffic_replay"]
