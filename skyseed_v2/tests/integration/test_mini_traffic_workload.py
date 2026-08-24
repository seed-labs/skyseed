from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
from threading import Event, RLock
from types import SimpleNamespace

import pytest

from examples.mini_satellite_internet.traffic_workload import (
    _MAXIMUM_WORKLOAD_CLOCK_SKEW_SECONDS,
    _await_workload_clock_alignment,
    _workload_clock_alignment,
    _CanonicalTrafficWorkloadHarness,
    _PING_GENERATOR_CONTROL_SCRIPT,
    _PING_GENERATOR_SCRIPT,
    _PingProcessSpec,
    _RunningPingGenerator,
    _ground_to_zone_process_specs,
    _runtime_bindings_from_runner_snapshot,
    build_canonical_traffic_workload,
    write_canonical_traffic_workload,
)


START = datetime(2026, 1, 1, 0, 0, 30, tzinfo=timezone.utc)
END = datetime(2026, 1, 1, 0, 11, 0, tzinfo=timezone.utc)
PREFIXES = {
    "western-europe": "198.18.0.1/32",
    "east-asia": "198.18.0.2/32",
    "north-america-east": "198.18.0.3/32",
    "northeast-priority": "198.18.0.4/32",
}
GROUND = {
    "Marsala": "65010/ground-station/gs-marsala-reference",
    "Porto Alegre": "65010/ground-station/gs-porto-alegre-reference",
    "Yamaguchi": "65010/ground-station/gs-yamaguchi-reference",
}
SAT_A = "65010/satellite/dynamic-shell-orbit-001-satellite-0001"
SAT_B = "65010/satellite/dynamic-shell-orbit-002-satellite-0002"


def _schedule() -> dict[str, object]:
    return build_canonical_traffic_workload(
        simulation_start=START,
        simulation_end=END,
        zone_prefixes=PREFIXES,
        zone_service_segments=(
            {
                "zone_id": "east-asia",
                "service_satellite_id": SAT_A,
                "start_time": START,
                "end_time": START + timedelta(seconds=200),
            },
            {
                "zone_id": "east-asia",
                "service_satellite_id": SAT_B,
                "start_time": START + timedelta(seconds=200),
                "end_time": START + timedelta(seconds=250),
            },
        ),
    )


def test_frozen_canonical_schedule_uses_corrected_relative_offsets() -> None:
    value = _schedule()
    series = {item["series_id"]: item for item in value["series"]}

    assert tuple(series) == ("S1", "S2", "S3", "S4", "S5", "S6", "S7")
    assert [series[key]["schedule"]["start_offset_seconds"] for key in series] == [
        10.25,
        85.75,
        90.4,
        230.1,
        330.6,
        460.9,
        525.2,
    ]
    assert [series[key]["schedule"]["end_offset_seconds"] for key in series] == [
        250.25,
        590.75,
        330.4,
        470.1,
        570.6,
        580.9,
        615.2,
    ]
    assert series["S1"]["schedule"]["start_time"] == (
        "2026-01-01T00:00:40.250000+00:00"
    )
    assert series["S7"]["schedule"]["end_time"] == (
        "2026-01-01T00:10:45.200000+00:00"
    )
    assert [series[key]["schedule"]["scheduled_slots"] for key in series] == [
        80,
        169,
        120,
        120,
        120,
        40,
        90,
    ]
    assert value["inventory"] == {
        "series": 7,
        "scheduled_slots": 739,
        "maximum_concurrent_series": 4,
    }
    assert value["static_expectation"] == {
        "status": "pre-runtime-estimate-only",
        "paired_transactions": 534,
        "scheduled_slots_without_tc_bpf_observation": 205,
        "packet_events": 4430,
    }


def test_schedule_serialization_keeps_plans_separate_from_runtime_evidence(
    tmp_path: Path,
) -> None:
    written = write_canonical_traffic_workload(
        tmp_path,
        simulation_start=START,
        simulation_end=END,
        zone_prefixes=PREFIXES,
    )
    loaded = json.loads((tmp_path / "traffic-workload.json").read_text())

    assert loaded["inventory"] == written["inventory"]
    assert loaded["authority"] == {
        "status": "planned-example-workload",
        "proves_command_execution": False,
        "proves_packet_emission": False,
        "proves_timeout": False,
        "proves_packet_loss": False,
        "packet_observation_authority": "traffic-telemetry.jsonl",
    }


