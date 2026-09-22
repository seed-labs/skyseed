from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import time

import pytest

from examples.Sky00_tiny_network.tiny_network import build_emulator
from examples.Sky00_tiny_network.tiny_network import create_simulator as create_complete_static_simulator
from tests._support import drain_simulator as run_complete_static_chain
from skyseed_v2.shared import NodeId
from skyseed_v2.backend.bpf import Runner
from skyseed_v2.backend.execution_materials.actions import _ForwardingBinding, _ForwardingKey
from tools.generation.traffic.probe_execution import TrafficProbeExecutor
from skyseed_v2.simulator import (
    PlannerConfiguration,
    GroundStationRouteDemandTarget,
    OperationalConnectionId,
    OperationalConnectionKind,
    ReachableRouteOutcome,
    RouteDemand,
    RouteUnreachableReason,
    UnreachableRouteOutcome,
    ZoneRouteDemandTarget,
)
from tools.generation.traffic.probe_plan import (
    TrafficProbePlanBuilder,
    _BoundaryProjection,
    _DIRECTION_GROUND_TO_ZONE,
    _DIRECTION_ZONE_TO_GROUND,
    _PairSeed,
    _ProbeStateInterval,
    _make_identity,
    _resolve_path,
    read_traffic_probe_plan,
    write_traffic_probe_plan,
)


START = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def complete_chain():
    simulator = create_complete_static_simulator(build_emulator())
    # This focused lineage fixture deliberately crosses a batch boundary.
    planner = simulator.get_planner()
    planner._configuration = PlannerConfiguration(frames_per_batch=3, inter_satellite_acquisition_duration=timedelta(0), ground_station_acquisition_duration=timedelta(0))
    plans, realizations, snapshot = run_complete_static_chain(simulator, planner=planner)
    return simulator, plans, realizations, snapshot


def test_probe_plan_uses_all_formal_activation_boundaries(
    complete_chain,
) -> None:
    _, plans, realizations, _ = complete_chain
    plan = TrafficProbePlanBuilder().build(plans, realizations)
    assert plan.nominal_boundaries == tuple(
        timestamp
        for realization in realizations
        for timestamp in realization.activation_timestamps
    )
    assert plan.coverage_start == realizations[0].start_timestamp
    assert plan.coverage_end == realizations[-1].end_timestamp
    assert plan.probe_identities
    assert plan.episodes


def test_probe_selection_is_deterministic_and_round_trips(
    tmp_path, complete_chain
) -> None:
    _, plans, realizations, _ = complete_chain
    builder = TrafficProbePlanBuilder()
    first = builder.build(plans, realizations)
    second = TrafficProbePlanBuilder().build(plans, realizations)
    assert first.revision_id == second.revision_id
    assert first.probe_identities == second.probe_identities
    assert first.episodes == second.episodes
    path = write_traffic_probe_plan(first, tmp_path / "probe-plan.json")
    loaded = read_traffic_probe_plan(path)
    assert loaded.revision_id == first.revision_id
    assert loaded.probe_identities == first.probe_identities
    assert loaded.episodes == first.episodes


def test_incremental_probe_projection_exactly_matches_batch_builder(
    complete_chain,
) -> None:
    _, plans, realizations, _ = complete_chain
    expected = TrafficProbePlanBuilder().build(plans, realizations).artifact()
    builder = TrafficProbePlanBuilder()
    for plan, realization in zip(plans, realizations, strict=True):
        builder.append(plan, realization)
    actual = builder.finalize().artifact()

    # Wall duration is diagnostic; every selected identity and semantic fact
    # must otherwise be byte-for-byte equivalent.
    expected["planning"]["duration_seconds"] = 0.0
    actual["planning"]["duration_seconds"] = 0.0
    assert actual == expected


def test_incremental_probe_duration_counts_work_not_interleaved_wait(
    complete_chain,
    monkeypatch,
) -> None:
    import time

    _, plans, realizations, _ = complete_chain
    ticks = iter(float(value) for value in range(0, 10_000, 2))
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    builder = TrafficProbePlanBuilder()
    for plan, realization in zip(plans, realizations, strict=True):
        builder.append(plan, realization)

    # Each append and finalize consumes two adjacent synthetic ticks. Time
    # between calls is deliberately absent from the diagnostic duration.
    artifact = builder.finalize().artifact()
    assert artifact["planning"]["duration_seconds"] == 2.0 * (
        len(realizations) + 1
    )


def test_reachable_intervals_carry_explicit_non_looping_paths(
    complete_chain,
) -> None:
    _, plans, realizations, _ = complete_chain
    plan = TrafficProbePlanBuilder().build(plans, realizations)
    reachable = [
        interval
        for episode in plan.episodes
        for interval in episode.state_intervals
        if interval.expected_reachable
    ]
    assert reachable
    assert all(
        interval.path_length_hops == len(interval.path_node_ids) - 1
        for interval in reachable
    )
    assert all(
        len(interval.path_node_ids) == len(set(interval.path_node_ids))
        for interval in reachable
    )


def test_absent_route_outcome_is_not_inferred_unreachable() -> None:
    ground = NodeId(65001, "ground-station", "ground")
    satellite = NodeId(65001, "satellite", "satellite")
    projection = _BoundaryProjection(
        nominal_time=complete_time(),
        route_outcomes={},
        forwarding={},
        zone_edges={("", "zone-a"): ("198.18.0.1/32", satellite)},
        resources=set(),
        route_outcomes_changed=[],
        changed_access_ground_stations=[],
        changed_zones=[],
        prior_zone_edges={},
    )
    seed = _PairSeed(
        _DIRECTION_GROUND_TO_ZONE,
        ground,
        ("", "zone-a"),
        defaultdict(set),
    )
    with pytest.raises(ValueError, match="no explicit planned Route outcome"):
        _resolve_path(seed, projection)


def test_probe_selection_discards_candidate_without_full_route_horizon(
    monkeypatch: pytest.MonkeyPatch,
    complete_chain,
) -> None:
    from tools.generation.traffic import probe_plan as probe_plan_module

    _, plans, realizations, _ = complete_chain
    original_candidates = probe_plan_module._candidate_pairs_from_deltas
    invalid_ground = NodeId(65001, "ground-station", "000-missing-demand")
    invalid_key = (
        _DIRECTION_GROUND_TO_ZONE,
        invalid_ground.value,
        "zone-a",
    )

    def candidates(plan_values, values, nominal_boundaries, **kwargs):
        return (
            _PairSeed(
                _DIRECTION_GROUND_TO_ZONE,
                invalid_ground,
                ("", "zone-a"),
                {"persistent-control-candidate": {nominal_boundaries[0]}},
            ),
            *original_candidates(plan_values, values, nominal_boundaries),
        )

    monkeypatch.setattr(probe_plan_module, "_candidate_pairs_from_deltas", candidates)
    result = TrafficProbePlanBuilder().build(plans, realizations)

    assert invalid_key not in {
        (value.direction, value.ground_station_id, value.zone_id)
        for value in result.probe_identities
    }
    assert result.episodes


def test_explicit_unreachable_reason_is_preserved() -> None:
    ground = NodeId(65001, "ground-station", "ground")
    satellite = NodeId(65001, "satellite", "satellite")
    demand = RouteDemand(ground, ZoneRouteDemandTarget("zone-a"))
    outcome = UnreachableRouteOutcome(
        demand,
        RouteUnreachableReason.NO_ELIGIBLE_PATH,
    )
    projection = _BoundaryProjection(
        nominal_time=complete_time(),
        route_outcomes={demand: outcome},
        forwarding={},
        zone_edges={("", "zone-a"): ("198.18.0.1/32", satellite)},
        resources=set(),
        route_outcomes_changed=[demand],
        changed_access_ground_stations=[],
        changed_zones=[],
        prior_zone_edges={},
    )
    seed = _PairSeed(
        _DIRECTION_GROUND_TO_ZONE,
        ground,
        ("", "zone-a"),
        defaultdict(set),
    )
    reachable, path, service, reason = _resolve_path(seed, projection)
    assert reachable is False
    assert path == ()
    assert service == satellite
    assert reason == RouteUnreachableReason.NO_ELIGIBLE_PATH.value


