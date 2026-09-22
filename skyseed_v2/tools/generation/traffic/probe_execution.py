"""Execute experiment Traffic probe episodes around one canonical Runner run."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import os
from pathlib import Path
import re
import selectors
import subprocess
from threading import Event, RLock, Thread
from time import monotonic, monotonic_ns, sleep
from typing import Any, BinaryIO
from uuid import uuid4

from skyseed_v2.backend.runner import Runner
from skyseed_v2.shared._durable_evidence import _append_jsonl, _reset_jsonl

from .probe_plan import (
    TrafficProbePlan,
    _DIRECTION_GROUND_TO_ZONE,
    _TrafficProbeEpisode,
    _TrafficProbeIdentity,
)


_EXECUTION_ARTIFACT = "traffic-probe-execution"
_FORMAT_VERSION = 1
_SEQUENCE = re.compile(r"icmp_seq[= ](?P<value>[0-9]+)")
_RTT = re.compile(r"time[=<](?P<value>[0-9]+(?:\.[0-9]+)?)\s*ms")
_SUMMARY = re.compile(
    r"(?P<sent>[0-9]+) packets transmitted,\s*"
    r"(?P<received>[0-9]+) received,\s*"
    r"(?P<loss>[0-9]+(?:\.[0-9]+)?)% packet loss"
)


_PING_SCRIPT = r"""
pid_file=$1
source_ipv4=$2
interval=$3
identifier=$4
destination_ipv4=$5
directory=${pid_file%/*}
mkdir -p "$directory" || exit 125
if [ -e "$pid_file" ]; then
    exit 125
fi
if ! printf '%s\n' "$$" > "$pid_file"; then
    exit 125
fi
exec ping -n -D -O -I "$source_ipv4" -i "$interval" -W 1 \
    -e "$identifier" "$destination_ipv4"
""".strip()


_CONTROL_SCRIPT = r"""
action=$1
pid_file=$2
generator_id=$3
directory=${pid_file%/*}
state=missing
pid=
if [ -f "$pid_file" ]; then
    IFS= read -r pid < "$pid_file" || pid=
    case "$pid" in
        ''|*[!0-9]*) state=invalid-pid ;;
        *)
            if [ ! -e "/proc/$pid" ]; then
                state=stale
            elif tr '\000' '\n' < "/proc/$pid/environ" 2>/dev/null \
                | grep -Fqx "SKYSEED_TRAFFIC_GENERATOR_ID=$generator_id"; then
                state=active
            else
                state=identity-mismatch
            fi
            ;;
    esac
else
    for environment in /proc/[0-9]*/environ; do
        if tr '\000' '\n' < "$environment" 2>/dev/null \
            | grep -Fqx "SKYSEED_TRAFFIC_GENERATOR_ID=$generator_id"; then
            candidate=${environment#/proc/}
            candidate=${candidate%/environ}
            if [ -n "$pid" ] && [ "$pid" != "$candidate" ]; then
                state=identity-duplicate
                pid=
                break
            fi
            pid=$candidate
            state=active
        fi
    done
fi
case "$action" in
    status)
        printf '%s\n' "$state"
        ;;
    term|kill)
        if [ "$state" = active ]; then
            signal=TERM
            [ "$action" = kill ] && signal=KILL
            if kill -"$signal" "$pid" 2>/dev/null; then
                printf '%s\n' signalled
            else
                printf '%s\n' stale
            fi
        else
            printf '%s\n' "$state"
        fi
        ;;
    cleanup)
        if [ "$state" = active ] || [ "$state" = identity-mismatch ] \
            || [ "$state" = identity-duplicate ]; then
            printf '%s\n' "$state"
            exit 42
        fi
        rm -f "$pid_file"
        rmdir "$directory" 2>/dev/null || true
        printf '%s\n' cleaned
        ;;
    discard)
        rm -f "$pid_file"
        rmdir "$directory" 2>/dev/null || true
        printf '%s\n' discarded
        ;;
    *) exit 64 ;;
