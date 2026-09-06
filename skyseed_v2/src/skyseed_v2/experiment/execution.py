"""Validate and execute one generated Skyseed experiment runtime package."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from datetime import datetime, timedelta
from hashlib import sha256
from importlib import metadata, util
import json
import os
from pathlib import Path
import platform
import re
from shlex import quote
import sys
from time import perf_counter
from typing import Any

from skyseed_v2.shared._durable_evidence import _semantic_value
from skyseed_v2.shared._execution_observation import _ExecutionObserver

from ..backend.bpf.runner import BpfRunner
from ..backend.bpf._runner_artifact import (
    _read_runner_artifact_manifest_snapshot,
)
from .traffic.probe_execution import TrafficProbeExecutor
from .traffic.probe_plan import read_traffic_probe_plan


_FORMAT_VERSION = 3
_KIND = "skyseed-v2-generated-bpf-execution"
_PROJECT_NAME_FILE = "runtime/compose-project-name"
_PROJECT_NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]*")


def _write_execution_definition(
    output_directory: Path,
    *,
    generated_output_identity: str,
    timeline_start: datetime,
    timeline_end: datetime,
    observation_interval: timedelta,
    project_root: Path,
    example_root: Path,
    runner_artifact: str,
    realization_producer: str,
) -> Path:
    """Write the persisted-only generated BPF execution definition."""

    if not isinstance(timeline_end, datetime):
        raise TypeError("timeline_end must be a datetime")
    if not isinstance(observation_interval, timedelta) or (
        observation_interval <= timedelta(0)
    ):
        raise ValueError("observation_interval must be a positive timedelta")
    if not isinstance(realization_producer, str) or not realization_producer:
        raise ValueError("realization_producer must be a non-empty identity")
    relative_manifest = _safe_relative_path(
        runner_artifact,
        "Runner artifact manifest",
    )
    manifest_path = Path(output_directory).resolve() / relative_manifest
    artifact_snapshot = _read_runner_artifact_manifest_snapshot(
        manifest_path
    )
    manifest_sha256 = sha256(manifest_path.read_bytes()).hexdigest()
    fingerprint = _build_source_fingerprint(project_root, example_root)
    generated_inputs = _build_generated_input_fingerprint(output_directory)
    value = {
        "format_version": _FORMAT_VERSION,
        "kind": _KIND,
        "backend": "bpf",
        "generated_output_identity": generated_output_identity,
        "progression": {
            "strategy": "sealed-precomputed-runner-artifact",
            "producer": realization_producer,
        },
        "runner_artifact": {
            "manifest": relative_manifest,
            "format_version": artifact_snapshot["format_version"],
            "encoding": artifact_snapshot["encoding"],
            "realization_count": artifact_snapshot["realization_count"],
            "boundary_count": artifact_snapshot["boundary_count"],
            "sequence_sha256": artifact_snapshot["sequence_sha256"],
            "manifest_sha256": manifest_sha256,
        },
        "simulation": {
            "start_time": timeline_start.isoformat(),
            "end_time": timeline_end.isoformat(),
            "observation_interval_seconds": (
                observation_interval.total_seconds()
            ),
            "bounded": True,
        },
        "runner": {"execution_contract": "sealed-precomputed-only"},
        "source_fingerprint": fingerprint,
        "generated_inputs": generated_inputs,
        "environment": {
            "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
            "seedemu_distribution_version": metadata.version("seedemu"),
        },
    }
    destination = Path(output_directory) / "runtime/execution.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return destination


def _write_generated_run_script(
    output_directory: Path,
    *,
    python_executable: str,
    compiler_tool_directory: str,
    tool_package_parent: Path,
    required_input_paths: Sequence[str] = (
        "inputs/scenario.yaml",
        "inputs/shells.yaml",
        "inputs/ground_stations.yaml",
        "inputs/zones.yaml",
    ),
    traffic_workload_path: str | None = None,
) -> Path:
    """Write generated runtime execution plus start-then-run convenience."""

    output = Path(output_directory)
    compiler_directory = _safe_relative_path(
        compiler_tool_directory,
        "Compiler Tool directory",
    )
    tool_parent = quote(str(Path(tool_package_parent).resolve()))
    if isinstance(required_input_paths, (str, bytes)):
        raise TypeError("required_input_paths must be a sequence of paths")
    required_inputs = tuple(
        _safe_relative_path(value, "generated runtime input")
        for value in required_input_paths
    )
    if not required_inputs or len(set(required_inputs)) != len(required_inputs):
        raise ValueError("generated runtime inputs must be non-empty and unique")
    required_paths = (
        "runtime/compose-project-name",
        "runtime/execution.json",
        "runtime/runner-artifact/manifest.json",
        "runtime/runner-artifact/runner-runtime.bin",
        "runtime/docker-compose.yml",
        *required_inputs,
    )
    required_lines = " \\\n".join(
        f'    "$SCRIPT_DIR/{value}"' for value in required_paths
    )
    workload_argument = ""
    if traffic_workload_path is not None:
        workload = _safe_relative_path(
            traffic_workload_path,
            "generated Traffic workload",
        )
        workload_argument = f'    --workload "$SCRIPT_DIR/{workload}" \\\n'
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
PROJECT_FILE="$SCRIPT_DIR/runtime/compose-project-name"
STARTUP_EVIDENCE="$SCRIPT_DIR/runtime/deployment-startup.json"
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
if [[ ! -f "$TOOL_PACKAGE_PARENT/tools/__init__.py" ]]; then
    echo "configured Skyseed Tool package is missing: $TOOL_PACKAGE_PARENT/tools" >&2
    exit 1
fi
INVOCATION_ID="$(date -u +%Y%m%dT%H%M%S.%NZ)-${{BASHPID}}"
INVOCATION_DIR="$SCRIPT_DIR/inspection/runtime-runs/$INVOCATION_ID"
mkdir -p "$INVOCATION_DIR/runner" "$INVOCATION_DIR/traffic" "$INVOCATION_DIR/observation"
TOOL_PYTHONPATH="$TOOL_PACKAGE_PARENT${{PYTHONPATH:+:$PYTHONPATH}}"

execution_status=0
$PYTHON -m skyseed_v2.experiment.execution "$SCRIPT_DIR" \\
    --invocation-directory "$INVOCATION_DIR" \\
    || execution_status=$?

runner_tool_status=0
runner_startup_arguments=()
if [[ -f "$STARTUP_EVIDENCE" ]]; then
    runner_startup_arguments=(--deployment-startup "$STARTUP_EVIDENCE")
fi
PYTHONPATH="$TOOL_PYTHONPATH" $PYTHON -m tools.runner \\
    --handoff "$INVOCATION_DIR/runner/runner-invocation.json" \\
    --events "$INVOCATION_DIR/runner/runner-events.jsonl" \\
    "${{runner_startup_arguments[@]}}" \\
    --output "$SCRIPT_DIR/inspection/runner" \\
    || runner_tool_status=$?

traffic_tool_status=0
PYTHONPATH="$TOOL_PYTHONPATH" $PYTHON -m tools.traffic \\
    --telemetry "$INVOCATION_DIR/traffic/traffic-telemetry.jsonl" \\
    --physical-frames "$SCRIPT_DIR/domain-history/physical-frames.jsonl" \\
    --planning "$SCRIPT_DIR/domain-history/planning.jsonl.gz" \\
    --bpf-compiler "$SCRIPT_DIR/inspection/{compiler_directory}/bpf-compiler-inspection.json" \\
{workload_argument}    --probe-plan "$SCRIPT_DIR/inspection/traffic/probe-plan.json" \\
    --probe-execution "$INVOCATION_DIR/traffic/probe-execution.jsonl" \\
    --runner-events "$INVOCATION_DIR/runner/runner-events.jsonl" \\
    --output "$SCRIPT_DIR/inspection/traffic" \\
    || traffic_tool_status=$?

observation_tool_status=0
PYTHONPATH="$TOOL_PYTHONPATH" $PYTHON -c 'import sys; from pathlib import Path; from tools.observation import write_observation_tool; write_observation_tool(Path(sys.argv[1]), Path(sys.argv[2]))' \
    "$INVOCATION_DIR/observation/execution-observations.jsonl" \
    "$SCRIPT_DIR/inspection/observation" \
    || observation_tool_status=$?

aggregate_status=0
PYTHONPATH="$TOOL_PYTHONPATH" $PYTHON -c 'import importlib,sys; from pathlib import Path; importlib.import_module("tools._html").finalize_runtime_aggregate_index(Path(sys.argv[1]))' \\
    "$SCRIPT_DIR/inspection" \\
    || aggregate_status=$?

if (( execution_status != 0 )); then
    exit "$execution_status"
fi
if (( runner_tool_status != 0 || traffic_tool_status != 0 || observation_tool_status != 0 || aggregate_status != 0 )); then
    echo "runtime completed; one or more detached inspection Tools failed" >&2
fi
exit 0
""",
        encoding="utf-8",
    )
    script.chmod(0o755)
    launch = output / "launch.sh"
    launch.write_text(
        """#!/usr/bin/env bash
set -euo pipefail

if (( $# != 0 )); then
    echo "launch.sh does not accept arguments" >&2
    exit 2
fi
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
"$SCRIPT_DIR/start.sh"
exec "$SCRIPT_DIR/run.sh"
""",
        encoding="utf-8",
    )
    launch.chmod(0o755)
    return script