def test_probe_planning_rejects_broken_realization_lineage(
    complete_chain,
) -> None:
    _, plans, realizations, _ = complete_chain
    with pytest.raises(ValueError, match="bootstrap|lineage"):
        TrafficProbePlanBuilder().build(
            tuple(reversed(plans)), tuple(reversed(realizations))
        )


def complete_time():
    return START


def test_probe_plan_selection_is_deterministic_bounded_and_horizon_scoped(
    tmp_path: Path,
    complete_chain,
) -> None:
    _, plans, realizations, _ = complete_chain
    builder = TrafficProbePlanBuilder()

    first = builder.build(plans, realizations)
    second = TrafficProbePlanBuilder().build(plans, realizations)
    artifact = first.artifact()

    assert first.revision_id == second.revision_id
    assert first.probe_identities == second.probe_identities
    assert first.episodes == second.episodes
    assert len(first.episodes) <= 20
    assert {value.direction for value in first.probe_identities} == {
        _DIRECTION_GROUND_TO_ZONE,
        _DIRECTION_ZONE_TO_GROUND,
    }
    assert {value.mode for value in first.episodes} >= {
        "persistent",
        "activation-window",
        "rotating-background",
    }
    assert artifact["planning_horizon"] == {
        "coverage_start_time": realizations[0].start_timestamp.isoformat(),
        "coverage_end_time": realizations[-1].end_timestamp.isoformat(),
        "supplied_realization_count": len(realizations),
        "terminality": "unknown",
        "revision_semantics": "revisable-lookahead",
        "revision_id": first.revision_id,
        "nominal_boundaries": [
            value.isoformat() for value in first.nominal_boundaries
        ],
    }
    assert artifact["planning"]["maximum_request_rate_per_second"] <= 40
    assert artifact["planning"]["selection_strategy"] == (
        "bounded-stratified-reservoir"
    )
    assert artifact["planning"]["global_flow_ranking"] is False

    path = write_traffic_probe_plan(first, tmp_path / "probe-plan.json")
    assert read_traffic_probe_plan(path).revision_id == first.revision_id
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["runtime_authority"] is False
    assert persisted["classification_scope"] == (
        "authoritative-active-published-runtime-only"
    )


def test_probe_plan_resolves_paths_only_after_bounded_coverage_selection(
    monkeypatch: pytest.MonkeyPatch,
    complete_chain,
) -> None:
    from tools.generation.traffic import probe_plan as probe_plan_module

    _, plans, realizations, _ = complete_chain
    original_candidates = probe_plan_module._candidate_pairs_from_deltas
    original_resolve = probe_plan_module._resolve_path
    builder = TrafficProbePlanBuilder()
    for source_plan, realization in zip(plans, realizations):
        builder.append(source_plan, realization)
    base_candidates = original_candidates(
        tuple(builder._deltas),
        builder._zone_prefixes,
        tuple(
            timestamp
            for realization in realizations
            for timestamp in realization.activation_timestamps
        ),
    )
    template = base_candidates[0]
    resolved: list[tuple[str, str, str]] = []

    def many_candidates(plan_values, values, nominal_boundaries, **kwargs):
        selected = list(
            original_candidates(plan_values, values, nominal_boundaries)
        )
        for index in range(60):
            seed = _PairSeed(
                (
                    _DIRECTION_GROUND_TO_ZONE
                    if index % 2 == 0
                    else _DIRECTION_ZONE_TO_GROUND
                ),
                NodeId(
                    template.ground_station.asn,
                    "ground-station",
                    f"bounded-background-{index:03d}",
                ),
                template.service_key,
                {"persistent-control-candidate": {START}},
            )
            selected.append(seed)
        return tuple(selected)

    def count_resolve(seed, projection):
        resolved.append(seed.key)
        if seed.ground_station.entity_id.startswith("bounded-background-"):
            return False, (), None, RouteUnreachableReason.NO_ELIGIBLE_PATH.value
        return original_resolve(seed, projection)

    monkeypatch.setattr(probe_plan_module, "_candidate_pairs_from_deltas", many_candidates)
    monkeypatch.setattr(probe_plan_module, "_resolve_path", count_resolve)

    plan = TrafficProbePlanBuilder().build(plans, realizations)

    assert plan.candidate_pair_count == len(base_candidates) + 60
    assert len(set(resolved)) <= 20
    assert len(resolved) <= 20 * len(plan.nominal_boundaries)
    assert len(resolved) < plan.candidate_pair_count * len(
        plan.nominal_boundaries
    )


def test_windowed_probe_reconstruction_is_event_local(
    monkeypatch: pytest.MonkeyPatch,
    complete_chain,
) -> None:
    from tools.generation.traffic import probe_plan as probe_plan_module

    _, plans, realizations, _ = complete_chain
    original = probe_plan_module._resolve_path
    resolved: list[tuple[tuple[str, str, str], datetime]] = []

    def count_resolve(seed, projection):
        resolved.append((seed.key, projection.nominal_time))
        return original(seed, projection)

    monkeypatch.setattr(probe_plan_module, "_resolve_path", count_resolve)
    plan = TrafficProbePlanBuilder(
        maximum_persistent_probes=0,
        maximum_activation_window_probes=8,
    ).build(plans, realizations)

    assert plan.episodes
    assert {episode.mode for episode in plan.episodes} == {
        "activation-window"
    }
    assert len(resolved) < (
        len(plan.probe_identities) * len(plan.nominal_boundaries) * 2
    )


def test_adjacent_semantically_equal_probe_states_are_compressed(
    complete_chain,
) -> None:
    _, plans, realizations, _ = complete_chain
    plan = TrafficProbePlanBuilder().build(plans, realizations)
    persistent = [
        episode for episode in plan.episodes if episode.mode == "persistent"
    ]

    assert persistent
    assert any(
        len(episode.state_intervals) < len(plan.nominal_boundaries)
        for episode in persistent
    )
    for episode in plan.episodes:
        for prior, current in zip(
            episode.state_intervals, episode.state_intervals[1:]
        ):
            assert not (
                prior.end_time == current.start_time
                and prior.expected_reachable == current.expected_reachable
                and prior.unreachable_reason == current.unreachable_reason
                and prior.service_satellite_id
                == current.service_satellite_id
                and prior.path_node_ids == current.path_node_ids
                and prior.path_length_hops == current.path_length_hops
            )


def test_probe_plan_classifies_zone_source_migration_and_path_change() -> None:
    ground = NodeId(65001, "ground-station", "ground")
    first_satellite = NodeId(65001, "satellite", "satellite-a")
    second_satellite = NodeId(65001, "satellite", "satellite-b")
    seed = _PairSeed(
        _DIRECTION_ZONE_TO_GROUND,
        ground,
        ("", "zone-a"),
        {"zone-service-handover": {START + timedelta(seconds=10)}},
    )
    intervals = (
        _ProbeStateInterval(
            START,
            START + timedelta(seconds=10),
            True,
            None,
            first_satellite.value,
            (first_satellite.value, ground.value),
            1,
            ("zone-source-migration",),
        ),
        _ProbeStateInterval(
            START + timedelta(seconds=10),
            START + timedelta(seconds=20),
            True,
            None,
            second_satellite.value,
            (second_satellite.value, ground.value),
            1,
            ("zone-source-migration",),
        ),
    )

    identity = _make_identity(seed, intervals, zone_ipv4="198.18.0.1")

    assert identity.path_stability == "changing"
    assert "zone-source-migration" in identity.selection_reasons
    assert "persistent-changing-path" in identity.selection_reasons


