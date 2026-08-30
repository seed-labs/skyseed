from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import time

import pytest

from skyseed_v2.experiment.traffic.probe_plan import (
    TrafficProbePlanBuilder,
    _DIRECTION_GROUND_TO_ZONE,
    _DIRECTION_ZONE_TO_GROUND,
    _PairSeed,
    read_traffic_probe_plan,
    write_traffic_probe_plan,
)
from skyseed_v2.experiment.traffic.probe_execution import TrafficProbeExecutor
from skyseed_v2.backend.bpf import BpfRunner
from skyseed_v2.backend.bpf.compiled_realization import (
    BpfCompiledRealization,
    _AttachmentDemand,
    _ForwardingBinding,
    _ForwardingIntent,
    _IntendedState,
    _QdiscIntent,
    _ResourceIntent,
    _ResourceState,
    _TopologySignature,
    _ZoneEdgeIntent,
    _ZoneEdgeState,
)
from skyseed_v2.shared import NodeId
from skyseed_v2.simulator import (
    Connection,
    ConnectionDirection,
    ConnectionId,
    ConnectionKind,
    ConnectionTransitionOperation,
    DifferenceOperation,
    DirectionalConnectionParameters,
    GroundStationRouteTarget,
    RouteKey,
    ZoneRouteTarget,
)


START = datetime(2026, 1, 1, tzinfo=timezone.utc)
GROUND = NodeId(65001, "ground-station", "ground-1")
SATELLITE_A = NodeId(65001, "satellite", "satellite-a")
SATELLITE_B = NodeId(65001, "satellite", "satellite-b")
ZONE = "zone-a"
ZONE_PREFIX = "198.18.0.1/32"
ACCESS = ConnectionId(
    ConnectionKind.GROUND_STATION_ACCESS, GROUND, SATELLITE_A
)
ISL = ConnectionId(
    ConnectionKind.INTER_SATELLITE, SATELLITE_A, SATELLITE_B
)
PARAMETERS = DirectionalConnectionParameters(1_000_000, 1_000, 0)


def _connection(connection_id: ConnectionId) -> Connection:
    return Connection(connection_id, PARAMETERS, PARAMETERS)


def _direction(connection_id: ConnectionId, source: NodeId) -> ConnectionDirection:
    return (
        ConnectionDirection.ENDPOINT_A_TO_B
        if connection_id.endpoint_a == source
        else ConnectionDirection.ENDPOINT_B_TO_A
    )


def _binding(
    source: NodeId,
    target,
    next_hop: NodeId,
    connection_id: ConnectionId,
) -> _ForwardingBinding:
    return _ForwardingBinding(
        RouteKey(source, target),
        next_hop,
        connection_id,
        _direction(connection_id, source),
        ZONE_PREFIX if isinstance(target, ZoneRouteTarget) else None,
    )


def _resource_intent(connection_id: ConnectionId) -> _ResourceIntent:
    first, second = connection_id.endpoint_a, connection_id.endpoint_b
    return _ResourceIntent(
        START,
        ConnectionTransitionOperation.ESTABLISH,
        connection_id,
        (
            _AttachmentDemand(
                connection_id,
                ConnectionDirection.ENDPOINT_A_TO_B,
                first,
                second,
            ),
            _AttachmentDemand(
                connection_id,
                ConnectionDirection.ENDPOINT_B_TO_A,
                second,
                first,
            ),
        ),
    )


