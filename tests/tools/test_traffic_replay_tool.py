from __future__ import annotations


from skyseed.simulator import PlannerConfiguration


from copy import deepcopy


from datetime import datetime, timedelta, timezone


from hashlib import sha256


from ipaddress import ip_interface


import gzip


import json


from pathlib import Path


import shutil


import socket


import struct


import pytest


from examples.Sky00_tiny_network.tiny_network import build_emulator


from examples.Sky00_tiny_network.tiny_network import create_simulator as create_complete_static_simulator


from tests._support import drain_simulator as run_complete_static_chain


from skyseed.backend.bpf.dataplane.telemetry_abi import (
    _TELEMETRY_EVENT,
    _decode_telemetry_event,
)


from tools.observation.bpf_compiler import write_bpf_compiler_tool


from tools.observation.planner import collect_planner_data


from tools.observation.planner.reader import read_planner_history


from tools.observation.traffic import collect_traffic_replay_data, write_traffic_replay_tool


from tools.observation.traffic.__main__ import main as traffic_tool_main


from tools.observation.traffic.projection import project_traffic_replay


from tools.observation.traffic.reader import (
    TrafficReplayRead,
    _ping_transactions,
    read_traffic_replay,
)


from tools.observation.traffic.renderer import render_traffic_replay_page


PROJECT_ROOT = Path(__file__).resolve().parents[2]


TOOL_SOURCES = tuple(sorted((PROJECT_ROOT / "tools/observation/traffic").glob("*.py")))


START_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_packet_alignment_uses_wait_and_resume_clock_segments(evidence_set, tmp_path):
    from tools.observation.traffic.reader import _read_telemetry
    rows = [json.loads(line) for line in evidence_set["telemetry"].read_text().splitlines()]
    anchor = next(row for row in rows if row["record_type"] == "simulation_clock_anchor")
    origin = anchor["monotonic_origin_ns"]
    hold = {**anchor, "effective_monotonic_ns": origin + 1_000_000_000,
            "held_since": (origin + 1_000_000_000) / 1e9,
            "active_frontier": anchor["nominal_origin"], "revision": 0, "action": "hold"}
    resume = {**anchor, "effective_monotonic_ns": origin + 2_500_000_000,
              "monotonic_origin_ns": origin + 1_500_000_000,
              "held_since": None, "revision": 1, "action": "resume"}
    rows.extend((hold, resume))
    path = tmp_path / "clock-segments.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    packets = _read_telemetry(path)["packets"]
    assert packets[0]["logical_clock_held"]
    assert packets[0]["simulation_timestamp"] == anchor["nominal_origin"]
    assert packets[-1]["clock_revision"] == 1
    assert packets[-1]["simulation_timestamp"] == (START_AT + timedelta(seconds=1.5)).isoformat()


@pytest.fixture(scope="module")
def evidence_set(tmp_path_factory):
    root = tmp_path_factory.mktemp("traffic-tool")
    history = root / "history"
    simulator = create_complete_static_simulator(build_emulator())
    # This focused lineage fixture deliberately crosses a batch boundary.
    planner = simulator.get_planner()
    planner._configuration = PlannerConfiguration(frames_per_batch=3, inter_satellite_acquisition_duration=timedelta(0), ground_station_acquisition_duration=timedelta(0))
    plans, realizations, _ = run_complete_static_chain(
        simulator, planner=planner, evidence_path=history
    )
    compiler_output = root / "compiler"
    write_bpf_compiler_tool(plans, realizations, compiler_output)
    evidence = read_planner_history(history / "planner/planning.jsonl.gz")
    planner_data = collect_planner_data(evidence)
    telemetry = _telemetry(
        root / "traffic-telemetry.jsonl",
        history / "physical-world/physical-frames.jsonl",
        planner_data,
        json.loads(
            (compiler_output / "bpf-compiler-inspection.json").read_text()
        ),
    )
    return {
        "root": root,
        "telemetry": telemetry,
        "frames": history / "physical-world/physical-frames.jsonl",
        "planning": history / "planner/planning.jsonl.gz",
        "compiler_json": compiler_output / "bpf-compiler-inspection.json",
        "compiler_jsonl": history / "compiler/compiler.jsonl",
        "plans": plans,
        "realizations": realizations,
    }