def test_probe_reachability_requires_request_and_echo_return_paths() -> None:
    ground = NodeId(65001, "ground-station", "ground")
    satellite = NodeId(65001, "satellite", "satellite")
    zone_target = ZoneRouteDemandTarget("zone-a")
    ground_target = GroundStationRouteDemandTarget(ground)
    access = OperationalConnectionId(
        OperationalConnectionKind.GROUND_STATION_ACCESS,
        ground,
        satellite,
    )
    outward_demand = RouteDemand(ground, zone_target)
    outward = ReachableRouteOutcome(
        outward_demand,
        satellite,
        (ground, satellite),
    )
    projection = _BoundaryProjection(
        nominal_time=START,
        route_outcomes={outward_demand: outward},
        forwarding={
            _ForwardingKey(ground, zone_target): _ForwardingBinding(
                _ForwardingKey(ground, zone_target),
                satellite,
                access,
                "198.18.0.1/32",
            )
        },
        zone_edges={("", "zone-a"): ("198.18.0.1/32", satellite)},
        resources={access},
        route_outcomes_changed=[outward_demand],
        changed_access_ground_stations=[],
        changed_zones=[],
        prior_zone_edges={},
    )

    reachable, path, _, reason = _resolve_path(
        _PairSeed(
            _DIRECTION_GROUND_TO_ZONE,
            ground,
            ("", "zone-a"),
            defaultdict(set),
        ),
        projection,
    )
    assert reachable is True
    assert path == (ground, satellite)
    assert reason is None
    with pytest.raises(ValueError, match="no explicit planned Route outcome"):
        _resolve_path(
            _PairSeed(
                _DIRECTION_ZONE_TO_GROUND,
                ground,
                ("", "zone-a"),
                defaultdict(set),
            ),
            projection,
        )
    assert RouteDemand(satellite, ground_target) not in projection.route_outcomes


def test_probe_plan_fails_closed_on_forwarding_loop() -> None:
    ground = NodeId(65001, "ground-station", "ground")
    satellite = NodeId(65001, "satellite", "satellite")
    demand = RouteDemand(ground, ZoneRouteDemandTarget("zone-a"))

    with pytest.raises(ValueError, match="loop-free"):
        ReachableRouteOutcome(
            demand,
            satellite,
            (ground, satellite, ground),
        )


def test_probe_plan_does_not_claim_complete_experiment(
    complete_chain,
) -> None:
    from tools.generation.traffic.probe_plan import TrafficProbePlan

    _, plans, realizations, _ = complete_chain
    artifact = TrafficProbePlanBuilder().build(plans, realizations).artifact()
    artifact["planning_horizon"]["terminality"] = "terminal"

    with pytest.raises(ValueError, match="claims terminality"):
        TrafficProbePlan.from_artifact(artifact)


def test_probe_identity_and_episode_identity_survive_horizon_extension(
    complete_chain,
) -> None:
    _, plans, realizations, _ = complete_chain
    builder = TrafficProbePlanBuilder()
    prefix = builder.build(plans[:1], realizations[:1])
    extended = TrafficProbePlanBuilder().build(plans, realizations)

    assert prefix.revision_id != extended.revision_id
    assert {value.probe_id for value in prefix.probe_identities} <= {
        value.probe_id for value in extended.probe_identities
    }
    prefix_persistent = {
        value.probe_id: value.episode_id
        for value in prefix.episodes
        if value.mode == "persistent"
    }
    extended_persistent = {
        value.probe_id: value.episode_id
        for value in extended.episodes
        if value.mode == "persistent"
    }
    assert prefix_persistent.keys() <= extended_persistent.keys()
    assert all(
        extended_persistent[probe_id] == episode_id
        for probe_id, episode_id in prefix_persistent.items()
    )


def test_unserved_zone_preserves_explicit_route_outcome_reason() -> None:
    ground = NodeId(65001, "ground-station", "ground")
    demand = RouteDemand(ground, ZoneRouteDemandTarget("zone-a"))
    outcome = UnreachableRouteOutcome(
        demand,
        RouteUnreachableReason.NO_ELIGIBLE_PATH,
    )
    projection = _BoundaryProjection(
        nominal_time=START,
        route_outcomes={demand: outcome},
        forwarding={},
        zone_edges={},
        resources=set(),
        route_outcomes_changed=[demand],
        changed_access_ground_stations=[],
        changed_zones=["zone-a"],
        prior_zone_edges={},
    )

    reachable, path, service, reason = _resolve_path(
        _PairSeed(
            _DIRECTION_GROUND_TO_ZONE,
            ground,
            ("", "zone-a"),
            defaultdict(set),
        ),
        projection,
    )

    assert reachable is False
    assert path == ()
    assert service is None
    assert reason == RouteUnreachableReason.NO_ELIGIBLE_PATH.value


def _runner_snapshot(
    plan,
    *,
    omit: str | None = None,
    extra_nodes: tuple[str, ...] = (),
) -> dict[str, object]:
    node_ids = {
        identity.ground_station_id for identity in plan.probe_identities
    }
    node_ids.update(
        interval.service_satellite_id
        for episode in plan.episodes
        for interval in episode.state_intervals
        if interval.service_satellite_id is not None
    )
    node_ids.update(extra_nodes)
    bindings = []
    for index, node_id in enumerate(sorted(node_ids), start=1):
        if node_id == omit:
            continue
        bindings.append(
            {
                "node_id": node_id,
                "container_id": f"container-{index}",
                "container_name": f"node-{index}",
                "pid": 1000 + index,
                "netns_inode": 2000 + index,
                "started_at": "2026-01-01T00:00:00Z",
                "runtime_ipv4": f"100.64.0.{index}",
            }
        )
    return {
        "runner": {
            "runtime_foundation": {
                "foundation_token": "foundation-test",
                "bound_node_count": len(bindings),
                "bindings": bindings,
            }
        }
    }


class _OriginRunner(Runner):
    def __init__(self, plan) -> None:
        self._test_plan = plan
        self._test_origin = time.monotonic()

    def inspection_snapshot(self) -> dict[str, object]:
        return {
            "runner": {
                "frontiers": {
                    "last_active": self._test_plan.coverage_start.isoformat(),
                }
            },
            "events": (
                {
                    "event": "automatic_run_start",
                    "nominal_origin": self._test_plan.coverage_start.isoformat(),
                    "wall_origin": self._test_origin,
                },
            ),
        }

    def status(self):
        snapshot = self.inspection_snapshot()
        return {"active_frontier": snapshot["runner"]["frontiers"]["last_active"],
                "paused": False, "wait_reasons": [],
                "clock": {"nominal_origin": self._test_plan.coverage_start.isoformat(),
                          "wall_origin": self._test_origin, "held_since": None, "revision": 0}}


class _JumpedOriginRunner(_OriginRunner):
    def __init__(self, plan) -> None:
        super().__init__(plan)
        self._test_origin -= (
            plan.coverage_end - plan.coverage_start
        ).total_seconds()

    def inspection_snapshot(self) -> dict[str, object]:
        value = super().inspection_snapshot()
        value["runner"]["frontiers"]["last_active"] = (
            self._test_plan.nominal_boundaries[-1].isoformat()
        )
        return value