def _handover_realization(
    *, loop: bool = False, one_way_initial: bool = False
) -> BpfCompiledRealization:
    zone_target = ZoneRouteTarget(ZONE)
    ground_target = GroundStationRouteTarget(GROUND)
    ground_to_a = _binding(GROUND, zone_target, SATELLITE_A, ACCESS)
    a_to_b = _binding(
        SATELLITE_A,
        zone_target,
        GROUND if loop else SATELLITE_B,
        ACCESS if loop else ISL,
    )
    b_to_a = _binding(SATELLITE_B, ground_target, SATELLITE_A, ISL)
    a_to_ground = _binding(SATELLITE_A, ground_target, GROUND, ACCESS)
    initial_forwarding = (
        (ground_to_a, b_to_a, a_to_ground)
        if one_way_initial
        else (ground_to_a, a_to_b, b_to_a, a_to_ground)
    )
    forwarding_intents = [
        _ForwardingIntent(
            START, DifferenceOperation.ADDED, value.route_key, None, value
        )
        for value in initial_forwarding
    ]
    if not loop:
        forwarding_intents.extend(
            tuple(
                _ForwardingIntent(
                    START + timedelta(seconds=10),
                    DifferenceOperation.REMOVED,
                    value.route_key,
                    value,
                    None,
                )
                for value in (
                    (b_to_a,) if one_way_initial else (a_to_b, b_to_a)
                )
            )
        )
    intended_forwarding = (
        (ground_to_a, a_to_ground)
        if not loop
        else (ground_to_a, a_to_b, b_to_a, a_to_ground)
    )
    zone_intents = [
        _ZoneEdgeIntent(
            START,
            DifferenceOperation.ADDED,
            ZONE,
            ZONE_PREFIX,
            None,
            SATELLITE_B,
        )
    ]
    intended_service = SATELLITE_B
    if not loop:
        zone_intents.append(
            _ZoneEdgeIntent(
                START + timedelta(seconds=10),
                DifferenceOperation.CHANGED,
                ZONE,
                ZONE_PREFIX,
                SATELLITE_B,
                SATELLITE_A,
            )
        )
        intended_service = SATELLITE_A
    return BpfCompiledRealization._create(
        start_slice_id=0,
        start_timestamp=None,
        end_slice_id=2,
        end_timestamp=START + timedelta(seconds=20),
        resource_realizations=(_resource_intent(ACCESS), _resource_intent(ISL)),
        parameter_realizations=(
            (
                _QdiscIntent(
                    START + timedelta(seconds=10),
                    ACCESS,
                    _direction(ACCESS, GROUND),
                    GROUND,
                    SATELLITE_A,
                    None,
                    2_000,
                    None,
                    "test-boundary",
                ),
            )
            if loop
            else ()
        ),
        forwarding_realizations=tuple(forwarding_intents),
        zone_edge_realizations=tuple(zone_intents),
        constraints=(),
        intended_state=_IntendedState(
            (_ResourceState(_connection(ACCESS)), _ResourceState(_connection(ISL))),
            (),
            tuple(intended_forwarding),
            (_ZoneEdgeState(ZONE, ZONE_PREFIX, intended_service),),
            ((ZONE, ZONE_PREFIX),),
        ),
        topology_signature=_TopologySignature(
            (GROUND, SATELLITE_A, SATELLITE_B), (ZONE,)
        ),
    )


def _extension_realization() -> BpfCompiledRealization:
    base = _handover_realization()
    return BpfCompiledRealization._create(
        start_slice_id=2,
        start_timestamp=START + timedelta(seconds=20),
        end_slice_id=3,
        end_timestamp=START + timedelta(seconds=30),
        resource_realizations=(),
        parameter_realizations=(
            _QdiscIntent(
                START + timedelta(seconds=20),
                ACCESS,
                _direction(ACCESS, GROUND),
                GROUND,
                SATELLITE_A,
                None,
                3_000,
                None,
                "extension-boundary",
            ),
        ),
        forwarding_realizations=(),
        zone_edge_realizations=(),
        constraints=(),
        intended_state=base._intended_state,
        topology_signature=base._topology_signature,
    )


def test_probe_plan_selection_is_deterministic_bounded_and_horizon_scoped(tmp_path):
    builder = TrafficProbePlanBuilder()

    first = builder.build((_handover_realization(),))
    second = builder.build((_handover_realization(),))
    first_artifact = first.artifact()
    second_artifact = second.artifact()

    assert [value["probe_id"] for value in first_artifact["probe_identities"]] == [
        value["probe_id"] for value in second_artifact["probe_identities"]
    ]
    assert len(first.episodes) <= 20
    assert {value.direction for value in first.probe_identities} == {
        "ground-station-to-zone",
        "zone-to-ground-station",
    }
    assert any(
        "zone-service-handover" in value.selection_reasons
        for value in first.probe_identities
    )
    assert any(value.mode == "activation-window" for value in first.episodes)
    assert any(value.mode == "rotating-background" for value in first.episodes)
    assert first_artifact["planning_horizon"] == {
        "coverage_start_time": START.isoformat(),
        "coverage_end_time": (START + timedelta(seconds=20)).isoformat(),
        "supplied_realization_count": 1,
        "terminality": "unknown",
        "revision_semantics": "revisable-lookahead",
        "revision_id": first.revision_id,
        "nominal_boundaries": [
            START.isoformat(),
            (START + timedelta(seconds=10)).isoformat(),
        ],
    }
    assert first_artifact["planning"]["maximum_request_rate_per_second"] <= 40
    assert first_artifact["planning"]["selection_strategy"] == (
        "bounded-stratified-reservoir"
    )
    assert first_artifact["planning"]["global_flow_ranking"] is False

    path = write_traffic_probe_plan(first, tmp_path / "probe-plan.json")
    loaded = read_traffic_probe_plan(path)
    assert [value.probe_id for value in loaded.probe_identities] == [
        value.probe_id for value in first.probe_identities
    ]
    assert [value.episode_id for value in loaded.episodes] == [
        value.episode_id for value in first.episodes
    ]
    assert json.loads(path.read_text())["runtime_authority"] is False
    assert (
        json.loads(path.read_text())["classification_scope"]
        == "authoritative-active-published-runtime-only"
    )


