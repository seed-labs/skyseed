from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from tools.traffic import collect_traffic_replay_data, write_traffic_replay_tool
from tools.traffic.__main__ import main as traffic_tool_main
from tools.traffic.projection import (
    _generation_at,
    _pair_ping_transactions,
    _position_at,
    _project_workload_activity,
    _project_zone_geometry,
)
from tools.traffic.renderer import render_traffic_replay_page


ORIGIN = "2026-01-01T00:00:00+00:00"
END = "2026-01-01T00:00:10+00:00"
SOURCE = "65010/satellite/dynamic-shell-orbit-001-satellite-0005"
MIDDLE = "65010/satellite/dynamic-shell-orbit-001-satellite-0004"
DESTINATION = "65010/ground-station/gs-porto-alegre-reference"
ZONE_ID = "test-zone"
ZONE_PREFIX = "198.18.0.1/32"


def _inputs(
    root: Path,
    *,
    losses: int = 0,
    recorder_complete: bool = True,
    mismatch_interface_identity: bool = False,
    zone_source: bool = False,
    unknown_source: bool = False,
    reply_generation: int = 0,
) -> tuple[Path, Path, Path, Path]:
    traffic = root / "inspection/traffic/traffic-telemetry.jsonl"
    dynamic = root / "inspection/slice/slice-difference.json"
    compiler = root / "inspection/bpf-compiler/bpf-compiler-inspection.json"
    workload = root / "inspection/traffic/traffic-workload.json"
    traffic.parent.mkdir(parents=True)
    dynamic.parent.mkdir(parents=True)
    compiler.parent.mkdir(parents=True)
    nodes = (
        (0, SOURCE, "100.64.0.1", "satellite"),
        (1, MIDDLE, "100.64.0.2", "satellite"),
        (2, DESTINATION, "100.64.0.3", "ground-station"),
    )
    first_connection = f"inter-satellite|{SOURCE}|{MIDDLE}"
    second_connection = f"ground-station-access|{MIDDLE}|{DESTINATION}"
    generations = tuple(range(max(reply_generation, 1) + 1))
    boundaries = tuple(ORIGIN if generation == 0 else END for generation in generations)
    interface_records = []
    for generation, boundary in zip(generations, boundaries, strict=True):
        interface_records.extend(
            (
                {
                    "record_type": "interface_identity",
                    "generation": generation,
                    "boundary_timestamp": boundary,
                    "node_slot": 0,
                    "node_id": SOURCE,
                    "ifindex": 10,
                    "interface": "sv2source",
                    "connection_id": first_connection,
                    "peer_node_id": MIDDLE,
                    "route_destinations": (),
                    "validity": "exact-generation",
                },
                {
                    "record_type": "interface_identity",
                    "generation": generation,
                    "boundary_timestamp": boundary,
                    "node_slot": 1,
                    "node_id": SOURCE if mismatch_interface_identity else MIDDLE,
                    "ifindex": 11,
                    "interface": "sv2middlein",
                    "connection_id": first_connection,
                    "peer_node_id": SOURCE,
                    "route_destinations": (),
                    "validity": "exact-generation",
                },
                {
                    "record_type": "interface_identity",
                    "generation": generation,
                    "boundary_timestamp": boundary,
                    "node_slot": 1,
                    "node_id": MIDDLE,
                    "ifindex": 12,
                    "interface": "sv2middleout",
                    "connection_id": second_connection,
                    "peer_node_id": DESTINATION,
                    "route_destinations": ("100.64.0.3",),
                    "validity": "exact-generation",
                },
                {
                    "record_type": "interface_identity",
                    "generation": generation,
                    "boundary_timestamp": boundary,
                    "node_slot": 2,
                    "node_id": DESTINATION,
                    "ifindex": 21,
                    "interface": "sv2destination",
                    "connection_id": second_connection,
                    "peer_node_id": MIDDLE,
                    "route_destinations": (),
                    "validity": "exact-generation",
                },
            )
        )
    source_address = (
        "198.18.0.1" if zone_source else "203.0.113.1" if unknown_source else "100.64.0.1"
    )
    packet_records = [
        _packet(
            101_250_000_000,
            slot=1,
            generation=0,
            ingress=11,
            egress=12,
            source=source_address,
            destination="100.64.0.3",
            icmp_type=8,
        ),
        _packet(
            101_300_000_000,
            slot=2,
            generation=0,
            ingress=21,
            egress=None,
            source=source_address,
            destination="100.64.0.3",
            icmp_type=8,
        ),
        _packet(
            106_250_000_000 if reply_generation else 101_350_000_000,
            slot=1,
            generation=reply_generation,
            ingress=12,
            egress=11,
            source="100.64.0.3",
            destination=source_address,
            icmp_type=0,
        ),
        _packet(
            106_300_000_000 if reply_generation else 101_400_000_000,
            slot=0,
            generation=reply_generation,
            ingress=10,
            egress=None,
            source="100.64.0.3",
            destination=source_address,
            icmp_type=0,
        ),
        {
            **_packet(
                106_400_000_000 if reply_generation else 101_500_000_000,
                slot=2,
                generation=reply_generation,
                ingress=21,
                egress=None,
                source="100.64.0.1",
                destination="100.64.0.3",
                icmp_type=8,
            ),
            "protocol": 17,
            "icmp": {
                "valid": False,
                "type": None,
                "code": None,
                "identifier": None,
                "sequence": None,
            },
        },
    ]
    records = [
        {
            "record_type": "telemetry_header",
            "format_version": 1,
            "foundation_token": "foundation-test",
            "event_schema": {"name": "skyseed-tc-ingress-v1"},
            "pin_root": "/sys/fs/bpf/skyseed-v2/run-test",
            "ring_capacity_bytes": 4096,
            "source_observation": {
                "hook": "ingress-only",
                "source_outbound_observed": False,
                "first_link": "inferred-from-first-observed-ingress",
            },
        },
        *(
            {
                "record_type": "node_identity",
                "node_slot": slot,
                "node_id": node_id,
                "runtime_ipv4": address,
                "pin_component": f"n-{slot}",
            }
            for slot, node_id, address, _kind in nodes
        ),
        {
            "record_type": "simulation_clock_anchor",
            "nominal_origin": ORIGIN,
            "monotonic_origin_ns": 100_000_000_000,
            "clock_domain": "host-kernel-monotonic",
            "nominal_pacing": "1:1",
        },
        *interface_records,
        *packet_records,
        {
            "record_type": "telemetry_loss",
            "node_slot": 1,
            "node_id": MIDDLE,
            "lost_events": losses,
        },
        {
            "record_type": "recorder_end",
            "complete": recorder_complete,
            "failure": None if recorder_complete else {"type": "Interrupted"},
            "packet_records": len(packet_records),
            "identity_records": len(nodes) + len(interface_records),
            "reported_losses": losses,
            "collector_exit_code": 0,
        },
    ]
    traffic.write_text(
        "".join(json.dumps(value) + "\n" for value in records),
        encoding="utf-8",
    )

    def positions(offset: float) -> list[dict[str, object]]:
        return [
            {
                "node_id": node_id,
                "ecef_m": {
                    "x": base[0] + offset,
                    "y": base[1] + offset,
                    "z": base[2] + offset,
                },
                "geographic": {
                    "latitude_deg": 0.0,
                    "longitude_deg": 0.0,
                    "ellipsoidal_height_m": 0.0,
                },
            }
            for (_slot, node_id, _address, _kind), base in zip(
                nodes,
                (
                    (7_000_000.0, 0.0, 0.0),
                    (6_900_000.0, 500_000.0, 0.0),
                    (6_371_000.0, 1_000_000.0, 0.0),
                ),
                strict=True,
            )
        ]

    dynamic.write_text(
        json.dumps(
            {
                "format_version": 1,
                "tool": "slice-difference-inspection",
                "timeline": {
                    "start_time": ORIGIN,
                    "end_time": END,
                    "slice_interval_seconds": 10.0,
                },
                "topology": {
                    "nodes": [
                        {"node_id": node_id, "kind": kind}
                        for _slot, node_id, _address, kind in nodes
                    ],
                    "zones": (
                        [
                            {
                                "zone_id": ZONE_ID,
                                "geometry": {
                                    "kind": "latitude-longitude-rectangle",
                                    "south_latitude_deg": -10.0,
                                    "north_latitude_deg": 10.0,
                                    "west_longitude_deg": 170.0,
                                    "east_longitude_deg": -170.0,
                                },
                                "representative_point": {
                                    "latitude_deg": 0.0,
                                    "longitude_deg": 180.0,
                                },
                            }
                        ]
                        if zone_source
                        else []
                    ),
                },
                "slices": [
                    {"slice_id": 0, "timestamp": None, "positions": []},
                    {"slice_id": 1, "timestamp": ORIGIN, "positions": positions(0)},
                    {"slice_id": 2, "timestamp": END, "positions": positions(1_000)},
                ],
            }
        ),
        encoding="utf-8",
    )
    compiler.write_text(
        json.dumps(
            _compiler_artifact(
                zone_source=zone_source,
                second_boundary=True,
            )
        ),
        encoding="utf-8",
    )
    workload.write_text(
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
                "simulation": {
                    "start_time": ORIGIN,
                    "end_time": END,
                    "duration_seconds": 10.0,
                },
                "inventory": {
                    "series": 0,
                    "scheduled_slots": 0,
                    "maximum_concurrent_series": 0,
                },
                "series": [],
            }
        ),
        encoding="utf-8",
    )
    return traffic, dynamic, compiler, workload


