"""Build the Starlink Gen1 example through one explicitly selected domain boundary."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
from pathlib import Path
import re
import resource
import shutil
import subprocess
import sys
from time import perf_counter
from uuid import uuid4


EXAMPLE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = EXAMPLE_ROOT.parents[1]
DEFAULT_OUTPUT = EXAMPLE_ROOT / "output"
COMPOSE_PROJECT_PREFIX = "skyseed_v2_starlink_gen1_satellite_internet"
_PROJECT_NAME_FILE = "runtime/compose-project-name"
_RUNNER_PREPARATION_LOOKAHEAD = timedelta(0)
_RUNNER_PREPARATION_BOUNDARY_DEPTH = 1
_THROUGH_STAGES = ("topology", "slicer", "planner", "compiler")

if __package__ in (None, ""):
    sys.path.insert(0, str(PROJECT_ROOT))

from examples.starlink_gen1_satellite_internet.emu import (
    build_starlink_gen1_satellite_internet_emulator,
)
from examples.starlink_gen1_satellite_internet.simu import (
    STARLINK_GEN1_PLANNING_LOOKAHEAD,
    STARLINK_GEN1_REAL_SLICE_COUNT,
    STARLINK_GEN1_SLICE_INTERVAL,
    create_starlink_gen1_planner,
    create_starlink_gen1_simulator,
    starlink_gen1_planning_configuration,
    starlink_gen1_slicing_configuration,
)
from skyseed_v2.backend.bpf import BpfCompiledRealization, compile_bpf
from skyseed_v2.backend.bpf.compiler import _bind_compile_observation
from skyseed_v2.compose.launcher import (
    _InterfaceLessRuntimeDocker,
    _prepare_generated_output,
    _write_compose_build_plan,
    _write_compose_project_scripts,
)
from skyseed_v2.experiment._realization_handoff import (
    _RealizationHandoffWriter,
    _read_persisted_realization_sequence,
)
from skyseed_v2.experiment.execution import (
    _RollingRealizationSupplier,
    _write_execution_definition,
    _write_generated_run_script,
)
from skyseed_v2.experiment.traffic.probe_plan import (
    TrafficProbePlanBuilder,
    write_traffic_probe_plan,
)
from skyseed_v2.simulator import Plan, Planner, Simulator
from tools._detached_inspection import _DetachedInspectionExecutor
from tools._html import write_aggregate_index
from tools.physical_frame import write_physical_frame_tool
from tools.topology import write_topology_tool
from tools.zone import write_zone_tool


def _guard_existing_output(output: Path) -> None:
    """Never replace controls while their generated Compose project exists."""

    if not output.exists() and not output.is_symlink():
        return
    project_file = output / _PROJECT_NAME_FILE
    if not project_file.is_file():
        legacy_project_file = output / ".compose-project-name"
        if legacy_project_file.is_file():
            project_file = legacy_project_file
        elif (output / "runtime/docker-compose.yml").is_file():
            raise RuntimeError(
                "existing Starlink Gen1 runtime has no verifiable Compose identity; "
                "clean/remove it explicitly before regenerating"
            )
        else:
            return
    project_name = project_file.read_text(encoding="utf-8").strip()
    if re.fullmatch(r"[a-z0-9][a-z0-9_-]*", project_name) is None:
        raise RuntimeError("existing Starlink Gen1 output generation identity is invalid")
    try:
        result = subprocess.run(
            (
                "docker",
                "ps",
                "--all",
                "--quiet",
                "--filter",
                f"label=com.docker.compose.project={project_name}",
            ),
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as error:
        raise RuntimeError("cannot verify existing Starlink Gen1 deployment") from error
    if result.returncode != 0:
        raise RuntimeError(
            "cannot verify existing Starlink Gen1 deployment: " + result.stderr.strip()
        )
    if result.stdout.strip():
        raise RuntimeError(
            "the existing generated Starlink Gen1 deployment still has containers; "
            f"run {output / 'cleanup.sh'} before regenerating"
        )


def _write_input_snapshots(output: Path) -> None:
    inputs = output / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    for name in (
        "scenario.yaml",
        "shells.yaml",
        "ground_stations.yaml",
        "zones.yaml",
    ):
        (inputs / name).write_bytes((EXAMPLE_ROOT / name).read_bytes())


def _write_runtime_package(
    output: Path,
    emulator,
    simulator: Simulator,
    realization_staging_directory: Path,
) -> None:
    """Persist the canonical Compiler stream and static deployment package."""

    staged_manifest = realization_staging_directory / "manifest.json"
    if not staged_manifest.is_file():
        raise ValueError("Starlink Gen1 runtime package requires compiled realizations")
    compose_project_name = f"{COMPOSE_PROJECT_PREFIX}-{uuid4().hex[:16]}"
    _write_input_snapshots(output)
    emulator.render()
    emulator.compile(
        _InterfaceLessRuntimeDocker(),
        str(output / "runtime"),
    )
    project_file = output / _PROJECT_NAME_FILE
    project_file.parent.mkdir(parents=True, exist_ok=True)
    project_file.write_text(f"{compose_project_name}\n", encoding="utf-8")
    build_plan = _write_compose_build_plan(output / "runtime")
    handoff_directory = output / "runtime/realizations"
    if handoff_directory.exists():
        raise FileExistsError(f"runtime realization handoff already exists: {handoff_directory}")
    shutil.move(str(realization_staging_directory), handoff_directory)
    handoff_manifest = handoff_directory / "manifest.json"
    realizations = _read_persisted_realization_sequence(handoff_manifest)
    write_traffic_probe_plan(
        TrafficProbePlanBuilder().build(realizations),
        output / "inspection/traffic/probe-plan.json",
    )
    del realizations
    timeline = simulator.get_timeline()
    _write_execution_definition(
        output,
        builder_module=None,
        builder_callable=None,
        generated_output_identity=compose_project_name,
        generation_lookahead=STARLINK_GEN1_PLANNING_LOOKAHEAD,
        preparation_lookahead=_RUNNER_PREPARATION_LOOKAHEAD,
        preparation_boundary_depth=_RUNNER_PREPARATION_BOUNDARY_DEPTH,
        timeline_start=timeline.start_time,
        timeline_end=timeline.end_time,
        slice_interval=timeline.slice_interval,
        project_root=PROJECT_ROOT,
        example_root=EXAMPLE_ROOT,
        realization_handoff=str(
            handoff_manifest.relative_to(output).as_posix()
        ),
        realization_producer=(
            "examples.starlink_gen1_satellite_internet.main._run_dynamic_progression"
        ),
    )
    _write_compose_project_scripts(
        output,
        compose_project_name,
        startup_evidence_path="runtime/deployment-startup.json",
        build_plan_path=str(build_plan.relative_to(output).as_posix()),
        python_executable=sys.executable,
    )
    _write_generated_run_script(
        output,
        python_executable=sys.executable,
        compiler_tool_directory="compiler",
        tool_package_parent=PROJECT_ROOT,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--through",
        choices=_THROUGH_STAGES,
        default="compiler",
        help="generation/progression boundary (default: compiler)",
    )
    parser.add_argument(
        "--slice-limit",
        type=int,
        default=None,
        help=(
            "execution-only final real Slice boundary "
            f"(1..{STARLINK_GEN1_REAL_SLICE_COUNT}; default: full Timeline)"
        ),
    )
    parser.add_argument(
        "--tool-workers",
        type=int,
        default=2,
        help=(
            "bounded independent processes for detached dynamic Inspection "
            "Tools (default: 2)"
        ),
    )
    return parser


def _validated_arguments(
    parser: argparse.ArgumentParser,
    arguments: argparse.Namespace,
) -> argparse.Namespace:
    if arguments.slice_limit is not None and arguments.through == "topology":
        parser.error("--slice-limit requires --through slicer, planner, or compiler")
    if arguments.through == "topology" and arguments.tool_workers != 2:
        parser.error("--tool-workers requires a dynamic --through stage")
    if (
        arguments.slice_limit is not None
        and not 1 <= arguments.slice_limit <= STARLINK_GEN1_REAL_SLICE_COUNT
    ):
        parser.error(
            f"--slice-limit must be between 1 and {STARLINK_GEN1_REAL_SLICE_COUNT}"
        )
    if arguments.tool_workers <= 0:
        parser.error("--tool-workers must be positive")
    arguments.slice_limit = arguments.slice_limit or STARLINK_GEN1_REAL_SLICE_COUNT
    return arguments


def _planning_configuration(simulator: Simulator, observer) -> dict[str, object]:
    """Attach exact observed planning/publication windows to Tool metadata."""

    records = observer.snapshot()["records"]
    assert isinstance(records, tuple)
    return {
        **starlink_gen1_planning_configuration(simulator),
        "planning_windows": tuple(
            {
                "publication_index": record["scope"]["publication_index"],
                "planning_start_slice_id": record["scope"][
                    "planning_start_slice_id"
                ],
                "planning_end_slice_id": record["scope"][
                    "planning_end_slice_id"
                ],
                "publication_start_slice_id": record["scope"][
                    "publication_start_slice_id"
                ],
                "publication_end_slice_id": record["scope"][
                    "publication_end_slice_id"
                ],
                "start_timestamp": record["scope"].get("start_timestamp"),
                "end_timestamp": record["scope"].get("end_timestamp"),
            }
            for record in records
            if record["component"] == "planner"
            and record["stage"] == "total-synchronize"
        ),
    }


def _worker_capacity_available(executor: _DetachedInspectionExecutor) -> bool:
    active = sum(
        record.get("status") == "BUILDING" for record in executor.poll()
    )
    return active < executor.max_workers


def _submit_or_defer(
    executor: _DetachedInspectionExecutor,
    task: str,
    source: dict[str, object],
    output: Path,
    deferred_sources: dict[str, dict[str, object]],
) -> None:
    """Keep an Inspection scheduling failure outside domain progression."""

    try:
        executor.submit(task, source, output)
    except Exception:
        deferred_sources[task] = source


def _advance_slicer(
    simulator: Simulator,
    *,
    final_slice_id: int,
    through_timestamp: datetime | None = None,
) -> None:
    """Advance only Slicer, bounded by both requested ID and optional time."""

    slicer = simulator.get_slicer()
    while slicer.get_latest_slice_id() < final_slice_id:
        next_timestamp = slicer.get_next_timestamp()
        if next_timestamp is None:
            raise RuntimeError("Starlink Gen1 Timeline exhausted before the requested Slice")
        if through_timestamp is not None and next_timestamp > through_timestamp:
            break
        topology_slice = slicer.create_next_slice()
        print(
            f"Starlink Gen1 Slice S{topology_slice.slice_id}/{final_slice_id} "
            f"@ {topology_slice.timestamp.isoformat()}",
            flush=True,
        )


def _slice_source(simulator: Simulator) -> dict[str, object]:
    return {
        "slicer": simulator.get_slicer(),
        "model_context": starlink_gen1_slicing_configuration(),
    }


def _planner_source(
    simulator: Simulator,
    planner: Planner,
    plans: tuple[Plan, ...],
    observer,
) -> dict[str, object]:
    return {
        "slicer": simulator.get_slicer(),
        "plans": plans,
        "differences": planner.get_consumed_differences(),
        "decisions": planner.get_planning_decisions(),
        "configuration": _planning_configuration(simulator, observer),
    }


def _run_dynamic_progression(
    simulator: Simulator,
    *,
    through: str,
    final_slice_id: int,
    observer,
    executor: _DetachedInspectionExecutor,
    inspection: Path,
) -> dict[str, object]:
    """Run the one rolling Starlink Gen1 chain without owning any domain algorithm."""

    slicer = simulator.get_slicer()
    planner: Planner | None = None
    plans: list[Plan] = []
    realizations: list[BpfCompiledRealization] = []
    compile_evidence: list[dict[str, object]] = []
    previous_realization: BpfCompiledRealization | None = None
    deferred_sources: dict[str, dict[str, object]] = {}

    if through == "slicer":
        _advance_slicer(simulator, final_slice_id=final_slice_id)
        for current_slice_id in range(1, final_slice_id + 1):
            slicer.create_difference(current_slice_id - 1, current_slice_id)
        _submit_or_defer(
            executor,
            "slice",
            _slice_source(simulator),
            inspection / "slice",
            deferred_sources,
        )
        return {
            "planner": None,
            "plans": (),
            "realizations": (),
            "compile_evidence": (),
            "deferred_sources": deferred_sources,
        }

    while not plans or plans[-1].end_slice_id < final_slice_id:
        anchor = (
            simulator.get_timeline().start_time
            if not plans
            else plans[-1].end_timestamp
        )
        _advance_slicer(
            simulator,
            final_slice_id=final_slice_id,
            through_timestamp=anchor + STARLINK_GEN1_PLANNING_LOOKAHEAD,
        )
        if slicer.get_latest_slice_id() == final_slice_id and not any(
            record.get("task") == "slice" for record in executor.records()
        ):
            _submit_or_defer(
                executor,
                "slice",
                _slice_source(simulator),
                inspection / "slice",
                deferred_sources,
            )

        plan = (
            create_starlink_gen1_planner(simulator).get_plan()
            if planner is None
            else planner.synchronize()
        )
        if planner is None:
            planner = simulator.get_planner()
        if plans and (
            plan.start_slice_id != plans[-1].end_slice_id
            or plan.end_slice_id <= plans[-1].end_slice_id
        ):
            raise RuntimeError("Starlink Gen1 Planner did not publish a continuous progression")
        plans.append(plan)
        publication_index = len(plans) - 1
        print(
            f"Starlink Gen1 Planner P{publication_index}: "
            f"S{plan.start_slice_id}..S{plan.end_slice_id}",
            flush=True,
        )

        if plan.end_slice_id == final_slice_id:
            source = _planner_source(simulator, planner, tuple(plans), observer)
            if _worker_capacity_available(executor):
                _submit_or_defer(
                    executor,
                    "planner",
                    source,
                    inspection / "planner",
                    deferred_sources,
                )
            else:
                deferred_sources["planner"] = source

        if through == "compiler":
            with _bind_compile_observation(
                observer,
                compile_index=publication_index,
            ) as compile_observation:
                realization = compile_bpf(
                    plan,
                    slicer=slicer,
                    previous=previous_realization,
                )
            compile_seconds = compile_observation.total_duration_seconds
            realizations.append(realization)
            previous_realization = realization
            compile_evidence.append(
                {
                    "compile_index": publication_index,
                    "compile_seconds": compile_seconds,
                }
            )
            print(
                f"Starlink Gen1 Compiler C{publication_index}/R{publication_index}: "
                f"{compile_seconds:.3f}s",
                flush=True,
            )

    assert planner is not None
    if slicer.get_latest_slice_id() != final_slice_id:
        raise RuntimeError("Starlink Gen1 execution crossed the requested Slice boundary")
    if plans[-1].end_slice_id != final_slice_id:
        raise RuntimeError("Starlink Gen1 Planner did not publish through the requested Slice")
    if len(planner.get_consumed_differences()) != final_slice_id:
        raise RuntimeError("Starlink Gen1 Planner did not consume one Difference per Slice")
    if through == "compiler" and len(realizations) != len(plans):
        raise RuntimeError("Starlink Gen1 Compiler did not produce one realization per Plan")

    return {
        "planner": planner,
        "plans": tuple(plans),
        "realizations": tuple(realizations),
        "compile_evidence": tuple(compile_evidence),
        "deferred_sources": deferred_sources,
    }


def _run_canonical_realization_progression(
    simulator: Simulator,
    *,
    final_slice_id: int,
    observer,
    output: Path,
) -> dict[str, object]:
    """Drain the same bounded producer used by live rolling execution."""

    slicer = simulator.get_slicer()
    slice_history_path = output / "domain-history/slice-history.jsonl.gz"
    planner_history_path = output / "domain-history/planning-history.jsonl.gz"
    compiled_history_path = output / "domain-history/compiled-history.jsonl.gz"
    slicer._enable_durable_history(slice_history_path)
    planner = create_starlink_gen1_planner(simulator)
    planner._enable_durable_history(planner_history_path)
    supplier = _RollingRealizationSupplier(
        slicer,
        planner,
        generation_lookahead=STARLINK_GEN1_PLANNING_LOOKAHEAD,
        compiled_history_path=compiled_history_path,
        final_slice_id=final_slice_id,
        include_compiled_semantics=False,
    )
    realization_staging_directory = output / ".canonical-realizations"
    realization_writer = _RealizationHandoffWriter(realization_staging_directory)
    compile_evidence: list[dict[str, object]] = []
    publication_index = 0
    while True:
        with _bind_compile_observation(
            observer,
            compile_index=publication_index,
        ) as compile_observation:
            realization = supplier()
        if realization is None:
            break
        plan = supplier.latest_plan
        if plan.end_slice_id > final_slice_id:
            raise RuntimeError("canonical realization producer crossed requested Slice")
        realization_writer.append(realization)
        compile_seconds = compile_observation.total_duration_seconds
        compile_evidence.append(
            {
                "compile_index": publication_index,
                "compile_seconds": compile_seconds,
            }
        )
        print(
            f"Starlink Gen1 canonical P{publication_index}/C{publication_index}/"
            f"R{publication_index}: S{plan.start_slice_id}..S{plan.end_slice_id} / "
            f"{compile_seconds:.3f}s",
            flush=True,
        )
        publication_index += 1
        if plan.end_slice_id == final_slice_id:
            if supplier() is not None:
                raise RuntimeError("canonical realization producer crossed requested Slice")
            break
    if slicer.get_latest_slice_id() != final_slice_id:
        raise RuntimeError("canonical realization producer ended before requested Slice")
    return {
        "planner": planner,
        "compile_evidence": tuple(compile_evidence),
        "realization_staging_directory": realization_staging_directory,
        "realization_manifest": realization_writer.finalize(),
        "slice_history_path": slice_history_path,
        "planner_history_path": planner_history_path,
        "compiled_history_path": compiled_history_path,
        "producer_snapshot": supplier.producer_snapshot(),
    }


def _result_or_failure(
    executor: _DetachedInspectionExecutor, task: str
) -> dict[str, object]:
    result = executor.result(task)
    if result is not None:
        return result
    record = next(
        (value for value in executor.records() if value.get("task") == task),
        None,
    )
    return {
        "status": "FAILED",
        "message": (
            "detached Inspection task produced no result"
            if record is None
            else str(record.get("message", "detached Inspection task failed"))
        ),
    }


def _write_tool_execution_evidence(
    inspection: Path,
    tasks: tuple[dict[str, object], ...],
) -> Path:
    """Keep one small timing/resource/inventory record per Tool invocation."""

    records = []
    for task in tasks:
        name = str(task.get("task", "unknown"))
        directory = inspection / name
        files = tuple(path for path in directory.rglob("*") if path.is_file())
        input_evidence = task.get("input_evidence")
        input_bytes = (
            input_evidence.get("size_bytes")
            if isinstance(input_evidence, dict)
            and isinstance(input_evidence.get("size_bytes"), int)
            else 0
        )
        records.append(
            {
                key: value
                for key, value in task.items()
                if key
                in {
                    "task",
                    "status",
                    "pid",
                    "scheduled_at",
                    "started_at",
                    "ended_at",
                    "input_preparation_wall_seconds",
                    "tool_wall_seconds",
                    "wall_seconds",
                    "peak_rss_bytes",
                    "exit_code",
                    "input_evidence",
                    "error_type",
                    "message",
                }
            }
            | {
                "input_bytes": input_bytes,
                "input_byte_semantics": (
                    "detached compressed/file handoff"
                    if input_bytes
                    else (
                        "no serialized Tool input; preparation wall cost is "
                        "accounted separately"
                        if "input_preparation_wall_seconds" in task
                        else "no serialized Tool input"
                    )
                ),
                "output_file_count": len(files),
                "output_bytes": sum(path.stat().st_size for path in files),
            }
        )
    destination = inspection / "tool-executions.json"
    destination.write_text(
        json.dumps(
            {
                "format_version": 1,
                "artifact_type": "inspection-tool-executions",
                "tools": records,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return destination


def _process_group_cpu_seconds() -> float:
    own = resource.getrusage(resource.RUSAGE_SELF)
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    return (
        own.ru_utime
        + own.ru_stime
        + children.ru_utime
        + children.ru_stime
    )


def _write_generation_milestones(
    inspection: Path,
    value: dict[str, object],
) -> Path:
    destination = inspection / "generation-milestones.json"
    destination.write_text(
        json.dumps(
            {
                "format_version": 1,
                "artifact_type": "skyseed-generation-milestones",
                **value,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return destination


def _run_static_inspection_tools(
    topology,
    physical_frame_timestamp,
    inspection: Path,
) -> tuple[
    dict[str, dict[str, object]],
    dict[str, int],
    tuple[dict[str, object], ...],
]:
    results: dict[str, dict[str, object]] = {}
    records = []
    for task in ("topology", "zone", "physical-frame"):
        started_at = datetime.now().astimezone().isoformat()
        input_preparation_wall_seconds = 0.0
        tool_wall_seconds = 0.0
        try:
            physical_frame = None
            if task == "physical-frame":
                input_preparation_started = perf_counter()
                try:
                    physical_frame = topology.frame_at(
                        physical_frame_timestamp
                    )
                finally:
                    input_preparation_wall_seconds = (
                        perf_counter() - input_preparation_started
                    )

            tool_started = perf_counter()
            try:
                if task == "topology":
                    result = dict(
                        write_topology_tool(topology, inspection / task)
                    )
                elif task == "zone":
                    result = dict(
                        write_zone_tool(topology, inspection / task)
                    )
                else:
                    result = dict(
                        write_physical_frame_tool(
                            topology,
                            physical_frame,
                            inspection / task,
                        )
                    )
            finally:
                tool_wall_seconds = perf_counter() - tool_started
            status = "COMPLETE"
            results[task] = result
            message = None
        except Exception as error:
            status = "FAILED"
            message = f"{type(error).__name__}: {error}"
            results[task] = {"status": "FAILED", "message": message}
        record = {
            "task": task,
            "status": status,
            "started_at": started_at,
            "ended_at": datetime.now().astimezone().isoformat(),
            "input_preparation_wall_seconds": (
                input_preparation_wall_seconds
            ),
            "tool_wall_seconds": tool_wall_seconds,
            "wall_seconds": (
                input_preparation_wall_seconds + tool_wall_seconds
            ),
            "peak_rss_bytes": int(
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            )
            * 1024,
        }
        if message is not None:
            record["message"] = message
        records.append(record)
    topology_result = results["topology"]
    counts = {
        key: int(value)
        for key, value in dict(topology_result.get("counts", {})).items()
    }
    return results, counts, tuple(records)


def main(argv: list[str] | None = None) -> int:
    command_started = perf_counter()
    command_started_at = datetime.now().astimezone().isoformat()
    parser = _parser()
    arguments = _validated_arguments(parser, parser.parse_args(argv))

    output = DEFAULT_OUTPUT
    _guard_existing_output(output)
    _prepare_generated_output(output, replace=True)
    emulator = build_starlink_gen1_satellite_internet_emulator()
    simulator = create_starlink_gen1_simulator(emulator)
    topology = simulator.get_topology()
    inspection = output / "inspection"

    results: dict[str, dict[str, object]] = {}
    counts: dict[str, int] = {}
    static_tasks: tuple[dict[str, object], ...] = ()

    if arguments.through == "topology":
        results, counts, static_tasks = _run_static_inspection_tools(
            topology,
            simulator.get_timeline().start_time,
            inspection,
        )
        _write_tool_execution_evidence(inspection, static_tasks)

    if arguments.through != "topology":
        control_directory = output / ".inspection-control"
        observation_path = inspection / "observation/execution-observations.jsonl"
        observer = simulator._enable_execution_observation(
            observation_path,
            metadata={
                "scenario_identity": "starlink_gen1_satellite_internet",
                "timeline_start": simulator.get_timeline().start_time.isoformat(),
                "timeline_end": simulator.get_timeline().end_time.isoformat(),
                "slice_interval_seconds": STARLINK_GEN1_SLICE_INTERVAL.total_seconds(),
                "configured_real_slice_count": STARLINK_GEN1_REAL_SLICE_COUNT,
                "execution_through_slice_id": arguments.slice_limit,
                "through": arguments.through,
                "progression": "rolling-slicer-planner-compiler",
            },
        )
        executor = _DetachedInspectionExecutor(
            control_directory,
            max_workers=arguments.tool_workers,
        )
        progression: dict[str, object] = {}
        try:
            if arguments.through == "compiler":
                domain_started = perf_counter()
                domain_cpu_started = _process_group_cpu_seconds()
                domain_started_at = datetime.now().astimezone().isoformat()
                progression = _run_canonical_realization_progression(
                    simulator,
                    final_slice_id=arguments.slice_limit,
                    observer=observer,
                    output=output,
                )
                domain_completed = perf_counter()
                domain_completed_at = datetime.now().astimezone().isoformat()
                domain_cpu_seconds = (
                    _process_group_cpu_seconds() - domain_cpu_started
                )
                compile_evidence = progression["compile_evidence"]
                staging = progression["realization_staging_directory"]
                assert isinstance(compile_evidence, tuple)
                assert isinstance(staging, Path)
                _write_runtime_package(output, emulator, simulator, staging)
                runtime_ready = perf_counter()
                runtime_ready_at = datetime.now().astimezone().isoformat()
                print(
                    "Starlink Gen1 runtime handoff ready; Inspection Tools are side work",
                    flush=True,
                )
            else:
                progression = _run_dynamic_progression(
                    simulator,
                    through=arguments.through,
                    final_slice_id=arguments.slice_limit,
                    observer=observer,
                    executor=executor,
                    inspection=inspection,
                )
        except BaseException:
            executor.terminate()
            raise

        inspection_failures: dict[str, str] = {}
        observation_persisted = False
        try:
            simulator._write_execution_observations()
        except Exception as error:
            inspection_failures["observation"] = (
                f"{type(error).__name__}: {error}"
            )
        else:
            observation_persisted = True

        static_results, counts, static_tasks = _run_static_inspection_tools(
            topology,
            simulator.get_timeline().start_time,
            inspection,
        )
        results.update(static_results)

        deferred = progression.get("deferred_sources", {})
        assert isinstance(deferred, dict)

        def schedule(task: str, source: dict[str, object]) -> None:
            try:
                executor.submit(task, source, inspection / task)
            except Exception as error:
                inspection_failures[task] = (
                    f"{type(error).__name__}: {error}"
                )

        for task, source in deferred.items():
            schedule(task, source)
        if arguments.through == "compiler":
            durable_source = {
                "slice_history_path": progression["slice_history_path"],
                "planner_history_path": progression["planner_history_path"],
                "topology": topology,
                "timeline": simulator.get_timeline(),
            }
            schedule(
                "slice",
                {
                    **durable_source,
                    "model_context": starlink_gen1_slicing_configuration(),
                },
            )
            schedule(
                "planner",
                {
                    **durable_source,
                    "configuration": _planning_configuration(simulator, observer),
                },
            )
            schedule(
                "compiler",
                {
                    "planner_history_path": progression["planner_history_path"],
                    "realization_manifest": (
                        output / "runtime/realizations/manifest.json"
                    ),
                    "compile_evidence": compile_evidence,
                },
            )
        if observation_persisted:
            schedule(
                "observation",
                {"observation_path": observation_path},
            )
        print(
            "Starlink Gen1 domain chain complete; joining detached Inspection workers",
            flush=True,
        )
        try:
            tasks = executor.join()
        except Exception as error:
            executor.terminate()
            inspection_failures["inspection-join"] = (
                f"{type(error).__name__}: {error}"
            )
            tasks = executor.records()
        tasks = (
            *tasks,
            *(
                {
                    "task": task,
                    "status": "FAILED",
                    "message": message,
                }
                for task, message in sorted(inspection_failures.items())
            ),
        )
        _write_tool_execution_evidence(
            inspection,
            (*static_tasks, *tasks),
        )
        if arguments.through == "compiler":
            tool_completed = perf_counter()
            _write_generation_milestones(
                inspection,
                {
                    "command_started_at": command_started_at,
                    "domain_started_at": domain_started_at,
                    "domain_completed_at": domain_completed_at,
                    "runtime_handoff_ready_at": runtime_ready_at,
                    "tool_completed_at": datetime.now().astimezone().isoformat(),
                    "domain_wall_seconds": domain_completed - domain_started,
                    "domain_process_group_cpu_seconds": domain_cpu_seconds,
                    "runtime_handoff_ready_seconds": (
                        runtime_ready - command_started
                    ),
                    "runtime_packaging_seconds": runtime_ready - domain_completed,
                    "tool_side_work_seconds": tool_completed - runtime_ready,
                    "tool_complete_seconds": tool_completed - command_started,
                    "producer": progression["producer_snapshot"],
                },
            )

        requested_tasks = ["slice", "observation"]
        if arguments.through in {"planner", "compiler"}:
            requested_tasks.append("planner")
        if arguments.through == "compiler":
            requested_tasks.append("compiler")
        for task in requested_tasks:
            results[task] = (
                {
                    "status": "FAILED",
                    "message": inspection_failures[task],
                }
                if task in inspection_failures
                else _result_or_failure(executor, task)
            )
    write_aggregate_index(inspection, results, counts)
    print(f"Inspection index: {inspection / 'index.html'}")
    for task, title in (
        ("topology", "Topology Tool"),
        ("zone", "Zone Tool"),
        ("physical-frame", "Physical Frame Tool"),
        ("slice", "Legacy Slice / Difference Tool"),
        ("observation", "Observation Tool"),
        ("planner", "Planner Tool"),
        ("compiler", "BPF Compiler Tool"),
    ):
        if task in results:
            print(f"{title} entry: {inspection / task / 'index.html'}")
    overall_pass = all(result.get("status") == "PASS" for result in results.values())
    print(f"Tooling overall: {'PASS' if overall_pass else 'FAIL'}")
    if (
        arguments.through == "compiler"
        and (output / "run.sh").is_file()
    ):
        print(f"start deployment: {output / 'start.sh'}")
        print(f"run experiment: {output / 'run.sh'}")
        print(f"launch start + run: {output / 'launch.sh'}")
        print(f"teardown deployment: {output / 'cleanup.sh'}")
    return 0 if overall_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