def test_probe_plan_resolves_paths_only_after_bounded_coverage_selection(
    monkeypatch,
):
    from skyseed_v2.experiment.traffic import probe_plan as probe_plan_module

    original_candidates = probe_plan_module._candidate_pairs
    original_resolve = probe_plan_module._resolve_path
    resolved = []

    def many_candidates(values, nominal_boundaries):
        selected = list(original_candidates(values, nominal_boundaries))
        for index in range(60):
            selected.append(
                _PairSeed(
                    (
                        _DIRECTION_GROUND_TO_ZONE
                        if index % 2 == 0
                        else _DIRECTION_ZONE_TO_GROUND
                    ),
                    NodeId(
                        65001,
                        "ground-station",
                        f"zz-background-{index:03d}",
                    ),
                    ZONE,
                    {"persistent-control-candidate": {START}},
                )
            )
        return tuple(selected)

    def count_resolve(seed, projection):
        resolved.append(seed.key)
        return original_resolve(seed, projection)

    monkeypatch.setattr(probe_plan_module, "_candidate_pairs", many_candidates)
    monkeypatch.setattr(probe_plan_module, "_resolve_path", count_resolve)

    plan = TrafficProbePlanBuilder().build((_handover_realization(),))

    assert plan.candidate_pair_count == 62
    assert len(set(resolved)) <= 20
    assert len(resolved) <= 20 * len(plan.nominal_boundaries)
    assert len(resolved) < plan.candidate_pair_count * len(plan.nominal_boundaries)


def test_windowed_probe_reconstruction_is_event_local(monkeypatch):
    from skyseed_v2.experiment.traffic import probe_plan as probe_plan_module

    original = probe_plan_module._resolve_path
    resolved = []

    def count_resolve(seed, projection):
        resolved.append((seed.key, projection.nominal_time))
        return original(seed, projection)

    monkeypatch.setattr(probe_plan_module, "_resolve_path", count_resolve)
    plan = TrafficProbePlanBuilder(
        maximum_persistent_probes=0,
        maximum_activation_window_probes=8,
    ).build((_handover_realization(), _extension_realization()))

    assert plan.episodes
    assert {episode.mode for episode in plan.episodes} == {"activation-window"}
    assert len(resolved) < (
        len(plan.probe_identities) * len(plan.nominal_boundaries) * 2
    )


def test_adjacent_semantically_equal_probe_states_are_compressed():
    plan = TrafficProbePlanBuilder().build(
        (_handover_realization(), _extension_realization())
    )
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
                and prior.service_satellite_id == current.service_satellite_id
                and prior.path_node_ids == current.path_node_ids
                and prior.path_length_hops == current.path_length_hops
            )


def test_probe_plan_classifies_zone_source_migration_and_path_change():
    plan = TrafficProbePlanBuilder().build((_handover_realization(),))

    reverse_ids = {
        value.probe_id
        for value in plan.probe_identities
        if value.direction == "zone-to-ground-station"
    }
    reverse_identities = [
        value for value in plan.probe_identities if value.probe_id in reverse_ids
    ]
    reverse_episodes = [
        value for value in plan.episodes if value.probe_id in reverse_ids
    ]

    assert reverse_identities
    assert reverse_episodes
    assert any(value.path_stability == "changing" for value in reverse_identities)
    assert any(
        "zone-source-migration" in interval.boundary_reasons
        for value in reverse_episodes
        for interval in value.state_intervals
    )
    assert {
        interval.service_satellite_id
        for value in reverse_episodes
        for interval in value.state_intervals
    } == {SATELLITE_A.value, SATELLITE_B.value}


def test_probe_reachability_requires_request_and_echo_return_paths():
    plan = TrafficProbePlanBuilder().build(
        (_handover_realization(one_way_initial=True),)
    )
    reverse_episodes = [
        value
        for value in plan.episodes
        if next(
            identity
            for identity in plan.probe_identities
            if identity.probe_id == value.probe_id
        ).direction
        == _DIRECTION_ZONE_TO_GROUND
    ]

    assert reverse_episodes
    intervals = reverse_episodes[0].state_intervals
    assert intervals[0].expected_reachable is False
    assert intervals[0].unreachable_reason == "return-missing-forwarding"
    assert intervals[1].expected_reachable is True


