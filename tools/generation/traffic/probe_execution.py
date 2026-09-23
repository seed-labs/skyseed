"""Execute persistent Traffic communications around one Runner invocation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import os
from pathlib import Path
import re
import selectors
import subprocess
from threading import Event, RLock, Thread
from time import monotonic, monotonic_ns, sleep
from typing import BinaryIO
from uuid import uuid4

from skyseed.backend.runner import Runner
from skyseed.shared._durable_evidence import _append_jsonl, _reset_jsonl

from .workload import TrafficWorkload, Communication

_EXECUTION_ARTIFACT = "traffic-probe-execution"
_FORMAT_VERSION = 3
_SEQUENCE = re.compile(r"icmp_seq[= ](?P<value>[0-9]+)")
_RTT = re.compile(r"time[=<](?P<value>[0-9]+(?:\.[0-9]+)?)\s*ms")
_SUMMARY = re.compile(
    r"(?P<sent>[0-9]+) packets transmitted,\s*"
    r"(?P<received>[0-9]+) received,\s*(?:\+[0-9]+ (?:duplicates|errors),\s*)*"
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
    term)
        if [ "$state" = active ]; then
            if kill -TERM "$pid" 2>/dev/null; then
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
    communication: Communication
    binding: _TrafficRuntimeBinding
    runtime_identity: str
    pid_file: str
    source_ipv4: str
    destination_ipv4: str
    icmp_identifier: int
    segment_index: int
    process: subprocess.Popen[bytes]
    launched_monotonic_ns: int
    observed_replies: int = 0
    observed_duplicates: int = 0
    observed_timeouts: int = 0
    rtt_count: int = 0
    rtt_mean_ms: float = 0.
    rtt_min_ms: float | None = None
    rtt_max_ms: float | None = None
    summary_sent: int | None = None
    summary_received: int | None = None
    summary_loss_percent: float | None = None
    stop_signal: str | None = None
    cleanup_result: str | None = None


class TrafficExecutor:
    """Own persistent senders for fixed communications, using ACTIVE Runner facts."""
    def __init__(self, *, workload, evidence_path, docker_executable='docker'):
        if not isinstance(workload, TrafficWorkload):
            raise TypeError('workload must be TrafficWorkload')
        self._workload, self._docker = workload, docker_executable
        self._path = _reset_jsonl(evidence_path, {'record_type': 'execution_header',
            'artifact_type': _EXECUTION_ARTIFACT, 'format_version': _FORMAT_VERSION,
            'workload': workload.artifact(), 'hop_evidence': 'separate BPF telemetry, when available'})
        self._stop, self._done, self._lock = Event(), Event(), RLock()
        self._selector = selectors.DefaultSelector()
        self._buffers, self._active, self._segment_counts = {}, {}, {}
        self._all_generators = []
        self._failure = None
        self._runner = self._thread = None
        self._records_written = 1
        self._generators_stopped = self._generators_launched = 0
        self._published_frontier = None
        self._run_token = uuid4().hex
        self._no_source = set()

    def start(self, runner):
        if self._thread is not None:
            raise RuntimeError('Traffic already started')
        self._runner = runner
        self._thread = Thread(target=self._run, name='skyseed-fixed-traffic')
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._done.wait()
            self._thread.join()
        if self._failure is not None:
            raise self._failure
        return self.snapshot()

    def snapshot(self):
        return {'communications': len(self._workload.communications), 'senders': len(self._active),
                'records': self._records_written, 'complete': self._done.is_set(),
                'error': None if self._failure is None else str(self._failure)}

    def _remember_failure(self, error):
        if self._failure is None:
            self._failure = error
        elif error is not self._failure:
            self._failure.add_note(f'Traffic cleanup: {type(error).__name__}: {error}')

    def _run(self):
        try:
            origin = None
            while not self._stop.is_set():
                status = self._runner.status()
                if status.get('cancel_requested'):
                    break
                clock = status.get('clock') or {}
                if origin is None:
                    origin = clock.get('wall_origin')
                if origin is None:
                    self._stop.wait(.02)
                    continue
                # Intended duration is application wall duration: topology pause
                # does not suspend clocks or kill a still-valid sender.
                if monotonic() - origin >= self._workload.duration_seconds:
                    break
                for communication in self._workload.communications:
                    self._reconcile(communication)
                self._reap_unexpected()
                self._drain_io(.03)
        except BaseException as error:
            self._remember_failure(error)
        finally:
            self._terminate_all('runtime-stop' if self._stop.is_set() else 'duration-or-runtime-end')
            self._selector.close()
            self._done.set()

    def _reconcile(self, communication):
        while not self._stop.is_set():
            facts = self._runner.communication_endpoints(communication.ground_station_id,
                communication.zone_id, communication.network_scope)
            frontier = facts.get('active_frontier')
            self._published_frontier = None if frontier is None else datetime.fromisoformat(frontier)
            source = facts.get('ground') if communication.direction == 'ground-to-zone' else facts.get('service')
            if not facts.get('available') or self._runner.status().get('cancel_requested'):
                source = None
            active = self._active.get(communication.communication_id)
            if active is not None and source is not None and active.binding.node_id == source['node_id']:
                return
            if active is not None:
                previous = active.binding.node_id
                self._stop_generator(active, 'source-migration' if source else 'source-unavailable')
                self._record('source_migration', communication_id=communication.communication_id,
                             previous_source_node_id=previous, current_source_node_id=None if source is None else source['node_id'])
                # Cleanup may span activations. Discard the old decision and ask
                # the owner again before launching a replacement.
                continue
            if source is None:
                if communication.communication_id not in self._no_source:
                    self._record('source_unavailable', communication_id=communication.communication_id,
                                 reason=facts.get('reason', 'Zone has no active service source'))
                    self._no_source.add(communication.communication_id)
                return
            self._no_source.discard(communication.communication_id)
            generator = self._launch_generator(communication, facts, source)
            self._active[communication.communication_id] = generator
            return

    def _launch_generator(self, communication, facts, source):
        binding = _TrafficRuntimeBinding(**source)
        segment_index = self._segment_counts.get(communication.communication_id, 0) + 1
        self._segment_counts[communication.communication_id] = segment_index
        runtime_identity = (
            f"skyseed-traffic-{self._run_token}-{communication.communication_id}-{segment_index}"
        )
        pid_file = (
            f"/tmp/skyseed-v2-traffic-{self._run_token}/"
            f"{communication.communication_id}-{segment_index}.pid"
        )
        source_ipv4 = (
            binding.runtime_ipv4
            if communication.direction == 'ground-to-zone'
            else facts['zone_ipv4']
        )
        destination_ipv4 = (
            facts['zone_ipv4']
            if communication.direction == 'ground-to-zone'
            else facts['ground']['runtime_ipv4']
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
            _seconds_argument(self._workload.interval_seconds),
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
            communication,
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
            communication_id=communication.communication_id,
            segment_index=segment_index,
            logical_direction=communication.direction,
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
                f"-i {_seconds_argument(self._workload.interval_seconds)} -W 1 "
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
                    f"{generator.communication.communication_id} registration is {state}"
                )
            code = generator.process.poll()
            if code is not None:
                self._drain_io(0)
                raise RuntimeError(
                    f"{generator.communication.communication_id} exited before registration: {code}"
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
                f"{generator.communication.communication_id} stop refused {state} identity"
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
            else None
        )
        # Only ping's terminal summary establishes unique received/sent totals.
        # Partial stdout is evidence of observations, not a complete packet count.
        received = generator.summary_received
        self._record(
            "generator_stopped",
            communication_id=generator.communication.communication_id,
            segment_index=generator.segment_index,
            physical_source_node_id=generator.binding.node_id,
            stop_reason=reason,
            stop_signal=generator.stop_signal,
            stop_monotonic_ns=monotonic_ns(),
            exit_status=generator.process.returncode,
            sent=sent,
            received=received,
            loss=None if sent is None or received is None else max(0, sent - received),
            observed_replies=generator.observed_replies,
            observed_duplicates=generator.observed_duplicates,
            observed_timeouts=generator.observed_timeouts,
            rtt_summary={'count': generator.rtt_count, 'min_ms': generator.rtt_min_ms,
                         'max_ms': generator.rtt_max_ms,
                         'mean_ms': generator.rtt_mean_ms if generator.rtt_count else None},
            cleanup_result=generator.cleanup_result,
        )

    def _release_generator(self, generator: _RunningGenerator) -> None:
        self._active.pop(generator.communication.communication_id, None)
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
                f"{generator.communication.communication_id} remains {state} after stop"
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
                f"Traffic generator exited unexpectedly: {generator.communication.communication_id}, status={code}"
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
            generator.observed_replies += 1
            generator.observed_duplicates += int("DUP!" in value)
        elif "no answer yet" in value and sequence is not None:
            observation = "timeout"
            generator.observed_timeouts += 1
        elif summary_match is not None:
            observation = "summary"
            generator.summary_sent = int(summary_match.group("sent"))
            generator.summary_received = int(summary_match.group("received"))
            generator.summary_loss_percent = float(summary_match.group("loss"))
        elif "Destination" in value and sequence is not None:
            observation = "network-error"
        if rtt is not None:
            generator.rtt_count += 1
            generator.rtt_mean_ms += (rtt - generator.rtt_mean_ms) / generator.rtt_count
            generator.rtt_min_ms = rtt if generator.rtt_min_ms is None else min(generator.rtt_min_ms, rtt)
            generator.rtt_max_ms = rtt if generator.rtt_max_ms is None else max(generator.rtt_max_ms, rtt)
        self._record(
            "ping_observation",
            communication_id=generator.communication.communication_id,
            segment_index=generator.segment_index,
            stream=stream_name,
            observation=observation,
            duplicate=observation == "reply" and "DUP!" in value,
            icmp_identifier=generator.icmp_identifier,
            icmp_sequence=sequence,
            rtt_ms=rtt,
            raw_line=value,
            packet_id=None if sequence is None else f"{generator.runtime_identity}:{sequence}",
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
                f"{generator.communication.communication_id} generator {action} failed: "
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
                "published_active_frontier": (None if self._published_frontier is None
                                               else self._published_frontier.isoformat()),
                **facts,
            },
        )
        with self._lock:
            self._records_written += 1



def _seconds_argument(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")

__all__: list[str] = []
