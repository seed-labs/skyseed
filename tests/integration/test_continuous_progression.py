from __future__ import annotations

from datetime import timedelta
from hashlib import sha256
import json
from types import SimpleNamespace

import pytest

from examples.Sky00_tiny_network.tiny_network import build_emulator
from examples.Sky00_tiny_network.tiny_network import DEFAULT_START_TIME, ZONE_LAYOUT_FILE, create_simulator as create_complete_static_simulator
from tests._support import drain_simulator as run_complete_static_chain
from skyseed.configuration.simulator import load_zone_layout_definition
from skyseed.experiment.producer import (
    Producer,
)
from skyseed.simulator import (
    CircularOrbitPositionModel,
    SimulationDefinition,
    SimulationTimeline,
    Simulator,
    apply_plan,
)
from tools.observation.bpf_compiler import read_bpf_compiler_history
from tools.observation.planner import collect_planner_data, read_planner_history


def _bounded_complete_simulator(duration: timedelta) -> Simulator:
    return create_complete_static_simulator(build_emulator(), duration=duration)


@pytest.fixture(scope="module")
def bounded_300_chain(tmp_path_factory):
    simulator = _bounded_complete_simulator(timedelta(seconds=300))
    planner = simulator.get_planner()
    history = tmp_path_factory.mktemp("bounded-300") / "results"
    producer = Producer(
        simulator.get_timeline(),
        simulator.get_physical_world(),
        simulator.get_physical_link_model(),
        planner,
        evidence_path=history,
    )
    initial = producer.snapshot()
    plans = []
    realizations = []
    first_pull = None
    peaks = {
        "buffered_plans": 0,
        "unpublished": 0,
        "published_evidence": 0,
    }
    try:
        while True:
            realization = producer.produce_next_realization()
            snapshot = producer.snapshot()
            peaks["buffered_plans"] = max(
                peaks["buffered_plans"],
                snapshot["buffered_plan_count"],
            )
            peaks["unpublished"] = max(
                peaks["unpublished"], len(planner._unpublished)
            )
            peaks["published_evidence"] = max(
                peaks["published_evidence"],
                len(planner._published_evidence),
            )
            if realization is None:
                terminal = snapshot
                break
            plans.append(producer.latest_plan)
            realizations.append(realization)
            if first_pull is None:
                first_pull = snapshot
        manifest = history / "experiment/domain-history-manifest.json"
        sealed_bytes = manifest.read_bytes()
        assert producer.produce_next_realization() is None
        assert producer.produce_next_realization() is None
        repeated_terminal = producer.snapshot()
        assert manifest.read_bytes() == sealed_bytes
        previous_result = producer._previous_result
    finally:
        producer.close()
    return SimpleNamespace(
        simulator=simulator,
        planner=planner,
        producer=producer,
        history=history,
        initial=initial,
        first_pull=first_pull,
        terminal=terminal,
        repeated_terminal=repeated_terminal,
        plans=tuple(plans),
        realizations=tuple(realizations),
        previous_result=previous_result,
        peaks=peaks,
        manifest_sha256=sha256(sealed_bytes).hexdigest(),
    )


