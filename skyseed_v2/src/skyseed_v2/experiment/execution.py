"""Validate and execute one generated SkySEED experiment runtime package."""

from __future__ import annotations
from skyseed_v2.shared._console import _console

import argparse
from collections.abc import Mapping
from datetime import datetime, timezone
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import re
from shlex import quote
import signal
import subprocess
import sys
from time import perf_counter
from threading import Event, Thread, current_thread, main_thread
from uuid import uuid4
from typing import Any

from skyseed_v2.shared._durable_evidence import _append_jsonl
from skyseed_v2.shared.recording import record_performance
from skyseed_v2.experiment.output.observation import _ExecutionObserver, _semantic_value

from skyseed_v2.backend.runner.lifecycle import Runner, _runtime_worker_count
from skyseed_v2.backend.selection import _backend
from .output.occupancy import claim_output, claim_runtime
from ._control import _ControlUnavailable, _request, _serve_control


_FORMAT_VERSION = 6
_KIND = "skyseed-v2-generated-forwarding-execution"
_PROJECT_NAME_FILE = "deployment/compose-project-name"
_PROJECT_NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]*")


def _validate_readiness_command(value):
    if value is None:
        return None
    from collections.abc import Sequence
    if (isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or not value
            or any(not isinstance(arg, str) or '\0' in arg for arg in value) or not value[0]):
        raise ValueError('initial_readiness_command must be a nonempty argv sequence of strings or None')
    return tuple(value)


class _ReadinessCancelled(Exception):
    """A control handoff to normal cleanup, not a command failure."""


def _check_runtime_stop(controls):
    if controls['cancel'].is_set() or controls['reset'].is_set():
        raise _ReadinessCancelled()