def _install_fake_docker_processes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    ignore_term: bool = False,
):
    from tools.generation.traffic import probe_execution as execution_module

    real_popen = subprocess.Popen
    processes = {}

    def popen(command, **_kwargs):
        runtime_identity = next(
            value.removeprefix("SKYSEED_TRAFFIC_GENERATOR_ID=")
            for value in command
            if isinstance(value, str)
            and value.startswith("SKYSEED_TRAFFIC_GENERATOR_ID=")
        )
        term_handler = (
            "trap '' TERM; "
            if ignore_term
            else (
                "trap 'printf \"2 packets transmitted, 1 received, "
                "50% packet loss\\n\"; exit 0' TERM; "
            )
        )
        process = real_popen(
            (
                "sh",
                "-c",
                "printf '[1.0] 64 bytes from test: icmp_seq=1 ttl=64 "
                "time=1.25 ms\\n'; "
                "printf '[2.0] no answer yet for icmp_seq=2\\n'; "
                + term_handler
                + "while :; do sleep 0.05; done",
            ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        processes[runtime_identity] = process
        return process

    def run(command, **_kwargs):
        action = command[-3]
        runtime_identity = command[-1]
        process = processes.get(runtime_identity)
        state = (
            "missing"
            if process is None
            else "active" if process.poll() is None else "stale"
        )
        if action == "term" and state == "active":
            if not ignore_term:
                process.terminate()
            value = "signalled"
        elif action == "kill" and state == "active":
            process.kill()
            value = "signalled"
        elif action == "cleanup":
            value = "cleaned"
        elif action == "discard":
            value = "discarded"
        else:
            value = state
        return SimpleNamespace(returncode=0, stdout=value + "\n", stderr="")

    monkeypatch.setattr(execution_module.subprocess, "Popen", popen)
    monkeypatch.setattr(execution_module.subprocess, "run", run)
    return processes


@pytest.mark.parametrize('state', ['active', 'identity-mismatch'])
def test_generator_control_uses_one_owned_operation_without_elapsed_deadline(monkeypatch, state):
    from tools.generation.traffic import probe_execution as module
    executor = TrafficProbeExecutor.__new__(TrafficProbeExecutor)
    executor._docker = 'docker'
    generator = SimpleNamespace(episode=SimpleNamespace(episode_id='episode'),
        binding=SimpleNamespace(container_id='immutable-container-id'),
        pid_file='/tmp/owned-unique-generator.pid', runtime_identity='owned-unique-generator')
    calls = []
    def invoke(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=state, stderr='')
    monkeypatch.setattr(module.subprocess, 'run', invoke)
    if state == 'active':
        assert executor._generator_control(generator, 'state', expected='active') == 'active'
    else:
        with pytest.raises(RuntimeError, match='identity-mismatch'):
            executor._generator_control(generator, 'state', expected='active')
    assert len(calls) == 1
    assert 'timeout' not in calls[0][1]
    assert calls[0][0][-1] == 'owned-unique-generator'


def test_probe_executor_resolves_bindings_parses_ping_and_cleans_every_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    complete_chain,
) -> None:
    _, plans, realizations, _ = complete_chain
    plan = TrafficProbePlanBuilder().build(plans, realizations)
    processes = _install_fake_docker_processes(monkeypatch)
    evidence = tmp_path / "probe-execution.jsonl"
    executor = TrafficProbeExecutor(
        docker_executable="docker",
        plan=plan,
        runner_snapshot=_runner_snapshot(plan),
        evidence_path=evidence,
    )

    executor.start(_OriginRunner(plan))
    time.sleep(0.2)
    snapshot = executor.stop()
    records = [
        json.loads(line) for line in evidence.read_text().splitlines()
    ]

    assert snapshot["complete"] is True
    assert snapshot["cleanup_complete"] is True
    assert snapshot["active_generators"] == 0
    assert snapshot["scheduling_actions"] == (
        len(plan.nominal_boundaries) + 2 * len(plan.episodes)
    )
    assert snapshot["reconciliations"] == snapshot["episodes_armed"]
    assert snapshot["duplicate_same_boundary_reconciliations"] == 0
    assert all(process.poll() is not None for process in processes.values())
    observations = [
        value for value in records if value["record_type"] == "ping_observation"
    ]
    assert any(
        value["observation"] == "reply"
        and value["icmp_sequence"] == 1
        and value["rtt_ms"] == 1.25
        for value in observations
    )
    assert any(
        value["observation"] == "timeout"
        and value["icmp_sequence"] == 2
        for value in observations
    )
    assert all(
        value["cleanup_result"] == "cleaned"
        for value in records
        if value["record_type"] == "generator_stopped"
    )
    assert any(value["record_type"] == "episode_cancelled" for value in records)
    assert records[-1]["record_type"] == "executor_end"


def test_slow_runner_frontier_remains_authority_for_due_traffic_actions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    complete_chain,
) -> None:
    """Expired ideal deadlines cannot run Traffic against future live state."""

    _, plans, realizations, _ = complete_chain
    plan = TrafficProbePlanBuilder().build(plans, realizations)
    executor = TrafficProbeExecutor(
        docker_executable="docker",
        plan=plan,
        runner_snapshot=_runner_snapshot(plan),
        evidence_path=tmp_path / "slowed-runner.jsonl",
    )
    from tools.generation.traffic import probe_execution as execution_module

    monkeypatch.setattr(execution_module, "monotonic", lambda: 10_000.0)
    executor._await_runner_origin = lambda: 100.0
    executor._runner = _OriginRunner(plan)
    executor._runner._test_origin = 100.0
    runner_status = executor._runner.status()
    monkeypatch.setattr(executor._runner, "status", lambda: runner_status)
    observed_actions = []
    frontier_waits = []

    def await_frontier(required):
        frontier_waits.append(required)
        executor._published_frontier = required
        runner_status["active_frontier"] = required.isoformat()
        return True

    def start_episode(episode):
        observed_actions.append(("start", episode, executor._published_frontier))
        executor._armed_episodes.add(episode.episode_id)

    def complete_episode(episode):
        observed_actions.append(("complete", episode, executor._published_frontier))
        executor._completed_episodes.add(episode.episode_id)
        if len(executor._completed_episodes) == len(plan.episodes):
            executor._stop.set()

    executor._await_published_frontier = await_frontier
    executor._start_episode = start_episode
    executor._complete_episode = complete_episode
    executor._reconcile_episode = lambda episode: None
    executor._drain_io = lambda timeout: None
    executor._reap_unexpected = lambda: None
    executor._cancel_remaining_episodes = lambda: None
    executor._terminate_all = lambda reason: None

    executor._run()

    starts = [value for value in observed_actions if value[0] == "start"]
    completions = [value for value in observed_actions if value[0] == "complete"]
    assert len(starts) == len(plan.episodes)
    assert len(completions) == len(plan.episodes)
    assert all(frontier >= episode.start_time for _, episode, frontier in starts)
    assert all(frontier >= episode.end_time for _, episode, frontier in completions)
    positions = {
        (action, episode.episode_id): index
        for index, (action, episode, _) in enumerate(observed_actions)
    }
    assert all(
        positions[("start", episode.episode_id)]
        < positions[("complete", episode.episode_id)]
        for episode in plan.episodes
    )
    assert plan.coverage_start in frontier_waits
    assert plan.coverage_end in frontier_waits


def test_traffic_never_observes_failed_target_as_formal_active(
    tmp_path: Path,
    complete_chain,
) -> None:
    _, plans, realizations, _ = complete_chain
    plan = TrafficProbePlanBuilder().build(plans, realizations)
    prior = plan.nominal_boundaries[0]
    target = plan.nominal_boundaries[1]

    class FailedTargetRunner:
        def status(self):
            return {"active_frontier": prior.isoformat()}
        def inspection_snapshot(self):
            return {
                "runner": {"frontiers": {"last_active": prior.isoformat()}},
                "events": ({
                    "event": "failure",
                    "phase": "activation",
                    "target_boundary": target.isoformat(),
                    "last_completed_active_frontier": prior.isoformat(),
                    "target_transition_complete": False,
                    "generation_publication_occurred": True,
                },),
            }

    executor = TrafficProbeExecutor(
        docker_executable="docker",
        plan=plan,
        runner_snapshot=_runner_snapshot(plan),
        evidence_path=tmp_path / "failed-target.jsonl",
    )
    executor._runner = FailedTargetRunner()
    observed = executor._observe_published_frontier()
    assert observed == prior
    assert observed != target