def test_one_producer_preserves_every_formal_boundary_and_lineage(
    tmp_path,
) -> None:
    simulator = create_complete_static_simulator(build_emulator())
    timeline = simulator.get_timeline()
    world_fingerprint = simulator.get_physical_world().semantic_fingerprint()
    history = tmp_path / "results"

    plans, realizations, snapshot = run_complete_static_chain(
        simulator,
        evidence_path=history,
    )

    expected_timestamps = tuple(timeline.iter_timestamps())
    transition_timestamps = tuple(
        transition.timestamp
        for plan in plans
        for transition in plan.transitions
    )
    activation_timestamps = tuple(
        timestamp
        for realization in realizations
        for timestamp in realization.activation_timestamps
    )
    assert transition_timestamps == expected_timestamps
    assert activation_timestamps == expected_timestamps
    assert snapshot["formal_frame_count"] == len(expected_timestamps)
    assert snapshot["physical_link_observation_count"] == len(
        expected_timestamps
    )
    assert snapshot["transition_count"] == len(expected_timestamps)
    assert snapshot["plan_count"] == len(plans) == 1
    assert snapshot["realization_count"] == len(realizations) == 1
    assert snapshot["domain_history_sealed"] is True

    state = None
    previous_plan = None
    for plan, realization in zip(plans, realizations, strict=True):
        if previous_plan is not None:
            assert (
                plan.start_timestamp
                == previous_plan.end_timestamp + timeline.observation_interval
            )
        state = apply_plan(
            state,
            plan,
            physical_world_fingerprint=world_fingerprint,
        )
        assert state == realization.output_planned_state
        assert plan.plan_id == realization.source_plan_id
        assert (
            plan.output_state_fingerprint
            == realization.output_state_fingerprint
        )
        previous_plan = plan

    detached = read_planner_history(history / "planner/planning.jsonl.gz")
    compiler = read_bpf_compiler_history(history / "compiler/compiler.jsonl")
    assert tuple(value.plan for value in detached) == plans
    assert len(compiler["realizations"]) == len(realizations)

    manifest = json.loads(
        (history / "experiment/domain-history-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["terminal"] is True
    assert manifest["through"] == "compiler"
    assert manifest["counts"] == {
        "expected_prehistory_frames": 0,
        "expected_formal_frames": 4,
        "prehistory_frames": 0,
        "formal_frames": 4,
        "plans": 1,
        "transitions": 4,
        "realizations": 1,
    }
    assert manifest["timeline"]["observation_interval"] == {
        "days": 0,
        "seconds": int(timedelta(seconds=10).total_seconds()),
        "microseconds": 0,
    }


def test_bounded_300_seconds_preserves_exact_plan_and_compiler_semantics(
    bounded_300_chain,
) -> None:
    chain = bounded_300_chain
    timeline = chain.simulator.get_timeline()
    timestamps = tuple(timeline.iter_timestamps())
    transition_timestamps = tuple(
        transition.timestamp
        for plan in chain.plans
        for transition in plan.transitions
    )
    activation_timestamps = tuple(
        timestamp
        for realization in chain.realizations
        for timestamp in realization.activation_timestamps
    )

    assert len(timestamps) == 31
    assert transition_timestamps == timestamps
    assert activation_timestamps == timestamps
    assert len(chain.plans) == len(chain.realizations) == 1
    assert [len(plan.transitions) for plan in chain.plans] == [31]

    state = None
    previous = None
    world_fingerprint = (
        chain.simulator.get_physical_world().semantic_fingerprint()
    )
    for plan, realization in zip(
        chain.plans, chain.realizations, strict=True
    ):
        if previous is None:
            assert plan.previous_plan_id is None
            assert plan.input_state_fingerprint is None
        else:
            assert plan.previous_plan_id == previous.plan_id
            assert plan.input_state_fingerprint == previous.output_state_fingerprint
            assert (
                plan.start_timestamp
                == previous.end_timestamp + timeline.observation_interval
            )
        state = apply_plan(
            state,
            plan,
            physical_world_fingerprint=world_fingerprint,
        )
        assert realization.source_plan_id == plan.plan_id
        assert realization.output_planned_state == state
        assert (
            realization.output_state_fingerprint
            == plan.output_state_fingerprint
        )
        previous = plan

    detached = read_planner_history(chain.history / "planner/planning.jsonl.gz")
    compiled = read_bpf_compiler_history(chain.history / "compiler/compiler.jsonl")
    assert tuple(value.plan for value in detached) == chain.plans
    assert tuple(
        value["source_plan_id"] for value in compiled["realizations"]
    ) == tuple(plan.plan_id for plan in chain.plans)
    manifest = json.loads(
        (chain.history / "experiment/domain-history-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["terminal"] is True
    assert manifest["through"] == "compiler"
    assert manifest["counts"] == {
        "expected_prehistory_frames": 0,
        "expected_formal_frames": 31,
        "prehistory_frames": 0,
        "formal_frames": 31,
        "plans": 1,
        "transitions": 31,
        "realizations": 1,
    }


def test_common_producer_is_lazy_bounded_and_terminal_is_idempotent(
    bounded_300_chain,
) -> None:
    chain = bounded_300_chain
    assert chain.initial == {
        "initial_end": "2026-01-01T00:05:00+00:00",
        "requested_end": "2026-01-01T00:05:00+00:00",
        "retained": False,
        "physical_prediction": {},
        "evidence_queue_peak": 0,
        "evidence_queue_wait_seconds": 0.0,
        "formal_frame_count": 0,
        "prehistory_frame_count": 0,
        "physical_link_observation_count": 0,
        "total_physical_link_observation_count": 0,
        "plan_count": 0,
        "transition_count": 0,
        "realization_count": 0,
        "buffered_plan_count": 0,
        "pull_mode": None,
        "formal_input_exhausted": False,
        "planner_finished": False,
        "domain_history_sealed": False,
        "closed": False,
        "latest_plan_id": None,
        "computation_range_end": "2026-01-01T00:05:00+00:00",
        "production_state": "NEW", "sealed_material_end": None,
        "evidence_scope": "ordinary",  # Candidate evidence has its own scope.
        "cancellation_requested": False,
        "sealed_batches": 0, "packaging_peak": 0, "idle_seconds": 0.0,
        "formal_observation_sink_failure_count": 0,
        "formal_observation_sink_failure": None,
        "formal_observation_sink_seconds": 0.0,
        "domain_evidence_persistence_seconds": 0.0,
        "frontiers": {
            "physical_observation": None,
            "planning": None,
            "publication": None,
        },
        "evidence": {
            "root": str(chain.history.resolve()),
            "physical_frames": str(
                (chain.history / "physical-world/physical-frames.jsonl").resolve()
            ),
            "planning": str((chain.history / "planner/planning.jsonl.gz").resolve()),
            "compiler": str((chain.history / "compiler/compiler.jsonl").resolve()),
            "manifest": None,
        },
    }
    assert chain.first_pull["evidence_scope"] == chain.terminal["evidence_scope"] == "ordinary"
    assert chain.first_pull["formal_frame_count"] == 31
    assert chain.first_pull["plan_count"] == 1
    assert chain.first_pull["realization_count"] == 1
    assert chain.first_pull["formal_input_exhausted"] is True
    assert chain.first_pull["planner_finished"] is True
    assert chain.first_pull["frontiers"] == {
        "physical_observation": "2026-01-01T00:05:00+00:00",
        "planning": "2026-01-01T00:05:00+00:00",
        "publication": "2026-01-01T00:05:00+00:00",
    }
    assert chain.terminal["formal_frame_count"] == 31
    assert chain.terminal["formal_input_exhausted"] is True
    assert chain.terminal["planner_finished"] is True
    assert chain.terminal["domain_history_sealed"] is True
    assert chain.terminal["buffered_plan_count"] == 0
    assert chain.repeated_terminal == chain.terminal
    assert chain.previous_result is chain.realizations[-1]
    assert chain.peaks == {
        "buffered_plans": 0,
        "unpublished": 0,
        "published_evidence": 0,
    }
    assert chain.manifest_sha256 == sha256(
        (chain.history / "experiment/domain-history-manifest.json").read_bytes()
    ).hexdigest()


def test_released_durable_history_feeds_tools_without_domain_reexecution(
    bounded_300_chain,
    monkeypatch,
) -> None:
    chain = bounded_300_chain

    def _forbidden(*_args, **_kwargs):
        pytest.fail("detached Tool attempted domain reexecution")

    monkeypatch.setattr(
        type(chain.simulator.get_physical_world()),
        "frame_at",
        _forbidden,
    )
    monkeypatch.setattr(
        type(chain.simulator.get_physical_link_model()),
        "compute_observation",
        _forbidden,
    )
    monkeypatch.setattr(type(chain.planner), "observe", _forbidden)
    before = {
        path.name: sha256(path.read_bytes()).hexdigest()
        for path in chain.history.rglob("*") if path.is_file()
    }

    detached = read_planner_history(chain.history / "planner/planning.jsonl.gz")
    compiled = read_bpf_compiler_history(chain.history / "compiler/compiler.jsonl")
    projected = collect_planner_data(detached)

    assert tuple(value.plan for value in detached) == chain.plans
    assert len(compiled["realizations"]) == len(chain.realizations)
    assert projected["inventory"]["plans"] == 1
    assert projected["inventory"]["formal_transitions"] == 31
    assert before == {
        path.name: sha256(path.read_bytes()).hexdigest()
        for path in chain.history.rglob("*") if path.is_file()
    }


def test_1000_step_common_producer_working_set_remains_bounded() -> None:
    simulator = _bounded_complete_simulator(timedelta(seconds=9_990))
    planner = simulator.get_planner()
    producer = Producer(
        simulator.get_timeline(),
        simulator.get_physical_world(),
        simulator.get_physical_link_model(),
        planner,
    )
    realization_count = 0
    transition_count = 0
    peaks = {
        "buffered_plans": 0,
        "unpublished": 0,
        "published_evidence": 0,
        "lifecycle_pairs": 0,
        "parameter_memory": 0,
        "route_demands": 0,
        "operational_connections": 0,
        "route_outcomes": 0,
    }
    try:
        while True:
            realization = producer.produce_next_realization()
            snapshot = producer.snapshot()
            peaks["buffered_plans"] = max(
                peaks["buffered_plans"],
                snapshot["buffered_plan_count"],
            )
            peaks["unpublished"] = max(
                peaks["unpublished"], len(planner._unpublished)
            )
            peaks["published_evidence"] = max(
                peaks["published_evidence"],
                len(planner._published_evidence),
            )
            peaks["lifecycle_pairs"] = max(
                peaks["lifecycle_pairs"], len(planner._lifecycle_by_pair)
            )
            peaks["parameter_memory"] = max(
                peaks["parameter_memory"], len(planner._parameter_memory)
            )
            peaks["route_demands"] = max(
                peaks["route_demands"], len(planner._route_demands or ())
            )
            if snapshot["formal_frame_count"]:
                state = planner.get_state()
                peaks["operational_connections"] = max(
                    peaks["operational_connections"],
                    len(state.operational_connections),
                )
                peaks["route_outcomes"] = max(
                    peaks["route_outcomes"], len(state.route_outcomes)
                )
            if realization is None:
                break
            realization_count += 1
            transition_count += len(producer.latest_plan.transitions)

        assert realization_count == 32
        assert transition_count == 1_000
        assert snapshot["formal_frame_count"] == 1_000
        assert snapshot["plan_count"] == 32
        assert snapshot["realization_count"] == 32
        assert snapshot["formal_input_exhausted"] is True
        assert snapshot["planner_finished"] is True
        assert len(producer._plans) == 0
        assert producer._previous_result is not None
        assert len(planner._unpublished) == 0
        assert len(planner._published_evidence) == 0
        assert peaks == {
            "buffered_plans": 0,
            "unpublished": 0,
            "published_evidence": 0,
            "lifecycle_pairs": 1,
            "parameter_memory": 2,
            "route_demands": 0,
            "operational_connections": 2,
            "route_outcomes": 3,
        }
    finally:
        producer.close()
