from __future__ import annotations

from datetime import timedelta
from hashlib import sha256
import gzip
import json

import pytest

from examples.Sky00_tiny_network.tiny_network import build_emulator
from examples.Sky00_tiny_network.tiny_network import DEFAULT_START_TIME, ZONE_LAYOUT_FILE, create_simulator as create_complete_static_simulator
from tests._support import drain_simulator as run_complete_static_chain
from skyseed_v2.configuration.simulator import load_zone_layout_definition
from skyseed_v2.experiment.producer import (
    Producer,
)
from skyseed_v2.simulator import (
    CircularOrbitPositionModel,
    SimulationDefinition,
    SimulationTimeline,
    Simulator,
)
from skyseed_v2.simulator.planner import (
    published_planning_evidence_from_data,
)


def _records(path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as stream:
        lines = tuple(stream)
    return tuple(
        json.loads(value)
        for value in lines
    )


def test_common_producer_persists_detached_formal_history(tmp_path) -> None:
    simulator = create_complete_static_simulator(build_emulator())
    evidence = tmp_path / "evidence"
    plans, realizations, snapshot = run_complete_static_chain(
        simulator,
        evidence_path=evidence,
    )

    assert snapshot["formal_frame_count"] == 4
    assert snapshot["prehistory_frame_count"] == 0
    assert snapshot["physical_link_observation_count"] == 4
    assert snapshot["plan_count"] == len(plans) == len(realizations) == 1
    assert snapshot["transition_count"] == 4
    assert snapshot["pull_mode"] == "compiler"
    assert snapshot["domain_history_sealed"] is True

    physical = _records(evidence / "physical-world/physical-frames.jsonl")
    planning = _records(evidence / "planner/planning.jsonl.gz")
    compiler = _records(evidence / "compiler/compiler.jsonl")
    assert len(physical) == 1 + snapshot["formal_frame_count"]
    assert len(planning) == 1 + len(plans)
    assert len(compiler) == 1 + len(realizations)
    assert [value["timestamp"] for value in physical[1:]] == [
        value.isoformat() for value in simulator.get_timeline().iter_timestamps()
    ]
    for frame in physical[1:]:
        node_keys = [
            (
                value["node"]["asn"],
                value["node"]["entity_kind"],
                value["node"]["entity_id"],
            )
            for value in frame["positions"]
        ]
        assert node_keys == sorted(node_keys)

    decoded = tuple(
        published_planning_evidence_from_data(value["data"])
        for value in planning[1:]
    )
    assert tuple(value.plan.plan_id for value in decoded) == tuple(
        value.plan_id for value in plans
    )
    assert [value["source_plan_id"] for value in compiler[1:]] == [
        value.plan_id for value in plans
    ]
    manifest_path = evidence / "experiment/domain-history-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert snapshot["evidence"]["manifest"] == str(manifest_path.resolve())
    assert manifest["terminal"] is True
    assert manifest["through"] == "compiler"
    assert manifest["physical_world_fingerprint"] == (
        simulator.get_physical_world().semantic_fingerprint()
    )
    assert manifest["counts"] == {
        "expected_prehistory_frames": 0,
        "expected_formal_frames": 4,
        "prehistory_frames": 0,
        "formal_frames": 4,
        "plans": 1,
        "transitions": 4,
        "realizations": 1,
    }
    for stream_name, filename in (
        ("physical_frames", "physical-frames.jsonl"),
        ("planning", "planning.jsonl.gz"),
        ("compiler", "compiler.jsonl"),
    ):
        path = evidence / filename
        entry = manifest["streams"][stream_name]
        assert entry["path"] == filename
        assert entry["bytes"] == path.stat().st_size
        assert entry["sha256"] == sha256(path.read_bytes()).hexdigest()
        assert entry["record_count"] == len(_records(path)) - 1


def test_producer_evidence_root_must_be_new_and_not_a_symlink(tmp_path) -> None:
    simulator = create_complete_static_simulator(build_emulator())
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    planner = simulator.get_planner()
    with pytest.raises(ValueError, match="symlink"):
        Producer(
            simulator.get_timeline(),
            simulator.get_physical_world(),
            simulator.get_physical_link_model(),
            planner,
            evidence_path=link,
        )
    planner.shutdown(wait=True)


def test_producer_close_is_idempotent() -> None:
    simulator = create_complete_static_simulator(build_emulator())
    planner = simulator.get_planner()
    producer = Producer(
        simulator.get_timeline(),
        simulator.get_physical_world(),
        simulator.get_physical_link_model(),
        planner,
    )
    producer.close()
    producer.close()
    with pytest.raises(RuntimeError, match="closed"):
        producer.produce_next_realization()


def test_producer_rejects_planner_from_another_physical_world(tmp_path) -> None:
    first = create_complete_static_simulator(build_emulator())
    changed_zones = tmp_path / "zones.yaml"
    changed_zones.write_text(
        ZONE_LAYOUT_FILE.read_text(encoding="utf-8").replace(
            "priority: 20", "priority: 21"
        ),
        encoding="utf-8",
    )
    second = create_complete_static_simulator(
        build_emulator(), zone_layout_path=changed_zones
    )
    planner = first.get_planner()
    try:
        with pytest.raises(ValueError, match="same PhysicalWorld"):
            Producer(
                second.get_timeline(),
                second.get_physical_world(),
                first.get_physical_link_model(),
                planner,
            )
    finally:
        planner.shutdown(wait=True)


def test_incomplete_close_does_not_seal_domain_history(tmp_path) -> None:
    simulator = create_complete_static_simulator(build_emulator())
    planner = simulator.get_planner()
    evidence = tmp_path / "incomplete"
    producer = Producer(
        simulator.get_timeline(),
        simulator.get_physical_world(),
        simulator.get_physical_link_model(),
        planner,
        evidence_path=evidence,
    )

    producer.close()

    assert not (evidence / "experiment/domain-history-manifest.json").exists()
    assert producer.snapshot()["domain_history_sealed"] is False


@pytest.mark.parametrize(
    ("first_method", "second_method"),
    (
        ("produce_next_plan", "produce_next_realization"),
        ("produce_next_realization", "produce_next_plan"),
    ),
)
def test_common_cursor_prohibits_mixing_pull_modes(
    first_method, second_method
) -> None:
    simulator = create_complete_static_simulator(build_emulator())
    planner = simulator.get_planner()
    producer = Producer(
        simulator.get_timeline(),
        simulator.get_physical_world(),
        simulator.get_physical_link_model(),
        planner,
    )
    try:
        assert getattr(producer, first_method)() is not None
        with pytest.raises(RuntimeError, match="cannot mix"):
            getattr(producer, second_method)()
    finally:
        producer.close()


def test_terminal_domain_history_manifest_is_deterministic(tmp_path) -> None:
    manifests = []
    stream_hashes = []
    for index in range(2):
        simulator = create_complete_static_simulator(build_emulator())
        evidence = tmp_path / f"history-{index}"
        run_complete_static_chain(simulator, evidence_path=evidence)
        manifest_path = evidence / "experiment/domain-history-manifest.json"
        manifests.append(manifest_path.read_bytes())
        stream_hashes.append(
            json.loads(manifest_path.read_text(encoding="utf-8"))["streams"]
        )

    assert manifests[0] == manifests[1]
    assert stream_hashes[0] == stream_hashes[1]


def test_planner_only_cursor_seals_121_transitions_in_four_batches(
    tmp_path,
) -> None:
    timeline = SimulationTimeline(
        DEFAULT_START_TIME,
        DEFAULT_START_TIME + timedelta(seconds=600),
        timedelta(seconds=5),
    )
    simulator = create_complete_static_simulator(build_emulator(),
        duration=timedelta(seconds=600), observation_interval=timedelta(seconds=5))
    planner = simulator.get_planner()
    evidence = tmp_path / "planner-only"
    producer = Producer(
        timeline,
        simulator.get_physical_world(),
        simulator.get_physical_link_model(),
        planner,
        evidence_path=evidence,
    )
    plans = []
    try:
        while True:
            plan = producer.produce_next_plan()
            if plan is None:
                break
            plans.append(plan)
        snapshot = producer.snapshot()
    finally:
        producer.close()

    assert len(plans) == snapshot["plan_count"] == 4
    assert sum(len(plan.transitions) for plan in plans) == 121
    assert snapshot["formal_frame_count"] == 121
    assert snapshot["transition_count"] == 121
    assert snapshot["realization_count"] == 0
    assert snapshot["pull_mode"] == "planner"
    assert snapshot["domain_history_sealed"] is True
    manifest = json.loads(
        (evidence / "experiment/domain-history-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["through"] == "planner"
    assert manifest["counts"]["expected_formal_frames"] == 121
    assert manifest["counts"]["plans"] == 4
    assert manifest["counts"]["transitions"] == 121
    assert manifest["counts"]["realizations"] == 0
    assert len(_records(evidence / "compiler/compiler.jsonl")) == 1


def test_common_producer_observes_truthful_owned_timing_and_accounting(
    tmp_path,
) -> None:
    simulator = create_complete_static_simulator(build_emulator())
    observation_path = tmp_path / "observations.jsonl"
    evidence_path = tmp_path / "evidence"
    planner = simulator.get_planner()
    producer = Producer(
        simulator.get_timeline(),
        simulator.get_physical_world(),
        simulator.get_physical_link_model(),
        planner,
        evidence_path=evidence_path,
    )
    # The small regression scene constructs its producer before enabling optional observation. The
    # producer must therefore discover the recorder dynamically from Planner.
    _bind_observer(simulator, None)
    plans = []
    realizations = []
    try:
        while True:
            realization = producer.produce_next_realization()
            if realization is None:
                break
            plans.append(producer.latest_plan)
            realizations.append(realization)
    finally:
        producer.close()
    simulator.get_planner()._get_execution_observer().write_jsonl(observation_path)

    records = _records(observation_path)[1:]

    def owned(component, stage):
        return tuple(
            value
            for value in records
            if value.get("component") == component
            and value.get("stage") == stage
        )

    physical_links = owned("physical-link", "physical-link-observation")
    validations = owned("planner", "physical-input-validation")
    ground_selections = owned(
        "planner", "ground-station-selection-and-ranking"
    )
    ground_validations = owned("planner", "ground-station-validation")
    ground_evidence = owned(
        "planner", "ground-station-evidence-projection"
    )
    planning_totals = owned("planner", "planning-total")
    creations = owned("planner", "plan-creation")
    publications = owned("planner", "publication-total")
    compiler_totals = owned("compiler", "compile-total")
    serializations = owned("realization-producer", "evidence-serialization")
    producer_totals = owned("realization-producer", "producer-total")

    assert len(physical_links) == len(validations) == len(planning_totals) == 4
    assert (
        len(ground_selections)
        == len(ground_validations)
        == len(ground_evidence)
        == 4
    )
    assert tuple(
        value["work"]["candidate_entries_scanned"]
        for value in ground_validations
    ) == tuple(
        value["work"]["feasible_candidates"]
        for value in ground_selections
    )
    assert all(
        value["work"]["histograms"] == 3 for value in ground_evidence
    )
    assert all(value["duration_seconds"] > 0 for value in physical_links)
    assert all(value["duration_role"] == "rollup" for value in planning_totals)
    assert not owned("planner", "physical-link-observation")
    assert not owned("planner", "publication")

    assert len(publications) == len(plans) == 1
    assert tuple(value["scope"]["plan_id"] for value in publications) == tuple(
        plan.plan_id for plan in plans
    )
    assert tuple(value["work"]["transitions"] for value in publications) == (4,)
    assert all(value["duration_role"] == "rollup" for value in publications)
    assert tuple(value["scope"]["plan_id"] for value in creations) == tuple(
        plan.plan_id for plan in plans
    )

    assert len(compiler_totals) == len(realizations) == 1
    assert all(value["duration_role"] == "rollup" for value in compiler_totals)
    assert tuple(
        value["scope"]["compile_index"] for value in compiler_totals
    ) == (0,)

    assert len(serializations) == 7
    assert tuple(
        value["scope"]["family"] for value in serializations
    ).count("physical-frame") == 4
    assert tuple(
        value["scope"]["family"] for value in serializations
    ).count("planning") == 1
    assert tuple(
        value["scope"]["family"] for value in serializations
    ).count("compiler") == 1
    assert tuple(
        value["scope"]["family"] for value in serializations
    ).count("domain-history-manifest") == 1
    assert all(value["work"].get("serialized_bytes", value["work"].get("output_bytes", 0)) > 0 for value in serializations)

    assert len(producer_totals) == len(realizations) + 1 == 2
    assert all(value["duration_role"] == "rollup" for value in producer_totals)
    assert producer_totals[-1]["phase"] == "idle"
    assert producer_totals[-1]["scope"]["terminal"] is True
    assert sum(value["work"]["formal_frames"] for value in producer_totals) == 4
    assert sum(value["work"]["evidence_records"] for value in producer_totals) == 7


def test_failed_optional_observer_does_not_change_domain_progression(tmp_path) -> None:
    simulator = create_complete_static_simulator(build_emulator())
    observer = _bind_observer(simulator, None)
    observer.record(
        component="",
        stage="invalid",
        phase="steady",
        duration_seconds=0.0,
    )

    plans, realizations, snapshot = run_complete_static_chain(simulator)

    assert len(plans) == len(realizations) == 1
    assert snapshot["formal_frame_count"] == 4
    assert observer._get_recording_failure() is not None


def test_failed_physical_link_inspection_sink_isolated_from_progression(
    tmp_path,
) -> None:
    simulator = create_complete_static_simulator(build_emulator())
    planner = simulator.get_planner()
    observer = _bind_observer(simulator, None)
    attempts = []

    def fail_once(observation):
        attempts.append(observation.timestamp)
        raise OSError("injected inspection sink failure")

    producer = Producer(
        simulator.get_timeline(),
        simulator.get_physical_world(),
        simulator.get_physical_link_model(),
        planner,
        formal_observation_sink=fail_once,
    )
    realizations = []
    try:
        while (value := producer.produce_next_realization()) is not None:
            realizations.append(value)
        snapshot = producer.snapshot()
    finally:
        producer.close()

    assert len(realizations) == 1
    assert snapshot["formal_frame_count"] == 4
    assert snapshot["transition_count"] == 4
    assert snapshot["formal_observation_sink_failure_count"] == 1
    assert snapshot["formal_observation_sink_failure"] == (
        "OSError: injected inspection sink failure"
    )
    assert attempts == [simulator.get_timeline().start_time]
    captures = tuple(
        value
        for value in observer.snapshot()["records"]
        if value["component"] == "inspection-evidence"
        and value["stage"] == "physical-link-candidate-capture"
    )
    assert len(captures) == 1
    assert captures[0]["work"] == {
        "observations_attempted": 1,
        "observations_captured": 0,
        "capture_failures": 1,
    }


def _bind_observer(simulator, metadata=None):
    from skyseed_v2.experiment.output.observation import _ExecutionObserver
    observer = _ExecutionObserver(metadata)
    simulator._set_execution_observer(observer)
    return observer