def test_cancelled_episode_can_rearm_under_a_revised_lookahead(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    complete_chain,
) -> None:
    _, plans, realizations, _ = complete_chain
    builder = TrafficProbePlanBuilder()
    first = builder.build(plans[:1], realizations[:1])
    revised = TrafficProbePlanBuilder().build(plans, realizations)
    processes = _install_fake_docker_processes(monkeypatch)
    evidence_sets = []

    for index, plan in enumerate((first, revised), start=1):
        evidence = tmp_path / f"probe-execution-{index}.jsonl"
        executor = TrafficProbeExecutor(
            docker_executable="docker",
            plan=plan,
            runner_snapshot=_runner_snapshot(plan),
            evidence_path=evidence,
        )
        executor.start(_OriginRunner(plan))
        time.sleep(0.15)
        executor.stop()
        evidence_sets.append(
            [json.loads(line) for line in evidence.read_text().splitlines()]
        )

    common_persistent = {
        value.episode_id for value in first.episodes if value.mode == "persistent"
    } & {
        value.episode_id
        for value in revised.episodes
        if value.mode == "persistent"
    }
    assert common_persistent
    for records, plan in zip(evidence_sets, (first, revised), strict=True):
        assert records[0]["plan_revision_id"] == plan.revision_id
        assert common_persistent <= {
            value["episode_id"]
            for value in records
            if value["record_type"] == "episode_armed"
        }
        assert common_persistent <= {
            value["episode_id"]
            for value in records
            if value["record_type"] == "episode_cancelled"
            and value["rearmable"] is True
            and value["plan_revision_id"] == plan.revision_id
        }
    assert all(process.poll() is not None for process in processes.values())


def test_frontier_jump_does_not_reconcile_one_episode_twice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    complete_chain,
) -> None:
    _, plans, realizations, _ = complete_chain
    plan = TrafficProbePlanBuilder().build(plans, realizations)
    processes = _install_fake_docker_processes(monkeypatch)
    executor = TrafficProbeExecutor(
        docker_executable="docker",
        plan=plan,
        runner_snapshot=_runner_snapshot(plan),
        evidence_path=tmp_path / "frontier-jump-execution.jsonl",
    )

    executor.start(_JumpedOriginRunner(plan))
    deadline = time.monotonic() + 5
    while len(executor._completed_episodes) < len(plan.episodes):
        if time.monotonic() >= deadline:
            pytest.fail("frontier-jump executor did not finish")
        time.sleep(0.01)
    snapshot = executor.stop()

    assert snapshot["complete"] is True
    assert snapshot["duplicate_same_boundary_reconciliations"] == 0
    assert all(process.poll() is not None for process in processes.values())


@pytest.mark.parametrize("resuming", [False, True])
def test_slow_launch_refreshes_frontier_before_the_next_episode(
    tmp_path, monkeypatch, complete_chain, resuming,
):
    _, plans, realizations, _ = complete_chain
    original = TrafficProbePlanBuilder().build(plans, realizations)
    identities = {value.probe_id: value for value in original.probe_identities}
    ground, zone = (
        next(episode for episode in original.episodes
             if episode.mode == "persistent"
             and identities[episode.probe_id].direction == direction)
        for direction in (_DIRECTION_GROUND_TO_ZONE, _DIRECTION_ZONE_TO_GROUND)
    )
    migration_time = original.nominal_boundaries[1]
    new_source = NodeId(65001, "satellite", "slow-launch-target").value
    first = zone.state_intervals[0]
    ground = replace(ground, episode_id="traffic-episode-00-ground")
    zone = replace(zone, episode_id="traffic-episode-01-zone", state_intervals=(
        replace(first, end_time=migration_time),
        replace(first, start_time=migration_time, end_time=original.coverage_end,
                service_satellite_id=new_source,
                path_node_ids=(new_source, identities[zone.probe_id].ground_station_id)),
    ))
    plan = replace(original, episodes=(ground, zone),
                   probe_identities=tuple(identities[e.probe_id] for e in (ground, zone)))
    processes = _install_fake_docker_processes(monkeypatch)
    evidence = tmp_path / "slow-launch.jsonl"
    executor = TrafficProbeExecutor(docker_executable="docker", plan=plan,
        runner_snapshot=_runner_snapshot(plan), evidence_path=evidence)
    runner = _OriginRunner(plan)
    status = runner.status()
    monkeypatch.setattr(runner, "status", lambda: status)
    executor._runner = runner
    launch = executor._launch_generator
    sources = []

    def launch_across_boundary(episode, identity, desired_node):
        sources.append(desired_node)
        generator = launch(episode, identity, desired_node)
        if len(sources) == 1:
            # Docker registration returns after Runner has advanced independently.
            status["active_frontier"] = migration_time.isoformat()
        else:
            executor._stop.set()
        return generator

    monkeypatch.setattr(executor, "_launch_generator", launch_across_boundary)
    if resuming:
        executor._logical_hold = True
        executor._armed_episodes.update((ground.episode_id, zone.episode_id))
        executor._published_frontier = plan.coverage_start
        try:
            executor._handle_logical_hold(status)
        finally:
            executor._terminate_all("test-finalization")
            executor._selector.close()
    else:
        executor._run()
        assert executor.snapshot()["complete"] is True
    assert sources == [identities[ground.probe_id].ground_station_id, new_source]
    assert executor.snapshot()["failure"] is None
    assert executor.snapshot()["duplicate_same_boundary_reconciliations"] == 0
    assert all(process.poll() is not None for process in processes.values())


@pytest.mark.parametrize("after_stop", ["superseded", "returned", "unserved", "completed", "held", "cancelled"])
def test_slow_migration_stop_reconsiders_current_active_state(
    tmp_path, monkeypatch, complete_chain, after_stop,
):
    _, plans, realizations, _ = complete_chain
    original = TrafficProbePlanBuilder().build(plans, realizations)
    identities = {value.probe_id: value for value in original.probe_identities}
    episode = next(value for value in original.episodes
                   if value.mode == "persistent"
                   and identities[value.probe_id].direction == _DIRECTION_ZONE_TO_GROUND)
    identity = identities[episode.probe_id]
    first = episode.state_intervals[0]
    at_ten = original.coverage_start + timedelta(seconds=10)
    at_twenty = original.coverage_start + timedelta(seconds=20)
    source_b = NodeId(65001, "satellite", "superseded-b").value
    source_c = (first.service_satellite_id if after_stop == "returned"
                else NodeId(65001, "satellite", "current-c").value)
    episode = replace(episode, state_intervals=(
        replace(first, end_time=at_ten),
        replace(first, start_time=at_ten, end_time=at_twenty,
                service_satellite_id=source_b,
                path_node_ids=(source_b, identity.ground_station_id)),
        replace(first, start_time=at_twenty, end_time=episode.end_time,
                service_satellite_id=None if after_stop == "unserved" else source_c,
                expected_reachable=after_stop != "unserved",
                unreachable_reason="ZONE_UNSERVED" if after_stop == "unserved" else None,
                path_length_hops=None if after_stop == "unserved" else 1,
                path_node_ids=() if after_stop == "unserved" else (source_c, identity.ground_station_id)),
    ))
    plan = replace(original, episodes=(episode,), probe_identities=(identity,))
    processes = _install_fake_docker_processes(monkeypatch)
    evidence = tmp_path / "slow-stop.jsonl"
    executor = TrafficProbeExecutor(docker_executable="docker", plan=plan,
        runner_snapshot=_runner_snapshot(plan), evidence_path=evidence)
    runner = _OriginRunner(plan)
    status = runner.status()
    monkeypatch.setattr(runner, "status", lambda: status)
    executor._runner = runner
    stop = executor._stop_generator

    def stop_across_boundary(generator, reason):
        if reason == "zone-source-migration":
            # Runner advances while the old source's process is still draining.
            status["active_frontier"] = (
                episode.end_time if after_stop == "completed" else at_twenty
            ).isoformat()
            if after_stop == "held":
                status["paused"] = True
                status["wait_reasons"] = ["user"]
            elif after_stop == "cancelled":
                executor._stop.set()
        stop(generator, reason)

    monkeypatch.setattr(executor, "_stop_generator", stop_across_boundary)
    try:
        executor._start_episode(episode)
        executor._observe_published_frontier()
        executor._reconcile_episode(episode)
        status["active_frontier"] = at_ten.isoformat()
        executor._observe_published_frontier()
        executor._reconcile_episode(episode)
        assert executor._published_frontier == datetime.fromisoformat(status["active_frontier"])
        if after_stop in {"superseded", "returned"}:
            assert executor._active[episode.episode_id].binding.node_id == source_c
            assert (episode.episode_id, at_twenty) in executor._reconciliation_keys
        else:
            assert not executor._active
        if after_stop == "completed":
            # The queued scheduler completion must remain idempotent.
            executor._complete_episode(episode)
            assert episode.episode_id in executor._completed_episodes
        assert executor.snapshot()["duplicate_same_boundary_reconciliations"] == 0
        assert executor.snapshot()["reconciliations"] == (
            2 if after_stop in {"held", "cancelled"} else 3)
    finally:
        executor._terminate_all("test-finalization")
        executor._selector.close()
    records = [json.loads(line) for line in evidence.read_text().splitlines()]
    launches = [r for r in records if r["record_type"] == "generator_launched"]
    assert [r["physical_source_node_id"] for r in launches] == (
        [first.service_satellite_id, source_c] if after_stop in {"superseded", "returned"} else [first.service_satellite_id])
    migrations = [r for r in records if r["record_type"] == "zone_source_migration"]
    assert len(migrations) == executor.snapshot()["zone_source_migrations"] == (after_stop == "superseded")
    if migrations:
        assert migrations[0]["previous_source_node_id"] == first.service_satellite_id
        assert migrations[0]["current_source_node_id"] == source_c
        assert launches[-1]["published_active_frontier"] == at_twenty.isoformat()
    if after_stop == "unserved":
        assert any(r["record_type"] == "episode_paused" and r["reason"] == "expected-zone-unserved" for r in records)
    if after_stop == "completed":
        assert sum(r["record_type"] == "episode_completed" for r in records) == 1
    assert executor.snapshot()["generators_launched"] == executor.snapshot()["generators_stopped"] == len(launches)
    assert all(process.poll() is not None for process in processes.values())