def _packet(
    timestamp: int,
    *,
    slot: int,
    generation: int,
    ingress: int,
    egress: int | None,
    source: str,
    destination: str,
    icmp_type: int,
) -> dict[str, object]:
    return {
        "record_type": "packet",
        "observation": "ingress",
        "kernel_monotonic_ns": timestamp,
        "node_slot": slot,
        "generation": generation,
        "ingress_ifindex": ingress,
        "selected_egress_ifindex": egress,
        "source_ipv4": source,
        "destination_ipv4": destination,
        "packet_length": 98,
        "decision": "redirect" if egress is not None else "local/pass",
        "reason": "route-redirect" if egress is not None else "local-route",
        "protocol": 1,
        "icmp": {
            "valid": True,
            "type": icmp_type,
            "code": 0,
            "identifier": 44,
            "sequence": 1,
        },
    }


def _compiler_artifact(*, zone_source: bool, second_boundary: bool) -> dict[str, object]:
    prefixes = (
        [{"zone_id": ZONE_ID, "packet_prefix": ZONE_PREFIX}]
        if zone_source
        else []
    )
    zone_events = (
        [
            {
                "nominal_time": ORIGIN,
                "operation": "added",
                "zone_id": ZONE_ID,
                "packet_prefix": ZONE_PREFIX,
                "previous_service_satellite": None,
                "service_satellite": SOURCE,
            }
        ]
        if zone_source
        else []
    )
    zone_state = (
        [
            {
                "zone_id": ZONE_ID,
                "packet_prefix": ZONE_PREFIX,
                "service_satellite": SOURCE,
            }
        ]
        if zone_source
        else []
    )
    operations = [
        {"nominal_time": ORIGIN, "operation": "establish"},
        *(
            [{"nominal_time": END, "operation": "changed"}]
            if second_boundary
            else [{"nominal_time": END, "operation": "changed"}]
        ),
    ]
    return {
        "format_version": 1,
        "tool": "bpf-compiler-inspection",
        "intervals": [
            {
                "start_timestamp": None,
                "end_timestamp": END,
                "compiled": {
                    "resources": operations,
                    "parameters": [],
                    "forwarding": [],
                    "zone_edges": zone_events,
                },
                "end_state": {
                    "resources": [],
                    "parameters": [],
                    "forwarding": [],
                    "zone_edges": zone_state,
                    "zone_prefixes": prefixes,
                },
            }
        ],
    }


