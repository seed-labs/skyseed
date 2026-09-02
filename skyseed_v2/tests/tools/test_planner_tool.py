from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
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
from skyseed_v2.shared import NodeId
from skyseed_v2.simulator import (
    InterSatelliteLifecycleEvidence,
    PlanningCandidateSummary,
    PlanTransition,
    PlanningFrameEvidence,
    PlanningFrontiers,
    PlanningInspection,
    PublishedPlanningEvidence,
    create_plan,
    plan_transition_fingerprint,
)
from skyseed_v2.simulator.physical import (
    InterSatelliteLinkCandidate,
    PhysicalLinkWorkCounts,
)
from tools.planner import (
    collect_planner_data,
    read_planner_history,
    write_planner_tool,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TOOL_SOURCES = tuple(sorted((PROJECT_ROOT / "tools/planner").glob("*.py")))
_STREAM_KEY = {
    "physical-frames.jsonl": "physical_frames",
    "planning.jsonl.gz": "planning",
    "compiler.jsonl": "compiler",
}


def _copy_history(source: Path, destination: Path) -> Path:
    return Path(shutil.copytree(source, destination))


def _rewrite_stream(
    history: Path,
    filename: str,
    rows: list[dict[str, object]],
    *,
    update_manifest: bool,
) -> Path:
    path = history / filename
    payload_text = "".join(
            json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
            for value in rows
        )
    if path.suffix == ".gz":
        with gzip.open(path, "wt", encoding="utf-8") as stream:
            stream.write(payload_text)
    else:
        path.write_text(payload_text, encoding="utf-8")
    if update_manifest:
        manifest_path = history / "domain-history-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        entry = manifest["streams"][_STREAM_KEY[filename]]
        payload = path.read_bytes()
        entry.update(
            {
                "sha256": sha256(payload).hexdigest(),
                "bytes": len(payload),
                "record_count": len(rows) - 1,
            }
        )
        manifest_path.write_text(
            json.dumps(
                manifest, sort_keys=True, separators=(",", ":")
            )
            + "\n",
            encoding="utf-8",
        )
    return path


@pytest.fixture(scope="module")
def complete_evidence(tmp_path_factory):
    root = tmp_path_factory.mktemp("planner-tool")
    simulator = create_complete_static_simulator(build_emulator())
    plans, realizations, snapshot = run_complete_static_chain(
        simulator, evidence_path=root / "history"
    )
    evidence = read_planner_history(root / "history/planning.jsonl.gz")
    return root, evidence, plans, realizations, snapshot


def test_projection_replays_complete_plans_and_separates_semantic_layers(
    complete_evidence,
) -> None:
    _, evidence, plans, _, snapshot = complete_evidence
    data = collect_planner_data(evidence)

    assert data["inventory"]["plans"] == len(plans) == 2
    assert data["inventory"]["formal_transitions"] == snapshot[
        "formal_frame_count"
    ] == 4
    assert data["lineage"]["last_plan_id"] == plans[-1].plan_id
    assert data["frames"][-1]["state_fingerprint"] == (
        plans[-1].output_state_fingerprint
    )
    assert [value["label"] for value in data["semantic_layers"]] == [
        "PHYSICAL CANDIDATE",
        "SELECTED / ACQUIRING",
        "OPERATIONAL CONNECTION",
        "ROUTED / UNREACHABLE",
        "COMPILED",
        "REALIZED",
    ]
    first = data["frames"][0]
    assert first["physical_candidates"]["inter_satellite"]
    assert first["selected"]["inter_satellite"]
    assert first["state_summary"]["operational_connection_count"] > 0
    route_changes = first["transition"]["route_outcome_transitions"]
    assert any(
        value["current"]["kind"] == "reachable" for value in route_changes
    )
    assert any(
        value["current"]["kind"] == "unreachable"
        and value["current"]["reason"] == "ZONE_UNSERVED"
        for value in route_changes
    )
    assert first["semantic_event_count"] > 0
    assert [value["stage"] for value in first["stages"]] == [
        "physical input validation",
        "ISL candidate selection",
        "acquisition / lifecycle advancement",
        "GroundStation selection / ranking",
        "GroundStation validation",
        "Zone service planning",
        "parameter planning",
        "Route planning",
        "coordination / semantic precedence",
        "planned-state advancement",
    ]
    assert data["final_state"]["operational_connections"]
    assert data["publications"][0]["semantic_event_precedences"]
    assert tuple(
        index
        for publication in data["publications"]
        for index in publication["frame_indexes"]
    ) == tuple(range(snapshot["formal_frame_count"]))


def test_selected_acquiring_evidence_retains_exact_timer_facts() -> None:
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    first = NodeId(65001, "satellite", "first")
    second = NodeId(65001, "satellite", "second")
    candidate = InterSatelliteLinkCandidate(
        first, second, True, 0, 1_000.0, 4
    )
    transition = PlanTransition(timestamp)
    plan = create_plan(
        None,
        previous_plan_id=None,
        physical_world_fingerprint="a" * 64,
        start_timestamp=timestamp,
        observation_interval=timedelta(seconds=5),
        transitions=(transition,),
    )
    inspection = PlanningInspection(
        timestamp=timestamp,
        feasible_isl_candidates=1,
        selected_isls=1,
        acquiring_isls=1,
        operational_isls=0,
        rejected_isls=0,
        underfilled_isl_slots=0,
        ground_station_accesses=0,
        zones_served=0,
        zones_unserved=0,
        routes_reachable=0,
        routes_unreachable=0,
        route_recomputed=False,
        acquisition_started=1,
        acquisition_completed=0,
        acquisition_reset=0,
        stages=("acquisition/lifecycle-advancement",),
    )
    frame = PlanningFrameEvidence(
        timestamp=timestamp,
        inter_satellite_candidate_summary=PlanningCandidateSummary(1, ()),
        ground_station_access_candidate_summary=PlanningCandidateSummary(0, ()),
        zone_service_candidate_summary=PlanningCandidateSummary(0, ()),
        physical_link_work_counts=PhysicalLinkWorkCounts(
            1, 1, 1, 0, 0, 0, 0, 0, 0
        ),
        selected_inter_satellite_links=(
            InterSatelliteLifecycleEvidence(
                candidate,
                timestamp,
                timedelta(seconds=20),
                timedelta(seconds=20),
                False,
            ),
        ),
        selected_ground_station_accesses=(),
        selected_zone_service_candidates=(),
        inspection=inspection,
    )
    evidence = PublishedPlanningEvidence(
        plan,
        (plan_transition_fingerprint(transition),),
        (frame,),
        PlanningFrontiers(timestamp, timestamp, timestamp),
    )

    selected = collect_planner_data((evidence,))["frames"][0]["selected"]
    assert selected["acquiring_inter_satellite_count"] == 1
    lifecycle = selected["inter_satellite"][0]
    assert lifecycle["selected_since"] == timestamp.isoformat()
    assert lifecycle["acquisition_duration"] == {
        "days": 0,
        "seconds": 20,
        "microseconds": 0,
    }
    assert lifecycle["remaining_acquisition"] == lifecycle[
        "acquisition_duration"
    ]
    assert lifecycle["operational"] is False


def test_projection_serializes_complete_operational_state_only_once(
    complete_evidence,
) -> None:
    _, evidence, _, _, _ = complete_evidence
    data = collect_planner_data(evidence)

    assert data["format_version"] == 3
    for frame in data["frames"]:
        assert "operational" not in frame
        assert "routing" not in frame
        assert set(frame["state_summary"]) == {
            "operational_connection_count",
            "zone_assignment_count",
            "zones_served",
            "zones_unserved",
            "reachable_route_count",
            "unreachable_route_count",
        }
        assert frame["transition"]["schema"] == (
            "skyseed-v2-plan-transition-evidence-v2"
        )
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":"))
    assert encoded.count("skyseed-v2-planned-operational-state-v1") == 1
    assert len(data["_tool_frame_chunks"]) == len(data["frames"])
    assert all("route_outcomes" not in value for value in data["_tool_frame_chunks"])
    final_state_bytes = len(
        json.dumps(data["final_state"], separators=(",", ":"))
    )
    materialized_per_frame_bytes = len(encoded) + final_state_bytes * (
        len(data["frames"]) - 1
    )
    assert len(encoded) < materialized_per_frame_bytes