def test_zone_to_ground_probe_migrates_its_physical_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    complete_chain,
) -> None:
    _, plans, realizations, _ = complete_chain
    original = TrafficProbePlanBuilder().build(plans, realizations)
    episode = next(
        value
        for value in original.episodes
        if value.mode == "persistent"
        and next(
            identity
            for identity in original.probe_identities
            if identity.probe_id == value.probe_id
        ).direction
        == _DIRECTION_ZONE_TO_GROUND
    )
    identity = next(
        value
        for value in original.probe_identities
        if value.probe_id == episode.probe_id
    )
    first = episode.state_intervals[0]
    second_service = NodeId(
        65001, "satellite", "migration-target"
    ).value
    migration_time = original.nominal_boundaries[1]
    migrated_episode = replace(
        episode,
        state_intervals=(
            replace(first, end_time=migration_time),
            replace(
                first,
                start_time=migration_time,
                end_time=original.coverage_end,
                service_satellite_id=second_service,
                path_node_ids=(
                    second_service,
                    identity.ground_station_id,
                ),
                boundary_reasons=("zone-source-migration",),
            ),
        ),
    )
    plan = replace(
        original,
        probe_identities=(identity,),
        episodes=(migrated_episode,),
    )
    processes = _install_fake_docker_processes(monkeypatch)
    evidence = tmp_path / "migration-execution.jsonl"
    executor = TrafficProbeExecutor(
        docker_executable="docker",
        plan=plan,
        runner_snapshot=_runner_snapshot(plan),
        evidence_path=evidence,
    )

    executor._start_episode(migrated_episode)
    executor._published_frontier = original.coverage_start
    executor._reconcile_episode(migrated_episode)
    first_service = first.service_satellite_id
    assert first_service is not None
    assert executor._active[migrated_episode.episode_id].binding.node_id == (
        first_service
    )
    assert executor._active[migrated_episode.episode_id].source_ipv4 == (
        identity.zone_ipv4
    )
    executor._runner = _OriginRunner(plan)
    status = executor._runner.status()
    status["active_frontier"] = migration_time.isoformat()
    monkeypatch.setattr(executor._runner, "status", lambda: status)
    executor._observe_published_frontier()
    executor._reconcile_episode(migrated_episode)
    assert executor._active[migrated_episode.episode_id].binding.node_id == (
        second_service
    )
    executor._terminate_all("test-finalization")
    executor._selector.close()

    records = [json.loads(line) for line in evidence.read_text().splitlines()]
    migrations = [
        value
        for value in records
        if value["record_type"] == "zone_source_migration"
    ]
    assert len(migrations) == 1
    assert migrations[0]["previous_source_node_id"] == first_service
    assert migrations[0]["current_source_node_id"] == second_service
    assert all(process.poll() is not None for process in processes.values())


def test_probe_stop_waits_for_actual_completion_without_escalation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    complete_chain,
) -> None:
    _, plans, realizations, _ = complete_chain
    original = TrafficProbePlanBuilder().build(plans, realizations)
    episode = next(
        value for value in original.episodes if value.mode == "persistent"
    )
    identity = next(
        value
        for value in original.probe_identities
        if value.probe_id == episode.probe_id
    )
    plan = replace(
        original,
        probe_identities=(identity,),
        episodes=(episode,),
    )
    processes = _install_fake_docker_processes(monkeypatch, ignore_term=True)
    evidence = tmp_path / "kill-execution.jsonl"
    executor = TrafficProbeExecutor(
        docker_executable="docker",
        plan=plan,
        runner_snapshot=_runner_snapshot(plan),
        evidence_path=evidence,
    )

    executor._start_episode(episode)
    executor._published_frontier = plan.coverage_start
    executor._reconcile_episode(episode)
    from threading import Thread
    cleanup = Thread(target=lambda: executor._terminate_all("test-finalization"))
    cleanup.start()
    time.sleep(0.05)
    assert cleanup.is_alive()
    assert all(process.poll() is None for process in processes.values())
    # The fake deliberately ignores TERM. The test supplies completion explicitly;
    # elapsed time is never a production reason to escalate.
    for process in processes.values():
        process.kill()
    cleanup.join()
    executor._selector.close()

    records = [json.loads(line) for line in evidence.read_text().splitlines()]
    stopped = [
        value for value in records if value["record_type"] == "generator_stopped"
    ]
    assert len(stopped) == 1
    assert stopped[0]["stop_signal"] == "TERM"
    assert stopped[0]["cleanup_result"] == "cleaned"
    assert all(process.poll() is not None for process in processes.values())


def test_controlled_traffic_interrupts_and_rearms_without_catch_up(tmp_path, monkeypatch, complete_chain):
    _, plans, realizations, _ = complete_chain
    plan = TrafficProbePlanBuilder().build(plans, realizations)
    _install_fake_docker_processes(monkeypatch)
    path = tmp_path / "pause.jsonl"
    executor = TrafficProbeExecutor(docker_executable="docker", plan=plan,
        runner_snapshot=_runner_snapshot(plan), evidence_path=path)
    runner = _OriginRunner(plan)
    status = runner.status()
    monkeypatch.setattr(runner, "status", lambda: status)
    executor.start(runner)
    def await_fact(predicate):
        deadline = time.monotonic() + 5
        while not predicate():
            assert time.monotonic() < deadline
            time.sleep(0.005)
    try:
        await_fact(lambda: executor.snapshot()["active_generators"] > 0)
        armed = set(executor._armed_episodes)
        status["paused"] = True
        status["wait_reasons"] = ["user-pause", "material"]
        status["clock"]["held_since"] = time.monotonic()
        await_fact(lambda: executor.snapshot()["logical_hold"] and not executor.snapshot()["active_generators"])
        status["paused"] = False
        status["wait_reasons"] = ["material"]
        time.sleep(0.02)
        assert not executor.snapshot()["active_generators"]
        status["wait_reasons"] = []
        status["clock"].update(held_since=None, wall_origin=time.monotonic(), revision=1)
        await_fact(lambda: not executor.snapshot()["logical_hold"] and executor.snapshot()["active_generators"] > 0)
        assert executor._armed_episodes == armed
    finally:
        executor.stop()
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert any(row["record_type"] == "controlled_traffic_interrupted" for row in records)
    assert any(row["record_type"] == "controlled_traffic_rearmed" and row["catch_up"] is False for row in records)
    assert any(row["record_type"] == "generator_launched" and row["segment_index"] == 2 for row in records)