def _telemetry(
    path: Path,
    frames_path: Path,
    planner_data: dict[str, object],
    compiler_data: dict[str, object],
) -> Path:
    frame_rows = [
        json.loads(line) for line in frames_path.read_text().splitlines()
    ]
    positions = frame_rows[1]["positions"]
    node_ids = [
        f"{value['node']['asn']}/{value['node']['entity_kind']}/{value['node']['entity_id']}"
        for value in positions
    ]
    addresses = {
        node_id: f"100.64.0.{index + 1}"
        for index, node_id in enumerate(node_ids)
    }
    first_frame = planner_data["frames"][0]
    route_outcomes = tuple(
        {"demand": value["demand"], **value["current"]}
        for value in first_frame["transition"]["route_outcome_transitions"]
        if value["current"] is not None
    )
    reachable = next(
        value
        for value in route_outcomes
        if value["kind"] == "reachable"
        if value["demand"]["target"]["kind"] == "ground-station"
    )
    unreachable = next(
        value
        for value in route_outcomes
        if value["kind"] == "unreachable"
        if value["reason"] == "ZONE_UNSERVED"
    )
    source_satellite = _node_id(reachable["demand"]["source"])
    target_station = _node_id(
        reachable["demand"]["target"]["ground_station_id"]
    )
    source_station = _node_id(unreachable["demand"]["source"])
    zone_id = unreachable["demand"]["target"]["zone_id"]
    prefixes = {
        value["zone_id"]: value["packet_prefix"]
        for value in compiler_data["realizations"][-1]["zone_prefixes"]
    }
    zone_address = str(ip_interface(prefixes[zone_id]).ip)
    origin = datetime.fromisoformat(frame_rows[1]["timestamp"])
    monotonic_origin = 1_000_000_000_000
    rows = [
        {
            "record_type": "telemetry_header",
            "format_version": 1,
            "foundation_token": "tool-test",
        },
        *(
            {
                "record_type": "node_identity",
                "node_slot": index,
                "node_id": node_id,
                "runtime_ipv4": addresses[node_id],
            }
            for index, node_id in enumerate(node_ids)
        ),
        {
            "record_type": "simulation_clock_anchor",
            "nominal_origin": origin.isoformat(),
            "monotonic_origin_ns": monotonic_origin,
        },
        _packet(
            monotonic_origin + 2_000_000_000,
            node_ids.index(target_station),
            addresses[source_satellite],
            addresses[target_station],
            8,
            10,
        ),
        _packet(
            monotonic_origin + 2_050_000_000,
            node_ids.index(source_satellite),
            addresses[target_station],
            addresses[source_satellite],
            0,
            10,
        ),
        _packet(
            monotonic_origin + 3_000_000_000,
            node_ids.index(source_station),
            addresses[source_station],
            zone_address,
            8,
            11,
        ),
        {
            "record_type": "telemetry_loss",
            "node_slot": 0,
            "node_id": node_ids[0],
            "lost_events": 0,
        },
        {
            "record_type": "recorder_end",
            "complete": True,
            "reported_losses": 0,
            "packet_records": 3,
        },
    ]
    for index, frame in enumerate(planner_data["frames"]):
        start = monotonic_origin + index * 10_000_000_000 - 1_000_000
        rows.append({
            "record_type": "boundary_activation", "boundary_timestamp": frame["timestamp"],
            "active_generation": index, "previous_generation": 0xFFFFFFFF if index == 0 else index - 1,
            "generation_publication_occurred": True, "preparation": index == 0,
            "main_start_monotonic_ns": start, "main_end_monotonic_ns": start + 1_000_000,
            "generation_switch_start_monotonic_ns": start + 100_000,
            "generation_switch_end_monotonic_ns": start + 200_000,
        })
    path.write_text(
        "".join(json.dumps(value) + "\n" for value in rows),
        encoding="utf-8",
    )
    return path