def test_node_endpoint_transaction_clock_and_active_graph(tmp_path: Path) -> None:
    traffic, dynamic, compiler, workload = _inputs(tmp_path, reply_generation=1)

    data = collect_traffic_replay_data(traffic, dynamic, compiler, workload)
    transaction = data["ping_transactions"][0]
    request = transaction["request"]

    assert transaction["pairing_status"] == "paired"
    assert transaction["generations"] == (0, 1)
    assert request["source_endpoint"]["endpoint_kind"] == "node"
    assert request["first_simulation_timestamp"] == (
        "2026-01-01T00:00:01.250000+00:00"
    )
    assert request["first_link"]["peer_identity_verified"] is True
    assert [value["node_id"] for value in request["observed_path"]] == [
        MIDDLE,
        DESTINATION,
    ]
    assert request["link_hops"] == 2
    assert len(data["active_networks"]) == 2
    assert all(len(value["connections"]) == 2 for value in data["active_networks"])
    assert len(data["unclassified_observations"]) == 1
    assert data["visualization"] == {
        "recent_traffic_default_seconds": 5,
        "recent_traffic_options_seconds": (2, 5, 10),
        "packet_animation": "visual-time-expansion",
        "recorded_timing_authoritative": True,
        "persistence_clock": "simulation-time",
        "persistence_represents": (
            "real observations inside the selected recent simulation-time window"
        ),
        "persistence_does_not_represent": (
            "RTT",
            "throughput",
            "traffic volume",
            "queue occupancy",
        ),
    }
    assert "position_samples" in data
    assert "active_networks" in data
    assert "traffic_records" not in data
    assert "context_frames" not in transaction
    assert "position" not in request["inferred_source"]


def test_zone_endpoint_uses_service_satellite_as_physical_source(tmp_path: Path) -> None:
    traffic, dynamic, compiler, workload = _inputs(tmp_path, zone_source=True)

    data = collect_traffic_replay_data(traffic, dynamic, compiler, workload)
    transaction = data["ping_transactions"][0]
    request = transaction["request"]

    assert transaction["source_endpoint"] == request["source_endpoint"]
    assert transaction["source_endpoint"]["endpoint_kind"] == "zone"
    assert transaction["source_endpoint"]["zone_id"] == ZONE_ID
    assert transaction["source_endpoint"]["ipv4"] == "198.18.0.1"
    assert transaction["source_endpoint"]["service_satellite_id"] == SOURCE
    assert request["inferred_source"]["node_id"] == SOURCE
    assert request["first_link"]["source_node_id"] == SOURCE
    assert request["first_link"]["peer_identity_verified"] is True


def test_unavailable_zone_destination_preserves_observed_drop(
    tmp_path: Path,
) -> None:
    traffic, dynamic, compiler, workload = _inputs(
        tmp_path, zone_source=True, reply_generation=1
    )
    records = [
        json.loads(line)
        for line in traffic.read_text(encoding="utf-8").splitlines()
    ]
    packet = {
        **_packet(
            106_250_000_000,
            slot=1,
            generation=1,
            ingress=11,
            egress=None,
            source="100.64.0.1",
            destination="198.18.0.1",
            icmp_type=8,
        ),
        "decision": "drop",
        "reason": "no-route",
    }
    records = [value for value in records if value["record_type"] != "packet"]
    recorder_end = next(
        value for value in records if value["record_type"] == "recorder_end"
    )
    recorder_index = records.index(recorder_end)
    records.insert(recorder_index, packet)
    recorder_end["packet_records"] = 1
    traffic.write_text(
        "".join(json.dumps(value) + "\n" for value in records),
        encoding="utf-8",
    )

    compiled = json.loads(compiler.read_text(encoding="utf-8"))
    compiled["intervals"][0]["compiled"]["zone_edges"].append(
        {
            "nominal_time": END,
            "operation": "removed",
            "zone_id": ZONE_ID,
            "packet_prefix": ZONE_PREFIX,
            "previous_service_satellite": SOURCE,
            "service_satellite": None,
        }
    )
    compiled["intervals"][0]["end_state"]["zone_edges"] = []
    compiler.write_text(json.dumps(compiled), encoding="utf-8")

    data = collect_traffic_replay_data(traffic, dynamic, compiler, workload)
    transaction = data["ping_transactions"][0]
    request = transaction["request"]

    assert transaction["pairing_status"] == "unpaired"
    assert request["destination_endpoint"]["endpoint_kind"] == "zone"
    assert request["destination_endpoint"]["zone_id"] == ZONE_ID
    assert request["destination_endpoint"]["physical_node_id"] is None
    assert request["destination_endpoint"]["service_satellite_id"] is None
    assert request["destination_endpoint"]["service_satellite_display_name"] is None
    assert request["final_decision"] == "drop"
    assert request["final_reason"] == "no-route"
    assert request["observed_path"][0]["generation"] == 1


