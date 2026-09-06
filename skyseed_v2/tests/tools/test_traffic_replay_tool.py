from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from ipaddress import ip_interface
import gzip
import json
from pathlib import Path
import shutil

import pytest

from examples.complete_static_emulator.emu import build_emulator
from examples.complete_static_emulator.simu import (
    create_complete_static_simulator,
    run_complete_static_chain,
)
from tools.bpf_compiler import write_bpf_compiler_tool
from tools.planner import collect_planner_data
from tools.planner.reader import read_planner_history
from tools.traffic import collect_traffic_replay_data, write_traffic_replay_tool
from tools.traffic.__main__ import main as traffic_tool_main
from tools.traffic.projection import project_traffic_replay
from tools.traffic.reader import (
    TrafficReplayRead,
    _ping_transactions,
    read_traffic_replay,
)
from tools.traffic.renderer import render_traffic_replay_page
from skyseed_v2.experiment.traffic.probe_plan import (
    TrafficProbePlanBuilder,
    write_traffic_probe_plan,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TOOL_SOURCES = tuple(sorted((PROJECT_ROOT / "tools/traffic").glob("*.py")))
START_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def evidence_set(tmp_path_factory):
    root = tmp_path_factory.mktemp("traffic-tool")
    history = root / "history"
    simulator = create_complete_static_simulator(build_emulator())
    plans, realizations, _ = run_complete_static_chain(
        simulator, evidence_path=history
    )
    compiler_output = root / "compiler"
    write_bpf_compiler_tool(plans, realizations, compiler_output)
    evidence = read_planner_history(history / "planning.jsonl.gz")
    planner_data = collect_planner_data(evidence)
    telemetry = _telemetry(
        root / "traffic-telemetry.jsonl",
        history / "physical-frames.jsonl",
        planner_data,
        json.loads(
            (compiler_output / "bpf-compiler-inspection.json").read_text()
        ),
    )
    return {
        "root": root,
        "telemetry": telemetry,
        "frames": history / "physical-frames.jsonl",
        "planning": history / "planning.jsonl.gz",
        "compiler_json": compiler_output / "bpf-compiler-inspection.json",
        "compiler_jsonl": history / "compiler.jsonl",
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


def _collect(
    evidence_set,
    *,
    telemetry: Path | None = None,
    compiler: Path | None = None,
    workload: Path | None = None,
    qualification: tuple[Path, Path, Path] | None = None,
) -> dict[str, object]:
    optional = () if qualification is None else qualification
    return collect_traffic_replay_data(
        evidence_set["telemetry"] if telemetry is None else telemetry,
        evidence_set["frames"],
        evidence_set["planning"],
        evidence_set["compiler_json"] if compiler is None else compiler,
        workload,
        *optional,
    )


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
        evidence_set["planning"],
        evidence_set["compiler_json"],
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


def test_replay_uses_physical_frames_and_explicit_planned_route_truth(
    evidence_set,
) -> None:
    data = collect_traffic_replay_data(
        evidence_set["telemetry"],
        evidence_set["frames"],
        evidence_set["planning"],
        evidence_set["compiler_json"],
    )

    assert data["inventory"]["physical_frames"] == 4
    assert data["inventory"]["plans"] == 2
    assert data["inventory"]["compiled_realizations"] == 2
    assert data["inventory"]["formal_activation_boundaries"] == 4
    by_id = {value["identifier"]: value for value in data["transactions"]}
    assert by_id[10]["planned_reachability"] == "reachable"
    assert by_id[10]["classification"] == "observed-reachable"
    assert by_id[11]["planned_reachability"] == "unreachable"
    assert by_id[11]["planned_unreachable_reason"] == "ZONE_UNSERVED"
    assert by_id[11]["classification"] == "expected-unreachable"
    assert data["overview"]["telemetry_complete"] is True
    assert data["overview"]["classification_counts"] == {
        "expected-unreachable": 1,
        "observed-reachable": 1,
    }


def test_compact_compiler_history_retains_lineage_without_inventing_zone_prefix(
    evidence_set,
) -> None:
    data = collect_traffic_replay_data(
        evidence_set["telemetry"],
        evidence_set["frames"],
        evidence_set["planning"],
        evidence_set["compiler_jsonl"],
    )
    assert data["overview"]["compiler_evidence"] == (
        "compact-producer-lineage"
    )
    assert data["transactions"][0]["planned_reachability"] == "reachable"
    assert data["transactions"][1]["planned_reachability"] == "unknown"
    assert data["transactions"][1]["classification"] == "unclassified"


def test_compact_compiler_inspection_format_four_is_explicitly_supported(
    tmp_path: Path, evidence_set
) -> None:
    compiler = tmp_path / "compiler-format-four.json"
    value = json.loads(evidence_set["compiler_json"].read_text())
    value["format_version"] = 4
    compiler.write_text(json.dumps(value), encoding="utf-8")

    data = collect_traffic_replay_data(
        evidence_set["telemetry"],
        evidence_set["frames"],
        evidence_set["planning"],
        compiler,
    )

    assert data["overview"]["compiler_evidence"] == (
        "compact-compiler-inspection-v4"
    )


def test_unknown_compiler_inspection_format_fails_clearly(
    tmp_path: Path, evidence_set
) -> None:
    compiler = tmp_path / "compiler-unknown-format.json"
    value = json.loads(evidence_set["compiler_json"].read_text())
    value["format_version"] = 99
    compiler.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(ValueError, match="format_version must be one of 2, 3, or 4"):
        collect_traffic_replay_data(
            evidence_set["telemetry"],
            evidence_set["frames"],
            evidence_set["planning"],
            compiler,
        )


def test_reader_rejects_physical_frame_coverage_drift(
    tmp_path, evidence_set
) -> None:
    history = Path(
        shutil.copytree(
            evidence_set["frames"].parent, tmp_path / "corrupt-history"
        )
    )
    corrupt = history / "physical-frames.jsonl"
    rows = [
        json.loads(line)
        for line in corrupt.read_text().splitlines()
    ]
    rows[-1]["positions"].pop()
    corrupt.write_text(
        "".join(
            json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
            for value in rows
        ),
        encoding="utf-8",
    )
    manifest_path = history / "domain-history-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload = corrupt.read_bytes()
    manifest["streams"]["physical_frames"].update(
        {
            "sha256": sha256(payload).hexdigest(),
            "bytes": len(payload),
            "record_count": len(rows) - 1,
        }
    )
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="node coverage"):
        collect_traffic_replay_data(
            evidence_set["telemetry"],
            corrupt,
            history / "planning.jsonl.gz",
            evidence_set["compiler_json"],
        )


def test_replay_rejects_mixed_domain_history_roots(
    tmp_path, evidence_set
) -> None:
    copied = Path(
        shutil.copytree(
            evidence_set["frames"].parent, tmp_path / "other-history"
        )
    )

    with pytest.raises(ValueError, match="one sealed domain-history root"):
        collect_traffic_replay_data(
            evidence_set["telemetry"],
            copied / "physical-frames.jsonl",
            evidence_set["planning"],
            evidence_set["compiler_json"],
        )


def test_replay_does_not_bind_prepublication_packet_to_future_state(
    tmp_path, evidence_set
) -> None:
    rows = [
        json.loads(line)
        for line in evidence_set["telemetry"].read_text().splitlines()
    ]
    monotonic_origin = next(
        value["monotonic_origin_ns"]
        for value in rows
        if value["record_type"] == "simulation_clock_anchor"
    )
    for value in rows:
        if (
            value["record_type"] == "packet"
            and value["icmp"]["identifier"] == 10
        ):
            value["kernel_monotonic_ns"] = monotonic_origin - 1_000_000
    telemetry = tmp_path / "prepublication-telemetry.jsonl"
    telemetry.write_text(
        "".join(json.dumps(value) + "\n" for value in rows),
        encoding="utf-8",
    )

    data = collect_traffic_replay_data(
        telemetry,
        evidence_set["frames"],
        evidence_set["planning"],
        evidence_set["compiler_json"],
    )
    transaction = next(
        value for value in data["transactions"] if value["identifier"] == 10
    )
    assert transaction["formal_boundary_timestamp"] is None
    assert transaction["planned_reachability"] == "unknown"
    assert transaction["classification"] == "unclassified"


def test_writer_emits_json_html_checksums_and_tool_accounting(
    tmp_path, evidence_set
) -> None:
    result = write_traffic_replay_tool(
        evidence_set["telemetry"],
        evidence_set["frames"],
        evidence_set["planning"],
        evidence_set["compiler_json"],
        None,
        tmp_path,
    )

    assert result["status"] == "PASS"
    assert result["artifact_metrics"]["wall_seconds"] > 0
    assert result["artifact_metrics"]["input_bytes"] > 0
    assert result["artifact_metrics"]["output_bytes"] > 0
    assert {value.name for value in tmp_path.iterdir()} == {
        "checksums.sha256",
        "index.html",
        "inventory.json",
        "summary.txt",
        "traffic-replay.json",
    }
    payload = json.loads((tmp_path / "traffic-replay.json").read_text())
    assert payload["tool_accounting"]["peak_rss_bytes"] > 0
    page = (tmp_path / "index.html").read_text()
    assert "explicit planned reachability" in page
    assert "unexplained reachable loss" in page
    for line in (tmp_path / "checksums.sha256").read_text().splitlines():
        digest, relative = line.split("  ", 1)
        assert sha256((tmp_path / relative).read_bytes()).hexdigest() == digest


def test_traffic_sources_use_no_retired_dynamic_records() -> None:
    combined = "\n".join(path.read_text() for path in TOOL_SOURCES)
    assert "compute_observation(" not in combined
    assert "compile_bpf(" not in combined
    assert "missing forwarding is planned-unreachable" not in combined


def test_node_endpoint_transaction_clock_and_active_graph(evidence_set) -> None:
    data = _collect(evidence_set)
    transaction = next(
        value for value in data["transactions"] if value["identifier"] == 10
    )
    request = transaction["request"]
    anchor = next(
        value
        for value in _telemetry_rows(evidence_set["telemetry"])
        if value["record_type"] == "simulation_clock_anchor"
    )

    assert request["simulation_timestamp"] == (
        datetime.fromisoformat(anchor["nominal_origin"])
        + timedelta(seconds=2)
    ).isoformat()
    assert transaction["observed_reply"] is True
    assert transaction["packet_observations"] == 2
    assert transaction["planned_reachability"] == "reachable"
    assert transaction["formal_boundary_timestamp"] == anchor["nominal_origin"]
    assert len(data["planned_evidence"]["formal_transitions"]) == 4
    assert all(
        set(value["state_summary"])
        == {
            "operational_connection_count",
            "zone_assignment_count",
            "zones_served",
            "zones_unserved",
            "reachable_route_count",
            "unreachable_route_count",
        }
        for value in data["planned_evidence"]["formal_transitions"]
    )


def test_zone_endpoint_uses_service_satellite_as_physical_source(
    tmp_path: Path,
    evidence_set,
) -> None:
    base = _collect(evidence_set)
    first_transition = base["planned_evidence"]["formal_transitions"][0]
    served = next(
        value
        for value in first_transition["transition"][
            "zone_service_transitions"
        ]
        if value["current_satellite_id"] is not None
    )
    prefix = next(
        value["packet_prefix"]
        for value in base["compiled_evidence"]["realizations"][-1][
            "zone_prefixes"
        ]
        if value["zone_id"] == served["zone_id"]
    )
    zone_address = str(ip_interface(prefix).ip)
    telemetry, rows = _copied_telemetry(tmp_path, evidence_set)
    for value in rows:
        if value.get("record_type") != "packet":
            continue
        icmp = value["icmp"]
        if icmp["identifier"] != 10:
            continue
        if icmp["type"] == 8:
            value["source_ipv4"] = zone_address
        else:
            value["destination_ipv4"] = zone_address
    _write_telemetry(telemetry, rows)

    data = _collect(evidence_set, telemetry=telemetry)
    transaction = next(
        value for value in data["transactions"] if value["identifier"] == 10
    )

    assert transaction["planned_reachability"] == "reachable"
    assert transaction["classification"] == "observed-reachable"
    assert transaction["route_outcome"]["demand"]["source"] == served[
        "current_satellite_id"
    ]
    assert transaction["request"]["source_ipv4"] == zone_address


def test_unavailable_zone_destination_preserves_observed_drop(
    tmp_path: Path,
    evidence_set,
) -> None:
    telemetry, rows = _copied_telemetry(tmp_path, evidence_set)
    for value in rows:
        if (
            value.get("record_type") == "packet"
            and value["icmp"]["identifier"] == 11
        ):
            value["decision"] = "drop"
            value["reason"] = "no-route"
    _write_telemetry(telemetry, rows)

    data = _collect(evidence_set, telemetry=telemetry)
    transaction = next(
        value for value in data["transactions"] if value["identifier"] == 11
    )
    raw = next(
        value
        for value in data["telemetry"]["packets"]
        if value["icmp"]["identifier"] == 11
    )

    assert transaction["observed_reply"] is False
    assert transaction["planned_reachability"] == "unreachable"
    assert transaction["planned_unreachable_reason"] == "ZONE_UNSERVED"
    assert transaction["classification"] == "expected-unreachable"
    assert raw["decision"] == "drop"
    assert raw["reason"] == "no-route"


def test_workload_schedule_is_read_and_projected_as_qualified_lane(
    tmp_path: Path,
    evidence_set,
) -> None:
    workload = _workload(tmp_path / "traffic-workload.json", series=[_series("S1")])

    data = _collect(evidence_set, workload=workload)

    assert data["workload"]["inventory"] == {
        "series": 1,
        "scheduled_slots": 5,
        "maximum_concurrent_series": 1,
    }
    assert data["workload"]["series"][0]["series_id"] == "S1"
    assert data["workload"]["authority"]["proves_timeout"] is False
    assert data["workload"]["authority"]["proves_packet_loss"] is False
    assert data["overview"]["classification_counts"] == {
        "expected-unreachable": 1,
        "observed-reachable": 1,
    }


def test_workload_is_optional_and_observed_flow_is_telemetry_derived(
    evidence_set,
) -> None:
    data = _collect(evidence_set)

    assert data["workload"] is None
    assert data["inventory"]["ping_transactions"] == 2
    assert {value["identifier"] for value in data["transactions"]} == {10, 11}
    assert data["inventory"]["packet_observations"] == 3


def test_workload_cannot_claim_timeout_or_packet_loss_evidence(
    tmp_path: Path,
    evidence_set,
) -> None:
    workload = _workload(tmp_path / "traffic-workload.json", series=[_series("S1")])
    value = json.loads(workload.read_text(encoding="utf-8"))
    value["authority"]["proves_timeout"] = True
    value["authority"]["proves_packet_loss"] = True
    workload.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(ValueError, match="runtime evidence|authority"):
        _collect(evidence_set, workload=workload)


def test_zone_prefix_conflict_and_instability_fail_closed(
    tmp_path: Path,
    evidence_set,
) -> None:
    conflict = tmp_path / "conflicting-compiler.json"
    value = json.loads(evidence_set["compiler_json"].read_text())
    prefixes = value["realizations"][-1]["zone_prefixes"]
    prefixes[1]["packet_prefix"] = prefixes[0]["packet_prefix"]
    conflict.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(ValueError, match="prefix|Zone"):
        _collect(evidence_set, compiler=conflict)

    unstable = tmp_path / "unstable-compiler.json"
    value = json.loads(evidence_set["compiler_json"].read_text())
    value["realizations"][-1]["zone_prefixes"][0][
        "packet_prefix"
    ] = "198.18.0.99/32"
    unstable.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(ValueError, match="prefix|stable"):
        _collect(evidence_set, compiler=unstable)


def test_unknown_endpoint_degrades_without_inventing_identity(
    tmp_path: Path,
    evidence_set,
) -> None:
    telemetry, rows = _copied_telemetry(tmp_path, evidence_set)
    for value in rows:
        if value.get("record_type") != "packet":
            continue
        icmp = value["icmp"]
        if icmp["identifier"] != 10:
            continue
        if icmp["type"] == 8:
            value["source_ipv4"] = "203.0.113.1"
        else:
            value["destination_ipv4"] = "203.0.113.1"
    _write_telemetry(telemetry, rows)

    data = _collect(evidence_set, telemetry=telemetry)
    transaction = next(
        value for value in data["transactions"] if value["identifier"] == 10
    )

    assert transaction["request"]["source_ipv4"] == "203.0.113.1"
    assert transaction["route_outcome"] is None
    assert transaction["planned_reachability"] == "unknown"
    assert transaction["classification"] == "unclassified"
    assert "203.0.113.1" not in {
        value["runtime_ipv4"] for value in data["telemetry"]["nodes"]
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

    assert len(transactions) == 2
    ambiguous = next(
        value for value in transactions if value["pairing_status"] == "ambiguous"
    )
    assert ambiguous["reply"] is None
    assert any(
        value["pairing_status"] == "paired" for value in transactions
    )


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


def test_future_generation_links_do_not_enter_current_active_graph(
    tmp_path: Path,
    evidence_set,
) -> None:
    telemetry, rows = _copied_telemetry(tmp_path, evidence_set)
    future = _interface_pair(
        rows,
        generation=1,
        boundary="2026-01-01T00:00:10+00:00",
        first_ifindex=30,
    )
    _insert_before_packets(rows, future)
    _write_telemetry(telemetry, rows)

    data = _collect(evidence_set, telemetry=telemetry)
    transaction = next(
        value for value in data["transactions"] if value["identifier"] == 10
    )

    assert {value["generation"] for value in data["telemetry"]["interfaces"]} == {
        1
    }
    assert transaction["formal_boundary_timestamp"] == (
        "2026-01-01T00:00:00+00:00"
    )
    assert transaction["planned_reachability"] == "reachable"
    assert "connection_id" not in transaction


def test_inactive_ingress_can_use_unique_future_physical_identity_without_leaking_link(
    tmp_path: Path,
    evidence_set,
) -> None:
    telemetry, rows = _copied_telemetry(tmp_path, evidence_set)
    future = _interface_pair(
        rows,
        generation=1,
        boundary="2026-01-01T00:00:10+00:00",
        first_ifindex=30,
    )
    nodes = _node_rows(rows)
    anchor = next(
        value for value in rows if value["record_type"] == "simulation_clock_anchor"
    )
    packet = _packet(
        anchor["monotonic_origin_ns"] + 5_000_000_000,
        int(nodes[0]["node_slot"]),
        str(nodes[0]["runtime_ipv4"]),
        str(nodes[1]["runtime_ipv4"]),
        8,
        99,
    )
    packet.update(
        {
            "generation": 0,
            "ingress_ifindex": 30,
            "decision": "drop",
            "reason": "inactive-ingress",
        }
    )
    _insert_before_packets(rows, future)
    end = next(value for value in rows if value["record_type"] == "recorder_end")
    rows.insert(rows.index(end), packet)
    end["packet_records"] += 1
    _write_telemetry(telemetry, rows)

    data = _collect(evidence_set, telemetry=telemetry)
    raw = next(
        value
        for value in data["telemetry"]["packets"]
        if value["icmp"]["identifier"] == 99
    )
    transaction = next(
        value for value in data["transactions"] if value["identifier"] == 99
    )

    assert raw["reason"] == "inactive-ingress"
    assert raw["generation"] == 0
    assert transaction["formal_boundary_timestamp"] == (
        "2026-01-01T00:00:00+00:00"
    )
    assert "connection_id" not in transaction


def test_inactive_ingress_identity_fallback_rejects_reuse_ambiguity(
    tmp_path: Path,
    evidence_set,
) -> None:
    telemetry, rows = _copied_telemetry(tmp_path, evidence_set)
    first = _interface_pair(
        rows,
        generation=1,
        boundary="2026-01-01T00:00:10+00:00",
        first_ifindex=30,
    )
    second = deepcopy(first)
    for value in second:
        value["generation"] = 2
        value["boundary_timestamp"] = "2026-01-01T00:00:20+00:00"
        value["connection_id"] = "ambiguous-reused-physical-interface"
    _insert_before_packets(rows, first + second)
    _write_telemetry(telemetry, rows)

    with pytest.raises(ValueError, match="ambiguous|reuse|interface|connection"):
        _collect(evidence_set, telemetry=telemetry)


def test_position_projection_preserves_exact_sample_and_marks_interpolation(
    evidence_set,
) -> None:
    data = _collect(evidence_set)
    frames = data["physical_evidence"]["frames"]

    assert len(frames) == 4
    assert [value["timestamp"] for value in frames] == [
        value["timestamp"]
        for value in data["planned_evidence"]["formal_transitions"]
    ]
    assert all(value["positions"] for value in frames)
    assert "interpolat" not in json.dumps(frames).lower()
    assert "visualization_only" not in json.dumps(frames)


def test_arbitrary_time_generation_uses_latest_boundary_and_exact_new_state(
    tmp_path: Path,
    evidence_set,
) -> None:
    telemetry, rows = _copied_telemetry(tmp_path, evidence_set)
    anchor = next(
        value for value in rows if value["record_type"] == "simulation_clock_anchor"
    )
    origin = int(anchor["monotonic_origin_ns"])
    for value in rows:
        if (
            value.get("record_type") == "packet"
            and value["icmp"]["identifier"] == 10
        ):
            offset = 25_000_000_000
            if value["icmp"]["type"] == 0:
                offset += 50_000_000
            value["kernel_monotonic_ns"] = origin + offset
    _write_telemetry(telemetry, rows)

    data = _collect(evidence_set, telemetry=telemetry)
    transaction = next(
        value for value in data["transactions"] if value["identifier"] == 10
    )

    assert transaction["formal_boundary_timestamp"] == (
        "2026-01-01T00:00:20+00:00"
    )
    assert transaction["planned_reachability"] == "reachable"
    assert transaction["route_outcome"]["kind"] == "reachable"


def test_zone_geometry_splits_dateline_without_packet_link_semantics(
    evidence_set,
) -> None:
    data = _collect(evidence_set)
    prefixes = data["compiled_evidence"]["realizations"][-1]["zone_prefixes"]

    assert {value["zone_id"] for value in prefixes} == {
        "americas",
        "new-york-metro",
    }
    assert all(value["packet_prefix"].endswith("/32") for value in prefixes)
    encoded = json.dumps(data)
    assert "packet_link_semantics" not in encoded
    assert "longitude_segments" not in encoded
    assert "representative_point" not in encoded


def test_workload_lanes_keep_overlapping_streams_and_qualify_gaps(
    tmp_path: Path,
    evidence_set,
) -> None:
    workload = _workload(
        tmp_path / "traffic-workload.json",
        series=[
            _series("A", scheduled_slots=5),
            _series(
                "B",
                direction="zone-to-ground-station",
                scheduled_slots=10,
            ),
        ],
    )

    data = _collect(evidence_set, workload=workload)

    assert [value["series_id"] for value in data["workload"]["series"]] == [
        "A",
        "B",
    ]
    assert data["workload"]["inventory"] == {
        "series": 2,
        "scheduled_slots": 15,
        "maximum_concurrent_series": 2,
    }
    assert data["workload"]["authority"] == {
        "status": "planned-example-workload",
        "proves_command_execution": False,
        "proves_packet_emission": False,
        "proves_timeout": False,
        "proves_packet_loss": False,
        "packet_observation_authority": "traffic-telemetry.jsonl",
    }


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


def test_identity_contradiction_fails_closed(
    tmp_path: Path,
    evidence_set,
) -> None:
    telemetry, rows = _copied_telemetry(tmp_path, evidence_set)
    pair = _interface_pair(rows)
    pair[0]["node_id"] = pair[1]["node_id"]
    _insert_before_packets(rows, pair)
    _write_telemetry(telemetry, rows)

    with pytest.raises(ValueError, match="node slot|identity|interface"):
        _collect(evidence_set, telemetry=telemetry)


def test_losses_and_incomplete_recorder_are_visible(
    tmp_path: Path,
    evidence_set,
) -> None:
    telemetry, rows = _copied_telemetry(tmp_path, evidence_set)
    loss = next(value for value in rows if value["record_type"] == "telemetry_loss")
    ending = next(value for value in rows if value["record_type"] == "recorder_end")
    loss["lost_events"] = 3
    ending["complete"] = False
    ending["failure"] = {"type": "Interrupted"}
    ending["reported_losses"] = 3
    _write_telemetry(telemetry, rows)

    data = _collect(evidence_set, telemetry=telemetry)

    assert data["overview"]["telemetry_complete"] is False
    assert data["inventory"]["telemetry_lost_events"] == 3
    assert data["telemetry"]["loss_records"][0]["lost_events"] == 3
    assert data["telemetry"]["recorder_end"]["complete"] is False
    assert len(data["transactions"]) == 2


def test_stale_index_is_invalidated_before_projection(
    tmp_path: Path,
    evidence_set,
) -> None:
    output = tmp_path / "traffic"
    output.mkdir()
    index = output / "index.html"
    index.write_text("stale page", encoding="utf-8")
    invalid_compiler = tmp_path / "invalid-compiler.json"
    invalid_compiler.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="Compiler inspection"):
        write_traffic_replay_tool(
            evidence_set["telemetry"],
            evidence_set["frames"],
            evidence_set["planning"],
            invalid_compiler,
            None,
            output,
        )

    assert not index.exists()
    assert evidence_set["telemetry"].exists()


def test_write_produces_dependency_free_file_url_bundle(
    tmp_path: Path,
    evidence_set,
) -> None:
    result = write_traffic_replay_tool(
        evidence_set["telemetry"],
        evidence_set["frames"],
        evidence_set["planning"],
        evidence_set["compiler_json"],
        None,
        tmp_path,
    )
    page = (tmp_path / "index.html").read_text(encoding="utf-8")

    assert result["status"] == "PASS"
    assert set(result["files"]) == {
        "traffic-replay.json",
        "index.html",
        "summary.txt",
        "inventory.json",
        "checksums.sha256",
    }
    for required in (
        "Traffic / Path Replay",
        "Physical evidence + planned intent + runtime telemetry",
        "Traffic transactions against planned Route outcomes",
        "Formal planning and physical evidence coverage",
        "explicit planned reachability",
        'class="scroll"',
    ):
        assert required in page
    for forbidden in (
        "<script src=",
        "fetch(",
        "WebSocket",
        "EventSource",
        "http://",
        "https://",
    ):
        assert forbidden not in page
    assert len(page.encode("utf-8")) < 1_000_000


def test_globe_vertical_rotation_clamps_without_longitude_flip() -> None:
    source = Path(
        __import__("tools.traffic.renderer", fromlist=["__file__"]).__file__
    ).read_text(encoding="utf-8")

    assert ".scroll {{ overflow:auto;max-height:560px; }}" in source
    assert "viewLat=Math.PI-viewLat;viewLon+=Math.PI" not in source
    assert "viewLat=-Math.PI-viewLat;viewLon+=Math.PI" not in source
    assert "Date.now(" not in source
    assert "requestAnimationFrame(" not in source


def test_no_workload_writes_the_same_primary_replay_surface(
    tmp_path: Path,
    evidence_set,
) -> None:
    result = write_traffic_replay_tool(
        evidence_set["telemetry"],
        evidence_set["frames"],
        evidence_set["planning"],
        evidence_set["compiler_json"],
        None,
        tmp_path,
    )
    page = (tmp_path / "index.html").read_text(encoding="utf-8")
    inventory = json.loads((tmp_path / "inventory.json").read_text())

    assert "traffic_workload" not in inventory["inputs"]
    assert "traffic-workload.json" not in result["files"]
    assert "Traffic transactions against planned Route outcomes" in page
    assert "Formal planning and physical evidence coverage" in page
    assert "Probe qualification" in page


def test_tool_owned_cli_writes_replay_without_workload_control_input(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    evidence_set,
) -> None:
    output = tmp_path / "traffic"
    monkeypatch.setattr(
        "sys.argv",
        [
            "tools.traffic",
            "--telemetry",
            str(evidence_set["telemetry"]),
            "--physical-frames",
            str(evidence_set["frames"]),
            "--planning",
            str(evidence_set["planning"]),
            "--bpf-compiler",
            str(evidence_set["compiler_json"]),
            "--output",
            str(output),
        ],
    )

    assert traffic_tool_main() == 0
    inventory = json.loads((output / "inventory.json").read_text())
    assert "traffic_workload" not in inventory["inputs"]
    assert (output / "index.html").is_file()


def test_qualification_inputs_project_plan_execution_and_runner_evidence(
    tmp_path: Path,
    evidence_set,
) -> None:
    probe = TrafficProbePlanBuilder().build(
        evidence_set["plans"], evidence_set["realizations"]
    )
    plan_path = write_traffic_probe_plan(probe, tmp_path / "probe-plan.json")
    execution_path = tmp_path / "probe-execution.jsonl"
    runner_path = tmp_path / "runner-events.jsonl"
    execution = [
        {
            "record_type": "execution_header",
            "artifact_type": "traffic-probe-execution",
            "format_version": 1,
            "plan_revision_id": probe.revision_id,
            "classification_scope": (
                "authoritative-active-published-runtime-only"
            ),
        },
        *(
            {
                "record_type": "episode_not_run",
                "episode_id": episode.episode_id,
                "probe_id": episode.probe_id,
                "reason": "bounded-test-run-ended",
                "plan_revision_id": probe.revision_id,
                "rearmable": True,
            }
            for episode in probe.episodes
        ),
        {
            "record_type": "executor_end",
            "plan_revision_id": probe.revision_id,
            "complete": True,
            "cleanup_complete": True,
            "active_generators": 0,
        },
    ]
    _write_telemetry(execution_path, execution)
    runner = [
        {
            "record_type": "history_header",
            "format_version": 2,
            "owner": "BpfRunner",
            "capture": "bounded-nonblocking",
        },
        *(
            {
                "record_type": "runner_event",
                "event": "activation_end",
                "active_generation": index,
                "generation_publication_occurred": True,
                "preparation": index == 0,
                "active_frontier": timestamp.isoformat(),
                "main_seconds": 0.01,
                "runtime_apply_seconds": 0.008,
                "activation_switch_seconds": 0.001,
                "foundation_validation_succeeded": True,
                "foundation_validation_timing_complete": True,
                "foundation_validation_timing_status": "complete",
                "foundation_validation_seconds": 0.0005,
                "foundation_validation_included_in_runtime_apply": True,
                "generation_switch_start_monotonic_ns": (
                    1_000_000_000_000 + index * 10_000_000_000
                ),
                "generation_switch_end_monotonic_ns": (
                    1_000_010_000_000 + index * 10_000_000_000
                ),
            }
            for index, timestamp in enumerate(probe.nominal_boundaries)
        ),
    ]
    _write_telemetry(runner_path, runner)
    output = tmp_path / "output"

    result = write_traffic_replay_tool(
        evidence_set["telemetry"],
        evidence_set["frames"],
        evidence_set["planning"],
        evidence_set["compiler_json"],
        None,
        output,
        plan_path,
        execution_path,
        runner_path,
    )
    data = json.loads((output / "traffic-replay.json").read_text())
    inventory = json.loads((output / "inventory.json").read_text())

    assert result["status"] == "PASS"
    assert data["qualification"]["summary"]["plan_revision_id"] == (
        probe.revision_id
    )
    assert data["qualification"]["summary"]["global_evidence_complete"] is True
    assert data["qualification"]["runner_observer_effect"]["main_seconds"] == {
        "count": len(probe.nominal_boundaries) - 1,
        "p50": 0.01,
        "p95": 0.01,
        "max": 0.01,
    }
    assert all(
        value["execution_disposition"] == "not-run-rearmable"
        for value in data["qualification"]["episodes"]
    )
    assert set(inventory["inputs"]) >= {
        "traffic_probe_plan",
        "traffic_probe_execution",
        "runner_events",
    }
    page = (output / "index.html").read_text(encoding="utf-8")
    assert "Probe qualification" in page
    assert "Traffic probe-plan construction" in page


def test_zero_and_many_observed_flows_keep_a_fixed_globe_and_scroll_browser(
    tmp_path: Path,
    evidence_set,
) -> None:
    telemetry, rows = _copied_telemetry(tmp_path, evidence_set)
    rows = [value for value in rows if value["record_type"] != "packet"]
    ending = next(value for value in rows if value["record_type"] == "recorder_end")
    ending["packet_records"] = 0
    _write_telemetry(telemetry, rows)
    zero = _collect(evidence_set, telemetry=telemetry)
    zero_page = render_traffic_replay_page(zero)

    assert zero["transactions"] == ()
    assert "No ICMP transactions observed." in zero_page
    assert "max-height:560px" in zero_page

    one = _collect(evidence_set)
    many = deepcopy(one)
    many["transactions"] = tuple(
        {
            **one["transactions"][0],
            "identifier": 1000 + index,
            "sequence": index,
        }
        for index in range(100)
    )
    many_page = render_traffic_replay_page(many)

    assert many_page.count("observed-reachable") >= 100
    assert 'class="scroll"' in many_page
    assert "requestAnimationFrame(" not in many_page


def test_latest_canonical_artifact_is_v3_or_explicitly_stale_before_rerun(
    evidence_set,
) -> None:
    data = _collect(evidence_set)
    planned = data["planned_evidence"]
    transitions = planned["formal_transitions"]

    assert data["format_version"] == 5
    assert len(transitions) == data["inventory"]["formal_planning_boundaries"]
    assert len(transitions) == 4
    assert set(planned) == {
        "plans",
        "formal_transitions",
        "final_state",
        "final_state_fingerprint",
    }
    for value in transitions:
        assert set(value) == {
            "plan_index",
            "plan_id",
            "transition_index",
            "timestamp",
            "transition_fingerprint",
            "state_fingerprint",
            "state_summary",
            "transition",
        }
        assert not {
            "zone_service_assignments",
            "route_outcomes",
            "operational_connections",
        } & set(value)
    assert len(json.dumps(planned).encode("utf-8")) < len(
        gzip.decompress(evidence_set["planning"].read_bytes())
    )


def test_compact_planning_replay_rejects_dropped_transition(
    evidence_set,
) -> None:
    snapshot = deepcopy(_read_observation(evidence_set).snapshot)
    values = list(snapshot["planning"]["formal_transitions"])
    del values[1]
    snapshot["planning"]["formal_transitions"] = tuple(values)

    with pytest.raises(ValueError, match="fingerprint|final|previous"):
        project_traffic_replay(TrafficReplayRead(snapshot))


def test_compact_planning_replay_rejects_mutated_transition_fingerprint(
    evidence_set,
) -> None:
    snapshot = deepcopy(_read_observation(evidence_set).snapshot)
    values = list(snapshot["planning"]["formal_transitions"])
    values[0]["transition_fingerprint"] = "0" * 64
    snapshot["planning"]["formal_transitions"] = tuple(values)

    with pytest.raises(ValueError, match="transition fingerprint"):
        project_traffic_replay(TrafficReplayRead(snapshot))


def test_traffic_renderer_labels_probe_plan_construction_without_simulator_claim() -> None:
    source = Path("tools/traffic/renderer.py").read_text(encoding="utf-8")
    assert "Traffic probe-plan construction" in source
    assert "Simulator Planner duration" not in source



def test_actual_boundary_time_does_not_follow_ideal_clock_when_runner_lags(tmp_path, evidence_set):
    telemetry, rows = _copied_telemetry(tmp_path, evidence_set)
    for row in rows:
        if row.get("record_type") == "boundary_activation" and not row["preparation"]:
            for key in ("main_start_monotonic_ns", "main_end_monotonic_ns", "generation_switch_start_monotonic_ns", "generation_switch_end_monotonic_ns"):
                row[key] += 20_000_000_000
        if row.get("record_type") == "packet" and row["icmp"]["identifier"] == 10:
            row["kernel_monotonic_ns"] += 23_000_000_000
    _write_telemetry(telemetry, rows)
    data = _collect(evidence_set, telemetry=telemetry)
    transaction = next(value for value in data["transactions"] if value["identifier"] == 10)
    assert transaction["request"]["simulation_timestamp"] == "2026-01-01T00:00:25+00:00"
    assert transaction["formal_boundary_timestamp"] == "2026-01-01T00:00:00+00:00"
    assert transaction["request"]["generation"] == 0
    assert transaction["request"]["generation_boundary_timestamp"] == "2026-01-01T00:00:00+00:00"
