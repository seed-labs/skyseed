"""Read traffic telemetry, PhysicalFrame history, and planned intent evidence."""

from __future__ import annotations

from collections import defaultdict
from bisect import bisect_right
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
from tools.planner.reader import _iter_traffic_planner_history
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
    physical_link_inspection_path: Path | None = None,
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
    probe_execution = () if probe_execution_path is None else _read_jsonl(Path(probe_execution_path))
    telemetry = _read_telemetry(Path(telemetry_path), probe_execution=probe_execution)
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
        telemetry["interfaces"], planning["formal_transitions"], telemetry["boundaries"]
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
            "execution": probe_execution,
            "runner_events": read_runner_event_history(
                Path(runner_events_path)
            ),
        }
        if not qualification["execution"]:
            raise ValueError("Traffic probe execution is empty")
        completed = tuple(value for value in qualification["runner_events"]
                          if value.get("event") == "activation_end")
        if tuple((value.get("active_frontier"), value.get("active_generation"),
                  value.get("generation_publication_occurred"), value.get("preparation"))
                 for value in completed) != tuple(
            (value["boundary_timestamp"], value["active_generation"],
             value["generation_publication_occurred"], value["preparation"])
            for value in telemetry["boundaries"]
        ):
            raise ValueError("Runner and Traffic completed boundary histories differ")
    shell_nodes = ()
    if physical_link_inspection_path is not None:
        from contextlib import closing
        from tools._inspection_evidence import iter_physical_link_inspection
        with closing(iter_physical_link_inspection(Path(physical_link_inspection_path))) as records:
            header = next(records)
        if {r["node_id"] for r in header["nodes"]} != telemetry_nodes:
            raise ValueError("Traffic Shell metadata and telemetry nodes differ")
        shell_nodes = tuple(header["nodes"])
    elif qualification is not None:
        membership = {r["node_id"]: r["shell_id"] for r in qualification["plan"].get("shell_metadata", ())}
        shell_nodes = tuple({"node_id": r["node_id"], "kind": next(p["node"]["entity_kind"] for p in physical["frames"][0]["positions"] if p["node_id"] == r["node_id"]),
                             "shell_id": membership.get(r["node_id"])} for r in telemetry["nodes"])
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
            "shell_nodes": shell_nodes,
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
    for plan_index, value in enumerate(_iter_traffic_planner_history(path)):
        plan = value.plan
        input_fingerprint = None if state is None else planned_operational_state_fingerprint(state)
        if input_fingerprint != plan.input_state_fingerprint:
            raise ValueError("Traffic Tool replay differs from Plan input fingerprint")
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
        if state is None or planned_operational_state_fingerprint(state) != plan.output_state_fingerprint:
            raise ValueError("Traffic Tool replay differs from Plan output fingerprint")
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
        format_version = artifact.get("format_version")
        if format_version in {3, 4}:
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
            source_kind = f"compact-compiler-inspection-v{format_version}"
        elif format_version == 2:
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
        else:
            raise ValueError(
                "Compiler inspection format_version must be one of 2, 3, or 4"
            )
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


