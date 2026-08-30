"""Read durable traffic, Position, and compiled boundary evidence."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from ipaddress import IPv4Interface, ip_interface
import json
from math import ceil
from pathlib import Path
from typing import Any

from tools._chunk_encoding import decode_json_chunk
from tools.runner.reader import read_runner_event_history


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
    probe_plan_path: Path | None = None,
    probe_execution_path: Path | None = None,
    runner_events_path: Path | None = None,
) -> TrafficReplayRead:
    """Read and structurally validate one replay evidence set."""

    telemetry = Path(telemetry_path)
    dynamic_path = Path(slice_path)
    compiler_path = Path(bpf_compiler_path)
    workload_path = None if workload_path is None else Path(workload_path)
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
    physical_interfaces: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
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
        physical_interfaces[(slot, ifindex)].append(dict(value))
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
            ingress_identity = _inactive_ingress_identity(
                packet,
                physical_interfaces.get((slot, ingress), ()),
            )
            if ingress_identity is None:
                raise ValueError("packet lacks exact-generation ingress identity")
        else:
            ingress_identity = {
                **ingress_identity,
                "packet_identity_validity": "exact-generation",
                "identity_generations": (generation,),
            }
        if ingress_identity["node_id"] != nodes_by_slot[slot]["node_id"]:
            raise ValueError("packet ingress identity contradicts its node slot")
        packet["ingress_identity"] = ingress_identity
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

    dynamic = _read_slice_inspection(dynamic_path)
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

    compiler = _read_compiler_inspection(compiler_path)
    compiler_evidence = _read_compiler_evidence(
        compiler,
        dynamic_origin=dynamic_origin,
        dynamic_end=dynamic_end,
        dynamic_nodes=dynamic_nodes,
        dynamic_zone_ids=set(dynamic_zones),
    )
    qualification = None
    runner_events: tuple[dict[str, Any], ...] | None = None
    if probe_plan_path is not None:
        plan = _read_probe_plan(Path(probe_plan_path))
        execution = _read_probe_execution(
            Path(probe_execution_path), plan  # type: ignore[arg-type]
        )
        runner_events = read_runner_event_history(
            Path(runner_events_path)  # type: ignore[arg-type]
        )
        _validate_qualification_alignment(
            plan,
            execution,
            runner_events,
            telemetry_origin=telemetry_origin,
        )
        generation_networks = _published_generation_networks(
            generation_networks,
            runner_events,
            packets,
        )
        qualification = {
            "plan": plan,
            "execution": execution,
            "runner_events": runner_events,
        }
    generation_networks = _align_generation_networks(
        generation_networks,
        compiler_evidence["nominal_boundaries"],
        compiler_evidence["zone_events"],
        allow_prefix=qualification is not None,
    )
    active_boundary_by_generation = {
        int(value["generation"]): value["boundary_timestamp"]
        for value in generation_networks
    }
    for packet in packets:
        packet["active_boundary_timestamp"] = active_boundary_by_generation[
            int(packet["generation"])
        ]
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
            "qualification": qualification,
            "raw_records": tuple(dict(value) for value in records),
        }
    )


def _read_probe_plan(path: Path) -> dict[str, Any]:
    value = _read_json(path, "Traffic probe plan")
    horizon = _mapping(value.get("planning_horizon"), "probe planning horizon")
    planning = _mapping(value.get("planning"), "probe planning")
    if (
        value.get("artifact_type") != "traffic-probe-plan"
        or value.get("format_version") != 1
        or value.get("runtime_authority") is not False
        or value.get("classification_scope")
        != "authoritative-active-published-runtime-only"
        or horizon.get("terminality") != "unknown"
        or horizon.get("revision_semantics") != "revisable-lookahead"
        or planning.get("selection_strategy") != "bounded-stratified-reservoir"
        or planning.get("global_flow_ranking") is not False
    ):
        raise ValueError("Traffic probe plan is incompatible")
    identities = _object_list(value.get("probe_identities"), "probe identities")
    episodes = _object_list(value.get("episodes"), "probe episodes")
    if planning.get("selected_probe_identity_count") != len(identities):
        raise ValueError("Traffic probe identity count differs")
    if planning.get("selected_episode_count") != len(episodes):
        raise ValueError("Traffic probe episode count differs")
    _parse_timestamp(horizon.get("coverage_start_time"), "probe coverage start")
    _parse_timestamp(horizon.get("coverage_end_time"), "probe coverage end")
    _non_empty_string(horizon.get("revision_id"), "probe revision_id")
    return value


def _read_probe_execution(
    path: Path, plan: Mapping[str, Any]
) -> tuple[dict[str, Any], ...]:
    records = _read_json_lines(path)
    if not records or records[0].get("record_type") != "execution_header":
        raise ValueError("Traffic probe execution must begin with one header")
    header = records[0]
    horizon = _mapping(plan.get("planning_horizon"), "probe planning horizon")
    if (
        header.get("artifact_type") != "traffic-probe-execution"
        or header.get("format_version") != 1
        or header.get("plan_revision_id") != horizon.get("revision_id")
        or header.get("classification_scope")
        != "authoritative-active-published-runtime-only"
    ):
        raise ValueError("Traffic probe execution header is incompatible")
    identities = {
        _non_empty_string(value.get("probe_id"), "probe_id")
        for value in _object_list(plan.get("probe_identities"), "probe identities")
    }
    episodes = {
        _non_empty_string(value.get("episode_id"), "episode_id")
        for value in _object_list(plan.get("episodes"), "probe episodes")
    }
    episode_probe = {
        _non_empty_string(value.get("episode_id"), "episode_id"):
        _non_empty_string(value.get("probe_id"), "episode probe_id")
        for value in _object_list(plan.get("episodes"), "probe episodes")
    }
    executor_ends = []
    launched = set()
    stopped = set()
    observed_segments = set()
    terminal_episodes = set()
    allowed_record_types = {
        "executor_start",
        "clock_alignment",
        "published_frontier_observed",
        "episode_armed",
        "episode_paused",
        "episode_completed",
        "episode_cancelled",
        "episode_not_run",
        "generator_launched",
        "generator_stopped",
        "ping_observation",
        "zone_source_migration",
        "executor_failure",
        "executor_end",
    }
    for value in records[1:]:
        record_type = _non_empty_string(
            value.get("record_type"), "probe execution record_type"
        )
        if record_type not in allowed_record_types:
            raise ValueError("Traffic probe execution record type is unsupported")
        _positive_int(value.get("host_monotonic_ns"), "host_monotonic_ns")
        _parse_timestamp(value.get("wall_time"), "probe execution wall_time")
        probe_id = value.get("probe_id")
        episode_id = value.get("episode_id")
        if probe_id is not None and probe_id not in identities:
            raise ValueError("Traffic probe execution references an unknown probe")
        if episode_id is not None and episode_id not in episodes:
            raise ValueError("Traffic probe execution references an unknown episode")
        if episode_id is not None and probe_id != episode_probe[episode_id]:
            raise ValueError("Traffic probe execution episode/probe identity differs")
        if record_type == "generator_launched":
            key = (
                episode_id,
                _positive_int(value.get("segment_index"), "segment_index"),
            )
            if key in launched:
                raise ValueError("Traffic generator launch identity is duplicated")
            launched.add(key)
            _positive_int(value.get("icmp_identifier"), "icmp_identifier")
            _non_empty_string(value.get("physical_source_container_id"), "container")
            _parse_timestamp(
                value.get("published_active_frontier"),
                "generator published active frontier",
            )
        elif record_type == "generator_stopped":
            key = (
                episode_id,
                _positive_int(value.get("segment_index"), "segment_index"),
            )
            if key in stopped:
                raise ValueError("Traffic generator stop identity is duplicated")
            if key not in launched:
                raise ValueError("Traffic generator stopped without a launch")
            stopped.add(key)
            _non_negative_int(value.get("sent"), "sent")
            _non_negative_int(value.get("received"), "received")
        elif record_type == "ping_observation":
            key = (
                episode_id,
                _positive_int(value.get("segment_index"), "segment_index"),
            )
            observed_segments.add(key)
            if not isinstance(value.get("raw_line"), str):
                raise ValueError("Traffic Ping observation lacks retained stdout")
            _parse_timestamp(
                value.get("published_active_frontier"),
                "Ping published active frontier",
            )
            sequence = value.get("icmp_sequence")
            if sequence is not None:
                _positive_int(sequence, "icmp_sequence")
            rtt = value.get("rtt_ms")
            if rtt is not None:
                _non_negative_number(rtt, "rtt_ms")
        elif record_type == "zone_source_migration":
            start = _positive_int(
                value.get("migration_start_monotonic_ns"), "migration start"
            )
            end = _positive_int(
                value.get("migration_end_monotonic_ns"), "migration end"
            )
            if end < start:
                raise ValueError("Traffic Zone migration bracket is reversed")
        elif record_type in {
            "episode_completed",
            "episode_cancelled",
            "episode_not_run",
        }:
            if episode_id in terminal_episodes:
                raise ValueError("Traffic probe episode has multiple dispositions")
            terminal_episodes.add(episode_id)
            if value.get("plan_revision_id") != horizon.get("revision_id"):
                raise ValueError("Traffic episode disposition has wrong revision")
            rearmable = value.get("rearmable")
            if rearmable is not (record_type != "episode_completed"):
                raise ValueError("Traffic episode re-arm semantics are invalid")
        elif record_type == "executor_end":
            executor_ends.append(value)
    if len(executor_ends) != 1:
        raise ValueError("Traffic probe execution requires one executor ending")
    if records[-1].get("record_type") != "executor_end":
        raise ValueError("Traffic probe executor ending must be final")
    if launched != stopped:
        raise ValueError("Traffic probe execution leaves generator evidence incomplete")
    if not observed_segments <= launched:
        raise ValueError("Traffic Ping observation lacks a generator launch")
    if terminal_episodes != episodes:
        raise ValueError("Traffic probe execution leaves episode disposition incomplete")
    executor_end = executor_ends[0]
    if (
        not isinstance(executor_end.get("complete"), bool)
        or not isinstance(executor_end.get("cleanup_complete"), bool)
        or _non_negative_int(
            executor_end.get("active_generators"), "active_generators"
        )
        != 0
    ):
        raise ValueError("Traffic probe executor ending is incomplete")
    return tuple(dict(value) for value in records)


def _validate_qualification_alignment(
    plan: Mapping[str, Any],
    execution: tuple[dict[str, Any], ...],
    runner_events: tuple[dict[str, Any], ...],
    *,
    telemetry_origin: datetime,
) -> None:
    horizon = _mapping(plan.get("planning_horizon"), "probe planning horizon")
    if _parse_timestamp(
        horizon.get("coverage_start_time"), "probe coverage start"
    ) != telemetry_origin:
        raise ValueError("Traffic probe plan and telemetry origins differ")
    starts = [
        value for value in runner_events if value.get("event") == "automatic_run_start"
    ]
    if len(starts) != 1 or starts[0].get("nominal_origin") != telemetry_origin.isoformat():
        raise ValueError("Traffic qualification Runner origin differs")
    for value in runner_events:
        if value.get("event") != "activation_end":
            continue
        start = _positive_int(
            value.get("generation_switch_start_monotonic_ns"),
            "generation switch start",
        )
        end = _positive_int(
            value.get("generation_switch_end_monotonic_ns"),
            "generation switch end",
        )
        if end < start:
            raise ValueError("Runner generation switch bracket is reversed")
        duration = _non_negative_number(
            value.get("activation_switch_seconds"), "activation switch duration"
        )
        if abs(duration - (end - start) / 1_000_000_000) > 1e-9:
            raise ValueError("Runner generation switch duration differs from bracket")
    published_runner_frontiers = {
        value.get("active_frontier")
        for value in runner_events
        if value.get("event") == "activation_end"
    }
    for value in execution:
        published = value.get("published_active_frontier")
        if published is not None and published not in published_runner_frontiers:
            raise ValueError(
                "Traffic execution uses a frontier not published by Runner"
            )
        if value.get("record_type") == "published_frontier_observed":
            active = value.get("active_frontier")
            _parse_timestamp(active, "observed published frontier")
            if active not in published_runner_frontiers:
                raise ValueError(
                    "Traffic execution observed an unpublished Runner frontier"
                )
    executor_end = next(
        value for value in execution if value.get("record_type") == "executor_end"
    )
    if executor_end.get("plan_revision_id") != horizon.get("revision_id"):
        raise ValueError("Traffic executor ending has the wrong plan revision")


def _object_list(value: object, label: str) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    return tuple(_mapping(item, label) for item in value)


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return dict(value)


def _non_negative_number(value: object, label: str) -> float:
    result = _finite_number(value, label)
    if result < 0:
        raise ValueError(f"{label} must be non-negative")
    return result


def _inactive_ingress_identity(
    packet: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    """Resolve only an unambiguous physically present but inactive ingress."""

    if (
        packet.get("decision") != "drop"
        or packet.get("reason") != "inactive-ingress"
        or packet.get("selected_egress_ifindex") is not None
        or not candidates
    ):
        return None
    signatures: dict[tuple[object, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for value in candidates:
        signatures[
            (
                value.get("node_id"),
                value.get("ifindex"),
                value.get("interface"),
                value.get("connection_id"),
                value.get("peer_node_id"),
            )
        ].append(value)
    if len(signatures) != 1:
        return None
    matching = next(iter(signatures.values()))
    identity = dict(matching[0])
    identity["packet_identity_validity"] = (
        "unique-observed-physical-inactive-ingress"
    )
    identity["identity_generations"] = tuple(
        sorted(int(value["generation"]) for value in matching)
    )
    return identity


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


def _published_generation_networks(
    generation_networks: tuple[dict[str, Any], ...],
    runner_events: tuple[dict[str, Any], ...],
    packets: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Restrict qualification topology to authoritative published ACTIVE state."""

    available = {
        int(value["generation"]): value for value in generation_networks
    }
    published: dict[int, str] = {}
    for value in runner_events:
        if value.get("event") != "activation_end":
            continue
        generation = _non_negative_int(
            value.get("active_generation"), "active_generation"
        )
        frontier = _parse_timestamp(
            value.get("active_frontier"), "active_frontier"
        ).isoformat()
        if generation in published:
            raise ValueError("Runner publishes an ACTIVE generation twice")
        published[generation] = frontier
    if not published or sorted(published) != list(range(len(published))):
        raise ValueError("Runner ACTIVE generations are not one continuous prefix")
    result = []
    for generation in sorted(published):
        network = available.get(generation)
        if (
            network is None
            or network.get("boundary_timestamp") != published[generation]
        ):
            raise ValueError(
                "Runner ACTIVE generation contradicts telemetry identity evidence"
            )
        result.append(network)
    if any(int(value["generation"]) not in published for value in packets):
        raise ValueError("packet references a generation not published ACTIVE")
    return tuple(result)