def test_workload_schedule_is_read_and_projected_as_qualified_lane(
    tmp_path: Path,
) -> None:
    traffic, dynamic, compiler, workload = _inputs(tmp_path, zone_source=True)
    value = json.loads(workload.read_text(encoding="utf-8"))
    value["inventory"] = {
        "series": 1,
        "scheduled_slots": 5,
        "maximum_concurrent_series": 1,
    }
    value["series"] = [
        {
            "series_id": "S-test",
            "display_label": "Zone source test",
            "direction": "zone-to-ground-station",
            "ground_station": {
                "node_id": DESTINATION,
                "display_name": "Porto Alegre",
            },
            "zone": {"zone_id": ZONE_ID, "packet_ipv4": "198.18.0.1"},
            "schedule": {
                "interval_semantics": "[start,end)",
                "start_time": ORIGIN,
                "end_time": END,
                "start_offset_seconds": 0.0,
                "end_offset_seconds": 10.0,
                "interval_seconds": 2.0,
                "phase_seconds": 0.0,
                "scheduled_slots": 5,
            },
            "semantic_purpose": "test durable schedule read",
            "event_focus": "test Zone service",
        }
    ]
    workload.write_text(json.dumps(value), encoding="utf-8")

    data = collect_traffic_replay_data(traffic, dynamic, compiler, workload)
    lane = data["traffic_lanes"][0]

    assert data["ping_transactions"][0]["series_id"] == "S-test"
    assert lane["observed_transactions"] == 1
    assert lane["observed_gap_slots"] == 4
    assert "does not prove" in lane["gap_qualification"]


def test_workload_is_optional_and_observed_flow_is_telemetry_derived(
    tmp_path: Path,
) -> None:
    traffic, dynamic, compiler, _workload = _inputs(tmp_path)

    data = collect_traffic_replay_data(traffic, dynamic, compiler, None)

    assert data["workload"] is None
    assert data["traffic_lanes"] == ()
    assert data["overview"]["workload_present"] is False
    assert data["overview"]["observed_flows"] == 1
    assert data["observed_flows"][0]["protocol"] == "ICMP"
    assert data["observed_flows"][0]["traffic_kind"] == "ping"
    assert data["ping_transactions"][0]["series_id"] is None
    assert data["ping_transactions"][0]["observed_flow_id"] == (
        data["observed_flows"][0]["flow_id"]
    )


def test_workload_cannot_claim_timeout_or_packet_loss_evidence(tmp_path: Path) -> None:
    traffic, dynamic, compiler, workload = _inputs(tmp_path)
    value = json.loads(workload.read_text(encoding="utf-8"))
    value["authority"]["proves_timeout"] = True
    workload.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported runtime evidence"):
        collect_traffic_replay_data(traffic, dynamic, compiler, workload)


def test_zone_prefix_conflict_and_instability_fail_closed(tmp_path: Path) -> None:
    traffic, dynamic, compiler, workload = _inputs(tmp_path, zone_source=True)
    value = json.loads(compiler.read_text(encoding="utf-8"))
    value["intervals"][0]["end_state"]["zone_prefixes"].append(
        {"zone_id": "other-zone", "packet_prefix": ZONE_PREFIX}
    )
    compiler.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="prefix identity is conflicting"):
        collect_traffic_replay_data(traffic, dynamic, compiler, workload)

    traffic, dynamic, compiler, workload = _inputs(
        tmp_path / "unstable", zone_source=True
    )
    value = json.loads(compiler.read_text(encoding="utf-8"))
    second = json.loads(json.dumps(value["intervals"][0]))
    second["start_timestamp"] = END
    second["compiled"] = {
        "resources": [],
        "parameters": [],
        "forwarding": [],
        "zone_edges": [],
    }
    second["end_state"]["zone_prefixes"][0]["packet_prefix"] = "198.18.0.9/32"
    second["end_state"]["zone_edges"][0]["packet_prefix"] = "198.18.0.9/32"
    value["intervals"].append(second)
    compiler.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="prefixes are unstable"):
        collect_traffic_replay_data(traffic, dynamic, compiler, workload)


def test_unknown_endpoint_degrades_without_inventing_identity(tmp_path: Path) -> None:
    traffic, dynamic, compiler, workload = _inputs(tmp_path, unknown_source=True)

    data = collect_traffic_replay_data(traffic, dynamic, compiler, workload)

    assert data["ping_transactions"] == ()
    unknown = next(
        value
        for value in data["unclassified_observations"]
        if value["source_ipv4"] == "203.0.113.1"
    )
    assert unknown["source_ipv4"] == "203.0.113.1"
    assert unknown["source_endpoint"]["endpoint_kind"] == "unknown"
    assert unknown["source_endpoint"]["stable_id"] is None
    assert [value["node_id"] for value in unknown["observations"]] == [
        MIDDLE,
        DESTINATION,
    ]