def _read_telemetry(path: Path, *, probe_execution=()) -> dict[str, object]:
    # Two streaming passes keep packet/loss facts and at most one generation
    # of expanded identity rows, never the complete raw identity history.
    rows = []
    raw_record_count = 0
    for value in _iter_jsonl(path):
        raw_record_count += 1
        if value.get("record_type") not in {"interface_identity", "interface_generation", "interface_update"}:
            rows.append(value)
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
    interfaces = _compact_interfaces(_iter_interface_records(path), by_slot=by_slot, by_node=by_node)
    boundaries = _read_boundary_activations(rows)
    completed = [value["main_end_monotonic_ns"] for value in boundaries]
    starts = [value["main_start_monotonic_ns"] for value in boundaries]
    generations = {value["active_generation"]: value for value in boundaries
                   if value["generation_publication_occurred"]}
    interface_index = defaultdict(list)
    for endpoint in interfaces["endpoints"]:
        for first, last, ifindex in endpoint["incarnations"]:
            interface_index[(endpoint["node_slot"], ifindex)].append((first, last, endpoint))
    for values in interface_index.values():
        values.sort(key=lambda value: value[0])
    def resolve(generation, physical):
        values = interface_index.get(physical, ())
        if generation is None:
            return None
        index = bisect_right(values, generation, key=lambda value: value[0]) - 1
        return values[index][2] if index >= 0 and generation <= values[index][1] else None
    for packet in packets:
        observed = packet["kernel_monotonic_ns"]
        active_index = bisect_right(completed, observed) - 1
        changing_index = bisect_right(starts, observed) - 1
        packet["formal_boundary_timestamp"] = (
            None if active_index < 0 else boundaries[active_index]["boundary_timestamp"]
        )
        packet["transition_boundary_timestamp"] = (
            boundaries[changing_index]["boundary_timestamp"]
            if changing_index > active_index else None
        )
        generation = generations.get(packet.get("generation"))
        packet["generation_boundary_timestamp"] = (
            None if generation is None or observed < generation["generation_switch_start_monotonic_ns"] else generation["boundary_timestamp"]
        )
        for direction in ("ingress", "egress"):
            physical = (packet["node_slot"], packet.get(
                "ingress_ifindex" if direction == "ingress" else "selected_egress_ifindex"))
            identity = resolve(packet.get("generation"), physical)
            validity = "exact-generation"
            if changing_index > active_index:
                transition = boundaries[changing_index]
                # Add-before-withdraw keeps both endpoint sets physically live
                # during this boundary. Never search unrelated past/future
                # generations: a later incarnation may reuse an ifindex.
                candidates = [value for selected in {
                    transition["previous_generation"], transition["active_generation"]
                } if (value := resolve(selected, physical)) is not None]
                if len({value["logical_port_id"] for value in candidates}) > 1:
                    raise ValueError("in-flight physical interface identity is ambiguous")
                if identity is None and candidates:
                    identity = candidates[0]
                    validity = "in-flight-boundary"
            packet[f"{direction}_identity"] = None if identity is None else {
                **{name: identity[name] for name in ("connection_id", "peer_node_id", "logical_port_id")},
                "validity": validity,
            }
    return {
        "header": dict(headers[0]),
        "nominal_origin": nominal_origin.isoformat(),
        "monotonic_origin_ns": monotonic_origin,
        "nodes": tuple(sorted(by_slot.values(), key=lambda value: value["node_slot"])),
        "interfaces": interfaces,
        "boundaries": boundaries,
        "packets": tuple(packets),
        "ping_transactions": _ping_transactions(
            tuple(packets), nodes_by_address=by_address, probe_execution=probe_execution
        ),
        "loss_records": tuple(dict(value) for value in losses),
        "recorder_end": None if not endings else dict(endings[0]),
        "telemetry_complete": complete,
        "telemetry_losses": loss_total,
        "raw_record_count": raw_record_count,
    }


def _iter_interface_records(path):
    """Expand v2 deltas lazily; retain support for original v1 identity rows."""
    live = {}
    pending = None
    for row in _iter_jsonl(path):
        kind = row.get("record_type")
        if kind == "interface_identity":
            yield row
        elif kind == "interface_update":
            port = _integer(row.get("logical_port_id"), "logical_port_id")
            live[port] = {**live.get(port, {}), **{k: v for k, v in row.items() if k != "record_type"}}
            pending = True
        elif kind == "interface_generation":
            for port in row.get("withdrawn", ()):
                if port not in live:
                    raise ValueError("interface delta withdraws an unknown logical port")
                del live[port]
            for value in live.values():
                yield {"record_type": "interface_identity", **value,
                       "generation": row["generation"], "boundary_timestamp": row["boundary_timestamp"]}
            pending = None
    if pending is not None:
        raise ValueError("incomplete interface generation delta")