def test_probe_executor_fails_closed_when_required_binding_is_missing(
    tmp_path: Path,
    complete_chain,
) -> None:
    _, plans, realizations, _ = complete_chain
    plan = TrafficProbePlanBuilder().build(plans, realizations)
    omitted = next(
        interval.service_satellite_id
        for episode in plan.episodes
        for interval in episode.state_intervals
        if interval.service_satellite_id is not None
    )

    with pytest.raises(ValueError, match="lacks required Runner bindings"):
        TrafficProbeExecutor(
            docker_executable="docker",
            plan=plan,
            runner_snapshot=_runner_snapshot(plan, omit=omitted),
            evidence_path=tmp_path / "probe-execution.jsonl",
        )


def test_shell_metadata_coverage_and_global_budget_match_incremental(complete_chain):
    simulator, plans, realizations, _ = complete_chain
    world = simulator.get_physical_world()
    expected = TrafficProbePlanBuilder(physical_world=world).build(plans, realizations)
    incremental = TrafficProbePlanBuilder(physical_world=world)
    for plan, realization in zip(plans, realizations, strict=True):
        incremental.append(plan, realization)
    actual = incremental.finalize()
    left, right = expected.artifact(), actual.artifact()
    left['planning']['duration_seconds'] = right['planning']['duration_seconds'] = 0
    assert left == right
    assert left['shell_metadata'] and left['shell_coverage']
    assert len(expected.episodes) <= left['global_budget']['maximum_episodes']
    assert left['planning']['maximum_concurrent_episode_bound'] <= 8
    assert left['planning']['estimated_echo_requests'] <= 3600
    assert read_traffic_probe_plan  # artifact round trip is also checked directly
    from tools.generation.traffic.probe_plan import TrafficProbePlan
    assert TrafficProbePlan.from_artifact(left).artifact() == left
    membership = dict(expected.shell_metadata)
    for coverage in left['shell_coverage']:
        for episode in coverage['episodes']:
            selected = next(e for e in expected.episodes if e.episode_id == episode['episode_id'])
            assert any(membership.get(n) == coverage['shell_id'] for i in selected.state_intervals
                       if i.expected_reachable for n in i.path_node_ids)
        if not coverage['eligible']:
            assert coverage['reason'] and coverage['status'] == 'no-eligible-candidate'


def test_bpf_builder_does_not_query_native_geography(complete_chain, monkeypatch):
    simulation, plans, realizations, _ = complete_chain
    world = simulation.get_physical_world()
    def no_native_query(*args, **kwargs):
        pytest.fail('BPF workload queried native-only Zone geography')
    monkeypatch.setattr(type(world), 'get_zone_layout', no_native_query)
    builder = TrafficProbePlanBuilder(physical_world=world)
    result = builder.build(plans, realizations)
    assert result.forwarding_backend == 'bpf' and result.episodes
    assert not builder._ground_coordinates and not builder._zone_coordinates
    assert builder._geography_world is None


def test_global_selection_covers_five_shells_without_multiplying_workload():
    from tools.generation.traffic.probe_plan import _budgeted_requests
    seeds = []
    for i in range(5):
        for direction in (_DIRECTION_GROUND_TO_ZONE, _DIRECTION_ZONE_TO_GROUND):
            seed = _PairSeed(direction, NodeId(1, 'ground-station', f'g{i}'), ("", f'z{i}'), {})
            for case, offset in [('stable', 10), ('transition', 200)]:
                seed.reason_times[f'shell-coverage:S{i+1}:{case}'] = {START+timedelta(seconds=offset+i*20)}
            seeds.append(seed)
    selected = _budgeted_requests(seeds, (), START, START+timedelta(seconds=600), (20, 2, 200), (1., .5))
    assert len(selected) == 20
    assert selected == _budgeted_requests(tuple(reversed(seeds)), (), START, START+timedelta(seconds=600), (20, 2, 200), (1., .5))
    assert {r[0].key for r in selected} == {s.key for s in seeds}
    for i in range(5):
        for direction in (_DIRECTION_GROUND_TO_ZONE, _DIRECTION_ZONE_TO_GROUND):
            assert len([r for r in selected if r[0].service_key[1] == f'z{i}' and r[0].direction == direction]) == 2


@pytest.mark.parametrize('incremental', [False, True])
def test_shell_eligibility_scan_does_not_resolve_unselected_paths(monkeypatch, complete_chain, incremental):
    from tools.generation.traffic import probe_plan as module
    simulator, plans, realizations, _ = complete_chain
    original = module._resolve_path
    calls = []
    def resolve(seed, projection):
        calls.append((seed.key, projection.nominal_time))
        return original(seed, projection)
    monkeypatch.setattr(module, '_resolve_path', resolve)
    if incremental:
        builder = TrafficProbePlanBuilder(physical_world=simulator.get_physical_world())
        for plan, realization in zip(plans, realizations, strict=True):
            builder.append(plan, realization)
        result = builder.finalize()
    else:
        result = TrafficProbePlanBuilder(physical_world=simulator.get_physical_world()).build(plans, realizations)
    assert result.shell_metadata
    # This fixture needs 14 selected forward/reverse resolutions; the old
    # Shell branch added 16 more while scanning eligibility.
    assert len(calls) == 14
    assert len({key for key, _ in calls}) <= 2 * len(result.probe_identities)


def test_long_controls_credit_only_eligible_windows_within_shared_budget():
    from types import SimpleNamespace
    from datetime import datetime, timezone, timedelta
    from tools.generation.traffic.probe_plan import _budgeted_requests, _PERSISTENT
    origin = datetime(2026, 1, 1, tzinfo=timezone.utc)
    seeds = []
    for shell in range(5):
        for direction in ('ground-station-to-zone', 'zone-to-ground-station'):
            index = len(seeds)
            seeds.append(SimpleNamespace(key=str(index), direction=direction, reason_times={
                f'shell-coverage:S{shell}:stable': {origin+timedelta(seconds=10+index*20)},
                f'shell-coverage:S{shell}:transition': {origin+timedelta(seconds=15+index*20)}}))
    # A nominal long control whose eligible windows are outside its episode
    # earns no credit; the other one contributes its two supported goals.
    longs = ((seeds[0], _PERSISTENT, origin, origin, origin+timedelta(seconds=600)),
             (seeds[1], _PERSISTENT, origin+timedelta(seconds=300), origin+timedelta(seconds=300), origin+timedelta(seconds=600)))
    result = _budgeted_requests(seeds, longs, origin, origin+timedelta(seconds=600),
                                (20,8,3600), (1,.25))
    covered = {(reason, seed.direction) for seed, _, _, begin, end in result
               for reason,times in seed.reason_times.items() if any(begin <= at < end for at in times)}
    assert len(covered) == 20
    assert len(result) == 20
    assert sum(r[1] == _PERSISTENT for r in result) == 2