def _zone_harness() -> _CanonicalTrafficWorkloadHarness:
    schedule = _schedule()
    bindings = {
        **{value: f"container-{key}" for key, value in GROUND.items()},
        SAT_A: "container-sat-a",
        SAT_B: "container-sat-b",
    }
    runtime_ipv4 = {
        **{
            GROUND["Marsala"]: "100.64.0.1",
            GROUND["Porto Alegre"]: "100.64.0.2",
            GROUND["Yamaguchi"]: "100.64.0.3",
        },
        SAT_A: "100.64.0.4",
        SAT_B: "100.64.0.5",
    }
    return _CanonicalTrafficWorkloadHarness(
        docker_executable="docker",
        schedule=schedule,
        runtime_bindings=bindings,
        runtime_ipv4=runtime_ipv4,
    )


def _record_zone_process_switches(harness, monkeypatch):
    launched = []
    stopped = []

    def launch(spec, _index):
        generator = SimpleNamespace(spec=spec)
        launched.append(generator)
        return generator

    def stop(generator):
        stopped.append(generator)
        harness._active_zone_generators.pop(generator.spec.series_id, None)

    monkeypatch.setattr(harness, "_launch_generator", launch)
    monkeypatch.setattr(harness, "_stop_generator", stop)
    return launched, stopped


def test_zone_source_same_service_across_frontier_refills_uses_one_process(
    monkeypatch,
) -> None:
    harness = _zone_harness()
    launched, stopped = _record_zone_process_switches(harness, monkeypatch)

    action = harness._pending_zone_actions[("S2", 85.75)]
    harness._apply_zone_action(action, 1)

    assert len(launched) == 1
    assert stopped == []
    assert launched[0].spec.series_id == "S2"
    assert launched[0].spec.count is None
    assert launched[0].spec.container_id == "container-sat-a"


def test_zone_source_change_switches_once_and_remove_stops_without_restart(
    monkeypatch,
) -> None:
    harness = _zone_harness()
    launched, stopped = _record_zone_process_switches(harness, monkeypatch)

    harness._apply_zone_action(
        harness._pending_zone_actions[("S2", 85.75)], 1
    )
    harness._apply_zone_action(
        harness._pending_zone_actions[("S2", 200.0)], 2
    )
    harness._apply_zone_action(
        harness._pending_zone_actions[("S2", 202.75)], 2
    )
    harness._apply_zone_action(
        harness._pending_zone_actions[("S2", 250.0)], 3
    )
    assert [value.spec.container_id for value in launched] == [
        "container-sat-a",
        "container-sat-b",
    ]
    assert [value.spec.launch_offset_seconds for value in launched] == [
        85.75,
        202.75,
    ]
    assert stopped == launched
    assert "S2" not in harness._active_zone_generators


def test_zone_source_actions_are_fully_planned_without_progression_callback() -> None:
    harness = _zone_harness()

    assert [
        (offset, action.service_satellite_id)
        for (series_id, offset), action in sorted(
            harness._pending_zone_actions.items()
        )
        if series_id == "S2"
    ] == [
        (85.75, SAT_A),
        (200.0, None),
        (202.75, SAT_B),
        (250.0, None),
        (590.75, None),
    ]
    source = Path(__import__(
        "examples.mini_satellite_internet.traffic_workload",
        fromlist=["__file__"],
    ).__file__).read_text(encoding="utf-8")
    assert "progression_snapshot" not in source
    assert "_refresh_zone_actions" not in source


def test_example_workload_has_no_runner_public_api_dependency() -> None:
    path = (
        Path(__file__).parents[2]
        / "examples/mini_satellite_internet/traffic_workload.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }

    assert not any(value.endswith(".runner") for value in imports)


def test_workload_consumes_detached_runner_bindings_without_docker_discovery(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("binding projection must not execute Docker")
        ),
    )
    snapshot = {
        "runner": {
            "runtime_foundation": {
                "foundation_token": "foundation-1",
                "bound_node_count": 2,
                "bindings": (
                    {
                        "node_id": SAT_A,
                        "container_id": "container-a",
                        "runtime_ipv4": "192.0.2.1",
                    },
                    {
                        "node_id": GROUND["Marsala"],
                        "container_id": "container-ground",
                        "runtime_ipv4": "192.0.2.2",
                    },
                ),
            }
        }
    }

    assert _runtime_bindings_from_runner_snapshot(snapshot) == (
        {
            SAT_A: "container-a",
            GROUND["Marsala"]: "container-ground",
        },
        {
            SAT_A: "192.0.2.1",
            GROUND["Marsala"]: "192.0.2.2",
        },
    )