def _compact_interfaces(rows, *, by_slot, by_node):
    from itertools import groupby
    endpoints, generations = {}, []
    endpoint_ports = {}
    destination_sets, destination_indexes = [], {}
    previous_generation = -1
    observations = 0
    for generation, group in groupby(rows, key=lambda row: row.get("generation")):
        if not isinstance(generation, int) or generation <= previous_generation:
            raise ValueError("interface generation rows are not ordered uniquely")
        previous_generation = generation
        # All legacy validation is retained, bounded to one generation.
        values = _read_interfaces(tuple(group), by_slot=by_slot, by_node=by_node)
        boundaries = {value["boundary_timestamp"] for value in values}
        if len(boundaries) != 1:
            raise ValueError("interface generation has inconsistent boundaries")
        generations.append((generation, next(iter(boundaries))))
        for value in values:
            observations += 1
            port = value["logical_port_id"]
            static = {k: v for k, v in value.items() if k not in {
                "generation", "boundary_timestamp", "ifindex", "record_type", "route_destinations"}}
            identity = value["node_id"], value["connection_id"], value["peer_node_id"]
            if endpoint_ports.setdefault(identity, port) != port:
                raise ValueError("logical port changed for the same connection endpoint")
            previous = endpoints.get(port)
            if previous is None:
                previous = endpoints[port] = {**static, "incarnations": [], "route_destination_ranges": []}
            elif {k: v for k, v in previous.items() if k not in {"incarnations", "route_destination_ranges"}} != static:
                raise ValueError("logical interface identity reuse contradicts its endpoint")
            if "route_destinations" in value:
                destinations = tuple(value["route_destinations"])
                destination_index = destination_indexes.get(destinations)
                if destination_index is None:
                    destination_index = len(destination_sets)
                    destination_indexes[destinations] = destination_index
                    destination_sets.append(destinations)
                ranges = previous["route_destination_ranges"]
                if ranges and ranges[-1][1] == generation-1 and ranges[-1][2] == destination_index:
                    ranges[-1][1] = generation
                else:
                    ranges.append([generation, generation, destination_index])
            ranges = previous["incarnations"]
            if ranges and ranges[-1][1] == generation-1 and ranges[-1][2] == value["ifindex"]:
                ranges[-1][1] = generation
            else:
                ranges.append([generation, generation, value["ifindex"]])
    return {"format_version": 2, "generations": generations,
            "endpoints": tuple(endpoints[port] for port in sorted(endpoints)),
            "identity_observations": observations, "route_destination_sets": destination_sets}


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
    logical_identities: dict[int, tuple[str, str, str]] = {}
    endpoint_ports: dict[tuple[str, str, str], int] = {}
    generation_ports = set()
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
        port = _integer(value.get("logical_port_id"), "logical_port_id")
        if not 0 < port <= 0xFFFFFFFF:
            raise ValueError("logical port must be a nonzero uint32")
        if peer not in by_node:
            raise ValueError("interface identity references an unknown peer node")
        if value.get("validity") != "exact-generation":
            raise ValueError("interface identity is not exact-generation evidence")
        key = generation, slot, ifindex
        if key in interfaces:
            raise ValueError("traffic telemetry interface identity is not unique")
        identity = value["node_id"], connection_id, peer
        previous_identity = logical_identities.setdefault(port, identity)
        if previous_identity != identity:
            raise ValueError("logical interface identity reuse contradicts its endpoint")
        if endpoint_ports.setdefault(identity, port) != port:
            raise ValueError("logical port changed for the same connection endpoint")
        if (generation, port) in generation_ports:
            raise ValueError("generation logical interface identity is not unique")
        generation_ports.add((generation, port))
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
    activations: tuple[dict[str, Any], ...],
) -> None:
    if tuple(value["boundary_timestamp"] for value in activations) != tuple(
        value["timestamp"] for value in formal_transitions[:len(activations)]
    ):
        raise ValueError("Runner completed boundary prefix differs from formal history")
    boundaries = {value["active_generation"]: value["boundary_timestamp"]
                  for value in activations if value["generation_publication_occurred"]}
    identities = (interfaces["generations"] if isinstance(interfaces, dict) else
                  ((v["generation"], v["boundary_timestamp"]) for v in interfaces))
    for generation, boundary in identities:
        if boundaries.get(generation) != boundary:
            raise ValueError("interface generation-to-boundary mapping differs")