esac
""".strip()


@dataclass(frozen=True, slots=True)
class _TrafficRuntimeBinding:
    node_id: str
    container_id: str
    container_name: str
    pid: int
    netns_inode: int
    started_at: str
    runtime_ipv4: str


@dataclass(slots=True)
class _RunningGenerator:
    episode: _TrafficProbeEpisode
    identity: _TrafficProbeIdentity
    binding: _TrafficRuntimeBinding
    runtime_identity: str
    pid_file: str
    source_ipv4: str
    destination_ipv4: str
    icmp_identifier: int
    segment_index: int
    process: subprocess.Popen[bytes]
    launched_monotonic_ns: int
    reply_sequences: set[int] = field(default_factory=set)
    timeout_sequences: set[int] = field(default_factory=set)
    observed_sequences: set[int] = field(default_factory=set)
    rtt_ms: list[float] = field(default_factory=list)
    summary_sent: int | None = None
    summary_received: int | None = None
    summary_loss_percent: float | None = None
    stop_signal: str | None = None
    cleanup_result: str | None = None


class TrafficProbeExecutor:
    """Supervise one revision's bounded probe episodes.

    This application-side helper consumes Runner facts but cannot activate a
    boundary or alter a frontier.  Episode cancellation on run end is normal
    revision/lifecycle evidence, not a network failure.
    """

    def __init__(
        self,
        *,
        docker_executable: str,
        plan: TrafficProbePlan,
        runner_snapshot: Mapping[str, object],
        evidence_path: Path,
    ) -> None:
        if not isinstance(docker_executable, str) or not docker_executable:
            raise ValueError("docker_executable must be non-empty")
        if not isinstance(plan, TrafficProbePlan):
            raise TypeError("plan must be a TrafficProbePlan")
        self._docker = docker_executable
        self._plan = plan
        self._bindings = _runtime_bindings(runner_snapshot)
        self._identities = {value.probe_id: value for value in plan.probe_identities}
        _validate_required_bindings(plan, self._identities, self._bindings)
        self._path = _reset_jsonl(
            evidence_path,
            {
                "record_type": "execution_header",
                "artifact_type": _EXECUTION_ARTIFACT,
                "format_version": _FORMAT_VERSION,
                "plan_revision_id": plan.revision_id,
                "probe_identity_count": len(plan.probe_identities),
                "episode_count": len(plan.episodes),
                "classification_scope": (
                    "authoritative-active-published-runtime-only"
                ),
            },
        )
        self._run_token = uuid4().hex
        self._stop = Event()
        self._lock = RLock()
        self._thread: Thread | None = None
        self._runner: Runner | None = None
        self._selector = selectors.DefaultSelector()
        self._buffers: dict[tuple[int, str], bytes] = {}
        self._active: dict[str, _RunningGenerator] = {}
        self._all_generators: list[_RunningGenerator] = []
        self._armed_episodes: set[str] = set()
        self._completed_episodes: set[str] = set()
        self._failure: BaseException | None = None
        self._executor_complete = False
        self._clock_alignment: dict[str, object] | None = None
        self._published_frontier: datetime | None = None
        self._records_written = 1
        self._state_cursors = {
            episode.episode_id: 0 for episode in self._plan.episodes
        }
        self._scheduled_action_count = 0
        self._reconciliation_count = 0
        self._duplicate_reconciliation_count = 0
        self._reconciliation_keys: set[tuple[str, datetime | None]] = set()
        self._generators_stopped = 0
        self._zone_source_migrations = 0
        self._pending_plan = None
        self._logical_hold = False
        self._clock_origin_time = plan.coverage_start
        self._batch_count = 1
        self._segment_counts = {}
        self._generators_launched = 0

    def append_plan(self, plan: TrafficProbePlan) -> None:
        """Admit one next delivery of Traffic inputs; the worker adopts it in order."""
        with self._lock:
            if self._pending_plan is not None or plan.coverage_start < self._plan.coverage_end:
                raise ValueError("Traffic accepts one chronological successor")
            _validate_required_bindings(plan, {v.probe_id: v for v in plan.probe_identities}, self._bindings)
            self._pending_plan = plan

    def _adopt_pending(self):
        with self._lock:
            if self._pending_plan is None:
                return False
            self._plan, self._pending_plan = self._pending_plan, None
        self._identities = {value.probe_id: value for value in self._plan.probe_identities}
        self._armed_episodes.clear()
        self._completed_episodes.clear()
        self._reconciliation_keys.clear()
        self._all_generators.clear()
        self._segment_counts.clear()
        self._state_cursors = {episode.episode_id: 0 for episode in self._plan.episodes}
        self._batch_count += 1
        self._record("traffic_batch_adopted", plan_revision_id=self._plan.revision_id,
                     coverage_start=self._plan.coverage_start.isoformat(), coverage_end=self._plan.coverage_end.isoformat())
        return True

    def start(self, runner: Runner) -> None:
        if not isinstance(runner, Runner):
            raise TypeError("runner must be a Runner")
        if self._thread is not None:
            raise RuntimeError("Traffic probe executor is already started")
        self._runner = runner
        self._record(
            "executor_start",
            plan_revision_id=self._plan.revision_id,
            episode_semantics="revisable-cancelable-rearmable",
        )
        self._thread = Thread(
            target=self._run,
            name="skyseed-traffic-probe-executor",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> dict[str, object]:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            # The worker owns generator state and bounded control RPCs. Calling
            # its teardown concurrently races signals, stream reads and evidence
            # when Docker is slow. Let that owner finish before the final check.
            thread.join()
        self._terminate_all("executor-finalization")
        if not self._all_generators:
            self._selector.close()
        snapshot = self.snapshot()
        if self._failure is not None:
            raise RuntimeError(
                f"Traffic probe execution failed: {self._failure}"
            ) from self._failure
        if not self._executor_complete:
            raise RuntimeError("Traffic probe executor omitted completion evidence")
        return snapshot

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "plan_revision_id": self._plan.revision_id,
                "probe_identities": len(self._plan.probe_identities),
                "episodes": len(self._plan.episodes),
                "batch_count": self._batch_count,
                "accepts_plan": self._pending_plan is None,
                "logical_hold": self._logical_hold,
                "episodes_armed": len(self._armed_episodes),
                "episodes_completed": len(self._completed_episodes),
                "generators_launched": self._generators_launched,
                "generators_stopped": self._generators_stopped,
                "active_generators": len(self._active),
                "scheduling_actions": self._scheduled_action_count,
                "reconciliations": self._reconciliation_count,
                "duplicate_same_boundary_reconciliations": (
                    self._duplicate_reconciliation_count
                ),
                "zone_source_migrations": self._zone_source_migrations,
                "cleanup_complete": not self._active
                and all(value.cleanup_result == "cleaned" for value in self._all_generators),
                "clock_alignment": self._clock_alignment,
                "records_written": self._records_written,
                "complete": self._executor_complete and self._failure is None,
                "failure": (
                    None
                    if self._failure is None
                    else f"{type(self._failure).__name__}: {self._failure}"
                ),
            }

    def _run(self) -> None:
        try:
            runner_origin = self._await_runner_origin()
            actions = self._actions()
            self._scheduled_action_count = len(actions)
            next_action = 0
            while not self._stop.is_set():
                if next_action == len(actions):
                    if self._adopt_pending():
                        actions, next_action = self._actions(), 0
                        self._scheduled_action_count += len(actions)
                    else:
                        self._drain_io(0.05)
                        continue
                status = self._runner.status()
                if self._handle_logical_hold(status):
                    self._drain_io(0.05)
                    continue
                runner_origin = status["clock"]["wall_origin"]
                offset = actions[next_action][0]
                due = runner_origin + offset
                remaining = due - monotonic()
                if remaining > 0:
                    self._drain_io(min(0.05, remaining))
                    self._reap_unexpected()
                    continue
                same_time = []
                while next_action < len(actions) and actions[next_action][0] == offset:
                    same_time.append(actions[next_action])
                    next_action += 1
                frontier_actions = [
                    current_episode
                    for _, current_action, current_episode in same_time
                    if current_action == "frontier"
                ]
                if frontier_actions:
                    if len(frontier_actions) != 1 or frontier_actions[0] is None:
                        raise RuntimeError("Traffic scheduler duplicated a frontier")
                    required_frontier = frontier_actions[0]
                else:
                    required_frontier = self._clock_origin_time + timedelta(
                        seconds=offset
                    )
                # Ideal wall-clock offsets only make an action eligible.  The
                # corresponding Runner ACTIVE frontier remains semantic
                # authority, so slowdown cannot complete or arm an episode
                # against future live state.
                if not self._await_published_frontier(required_frontier):
                    break
                for _, current_action, current_episode in same_time:
                    if current_action == "complete":
                        assert current_episode is not None
                        self._complete_episode(current_episode)
                started = []
                for _, current_action, current_episode in same_time:
                    if current_action == "start":
                        assert current_episode is not None
                        self._start_episode(current_episode)
                        started.append(current_episode)
                if frontier_actions:
                    reconcile = [
                        episode
                        for episode in self._plan.episodes
                        if episode.episode_id in self._armed_episodes
                        and episode.episode_id not in self._completed_episodes
                        and (
                            episode.episode_id,
                            self._published_frontier,
                        )
                        not in self._reconciliation_keys
                    ]
                else:
                    reconcile = started
                for episode in reconcile:
                    # An earlier Docker launch/stop can span several ACTIVE
                    # boundaries. Choose this episode's source from live facts.
                    self._observe_published_frontier()
                    if (
                        episode.episode_id, self._published_frontier
                    ) not in self._reconciliation_keys:
                        self._reconcile_episode(episode)
                self._drain_io(0)
                self._reap_unexpected()
            self._cancel_remaining_episodes()
        except BaseException as error:
            self._remember_failure(error)
            self._try_record("executor_failure", error_type=type(error).__name__, error_message=str(error))
        finally:
            self._terminate_all("runner-run-ended")
            for episode in self._plan.episodes:
                if episode.episode_id not in self._armed_episodes:
                    self._try_record(
                        "episode_not_run",
                        episode_id=episode.episode_id,
                        probe_id=episode.probe_id,
                        reason="runner-ended-before-episode",
                        plan_revision_id=self._plan.revision_id,
                        rearmable=True,
                    )
            self._executor_complete = self._failure is None and not self._all_generators
            self._try_record("executor_end", **self.snapshot())
            if not self._all_generators:
                self._selector.close()

    def _remember_failure(self, error: BaseException) -> None:
        if self._failure is None:
            self._failure = error
        elif self._failure is not error:
            self._failure.add_note(f"Additional Traffic failure: {type(error).__name__}: {error}")
        self._stop.set()

    def _try_record(self, record_type: str, **facts: object) -> None:
        try:
            self._record(record_type, **facts)
        except Exception as error:
            self._remember_failure(error)

    def _await_runner_origin(self) -> float:
        assert self._runner is not None
        while not self._stop.is_set():
            event = self._runner.status()["clock"]
            if event["wall_origin"] is not None:
                if event.get("nominal_origin") != self._plan.coverage_start.isoformat():
                    raise RuntimeError("Traffic plan and Runner nominal origins differ")
                origin = event.get("wall_origin")
                if isinstance(origin, bool) or not isinstance(origin, (int, float)):
                    raise RuntimeError("Traffic execution Runner origin is invalid")
                observed = monotonic_ns()
                self._clock_alignment = {
                    "runner_wall_origin": float(origin),
                    "observed_monotonic_ns": observed,
                    "observation_delay_seconds": observed / 1_000_000_000 - float(origin),
                    "status": "PASS",
                }
                self._record("clock_alignment", **self._clock_alignment)
                return float(origin)
            self._stop.wait(0.005)
        return None

    def _actions(
        self,
    ) -> tuple[tuple[float, str, _TrafficProbeEpisode | datetime], ...]:
        result = []
        for boundary in self._plan.nominal_boundaries:
            result.append(
                (
                    (boundary - self._clock_origin_time).total_seconds(),
                    "frontier",
                    boundary,
                )
            )
        for episode in self._plan.episodes:
            start = (episode.start_time - self._clock_origin_time).total_seconds()
            end = (episode.end_time - self._clock_origin_time).total_seconds()
            result.append((start, "start", episode))
            result.append((end, "complete", episode))
        return tuple(
            sorted(
                result,
                key=lambda value: (
                    value[0],
                    {"complete": 0, "start": 1, "frontier": 2}[value[1]],
                    (
                        value[2].episode_id
                        if isinstance(value[2], _TrafficProbeEpisode)
                        else value[2].isoformat()
                    ),
                ),
            )
        )

    def _start_episode(self, episode: _TrafficProbeEpisode) -> None:
        if episode.episode_id in self._armed_episodes:
            raise RuntimeError("Traffic episode was armed more than once")
        self._armed_episodes.add(episode.episode_id)
        self._record(
            "episode_armed",
            episode_id=episode.episode_id,
            probe_id=episode.probe_id,
            mode=episode.mode,
            plan_revision_id=self._plan.revision_id,
        )
    def _reconcile_episode(self, episode: _TrafficProbeEpisode) -> None:
        if (episode.episode_id not in self._armed_episodes
                or episode.episode_id in self._completed_episodes
                or self._stop.is_set()):
            return
        identity = self._identities[episode.probe_id]
        migration_started = None
        previous_node = None
        while True:
            reconciliation_key = episode.episode_id, self._published_frontier
            if reconciliation_key in self._reconciliation_keys:
                self._duplicate_reconciliation_count += 1
                raise RuntimeError("Traffic episode reconciled twice at one frontier")
            self._reconciliation_keys.add(reconciliation_key)
            self._reconciliation_count += 1
            if (self._published_frontier is not None
                    and self._published_frontier >= episode.end_time):
                self._complete_episode(episode)
                return
            state = (
                None
                if self._published_frontier is None
                else self._state_for(episode, self._published_frontier)
            )
            desired_node = (
                identity.ground_station_id
                if state is not None
                and identity.direction == _DIRECTION_GROUND_TO_ZONE
                else None if state is None else state.service_satellite_id
            )
            active = self._active.get(episode.episode_id)
            if desired_node is None:
                if active is not None:
                    self._stop_generator(active, "expected-zone-unserved")
                self._record(
                    "episode_paused",
                    episode_id=episode.episode_id,
                    probe_id=episode.probe_id,
                    reason=(
                        "active-state-not-published"
                        if self._published_frontier is None
                        else "expected-zone-unserved"
                    ),
                    published_active_frontier=(
                        None
                        if self._published_frontier is None
                        else self._published_frontier.isoformat()
                    ),
                )
                return
            if active is not None and active.binding.node_id == desired_node:
                return
            if active is not None:
                migration_started = monotonic_ns()
                previous_node = active.binding.node_id
                self._stop_generator(active, "zone-source-migration")
                # Drainage can span ACTIVE boundaries or a lifecycle change.
                # Retire the old decision before choosing its replacement;
                # Runner remains the authority and continues independently.
                self._observe_published_frontier()
                if self._stop.is_set() or self._handle_logical_hold(self._runner.status()):
                    return
                if self._published_frontier != reconciliation_key[1]:
                    continue
            generator = self._launch_generator(episode, identity, desired_node)
            self._active[episode.episode_id] = generator
            if migration_started is not None and previous_node != desired_node:
                self._zone_source_migrations += 1
                self._record(
                    "zone_source_migration",
                    episode_id=episode.episode_id,
                    probe_id=episode.probe_id,
                    previous_source_node_id=previous_node,
                    current_source_node_id=desired_node,
                    migration_start_monotonic_ns=migration_started,
                    migration_end_monotonic_ns=monotonic_ns(),
                )
            return

    def _state_for(
        self, episode: _TrafficProbeEpisode, nominal_time: datetime
    ):
        intervals = episode.state_intervals
        cursor = self._state_cursors[episode.episode_id]
        while (
            cursor + 1 < len(intervals)
            and intervals[cursor + 1].start_time <= nominal_time
        ):
            cursor += 1
        self._state_cursors[episode.episode_id] = cursor
        state = intervals[cursor]
        if state.start_time <= nominal_time < state.end_time:
            return state
        return state if nominal_time >= intervals[-1].end_time else None

    def _complete_episode(self, episode: _TrafficProbeEpisode) -> None:
        if episode.episode_id in self._completed_episodes:
            return
        active = self._active.get(episode.episode_id)
        if active is not None:
            self._stop_generator(active, "planned-episode-end")
        self._completed_episodes.add(episode.episode_id)
        self._record(
            "episode_completed",
            episode_id=episode.episode_id,
            probe_id=episode.probe_id,
            reason="planned-episode-end",
            plan_revision_id=self._plan.revision_id,
            rearmable=False,
        )

    def _cancel_remaining_episodes(self) -> None:
        for episode in self._plan.episodes:
            if (
                episode.episode_id in self._armed_episodes
                and episode.episode_id not in self._completed_episodes
            ):
                self._record(
                    "episode_cancelled",
                    episode_id=episode.episode_id,
                    probe_id=episode.probe_id,
                    reason="runner-run-ended",
                    plan_revision_id=self._plan.revision_id,
                    rearmable=True,
                )

    def _launch_generator(
        self,
        episode: _TrafficProbeEpisode,
        identity: _TrafficProbeIdentity,
        source_node_id: str,
    ) -> _RunningGenerator:
        binding = self._bindings[source_node_id]
        segment_index = self._segment_counts.get(episode.episode_id, 0) + 1
        self._segment_counts[episode.episode_id] = segment_index
        runtime_identity = (
            f"skyseed-traffic-{self._run_token}-{episode.episode_id}-{segment_index}"
        )
        pid_file = (
            f"/tmp/skyseed-v2-traffic-{self._run_token}/"
            f"{episode.episode_id}-{segment_index}.pid"
        )
        source_ipv4 = (
            binding.runtime_ipv4
            if identity.direction == _DIRECTION_GROUND_TO_ZONE
            else identity.zone_ipv4
        )
        destination_ipv4 = (
            identity.zone_ipv4
            if identity.direction == _DIRECTION_GROUND_TO_ZONE
            else self._bindings[identity.ground_station_id].runtime_ipv4
        )
        identifier = 1 + int(
            sha256(runtime_identity.encode("utf-8")).hexdigest()[:4], 16
        ) % 65534
        command = (
            self._docker,
            "exec",
            binding.container_id,
            "env",
            f"SKYSEED_TRAFFIC_GENERATOR_ID={runtime_identity}",
            "sh",
            "-c",
            _PING_SCRIPT,
            "skyseed-traffic-generator",
            pid_file,
            source_ipv4,
            _seconds_argument(episode.interval_seconds),
            str(identifier),
            destination_ipv4,
        )
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert process.stdout is not None and process.stderr is not None
        generator = _RunningGenerator(
            episode,
            identity,
            binding,
            runtime_identity,
            pid_file,
            source_ipv4,
            destination_ipv4,
            identifier,
            segment_index,
            process,
            monotonic_ns(),
        )
        self._all_generators.append(generator)
        self._generators_launched += 1
        for stream_name, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
            os.set_blocking(stream.fileno(), False)
            self._selector.register(stream, selectors.EVENT_READ, (generator, stream_name))
            self._buffers[(id(generator), stream_name)] = b""
        self._record(
            "generator_launched",
            episode_id=episode.episode_id,
            probe_id=identity.probe_id,
            segment_index=segment_index,
            logical_direction=identity.direction,
            physical_source_node_id=binding.node_id,
            physical_source_container_id=binding.container_id,
            physical_source_container_name=binding.container_name,
            physical_source_pid=binding.pid,
            physical_source_netns_inode=binding.netns_inode,
            physical_source_started_at=binding.started_at,
            source_ipv4=source_ipv4,
            destination_ipv4=destination_ipv4,
            icmp_identifier=identifier,
            runtime_identity=runtime_identity,
            command=(
                "docker exec <container-id> ping -n -D -O -I <source-ip> "
                f"-i {_seconds_argument(episode.interval_seconds)} -W 1 "
                f"-e {identifier} <destination-ip>"
            ),
            launch_monotonic_ns=generator.launched_monotonic_ns,
            published_active_frontier=self._published_frontier.isoformat(),
        )
        try:
            self._wait_for_registration(generator)
        except BaseException:
            self._stop_generator(generator, "launch-failure")
            raise
        return generator

    def _await_published_frontier(self, required: datetime) -> None:
        while not self._stop.is_set():
            if self._handle_logical_hold(self._runner.status()):
                self._drain_io(0.01)
                continue
            observed = self._observe_published_frontier()
            if observed is not None and observed >= required:
                return True
            self._drain_io(0.01)
            self._reap_unexpected()
        return False

    def _handle_logical_hold(self, status):
        held = bool(status["paused"] or status["clock"]["held_since"] is not None)
        if held == self._logical_hold:
            return held
        self._logical_hold = held
        if held:
            for generator in tuple(self._active.values()):
                self._stop_generator(generator, "logical-clock-held")
            self._record("controlled_traffic_interrupted", active_frontier=status["active_frontier"],
                         reasons=status["wait_reasons"], in_flight_observations_retained=True)
        else:
            for episode in self._plan.episodes:
                if episode.episode_id in self._armed_episodes and episode.episode_id not in self._completed_episodes:
                    self._observe_published_frontier()
                    self._reconciliation_keys.discard((episode.episode_id, self._published_frontier))
                    self._reconcile_episode(episode)
            self._record("controlled_traffic_rearmed", active_frontier=status["active_frontier"],
                         clock=status["clock"], catch_up=False)
        return held

    def _observe_published_frontier(self) -> datetime | None:
        assert self._runner is not None
        raw = self._runner.status()["active_frontier"]
        if raw is None:
            return None
        if not isinstance(raw, str):
            raise RuntimeError("Traffic execution Runner active frontier is invalid")
        try:
            observed = datetime.fromisoformat(raw)
        except ValueError as error:
            raise RuntimeError(
                "Traffic execution Runner active frontier is invalid"
            ) from error
        if observed.tzinfo is None:
            raise RuntimeError("Traffic execution Runner active frontier is naive")
        if (
            self._published_frontier is not None
            and observed < self._published_frontier
        ):
            raise RuntimeError("Traffic execution Runner active frontier regressed")
        if observed != self._published_frontier:
            previous = self._published_frontier
            self._published_frontier = observed
            self._record(
                "published_frontier_observed",
                previous_active_frontier=(
                    None if previous is None else previous.isoformat()
                ),
                active_frontier=observed.isoformat(),
            )
        return observed

    def _wait_for_registration(self, generator: _RunningGenerator) -> None:
        while True:
            state = self._generator_state(generator)
            if state == "active":
                return
            if state in {
                "identity-mismatch",
                "identity-duplicate",
                "invalid-pid",
            }:
                raise RuntimeError(
                    f"{generator.episode.episode_id} registration is {state}"
                )
            code = generator.process.poll()
            if code is not None:
                self._drain_io(0)
                raise RuntimeError(
                    f"{generator.episode.episode_id} exited before registration: {code}"
                )
            self._drain_io(0.01)

    def _stop_generator(self, generator: _RunningGenerator, reason: str) -> None:
        if generator.cleanup_result == "cleaned":
            self._release_generator(generator)
            return
        state = self._stable_generator_state(generator)
        while state == "missing" and generator.process.poll() is None:
            self._drain_io(0.02)
            state = self._stable_generator_state(generator)
        if state == "active":
            generator.stop_signal = "TERM"
            self._signal_generator(generator, "term")
        elif state in {
            "identity-mismatch",
            "identity-duplicate",
            "invalid-pid",
        }:
            self._generator_control(generator, "discard", expected="discarded")
            raise RuntimeError(
                f"{generator.episode.episode_id} stop refused {state} identity"
            )
        while generator.process.poll() is None:
            self._drain_io(0.02)
        generator.process.wait()
        self._verify_stopped(generator)
        # The remote process is confirmed stopped. Evidence can no longer
        # prevent release of this executor's local handles and registries.
        try:
            self._drain_generator_io(generator)
            self._record_generator_stopped(generator, reason)
        except BaseException as error:
            self._remember_failure(error)
            raise
        finally:
            self._release_generator(generator)

    def _record_generator_stopped(self, generator: _RunningGenerator, reason: str) -> None:
        sent = (
            generator.summary_sent
            if generator.summary_sent is not None
            else max(generator.observed_sequences, default=0)
        )
        received = (
            generator.summary_received
            if generator.summary_received is not None
            else len(generator.reply_sequences)
        )
        self._record(
            "generator_stopped",
            episode_id=generator.episode.episode_id,
            probe_id=generator.identity.probe_id,
            segment_index=generator.segment_index,
            physical_source_node_id=generator.binding.node_id,
            stop_reason=reason,
            stop_signal=generator.stop_signal,
            stop_monotonic_ns=monotonic_ns(),
            exit_status=generator.process.returncode,
            sent=sent,
            received=received,
            loss=max(0, sent - received),
            timeout_sequences=sorted(generator.timeout_sequences),
            reply_sequences=sorted(generator.reply_sequences),
            rtt_ms=generator.rtt_ms,
            cleanup_result=generator.cleanup_result,
        )

    def _release_generator(self, generator: _RunningGenerator) -> None:
        self._active.pop(generator.episode.episode_id, None)
        if generator in self._all_generators:
            self._all_generators.remove(generator)
            self._generators_stopped += 1
        errors = []
        for name in ("stdout", "stderr"):
            self._buffers.pop((id(generator), name), None)
            stream = getattr(generator.process, name)
            if stream is None:
                continue
            try:
                self._selector.unregister(stream)
            except KeyError:
                pass
            except Exception as error:
                errors.append(error)
            try:
                stream.close()
            except Exception as error:
                errors.append(error)
        for error in errors:
            self._remember_failure(error)

    def _verify_stopped(self, generator: _RunningGenerator) -> None:
        state = self._stable_generator_state(generator)
        while state == "active":
            sleep(0.02)
            state = self._stable_generator_state(generator)
        if state in {
            "active",
            "identity-mismatch",
            "identity-duplicate",
            "invalid-pid",
        }:
            self._generator_control(generator, "discard", expected="discarded")
            raise RuntimeError(
                f"{generator.episode.episode_id} remains {state} after stop"
            )
        self._generator_control(generator, "cleanup", expected="cleaned")
        generator.cleanup_result = "cleaned"

    def _terminate_all(self, reason: str) -> None:
        for generator in tuple(self._all_generators):
            try:
                self._stop_generator(generator, reason)
            except BaseException as error:
                self._remember_failure(error)

    def _reap_unexpected(self) -> None:
        for generator in tuple(self._active.values()):
            code = generator.process.poll()
            if code is None:
                continue
            error = RuntimeError(
                f"Traffic generator exited unexpectedly: {generator.episode.episode_id}, status={code}"
            )
            try:
                self._stop_generator(generator, "unexpected-exit")
            except BaseException as cleanup_error:
                error.add_note(f"Generator cleanup: {type(cleanup_error).__name__}: {cleanup_error}")
            raise error

    def _drain_io(self, timeout: float) -> None:
        for key, _ in self._selector.select(timeout=max(0.0, timeout)):
            stream = key.fileobj
            generator, stream_name = key.data
            assert hasattr(stream, "fileno")
            try:
                chunk = os.read(stream.fileno(), 65536)
            except BlockingIOError:
                continue
            if not chunk:
                try:
                    self._selector.unregister(stream)
                except KeyError:
                    pass
                try:
                    self._flush_buffer(generator, stream_name)
                except Exception as error:
                    self._remember_failure(error)
                continue
            buffer_key = id(generator), stream_name
            pending = self._buffers.get(buffer_key, b"") + chunk
            lines = pending.split(b"\n")
            self._buffers[buffer_key] = lines.pop()
            for line in lines:
                try:
                    self._record_ping_line(
                        generator, stream_name, line.decode("utf-8", errors="replace")
                    )
                except Exception as error:
                    self._remember_failure(error)

    def _drain_generator_io(self, generator: _RunningGenerator) -> None:
        while True:
            before = sum(len(value) for value in self._buffers.values())
            self._drain_io(0.01)
            streams_registered = any(
                key.data[0] is generator
                for key in self._selector.get_map().values()
            )
            after = sum(len(value) for value in self._buffers.values())
            if not streams_registered and before == after:
                break
        for stream_name in ("stdout", "stderr"):
            self._flush_buffer(generator, stream_name)

    def _flush_buffer(self, generator: _RunningGenerator, stream_name: str) -> None:
        key = id(generator), stream_name
        value = self._buffers.pop(key, b"")
        if value:
            self._record_ping_line(
                generator, stream_name, value.decode("utf-8", errors="replace")
            )

    def _record_ping_line(
        self, generator: _RunningGenerator, stream_name: str, line: str
    ) -> None:
        value = line.rstrip("\r")
        sequence_match = _SEQUENCE.search(value)
        sequence = None if sequence_match is None else int(sequence_match.group("value"))
        rtt_match = _RTT.search(value)
        rtt = None if rtt_match is None else float(rtt_match.group("value"))
        summary_match = _SUMMARY.search(value)
        observation = "stderr" if stream_name == "stderr" else "informational"
        if "bytes from" in value and sequence is not None:
            observation = "reply"
            generator.reply_sequences.add(sequence)
        elif "no answer yet" in value and sequence is not None:
            observation = "timeout"
            generator.timeout_sequences.add(sequence)
        elif summary_match is not None:
            observation = "summary"
            generator.summary_sent = int(summary_match.group("sent"))
            generator.summary_received = int(summary_match.group("received"))
            generator.summary_loss_percent = float(summary_match.group("loss"))
        elif "Destination" in value and sequence is not None:
            observation = "network-error"
        if sequence is not None:
            generator.observed_sequences.add(sequence)
        if rtt is not None:
            generator.rtt_ms.append(rtt)
        self._record(
            "ping_observation",
            episode_id=generator.episode.episode_id,
            probe_id=generator.identity.probe_id,
            segment_index=generator.segment_index,
            stream=stream_name,
            observation=observation,
            icmp_identifier=generator.icmp_identifier,
            icmp_sequence=sequence,
            rtt_ms=rtt,
            raw_line=value,
            published_active_frontier=(
                None
                if self._published_frontier is None
                else self._published_frontier.isoformat()
            ),
        )

    def _stable_generator_state(self, generator: _RunningGenerator) -> str:
        state = self._generator_state(generator)
        if state != "identity-mismatch":
            return state
        sleep(0.02)
        return self._generator_state(generator)

    def _generator_state(self, generator: _RunningGenerator) -> str:
        return self._generator_control(
            generator,
            "status",
            expected={
                "missing",
                "stale",
                "active",
                "identity-mismatch",
                "identity-duplicate",
                "invalid-pid",
            },
        )

    def _signal_generator(self, generator: _RunningGenerator, action: str) -> None:
        self._generator_control(
            generator,
            action,
            expected={"signalled", "missing", "stale"},
        )

    def _generator_control(
        self,
        generator: _RunningGenerator,
        action: str,
        *,
        expected: str | set[str],
    ) -> str:
        command = (
            self._docker, "exec", generator.binding.container_id,
            "sh", "-c", _CONTROL_SCRIPT, "skyseed-traffic-generator-control",
            action, generator.pid_file, generator.runtime_identity,
        )
        # The worker owns this exact operation and identity. Completion or an
        # actual Docker/identity error decides its outcome, never elapsed time.
        result = subprocess.run(command, check=False, capture_output=True, text=True)
        value = result.stdout.strip()
        allowed = {expected} if isinstance(expected, str) else expected
        if result.returncode != 0 or value not in allowed:
            raise RuntimeError(
                f"{generator.episode.episode_id} generator {action} failed: "
                f"code={result.returncode}, state={value or 'none'}, "
                f"stderr={result.stderr.strip()}"
            )
        return value

    def _record(self, record_type: str, **facts: object) -> None:
        _append_jsonl(
            self._path,
            {
                "record_type": record_type,
                "host_monotonic_ns": monotonic_ns(),
                "wall_time": datetime.now(timezone.utc).isoformat(),
                **facts,
            },
        )
        with self._lock:
            self._records_written += 1


def _runtime_bindings(snapshot: Mapping[str, object]) -> dict[str, _TrafficRuntimeBinding]:
    runner = snapshot.get("runner")
    if not isinstance(runner, Mapping):
        raise ValueError("Traffic execution lacks Runner snapshot")
    foundation = runner.get("runtime_foundation")
    if not isinstance(foundation, Mapping):
        raise ValueError("Traffic execution lacks Runner runtime foundation")
    raw = foundation.get("bindings")
    if not isinstance(raw, (tuple, list)) or not raw:
        raise ValueError("Traffic execution lacks frozen runtime bindings")
    result = {}
    for item in raw:
        if not isinstance(item, Mapping):
            raise ValueError("Traffic runtime binding is invalid")
        binding = _TrafficRuntimeBinding(
            _string(item.get("node_id"), "binding node_id"),
            _string(item.get("container_id"), "binding container_id"),
            _string(item.get("container_name"), "binding container_name"),
            _positive_int(item.get("pid"), "binding pid"),
            _positive_int(item.get("netns_inode"), "binding netns_inode"),
            _string(item.get("started_at"), "binding started_at"),
            _string(item.get("runtime_ipv4"), "binding runtime_ipv4"),
        )
        if binding.node_id in result:
            raise ValueError("Traffic runtime binding identity is duplicated")
        result[binding.node_id] = binding
    if foundation.get("bound_node_count") != len(result):
        raise ValueError("Traffic runtime binding count differs")
    return result


def _validate_required_bindings(
    plan: TrafficProbePlan,
    identities: Mapping[str, _TrafficProbeIdentity],
    bindings: Mapping[str, _TrafficRuntimeBinding],
) -> None:
    required = {
        identity.ground_station_id for identity in plan.probe_identities
    }
    required.update(
        interval.service_satellite_id
        for episode in plan.episodes
        for interval in episode.state_intervals
        if interval.service_satellite_id is not None
    )
    missing = sorted(required - bindings.keys())
    if missing:
        raise ValueError(
            "Traffic execution lacks required Runner bindings: " + ", ".join(missing)
        )
    if set(identities) != {value.probe_id for value in plan.probe_identities}:
        raise ValueError("Traffic execution probe identities differ")


def _seconds_argument(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive int")
    return value


__all__: list[str] = []