def test_writer_emits_json_html_inventory_and_deterministic_checksums(
    tmp_path, complete_evidence
) -> None:
    _, evidence, _, _, _ = complete_evidence
    result = write_planner_tool(evidence, tmp_path)

    assert result["status"] == "PASS"
    assert {value.name for value in tmp_path.iterdir()} == {
        "checksums.sha256",
        "index.html",
        "inventory.json",
        "planner-inspection.json",
        "summary.txt",
        "data",
    }
    payload = json.loads((tmp_path / "planner-inspection.json").read_text())
    assert payload["tool"] == "planner-inspection"
    page = (tmp_path / "index.html").read_text()
    assert "PHYSICAL CANDIDATE" in page
    assert "SELECTED / ACQUIRING" in page
    assert "RouteDemand from selected source" in page
    assert 'data-stage="gs-validation"' not in page
    assert 'data-gs-step="allocation"' in page
    assert 'data-gs-step="validation"' in page
    assert "Advanced evidence / 高级证据" in page
    assert "Advanced RouteDemand search" in page
    assert "v.label.startsWith(source+' → ')" in page
    assert 'globe.dataset.selectedRoute' in page
    assert 'map.dataset.selectedRoute' in page
    assert "Map-centered stage and boundary inspection" in page
    assert 'id="planned-globe"' in page
    assert 'id="planned-map"' in page
    assert 'id="planner-time"' in page
    assert len(tuple((tmp_path / "data").glob("frame-*.js"))) == 4
    assert "frames" not in payload
    assert "final_state" not in payload
    for line in (tmp_path / "checksums.sha256").read_text().splitlines():
        digest, relative = line.split("  ", 1)
        assert sha256((tmp_path / relative).read_bytes()).hexdigest() == digest