def _signal_readiness_group(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass


def _drain_readiness_process(process):
    # The session is owned by this invocation. Never kill unrelated processes,
    # escalate to SIGKILL, or let elapsed time authorize Runner cleanup.
    _signal_readiness_group(process)
    process.wait()
    while True:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return
        # Orphan zombies cannot execute and are reaped by their adopting parent.
        # A cooperative command normally reaps its own children before exit.
        active = False
        for path in Path('/proc').iterdir():
            if not path.name.isdecimal():
                continue
            try:
                stat = (path / 'stat').read_text().rsplit(')', 1)[1].split()
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                continue
            if int(stat[2]) == process.pid and stat[0] not in ('Z', 'X'):
                active = True
                break
        if not active:
            return
        Event().wait(0.05)


def _run_initial_readiness(output, invocation, definition, runner, controls, lifecycle, observer):
    """One optional prepared-input subprocess in the existing execution owner."""
    argv = list(_validate_readiness_command(definition['initial_readiness_command']))
    if argv[0] == definition.get('environment', {}).get('python_executable'):
        argv[0] = sys.executable
    argv.extend(('--output', str(output), '--invocation-directory', str(invocation)))
    directory = invocation / 'readiness'
    directory.mkdir(parents=True, exist_ok=True)
    started = perf_counter()
    interrupted = Event()
    row = {'argv': argv, 'cwd': str(output), 'status': 'waiting',
           'started_at': datetime.now(timezone.utc).isoformat(), 'ended_at': None,
           'exit_code': None, 'elapsed_seconds': None, 'error': None,
           'stdout': 'readiness/stdout.log', 'stderr': 'readiness/stderr.log'}
    process = primary = None
    stop_sent = False
    previous_handler = None
    if current_thread() is main_thread():
        # Deferring Ctrl-C closes Popen's launch/assignment race and permits the
        # command to drain even when the user presses Ctrl-C repeatedly.
        previous_handler = signal.signal(signal.SIGINT, lambda *_: interrupted.set())
    try:
        _write_runner_inspection_handoff(invocation / 'runner/prepared.json',
            runner.inspection_snapshot(), {'status': 'prepared'}, invocation_id=invocation.name)
        _write_json_file_atomic(directory / 'command.json', row)
        controls['readiness'] = dict(row)
        lifecycle('initial_readiness_start', **row)
        _console(f"User preparation starting; formal clock has not started. Logs: {directory}")
        _check_runtime_stop(controls)
        if interrupted.is_set():
            raise KeyboardInterrupt()
        with (directory / 'stdout.log').open('wb') as stdout, (directory / 'stderr.log').open('wb') as stderr:
            process = subprocess.Popen(argv, cwd=output, stdout=stdout, stderr=stderr, start_new_session=True)
            row['pid'] = process.pid
            while process.poll() is None:
                if not stop_sent and (interrupted.is_set() or controls['cancel'].is_set() or controls['reset'].is_set()):
                    _signal_readiness_group(process)
                    stop_sent = True
                Event().wait(0.05)
            row['exit_code'] = process.returncode
        if process.returncode and not (stop_sent and process.returncode in (-signal.SIGTERM, -signal.SIGINT, 130, 143)):
            raise subprocess.CalledProcessError(process.returncode, argv)
        if interrupted.is_set():
            raise KeyboardInterrupt()
        _check_runtime_stop(controls)
    except BaseException as error:
        primary = error
    finally:
        try:
            if process is not None:
                _drain_readiness_process(process)
                row['exit_code'] = process.returncode
        except BaseException as error:
            if primary is None:
                primary = error
            else:
                primary.add_note(f'readiness process drainage also failed: {error}')
        # Cancellation may arrive after command exit or while draining children.
        if primary is None:
            if interrupted.is_set():
                primary = KeyboardInterrupt()
            elif controls['cancel'].is_set() or controls['reset'].is_set():
                primary = _ReadinessCancelled()
        row.update(status=('interrupted' if isinstance(primary, KeyboardInterrupt) else
            'cancelled' if isinstance(primary, _ReadinessCancelled) else 'failed' if primary else 'completed'),
            ended_at=datetime.now(timezone.utc).isoformat(), elapsed_seconds=perf_counter() - started,
            error=None if isinstance(primary, _ReadinessCancelled) else _error_fact(primary))
        controls['readiness'] = dict(row)
        try:
            _write_json_file_atomic(directory / 'command.json', row)
            lifecycle('initial_readiness_end', **row)
            record_performance(observer, component='experiment', stage='initial-readiness', phase='one-shot',
                duration_seconds=row['elapsed_seconds'], duration_role='stage',
                scope={'invocation_id': invocation.name}, work={'completed': int(primary is None)},
                started_monotonic=started)
        except BaseException as error:
            if primary is None:
                primary = error
            else:
                primary.add_note(f'readiness evidence also failed: {error}')
        finally:
            if previous_handler is not None:
                signal.signal(signal.SIGINT, previous_handler)
    if primary is not None:
        state = row['status'] if row['status'] != 'completed' else 'failed'
        _console(f"User preparation {state}: {type(primary).__name__}: {primary}. "
                 "Formal clock did not start; runtime cleanup follows.")
        raise primary
    _console(f"User preparation succeeded after {row['elapsed_seconds']:.1f}s; ready for the formal clock.")


def _run_generated_control(script: Path) -> None:
    """Join one existing control, including its interrupted shutdown, before returning."""
    # A separate process group lets the caller forward Ctrl-C exactly once to
    # the shell and its foreground child, while subsequent Ctrl-C cannot cut
    # short Runner cleanup or leave the control running during Compose teardown.
    process = subprocess.Popen([str(script)], start_new_session=True)
    try:
        returncode = process.wait()
    except KeyboardInterrupt:
        previous_handler = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            try:
                os.killpg(process.pid, signal.SIGINT)
            except ProcessLookupError:
                pass
            # Completion, not elapsed time, permits outer project teardown.
            # Forced termination remains an explicit executor action.
            process.wait()
        finally:
            signal.signal(signal.SIGINT, previous_handler)
        raise
    if returncode:
        raise subprocess.CalledProcessError(returncode, process.args)


def run_generated_experiment(output: Path, *, cleanup: bool = True) -> None:
    """Start and run generated controls, then tear down their Compose project.

    Layer: finite experiment orchestration. Callers use this operation on an
    already generated output. It creates no Simulator, Producer or materials;
    the existing scripts own startup, Runner/Traffic execution and verification.
    Environment, standard streams and generated interpreter selection are inherited.
    By default teardown follows every startup attempt, after the active control
    exits. With ``cleanup=False``, the caller owns project teardown. Primary errors
    and interruptions propagate, with any secondary teardown failure attached.
    """
    if not isinstance(output, Path):
        raise TypeError("output must be a pathlib.Path")
    if type(cleanup) is not bool:
        raise TypeError("cleanup must be a bool")
    output = output.expanduser().resolve()
    names = ("deploy.sh", "run.sh", "cleanup.sh") if cleanup else ("deploy.sh", "run.sh")
    with claim_output(output):
        for name in names:
            script = output / name
            if not script.is_file():
                raise FileNotFoundError(f"generated control is missing: {script}")
            if not os.access(script, os.X_OK):
                raise PermissionError(f"generated control is not executable: {script}")

        primary_error = None
        try:
            _run_generated_control(output / "deploy.sh")
            try:
                session = _request(output, "computation", "status")
            except _ControlUnavailable:
                session = {}
            if not (session.get("runtime_managed") and session["shutdown_requested"]):
                # This avoids a needless child after deployment cancellation. The
                # runtime claim below closes the check-to-launch race itself.
                _run_generated_control(output / "run.sh")
        except BaseException as error:
            primary_error = error
            raise
        finally:
            if cleanup:
                try:
                    _run_generated_control(output / "cleanup.sh")
                except BaseException as teardown_error:
                    if primary_error is None:
                        raise
                    primary_error.add_note(
                        f"generated project teardown also failed: "
                        f"{type(teardown_error).__name__}: {teardown_error}"
                    )


def _write_execution_definition(output_directory, *, generated_output_identity,
        timeline_start, timeline_end, observation_interval,
        runner_artifact, realization_producer, sealed_metadata, runner_batches=(),
        batch_lead_fraction=0.75, batch_capacity_factor=2, keep_alive=False, traffic_enabled=True, traffic_telemetry_enabled=None,
        initial_readiness_command=None):
    """Publish facts already sealed by the backend writer; never rescan its bytes."""
    manifest = sealed_metadata["manifest"]
    value = {
        "format_version": _FORMAT_VERSION, "kind": _KIND, "backend": manifest["backend"],
        "generated_output_identity": generated_output_identity,
        "progression": {"strategy": "sealed-precomputed-runner-artifact", "producer": realization_producer},
        "runner_artifact": {"manifest": runner_artifact,
            "manifest_sha256": sealed_metadata["manifest_sha256"],
            "boundary_count": manifest["timeline"]["boundary_count"],
            "physical_world_fingerprint": manifest["physical_world"]["fingerprint"],
            **{key: manifest[key] for key in ("format_version", "encoding", "realization_count", "sequence_sha256", "timeline")}},
        "simulation": {"start_time": timeline_start.isoformat(), "end_time": timeline_end.isoformat(),
            "observation_interval_seconds": observation_interval.total_seconds(), "bounded": True},
        "runner": {"execution_contract": "sealed-precomputed-only", "keep_alive": keep_alive},
        "traffic_enabled": traffic_enabled,
        "initial_readiness_command": _validate_readiness_command(initial_readiness_command),
        "traffic_telemetry_enabled": (manifest["backend"] == "bpf" if traffic_telemetry_enabled is None else traffic_telemetry_enabled),
        "environment": {"python_executable": sys.executable, "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(), "seedemu_distribution_version": metadata.version("seedemu")},
    }
    if keep_alive or runner_batches:
        value["runner"].update(batch_lead_fraction=batch_lead_fraction, batch_capacity_factor=batch_capacity_factor)
    if runner_batches:
        value["runner_batches"] = list(runner_batches)
    destination = Path(output_directory) / "results/compiler/execution.json"
    _write_json_file_atomic(destination, value)
    return destination


def _write_generated_run_script(
    output_directory: Path,
    *,
    python_executable: str,
    tool_package_parent: Path,
    runtime_workers: int | None = None,
) -> Path:
    """Write the generated Runner/Traffic execution control."""

    output = Path(output_directory)
    tool_parent = quote(str(Path(tool_package_parent).resolve()))
    required_paths = (
        "deployment/compose-project-name",
        "results/compiler/execution.json",
        "results/compiler/runner-artifact/manifest.json",
        "results/compiler/runner-artifact/runner-runtime.bin",
        "deployment/docker-compose.yml",
    )
    required_lines = " \\\n".join(
        f'    "$SCRIPT_DIR/{value}"' for value in required_paths
    )
    if runtime_workers is not None:
        runtime_workers = _runtime_worker_count(runtime_workers)
    runtime_environment = "" if runtime_workers is None else f'export SKYSEED_BPF_RUNTIME_WORKERS="${{SKYSEED_BPF_RUNTIME_WORKERS:-{runtime_workers}}}"\n'
    script = output / "run.sh"
    script.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail

if (( $# != 0 )); then
    echo "run.sh does not accept arguments" >&2
    exit 2
fi
SCRIPT_DIR="$(cd -- "$(dirname -- "${{BASH_SOURCE[0]}}")" && pwd)"
PYTHON={quote(python_executable)}
TOOL_PACKAGE_PARENT={tool_parent}
{runtime_environment}PROJECT_FILE="$SCRIPT_DIR/deployment/compose-project-name"
STARTUP_EVIDENCE="$SCRIPT_DIR/deployment/deployment-startup.json"
for required in \\
{required_lines}; do
    if [[ ! -f "$required" ]]; then
        echo "generated runtime input is missing: $required" >&2
        exit 1
    fi
done
PROJECT_NAME="$(<"$PROJECT_FILE")"
if [[ ! "$PROJECT_NAME" =~ ^[a-z0-9][a-z0-9_-]*$ ]]; then
    echo "generated Compose project identity is invalid" >&2
    exit 1
fi
INVOCATION_ID="$(date -u +%Y%m%dT%H%M%S.%NZ)-${{BASHPID}}"
INVOCATION_DIR="$SCRIPT_DIR/runs/$INVOCATION_ID"
mkdir -p "$INVOCATION_DIR/runner" "$INVOCATION_DIR/traffic" "$INVOCATION_DIR/performance"
TOOL_PYTHONPATH="$TOOL_PACKAGE_PARENT${{PYTHONPATH:+:$PYTHONPATH}}"

execution_status=0
PYTHONPATH="$TOOL_PYTHONPATH" $PYTHON -m skyseed_v2.experiment.execution "$SCRIPT_DIR" \\
    --invocation-directory "$INVOCATION_DIR" \\
    || execution_status=$?

# Raw invocation evidence is drained. Retained calculation may still be live;
# Traffic pins its latest completed request across all domain readers.
if [[ "${{SKYSEED_NO_TOOLS:-0}}" == "1" || ! -f "$SCRIPT_DIR/results/experiment/completed.json" || ! -f "$TOOL_PACKAGE_PARENT/tools/__init__.py" ]]; then
    echo "Observation Tool generation skipped." >&2 || true
    exit "$execution_status"
fi

echo "Observation Tool generation starting; this is separate from runtime resource cleanup." >&2 || true
runner_tool_status=0
PYTHONPATH="$TOOL_PYTHONPATH" $PYTHON -m tools.observation.runner \\
    --handoff "$INVOCATION_DIR/runner/runner-invocation.json" \\
    --events "$INVOCATION_DIR/runner/runner-events.jsonl" \\
    --deployment-startup "$STARTUP_EVIDENCE" \\
    --output "$SCRIPT_DIR/visualizations/runner" \\
    || runner_tool_status=$?
if (( runner_tool_status != 0 )); then
    echo "Runner observation Tool failed (exit $runner_tool_status)." >&2 || true
fi

traffic_tool_status=0
if [[ -f "$SCRIPT_DIR/results/traffic/probe-plan.json" || -f "$INVOCATION_DIR/traffic/traffic-telemetry.jsonl" ]]; then
TRAFFIC_INPUTS=()
if [[ -f "$SCRIPT_DIR/results/traffic/probe-plan.json" ]]; then
    TRAFFIC_INPUTS=(--probe-plan "$SCRIPT_DIR/results/traffic/probe-plan.json" --probe-execution "$INVOCATION_DIR/traffic/probe-execution.jsonl" --runner-events "$INVOCATION_DIR/runner/runner-events.jsonl")
fi
PYTHONPATH="$TOOL_PYTHONPATH" $PYTHON -m tools.observation.traffic \\
    --telemetry "$INVOCATION_DIR/traffic/traffic-telemetry.jsonl" \\
    --physical-frames "$SCRIPT_DIR/results/physical-world/physical-frames.jsonl" \\
    --physical-links "$SCRIPT_DIR/results/physical-world/candidates/physical-link-observations.jsonl.gz" \\
    --planning "$SCRIPT_DIR/results/planner/planning.jsonl.gz" \\
    --bpf-compiler "$SCRIPT_DIR/results/compiler/compiler.jsonl" \\
    "${{TRAFFIC_INPUTS[@]}}" \\
    --output "$SCRIPT_DIR/visualizations/traffic" \\
    || traffic_tool_status=$?
if (( traffic_tool_status != 0 )); then
    echo "Traffic observation Tool failed (exit $traffic_tool_status)." >&2 || true
fi
fi

performance_tool_status=0
PYTHONPATH="$TOOL_PYTHONPATH" $PYTHON -m tools.observation.performance \
    --generated "$SCRIPT_DIR" \
    --observations "$INVOCATION_DIR/performance/execution-observations.jsonl" \
    --output "$SCRIPT_DIR/visualizations/performance" \
    || performance_tool_status=$?
if (( performance_tool_status != 0 )); then
    echo "Performance observation Tool failed (exit $performance_tool_status)." >&2 || true
fi

aggregate_status=0
PYTHONPATH="$TOOL_PYTHONPATH" $PYTHON -c 'import importlib,sys; from pathlib import Path; importlib.import_module("tools.observation._presentation.html").finalize_runtime_aggregate_index(Path(sys.argv[1]))' \\
    "$SCRIPT_DIR/visualizations" \\
    || aggregate_status=$?
if (( aggregate_status != 0 )); then
    echo "Observation index generation failed (exit $aggregate_status)." >&2 || true
fi

if (( runner_tool_status != 0 || traffic_tool_status != 0 || performance_tool_status != 0 || aggregate_status != 0 )); then
    echo "Observation Tool generation failed: one or more detached inspection Tools failed; runtime exit status is unchanged." >&2 || true
else
    echo "Observation Tool generation succeeded." >&2 || true
fi
if (( execution_status != 0 )); then
    exit "$execution_status"
fi
exit 0
""",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def _run_generated_bpf_experiment(
    output_directory: Path,
    *,
    invocation_directory: Path | None = None,
) -> None:
    """Run fresh invocations over retained references; reset never tears down Compose."""
    output = Path(output_directory).resolve()
    controls = {"runner": None, "traffic": None, "reset": Event(), "cancel": Event(),
                "pause": False, "invocation_id": None, "delivery_count": 1, "delivery_error": None,
                "readiness": None}

    def dispatch(request):
        command = request["command"]
        if command in {"reset", "cancel-runtime"}:
            controls["cancel" if command == "cancel-runtime" else command].set()
        # Publish cancellation before reading the Runner reference. Construction
        # publishes that reference before checking the flag, so one side sees it.
        runner = controls["runner"]
        if command in {"reset", "cancel-runtime"}:
            if runner is not None:
                runner.cancel_runtime()
            else:
                _console(f"Runtime {command} requested before Runner preparation; draining admitted work.")
        elif command == "pause":
            controls["pause"] = True
            if runner is not None:
                runner.pause()
        elif command == "resume":
            controls["pause"] = False
            if runner is not None:
                runner.resume()
        elif command != "status":
            raise ValueError(f"unsupported runtime command: {command}")
        return {"invocation_id": controls["invocation_id"],
            "reset_pending": controls["reset"].is_set(), "cancel_requested": controls["cancel"].is_set(),
            "delivered_batches": controls["delivery_count"],
            "delivery_error": controls["delivery_error"],
            "initial_readiness": controls["readiness"],
            "runner": None if runner is None else runner.status(),
            "traffic": None if controls["traffic"] is None else controls["traffic"].snapshot()}

    previous = None
    if invocation_directory is None:
        invocation_directory = output / "runs" / f"run-{uuid4().hex}"
    with claim_output(output), claim_runtime(output), _serve_control(output, "runtime", dispatch):
        verified = _read_and_verify_execution(output)
        try:
            session = _request(output, "computation", "runtime_claim")
        except _ControlUnavailable:
            session = {}
        managed = session.get("runtime_managed", False)
        if managed and session["shutdown_requested"]:
            _console("Runtime stopped before preparation; no runtime resources were acquired.")
            return  # No Runner/Traffic construction before an accepted handoff.
        primary_error = None
        try:
            while not controls["cancel"].is_set():
                controls["reset"].clear()
                controls["delivery_count"] = 1
                controls["delivery_error"] = None
                controls["readiness"] = None
                _run_runtime_invocation(output, invocation_directory=invocation_directory,
                                        verified=verified, controls=controls, reset_from=previous)
                if controls["cancel"].is_set() or not controls["reset"].is_set():
                    break
                previous = controls["invocation_id"]
                invocation_directory = output / "runs" / f"reset-{uuid4().hex}"
            if controls["invocation_id"] is None:
                _console("Runtime stopped before preparation; no runtime resources were acquired.")
        except BaseException as error:
            primary_error = error
            raise
        finally:
            if managed:
                try:
                    # Cleanup/evidence have settled; serialize release before
                    # closing this endpoint, which may still receive shutdown.
                    _request(output, "computation", "runtime_release")
                except BaseException as error:
                    if primary_error is None:
                        raise
                    primary_error.add_note(f"runtime handoff release also failed: {error}")


def _run_runtime_invocation(
    output_directory: Path, *, invocation_directory: Path | None,
    verified, controls, reset_from,
) -> None:
    """Verify, restore, and own prepare/run/cleanup for generated BPF output."""

    output = Path(output_directory).resolve()
    invocation = (output / "runs" / f"run-{uuid4().hex}" if invocation_directory is None
                  else Path(invocation_directory).resolve())
    invocation_root = (output / "runs").resolve()
    if invocation.parent != invocation_root or not invocation.name:
        raise ValueError("runtime invocation directory must be one owned run root")
    runner_evidence = invocation / "runner"
    traffic_evidence = invocation / "traffic"
    runner_evidence.mkdir(parents=True, exist_ok=True)
    traffic_evidence.mkdir(parents=True, exist_ok=True)
    invocation_id = invocation.name
    try:
        _write_runtime_invocation_index(
            invocation_root,
            invocation_id=invocation_id,
            status="running",
        )
    except BaseException as error:
        print(
            "runtime invocation index side work failed without replacing "
            f"domain execution: {error}",
            file=sys.stderr,
        )
    controls["invocation_id"] = invocation_id
    (output / ".control").mkdir(exist_ok=True)
    def lifecycle(event, **facts):
        _append_jsonl(output / ".control/runtime-events.jsonl",
            {"event": event, "invocation_id": invocation_id, "reset_from": reset_from,
             "monotonic_seconds": perf_counter(), **facts})
    lifecycle("runtime_invocation_start")
    definition, artifact_path, artifact_snapshot = verified
    traffic_enabled = definition.get("traffic_enabled", True)
    TrafficProbeExecutor = read_traffic_probe_plan = None
    probe_plan = None
    if traffic_enabled:
        try:
            from tools.generation.traffic.probe_execution import TrafficProbeExecutor
            from tools.generation.traffic.probe_plan import read_traffic_probe_plan
        except ImportError as error:
            raise RuntimeError("Traffic is enabled but its Tool is unavailable") from error
        probe_plan_path = output / "results/traffic/probe-plan.json"
        if not probe_plan_path.is_file():
            raise RuntimeError("generated Traffic probe plan is missing")
        probe_plan = read_traffic_probe_plan(probe_plan_path)
    project_name = str(definition["generated_output_identity"])
    runner = Runner(
        compose_project_name=project_name,
        runtime_artifact=artifact_path,
        forwarding_backend=definition["backend"],
        **{key: definition["runner"][key] for key in ("batch_lead_fraction", "batch_capacity_factor", "keep_alive")
           if key in definition["runner"]},
    )
    if definition["format_version"] >= 4:
        runner._bind_execution_input(physical_world_fingerprint=artifact_snapshot["physical_world_fingerprint"],
            start_time=artifact_snapshot["timeline"]["start"], end_time=artifact_snapshot["timeline"]["end"])
    runner._enable_event_history(
        runner_evidence / "runner-events.jsonl"
    )
    if definition.get("traffic_telemetry_enabled", traffic_enabled and definition["backend"] == "bpf"):
        runner._enable_traffic_telemetry(traffic_evidence)
    connection_observer = None
    if (output / '.control/internet-map').is_dir():
        from skyseed_v2.backend.runner.connection_observer import _ConnectionObserver
        connection_observer = _ConnectionObserver(output / '.control/internet-map',
            invocation=invocation_id, backend=definition['backend'])
        runner._connection_observer = connection_observer
    controls["runner"] = runner
    if controls.get("pause", False):
        runner.pause()
    controls["traffic"] = None
    if controls["cancel"].is_set() or controls["reset"].is_set():
        runner.cancel_runtime()
    if reset_from is not None:
        runner._record("runtime_reset", invocation_id=invocation_id, reset_from=reset_from,
                       replay_from=definition["simulation"]["start_time"], deployment_reused=True)
    runtime_observer = _ExecutionObserver(
        {
            "scope": "generated-runtime",
            "invocation_id": invocation_id,
            "realization_count": artifact_snapshot["realization_count"],
            "boundary_count": artifact_snapshot["boundary_count"],
        }
    )

    primary_error: BaseException | None = None
    traffic_error: BaseException | None = None
    cleanup_error: BaseException | None = None
    traffic_executor: TrafficProbeExecutor | None = None
    runner_started = perf_counter()
    traffic_started: float | None = None
    delivery_stop = Event()
    delivery_thread = None
    delivery_failures = []

    def deliver():
        # Experiment owns the future references. Read an append-only catalog
        # cursor, keeping just the next reference decoded; Runner has one successor.
        stream = None
        try:
            rolling = definition["runner"].get("keep_alive", False)
            pending = None
            finite = iter(definition.get("runner_batches", ()))
            if rolling:
                stream = (output / "results/compiler/materials.jsonl").open()
                stream.readline()  # Initial materials were prepared above.
            while not delivery_stop.is_set():
                if pending is None:
                    if rolling:
                        line = stream.readline()
                        pending = json.loads(line) if line else None
                    else:
                        pending = next(finite, None)
                        if pending is None:
                            return
                if pending is not None and runner.status()["accepts_batch"]:
                    if traffic_executor is not None and "traffic" in pending:
                        if not traffic_executor.snapshot()["accepts_plan"]:
                            delivery_stop.wait(0.05)
                            continue
                        traffic_executor.append_plan(read_traffic_probe_plan(output / pending["traffic"]))
                    if not runner.append_batch(output / pending["manifest"]):
                        return
                    controls["delivery_count"] += 1
                    pending = None
                delivery_stop.wait(0.05)
        except BaseException as error:
            delivery_failures.append(error)
            controls["delivery_error"] = f"{type(error).__name__}: {error}"
            runner.cancel_runtime()
        finally:
            if stream is not None:
                stream.close()
    try:
        if invocation_id is not None:
            try:
                _record_container_checkpoint(output, runner_evidence / "containers-before-preparation.json", project_name)
            except Exception as error:
                print(f"container observation degraded: {error}", file=sys.stderr)
        runner.prepare()
        if definition.get('initial_readiness_command') is not None:
            _run_initial_readiness(output, invocation, definition, runner, controls, lifecycle, runtime_observer)
        _check_runtime_stop(controls)
        if traffic_enabled:
            traffic_started = perf_counter()
            traffic_executor = TrafficProbeExecutor(
                docker_executable="docker",
                plan=probe_plan,
                runner_snapshot=runner.inspection_snapshot(),
                evidence_path=(
                    traffic_evidence / "probe-execution.jsonl"
                ),
            )
            _check_runtime_stop(controls)
            traffic_executor.start(runner)
            controls["traffic"] = traffic_executor
        _check_runtime_stop(controls)
        delivery_thread = Thread(target=deliver, name="skyseed-experiment-delivery")
        delivery_thread.start()
        raw_activation_limit = os.environ.get(
            "SKYSEED_RUNNER_ACTIVATION_LIMIT"
        )
        if raw_activation_limit is None:
            _check_runtime_stop(controls)
            runner.run()
        else:
            try:
                activation_limit = int(raw_activation_limit)
            except ValueError as error:
                raise ValueError(
                    "SKYSEED_RUNNER_ACTIVATION_LIMIT must be a positive int"
                ) from error
            _check_runtime_stop(controls)
            runner._run_bounded(activation_limit)
    except _ReadinessCancelled:
        pass  # Reset/cancel returns through normal cleanup and the existing loop.
    except BaseException as error:
        primary_error = error
    _console("Runtime cleanup starting: draining delivery, Traffic and Runner resources.")
    delivery_stop.set()
    if delivery_thread is not None:
        delivery_thread.join()
    if delivery_failures and primary_error is None:
        primary_error = delivery_failures[0]
    if traffic_executor is not None:
        try:
            traffic_executor.stop()
            lifecycle("traffic_shutdown_complete")
        except BaseException as error:
            traffic_error = error
            if primary_error is None:
                primary_error = error
            else:
                primary_error.add_note(
                    "secondary Traffic probe execution failure: "
                    f"{type(error).__name__}: {error}"
                )
    if traffic_started is not None:
        record_performance(runtime_observer,
            component="traffic",
            stage="traffic-total",
            phase="one-shot",
            duration_seconds=perf_counter() - traffic_started,
            duration_role="rollup",
            scope={"invocation_id": invocation_id},
            work={
                "probe_episodes": len(probe_plan.episodes),
                "completed": int(traffic_error is None),
            },
            started_monotonic=traffic_started,
        )
    try:
        if invocation_id is not None:
            _record_container_checkpoint(output, runner_evidence / "containers-before-cleanup.json", project_name)
    except Exception as error:
        print(f"container observation degraded: {error}", file=sys.stderr)
        if primary_error is not None:
            primary_error.add_note(f"container checkpoint also failed: {type(error).__name__}: {error}")
    try:
        runner.cleanup()
        lifecycle("runner_cleanup_complete")
    except BaseException as error:
        cleanup_error = error
    finally:
        if connection_observer is not None:
            try:
                connection_observer.close()
            except BaseException as error:
                if cleanup_error is None:
                    cleanup_error = error
                else:
                    cleanup_error.add_note(f"connection observer cleanup also failed: {type(error).__name__}: {error}")
    if cleanup_error is not None or traffic_error is not None:
        failure = cleanup_error if cleanup_error is not None else traffic_error
        _console(f"Runtime resource cleanup failed: {type(failure).__name__}: {failure}")
    else:
        _console("Runtime resource cleanup succeeded. Deployment containers remain until cleanup.sh.")
    record_performance(runtime_observer,
        component="runner",
        stage="runner-total",
        phase="one-shot",
        duration_seconds=perf_counter() - runner_started,
        duration_role="rollup",
        scope={"invocation_id": invocation_id},
        work={
            "precomputed_boundaries": artifact_snapshot["boundary_count"],
            "completed": int(
                primary_error is None and cleanup_error is None
            ),
        },
        started_monotonic=runner_started,
    )
    try:
        _write_runtime_execution_observations(
            output,
            runner_evidence.parent
            / "performance/execution-observations.jsonl",
            runtime_observer,
        )
    except BaseException as error:
        print(
            "runtime Observation side work failed without replacing domain "
            f"execution: {error}",
            file=sys.stderr,
        )

    try:
        _write_runner_inspection_handoff(
            runner_evidence / "runner-invocation.json",
            runner.inspection_snapshot(),
            _outcome(primary_error, cleanup_error, traffic_error),
            **(
                {}
                if invocation_id is None
                else {"invocation_id": invocation_id}
            ),
        )
    except BaseException as error:
        print(
            "generated BPF inspection handoff side work failed without "
            f"replacing domain execution: {error}",
            file=sys.stderr,
        )
    if invocation_id is not None:
        try:
            _write_runtime_invocation_index(
                (output / "runs").resolve(),
                invocation_id=invocation_id,
                status=(
                    "completed"
                    if primary_error is None and cleanup_error is None
                    else "failed"
                ),
            )
        except BaseException as error:
            print(
                "runtime invocation index side work failed without replacing "
                f"domain execution: {error}",
                file=sys.stderr,
            )
    if primary_error is not None:
        if cleanup_error is not None:
            primary_error.add_note(f"Runner cleanup also failed: {type(cleanup_error).__name__}: {cleanup_error}")
        raise primary_error
    if cleanup_error is not None:
        raise cleanup_error


def _record_container_checkpoint(output, destination, project):
    """Capture bounded terminal identity evidence before Runner reclamation."""
    from yaml import safe_load
    from skyseed_v2.deployment.deployer import _inspect_startup_services
    started = perf_counter()
    try:
        services = safe_load((output / "deployment/docker-compose.yml").read_text())["services"]
        containers = _inspect_startup_services(project, services, tuple(services))
        data = {"status": "COMPLETE", "project": project, "containers": [
            {"id": c["Id"], "service": c["Config"]["Labels"]["com.docker.compose.service"],
             "image": c["Image"], "state": c["State"], "restart_count": c["RestartCount"]}
            for c in containers]}
    except Exception as error:
        data = {"status": "UNAVAILABLE", "project": project, "error": f"{type(error).__name__}: {error}"}
    data["duration_seconds"] = perf_counter() - started
    destination.write_text(json.dumps(data, sort_keys=True) + "\n")


def _write_runtime_execution_observations(output, destination, runtime_observer):
    """Each invocation owns only its records and references generation once."""
    generation = Path(output) / "results/experiment/generation-performance.jsonl"
    runtime_observer._metadata["generation_reference"] = os.path.relpath(generation, Path(destination).parent)
    return runtime_observer.write_jsonl(destination)


def _write_runner_inspection_handoff(
    path: Path,
    snapshot: Mapping[str, object],
    outcome: Mapping[str, object],
    *,
    invocation_id: str | None = None,
) -> None:
    """Persist detached inputs for the repository-local Runner Tool."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(
            {
                "format_version": 1,
                "artifact_type": "runner-inspection-handoff",
                "invocation_id": invocation_id,
                "runner_snapshot": _semantic_value(snapshot),
                "runtime_outcome": _semantic_value(outcome),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_runtime_invocation_index(
    root: Path,
    *,
    invocation_id: str,
    status: str,
) -> None:
    """Own immutable raw evidence by run and update only a derived latest view."""

    if status not in {"running", "completed", "failed"}:
        raise ValueError("runtime invocation status is invalid")
    invocation_root = Path(root).resolve()
    directory = invocation_root / invocation_id
    directory.mkdir(parents=True, exist_ok=True)
    record_path = directory / "invocation.json"
    existing: dict[str, object] = {}
    if record_path.is_file():
        value = json.loads(record_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("invocation_id") != invocation_id:
            raise ValueError("runtime invocation identity differs")
        existing = value
    now = datetime.now().astimezone().isoformat()
    record = {
        "format_version": 1,
        "artifact_type": "skyseed-runtime-invocation",
        "invocation_id": invocation_id,
        "status": status,
        "started_at": existing.get("started_at", now),
        "ended_at": None if status == "running" else now,
        "raw_evidence": {
            "runner_events": "runner/runner-events.jsonl",
            "runner_outcome": "runner/runner-invocation.json",
            "traffic_execution": "traffic/probe-execution.jsonl",
            "traffic_telemetry": "traffic/traffic-telemetry.jsonl",
            "resource_samples": "runner/runner-events.jsonl",
        },
    }
    _write_json_file_atomic(record_path, record)
    _write_json_file_atomic(
        invocation_root / "latest.json",
        {
            "format_version": 1,
            "artifact_type": "skyseed-runtime-latest-view",
            "invocation_id": invocation_id,
            "status": status,
            "invocation": f"{invocation_id}/invocation.json",
        },
    )


def _write_json_file_atomic(path: Path, value: Mapping[str, object]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
    try:
        temporary.write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _read_and_verify_execution(
    output: Path,
) -> tuple[dict[str, Any], Path, dict[str, Any]]:
    path = output / "results/compiler/execution.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(
            f"generated execution definition is unavailable: {path}"
        ) from error
    if not isinstance(value, dict):
        raise RuntimeError("generated execution definition must be an object")
    if value.get("format_version") not in (5, _FORMAT_VERSION) or value.get("kind") != _KIND:
        raise RuntimeError("generated execution definition is unsupported")
    if value['format_version'] == 5:
        # The actual previous format has no readiness handoff.
        value.pop('initial_readiness_command', None)
    else:
        _validate_readiness_command(value.get('initial_readiness_command'))
    backend = _backend(value.get("backend"))
    if backend == "linux" and value.get("traffic_telemetry_enabled", False):
        raise RuntimeError("Linux execution cannot collect BPF packet telemetry")
    simulation = _mapping(value.get("simulation"), "simulation")
    start_time = _aware_datetime(simulation.get("start_time"), "start_time")
    end_time = _aware_datetime(simulation.get("end_time"), "end_time")
    if simulation.get("bounded") is not True or end_time < start_time:
        raise RuntimeError("generated execution Timeline range is invalid")
    if _number(
        simulation.get("observation_interval_seconds"),
        "observation_interval_seconds",
    ) <= 0:
        raise RuntimeError("generated execution observation interval is invalid")
    runner = _mapping(value.get("runner"), "runner")
    if (runner.get("execution_contract") != "sealed-precomputed-only"
        or set(runner) - {"execution_contract", "batch_lead_fraction", "batch_capacity_factor", "keep_alive"}):
        raise RuntimeError("generated Runner execution contract is unsupported")
    progression = _mapping(value.get("progression"), "progression")
    if (
        progression.get("strategy") != "sealed-precomputed-runner-artifact"
        or not isinstance(progression.get("producer"), str)
        or not progression.get("producer")
    ):
        raise RuntimeError("generated precomputed progression identity is invalid")
    handoff = _mapping(value.get("runner_artifact"), "runner_artifact")
    relative_manifest = _safe_relative_path(
        handoff.get("manifest"), "Runner artifact manifest"
    )
    manifest_path = output / relative_manifest
    # Runner's real reader validates material format, binary seals, nodes and
    # lineage during admission. These are reference facts, not a second reader.
    from skyseed_v2.backend.execution_materials.codec import _FORMAT_VERSION as material_version
    if handoff.get("format_version") != material_version:
        raise RuntimeError("execution materials are unsupported; regenerate the experiment")
    # Verify the small backend identity before deployment; Runner owns full binary validation.
    material = json.loads(manifest_path.read_text())
    if material.get("backend") != backend:
        raise RuntimeError("execution definition and materials select different forwarding backends")
    snapshot = dict(handoff)
    project_name = _read_compose_project_name(output)
    if value.get("generated_output_identity") != project_name:
        raise RuntimeError("generated output identity does not match Compose controls")
    return value, manifest_path, snapshot


def _read_and_verify_execution_definition(output: Path) -> dict[str, Any]:
    """Verify a generated definition without starting the live Runner."""

    definition, _, _ = _read_and_verify_execution(output)
    return definition

def _safe_relative_path(value: object, label: str) -> str:
    raw = _single_line(value, label)
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts or raw in {".", ""}:
        raise RuntimeError(f"generated execution {label} is invalid")
    return path.as_posix()


def _read_compose_project_name(output: Path) -> str:
    try:
        value = (output / _PROJECT_NAME_FILE).read_text(encoding="utf-8").strip()
    except OSError as error:
        raise RuntimeError("generated Compose project identity is missing") from error
    if _PROJECT_NAME_PATTERN.fullmatch(value) is None:
        raise RuntimeError("generated Compose project identity is invalid")
    return value


def _outcome(
    primary_error: BaseException | None,
    cleanup_error: BaseException | None,
    traffic_error: BaseException | None = None,
) -> dict[str, object]:
    if isinstance(primary_error, KeyboardInterrupt):
        status, exit_code = "interrupted", 130
    elif primary_error is not None:
        status, exit_code = "failed", 1
    elif cleanup_error is not None:
        status = "cleanup-failed"
        exit_code = 130 if isinstance(cleanup_error, KeyboardInterrupt) else 1
    else:
        status, exit_code = "completed", 0
    return {
        "status": status,
        "exit_code": exit_code,
        "primary_error": _error_fact(primary_error),
        "traffic": {
            "status": "failed" if traffic_error is not None else "completed",
            "error": _error_fact(traffic_error),
        },
        "cleanup": {
            "status": "failed" if cleanup_error is not None else "completed",
            "error": _error_fact(cleanup_error),
        },
    }


def _error_fact(error: BaseException | None) -> dict[str, str] | None:
    return None if error is None else {
        "type": type(error).__name__,
        "message": str(error),
        **({"notes": list(error.__notes__)} if getattr(error, "__notes__", None) else {}),
    }


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"generated execution {label} must be an object")
    return value


def _single_line(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or "\n" in value or "\r" in value:
        raise RuntimeError(f"generated execution {label} is invalid")
    return value


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(f"generated execution {label} must be numeric")
    return float(value)


def _aware_datetime(value: object, label: str) -> datetime:
    raw = _single_line(value, label)
    try:
        result = datetime.fromisoformat(raw)
    except ValueError as error:
        raise RuntimeError(f"generated execution {label} is invalid") from error
    if result.tzinfo is None or result.utcoffset() is None:
        raise RuntimeError(f"generated execution {label} must be timezone-aware")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--invocation-directory", type=Path)
    arguments = parser.parse_args()
    try:
        _run_generated_bpf_experiment(
            arguments.output,
            invocation_directory=arguments.invocation_directory,
        )
    except KeyboardInterrupt:
        _console("Runner interrupted.")
        return 130
    except Exception as error:
        _console(f"Runner failed: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