def _read_boundary_activations(rows: tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], ...]:
    """Read completed nominal boundaries separately from publication lifetimes."""
    values = _records(rows, "boundary_activation")
    previous_generation = 0xFFFFFFFF
    previous_end = 0
    for index, value in enumerate(values):
        _timestamp(value.get("boundary_timestamp"), "boundary_timestamp")
        generation = _integer(value.get("active_generation"), "active_generation")
        start = _integer(value.get("main_start_monotonic_ns"), "main_start_monotonic_ns")
        end = _integer(value.get("main_end_monotonic_ns"), "main_end_monotonic_ns")
        published = value.get("generation_publication_occurred")
        if (type(published) is not bool or value.get("previous_generation") != previous_generation
            or value.get("preparation") is not (index == 0)
            or start < previous_end or end < start):
            raise ValueError("Runner boundary progression/timing evidence differs")
        expected = (0 if index == 0 else previous_generation + 1) if published else previous_generation
        if generation != expected or (index == 0 and not published):
            raise ValueError("Runner generation lifetime contradicts publication")
        switch_names = ("generation_switch_start_monotonic_ns", "generation_switch_end_monotonic_ns")
        if published:
            switch_start, switch_end = (_integer(value.get(name), name) for name in switch_names)
            if not start <= switch_start <= switch_end <= end:
                raise ValueError("Runner publication timing lies outside its boundary")
        elif any(name in value for name in switch_names):
            raise ValueError("reused generation must not invent publication timing")
        previous_generation, previous_end = generation, end
    if not values:
        raise ValueError("traffic telemetry lacks completed boundary evidence")
    return tuple(values)


def _ping_transactions(
    packets: tuple[dict[str, Any], ...],
    *,
    nodes_by_address: dict[str, dict[str, Any]] | None = None,
    probe_execution: tuple[dict[str, Any], ...] = (),
) -> tuple[dict[str, object], ...]:
    """Associate probes, never count forwarding hops as independent sends.

    A generator lifetime disambiguates identifier reuse. Without that evidence,
    repeated endpoint observations (or sequence wrap) remain an uncertain group.
    Replies after identifier reuse are not assigned to the newest request merely
    because it is closest. Packet indices retain every observation exactly once.
    """
    launches = defaultdict(list)
    stops = {(r.get("episode_id"), r.get("segment_index")): r
             for r in probe_execution if r.get("record_type") == "generator_stopped"}
    for r in probe_execution:
        if r.get("record_type") == "generator_launched":
            launches[(r["icmp_identifier"], r["source_ipv4"], r["destination_ipv4"])].append(r)
    grouped = defaultdict(list)
    for index, packet in enumerate(packets):
        icmp = packet["icmp"]
        if (packet.get("protocol") != 1 or not icmp.get("valid")
            or not isinstance(icmp.get("identifier"), int)
            or not isinstance(icmp.get("sequence"), int)
            or icmp.get("type") not in (8, 0)):
            continue
        source, destination = packet["source_ipv4"], packet["destination_ipv4"]
        if icmp["type"] == 0:
            source, destination = destination, source
        grouped[(icmp["identifier"], icmp["sequence"], source, destination)].append(index)
    transactions = []
    for (identifier, sequence, source, destination), indices in sorted(grouped.items()):
        indices.sort(key=lambda i: packets[i]["kernel_monotonic_ns"])
        generators = launches.get((identifier, source, destination), ())
        buckets = {}
        def bucket(key):
            return buckets.setdefault(key, {"requests": [], "replies": [], "uncertainty": []})
        for i in indices:
            packet = packets[i]
            if packet["icmp"]["type"] != 8:
                continue
            at = packet["kernel_monotonic_ns"]
            matches = []
            for g in generators:
                key = (g["episode_id"], g["segment_index"])
                stop = stops.get(key, {})
                if g.get("launch_monotonic_ns", g["host_monotonic_ns"]) <= at <= stop.get("stop_monotonic_ns", stop.get("host_monotonic_ns", float("inf"))):
                    matches.append(key)
            key = matches[0] if len(matches) == 1 else None
            if not matches:
                # A stop prevents new emissions, not forwarding of an already
                # observed request. Extend only an established unique identity
                # with no intervening launch that could have reused the key.
                preceding = [k for k, value in buckets.items() if k is not None
                             and value["requests"] and not value["uncertainty"]]
                if len(preceding) == 1:
                    prior_key = preceding[0]
                    first_at = packets[buckets[prior_key]["requests"][0]]["kernel_monotonic_ns"]
                    competing = any((g["episode_id"], g["segment_index"]) != prior_key
                                    and first_at <= g.get("launch_monotonic_ns", g["host_monotonic_ns"]) <= at
                                    for g in generators)
                    if not competing:
                        key = prior_key
            b = bucket(key)
            b["requests"].append(i)
            if generators and (len(matches) > 1 or key is None):
                b["uncertainty"].append("request generator lifetime is missing or overlapping")
        # Distinct generator lifetimes give distinct requests. A late reply can
        # cross stop/migration; retain it, but never guess across reused keys.
        for i in indices:
            if packets[i]["icmp"]["type"] != 0:
                continue
            at = packets[i]["kernel_monotonic_ns"]
            candidates = [key for key, b in buckets.items() if b["requests"]
                          and packets[b["requests"][0]]["kernel_monotonic_ns"] <= at]
            if len(candidates) == 1:
                bucket(candidates[0])["replies"].append(i)
            else:
                b = bucket(("unresolved-reply", None))
                b["replies"].append(i)
                b["uncertainty"].append("reply has no unique preceding request lifetime")
                for key in candidates:
                    bucket(key)["uncertainty"].append("reply association overlaps identifier/sequence reuse")
        for key, b in buckets.items():
            requests, replies = b["requests"], b["replies"]
            g = next((g for g in generators if (g["episode_id"], g["segment_index"]) == key), None)
            if g is not None and stops.get(key, {}).get("sent", 0) > 65535:
                b["uncertainty"].append("sequence wrap cannot be resolved from recorded ICMP fields")
            if g is None and requests:
                endpoints = {packets[i]["kernel_monotonic_ns"] for i in requests
                             if _is_endpoint_packet(packets[i], nodes_by_address)}
                if len(endpoints) > 1 or (len(requests) > 1 and any(not packets[i].get("node_id") for i in requests)):
                    b["uncertainty"].append("repeated requests lack generator/payload identity")
            # First observed request is a lower bound on emission, not the last
            # hop or the later executor timeout/report timestamp.
            request = None if not requests else _ping_direction(packets[requests[0]])
            reply = None if not replies else _ping_direction(packets[replies[0]])
            delivered = any(_is_endpoint_packet(packets[i], nodes_by_address) for i in replies)
            executor_reply = bool(g is not None and sequence in stops.get(key, {}).get("reply_sequences", ()))
            uncertain = sorted(set(b["uncertainty"]))
            transactions.append({
                "identifier": identifier, "sequence": sequence,
                "request": request, "reply": reply,
                "observed_reply": bool(replies),
                "reply_delivery": "endpoint-observed" if delivered else "executor-confirmed" if executor_reply else "unconfirmed",
                "pairing_status": "ambiguous" if uncertain else "paired" if requests and replies else "unpaired",
                "association_uncertainty": uncertain,
                "generator": None if g is None else {k: g.get(k) for k in ("episode_id", "probe_id", "segment_index", "runtime_identity", "physical_source_node_id")},
                "request_packet_indices": requests, "reply_packet_indices": replies,
                "packet_observations": len(requests) + len(replies),
            })
    return tuple(transactions)