def _align_generation_networks(
    generation_networks: tuple[dict[str, Any], ...],
    nominal_boundaries: tuple[str, ...],
    zone_events: tuple[dict[str, Any], ...],
    *,
    allow_prefix: bool = False,
) -> tuple[dict[str, Any], ...]:
    if len(generation_networks) > len(nominal_boundaries) or (
        not allow_prefix and len(generation_networks) != len(nominal_boundaries)
    ):
        raise ValueError("generation and nominal boundary counts differ")
    events_by_time: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in zone_events:
        events_by_time[event["nominal_time"]].append(event)
    services: dict[str, str] = {}
    result = []
    for index, (network, boundary) in enumerate(
        zip(
            generation_networks,
            nominal_boundaries[: len(generation_networks)],
            strict=True,
        )
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


def _read_slice_inspection(path: Path) -> dict[str, Any]:
    """Read either the legacy monolith or the offline chunked Slice bundle."""

    dynamic = _read_json(path, "Slice / Difference")
    manifest = dynamic.get("slice_manifest")
    if "slices" in dynamic or manifest is None:
        return dynamic
    if not isinstance(manifest, list):
        raise ValueError("Slice / Difference chunk manifest is invalid")
    slices = []
    for entry in manifest:
        if not isinstance(entry, dict):
            raise ValueError("Slice / Difference chunk entry is invalid")
        slice_id = _non_negative_int(entry.get("slice_id"), "chunk slice_id")
        relative = entry.get("chunk")
        if not isinstance(relative, str) or not relative:
            raise ValueError("Slice / Difference chunk path is invalid")
        chunk_path = path.parent / relative
        try:
            source = chunk_path.read_text(encoding="utf-8")
        except OSError as error:
            raise RuntimeError(
                f"cannot read Slice / Difference chunk: {chunk_path}"
            ) from error
        marker = f"window.__SKYSEED_SLICE_CHUNKS__[{slice_id}]="
        marker_index = source.find(marker)
        if marker_index < 0 or not source.endswith(";\n"):
            raise ValueError("Slice / Difference chunk wrapper is invalid")
        payload = source[marker_index + len(marker) : -2]
        try:
            value = decode_json_chunk(json.loads(payload))
        except (json.JSONDecodeError, ValueError) as error:
            raise ValueError("Slice / Difference chunk JSON is invalid") from error
        if not isinstance(value, dict) or value.get("slice_id") != slice_id:
            raise ValueError("Slice / Difference chunk identity is invalid")
        slices.append(value)
    dynamic["slices"] = slices
    return dynamic


def _read_compiler_inspection(path: Path) -> dict[str, Any]:
    """Read either the legacy monolith or lazy Compiler manifest/chunks."""

    compiler = _read_json(path, "BPF Compiler")
    if compiler.get("format_version") != 2 or "plans" not in compiler:
        return compiler
    plans = compiler.get("plans")
    if not isinstance(plans, list) or not plans:
        raise ValueError("BPF Compiler lazy manifest contains no Plans")
    intervals = []
    for plan_index, plan in enumerate(plans):
        if not isinstance(plan, dict) or plan.get("plan_index") != plan_index:
            raise ValueError("BPF Compiler lazy Plan catalog is invalid")
        sections = plan.get("sections")
        if not isinstance(sections, dict):
            raise ValueError("BPF Compiler lazy Plan sections are invalid")
        compiled = {
            domain: _read_compiler_section(
                path,
                plan_index,
                f"intents-{domain.replace('_', '-')}",
                sections,
            )
            for domain in _COMPILED_DOMAINS
        }
        intervals.append(
            {
                "plan_index": plan_index,
                "start_timestamp": plan.get("start_timestamp"),
                "end_timestamp": plan.get("end_timestamp"),
                "compiled": compiled,
                "end_state": {
                    "zone_edges": _read_compiler_section(
                        path,
                        plan_index,
                        "baseline-zone-edges",
                        sections,
                    ),
                    "zone_prefixes": _read_compiler_section(
                        path,
                        plan_index,
                        "baseline-zone-prefixes",
                        sections,
                    ),
                },
            }
        )
    return {
        "format_version": 2,
        "tool": compiler.get("tool"),
        "intervals": intervals,
    }


def _read_compiler_section(
    manifest_path: Path,
    plan_index: int,
    section: str,
    sections: dict[str, Any],
) -> list[dict[str, Any]]:
    catalog = sections.get(section)
    if not isinstance(catalog, list):
        raise ValueError(f"BPF Compiler {section} catalog is invalid")
    values = []
    for descriptor in catalog:
        if not isinstance(descriptor, dict):
            raise ValueError("BPF Compiler chunk descriptor is invalid")
        chunk_id = descriptor.get("chunk_id")
        relative = descriptor.get("path")
        if not isinstance(chunk_id, str) or not isinstance(relative, str):
            raise ValueError("BPF Compiler chunk identity is invalid")
        chunk_path = manifest_path.parent / relative
        try:
            source = chunk_path.read_text(encoding="utf-8")
        except OSError as error:
            raise RuntimeError(
                f"cannot read BPF Compiler chunk: {chunk_path}"
            ) from error
        marker = (
            "window.__SKYSEED_BPF_COMPILER_CHUNKS__="
            "window.__SKYSEED_BPF_COMPILER_CHUNKS__||{};"
            "window.__SKYSEED_BPF_COMPILER_CHUNKS__"
            f"[{json.dumps(chunk_id)}]="
        )
        if not source.startswith(marker) or not source.endswith(";\n"):
            raise ValueError("BPF Compiler chunk wrapper is invalid")
        try:
            payload = decode_json_chunk(json.loads(source[len(marker) : -2]))
        except (json.JSONDecodeError, ValueError) as error:
            raise ValueError("BPF Compiler chunk JSON is invalid") from error
        if (
            not isinstance(payload, dict)
            or payload.get("chunk_id") != chunk_id
            or payload.get("plan_index") != plan_index
            or payload.get("section") != section
            or not isinstance(payload.get("rows"), list)
        ):
            raise ValueError("BPF Compiler chunk content is invalid")
        values.extend(payload["rows"])
    return values


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