def test_history_reader_rejects_header_and_lineage_drift(
    tmp_path, complete_evidence
) -> None:
    root, _, _, _, _ = complete_evidence
    history = _copy_history(root / "history", tmp_path / "header")
    rows = [
        json.loads(line)
        for line in gzip.open(
            history / "planning.jsonl.gz", "rt", encoding="utf-8"
        ).read().splitlines()
    ]
    rows[0] = {"record_type": "wrong"}
    path = _rewrite_stream(
        history, "planning.jsonl.gz", rows, update_manifest=True
    )
    with pytest.raises(ValueError, match="header"):
        read_planner_history(path)


@pytest.mark.parametrize(
    ("mutation", "expected"),
    (
        ("drop-final", "Planning count"),
        ("drop-middle", "Planning count"),
        ("duplicate", "Planning count"),
    ),
)
def test_terminal_manifest_rejects_dropped_or_duplicate_planning_rows(
    tmp_path, complete_evidence, mutation, expected
) -> None:
    root, _, _, _, _ = complete_evidence
    history = _copy_history(root / "history", tmp_path / mutation)
    path = history / "planning.jsonl.gz"
    rows = [
        json.loads(line)
        for line in gzip.open(path, "rt", encoding="utf-8")
    ]
    if mutation == "drop-final":
        rows.pop()
    elif mutation == "drop-middle":
        rows.pop(1)
    else:
        rows.insert(2, dict(rows[1]))
    _rewrite_stream(history, path.name, rows, update_manifest=True)

    with pytest.raises(ValueError, match=expected):
        read_planner_history(path)


def test_history_reader_rejects_a_dropped_transition_inside_a_sealed_plan(
    tmp_path, complete_evidence
) -> None:
    root, _, _, _, _ = complete_evidence
    history = _copy_history(root / "history", tmp_path / "dropped-transition")
    path = history / "planning.jsonl.gz"
    rows = [
        json.loads(line)
        for line in gzip.open(path, "rt", encoding="utf-8")
    ]
    evidence = rows[1]["data"]
    evidence["plan"]["transitions"].pop()
    evidence["frames"].pop()
    evidence["transition_fingerprints"].pop()
    _rewrite_stream(history, path.name, rows, update_manifest=True)

    with pytest.raises(ValueError):
        read_planner_history(path)


def test_reader_rejects_cross_stream_compiler_lineage_tamper(
    tmp_path, complete_evidence
) -> None:
    root, _, _, _, _ = complete_evidence
    history = _copy_history(root / "history", tmp_path / "cross-stream")
    path = history / "compiler.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[-1]["source_plan_id"] = "cross-stream-plan"
    _rewrite_stream(history, path.name, rows, update_manifest=True)

    with pytest.raises(ValueError, match="differs from Planner lineage"):
        read_planner_history(history / "planning.jsonl.gz")


def test_reader_rejects_a_stale_manifest_after_byte_tamper(
    tmp_path, complete_evidence
) -> None:
    root, _, _, _, _ = complete_evidence
    history = _copy_history(root / "history", tmp_path / "stale")
    path = history / "planning.jsonl.gz"
    payload = bytearray(path.read_bytes())
    payload[-1] ^= 1
    path.write_bytes(payload)

    with pytest.raises(ValueError, match="sha256 differs"):
        read_planner_history(path)


def test_history_reader_requires_terminal_sibling_manifest(
    tmp_path, complete_evidence
) -> None:
    root, _, _, _, _ = complete_evidence
    standalone = tmp_path / "planning.jsonl.gz"
    shutil.copyfile(root / "history/planning.jsonl.gz", standalone)

    with pytest.raises(ValueError, match="terminal manifest is missing"):
        read_planner_history(standalone)


@pytest.mark.parametrize(
    "invalid_path", ("../planning.jsonl.gz", "/planning.jsonl.gz")
)
def test_manifest_rejects_stream_path_escape(
    tmp_path, complete_evidence, invalid_path
) -> None:
    root, _, _, _, _ = complete_evidence
    history = _copy_history(root / "history", tmp_path / "path-escape")
    manifest_path = history / "domain-history-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["streams"]["planning"]["path"] = invalid_path
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="path is invalid"):
        read_planner_history(history / "planning.jsonl.gz")


def test_history_reader_rejects_a_symlink_stream(
    tmp_path, complete_evidence
) -> None:
    root, _, _, _, _ = complete_evidence
    history = _copy_history(root / "history", tmp_path / "symlink")
    planning = history / "planning.jsonl.gz"
    target = tmp_path / "planning-target.jsonl"
    shutil.copyfile(planning, target)
    planning.unlink()
    planning.symlink_to(target)

    with pytest.raises(ValueError, match="must not be a symlink"):
        read_planner_history(planning)


def test_tool_sources_only_read_public_evidence() -> None:
    combined = "\n".join(path.read_text() for path in TOOL_SOURCES)
    assert "select_inter_satellite_links(" not in combined
    assert "plan_route_outcomes(" not in combined
    assert "compute_observation(" not in combined
    assert "compile_bpf(" not in combined
    reader = (PROJECT_ROOT / "tools/planner/reader.py").read_text()
    assert ".read_text(" not in reader
