"""Validate and execute one generated Skyseed experiment runtime package."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from datetime import datetime, timedelta
from hashlib import sha256
import importlib
from importlib import metadata, util
import json
import os
from pathlib import Path
import platform
import re
from shlex import quote
import sys
from threading import RLock
from typing import Any

from skyseed_v2.simulator import Plan, Planner, Simulator, Slicer
from skyseed_v2.shared._durable_evidence import (
    _append_jsonl,
    _reset_jsonl,
    _semantic_value,
)

from ..backend.bpf.compiler import compile_bpf
from ..backend.bpf.runner import BpfRunner
from ..backend.bpf.compiled_realization import BpfCompiledRealization
from ._realization_handoff import (
    _PersistedRealizationSupplier,
    _read_persisted_realization_sequence,
)
from .traffic.probe_execution import TrafficProbeExecutor
from .traffic.probe_plan import (
    TrafficProbePlanBuilder,
    read_traffic_probe_plan,
    write_traffic_probe_plan,
)


_FORMAT_VERSION = 1
_KIND = "skyseed-v2-generated-bpf-execution"
_PROJECT_NAME_FILE = "runtime/compose-project-name"
_PROJECT_NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]*")


class _RollingRealizationSupplier:
    """Compose one bounded-memory Slicer→Planner→Compiler pull seam."""

    def __init__(
        self,
        slicer: Slicer,
        planner: Planner,
        *,
        generation_lookahead: timedelta,
        compiled_history_path: Path,
        final_slice_id: int | None = None,
        include_compiled_semantics: bool = True,
    ) -> None:
        if not isinstance(slicer, Slicer):
            raise TypeError("slicer must be a Slicer")
        if not isinstance(planner, Planner):
            raise TypeError("planner must be a Planner")
        if (
            not isinstance(generation_lookahead, timedelta)
            or generation_lookahead <= timedelta(0)
        ):
            raise ValueError("generation_lookahead must be a positive timedelta")
        self._slicer = slicer
        self._planner = planner
        self._generation_lookahead = generation_lookahead
        self._compiled_history_path = _reset_jsonl(
            compiled_history_path,
            {
                "record_type": "history_header",
                "format_version": 1,
                "owner": "BPF Compiler handoff",
            },
        )
        self._lock = RLock()
        self._previous_realization: BpfCompiledRealization | None = None
        self._last_plan_identity: tuple[int, datetime | None, int, datetime] | None = None
        self._last_plan_end: datetime | None = None
        if final_slice_id is not None and (
            isinstance(final_slice_id, bool)
            or not isinstance(final_slice_id, int)
            or final_slice_id <= 0
        ):
            raise ValueError("final_slice_id must be a positive integer or None")
        self._final_slice_id = final_slice_id
        if not isinstance(include_compiled_semantics, bool):
            raise TypeError("include_compiled_semantics must be a bool")
        self._include_compiled_semantics = include_compiled_semantics
        self._latest_plan: Plan | None = None
        self._working_set_high_water = {
            "retained_slices": 0,
            "slice_timestamp_index": 0,
            "planner_differences": 0,
            "planner_route_contexts": 0,
            "planner_decisions": 0,
        }
        self._realization_count = 0
        self._input_exhausted = False

    def __call__(self) -> BpfCompiledRealization | None:
        with self._lock:
            if self._input_exhausted:
                return None
            self._generate_planning_facts()
            plan = self._planner.synchronize()
            identity = _plan_identity(plan)
            if identity == self._last_plan_identity:
                if not self._at_input_limit():
                    self._slicer.create_next_slice()
                    plan = self._planner.synchronize()
                    identity = _plan_identity(plan)
                if identity == self._last_plan_identity:
                    if not self._at_input_limit():
                        raise RuntimeError(
                            "Planner did not publish after new Slice facts"
                        )
                    self._input_exhausted = True
                    return None

            realization = compile_bpf(
                plan,
                slicer=self._slicer,
                previous=self._previous_realization,
            )
            _append_jsonl(
                self._compiled_history_path,
                {
                    "record_type": "compiled_realization",
                    "realization_index": self._realization_count,
                    "start_slice_id": realization.start_slice_id,
                    "end_slice_id": realization.end_slice_id,
                    "start_timestamp": (
                        None
                        if realization.start_timestamp is None
                        else realization.start_timestamp.isoformat()
                    ),
                    "end_timestamp": realization.end_timestamp.isoformat(),
                    "nominal_boundaries": sorted(
                        {
                            intent.nominal_time.isoformat()
                            for attribute in (
                                "resource_realizations",
                                "parameter_realizations",
                                "forwarding_realizations",
                                "zone_edge_realizations",
                            )
                            for intent in getattr(realization, attribute)
                        }
                    ),
                    **(
                        {"semantic": _semantic_value(realization)}
                        if self._include_compiled_semantics
                        else {
                            "semantic_source": (
                                "persisted canonical realization handoff"
                            )
                        }
                    ),
                },
            )
            self._previous_realization = realization
            self._last_plan_identity = identity
            self._last_plan_end = plan.end_timestamp
            self._latest_plan = plan
            self._realization_count += 1
            self._capture_working_set_high_water()
            self._planner._release_compiled_through(plan.end_slice_id)
            self._slicer._release_before(plan.end_slice_id)
            return realization

    @property
    def latest_plan(self) -> Plan:
        """Return the latest immutable publication for immediate persistence."""

        if self._latest_plan is None:
            raise RuntimeError("no realization has been produced")
        return self._latest_plan

    def _at_input_limit(self) -> bool:
        return self._slicer.is_exhausted() or (
            self._final_slice_id is not None
            and self._slicer.get_latest_slice_id() >= self._final_slice_id
        )

    def _capture_working_set_high_water(self) -> None:
        current = {
            "retained_slices": len(self._slicer.get_retained_slice_ids()),
            "slice_timestamp_index": len(self._slicer._slices_by_timestamp),
            "planner_differences": len(self._planner._differences),
            "planner_route_contexts": len(
                set(self._planner._route_next_hops)
                | set(self._planner._route_path_connection_ids)
                | set(self._planner._connection_values_by_slice_id)
            ),
            "planner_decisions": len(self._planner._decisions),
        }
        self._working_set_high_water = {
            key: max(self._working_set_high_water[key], value)
            for key, value in current.items()
        }

    def producer_snapshot(self) -> dict[str, object]:
        """Return bounded progression bookkeeping without semantic history."""

        return {
            "realization_count": self._realization_count,
            "input_exhausted": self._input_exhausted,
            "working_set_high_water": dict(self._working_set_high_water),
            "retained_at_capture": {
                "slices": len(self._slicer.get_retained_slice_ids()),
                "planner_differences": len(self._planner._differences),
                "planner_route_contexts": len(
                    set(self._planner._route_next_hops)
                    | set(self._planner._route_path_connection_ids)
                    | set(self._planner._connection_values_by_slice_id)
                ),
                "planner_decisions": len(self._planner._decisions),
            },
        }

    def _generate_planning_facts(self) -> None:
        anchor = (
            self._slicer.get_timeline().start_time
            if self._last_plan_end is None
            else self._last_plan_end
        )
        target = anchor + self._generation_lookahead
        while (
            (next_timestamp := self._slicer.get_next_timestamp()) is not None
            and next_timestamp <= target
            and (
                self._final_slice_id is None
                or self._slicer.get_latest_slice_id() < self._final_slice_id
            )
        ):
            self._slicer.create_next_slice()
def _write_execution_definition(
    output_directory: Path,
    *,
    builder_module: str | None,
    builder_callable: str | None,
    generated_output_identity: str,
    generation_lookahead: timedelta,
    preparation_lookahead: timedelta | None,
    preparation_boundary_depth: int | None,
    timeline_start: datetime,
    timeline_end: datetime | None,
    slice_interval: timedelta,
    project_root: Path,
    example_root: Path,
    realization_handoff: str | None = None,
    realization_producer: str | None = None,
) -> Path:
    """Write the sole generated BPF execution definition used by run.sh."""

    _validate_execution_parameters(
        generation_lookahead,
        preparation_lookahead,
        preparation_boundary_depth,
    )
    fingerprint = _build_source_fingerprint(project_root, example_root)
    generated_inputs = _build_generated_input_fingerprint(output_directory)
    if realization_handoff is None:
        if builder_module is None or builder_callable is None:
            raise ValueError("live generated execution requires a progression builder")
        progression = {
            "progression_builder": {
                "module": builder_module,
                "callable": builder_callable,
                "input_directory": "inputs",
            }
        }
    else:
        if not isinstance(realization_producer, str) or not realization_producer:
            raise ValueError("persisted execution requires realization producer identity")
        progression = {
            "progression": {
                "strategy": "persisted-canonical-realization-handoff",
                "producer": realization_producer,
            }
        }
    value = {
        "format_version": _FORMAT_VERSION,
        "kind": _KIND,
        "backend": "bpf",
        "generated_output_identity": generated_output_identity,
        **progression,
        **(
            {}
            if realization_handoff is None
            else {
                "realization_handoff": {
                    "manifest": _safe_relative_path(
                        realization_handoff,
                        "realization handoff manifest",
                    )
                }
            }
        ),
        "simulation": {
            "start_time": timeline_start.isoformat(),
            "end_time": None if timeline_end is None else timeline_end.isoformat(),
            "slice_interval_seconds": slice_interval.total_seconds(),
            "bounded": timeline_end is not None,
            "generation_lookahead_seconds": generation_lookahead.total_seconds(),
        },
        "runner": {
            "preparation_lookahead_seconds": (
                None
                if preparation_lookahead is None
                else preparation_lookahead.total_seconds()
            ),
            "preparation_boundary_depth": preparation_boundary_depth,
        },
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
) -> Path:
    """Write S4 runtime execution plus its thin start-then-run convenience."""

    output = Path(output_directory)
    compiler_directory = _safe_relative_path(
        compiler_tool_directory,
        "Compiler Tool directory",
    )
    tool_parent = quote(str(Path(tool_package_parent).resolve()))
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
    "$PROJECT_FILE" \\
    "$SCRIPT_DIR/runtime/execution.json" \\
    "$SCRIPT_DIR/runtime/realizations/manifest.json" \\
    "$SCRIPT_DIR/runtime/docker-compose.yml" \\
    "$SCRIPT_DIR/inputs/scenario.yaml" \\
    "$SCRIPT_DIR/inputs/shells.yaml" \\
    "$SCRIPT_DIR/inputs/ground_stations.yaml" \\
    "$SCRIPT_DIR/inputs/zones.yaml"; do
    if [[ ! -f "$required" ]]; then
        echo "generated S4 execution input is missing: $required" >&2
        exit 1
    fi
done
PROJECT_NAME="$(<"$PROJECT_FILE")"
if [[ ! "$PROJECT_NAME" =~ ^[a-z0-9][a-z0-9_-]*$ ]]; then
    echo "generated S4 Compose project identity is invalid" >&2
    exit 1
fi
if [[ ! -f "$TOOL_PACKAGE_PARENT/tools/__init__.py" ]]; then
    echo "configured Skyseed Tool package is missing: $TOOL_PACKAGE_PARENT/tools" >&2
    exit 1
fi
INVOCATION_ID="$(date -u +%Y%m%dT%H%M%S.%NZ)-${{BASHPID}}"
INVOCATION_DIR="$SCRIPT_DIR/inspection/runtime-runs/$INVOCATION_ID"
mkdir -p "$INVOCATION_DIR/runner" "$INVOCATION_DIR/traffic"
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
    --slice "$SCRIPT_DIR/inspection/slice/slice-manifest.json" \\
    --bpf-compiler "$SCRIPT_DIR/inspection/{compiler_directory}/manifest.json" \\
    --probe-plan "$SCRIPT_DIR/inspection/traffic/probe-plan.json" \\
    --probe-execution "$INVOCATION_DIR/traffic/probe-execution.jsonl" \\
    --runner-events "$INVOCATION_DIR/runner/runner-events.jsonl" \\
    --output "$SCRIPT_DIR/inspection/traffic" \\
    || traffic_tool_status=$?

aggregate_status=0
PYTHONPATH="$TOOL_PYTHONPATH" $PYTHON -c 'import importlib,sys; from pathlib import Path; importlib.import_module("tools._html").finalize_runtime_aggregate_index(Path(sys.argv[1]))' \\
    "$SCRIPT_DIR/inspection" \\
    || aggregate_status=$?

if (( execution_status != 0 )); then
    exit "$execution_status"
fi
if (( runner_tool_status != 0 )); then
    exit "$runner_tool_status"
fi
if (( traffic_tool_status != 0 )); then
    exit "$traffic_tool_status"
fi
exit "$aggregate_status"
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
        _write_runtime_invocation_index(
            invocation_root,
            invocation_id=invocation_id,
            status="running",
        )
    definition = _read_and_verify_execution_definition(output)
    simulation = _mapping(definition["simulation"], "simulation")
    runner_settings = _mapping(definition["runner"], "runner")
    handoff_value = definition.get("realization_handoff")
    probe_plan_path = output / "inspection/traffic/probe-plan.json"
    if handoff_value is None:
        if not probe_plan_path.is_file():
            raise RuntimeError(
                "generated rolling execution lacks its derived Traffic probe plan; "
                "rerun the example main.py"
            )
        slicer, planner = _construct_progression(output, definition)
        slicer._enable_durable_history(
            output / "domain-history/slice-history.jsonl.gz"
        )
        planner._enable_durable_history(
            output / "domain-history/planning-history.jsonl.gz"
        )
        supplier = _RollingRealizationSupplier(
            slicer,
            planner,
            generation_lookahead=timedelta(
                seconds=_number(
                    simulation["generation_lookahead_seconds"],
                    "generation_lookahead_seconds",
                )
            ),
            compiled_history_path=(
                output / "domain-history/compiled-history.jsonl.gz"
            ),
        )
    else:
        handoff = _mapping(handoff_value, "realization_handoff")
        relative_manifest = _safe_relative_path(
            handoff.get("manifest"), "realization handoff manifest"
        )
        manifest_path = output / relative_manifest
        if not probe_plan_path.is_file():
            planning_realizations = _read_persisted_realization_sequence(
                manifest_path
            )
            write_traffic_probe_plan(
                TrafficProbePlanBuilder().build(planning_realizations),
                probe_plan_path,
            )
            del planning_realizations
        supplier = _PersistedRealizationSupplier(manifest_path)
    probe_plan = read_traffic_probe_plan(probe_plan_path)
    project_name = str(definition["generated_output_identity"])
    raw_lookahead = runner_settings["preparation_lookahead_seconds"]
    preparation_lookahead = (
        None
        if raw_lookahead is None
        else timedelta(seconds=_number(raw_lookahead, "preparation_lookahead_seconds"))
    )
    depth = runner_settings["preparation_boundary_depth"]
    runner = BpfRunner(
        compose_project_name=project_name,
        future_realization_supplier=supplier,
        preparation_lookahead=preparation_lookahead,
        preparation_boundary_depth=(None if depth is None else int(depth)),
    )
    runner._enable_event_history(
        runner_evidence / "runner-events.jsonl"
    )
    runner._enable_traffic_telemetry(traffic_evidence)

    primary_error: BaseException | None = None
    traffic_error: BaseException | None = None
    cleanup_error: BaseException | None = None
    traffic_executor: TrafficProbeExecutor | None = None
    try:
        runner.prepare()
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
    try:
        runner.cleanup()
    except BaseException as error:
        cleanup_error = error

    handoff_error: BaseException | None = None
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
        handoff_error = error
        if primary_error is not None or cleanup_error is not None:
            print(
                "generated BPF inspection handoff failed without replacing "
                f"the runtime error: {error}",
                file=sys.stderr,
            )
    if invocation_id is not None:
        _write_runtime_invocation_index(
            (output / "inspection/runtime-runs").resolve(),
            invocation_id=invocation_id,
            status=(
                "completed"
                if primary_error is None
                and cleanup_error is None
                and handoff_error is None
                else "failed"
            ),
        )
    if primary_error is not None:
        raise primary_error
    if cleanup_error is not None:
        raise cleanup_error
    if handoff_error is not None:
        raise handoff_error
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


def _read_and_verify_execution_definition(output: Path) -> dict[str, Any]:
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
    runner = _mapping(value.get("runner"), "runner")
    raw_generation_lookahead = _number(
        simulation.get("generation_lookahead_seconds"),
        "generation_lookahead_seconds",
    )
    raw_preparation_lookahead = runner.get("preparation_lookahead_seconds")
    preparation_lookahead = (
        None
        if raw_preparation_lookahead is None
        else timedelta(
            seconds=_number(
                raw_preparation_lookahead,
                "preparation_lookahead_seconds",
            )
        )
    )
    depth = runner.get("preparation_boundary_depth")
    if depth is not None and (
        isinstance(depth, bool) or not isinstance(depth, int)
    ):
        raise RuntimeError("generated execution boundary depth is invalid")
    try:
        _validate_execution_parameters(
            timedelta(seconds=raw_generation_lookahead),
            preparation_lookahead,
            depth,
        )
    except (TypeError, ValueError) as error:
        raise RuntimeError("generated BPF execution bounds are invalid") from error
    start_time = _aware_datetime(simulation.get("start_time"), "start_time")
    end_value = simulation.get("end_time")
    end_time = (
        None if end_value is None else _aware_datetime(end_value, "end_time")
    )
    bounded = simulation.get("bounded")
    if not isinstance(bounded, bool) or bounded is not (end_time is not None):
        raise RuntimeError("generated execution Timeline boundedness is invalid")
    if end_time is not None and end_time < start_time:
        raise RuntimeError("generated execution Timeline range is invalid")
    if end_time is None and preparation_lookahead is None:
        raise RuntimeError(
            "unbounded generated execution requires rolling preparation bounds"
        )
    if _number(simulation.get("slice_interval_seconds"), "slice_interval_seconds") <= 0:
        raise RuntimeError("generated execution Slice interval is invalid")
    handoff_value = value.get("realization_handoff")
    if handoff_value is None:
        _mapping(value.get("progression_builder"), "progression_builder")
    else:
        progression = _mapping(value.get("progression"), "progression")
        if (
            progression.get("strategy")
            != "persisted-canonical-realization-handoff"
            or not isinstance(progression.get("producer"), str)
        ):
            raise RuntimeError("generated persisted progression identity is invalid")
        handoff = _mapping(handoff_value, "realization_handoff")
        relative_manifest = _safe_relative_path(
            handoff.get("manifest"), "realization handoff manifest"
        )
        _PersistedRealizationSupplier(output / relative_manifest)
    project_name = _read_compose_project_name(output)
    if value.get("generated_output_identity") != project_name:
        raise RuntimeError("generated output identity does not match Compose controls")
    _verify_source_fingerprint(_mapping(value.get("source_fingerprint"), "source_fingerprint"))
    _verify_generated_input_fingerprint(
        output,
        _mapping(value.get("generated_inputs"), "generated_inputs"),
    )
    environment = _mapping(value.get("environment"), "environment")
    if environment.get("python_implementation") != platform.python_implementation():
        raise RuntimeError("generated output Python implementation differs; rerun main.py")
    if environment.get("python_version") != platform.python_version():
        raise RuntimeError("generated output Python version differs; rerun main.py")
    if environment.get("seedemu_distribution_version") != metadata.version("seedemu"):
        raise RuntimeError("generated output SEED version differs; rerun main.py")
    return value


def _construct_progression(
    output: Path, definition: Mapping[str, object]
) -> tuple[Slicer, Planner]:
    builder = _mapping(definition["progression_builder"], "progression_builder")
    module_name = _single_line(builder.get("module"), "builder module")
    callable_name = _single_line(builder.get("callable"), "builder callable")
    input_directory = _single_line(
        builder.get("input_directory"), "builder input_directory"
    )
    if Path(input_directory).is_absolute() or ".." in Path(input_directory).parts:
        raise RuntimeError("builder input_directory must stay within generated output")
    module = importlib.import_module(module_name)
    factory = getattr(module, callable_name, None)
    if not callable(factory):
        raise RuntimeError("generated progression builder is not callable")
    value = factory(output / input_directory)
    if (
        not isinstance(value, tuple)
        or len(value) != 2
        or not isinstance(value[0], Simulator)
        or not isinstance(value[1], Planner)
    ):
        raise TypeError("progression builder must return (Simulator, Planner)")
    simulator, planner = value
    slicer = simulator.get_slicer()
    timeline = slicer.get_timeline()
    expected = _mapping(definition["simulation"], "simulation")
    actual = {
        "start_time": timeline.start_time.isoformat(),
        "end_time": None if timeline.end_time is None else timeline.end_time.isoformat(),
        "slice_interval_seconds": timeline.slice_interval.total_seconds(),
        "bounded": timeline.is_bounded,
    }
    for key, item in actual.items():
        if expected.get(key) != item:
            raise RuntimeError(
                f"restored progression {key} differs from generated definition"
            )
    if slicer.get_latest_slice_id() != 0:
        raise RuntimeError("progression builder must not precompute Slices")
    return slicer, planner


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


def _validate_execution_parameters(
    generation_lookahead: timedelta,
    preparation_lookahead: timedelta | None,
    preparation_boundary_depth: int | None,
) -> None:
    if not isinstance(generation_lookahead, timedelta) or generation_lookahead <= timedelta(0):
        raise ValueError("generation_lookahead must be a positive timedelta")
    if preparation_lookahead is None:
        if preparation_boundary_depth is not None:
            raise ValueError("full-range execution requires no boundary depth")
        return
    if not isinstance(preparation_lookahead, timedelta) or preparation_lookahead < timedelta(0):
        raise ValueError("preparation_lookahead must be non-negative or None")
    if (
        isinstance(preparation_boundary_depth, bool)
        or not isinstance(preparation_boundary_depth, int)
        or preparation_boundary_depth <= 0
    ):
        raise ValueError("rolling execution requires a positive boundary depth")


def _plan_identity(plan: Plan) -> tuple[int, datetime | None, int, datetime]:
    return (
        plan.start_slice_id,
        plan.start_timestamp,
        plan.end_slice_id,
        plan.end_timestamp,
    )


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