def test_transaction_pairing_marks_temporal_collision_ambiguous() -> None:
    def record(record_id: str, first: int, last: int, icmp_type: int) -> dict[str, object]:
        source, destination = (
            ("10.0.0.1", "10.0.0.2")
            if icmp_type == 8
            else ("10.0.0.2", "10.0.0.1")
        )
        endpoint = {"display_name": source, "endpoint_kind": "node"}
        return {
            "record_id": record_id,
            "icmp_type": icmp_type,
            "identifier": 1,
            "sequence": 1,
            "source_ipv4": source,
            "destination_ipv4": destination,
            "source_endpoint": endpoint,
            "destination_endpoint": endpoint,
            "first_kernel_monotonic_ns": first,
            "last_kernel_monotonic_ns": last,
            "first_simulation_timestamp": ORIGIN,
            "observed_path": ({"generation": 0, "active_boundary_timestamp": ORIGIN},),
            "evidence_complete": True,
        }

    transactions = _pair_ping_transactions(
        [
            record("request-1", 10, 11, 8),
            record("request-2", 12, 13, 8),
            record("reply", 14, 15, 0),
        ]
    )

    assert transactions[0]["pairing_status"] == "ambiguous"
    assert transactions[0]["reply"] is None
    assert any(value["pairing_status"] == "paired" for value in transactions)


def test_connection_pair_and_generation_boundary_corruption_fail_closed(
    tmp_path: Path,
) -> None:
    traffic, dynamic, compiler, workload = _inputs(tmp_path)
    records = [json.loads(line) for line in traffic.read_text().splitlines()]
    removed = False
    filtered = []
    for value in records:
        if (
            not removed
            and value.get("record_type") == "interface_identity"
            and value.get("node_slot") == 0
        ):
            removed = True
            continue
        filtered.append(value)
    traffic.write_text("".join(json.dumps(value) + "\n" for value in filtered))
    with pytest.raises(ValueError, match="lacks two endpoints"):
        collect_traffic_replay_data(traffic, dynamic, compiler, workload)

    traffic, dynamic, compiler, workload = _inputs(tmp_path / "boundary")
    records = [json.loads(line) for line in traffic.read_text().splitlines()]
    for value in records:
        if value.get("record_type") == "interface_identity":
            value["boundary_timestamp"] = "2026-01-01T00:00:01+00:00"
    traffic.write_text("".join(json.dumps(value) + "\n" for value in records))
    with pytest.raises(ValueError, match="generation-to-boundary mapping"):
        collect_traffic_replay_data(traffic, dynamic, compiler, workload)


def test_future_generation_links_do_not_enter_current_active_graph(
    tmp_path: Path,
) -> None:
    traffic, dynamic, compiler, workload = _inputs(tmp_path)
    records = [json.loads(line) for line in traffic.read_text().splitlines()]
    future_connection = f"inter-satellite|{SOURCE}|{DESTINATION}"
    recorder_index = next(
        index
        for index, value in enumerate(records)
        if value.get("record_type") == "recorder_end"
    )
    records[recorder_index:recorder_index] = [
        {
            "record_type": "interface_identity",
            "generation": 1,
            "boundary_timestamp": END,
            "node_slot": 0,
            "node_id": SOURCE,
            "ifindex": 30,
            "interface": "sv2futurea",
            "connection_id": future_connection,
            "peer_node_id": DESTINATION,
            "route_destinations": (),
            "validity": "exact-generation",
        },
        {
            "record_type": "interface_identity",
            "generation": 1,
            "boundary_timestamp": END,
            "node_slot": 2,
            "node_id": DESTINATION,
            "ifindex": 31,
            "interface": "sv2futureb",
            "connection_id": future_connection,
            "peer_node_id": SOURCE,
            "route_destinations": (),
            "validity": "exact-generation",
        },
    ]
    traffic.write_text(
        "".join(json.dumps(value) + "\n" for value in records),
        encoding="utf-8",
    )

    data = collect_traffic_replay_data(traffic, dynamic, compiler, workload)
    networks = {
        value["generation"]: value for value in data["active_networks"]
    }

    assert len(networks[0]["connections"]) == 2
    assert future_connection not in {
        value["connection_id"] for value in networks[0]["connections"]
    }
    assert future_connection in {
        value["connection_id"] for value in networks[1]["connections"]
    }


def test_position_projection_preserves_exact_sample_and_marks_interpolation() -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    samples = (
        (
            start,
            {
                "positions": {
                    "node": {
                        "ecef_m": {"x": 0.0, "y": 10.0, "z": 20.0},
                        "geographic": {"latitude_deg": 1.0},
                    }
                }
            },
        ),
        (
            datetime(2026, 1, 1, 0, 0, 10, tzinfo=timezone.utc),
            {
                "positions": {
                    "node": {
                        "ecef_m": {"x": 10.0, "y": 20.0, "z": 30.0},
                        "geographic": {"latitude_deg": 2.0},
                    }
                }
            },
        ),
    )

    exact = _position_at(samples, "node", start)
    middle = _position_at(
        samples,
        "node",
        datetime(2026, 1, 1, 0, 0, 5, tzinfo=timezone.utc),
    )

    assert exact["provenance"] == "retained-position"
    assert exact["visualization_only"] is False
    assert middle["ecef_m"] == {"x": 5.0, "y": 15.0, "z": 25.0}
    assert middle["provenance"] == "linear-ecef-interpolation"
    assert middle["visualization_only"] is True
    assert _position_at(
        samples,
        "node",
        datetime(2025, 12, 31, 23, 59, 59, tzinfo=timezone.utc),
    ) is None