def test_long_control_outside_reservoir_gets_only_reachable_window_credit():
    from tools.generation.traffic.probe_plan import _complete_control_coverage
    ground = NodeId(65001, 'ground-station', 'g')
    satellites = [NodeId(65001, 'satellite', f's{i}') for i in range(2)]
    seed = _PairSeed(_DIRECTION_ZONE_TO_GROUND, ground, ("", 'zone'), {
        'long-changing-candidate': {START}, 'persistent-control-candidate': {START}})
    times = tuple(START + timedelta(seconds=i*5) for i in range(4))
    def scan(visit):
        for index, at in enumerate(times):
            satellite = satellites[index > 0]
            forward = RouteDemand(ground, ZoneRouteDemandTarget('zone'))
            reverse = RouteDemand(satellite, GroundStationRouteDemandTarget(ground))
            outcomes = {forward: ReachableRouteOutcome(forward, satellite, (ground, satellite)),
                        reverse: ReachableRouteOutcome(reverse, ground, (satellite, ground))}
            # The return path is unsupported at 10 s; it earns neither goal.
            if index == 2:
                outcomes.pop(reverse)
            visit(SimpleNamespace(nominal_time=at, zone_edges={('', 'zone'): ('edge', satellite)},
                route_outcomes=outcomes, changed_access_ground_stations=[ground] if index == 0 else []))
        return times
    _complete_control_coverage(scan, (seed,), times, dict(zip(satellites, ('S1', 'S2'))))
    assert seed.reason_times['shell-coverage:S1:stable'] == {START+timedelta(seconds=3)}
    assert 'shell-coverage:S1:transition' not in seed.reason_times
    assert seed.reason_times['shell-coverage:S2:transition'] == {times[1]}
    assert seed.reason_times['shell-coverage:S2:stable'] == {times[1]+timedelta(seconds=3)}


def test_same_path_access_change_retains_its_transition_boundary():
    from tools.generation.traffic.probe_plan import _append_state_interval
    first = _ProbeStateInterval(START, START+timedelta(seconds=5), True, None,
                                'sat', ('ground', 'sat'), 1, ())
    second = replace(first, start_time=first.end_time, end_time=START+timedelta(seconds=10),
                     boundary_reasons=('ground-station-attachment-change',))
    intervals = [first]
    _append_state_interval(intervals, second)
    assert intervals == [first, second]


def test_empty_workload_is_valid_and_does_not_reconstruct_paths(monkeypatch):
    from tools.generation.traffic import probe_plan as module
    simulation = create_complete_static_simulator(build_emulator(), zone_layout_path=None)
    plans, realizations, _ = run_complete_static_chain(simulation)
    monkeypatch.setattr(module, '_resolve_path', lambda *a: pytest.fail('empty selection reconstructed paths'))
    finite = TrafficProbePlanBuilder(maximum_echo_requests=1).build(plans, realizations)
    builder = TrafficProbePlanBuilder(maximum_echo_requests=1)
    for plan, realization in zip(plans, realizations):
        builder.append(plan, realization)
    incremental = builder.finalize()
    assert finite.episodes == incremental.episodes == ()
    assert finite.artifact()['planning']['workload_status'] == 'empty'
    assert finite.artifact()['planning']['empty_reason']
    assert finite.nominal_boundaries == incremental.nominal_boundaries
    assert builder.snapshot()['boundary_delta_count'] == 0
    assert builder._initial_projection is not None


def test_failed_flush_retains_accepted_delivery(complete_chain):
    _, plans, realizations, _ = complete_chain
    builder = TrafficProbePlanBuilder()
    builder.append(plans[0], realizations[0])
    before = builder.snapshot()
    with pytest.raises(ValueError, match='cannot precede'):
        builder.flush(end_time=plans[0].start_timestamp-timedelta(seconds=1))
    assert builder.snapshot() == before
    first = builder.flush()
    assert not builder.snapshot()['finalized']
    assert builder.snapshot()['boundary_delta_count'] == 0
    for plan, realization in zip(plans[1:], realizations[1:]):
        builder.append(plan, realization)
    suffix = builder.finalize()
    assert suffix.coverage_start > first.coverage_end
    assert suffix.nominal_boundaries == realizations[-1].activation_timestamps
    assert suffix.artifact()['planning']['workload_status'] == 'empty'


def _owned_stopped_generator(executor, plan, index=0):
    import os
    import selectors
    from tools.generation.traffic.probe_execution import _RunningGenerator
    episode = plan.episodes[index]
    identity = executor._identities[episode.probe_id]
    streams = []
    for _ in range(2):
        reader, writer = os.pipe()
        os.close(writer)
        streams.append(os.fdopen(reader, 'rb'))
    process = SimpleNamespace(stdout=streams[0], stderr=streams[1], returncode=0,
                              poll=lambda: 0, wait=lambda: 0)
    generator = _RunningGenerator(episode, identity, next(iter(executor._bindings.values())),
        'test-process', '/tmp/fake-process', '1.1.1.1', '2.2.2.2', 1, 1, process, 0)
    executor._all_generators.append(generator)
    executor._active[episode.episode_id] = generator
    for name, stream in zip(('stdout', 'stderr'), streams):
        executor._selector.register(stream, selectors.EVENT_READ, (generator, name))
        executor._buffers[id(generator), name] = b''
    return generator


@pytest.mark.parametrize('failure_at', ['drain', 'record'])
@pytest.mark.parametrize('release_fails', [False, True])
def test_stopped_generator_releases_all_local_resources_after_recording_failure(tmp_path, complete_chain, monkeypatch, failure_at, release_fails):
    _, plans, realizations, _ = complete_chain
    plan = TrafficProbePlanBuilder().build(plans, realizations)
    executor = TrafficProbeExecutor(docker_executable='docker', plan=plan,
        runner_snapshot=_runner_snapshot(plan), evidence_path=tmp_path/'traffic.jsonl')
    generator = _owned_stopped_generator(executor, plan)
    monkeypatch.setattr(executor, '_stable_generator_state', lambda g: 'stale')
    monkeypatch.setattr(executor, '_generator_control', lambda *a, **k: 'cleaned')
    failure = OSError('evidence unavailable')
    def fail(*a, **k): raise failure
    monkeypatch.setattr(executor, '_drain_generator_io' if failure_at == 'drain' else '_record', fail)
    if release_fails:
        if failure_at == 'record':
            monkeypatch.setattr(executor, '_drain_generator_io', lambda g: None)
        unregister = executor._selector.unregister
        def failed_unregister(stream):
            unregister(stream)
            raise OSError('secondary selector-release failure')
        monkeypatch.setattr(executor._selector, 'unregister', failed_unregister)
    executor._terminate_all('requested-stop')
    assert executor._failure is failure
    assert executor.snapshot()['cleanup_complete']
    assert not executor._active and not executor._all_generators and not executor._buffers
    assert not executor._selector.get_map()
    assert generator.process.stdout.closed and generator.process.stderr.closed
    executor._selector.close()


def test_generator_stop_failure_remains_owned_while_other_generators_clean(tmp_path, complete_chain, monkeypatch):
    _, plans, realizations, _ = complete_chain
    plan = TrafficProbePlanBuilder().build(plans, realizations)
    executor = TrafficProbeExecutor(docker_executable='docker', plan=plan,
        runner_snapshot=_runner_snapshot(plan), evidence_path=tmp_path/'traffic.jsonl')
    first = _owned_stopped_generator(executor, plan, 0)
    second = _owned_stopped_generator(executor, plan, 1)
    failure = RuntimeError('actual identity failure')
    def state(g):
        if g is first: raise failure
        return 'stale'
    monkeypatch.setattr(executor, '_stable_generator_state', state)
    monkeypatch.setattr(executor, '_generator_control', lambda *a, **k: 'cleaned')
    executor._terminate_all('requested-stop')
    assert executor._failure is failure
    assert not executor.snapshot()['cleanup_complete']
    assert executor._all_generators == [first]
    assert not first.process.stdout.closed and second.process.stdout.closed
    monkeypatch.setattr(executor, '_stable_generator_state', lambda g: 'stale')
    executor._terminate_all('confirmed-afterward')
    assert executor.snapshot()['cleanup_complete']
    assert executor._failure is failure
    executor._selector.close()