def test_probe_plan_fails_closed_on_forwarding_loop():
    with pytest.raises(ValueError, match="forwarding loop"):
        TrafficProbePlanBuilder().build((_handover_realization(loop=True),))


def test_probe_plan_does_not_claim_complete_experiment():
    artifact = TrafficProbePlanBuilder().build((_handover_realization(),)).artifact()

    artifact["planning_horizon"]["terminality"] = "terminal"

    with pytest.raises(ValueError, match="claims terminality"):
        from skyseed_v2.experiment.traffic.probe_plan import TrafficProbePlan

        TrafficProbePlan.from_artifact(artifact)


def test_probe_identity_and_episode_identity_survive_horizon_extension():
    builder = TrafficProbePlanBuilder()
    prefix = builder.build((_handover_realization(),))
    extended = builder.build((_handover_realization(), _extension_realization()))

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


def _runner_snapshot(*, omit: str | None = None) -> dict[str, object]:
    bindings = []
    for index, node in enumerate((GROUND, SATELLITE_A, SATELLITE_B), start=1):
        if node.value == omit:
            continue
        bindings.append(
            {
                "node_id": node.value,
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


class _OriginRunner(BpfRunner):
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
            )
        }


class _JumpedOriginRunner(_OriginRunner):
    def __init__(self, plan) -> None:
        super().__init__(plan)
        self._test_origin -= (plan.coverage_end - plan.coverage_start).total_seconds()

    def inspection_snapshot(self) -> dict[str, object]:
        value = super().inspection_snapshot()
        value["runner"]["frontiers"]["last_active"] = (
            self._test_plan.nominal_boundaries[-1].isoformat()
        )
        return value