def test_arbitrary_time_generation_uses_latest_boundary_and_exact_new_state() -> None:
    networks = (
        {"generation": 0, "boundary_timestamp": ORIGIN},
        {"generation": 1, "boundary_timestamp": END},
    )

    assert _generation_at(
        networks,
        datetime(2025, 12, 31, 23, 59, 59, tzinfo=timezone.utc),
    ) is None
    assert _generation_at(
        networks,
        datetime(2026, 1, 1, 0, 0, 5, tzinfo=timezone.utc),
    ) == 0
    assert _generation_at(
        networks,
        datetime(2026, 1, 1, 0, 0, 10, tzinfo=timezone.utc),
    ) == 1


def test_zone_geometry_splits_dateline_without_packet_link_semantics() -> None:
    value = _project_zone_geometry(
        {
            "zone_id": "dateline-zone",
            "geometry": {
                "kind": "latitude-longitude-rectangle",
                "south_latitude_deg": -10.0,
                "north_latitude_deg": 10.0,
                "west_longitude_deg": 170.0,
                "east_longitude_deg": -170.0,
            },
            "representative_point": {
                "latitude_deg": 0.0,
                "longitude_deg": 180.0,
            },
        }
    )

    assert value["geometry"]["longitude_segments"] == (
        {"west_longitude_deg": 170.0, "east_longitude_deg": 180.0},
        {"west_longitude_deg": -180.0, "east_longitude_deg": -170.0},
    )
    assert value["packet_link_semantics"] is False


def test_workload_lanes_keep_overlapping_streams_and_qualify_gaps() -> None:
    def endpoint(kind: str, stable_id: str) -> dict[str, object]:
        return {
            "endpoint_kind": kind,
            "node_id": stable_id if kind == "node" else None,
            "zone_id": stable_id if kind == "zone" else None,
        }

    transactions = (
        {
            "transaction_id": "ping-a",
            "first_simulation_timestamp": "2026-01-01T00:00:02+00:00",
            "source_endpoint": endpoint("node", "ground-a"),
            "destination_endpoint": endpoint("zone", "zone-a"),
            "pairing_status": "paired",
            "recorded_duration_ms": 10.0,
            "request": {"observed_path": ({},)},
            "reply": {"observed_path": ({},)},
        },
        {
            "transaction_id": "ping-b",
            "first_simulation_timestamp": "2026-01-01T00:00:03+00:00",
            "source_endpoint": endpoint("zone", "zone-b"),
            "destination_endpoint": endpoint("node", "ground-b"),
            "pairing_status": "paired",
            "recorded_duration_ms": 12.0,
            "request": {"observed_path": ({},)},
            "reply": {"observed_path": ({},)},
        },
    )
    series = (
        {
            "series_id": "A",
            "display_label": "A",
            "direction": "ground-station-to-zone",
            "ground_station": {"node_id": "ground-a", "display_name": "A"},
            "zone": {"zone_id": "zone-a", "packet_ipv4": "198.18.0.1"},
            "schedule": {
                "start_time": ORIGIN,
                "end_time": END,
                "interval_seconds": 2.0,
                "scheduled_slots": 5,
            },
            "semantic_purpose": "overlap A",
            "event_focus": "event A",
        },
        {
            "series_id": "B",
            "display_label": "B",
            "direction": "zone-to-ground-station",
            "ground_station": {"node_id": "ground-b", "display_name": "B"},
            "zone": {"zone_id": "zone-b", "packet_ipv4": "198.18.0.2"},
            "schedule": {
                "start_time": ORIGIN,
                "end_time": END,
                "interval_seconds": 1.0,
                "scheduled_slots": 10,
            },
            "semantic_purpose": "overlap B",
            "event_focus": "event B",
        },
    )

    projected, lanes = _project_workload_activity(
        transactions,
        {"series": series},
    )

    assert [value["series_id"] for value in projected] == ["A", "B"]
    assert [value["observed_transactions"] for value in lanes] == [1, 1]
    assert [value["observed_gap_slots"] for value in lanes] == [4, 9]
    assert all("does not prove" in value["gap_qualification"] for value in lanes)


def test_long_lived_ping_sequence_prevents_scheduled_slot_drift() -> None:
    source = {
        "endpoint_kind": "node",
        "stable_id": "ground-a",
        "node_id": "ground-a",
    }
    destination = {
        "endpoint_kind": "zone",
        "stable_id": "zone-a",
        "zone_id": "zone-a",
    }
    transactions = tuple(
        {
            "transaction_id": f"ping-{index:03d}",
            "identifier": 17,
            "sequence": index + 1,
            "first_kernel_monotonic_ns": index,
            "first_simulation_timestamp": datetime.fromtimestamp(
                index * 3.015, tz=timezone.utc
            ).replace(year=2026, month=1, day=1).isoformat(),
            "source_endpoint": source,
            "destination_endpoint": destination,
            "pairing_status": "paired",
            "recorded_duration_ms": 20.0,
            "request": {"observed_path": ({},)},
            "reply": {"observed_path": ({},)},
        }
        for index in range(80)
    )
    series = {
        "series_id": "S5",
        "display_label": "drifting Linux ping",
        "direction": "ground-station-to-zone",
        "ground_station": {"node_id": "ground-a", "display_name": "A"},
        "zone": {"zone_id": "zone-a", "packet_ipv4": "198.18.0.1"},
        "schedule": {
            "start_time": ORIGIN,
            "end_time": "2026-01-01T00:04:00+00:00",
            "interval_seconds": 3.0,
            "scheduled_slots": 80,
        },
        "semantic_purpose": "cadence regression",
        "event_focus": "none",
    }

    projected, lanes = _project_workload_activity(
        transactions,
        {"series": (series,)},
    )

    assert [value["slot_index"] for value in lanes[0]["transaction_markers"]] == list(
        range(80)
    )
    assert lanes[0]["observed_gap_slots"] == 0
    assert all(value["series_id"] == "S5" for value in projected)