def _packet(
    monotonic_ns: int,
    node_slot: int,
    source: str,
    destination: str,
    icmp_type: int,
    identifier: int,
) -> dict[str, object]:
    return {
        "record_type": "packet",
        "kernel_monotonic_ns": monotonic_ns,
        "node_slot": node_slot,
        "generation": 0,
        "ingress_ifindex": 1,
        "selected_egress_ifindex": None,
        "source_ipv4": source,
        "destination_ipv4": destination,
        "packet_length": 98,
        "decision": "local/pass",
        "reason": "tool-test",
        "protocol": 1,
        "icmp": {
            "valid": True,
            "type": icmp_type,
            "code": 0,
            "identifier": identifier,
            "sequence": 1,
        },
    }


def _node_id(value: dict[str, object]) -> str:
    return f"{value['asn']}/{value['entity_kind']}/{value['entity_id']}"


def _collect(evidence_set, *, telemetry=None):
    return collect_traffic_replay_data(
        evidence_set['telemetry'] if telemetry is None else telemetry, evidence_set['frames'])


def _telemetry_rows(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def _write_telemetry(
    path: Path,
    rows: list[dict[str, object]],
) -> Path:
    path.write_text(
        "".join(json.dumps(value) + "\n" for value in rows),
        encoding="utf-8",
    )
    return path


def _copied_telemetry(
    tmp_path: Path,
    evidence_set,
    *,
    name: str = "traffic-telemetry.jsonl",
) -> tuple[Path, list[dict[str, object]]]:
    rows = deepcopy(_telemetry_rows(evidence_set["telemetry"]))
    return tmp_path / name, rows


def _decoded_v2_packet(packet, *, reason_code=2, ipv4_observed=True, packet_length=98):
    """Encode wire fields, then use the collector's actual v2 decoder."""
    icmp = packet["icmp"]
    dropping = reason_code in (4, 5)
    return _decode_telemetry_event(_TELEMETRY_EVENT.pack(
        packet["kernel_monotonic_ns"], packet["node_slot"], packet["generation"],
        packet["ingress_ifindex"], packet["selected_egress_ifindex"] or 0,
        struct.unpack("=I", socket.inet_aton(packet["source_ipv4"]))[0],
        struct.unpack("=I", socket.inet_aton(packet["destination_ipv4"]))[0],
        packet_length, 3 if dropping else 2, reason_code, packet["protocol"],
        int(ipv4_observed), icmp["type"], icmp["code"],
        socket.htons(icmp["identifier"]), socket.htons(icmp["sequence"]),
        0, 2 if dropping else 0, int(ipv4_observed),
    ))


def _read_replay(evidence_set, telemetry):
    return read_traffic_replay(
        telemetry, evidence_set["frames"],
    )


def _workload(path: Path, *, series: list[dict[str, object]] | None = None) -> Path:
    values = [] if series is None else series
    slots = sum(
        int(value["schedule"]["scheduled_slots"])
        for value in values
    )
    path.write_text(
        json.dumps(
            {
                "format_version": 1,
                "artifact_type": "canonical-traffic-workload-schedule",
                "authority": {
                    "status": "planned-example-workload",
                    "proves_command_execution": False,
                    "proves_packet_emission": False,
                    "proves_timeout": False,
                    "proves_packet_loss": False,
                    "packet_observation_authority": "traffic-telemetry.jsonl",
                },
                "inventory": {
                    "series": len(values),
                    "scheduled_slots": slots,
                    "maximum_concurrent_series": len(values),
                },
                "series": values,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path


def _series(
    series_id: str,
    *,
    direction: str = "ground-station-to-zone",
    scheduled_slots: int = 5,
) -> dict[str, object]:
    return {
        "series_id": series_id,
        "display_label": f"Traffic series {series_id}",
        "direction": direction,
        "ground_station": {
            "node_id": "65001/ground-station/gs-new-york",
            "display_name": "New York",
        },
        "zone": {
            "zone_id": "new-york-metro",
            "packet_ipv4": "198.18.0.2",
        },
        "schedule": {
            "start_time": "2026-01-01T00:00:00+00:00",
            "end_time": "2026-01-01T00:00:30+00:00",
            "interval_seconds": 2.0,
            "scheduled_slots": scheduled_slots,
        },
        "semantic_purpose": "detached workload display",
        "event_focus": "formal planning boundary",
    }


def _node_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return [value for value in rows if value["record_type"] == "node_identity"]


def _insert_before_packets(
    rows: list[dict[str, object]],
    values: list[dict[str, object]],
) -> None:
    index = next(
        position
        for position, value in enumerate(rows)
        if value["record_type"] == "packet"
    )
    rows[index:index] = values


def _interface_pair(
    rows: list[dict[str, object]],
    *,
    generation: int = 0,
    boundary: str = "2026-01-01T00:00:00+00:00",
    first_ifindex: int = 10,
) -> list[dict[str, object]]:
    first, second = _node_rows(rows)[:2]
    connection = f"test-link|{first['node_id']}|{second['node_id']}"
    return [
        {
            "record_type": "interface_identity",
            "generation": generation,
            "boundary_timestamp": boundary,
            "node_slot": first["node_slot"],
            "node_id": first["node_id"],
            "ifindex": first_ifindex,
            "logical_port_id": 1,
            "interface": f"sv2-{first_ifindex}",
            "connection_id": connection,
            "peer_node_id": second["node_id"],
            "route_destinations": [],
            "validity": "exact-generation",
        },
        {
            "record_type": "interface_identity",
            "generation": generation,
            "boundary_timestamp": boundary,
            "node_slot": second["node_slot"],
            "node_id": second["node_id"],
            "ifindex": first_ifindex + 1,
            "logical_port_id": 2,
            "interface": f"sv2-{first_ifindex + 1}",
            "connection_id": connection,
            "peer_node_id": first["node_id"],
            "route_destinations": [],
            "validity": "exact-generation",
        },
    ]


def _read_observation(evidence_set) -> TrafficReplayRead:
    return read_traffic_replay(
        evidence_set["telemetry"],
        evidence_set["frames"],
    )


def test_ping_pairing_separates_reverse_flows_with_reused_icmp_identity() -> None:
    def packet(
        timestamp: int, source: str, destination: str, icmp_type: int
    ) -> dict[str, object]:
        return {
            "protocol": 1,
            "kernel_monotonic_ns": timestamp,
            "source_ipv4": source,
            "destination_ipv4": destination,
            "simulation_timestamp": f"2026-01-01T00:00:0{timestamp}+00:00",
            "icmp": {
                "valid": True,
                "type": icmp_type,
                "identifier": 42,
                "sequence": 7,
            },
        }

    transactions = _ping_transactions(
        (
            packet(1, "100.64.0.1", "100.64.0.2", 8),
            packet(2, "100.64.0.2", "100.64.0.1", 0),
            packet(3, "100.64.0.2", "100.64.0.1", 8),
            packet(4, "100.64.0.1", "100.64.0.2", 0),
        )
    )

    assert len(transactions) == 2
    assert {
        (
            value["request"]["source_ipv4"],
            value["request"]["destination_ipv4"],
        )
        for value in transactions
    } == {
        ("100.64.0.1", "100.64.0.2"),
        ("100.64.0.2", "100.64.0.1"),
    }


def test_transaction_pairing_marks_temporal_collision_ambiguous() -> None:
    def packet(timestamp: int, icmp_type: int) -> dict[str, object]:
        source, destination = (
            ("10.0.0.1", "10.0.0.2")
            if icmp_type == 8
            else ("10.0.0.2", "10.0.0.1")
        )
        return {
            "protocol": 1,
            "kernel_monotonic_ns": timestamp,
            "source_ipv4": source,
            "destination_ipv4": destination,
            "simulation_timestamp": START_AT.isoformat(),
            "icmp": {
                "valid": True,
                "type": icmp_type,
                "identifier": 1,
                "sequence": 1,
            },
        }

    transactions = _ping_transactions(
        (packet(10, 8), packet(12, 8), packet(14, 0))
    )

    assert len(transactions) == 1
    assert transactions[0]["pairing_status"] == "ambiguous"
    assert transactions[0]["observed_reply"] is True
    assert transactions[0]["packet_observations"] == 3
    assert transactions[0]["association_uncertainty"]


def test_connection_pair_and_generation_boundary_corruption_fail_closed(
    tmp_path: Path,
    evidence_set,
) -> None:
    telemetry, rows = _copied_telemetry(
        tmp_path, evidence_set, name="unmatched.jsonl"
    )
    pair = _interface_pair(rows)
    _insert_before_packets(rows, pair[:1])
    _write_telemetry(telemetry, rows)
    with pytest.raises(ValueError, match="connection|endpoint|interface"):
        _collect(evidence_set, telemetry=telemetry)

    boundary_root = tmp_path / "boundary"
    boundary_root.mkdir()
    telemetry, rows = _copied_telemetry(boundary_root, evidence_set)
    pair = _interface_pair(
        rows, boundary="2026-01-01T00:00:01+00:00"
    )
    _insert_before_packets(rows, pair)
    _write_telemetry(telemetry, rows)
    with pytest.raises(ValueError, match="generation|boundary"):
        _collect(evidence_set, telemetry=telemetry)


def test_long_lived_ping_sequence_prevents_scheduled_slot_drift() -> None:
    packets = []
    for index in range(80):
        packets.extend(
            (
                {
                    "protocol": 1,
                    "kernel_monotonic_ns": index * 3_015_000_000,
                    "source_ipv4": "10.0.0.1",
                    "destination_ipv4": "10.0.0.2",
                    "simulation_timestamp": (
                        START_AT + timedelta(seconds=index * 3.015)
                    ).isoformat(),
                    "icmp": {
                        "valid": True,
                        "type": 8,
                        "identifier": 17,
                        "sequence": index + 1,
                    },
                },
                {
                    "protocol": 1,
                    "kernel_monotonic_ns": index * 3_015_000_000 + 20_000_000,
                    "source_ipv4": "10.0.0.2",
                    "destination_ipv4": "10.0.0.1",
                    "simulation_timestamp": (
                        START_AT
                        + timedelta(seconds=index * 3.015 + 0.02)
                    ).isoformat(),
                    "icmp": {
                        "valid": True,
                        "type": 0,
                        "identifier": 17,
                        "sequence": index + 1,
                    },
                },
            )
        )

    transactions = _ping_transactions(tuple(packets))

    assert [value["sequence"] for value in transactions] == list(range(1, 81))
    assert all(value["observed_reply"] is True for value in transactions)
    assert all(value["packet_observations"] == 2 for value in transactions)
    assert [
        datetime.fromisoformat(value["request"]["simulation_timestamp"])
        for value in transactions
    ] == [START_AT + timedelta(seconds=index * 3.015) for index in range(80)]


def test_json_stream_preserves_bytes_and_previous_file_on_failure(tmp_path):
    from tools.observation._presentation.html import _write_compact_json, write_json
    path = tmp_path / "evidence.json"
    data = {"多语言": [1, {"ordered": "yes", "missing": None}], "empty": ()}
    expected = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    write_json(path, data)
    assert path.read_text() == expected
    with pytest.raises(TypeError):
        write_json(path, {"valid": data, "invalid": object()})
    assert path.read_text() == expected
    assert not path.with_name(".evidence.json.tmp").exists()
    _write_compact_json(path, data)
    assert path.read_text() == json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"



def test_observed_globe_timeline_keeps_uncertainty_and_has_no_planned_path(evidence_set,tmp_path):
    data=collect_traffic_replay_data(evidence_set['telemetry'],evidence_set['frames'])
    assert data['events'] and data['frames']
    assert all('route_outcome' not in event for event in data['events'])
    assert 'gaps' in data['evidence_limit']
    output=tmp_path/'view'
    write_traffic_replay_tool(evidence_set['telemetry'],evidence_set['frames'],output)
    page=(output/'index.html').read_text()
    assert all(value in page for value in ('traffic-play','traffic-seek','traffic-flow','createEvidenceGlobe'))
    assert 'Planned request path' not in page


def test_sender_aggregate_never_manufactures_packet_hops(evidence_set,tmp_path):
    execution=tmp_path/'sender.jsonl'
    execution.write_text(json.dumps({'record_type':'execution_header'})+'\n'+json.dumps({
        'record_type':'generator_stopped','communication_id':'fixed','host_monotonic_ns':100,
        'sent':100,'received':70})+'\n')
    data=collect_traffic_replay_data(tmp_path/'absent',evidence_set['frames'],probe_execution_path=execution)
    assert len(data['events'])==1
    assert data['events'][0]['hops']==[]
    assert data['telemetry_complete'] is None
    assert 'no intermediate' in data['evidence_limit']