def _run_generated_bpf_experiment(
    output_directory: Path,
    *,
    invocation_directory: Path | None = None,
) -> None:
    """Verify, restore, and own prepare/run/cleanup for generated BPF output."""

    output = Path(output_directory).resolve()
    if invocation_directory is None:
        runner_evidence = output / "inspection/runner"
        traffic_evidence = output / "inspection/traffic"
        invocation_id = None
    else:
        invocation = Path(invocation_directory).resolve()
        invocation_root = (output / "inspection/runtime-runs").resolve()
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
    definition, artifact_path, artifact_snapshot = _read_and_verify_execution(output)
    probe_plan_path = output / "inspection/traffic/probe-plan.json"
    if not probe_plan_path.is_file():
        raise RuntimeError(
            "generated Traffic probe plan is missing; rerun main.py"
        )
    probe_plan = read_traffic_probe_plan(probe_plan_path)
    project_name = str(definition["generated_output_identity"])
    runner = BpfRunner(
        compose_project_name=project_name,
        runtime_artifact=artifact_path,
    )
    runner._enable_event_history(
        runner_evidence / "runner-events.jsonl"
    )
    runner._enable_traffic_telemetry(traffic_evidence)
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
    try:
        runner.prepare()
        traffic_started = perf_counter()
        traffic_executor = TrafficProbeExecutor(
            docker_executable="docker",
            plan=probe_plan,
            runner_snapshot=runner.inspection_snapshot(),
            evidence_path=(
                traffic_evidence / "probe-execution.jsonl"
            ),
        )
        traffic_executor.start(runner)
        raw_activation_limit = os.environ.get(
            "SKYSEED_RUNNER_ACTIVATION_LIMIT"
        )
        if raw_activation_limit is None:
            runner.run()
        else:
            try:
                activation_limit = int(raw_activation_limit)
            except ValueError as error:
                raise ValueError(
                    "SKYSEED_RUNNER_ACTIVATION_LIMIT must be a positive int"
                ) from error
            runner._run_bounded(activation_limit)
    except BaseException as error:
        primary_error = error
    if traffic_executor is not None:
        try:
            traffic_executor.stop()
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
        runtime_observer.record(
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
        runner.cleanup()
    except BaseException as error:
        cleanup_error = error
    runtime_observer.record(
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
            / "observation/execution-observations.jsonl",
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
                (output / "inspection/runtime-runs").resolve(),
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
        raise primary_error
    if cleanup_error is not None:
        raise cleanup_error


def _write_runtime_execution_observations(
    output: Path,
    destination: Path,
    runtime_observer: _ExecutionObserver,
) -> None:
    """Combine generation and runtime observations as detached side work."""

    generation_path = (
        output / "inspection/observation/execution-observations.jsonl"
    )
    generation_header: dict[str, object] | None = None
    generation_records: list[dict[str, object]] = []
    if generation_path.is_file():
        values = [
            json.loads(line)
            for line in generation_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if not values or not isinstance(values[0], dict):
            raise ValueError("generation Observation stream is invalid")
        generation_header = values[0]
        for index, value in enumerate(values[1:]):
            if (
                not isinstance(value, dict)
                or value.get("record_type") != "execution_observation"
                or value.get("sequence") != index
            ):
                raise ValueError(
                    "generation Observation records are not continuous"
                )
            generation_records.append(value)
    runtime = runtime_observer.snapshot()
    runtime_header = runtime["header"]
    runtime_records = runtime["records"]
    assert isinstance(runtime_header, dict)
    assert isinstance(runtime_records, tuple)
    header = {
        "record_type": "execution_observation_header",
        "format_version": 2,
        "metadata": {
            "scope": "combined-generation-runtime",
            "generation": (
                None
                if generation_header is None
                else generation_header.get("metadata", {})
            ),
            "runtime": runtime_header.get("metadata", {}),
        },
    }
    records = []
    for value in (*generation_records, *runtime_records):
        copied = dict(value)
        copied["sequence"] = len(records)
        records.append(copied)
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "".join(
            json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
            for value in (header, *records)
        ),
        encoding="utf-8",
    )


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
    path = output / "runtime/execution.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(
            f"generated BPF execution definition is unavailable: {path}"
        ) from error
    if not isinstance(value, dict):
        raise RuntimeError("generated BPF execution definition must be an object")
    if value.get("format_version") != _FORMAT_VERSION or value.get("kind") != _KIND:
        raise RuntimeError("generated BPF execution definition is unsupported")
    if value.get("backend") != "bpf":
        raise RuntimeError("generated execution backend is not BPF")
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
    if runner != {"execution_contract": "sealed-precomputed-only"}:
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
    snapshot = _read_runner_artifact_manifest_snapshot(manifest_path)
    if (
        handoff.get("format_version") != snapshot["format_version"]
        or handoff.get("encoding") != snapshot["encoding"]
        or handoff.get("realization_count") != snapshot["realization_count"]
        or handoff.get("boundary_count") != snapshot["boundary_count"]
        or handoff.get("sequence_sha256") != snapshot["sequence_sha256"]
        or handoff.get("manifest_sha256")
        != sha256(manifest_path.read_bytes()).hexdigest()
    ):
        raise RuntimeError("generated Runner artifact changed; rerun main.py")
    timeline = snapshot["timeline"]
    if (
        timeline.get("start") != start_time.isoformat()
        or timeline.get("end") != end_time.isoformat()
    ):
        raise RuntimeError("generated Runner artifact Timeline differs")
    project_name = _read_compose_project_name(output)
    if value.get("generated_output_identity") != project_name:
        raise RuntimeError("generated output identity does not match Compose controls")
    _verify_source_fingerprint(
        _mapping(value.get("source_fingerprint"), "source_fingerprint")
    )
    _verify_generated_input_fingerprint(
        output, _mapping(value.get("generated_inputs"), "generated_inputs")
    )
    environment = _mapping(value.get("environment"), "environment")
    if environment.get("python_implementation") != platform.python_implementation():
        raise RuntimeError("generated output Python implementation differs; rerun main.py")
    if environment.get("python_version") != platform.python_version():
        raise RuntimeError("generated output Python version differs; rerun main.py")
    if environment.get("seedemu_distribution_version") != metadata.version("seedemu"):
        raise RuntimeError("generated output SEED version differs; rerun main.py")
    return value, manifest_path, snapshot


def _read_and_verify_execution_definition(output: Path) -> dict[str, Any]:
    """Verify a generated definition without starting the live Runner."""

    definition, _, _ = _read_and_verify_execution(output)
    return definition

def _build_source_fingerprint(
    project_root: Path, example_root: Path
) -> dict[str, object]:
    project = Path(project_root).resolve()
    example = Path(example_root).resolve()
    seed_spec = util.find_spec("seedemu")
    if seed_spec is None or not seed_spec.submodule_search_locations:
        raise RuntimeError("cannot locate installed SEED source")
    seed_root = Path(next(iter(seed_spec.submodule_search_locations))).resolve()
    scopes = [
        _source_scope("skyseed", project / "src/skyseed_v2"),
        _source_scope(
            "example-progression",
            example,
            includes=("emu.py", "simu.py"),
        ),
        _source_scope("seedemu", seed_root),
    ]
    return {
        "algorithm": "sha256",
        "digest": _scope_records_digest(scopes),
        "scopes": scopes,
    }


def _verify_source_fingerprint(value: Mapping[str, object]) -> None:
    if value.get("algorithm") != "sha256":
        raise RuntimeError("generated source fingerprint algorithm is unsupported")
    raw_scopes = value.get("scopes")
    if not isinstance(raw_scopes, list) or not raw_scopes:
        raise RuntimeError("generated source fingerprint scopes are missing")
    actual = []
    for raw in raw_scopes:
        scope = _mapping(raw, "source fingerprint scope")
        label = _single_line(scope.get("label"), "source scope label")
        root = Path(_single_line(scope.get("root"), "source scope root"))
        raw_includes = scope.get("includes")
        includes = _scope_includes(raw_includes, label=label)
        try:
            current = _source_scope(label, root, includes=includes)
        except RuntimeError as error:
            raise RuntimeError(
                f"generated execution source changed; rerun main.py: {label}"
            ) from error
        if (
            current["file_count"] != scope.get("file_count")
            or current["sha256"] != scope.get("sha256")
        ):
            raise RuntimeError(
                f"generated execution source changed; rerun main.py: {label}"
            )
        actual.append(current)
    if value.get("digest") != _scope_records_digest(actual):
        raise RuntimeError("generated source fingerprint is inconsistent; rerun main.py")


def _source_scope(
    label: str,
    root: Path,
    *,
    includes: tuple[str, ...] | None = None,
) -> dict[str, object]:
    source_root = Path(root).resolve()
    if not source_root.is_dir():
        raise RuntimeError(f"source fingerprint root is unavailable: {source_root}")
    paths = (
        tuple(source_root / relative for relative in includes)
        if includes is not None
        else tuple(
            path
            for path in sorted(source_root.rglob("*.py"))
            if not any(
                part in {"__pycache__", "output", "build"}
                for part in path.relative_to(source_root).parts
            )
        )
    )
    digest = _file_set_digest(source_root, paths)
    return {
        "label": label,
        "root": str(source_root),
        **({} if includes is None else {"includes": list(includes)}),
        "file_count": len(paths),
        "sha256": digest,
    }


def _build_generated_input_fingerprint(output_directory: Path) -> dict[str, object]:
    output = Path(output_directory).resolve()
    scopes = [
        _generated_input_scope(
            output,
            label="input-snapshots",
            relative_root="inputs",
            excludes=("traffic-workload.json",),
        ),
        _generated_input_scope(
            output,
            label="docker-compose",
            relative_root="runtime",
            includes=("docker-compose.yml",),
        ),
    ]
    return {
        "algorithm": "sha256",
        "digest": _scope_records_digest(scopes),
        "scopes": scopes,
    }


def _verify_generated_input_fingerprint(
    output: Path, value: Mapping[str, object]
) -> None:
    if value.get("algorithm") != "sha256":
        raise RuntimeError("generated input fingerprint algorithm is unsupported")
    raw_scopes = value.get("scopes")
    if not isinstance(raw_scopes, list) or not raw_scopes:
        raise RuntimeError("generated input fingerprint scopes are missing")
    actual = []
    for raw in raw_scopes:
        scope = _mapping(raw, "generated input scope")
        label = _single_line(scope.get("label"), "generated input scope label")
        relative_root = _safe_relative_path(
            scope.get("path"), "generated input scope path"
        )
        includes = _scope_includes(scope.get("includes"), label=label)
        excludes = _scope_includes(
            scope.get("excludes"), label=label, allow_none=True
        )
        try:
            current = _generated_input_scope(
                output,
                label=label,
                relative_root=relative_root,
                includes=includes,
                excludes=() if excludes is None else excludes,
            )
        except RuntimeError as error:
            raise RuntimeError(
                f"generated runtime input changed; rerun main.py: {label}"
            ) from error
        if (
            current["file_count"] != scope.get("file_count")
            or current["sha256"] != scope.get("sha256")
        ):
            raise RuntimeError(
                f"generated runtime input changed; rerun main.py: {label}"
            )
        actual.append(current)
    if value.get("digest") != _scope_records_digest(actual):
        raise RuntimeError("generated input fingerprint is inconsistent; rerun main.py")


def _generated_input_scope(
    output: Path,
    *,
    label: str,
    relative_root: str,
    includes: tuple[str, ...] | None = None,
    excludes: tuple[str, ...] = (),
) -> dict[str, object]:
    root = Path(output).resolve() / relative_root
    if not root.is_dir():
        raise RuntimeError(f"generated input root is unavailable: {root}")
    paths = (
        tuple(root / relative for relative in includes)
        if includes is not None
        else tuple(
            path
            for path in sorted(root.rglob("*"))
            if path.is_file()
            and path.relative_to(root).as_posix() not in excludes
        )
    )
    return {
        "label": label,
        "path": relative_root,
        **({} if includes is None else {"includes": list(includes)}),
        **({} if not excludes else {"excludes": list(excludes)}),
        "file_count": len(paths),
        "sha256": _file_set_digest(root, paths),
    }


def _file_set_digest(root: Path, paths: tuple[Path, ...]) -> str:
    records = []
    for path in paths:
        if not path.is_file():
            raise RuntimeError(f"fingerprinted file is unavailable: {path}")
        records.append(
            f"{path.relative_to(root).as_posix()}\0{sha256(path.read_bytes()).hexdigest()}"
        )
    return sha256("\n".join(records).encode("utf-8")).hexdigest()


def _scope_records_digest(scopes: list[Mapping[str, object]]) -> str:
    payload = "\n".join(
        f"{scope['label']}\0{scope.get('root', scope.get('path'))}\0"
        f"{scope['file_count']}\0{scope['sha256']}"
        for scope in scopes
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def _scope_includes(
    value: object,
    *,
    label: str,
    allow_none: bool = True,
) -> tuple[str, ...] | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, list) or not value:
        raise RuntimeError(f"generated fingerprint includes are invalid: {label}")
    return tuple(
        _safe_relative_path(item, f"generated fingerprint include: {label}")
        for item in value
    )


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
        print("BPF Runner interrupted", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"BPF Runner failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
