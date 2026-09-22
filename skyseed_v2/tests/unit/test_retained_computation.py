"""A retained cursor extends semantics, never replays formal history."""
from datetime import timedelta
from hashlib import sha256

import pytest

from examples.Sky00_tiny_network import tiny_network as tiny
from skyseed_v2.experiment.producer import Producer
from tools.generation.traffic.probe_plan import TrafficProbePlanBuilder
from skyseed_v2.backend.execution_materials.sealing import _RunnerArtifactWriter
from skyseed_v2.simulator.planner.values import plan_transition_fingerprint


def calculate(root, endpoints, *, retained, warm=False):
    simulator = tiny.create_simulator(tiny.build_emulator(), duration=timedelta(seconds=endpoints[0]),
                                     observation_interval=timedelta(seconds=5))
    if warm:
        from dataclasses import replace
        from skyseed_v2.simulator import create_default_simulator
        configuration = tiny.simulation_configuration()
        configuration = replace(configuration,
            inter_satellite_configuration=replace(configuration.inter_satellite_configuration,
                minimum_service_duration=timedelta(seconds=180)),
            ground_station_minimum_service_duration=timedelta(seconds=60))
        simulator.get_planner().shutdown()
        simulator = create_default_simulator(simulator.get_definition(), configuration=configuration)
    frames, transitions, paths, seals, traffic_plans = [], [], [], [], []
    traffic = TrafficProbePlanBuilder(physical_world=simulator.get_physical_world())
    previous = None
    with Producer(simulator.get_timeline(), simulator.get_physical_world(),
            simulator.get_physical_link_model(), simulator.get_planner(), retain=retained,
            evidence_path=root / "history", formal_observation_sink=lambda o: frames.append(o.timestamp)) as producer:
        for index, endpoint in enumerate(endpoints):
            if index:
                producer.extend(tiny.DEFAULT_START_TIME + timedelta(seconds=endpoint))
            writer = _RunnerArtifactWriter(root / f"batch-{index}", continuation=previous, final=not retained)
            while (value := producer.produce_next_realization()) is not None:
                transitions.extend(plan_transition_fingerprint(t) for t in producer.latest_plan.transitions)
                writer.append(value)
                traffic.append(producer.latest_plan, value)
            paths.append(writer.finalize())
            seals.append(sha256(paths[-1].read_bytes()).hexdigest())
            writer.close()
            previous = writer
            traffic_plans.append(traffic.flush())
            if retained:
                assert not producer.snapshot()["planner_finished"]
                assert producer.snapshot()["formal_input_exhausted"]
                producer.flush_evidence()
        snapshot = producer.snapshot()
        if retained:
            producer.finish()
        assert producer.snapshot()["domain_history_sealed"]
    assert [sha256(p.read_bytes()).hexdigest() for p in paths] == seals
    return frames, transitions, snapshot, traffic_plans


def test_staged_extension_matches_finite_calculation_and_seals_are_immutable(tmp_path):
    whole = calculate(tmp_path / "whole", (90,), retained=False)
    staged = calculate(tmp_path / "staged", (30, 60, 90), retained=True)
    assert whole[:2] == staged[:2]
    assert len(set(staged[0])) == len(staged[0]) == 19
    assert staged[0][7] == tiny.DEFAULT_START_TIME + timedelta(seconds=35)
    assert whole[2]["prehistory_frame_count"] == staged[2]["prehistory_frame_count"]
    assert [p.coverage_start for p in staged[3]] == [staged[0][i] for i in (0, 7, 13)]
    assert all(p.episodes for p in staged[3])


def test_exited_or_finite_producer_cannot_resume():
    simulator = tiny.create_simulator(tiny.build_emulator())
    with Producer(simulator.get_timeline(), simulator.get_physical_world(),
            simulator.get_physical_link_model(), simulator.get_planner()) as producer:
        with pytest.raises(RuntimeError, match="original retained"):
            producer.extend(tiny.DEFAULT_END_TIME + timedelta(seconds=30))
    with pytest.raises(RuntimeError, match="original retained"):
        producer.extend(tiny.DEFAULT_END_TIME + timedelta(seconds=30))


def test_policy_lookahead_and_warm_bootstrap_survive_600_to_900_extension(tmp_path, monkeypatch):
    from collections import Counter
    from skyseed_v2.simulator import PhysicalWorld
    whole = calculate(tmp_path / "whole", (900,), retained=False, warm=True)
    observed = []
    original = PhysicalWorld.frame_at
    def frame_at(self, timestamp):
        observed.append(timestamp)
        return original(self, timestamp)
    monkeypatch.setattr(PhysicalWorld, "frame_at", frame_at)
    staged = calculate(tmp_path / "staged", (600, 900), retained=True, warm=True)
    assert staged[:2] == whole[:2]
    assert staged[2]["prehistory_frame_count"] == whole[2]["prehistory_frame_count"] == 1
    assert staged[0][121] == tiny.DEFAULT_START_TIME + timedelta(seconds=605)
    assert max(Counter(observed).values()) == 1