def test_clock_alignment_uses_existing_runner_evidence_and_strict_bound() -> None:
    workload_origin = 100.0

    passing = _workload_clock_alignment(
        {
            "events": (
                {
                    "event": "automatic_run_start",
                    "wall_origin": 100.049,
                },
            )
        },
        workload_origin,
    )
    failing = _workload_clock_alignment(
        {
            "events": (
                {
                    "event": "automatic_run_start",
                    "wall_origin": 100.050,
                },
            )
        },
        workload_origin,
    )

    assert _MAXIMUM_WORKLOAD_CLOCK_SKEW_SECONDS == 0.050
    assert passing["status"] == "PASS"
    assert passing["scope"] == "planned-schedule-clock-alignment-only"
    assert passing["packet_timing_authority"] == "tc-BPF traffic telemetry"
    assert failing["status"] == "FAIL"


def test_clock_alignment_requires_one_valid_automatic_run_anchor() -> None:
    with pytest.raises(RuntimeError, match="exactly one"):
        _workload_clock_alignment({"events": ()}, 100.0)
    with pytest.raises(RuntimeError, match="invalid"):
        _workload_clock_alignment(
            {
                "events": (
                    {"event": "automatic_run_start", "wall_origin": True},
                )
            },
            100.0,
        )


def test_clock_alignment_is_read_from_detached_runner_history() -> None:
    runner = SimpleNamespace(
        inspection_snapshot=lambda: {
            "events": (
                {"event": "preparation_end"},
                {"event": "automatic_run_start", "wall_origin": 25.012},
            )
        }
    )

    evidence = _await_workload_clock_alignment(
        runner,
        25.0,
        Event(),
    )

    assert evidence["status"] == "PASS"
    assert evidence["runner_wall_origin"] == 25.012


class _FakeGeneratorProcess:
    def __init__(self) -> None:
        self.code: int | None = None
        self.wait_count = 0
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.code

    def wait(self, timeout: float) -> int:
        self.wait_count += 1
        if self.wait_count == 1:
            raise subprocess.TimeoutExpired("docker exec", timeout)
        self.code = 143
        return self.code

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True
        self.code = 137


class _NonBlockingStop:
    def wait(self, _timeout: float) -> bool:
        return False

    def is_set(self) -> bool:
        return False

    def set(self) -> None:
        return None


def _lifecycle_generator() -> tuple[
    _CanonicalTrafficWorkloadHarness,
    _RunningPingGenerator,
    _FakeGeneratorProcess,
]:
    harness = _CanonicalTrafficWorkloadHarness.__new__(
        _CanonicalTrafficWorkloadHarness
    )
    process = _FakeGeneratorProcess()
    generator = _RunningPingGenerator(
        spec=_PingProcessSpec(
            series_id="S-test",
            launch_offset_seconds=1.0,
            interval_seconds=1.0,
            count=10,
            container_id="container-test",
            source_ipv4="100.64.0.1",
            destination_ipv4="198.18.0.1",
        ),
        runtime_identity="skyseed-mini-token-generator-001",
        pid_file="/tmp/skyseed-v2-traffic-token/generator-001.pid",
        process=process,
    )
    harness._lock = RLock()
    harness._processes = [generator]
    harness._active_zone_generators = {}
    harness._completed_process_count = 0
    return harness, generator, process


def test_generator_stop_uses_scoped_term_then_kill_and_cleans_identity(
    monkeypatch,
) -> None:
    harness, generator, process = _lifecycle_generator()
    states = iter(("active", "active", "missing"))
    signals = []
    controls = []
    monkeypatch.setattr(
        harness,
        "_stable_generator_state",
        lambda _generator: next(states),
    )
    monkeypatch.setattr(
        harness,
        "_signal_generator",
        lambda _generator, signal: signals.append(signal),
    )
    monkeypatch.setattr(
        harness,
        "_generator_control",
        lambda _generator, action, *, expected: controls.append(action) or "cleaned",
    )

    harness._stop_generator(generator)

    assert signals == ["term", "kill"]
    assert controls == ["cleanup"]
    assert generator.stopped_verified is True
    assert process.terminated is False
    assert process.killed is False


