"""Build the S4 example through one explicitly selected domain boundary."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sys


EXAMPLE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = EXAMPLE_ROOT.parents[1]
DEFAULT_OUTPUT = EXAMPLE_ROOT / "output"
_THROUGH_STAGES = ("topology", "slicer", "planner", "compiler")

if __package__ in (None, ""):
    sys.path.insert(0, str(PROJECT_ROOT))

from examples._direct_execution import prepare_direct_file_execution

prepare_direct_file_execution(__file__)

from examples._compose_project_scripts import prepare_example_output
from examples.s4_satellite_internet.emu import (
    build_s4_satellite_internet_emulator,
)
from examples.s4_satellite_internet.simu import (
    S4_PLANNING_LOOKAHEAD,
    S4_REAL_SLICE_COUNT,
    S4_SLICE_INTERVAL,
    create_s4_planner,
    create_s4_simulator,
    s4_planning_configuration,
    s4_slicing_configuration,
)
from skyseed_v2.backend.bpf import BpfCompiledRealization, compile_bpf
from skyseed_v2.backend.bpf.compiler import _bind_compile_observation
from skyseed_v2.simulator import Plan, Planner, Simulator
from tools._detached_inspection import _DetachedInspectionExecutor
from tools._html import write_aggregate_index, write_json
from tools.topology import write_topology_tool
from tools.zone import write_zone_tool


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--override",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="replace an existing generated output directory (default: enabled)",
    )
    parser.add_argument(
        "--through",
        choices=_THROUGH_STAGES,
        default="topology",
        help="last domain stage to execute (default: topology)",
    )
    parser.add_argument(
        "--slice-limit",
        type=int,
        default=None,
        help=(
            "execution-only final real Slice boundary "
            f"(1..{S4_REAL_SLICE_COUNT}; default: full Timeline)"
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
        and not 1 <= arguments.slice_limit <= S4_REAL_SLICE_COUNT
    ):
        parser.error(
            f"--slice-limit must be between 1 and {S4_REAL_SLICE_COUNT}"
        )
    if arguments.tool_workers <= 0:
        parser.error("--tool-workers must be positive")
    arguments.slice_limit = arguments.slice_limit or S4_REAL_SLICE_COUNT
    return arguments


def _planning_configuration(simulator: Simulator, observer) -> dict[str, object]:
    """Attach exact observed planning/publication windows to Tool metadata."""

    records = observer.snapshot()["records"]
    assert isinstance(records, tuple)
    return {
        **s4_planning_configuration(simulator),
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
            raise RuntimeError("S4 Timeline exhausted before the requested Slice")
        if through_timestamp is not None and next_timestamp > through_timestamp:
            break
        topology_slice = slicer.create_next_slice()
        print(
            f"S4 Slice S{topology_slice.slice_id}/{final_slice_id} "
            f"@ {topology_slice.timestamp.isoformat()}",
            flush=True,
        )


def _slice_source(simulator: Simulator) -> dict[str, object]:
    return {
        "slicer": simulator.get_slicer(),
        "model_context": s4_slicing_configuration(),
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
    """Run the one rolling S4 chain without owning any domain algorithm."""

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
            through_timestamp=anchor + S4_PLANNING_LOOKAHEAD,
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
            create_s4_planner(simulator).get_plan()
            if planner is None
            else planner.synchronize()
        )
        if planner is None:
            planner = simulator.get_planner()
        if plans and (
            plan.start_slice_id != plans[-1].end_slice_id
            or plan.end_slice_id <= plans[-1].end_slice_id
        ):
            raise RuntimeError("S4 Planner did not publish a continuous progression")
        plans.append(plan)
        publication_index = len(plans) - 1
        print(
            f"S4 Planner P{publication_index}: "
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
                f"S4 Compiler C{publication_index}/R{publication_index}: "
                f"{compile_seconds:.3f}s",
                flush=True,
            )

    assert planner is not None
    if slicer.get_latest_slice_id() != final_slice_id:
        raise RuntimeError("S4 execution crossed the requested Slice boundary")
    if plans[-1].end_slice_id != final_slice_id:
        raise RuntimeError("S4 Planner did not publish through the requested Slice")
    if len(planner.get_consumed_differences()) != final_slice_id:
        raise RuntimeError("S4 Planner did not consume one Difference per Slice")
    if through == "compiler" and len(realizations) != len(plans):
        raise RuntimeError("S4 Compiler did not produce one realization per Plan")

    return {
        "planner": planner,
        "plans": tuple(plans),
        "realizations": tuple(realizations),
        "compile_evidence": tuple(compile_evidence),
        "deferred_sources": deferred_sources,
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


def _write_execution_status(
    path: Path,
    *,
    through: str,
    final_slice_id: int,
    domain_status: str,
    simulator: Simulator,
    plans: tuple[Plan, ...],
    realizations: tuple[BpfCompiledRealization, ...],
    tasks: tuple[dict[str, object], ...],
    inspection_status: str | None = None,
    error: BaseException | None = None,
) -> None:
    if inspection_status is None:
        inspection_status = (
            "PASS"
            if tasks
            and all(task.get("status") == "COMPLETE" for task in tasks)
            else "FAILED"
        )
    value: dict[str, object] = {
        "artifact_type": "s4-example-execution-status",
        "format_version": 1,
        "through": through,
        "domain": {
            "status": domain_status,
            "validation": "PASS" if domain_status == "PASS" else "FAILED",
            "latest_slice_id": simulator.get_slicer().get_latest_slice_id(),
            "plan_count": len(plans),
            "realization_count": len(realizations),
        },
        "timeline": {
            "configured_real_slice_count": S4_REAL_SLICE_COUNT,
            "execution_through_slice_id": final_slice_id,
            "slice_interval_seconds": S4_SLICE_INTERVAL.total_seconds(),
        },
        "inspection": {
            "status": inspection_status,
            "tasks": tasks,
        },
        "excluded_stages": ["BPF Runner", "Compose/runtime execution"],
    }
    if error is not None:
        value["error"] = {
            "type": type(error).__name__,
            "message": str(error),
        }
    write_json(path, value)


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    arguments = _validated_arguments(parser, parser.parse_args(argv))

    prepare_example_output(arguments.output, override=arguments.override)
    emulator = build_s4_satellite_internet_emulator()
    simulator = create_s4_simulator(emulator)
    topology = simulator.get_topology()
    inspection = arguments.output / "inspection"

    topology_result = write_topology_tool(topology, inspection / "topology")
    zone_result = write_zone_tool(topology, inspection / "zone")
    results: dict[str, dict[str, object]] = {
        "topology": dict(topology_result),
        "zone": dict(zone_result),
    }
    counts = {
        key: int(value)
        for key, value in dict(topology_result.get("counts", {})).items()
    }

    if arguments.through != "topology":
        execution_directory = inspection / "_execution"
        observation_path = execution_directory / "execution-observations.jsonl"
        observer = simulator._enable_execution_observation(
            observation_path,
            metadata={
                "scenario_identity": "s4_satellite_internet",
                "timeline_start": simulator.get_timeline().start_time.isoformat(),
                "timeline_end": simulator.get_timeline().end_time.isoformat(),
                "slice_interval_seconds": S4_SLICE_INTERVAL.total_seconds(),
                "configured_real_slice_count": S4_REAL_SLICE_COUNT,
                "execution_through_slice_id": arguments.slice_limit,
                "through": arguments.through,
                "progression": "rolling-slicer-planner-compiler",
            },
        )
        executor = _DetachedInspectionExecutor(
            execution_directory,
            max_workers=arguments.tool_workers,
        )
        progression: dict[str, object] = {}
        try:
            progression = _run_dynamic_progression(
                simulator,
                through=arguments.through,
                final_slice_id=arguments.slice_limit,
                observer=observer,
                executor=executor,
                inspection=inspection,
            )
            plans = progression["plans"]
            realizations = progression["realizations"]
            compile_evidence = progression["compile_evidence"]
            assert isinstance(plans, tuple)
            assert isinstance(realizations, tuple)
            assert isinstance(compile_evidence, tuple)
        except BaseException as error:
            executor.terminate()
            plans = progression.get("plans", ())
            realizations = progression.get("realizations", ())
            if not isinstance(plans, tuple):
                plans = ()
            if not isinstance(realizations, tuple):
                realizations = ()
            try:
                _write_execution_status(
                    execution_directory / "status.json",
                    through=arguments.through,
                    final_slice_id=arguments.slice_limit,
                    domain_status="FAILED",
                    simulator=simulator,
                    plans=plans,
                    realizations=realizations,
                    tasks=executor.records(),
                    error=error,
                )
            except BaseException as diagnostic_error:
                error.add_note(
                    "secondary S4 status failure: "
                    f"{type(diagnostic_error).__name__}: {diagnostic_error}"
                )
            raise

        inspection_failures: dict[str, str] = {}
        deferred = progression["deferred_sources"]
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
            schedule(
                "compiler",
                {
                    "plans": plans,
                    "realizations": realizations,
                    "compile_evidence": compile_evidence,
                },
            )
        try:
            simulator._write_execution_observations()
        except Exception as error:
            inspection_failures["observation"] = (
                f"{type(error).__name__}: {error}"
            )
        else:
            schedule(
                "observation",
                {"observation_path": observation_path},
            )
        print(
            "S4 domain chain complete; joining detached Inspection workers",
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
        _write_execution_status(
            execution_directory / "status.json",
            through=arguments.through,
            final_slice_id=arguments.slice_limit,
            domain_status="PASS",
            simulator=simulator,
            plans=plans,
            realizations=realizations,
            tasks=tasks,
            inspection_status=(
                "PASS"
                if all(
                    results[task].get("status") == "PASS"
                    for task in requested_tasks
                )
                else "FAILED"
            ),
        )

    write_aggregate_index(inspection, results, counts)
    print(f"Inspection index: {inspection / 'index.html'}")
    for task, title in (
        ("topology", "Topology Tool"),
        ("zone", "Zone Tool"),
        ("slice", "Slice / Difference Tool"),
        ("observation", "Observation Tool"),
        ("planner", "Planner Tool"),
        ("compiler", "BPF Compiler Tool"),
    ):
        if task in results:
            print(f"{title} entry: {inspection / task / 'index.html'}")
    overall_pass = all(result.get("status") == "PASS" for result in results.values())
    print(f"Tooling overall: {'PASS' if overall_pass else 'FAIL'}")
    return 0 if overall_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