def test_identity_contradiction_fails_closed(tmp_path: Path) -> None:
    traffic, dynamic, compiler, workload = _inputs(
        tmp_path,
        mismatch_interface_identity=True,
    )

    with pytest.raises(ValueError, match="interface identity contradicts its node slot"):
        collect_traffic_replay_data(traffic, dynamic, compiler, workload)


def test_losses_and_incomplete_recorder_are_visible(tmp_path: Path) -> None:
    traffic, dynamic, compiler, workload = _inputs(
        tmp_path,
        losses=3,
        recorder_complete=False,
    )

    data = collect_traffic_replay_data(traffic, dynamic, compiler, workload)
    transaction = data["ping_transactions"][0]

    assert data["overview"]["telemetry_complete"] is False
    assert data["overview"]["reported_losses"] == 3
    assert transaction["evidence_complete"] is False
    assert "durable telemetry recorder did not complete" in transaction["request"][
        "warnings"
    ]


def test_stale_index_is_invalidated_before_projection(tmp_path: Path) -> None:
    traffic, dynamic, compiler, workload = _inputs(tmp_path)
    index = traffic.parent / "index.html"
    index.write_text("stale page", encoding="utf-8")
    compiler.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="requires BPF Compiler"):
        write_traffic_replay_tool(
            traffic, dynamic, compiler, workload, traffic.parent
        )

    assert not index.exists()
    assert traffic.exists()


def test_write_produces_dependency_free_file_url_bundle(tmp_path: Path) -> None:
    traffic, dynamic, compiler, workload = _inputs(tmp_path)

    result = write_traffic_replay_tool(
        traffic, dynamic, compiler, workload, traffic.parent
    )
    page = (traffic.parent / "index.html").read_text(encoding="utf-8")

    assert result["status"] == "PASS"
    assert set(result["files"]) == {
        "traffic-telemetry.jsonl",
        "traffic-workload.json",
        "traffic-replay.json",
        "index.html",
        "summary.txt",
        "inventory.json",
        "checksums.sha256",
    }
    for required in (
        "Observed Traffic",
        "真实流量",
        'id="globe"',
        'id="simulation-toggle"',
        'id="simulation-scrubber"',
        'id="recent-window"',
        'id="flow-filter"',
        'id="globe-ping-control"',
        'id="globe-ping-context"',
        'id="replay-ping"',
        "control.hidden=!tx",
        '$("replay-ping").disabled=!tx',
        "Observed Pings",
        "Planned Workload / Schedule Comparison",
        "Scheduled slot without tc-BPF observation",
        "Interpolated position is visualization-only",
        "function networkAt(timestampMs)",
        "networkByGeneration.get(generation)",
        "function positionsAt(timestampMs)",
        "simulationMs-recentWindowSeconds*1000",
        "pingReplay.phase+=1",
        '$("globe").scrollIntoView({behavior:"smooth",block:"center"})',
        "function drawReplayPulse(positions,width,height)",
        'pingReplay.label==="Request"',
        'pingReplay?.label==="Reply"',
        "ctx.setLineDash(dash)",
        "if(pingReplay)drawGlobe()",
        "function normalizeGlobeView()",
        "const MAX_VIEW_LAT=89*Math.PI/180",
        "viewLat=Math.max(-MAX_VIEW_LAT,Math.min(MAX_VIEW_LAT,viewLat))",
        "Observed Ping activity",
        "not bandwidth, bytes, RTT, utilization, or queue occupancy",
        "function drawSegment(a,b",
        ".flow-list { overflow:auto",
    ):
        assert required in page
    for forbidden in (
        "<script src=",
        "fetch(",
        "WebSocket",
        "EventSource",
        "http://",
        "https://",
        'id="packet-play"',
        'id="packet-pause"',
        'id="packet-restart"',
        "data-lane=",
        "recentPulses",
        "Date.now(",
        '$("selected-ping-section").scrollIntoView',
    ):
        assert forbidden not in page
    assert "context_frames" not in page
    assert "Zone center" not in page
    assert len(page.encode("utf-8")) < 1_000_000


def test_globe_vertical_rotation_clamps_without_longitude_flip() -> None:
    source = Path(
        __import__("tools.traffic.renderer", fromlist=["__file__"]).__file__
    ).read_text(encoding="utf-8")
    assert "function normalizeGlobeView(){" in source
    assert "const MAX_VIEW_LAT=89*Math.PI/180" in source
    assert (
        "viewLat=Math.max(-MAX_VIEW_LAT,Math.min(MAX_VIEW_LAT,viewLat))"
        in source
    )
    assert "viewLat=Math.PI-viewLat;viewLon+=Math.PI" not in source
    assert "viewLat=-Math.PI-viewLat;viewLon+=Math.PI" not in source

    def normalize(latitude: float, longitude: float) -> tuple[float, float]:
        maximum = 89 * 3.141592653589793 / 180
        latitude = max(-maximum, min(maximum, latitude))
        longitude = (
            (longitude + 3.141592653589793) % (2 * 3.141592653589793)
            - 3.141592653589793
        )
        return latitude, longitude

    values = (
        normalize(3.141592653589793 / 2 + 0.2, 0.4),
        normalize(-3.141592653589793 / 2 - 0.3, -0.7),
    )

    assert values[0][0] == pytest.approx(89 * 3.141592653589793 / 180)
    assert values[0][1] == pytest.approx(0.4)
    assert values[1][0] == pytest.approx(-89 * 3.141592653589793 / 180)
    assert values[1][1] == pytest.approx(-0.7)