def _is_endpoint_packet(
    packet: dict[str, Any],
    nodes_by_address: dict[str, dict[str, Any]] | None,
) -> bool:
    # local-route is the observed kernel delivery decision, including Zone IPs
    # on the serving satellite. No fixed-address lookup can represent migration.
    if packet.get("decision") == "local/pass" and packet.get("reason") == "local-route":
        return True
    if nodes_by_address is None:
        return not packet.get("node_id")
    endpoint = nodes_by_address.get(packet["destination_ipv4"])
    return endpoint is not None and packet.get("node_id") == endpoint["node_id"]


def _ping_direction(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "source_ipv4": value["source_ipv4"],
        "destination_ipv4": value["destination_ipv4"],
        "simulation_timestamp": value["simulation_timestamp"],
        **{key: value.get(key) for key in (
            "kernel_monotonic_ns", "formal_boundary_timestamp", "transition_boundary_timestamp",
            "generation", "generation_boundary_timestamp", "ingress_identity", "egress_identity",
        )},
    }


def _read_jsonl(path: Path) -> tuple[dict[str, Any], ...]:
    return tuple(_iter_jsonl(path))


def _iter_jsonl(path: Path):
    with Path(path).open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"invalid JSON on line {line_number}: {path}") from error
            if not isinstance(value, dict):
                raise TypeError(f"JSONL rows must be objects: {path}")
            yield value


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
