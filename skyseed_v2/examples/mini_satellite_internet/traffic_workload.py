"""Define and run the canonical Mini's example-local traffic workload."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from ipaddress import IPv4Interface, ip_interface
import json
from math import ceil
from pathlib import Path
import subprocess
from threading import Event, RLock, Thread
from time import monotonic, sleep
from typing import Iterable, Mapping
from uuid import uuid4

from skyseed_v2.backend.bpf import BpfCompiledRealization, BpfRunner
from skyseed_v2.simulator import Plan, plan_transition_fingerprint

_GROUND_STATIONS = {
    "Marsala": "65010/ground-station/gs-marsala-reference",
    "Porto Alegre": "65010/ground-station/gs-porto-alegre-reference",
    "Yamaguchi": "65010/ground-station/gs-yamaguchi-reference",
}
_MAXIMUM_WORKLOAD_CLOCK_SKEW_SECONDS = 0.050


@dataclass(frozen=True, slots=True)
class _SeriesSpec:
    series_id: str
    label: str
    direction: str
    ground_station_name: str
    zone_id: str
    start_offset_ms: int
    end_offset_ms: int
    interval_ms: int
    phase_ms: int
    purpose: str
    event_focus: str

    @property
    def scheduled_slots(self) -> int:
        duration = self.end_offset_ms - self.start_offset_ms
        if duration <= 0:
            raise AssertionError("canonical traffic series has an invalid cadence")
        return ceil(duration / self.interval_ms)


# These offsets are the reviewed absolute schedule minus the canonical 00:00:30
# simulation origin.  Milliseconds keep the sub-second phases exact.
_SERIES = (
    _SeriesSpec(
        "S1",
        "Bootstrap baseline",
        "ground-station-to-zone",
        "Porto Alegre",
        "western-europe",
        10_250,
        250_250,
        3_000,
        250,
        "long-lived bootstrap background traffic",
        "stable western-europe service after bootstrap",
    ),
    _SeriesSpec(
        "S2",
        "East Asia lifecycle",
        "zone-to-ground-station",
        "Marsala",
        "east-asia",
        85_750,
        590_750,
        3_000,
        750,
        "long-lived Zone-source background traffic",
        "east-asia ADD and REMOVE",
    ),
    _SeriesSpec(
        "S3",
        "North America remove",
        "ground-station-to-zone",
        "Porto Alegre",
        "north-america-east",
        90_400,
        330_400,
        2_000,
        400,
        "sustained traffic across service withdrawal",
        "north-america-east REMOVE",
    ),
    _SeriesSpec(
        "S4",
        "Western Europe remove",
        "ground-station-to-zone",
        "Yamaguchi",
        "western-europe",
        230_100,
        470_100,
        2_000,
        100,
        "sustained traffic across service withdrawal",
        "western-europe REMOVE",
    ),
    _SeriesSpec(
        "S5",
        "Northeast re-add",
        "ground-station-to-zone",
        "Porto Alegre",
        "northeast-priority",
        330_600,
        570_600,
        2_000,
        600,
        "sustained traffic across service restoration",
        "northeast-priority re-ADD and path changes",
    ),
    _SeriesSpec(
        "S6",
        "Post-re-add background",
        "zone-to-ground-station",
        "Yamaguchi",
        "northeast-priority",
        460_900,
        580_900,
        3_000,
        900,
        "Zone-source background traffic after restoration",
        "post-re-ADD path change and Ground Station access withdrawal",
    ),
    _SeriesSpec(
        "S7",
        "Handover focus",
        "ground-station-to-zone",
        "Marsala",
        "north-america-east",
        525_200,
        615_200,
        1_000,
        200,
        "one uninterrupted Ground Station ping process across handover",
        "north-america-east service-satellite handover",
    ),
)

_STATIC_EXPECTATION = {
    "status": "pre-runtime-estimate-only",
    "paired_transactions": 534,
    "scheduled_slots_without_tc_bpf_observation": 205,
    "packet_events": 4_430,
}


def planned_zone_service_segments(
    plans: Iterable[Plan],
    realizations: Iterable[BpfCompiledRealization],
    *,
    simulation_start: datetime,
    simulation_end: datetime,
) -> tuple[dict[str, object], ...]:
    """Project compiled nominal Zone service into workload-owned segments."""

    events = []
    plan_values = tuple(plans)
    realization_values = tuple(realizations)
    if len(plan_values) != len(realization_values):
        raise ValueError("Plans and realizations must be aligned")
    for plan, realization in zip(plan_values, realization_values):
        if not isinstance(realization, BpfCompiledRealization):
            raise TypeError("realizations must contain BpfCompiledRealization")
        if plan.plan_id != realization.source_plan_id:
            raise ValueError("Plan and realization lineage differs")
        for transition, boundary in zip(
            plan.transitions, realization.compiled_activation_boundaries
        ):
            if plan_transition_fingerprint(transition) != (
                boundary.transition_fingerprint
            ):
                raise ValueError("Plan transition provenance differs")
            for change in transition.zone_service_transitions:
                events.append(
                    (
                        transition.timestamp,
                        change.zone_id,
                        None
                        if change.previous_satellite_id is None
                        else change.previous_satellite_id.value,
                        None
                        if change.current_satellite_id is None
                        else change.current_satellite_id.value,
                    )
                )
    events.sort(key=lambda value: (value[0], value[1]))
    active: dict[str, tuple[str, datetime]] = {}
    segments = []
    for timestamp, zone_id, previous, current in events:
        if timestamp < simulation_start or timestamp > simulation_end:
            raise ValueError("compiled Zone service event is outside the simulation")
        observed_previous = active.get(zone_id)
        observed_service = None if observed_previous is None else observed_previous[0]
        if observed_service != previous:
            raise ValueError("compiled Zone service progression is discontinuous")
        if observed_previous is not None and timestamp > observed_previous[1]:
            segments.append(
                {
                    "zone_id": zone_id,
                    "service_satellite_id": observed_previous[0],
                    "start_time": observed_previous[1],
                    "end_time": timestamp,
                }
            )
        if current is None:
            active.pop(zone_id, None)
        else:
            active[zone_id] = (current, timestamp)
    for zone_id, (service, start) in sorted(active.items()):
        if start < simulation_end:
            segments.append(
                {
                    "zone_id": zone_id,
                    "service_satellite_id": service,
                    "start_time": start,
                    "end_time": simulation_end,
                }
            )
    return _normalize_zone_service_segments(
        segments,
        simulation_start=simulation_start,
        simulation_end=simulation_end,
    )


def _normalize_zone_service_segments(
    values: Iterable[Mapping[str, object]],
    *,
    simulation_start: datetime,
    simulation_end: datetime,
) -> tuple[dict[str, object], ...]:
    result = []
    previous_end: dict[str, datetime] = {}
    raw_values = tuple(values)
    if any(not isinstance(value, Mapping) for value in raw_values):
        raise TypeError("Zone service segments must be mappings")
    sorted_values = sorted(
        raw_values,
        key=lambda value: (
            str(value.get("zone_id", "")),
            str(value.get("start_time", "")),
        ),
    )
    for value in sorted_values:
        zone_id = str(value.get("zone_id", ""))
        service = str(value.get("service_satellite_id", ""))
        raw_start = value.get("start_time")
        raw_end = value.get("end_time")
        start = (
            raw_start
            if isinstance(raw_start, datetime)
            else datetime.fromisoformat(str(raw_start))
        )
        end = (
            raw_end
            if isinstance(raw_end, datetime)
            else datetime.fromisoformat(str(raw_end))
        )
        if not zone_id or not service or not (simulation_start <= start < end <= simulation_end):
            raise ValueError("canonical Zone service segment is invalid")
        if start < previous_end.get(zone_id, simulation_start):
            raise ValueError("canonical Zone service segments overlap")
        previous_end[zone_id] = end
        result.append(
            {
                "zone_id": zone_id,
                "service_satellite_id": service,
                "start_time": start.isoformat(),
                "end_time": end.isoformat(),
                "start_offset_seconds": (
                    start - simulation_start
                ).total_seconds(),
                "end_offset_seconds": (
                    end - simulation_start
                ).total_seconds(),
            }
        )
    return tuple(result)


def _workload_clock_alignment(
    snapshot: dict[str, object], workload_origin: float
) -> dict[str, object]:
    events = snapshot.get("events")
    if not isinstance(events, (tuple, list)):
        raise RuntimeError("Runner inspection omitted automatic-run evidence")
    starts = [
        value
        for value in events
        if isinstance(value, dict) and value.get("event") == "automatic_run_start"
    ]
    if len(starts) != 1:
        raise RuntimeError(
            "canonical workload requires exactly one automatic-run clock anchor"
        )
    runner_origin = starts[0].get("wall_origin")
    if isinstance(runner_origin, bool) or not isinstance(runner_origin, (int, float)):
        raise RuntimeError("automatic-run clock anchor is invalid")
    skew_seconds = float(runner_origin) - workload_origin
    skew_nanoseconds = round(skew_seconds * 1_000_000_000)
    maximum_skew_nanoseconds = round(
        _MAXIMUM_WORKLOAD_CLOCK_SKEW_SECONDS * 1_000_000_000
    )
    return {
        "workload_monotonic_origin": workload_origin,
        "runner_wall_origin": float(runner_origin),
        "signed_skew_seconds": skew_seconds,
        "absolute_skew_seconds": abs(skew_nanoseconds) / 1_000_000_000,
        "maximum_skew_seconds": _MAXIMUM_WORKLOAD_CLOCK_SKEW_SECONDS,
        "status": (
            "PASS"
            if abs(skew_nanoseconds) < maximum_skew_nanoseconds
            else "FAIL"
        ),
        "scope": "planned-schedule-clock-alignment-only",
        "packet_timing_authority": "tc-BPF traffic telemetry",
    }


def _await_workload_clock_alignment(
    runner: BpfRunner,
    workload_origin: float,
    stop: Event,
) -> dict[str, object]:
    deadline = monotonic() + 1.0
    while True:
        snapshot = runner.inspection_snapshot()
        if any(
            isinstance(value, dict)
            and value.get("event") == "automatic_run_start"
            for value in snapshot.get("events", ())
        ):
            return _workload_clock_alignment(snapshot, workload_origin)
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise RuntimeError(
                "canonical workload could not observe the Runner clock anchor"
            )
        if stop.wait(min(0.005, remaining)):
            raise RuntimeError(
                "canonical workload stopped before Runner clock alignment"
            )


def build_canonical_traffic_workload(
    *,
    simulation_start: datetime,
    simulation_end: datetime,
    zone_prefixes: Mapping[str, str],
    zone_service_segments: Iterable[Mapping[str, object]] = (),
) -> dict[str, object]:
    """Build detached planned-workload facts for the canonical example."""

    if simulation_start.tzinfo is None or simulation_start.utcoffset() is None:
        raise ValueError("simulation_start must be timezone-aware")
    if simulation_end.tzinfo is None or simulation_end.utcoffset() is None:
        raise ValueError("simulation_end must be timezone-aware")
    duration_ms = round((simulation_end - simulation_start).total_seconds() * 1_000)
    if duration_ms != 630_000:
        raise ValueError("canonical traffic workload requires the 10m30s Mini timeline")

    packet_addresses = {
        zone_id: _zone_packet_ipv4(prefix)
        for zone_id, prefix in zone_prefixes.items()
    }
    required_zones = {value.zone_id for value in _SERIES}
    if not required_zones <= packet_addresses.keys():
        missing = sorted(required_zones - packet_addresses.keys())
        raise ValueError(f"canonical traffic workload lacks Zone prefixes: {missing}")

    planned_zone_segments = _normalize_zone_service_segments(
        zone_service_segments,
        simulation_start=simulation_start,
        simulation_end=simulation_end,
    )
    series = []
    for spec in _SERIES:
        start = simulation_start + timedelta(milliseconds=spec.start_offset_ms)
        end = simulation_start + timedelta(milliseconds=spec.end_offset_ms)
        if end > simulation_end:
            raise ValueError("canonical traffic series falls outside the simulation")
        series.append(
            {
                "series_id": spec.series_id,
                "display_label": spec.label,
                "direction": spec.direction,
                "ground_station": {
                    "node_id": _GROUND_STATIONS[spec.ground_station_name],
                    "display_name": spec.ground_station_name,
                },
                "zone": {
                    "zone_id": spec.zone_id,
                    "packet_ipv4": packet_addresses[spec.zone_id],
                },
                "schedule": {
                    "interval_semantics": "[start,end)",
                    "start_time": start.isoformat(),
                    "end_time": end.isoformat(),
                    "start_offset_seconds": spec.start_offset_ms / 1_000,
                    "end_offset_seconds": spec.end_offset_ms / 1_000,
                    "interval_seconds": spec.interval_ms / 1_000,
                    "phase_seconds": spec.phase_ms / 1_000,
                    "scheduled_slots": spec.scheduled_slots,
                },
                "semantic_purpose": spec.purpose,
                "event_focus": spec.event_focus,
            }
        )
    return {
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
            "start_time": simulation_start.isoformat(),
            "end_time": simulation_end.isoformat(),
            "duration_seconds": duration_ms / 1_000,
        },
        "inventory": {
            "series": len(series),
            "scheduled_slots": sum(value.scheduled_slots for value in _SERIES),
            "maximum_concurrent_series": _maximum_concurrent_series(_SERIES),
        },
        "static_expectation": dict(_STATIC_EXPECTATION),
        "planned_zone_service_segments": planned_zone_segments,
        "series": tuple(series),
    }


def write_canonical_traffic_workload(
    directory: Path,
    *,
    simulation_start: datetime,
    simulation_end: datetime,
    zone_prefixes: Mapping[str, str],
    zone_service_segments: Iterable[Mapping[str, object]] = (),
) -> dict[str, object]:
    """Write the example-owned durable workload schedule."""

    output = Path(directory)
    output.mkdir(parents=True, exist_ok=True)
    path = output / "traffic-workload.json"
    value = build_canonical_traffic_workload(
        simulation_start=simulation_start,
        simulation_end=simulation_end,
        zone_prefixes=zone_prefixes,
        zone_service_segments=zone_service_segments,
    )
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return value


@dataclass(frozen=True, slots=True)
class _PingProcessSpec:
    series_id: str
    launch_offset_seconds: float
    interval_seconds: float
    count: int | None
    container_id: str
    source_ipv4: str
    destination_ipv4: str


@dataclass(slots=True)
class _RunningPingGenerator:
    spec: _PingProcessSpec
    runtime_identity: str
    pid_file: str
    process: subprocess.Popen[bytes]
    stopped_verified: bool = False


@dataclass(frozen=True, slots=True)
class _ZoneServiceAction:
    series_id: str
    offset_seconds: float
    service_satellite_id: str | None


_PING_GENERATOR_SCRIPT = r"""
pid_file=$1
source_ipv4=$2
interval=$3
count=$4
destination_ipv4=$5
directory=${pid_file%/*}
mkdir -p "$directory" || exit 125
if [ -e "$pid_file" ]; then
    exit 125
fi
if ! printf '%s\n' "$$" > "$pid_file"; then
    exit 125
fi
if [ -n "$count" ]; then
    exec ping -n -I "$source_ipv4" -i "$interval" -W 1 -c "$count" \
        "$destination_ipv4"
fi
exec ping -n -I "$source_ipv4" -i "$interval" -W 1 \
    "$destination_ipv4"
""".strip()


_PING_GENERATOR_CONTROL_SCRIPT = r"""
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
    kill)
        if [ "$state" = active ]; then
            if kill -KILL "$pid" 2>/dev/null; then
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
    *)
        exit 64
        ;;
esac
""".strip()


class _CanonicalTrafficWorkloadHarness:
    """Supervise the canonical example's ordinary Linux ping processes."""

    def __init__(
        self,
        *,
        docker_executable: str,
        schedule: Mapping[str, object],
        runtime_bindings: Mapping[str, str],
        runtime_ipv4: Mapping[str, str],
    ) -> None:
        self._docker = docker_executable
        self._schedule = dict(schedule)
        self._runtime_bindings = dict(runtime_bindings)
        self._runtime_ipv4 = dict(runtime_ipv4)
        self._process_specs = list(
            _ground_to_zone_process_specs(
                self._schedule,
                self._runtime_bindings,
                self._runtime_ipv4,
            )
        )
        simulation = self._schedule.get("simulation")
        if not isinstance(simulation, Mapping):
            raise ValueError("canonical workload schedule is invalid")
        self._simulation_start = datetime.fromisoformat(
            str(simulation["start_time"])
        )
        self._zone_series = _zone_to_ground_series(self._schedule)
        self._pending_zone_actions = _planned_zone_service_actions(
            self._schedule,
            self._zone_series,
        )
        self._active_zone_generators: dict[str, _RunningPingGenerator] = {}
        self._completed_zone_series: set[str] = set()
        self._runtime_token = uuid4().hex
        self._stop = Event()
        self._lock = RLock()
        self._processes: list[_RunningPingGenerator] = []
        self._completed_process_count = 0
        self._thread: Thread | None = None
        self._error: BaseException | None = None

    def start(self, monotonic_origin: float) -> None:
        if self._thread is not None:
            raise RuntimeError("canonical traffic workload is already started")
        self._thread = Thread(
            target=self._run,
            args=(monotonic_origin,),
            name="skyseed-mini-traffic-workload",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5)
            if thread.is_alive():
                self._terminate_processes()
                thread.join(timeout=2)
            if thread.is_alive() and self._error is None:
                self._error = RuntimeError(
                    "canonical traffic workload supervisor did not stop"
                )
        self._terminate_processes()
        if self._error is not None:
            raise RuntimeError(
                f"canonical traffic workload failed: {self._error}"
            ) from self._error

    def _run(self, origin: float) -> None:
        try:
            generator_index = 0
            pending = list(self._process_specs)
            while True:
                now = monotonic()
                pending.sort(
                    key=lambda item: (
                        item.launch_offset_seconds,
                        item.series_id,
                    )
                )
                while (
                    pending
                    and origin + pending[0].launch_offset_seconds <= now
                ):
                    generator_index += 1
                    self._launch_generator(
                        pending.pop(0),
                        generator_index,
                    )
                    now = monotonic()
                due_zone_actions = tuple(
                    sorted(
                        (
                            value
                            for value in self._pending_zone_actions.values()
                            if origin + value.offset_seconds <= now
                        ),
                        key=lambda value: (value.offset_seconds, value.series_id),
                    )
                )
                for action in due_zone_actions:
                    generator_index += self._apply_zone_action(
                        action,
                        generator_index + 1,
                    )
                self._reap_processes(final=False)
                if self._stop.is_set():
                    return
                if (
                    not pending
                    and not self._pending_zone_actions
                    and len(self._completed_zone_series)
                    == len(self._zone_series)
                    and self._reap_processes(final=False) == 0
                ):
                    return
                delay = 0.05
                if pending:
                    delay = min(
                        delay,
                        max(
                            0.0,
                            origin
                            + pending[0].launch_offset_seconds
                            - monotonic(),
                        ),
                    )
                if self._pending_zone_actions:
                    delay = min(
                        delay,
                        max(
                            0.0,
                            origin
                            + min(
                                value.offset_seconds
                                for value in self._pending_zone_actions.values()
                            )
                            - monotonic(),
                        ),
                    )
                if self._stop.wait(delay):
                    return
        except BaseException as error:
            self._error = error
            self._stop.set()

    def _apply_zone_action(
        self,
        action: _ZoneServiceAction,
        generator_index: int,
    ) -> int:
        key = (action.series_id, action.offset_seconds)
        self._pending_zone_actions.pop(key, None)
        series = self._zone_series[action.series_id]
        end_offset = float(series["schedule"]["end_offset_seconds"])
        active = self._active_zone_generators.get(action.series_id)
        active_service = (
            None if active is None else active.spec.container_id
        )
        desired_container = (
            None
            if action.service_satellite_id is None
            else self._runtime_bindings[action.service_satellite_id]
        )
        if active is not None and active_service != desired_container:
            self._stop_generator(active)
            active = None
        if (
            active is None
            and action.service_satellite_id is not None
            and action.offset_seconds < end_offset - 1e-9
        ):
            ground = series["ground_station"]
            zone = series["zone"]
            ground_id = str(ground["node_id"])
            generator = self._launch_generator(
                _PingProcessSpec(
                    action.series_id,
                    action.offset_seconds,
                    float(series["schedule"]["interval_seconds"]),
                    None,
                    desired_container,
                    str(zone["packet_ipv4"]),
                    self._runtime_ipv4[ground_id],
                ),
                generator_index,
            )
            self._active_zone_generators[action.series_id] = generator
            return 1
        if action.offset_seconds >= end_offset - 1e-9:
            self._completed_zone_series.add(action.series_id)
        return 0

    def _launch_generator(
        self,
        spec: _PingProcessSpec,
        index: int,
    ) -> _RunningPingGenerator:
        runtime_identity = (
            f"skyseed-mini-{self._runtime_token}-generator-{index:03d}"
        )
        pid_file = (
            f"/tmp/skyseed-v2-traffic-{self._runtime_token}/"
            f"generator-{index:03d}.pid"
        )
        process = subprocess.Popen(
            (
                self._docker,
                "exec",
                spec.container_id,
                "env",
                f"SKYSEED_TRAFFIC_GENERATOR_ID={runtime_identity}",
                "sh",
                "-c",
                _PING_GENERATOR_SCRIPT,
                "skyseed-traffic-generator",
                pid_file,
                spec.source_ipv4,
                _seconds_argument(spec.interval_seconds),
                "" if spec.count is None else str(spec.count),
                spec.destination_ipv4,
            ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        generator = _RunningPingGenerator(
            spec=spec,
            runtime_identity=runtime_identity,
            pid_file=pid_file,
            process=process,
        )
        with self._lock:
            self._processes.append(generator)
        self._wait_for_generator_registration(generator)
        return generator

    def _wait_for_generator_registration(
        self, generator: _RunningPingGenerator
    ) -> None:
        deadline = monotonic() + 2.0
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
                    f"{generator.spec.series_id} generator registration is {state}"
                )
            code = generator.process.poll()
            if code is not None:
                raise RuntimeError(
                    f"{generator.spec.series_id} generator exited before registration "
                    f"with {code}"
                )
            if self._stop.wait(0.02):
                return
            if monotonic() >= deadline:
                raise RuntimeError(
                    f"{generator.spec.series_id} generator did not register"
                )

    def _reap_processes(self, *, final: bool) -> int:
        alive = 0
        with self._lock:
            values = tuple(self._processes)
        for generator in values:
            code = generator.process.poll()
            if code is None:
                alive += 1
                continue
            if not generator.stopped_verified:
                try:
                    self._verify_generator_stopped(generator)
                except BaseException as error:
                    if self._error is None:
                        self._error = error
                        self._stop.set()
            if (
                code not in (0, 1)
                and not self._stop.is_set()
                and self._error is None
            ):
                self._error = RuntimeError(
                    f"{generator.spec.series_id} ping process exited with {code}"
                )
                self._stop.set()
            if generator.stopped_verified:
                self._retire_generator(generator)
        if final and alive:
            self._terminate_processes()
            return 0
        return alive

    def _terminate_processes(self) -> None:
        with self._lock:
            values = tuple(self._processes)
        errors = []
        for generator in values:
            try:
                self._stop_generator(generator)
            except BaseException as error:
                errors.append(error)
            finally:
                self._reap_docker_exec(generator.process)
        if errors:
            cleanup_failure = "; ".join(str(error) for error in errors)
            self._error = RuntimeError(
                cleanup_failure
                if self._error is None
                else f"{self._error}; generator cleanup failed: {cleanup_failure}"
            )

    @staticmethod
    def _reap_docker_exec(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1)

    def _stop_generator(self, generator: _RunningPingGenerator) -> None:
        if generator.stopped_verified:
            self._reap_docker_exec(generator.process)
            self._retire_generator(generator)
            return
        state = self._stable_generator_state(generator)
        if state == "active":
            self._signal_generator(generator, "term")
        elif state in {
            "identity-mismatch",
            "identity-duplicate",
            "invalid-pid",
        }:
            self._generator_control(
                generator, "discard", expected="discarded"
            )
            raise RuntimeError(
                f"{generator.spec.series_id} generator stop refused {state} identity"
            )
        elif generator.process.poll() is None:
            # The generator has already exited, or the exact docker-exec
            # command failed before registering its fixed in-container PID.
            generator.process.terminate()

        try:
            generator.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            state = self._stable_generator_state(generator)
            if state == "active":
                self._signal_generator(generator, "kill")
            try:
                generator.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                generator.process.terminate()
                try:
                    generator.process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    generator.process.kill()
                    generator.process.wait(timeout=1)
        self._verify_generator_stopped(generator)
        self._reap_docker_exec(generator.process)
        self._retire_generator(generator)

    def _retire_generator(self, generator: _RunningPingGenerator) -> None:
        with self._lock:
            if generator not in self._processes:
                return
            self._processes.remove(generator)
            self._completed_process_count += 1
            active = self._active_zone_generators.get(generator.spec.series_id)
            if active is generator:
                del self._active_zone_generators[generator.spec.series_id]

    def _verify_generator_stopped(self, generator: _RunningPingGenerator) -> None:
        state = self._stable_generator_state(generator)
        if state == "active":
            self._signal_generator(generator, "kill")
            deadline = monotonic() + 1.0
            while monotonic() < deadline:
                sleep(0.02)
                state = self._stable_generator_state(generator)
                if state != "active":
                    break
        if state in {
            "active",
            "identity-mismatch",
            "identity-duplicate",
            "invalid-pid",
        }:
            self._generator_control(
                generator, "discard", expected="discarded"
            )
            raise RuntimeError(
                f"{generator.spec.series_id} generator remains {state} after stop"
            )
        self._generator_control(generator, "cleanup", expected="cleaned")
        generator.stopped_verified = True

    def _stable_generator_state(self, generator: _RunningPingGenerator) -> str:
        state = self._generator_state(generator)
        if state != "identity-mismatch":
            return state
        # A just-exited process can briefly retain /proc state with an empty
        # environment. Re-read once before treating the PID as reused/foreign.
        sleep(0.02)
        return self._generator_state(generator)

    def _generator_state(self, generator: _RunningPingGenerator) -> str:
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

    def _signal_generator(
        self, generator: _RunningPingGenerator, signal: str
    ) -> None:
        if signal not in {"term", "kill"}:
            raise ValueError("traffic generator signal is invalid")
        self._generator_control(
            generator,
            signal,
            expected={"signalled", "missing", "stale"},
        )

    def _generator_control(
        self,
        generator: _RunningPingGenerator,
        action: str,
        *,
        expected: str | set[str],
    ) -> str:
        result = subprocess.run(
            (
                self._docker,
                "exec",
                generator.spec.container_id,
                "sh",
                "-c",
                _PING_GENERATOR_CONTROL_SCRIPT,
                "skyseed-traffic-generator-control",
                action,
                generator.pid_file,
                generator.runtime_identity,
            ),
            check=False,
            capture_output=True,
            text=True,
            timeout=2.0,
        )
        value = result.stdout.strip()
        allowed = {expected} if isinstance(expected, str) else expected
        if result.returncode != 0 or value not in allowed:
            raise RuntimeError(
                f"{generator.spec.series_id} generator {action} failed: "
                f"code={result.returncode}, state={value or 'none'}, "
                f"stderr={result.stderr.strip()}"
            )
        return value


def create_canonical_traffic_workload_harness(
    *,
    docker_executable: str,
    schedule: Mapping[str, object],
    runner_snapshot: Mapping[str, object],
) -> _CanonicalTrafficWorkloadHarness:
    """Bind generators only to frozen Runner-owned runtime facts."""

    runtime_bindings, runtime_ipv4 = _runtime_bindings_from_runner_snapshot(
        runner_snapshot
    )
    return _CanonicalTrafficWorkloadHarness(
        docker_executable=docker_executable,
        schedule=schedule,
        runtime_bindings=runtime_bindings,
        runtime_ipv4=runtime_ipv4,
    )


def _ground_to_zone_process_specs(
    schedule: Mapping[str, object],
    bindings: Mapping[str, str],
    runtime_ipv4: Mapping[str, str],
) -> tuple[_PingProcessSpec, ...]:
    series_values = schedule.get("series")
    if not isinstance(series_values, (tuple, list)):
        raise ValueError("canonical workload schedule is invalid")
    result = []
    for value in series_values:
        if not isinstance(value, Mapping):
            raise ValueError("canonical workload series is invalid")
        series_id = str(value["series_id"])
        direction = str(value["direction"])
        if direction != "ground-station-to-zone":
            if direction != "zone-to-ground-station":
                raise ValueError("canonical workload direction is invalid")
            continue
        ground = value["ground_station"]
        zone = value["zone"]
        cadence = value["schedule"]
        if not all(
            isinstance(item, Mapping) for item in (ground, zone, cadence)
        ):
            raise ValueError("canonical workload series facts are invalid")
        ground_id = str(ground["node_id"])
        zone_ipv4 = str(zone["packet_ipv4"])
        start_offset = float(cadence["start_offset_seconds"])
        interval = float(cadence["interval_seconds"])
        slots = int(cadence["scheduled_slots"])
        result.append(
            _PingProcessSpec(
                series_id,
                start_offset,
                interval,
                slots,
                bindings[ground_id],
                runtime_ipv4[ground_id],
                zone_ipv4,
            )
        )
    return tuple(
        sorted(
            result,
            key=lambda item: (item.launch_offset_seconds, item.series_id),
        )
    )


def _zone_to_ground_series(
    schedule: Mapping[str, object],
) -> dict[str, Mapping[str, object]]:
    """Index the bounded logical Zone-source series without realizing work."""

    series_values = schedule.get("series")
    if not isinstance(series_values, (tuple, list)):
        raise ValueError("canonical workload schedule is invalid")
    result: dict[str, Mapping[str, object]] = {}
    for value in series_values:
        if not isinstance(value, Mapping):
            raise ValueError("canonical workload series is invalid")
        if value.get("direction") != "zone-to-ground-station":
            continue
        series_id = str(value["series_id"])
        if series_id in result:
            raise ValueError("canonical workload series identity is duplicated")
        if not all(
            isinstance(value.get(key), Mapping)
            for key in ("ground_station", "zone", "schedule")
        ):
            raise ValueError("canonical workload series facts are invalid")
        result[series_id] = value
    return result


def _planned_zone_service_actions(
    schedule: Mapping[str, object],
    series_values: Mapping[str, Mapping[str, object]],
) -> dict[tuple[str, float], _ZoneServiceAction]:
    raw_segments = schedule.get("planned_zone_service_segments")
    if not isinstance(raw_segments, (tuple, list)):
        raise ValueError("canonical workload lacks planned Zone service segments")
    segments_by_zone: dict[str, list[tuple[float, float, str]]] = {}
    for value in raw_segments:
        if not isinstance(value, Mapping):
            raise ValueError("canonical workload Zone service segment is invalid")
        zone_id = str(value.get("zone_id", ""))
        service = str(value.get("service_satellite_id", ""))
        start = float(value.get("start_offset_seconds", -1))
        end = float(value.get("end_offset_seconds", -1))
        if not zone_id or not service or start < 0 or end <= start:
            raise ValueError("canonical workload Zone service segment is invalid")
        segments_by_zone.setdefault(zone_id, []).append((start, end, service))
    for values in segments_by_zone.values():
        values.sort()
        if any(current[0] < previous[1] for previous, current in zip(values, values[1:])):
            raise ValueError("canonical workload Zone service segments overlap")

    actions: dict[tuple[str, float], _ZoneServiceAction] = {}
    for series_id, series in series_values.items():
        cadence = series["schedule"]
        zone = series["zone"]
        start = float(cadence["start_offset_seconds"])
        end = float(cadence["end_offset_seconds"])
        interval = float(cadence["interval_seconds"])
        zone_id = str(zone["zone_id"])
        segments = segments_by_zone.get(zone_id, [])

        def service_at(offset: float) -> str | None:
            return next(
                (
                    service
                    for segment_start, segment_end, service in segments
                    if segment_start - 1e-9 <= offset < segment_end - 1e-9
                ),
                None,
            )

        transition_offsets = sorted(
            {
                start,
                *(
                    point
                    for segment_start, segment_end, _service in segments
                    for point in (segment_start, segment_end)
                    if start < point < end
                ),
            }
        )
        previous_service: str | None = None
        for offset in transition_offsets:
            desired_service = service_at(offset)
            if offset == start:
                if desired_service is not None:
                    actions[(series_id, offset)] = _ZoneServiceAction(
                        series_id, offset, desired_service
                    )
                previous_service = desired_service
                continue
            if desired_service == previous_service:
                continue
            if desired_service is None:
                actions[(series_id, offset)] = _ZoneServiceAction(
                    series_id, offset, None
                )
            else:
                launch = _next_series_slot(start, interval, offset)
                if launch > offset + 1e-9:
                    actions[(series_id, offset)] = _ZoneServiceAction(
                        series_id, offset, None
                    )
                if launch < end - 1e-9 and service_at(launch) == desired_service:
                    actions[(series_id, launch)] = _ZoneServiceAction(
                        series_id, launch, desired_service
                    )
            previous_service = desired_service
        actions[(series_id, end)] = _ZoneServiceAction(series_id, end, None)
    return actions


def _next_series_slot(
    start_offset: float,
    interval: float,
    event_offset: float,
) -> float:
    index = max(0, ceil((event_offset - start_offset) / interval - 1e-9))
    return start_offset + index * interval


def _runtime_bindings_from_runner_snapshot(
    snapshot: Mapping[str, object],
) -> tuple[dict[str, str], dict[str, str]]:
    runner = snapshot.get("runner")
    if not isinstance(runner, Mapping):
        raise ValueError("Runner inspection snapshot lacks runner evidence")
    foundation = runner.get("runtime_foundation")
    if not isinstance(foundation, Mapping):
        raise ValueError("Runner inspection snapshot lacks runtime foundation")
    foundation_token = foundation.get("foundation_token")
    if not isinstance(foundation_token, str) or not foundation_token:
        raise ValueError("Runner runtime foundation identity is unavailable")
    raw_bindings = foundation.get("bindings")
    if not isinstance(raw_bindings, (tuple, list)) or not raw_bindings:
        raise ValueError("Runner inspection snapshot lacks runtime bindings")
    bindings = {}
    runtime_ipv4 = {}
    for value in raw_bindings:
        if not isinstance(value, Mapping):
            raise ValueError("Runner runtime binding evidence is invalid")
        node_id = str(value.get("node_id", ""))
        container_id = str(value.get("container_id", ""))
        address = str(value.get("runtime_ipv4", ""))
        if not node_id or not container_id or not address:
            raise ValueError("Runner runtime binding evidence is incomplete")
        if node_id in bindings:
            raise ValueError("Runner runtime binding identity is duplicated")
        bindings[node_id] = container_id
        runtime_ipv4[node_id] = address
    if foundation.get("bound_node_count") != len(bindings):
        raise ValueError("Runner runtime binding count evidence differs")
    if bindings.keys() != runtime_ipv4.keys():
        raise ValueError("Runner runtime address coverage differs from bindings")
    return bindings, runtime_ipv4


def _zone_packet_ipv4(prefix: str) -> str:
    value = ip_interface(prefix)
    if not isinstance(value, IPv4Interface) or value.network.prefixlen != 32:
        raise ValueError("canonical Zone packet identity must be one IPv4 /32")
    return str(value.ip)


def _maximum_concurrent_series(values: tuple[_SeriesSpec, ...]) -> int:
    points = []
    for value in values:
        points.extend(((value.start_offset_ms, 1), (value.end_offset_ms, -1)))
    current = maximum = 0
    for _offset, delta in sorted(points, key=lambda item: (item[0], item[1])):
        current += delta
        maximum = max(maximum, current)
    return maximum


def _seconds_argument(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


__all__ = [
    "build_canonical_traffic_workload",
    "create_canonical_traffic_workload_harness",
    "write_canonical_traffic_workload",
]