def _install_fake_docker_processes(monkeypatch, *, ignore_term: bool = False):
    from skyseed_v2.experiment.traffic import probe_execution as execution_module

    real_popen = subprocess.Popen
    processes = {}

    def popen(command, **_kwargs):
        runtime_identity = next(
            value.removeprefix("SKYSEED_TRAFFIC_GENERATOR_ID=")
            for value in command
            if isinstance(value, str)
            and value.startswith("SKYSEED_TRAFFIC_GENERATOR_ID=")
        )
        term_handler = "trap '' TERM; " if ignore_term else (
            "trap 'printf \"2 packets transmitted, 1 received, 50% packet loss\\n\"; exit 0' TERM; "
        )
        process = real_popen(
            (
                "sh",
                "-c",
                "printf '[1.0] 64 bytes from test: icmp_seq=1 ttl=64 time=1.25 ms\\n'; "
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
        state = "missing" if process is None else "active" if process.poll() is None else "stale"
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


def test_probe_executor_resolves_bindings_parses_ping_and_cleans_every_process(
    tmp_path, monkeypatch
):
    plan = TrafficProbePlanBuilder().build((_handover_realization(),))
    processes = _install_fake_docker_processes(monkeypatch)
    evidence = tmp_path / "probe-execution.jsonl"
    executor = TrafficProbeExecutor(
        docker_executable="docker",
        plan=plan,
        runner_snapshot=_runner_snapshot(),
        evidence_path=evidence,
    )

    executor.start(_OriginRunner(plan))
    time.sleep(0.2)
    snapshot = executor.stop()
    records = [json.loads(line) for line in evidence.read_text().splitlines()]

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
        value["observation"] == "timeout" and value["icmp_sequence"] == 2
        for value in observations
    )
    assert all(
        value["cleanup_result"] == "cleaned"
        for value in records
        if value["record_type"] == "generator_stopped"
    )
    assert any(value["record_type"] == "episode_cancelled" for value in records)
    assert records[-1]["record_type"] == "executor_end"


def test_cancelled_episode_can_rearm_under_a_revised_lookahead(
    tmp_path, monkeypatch
):
    builder = TrafficProbePlanBuilder()
    first = builder.build((_handover_realization(),))
    revised = builder.build((_handover_realization(), _extension_realization()))
    processes = _install_fake_docker_processes(monkeypatch)
    evidence_sets = []

    for index, plan in enumerate((first, revised), start=1):
        evidence = tmp_path / f"probe-execution-{index}.jsonl"
        executor = TrafficProbeExecutor(
            docker_executable="docker",
            plan=plan,
            runner_snapshot=_runner_snapshot(),
            evidence_path=evidence,
        )
        executor.start(_OriginRunner(plan))
        time.sleep(0.15)
        executor.stop()
        evidence_sets.append(
            [json.loads(line) for line in evidence.read_text().splitlines()]
        )

    common_persistent = {
        value.episode_id
        for value in first.episodes
        if value.mode == "persistent"
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
    tmp_path, monkeypatch
):
    plan = TrafficProbePlanBuilder().build((_handover_realization(),))
    processes = _install_fake_docker_processes(monkeypatch)
    executor = TrafficProbeExecutor(
        docker_executable="docker",
        plan=plan,
        runner_snapshot=_runner_snapshot(),
        evidence_path=tmp_path / "frontier-jump-execution.jsonl",
    )

    executor.start(_JumpedOriginRunner(plan))
    deadline = time.monotonic() + 5
    while executor._thread is not None and executor._thread.is_alive():
        if time.monotonic() >= deadline:
            pytest.fail("frontier-jump executor did not finish")
        time.sleep(0.01)
    snapshot = executor.stop()

    assert snapshot["complete"] is True
    assert snapshot["duplicate_same_boundary_reconciliations"] == 0
    assert all(process.poll() is not None for process in processes.values())


def test_zone_to_ground_probe_migrates_its_physical_source(tmp_path, monkeypatch):
    plan = TrafficProbePlanBuilder().build((_handover_realization(),))
    episode = next(
        episode
        for episode in plan.episodes
        if episode.mode == "persistent"
        and next(
            identity
            for identity in plan.probe_identities
            if identity.probe_id == episode.probe_id
        ).direction
        == _DIRECTION_ZONE_TO_GROUND
    )
    processes = _install_fake_docker_processes(monkeypatch)
    evidence = tmp_path / "migration-execution.jsonl"
    executor = TrafficProbeExecutor(
        docker_executable="docker",
        plan=plan,
        runner_snapshot=_runner_snapshot(),
        evidence_path=evidence,
    )

    executor._start_episode(episode)
    executor._published_frontier = START
    executor._reconcile_episode(episode)
    assert executor._active[episode.episode_id].binding.node_id == SATELLITE_B.value
    assert executor._active[episode.episode_id].source_ipv4 == "198.18.0.1"
    executor._published_frontier = START + timedelta(seconds=10)
    executor._reconcile_episode(episode)
    assert executor._active[episode.episode_id].binding.node_id == SATELLITE_A.value
    executor._terminate_all("test-finalization")
    executor._selector.close()

    records = [json.loads(line) for line in evidence.read_text().splitlines()]
    migrations = [
        value for value in records if value["record_type"] == "zone_source_migration"
    ]
    assert len(migrations) == 1
    assert migrations[0]["previous_source_node_id"] == SATELLITE_B.value
    assert migrations[0]["current_source_node_id"] == SATELLITE_A.value
    assert all(process.poll() is not None for process in processes.values())


def test_probe_stop_escalates_from_term_to_kill(tmp_path, monkeypatch):
    plan = TrafficProbePlanBuilder().build((_handover_realization(),))
    episode = next(value for value in plan.episodes if value.mode == "persistent")
    processes = _install_fake_docker_processes(monkeypatch, ignore_term=True)
    evidence = tmp_path / "kill-execution.jsonl"
    executor = TrafficProbeExecutor(
        docker_executable="docker",
        plan=plan,
        runner_snapshot=_runner_snapshot(),
        evidence_path=evidence,
    )

    executor._start_episode(episode)
    executor._published_frontier = START
    executor._reconcile_episode(episode)
    executor._terminate_all("test-finalization")
    executor._selector.close()

    records = [json.loads(line) for line in evidence.read_text().splitlines()]
    stopped = [
        value for value in records if value["record_type"] == "generator_stopped"
    ]
    assert len(stopped) == 1
    assert stopped[0]["stop_signal"] == "KILL"
    assert stopped[0]["cleanup_result"] == "cleaned"
    assert all(process.poll() is not None for process in processes.values())


def test_probe_executor_fails_closed_when_required_binding_is_missing(tmp_path):
    plan = TrafficProbePlanBuilder().build((_handover_realization(),))

    with pytest.raises(ValueError, match="lacks required Runner bindings"):
        TrafficProbeExecutor(
            docker_executable="docker",
            plan=plan,
            runner_snapshot=_runner_snapshot(omit=SATELLITE_B.value),
            evidence_path=Path(tmp_path) / "probe-execution.jsonl",
        )
