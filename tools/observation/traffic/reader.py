"""Read actual sender and BPF packet evidence. No planned routes or qualification."""
from collections import defaultdict
from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
import json

@dataclass(frozen=True)
class TrafficReplayRead:
    snapshot: dict


def read_traffic_replay(telemetry_path, physical_frames_path, *,
                       workload_path=None, probe_execution_path=None):
    from tools.observation._history import read_sealed_physical_frames
    records = list(_iter_jsonl(probe_execution_path)) if probe_execution_path and Path(probe_execution_path).is_file() else []
    telemetry = _read_telemetry(Path(telemetry_path), probe_execution=records) if Path(telemetry_path).is_file() else None
    frames = read_sealed_physical_frames(Path(physical_frames_path)) if physical_frames_path and Path(physical_frames_path).is_file() else []
    workload = records[0].get('workload', {}) if records else {}
    config = workload_path
    if config and Path(config).is_file():
        workload = json.loads(Path(config).read_text())
    return TrafficReplayRead({'records': records, 'telemetry': telemetry, 'frames': frames, 'workload': workload})


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
    if len(headers) != 1 or not anchors:
        raise ValueError("traffic telemetry header/clock anchor cardinality is invalid")
    anchor = anchors[0]
    nominal_origin = _timestamp(anchor.get("nominal_origin"), "nominal_origin")
    monotonic_origin = _integer(
        anchor.get("monotonic_origin_ns"), "monotonic_origin_ns"
    )
    clock_starts = [a.get("effective_monotonic_ns", a["monotonic_origin_ns"]) for a in anchors]
    if clock_starts != sorted(clock_starts) or any(a["nominal_origin"] != anchor["nominal_origin"] for a in anchors):
        raise ValueError("Traffic clock segments are not ordered within one invocation")
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
        if 'local_origin' in packet and (type(packet['local_origin']) is not bool
                or packet.get('observation') != ('local-output' if packet['local_origin'] else 'ingress')):
            raise ValueError('packet local-origin observation is inconsistent')
        monotonic = _integer(
            packet.get("kernel_monotonic_ns"), "kernel_monotonic_ns"
        )
        slot = _integer(packet.get("node_slot"), "node_slot")
        if slot not in by_slot:
            raise ValueError("packet references an unknown node slot")
        if not isinstance(packet.get("icmp"), dict):
            raise TypeError("packet ICMP evidence must be an object")
        # Legacy records always carried IPv4 addresses. Only an explicit v2
        # fact marker permits unknown addresses; retain those observations.
        ipv4_observed = packet.get("ipv4_observed", True)
        if not isinstance(ipv4_observed, bool):
            raise ValueError("packet ipv4_observed must be a boolean")
        for name in ("source_ipv4", "destination_ipv4"):
            if ipv4_observed:
                _string(packet.get(name), name)
            elif name not in packet or packet[name] is not None:
                raise ValueError(f"{name} must be null when ipv4_observed is false")
        segment = anchors[max(0, bisect_right(clock_starts, monotonic) - 1)]
        held = segment.get("held_since") is not None
        packet["simulation_timestamp"] = (segment["active_frontier"] if held else
            (nominal_origin + timedelta(seconds=(monotonic - segment["monotonic_origin_ns"]) / 1e9)).isoformat())
        packet["logical_clock_held"] = held
        packet["clock_revision"] = segment.get("revision", 0)
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
    generation_boundaries = {v['active_generation']: v['boundary_timestamp'] for v in boundaries if v['generation_publication_occurred']}
    for generation, boundary in interfaces['generations']:
        if generation_boundaries.get(generation) != boundary:
            raise ValueError('interface generation-to-boundary mapping differs')
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
            if direction == 'ingress' and packet.get('local_origin') is True:
                packet['ingress_identity'] = None
                continue
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
        "clock_segments": anchors,
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
    probe_execution = tuple({**r, 'episode_id': r.get('communication_id', r.get('episode_id'))} for r in probe_execution)
    launches = defaultdict(list)
    stops = {(r.get("episode_id"), r.get("segment_index")): r
             for r in probe_execution if r.get("record_type") == "generator_stopped"}
    for r in probe_execution:
        if r.get("record_type") == "generator_launched":
            launches[(r["icmp_identifier"], r["source_ipv4"], r["destination_ipv4"])].append(r)
    sender_replies = defaultdict(list)
    streamed_segments = set()
    for row in probe_execution:
        if row.get('record_type') == 'ping_observation':
            key = (row.get('episode_id'), row.get('segment_index'))
            streamed_segments.add(key)
            if row.get('observation') == 'reply' and row.get('icmp_sequence') is not None:
                sender_replies[(*key, row['icmp_sequence'])].append(row)
    grouped = defaultdict(list)
    for index, packet in enumerate(packets):
        icmp = packet["icmp"]
        if (packet.get("ipv4_observed") is False
            or packet.get("protocol") != 1 or not icmp.get("valid")
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
            observations = sender_replies.get((*key, sequence), ()) if g is not None else ()
            unique_replies = [r for r in observations if not r.get('duplicate', 'DUP!' in r.get('raw_line', ''))]
            emissions = {packets[i]['kernel_monotonic_ns'] for i in requests if packets[i].get('local_origin')}
            if (g is not None and (stops.get(key, {}).get('sent') or 0) > 65535
                    or len(unique_replies) > 1 or len(emissions) > 1):
                b['uncertainty'].append('sequence wrap or repeated emission cannot be resolved from recorded ICMP fields')
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
            uncertain = sorted(set(b["uncertainty"]))
            executor_reply = bool(g is not None and requests and not uncertain and any(
                r.get('icmp_identifier', identifier) == identifier
                and r.get('host_monotonic_ns', -1) >= packets[requests[0]]['kernel_monotonic_ns']
                for r in observations))
            if g is not None and key not in streamed_segments and requests and not uncertain:
                # Historical terminal-only recordings predate the detailed stream.
                executor_reply = sequence in stops.get(key, {}).get('reply_sequences', ())
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
    if packet.get('local_origin') is True:
        return False
    # v2 local-route is explicit handler metadata; a generic PASS makes no
    # delivery claim. Preserve the legacy address fallback only for old records.
    if packet.get("decision") in {"pass", "local/pass"} and packet.get("reason") == "local-route":
        return True
    if "tc_action" in packet:
        return False
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



__all__ = ["TrafficReplayRead", "read_traffic_replay"]