def test_no_workload_writes_the_same_primary_replay_surface(tmp_path: Path) -> None:
    traffic, dynamic, compiler, _workload = _inputs(tmp_path)

    result = write_traffic_replay_tool(
        traffic, dynamic, compiler, None, traffic.parent
    )
    page = (traffic.parent / "index.html").read_text(encoding="utf-8")
    inventory = json.loads(
        (traffic.parent / "inventory.json").read_text(encoding="utf-8")
    )

    assert "traffic-workload.json" not in result["files"]
    assert "traffic_workload" not in inventory["inputs"]
    for primary in (
        'id="globe"',
        'id="flow-list"',
        'id="simulation-scrubber"',
        'id="ping-table"',
        'id="replay-ping"',
    ):
        assert primary in page
    assert "section.hidden=!DATA.workload" in page


def test_tool_owned_cli_writes_replay_without_workload_control_input(
    monkeypatch,
    tmp_path: Path,
) -> None:
    traffic, dynamic, compiler, _workload = _inputs(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "tools.traffic",
            "--telemetry",
            str(traffic),
            "--slice",
            str(dynamic),
            "--bpf-compiler",
            str(compiler),
            "--output",
            str(traffic.parent),
        ],
    )

    assert traffic_tool_main() == 0
    inventory = json.loads(
        (traffic.parent / "inventory.json").read_text(encoding="utf-8")
    )
    assert "traffic_workload" not in inventory["inputs"]
    assert (traffic.parent / "index.html").is_file()


def test_zero_and_many_observed_flows_keep_a_fixed_globe_and_scroll_browser(
    tmp_path: Path,
) -> None:
    traffic, dynamic, compiler, _workload = _inputs(tmp_path)
    records = [json.loads(line) for line in traffic.read_text().splitlines()]
    traffic.write_text(
        "".join(
            json.dumps(value) + "\n"
            for value in records
            if value.get("record_type") != "packet"
        ),
        encoding="utf-8",
    )
    zero = collect_traffic_replay_data(traffic, dynamic, compiler, None)
    zero_page = render_traffic_replay_page(zero)

    assert zero["observed_flows"] == ()
    assert "没有真实 observed traffic" in zero_page
    assert "height:min(70vh, 760px)" in zero_page

    one = collect_traffic_replay_data(*_inputs(tmp_path / "one")[:3], None)
    many = {
        **one,
        "observed_flows": tuple(
            {
                **one["observed_flows"][0],
                "flow_id": f"observed-flow-{index:04d}",
                "display_label": f"arbitrary-{index} ↔ endpoint · ICMP Ping",
            }
            for index in range(100)
        ),
    }
    many_page = render_traffic_replay_page(many)

    assert many_page.count('"flow_id":"observed-flow-') >= 100
    assert ".flow-list { overflow:auto" in many_page
    assert "traffic-lane" not in many_page
    assert "zone-marker" not in many_page


def test_latest_canonical_artifact_is_v3_or_explicitly_stale_before_rerun() -> None:
    root = (
        Path(__file__).parents[2]
        / "examples/mini_satellite_internet/output/inspection"
    )
    telemetry = root / "traffic/traffic-telemetry.jsonl"
    workload = root.parent / "inputs/traffic-workload.json"
    dynamic = root / "slice/slice-difference.json"
    compiler = root / "bpf-compiler/bpf-compiler-inspection.json"
    replay = root / "traffic/traffic-replay.json"
    if not workload.is_file():
        if not replay.is_file():
            pytest.skip("canonical replay artifacts are not present")
        stale = json.loads(replay.read_text(encoding="utf-8"))
        assert stale["format_version"] < 3
        assert "workload" not in stale
        assert "traffic_lanes" not in stale
        return
    if not all(path.is_file() for path in (telemetry, dynamic, compiler)):
        pytest.skip("canonical runtime telemetry is not present before the live run")

    data = collect_traffic_replay_data(telemetry, dynamic, compiler, workload)

    assert data["format_version"] == 3
    assert [value["series_id"] for value in data["traffic_lanes"]] == [
        "S1",
        "S2",
        "S3",
        "S4",
        "S5",
        "S6",
        "S7",
    ]
    assert data["workload"]["inventory"] == {
        "series": 7,
        "scheduled_slots": 739,
        "maximum_concurrent_series": 4,
    }
    assert data["workload"]["static_expectation"] == {
        "status": "pre-runtime-estimate-only",
        "paired_transactions": 534,
        "scheduled_slots_without_tc_bpf_observation": 205,
        "packet_events": 4430,
    }
    assert data["overview"]["ping_transactions"] == len(
        data["ping_transactions"]
    )
    assert data["overview"]["observed_workload_transactions"] == sum(
        value["observed_transactions"] for value in data["traffic_lanes"]
    )
    assert data["overview"]["scheduled_slots_without_tc_bpf_observation"] == sum(
        value["observed_gap_slots"] for value in data["traffic_lanes"]
    )
    assert all(
        "does not prove" in value["gap_qualification"]
        for value in data["traffic_lanes"]
    )
    assert data["workload"]["authority"] == {
        "status": "planned-example-workload",
        "proves_command_execution": False,
        "proves_packet_emission": False,
        "proves_timeout": False,
        "proves_packet_loss": False,
        "packet_observation_authority": "traffic-telemetry.jsonl",
    }