def test_generator_identity_mismatch_refuses_signal_and_discards_own_evidence(
    monkeypatch,
) -> None:
    harness, generator, _process = _lifecycle_generator()
    signals = []
    controls = []
    monkeypatch.setattr(
        harness,
        "_stable_generator_state",
        lambda _generator: "identity-mismatch",
    )
    monkeypatch.setattr(
        harness,
        "_signal_generator",
        lambda _generator, signal: signals.append(signal),
    )
    monkeypatch.setattr(
        harness,
        "_generator_control",
        lambda _generator, action, *, expected: controls.append(action) or "discarded",
    )

    with pytest.raises(RuntimeError, match="refused identity-mismatch"):
        harness._stop_generator(generator)

    assert signals == []
    assert controls == ["discard"]


def test_generator_protocol_is_unique_scoped_and_never_uses_broad_pkill() -> None:
    source = _PING_GENERATOR_SCRIPT + _PING_GENERATOR_CONTROL_SCRIPT

    assert "SKYSEED_TRAFFIC_GENERATOR_ID" in source
    assert "printf '%s\\n' \"$$\" > \"$pid_file\"" in source
    assert "exec ping" in source
    assert 'grep -Fqx "SKYSEED_TRAFFIC_GENERATOR_ID=$generator_id"' in source
    assert "kill -TERM \"$pid\"" in source
    assert "kill -KILL \"$pid\"" in source
    assert "rm -f \"$pid_file\"" in source
    assert "pkill" not in source


def test_each_physical_segment_gets_a_distinct_pre_exec_identity(
    monkeypatch,
) -> None:
    harness = _CanonicalTrafficWorkloadHarness.__new__(
        _CanonicalTrafficWorkloadHarness
    )
    spec = _PingProcessSpec(
        series_id="S2",
        launch_offset_seconds=0.0,
        interval_seconds=3.0,
        count=2,
        container_id="container-zone-service",
        source_ipv4="198.18.0.2",
        destination_ipv4="100.64.0.1",
    )
    harness._docker = "docker"
    harness._runtime_token = "runtime-token"
    harness._stop = _NonBlockingStop()
    harness._lock = RLock()
    harness._processes = []
    harness._active_zone_generators = {}
    harness._completed_process_count = 0
    harness._error = None
    commands = []

    def popen(command, **_kwargs):
        commands.append(command)
        return _FakeGeneratorProcess()

    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(
        harness,
        "_wait_for_generator_registration",
        lambda _generator: None,
    )
    harness._launch_generator(spec, 1)
    harness._launch_generator(spec, 2)

    assert len(harness._processes) == 2
    assert len({value.runtime_identity for value in harness._processes}) == 2
    assert len({value.pid_file for value in harness._processes}) == 2
    for command, generator in zip(commands, harness._processes):
        assert command[3:5] == (
            "env",
            f"SKYSEED_TRAFFIC_GENERATOR_ID={generator.runtime_identity}",
        )
        assert command[5:7] == ("sh", "-c")


@pytest.mark.parametrize(
    "source",
    (_PING_GENERATOR_SCRIPT, _PING_GENERATOR_CONTROL_SCRIPT),
)
def test_generator_protocol_is_valid_posix_shell(source: str) -> None:
    checked = subprocess.run(
        ("sh", "-n"),
        input=source,
        capture_output=True,
        text=True,
        check=False,
    )

    assert checked.returncode == 0, checked.stderr


def test_generator_cleanup_failure_still_reaps_exact_docker_exec(
    monkeypatch,
) -> None:
    harness, generator, process = _lifecycle_generator()
    harness._lock = RLock()
    harness._processes = [generator]
    harness._error = None
    monkeypatch.setattr(
        harness,
        "_stop_generator",
        lambda _generator: (_ for _ in ()).throw(
            RuntimeError("container identity verification failed")
        ),
    )

    harness._terminate_processes()

    assert process.terminated is True
    assert process.killed is True
    assert process.poll() is not None
    assert "container identity verification failed" in str(harness._error)


def test_finished_generator_is_reaped_and_removed_from_live_working_set(
    monkeypatch,
) -> None:
    harness, generator, process = _lifecycle_generator()
    harness._stop = _NonBlockingStop()
    harness._error = None
    process.code = 0

    def verify(value):
        value.stopped_verified = True

    monkeypatch.setattr(harness, "_verify_generator_stopped", verify)

    assert harness._reap_processes(final=False) == 0
    assert harness._processes == []
    assert harness._completed_process_count == 1
