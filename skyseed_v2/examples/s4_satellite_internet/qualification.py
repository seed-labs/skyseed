"""Record example-local evidence for one bounded dynamic S4 run."""

from __future__ import annotations

from bisect import bisect_left
from collections import Counter
from contextlib import contextmanager
from datetime import datetime
from hashlib import sha256
import json
from math import ceil
import os
from pathlib import Path
import resource
import signal
from statistics import median
from time import perf_counter
from typing import Iterable, Mapping

from skyseed_v2.shared import NodeId
from skyseed_v2.backend.bpf import BpfCompiledRealization
from skyseed_v2.backend.bpf.compiler import _compile_bpf_observed
from skyseed_v2.simulator import (
    Connection,
    ConnectionDirection,
    ConnectionKind,
    ConnectionTransitionOperation,
    DifferenceOperation,
    DirectionalConnectionParameters,
    GroundStationRouteTarget,
    Plan,
    Planner,
    Simulator,
    Slicer,
    Topology,
    TopologyDifference,
    TopologySlice,
    ZoneRouteTarget,
)
from skyseed_v2.simulator.geometry import ecef_distance_m
from skyseed_v2.simulator.planning.algorithms import (
    _path_delay_us,
    _path_nodes,
    _planned_paths_by_slice,
)
from tools.observation import write_observation_tool
from tools._detached_inspection import _DetachedInspectionExecutor

from .simu import (
    BANDWIDTH_BPS_BY_KIND,
    DYNAMIC_REAL_SLICE_COUNT,
    DYNAMIC_SLICE_INTERVAL,
    GATEWAY_CANDIDATE_COUNT,
    GATEWAY_CANDIDATE_MAX_DISTANCE_M,
    GROUND_STATION_MAXIMUM_DISTANCE_M,
    GROUND_STATION_MINIMUM_ELEVATION_DEG,
    MAXIMUM_GROUND_STATION_ACCESS_COUNT,
    MAXIMUM_ISL_DISTANCE_M,
    PACKET_LOSS_PPM_BY_KIND,
    PROPAGATION_SPEED_M_PER_S,
    REFERENCE_EPOCH,
    ZONE_COVERAGE_MINIMUM_ELEVATION_DEG,
    create_s4_planner,
    s4_planning_configuration,
)


FIRST_SANITY_SLICE_ID = 2
TEN_MINUTE_SLICE_ID = 61
FINAL_SLICE_ID = DYNAMIC_REAL_SLICE_COUNT
HOP_CHECKPOINT_SLICE_IDS = frozenset({1, TEN_MINUTE_SLICE_ID, FINAL_SLICE_ID})
PLANNER_BOOTSTRAP_STAGE_STOP_SECONDS = 300.0
PLANNER_STEADY_STAGE_STOP_SECONDS = 30.0
PLANNER_STEADY_GROWTH_FACTOR = 4.0
PLANNER_STEADY_GROWTH_FLOOR_SECONDS = 10.0
PLANNER_BOOTSTRAP_WALL_STOP_SECONDS = 600.0
PLANNER_STEADY_WALL_STOP_SECONDS = 60.0
COMPILER_COMPILE_WALL_STOP_SECONDS = 180.0


class _PlannerPerformanceStop(RuntimeError):
    """End qualification without presenting a performance abort as success."""


class _CompilerPerformanceStop(RuntimeError):
    """Stop an obviously pathological compile while retaining evidence."""


def run_s4_dynamic_qualification(
    simulator: Simulator,
    output_directory: Path,
    *,
    static_construction_seconds: float,
    include_planner: bool = False,
    planner_profile: str | None = None,
    final_slice_id: int = DYNAMIC_REAL_SLICE_COUNT,
    tool_workers: int = 2,
) -> dict[str, object]:
    """Advance accepted S4 execution and write qualification evidence."""

    if not isinstance(simulator, Simulator):
        raise TypeError("simulator must be a Simulator")
    if static_construction_seconds < 0.0:
        raise ValueError("static_construction_seconds must be non-negative")
    if not isinstance(include_planner, bool):
        raise TypeError("include_planner must be a bool")
    if (
        isinstance(final_slice_id, bool)
        or not isinstance(final_slice_id, int)
        or not 1 <= final_slice_id <= DYNAMIC_REAL_SLICE_COUNT
    ):
        raise ValueError(
            "final_slice_id must identify an S4 real Slice within the Timeline"
        )
    if (
        isinstance(tool_workers, bool)
        or not isinstance(tool_workers, int)
        or tool_workers <= 0
    ):
        raise ValueError("tool_workers must be a positive integer")

    output = Path(output_directory)
    evidence_directory = output / "qualification"
    evidence_directory.mkdir(parents=True, exist_ok=True)
    slice_metrics_path = evidence_directory / "slice-metrics.jsonl"
    difference_metrics_path = evidence_directory / "difference-metrics.jsonl"
    planner_metrics_path = evidence_directory / "planner-metrics.jsonl"
    compiler_metrics_path = evidence_directory / "compiler-metrics.jsonl"
    run_path = evidence_directory / "run.json"
    checksums_path = evidence_directory / "checksums.sha256"
    observation_directory = output / "inspection/observation"
    observation_path = evidence_directory / "execution-observations.jsonl"
    metric_paths = [slice_metrics_path, difference_metrics_path]
    if include_planner:
        metric_paths.extend((planner_metrics_path, compiler_metrics_path))
    for path in metric_paths:
        path.write_text("", encoding="utf-8")
    inspection_executor = _DetachedInspectionExecutor(
        evidence_directory,
        max_workers=tool_workers,
    )

    slicer = simulator.get_slicer()
    observation_metadata: dict[str, object] = {
        "scenario_identity": "s4_satellite_internet",
        "slice_interval_seconds": DYNAMIC_SLICE_INTERVAL.total_seconds(),
        "qualification": (
            "slicer-planner" if include_planner else "dynamic-slice"
        ),
        "real_slice_count": final_slice_id,
    }
    if include_planner and planner_profile is not None:
        observation_metadata["planner_profile"] = planner_profile
    observer = simulator._enable_execution_observation(
        observation_path,
        metadata=observation_metadata,
    )
    topology = simulator.get_topology()
    timeline = simulator.get_timeline()
    topology_identity = id(topology)
    context = _topology_context(topology)
    started = perf_counter()
    domain_timeline: list[dict[str, object]] = [
        _execution_event("slicer", "start", started)
    ]
    slice_records: list[dict[str, object]] = []
    difference_records: list[dict[str, object]] = []
    checkpoints: dict[str, dict[str, object]] = {}
    slice_tool_record: dict[str, object] | None = None
    planner: Planner | None = None
    plans: list[Plan] = []
    planner_checkpoints: list[dict[str, object]] = []
    planner_configuration: dict[str, object] | None = None
    planner_started: float | None = None
    planner_finished: float | None = None
    planner_elapsed_seconds: float | None = None
    planner_stop_reason: str | None = None
    planner_policy_evidence: dict[str, object] | None = None
    compiled_realizations: list[BpfCompiledRealization] = []
    compiler_checkpoints: list[dict[str, object]] = []
    compiler_elapsed_seconds: float | None = None
    compiler_stop_reason: str | None = None
    compiler_started: float | None = None
    compiler_finished: float | None = None
    final_join_started: float | None = None
    final_join_finished: float | None = None
    slicer_finished: float | None = None

    try:
        while (
            not slicer.is_exhausted()
            and slicer.get_latest_slice_id() < final_slice_id
        ):
            slice_started = perf_counter()
            topology_slice = slicer.create_next_slice()
            slice_generation_seconds = perf_counter() - slice_started
            _verify_slice(
                topology_slice,
                topology,
                topology_identity,
                slicer.get_topology(),
                context,
            )

            difference = None
            difference_seconds = None
            if not include_planner:
                difference_started = perf_counter()
                difference = slicer.create_difference(
                    topology_slice.slice_id - 1,
                    topology_slice.slice_id,
                )
                difference_seconds = perf_counter() - difference_started

            analysis_started = perf_counter()
            slice_record = _slice_metrics(topology_slice, context)
            if topology_slice.slice_id in (
                HOP_CHECKPOINT_SLICE_IDS | {final_slice_id}
            ):
                slice_record["route_hop_count_distribution"] = (
                    _route_hop_count_distribution(slicer, topology_slice)
                )
            memory = _memory_observation()
            observer.record(
                component="slicer",
                stage="resource-sample",
                phase=(
                    "bootstrap" if topology_slice.slice_id == 1 else "steady"
                ),
                duration_seconds=0.0,
                duration_role="sample",
                scope={
                    "slice_id": topology_slice.slice_id,
                    "timestamp": topology_slice.timestamp.isoformat(),
                },
                resources={
                    "process_rss_bytes": memory["process_rss_kib"] * 1024,
                    "process_peak_rss_bytes": (
                        memory["process_peak_rss_kib"] * 1024
                    ),
                    "host_memory_available_bytes": (
                        memory["host_memory_available_kib"] * 1024
                    ),
                },
            )
            slice_record.update(
                {
                    "slice_generation_seconds": slice_generation_seconds,
                    "retained_slice_count_including_zero": len(
                        slicer.get_retained_slice_ids()
                    ),
                    "process_rss_kib": memory["process_rss_kib"],
                    "process_peak_rss_kib": memory["process_peak_rss_kib"],
                    "host_memory_available_kib": memory[
                        "host_memory_available_kib"
                    ],
                }
            )
            slice_record["evidence_analysis_seconds"] = (
                perf_counter() - analysis_started
            )
            slice_records.append(slice_record)
            _append_jsonl(slice_metrics_path, slice_record)
            if difference is not None and difference_seconds is not None:
                difference_record = _difference_metrics(
                    difference,
                    slicer.get_slice(topology_slice.slice_id - 1),
                    topology_slice,
                    context,
                )
                difference_record["difference_generation_seconds"] = (
                    difference_seconds
                )
                difference_records.append(difference_record)
                _append_jsonl(difference_metrics_path, difference_record)

            if topology_slice.slice_id == FIRST_SANITY_SLICE_ID:
                checkpoints["first_dynamic_sanity_window"] = (
                    _checkpoint_record(topology_slice, started, memory)
                )
            if topology_slice.slice_id == TEN_MINUTE_SLICE_ID:
                checkpoints["ten_simulated_minutes"] = _checkpoint_record(
                    topology_slice, started, memory
                )
            if topology_slice.slice_id == final_slice_id:
                checkpoints["qualification_final_slice"] = _checkpoint_record(
                    topology_slice, started, memory
                )

            difference_message = (
                "Difference deferred to Planner"
                if difference_seconds is None
                else f"{difference_seconds:.3f}s Difference"
            )
            print(
                "S4 dynamic "
                f"Slice {topology_slice.slice_id}/{final_slice_id}: "
                f"{slice_generation_seconds:.3f}s Slice, "
                f"{difference_message}, "
                f"{slice_record['connections']['total']} Connections, "
                f"{slice_record['routes']['entries']} Routes, "
                f"RSS {memory['process_rss_kib'] / 1024:.1f} MiB",
                flush=True,
            )
            _raise_if_memory_exhaustion_is_imminent(memory)
            if difference is not None:
                del difference

        if len(slice_records) != final_slice_id:
            raise RuntimeError(
                "dynamic S4 run did not materialize exactly "
                f"{final_slice_id} real Slices"
            )
        if (
            final_slice_id == DYNAMIC_REAL_SLICE_COUNT
            and not slicer.is_exhausted()
        ):
            raise RuntimeError("dynamic S4 Timeline is not exhausted")
        slicer_finished = perf_counter()
        domain_timeline.append(
            _execution_event("slicer", "end", slicer_finished)
        )
        inspection_executor.submit(
            "slice",
            {
                "slicer": slicer,
                "model_context": _slice_tool_context(),
            },
            output / "inspection/slice",
        )
        if include_planner:
            planner_configuration = s4_planning_configuration(
                simulator, profile=planner_profile
            )
            planner_started = perf_counter()
            domain_timeline.append(
                _execution_event("planner", "start", planner_started)
            )
            print(
                "S4 Planner bootstrap starting: "
                f"{slicer.get_latest_slice_id()} available Differences, "
                + (
                    f"diagnostic profile {planner_profile!r}"
                    if planner_profile is not None
                    else "normal explicit S4 composition"
                ),
                flush=True,
            )
            try:
                with _planner_deadline(
                    PLANNER_BOOTSTRAP_WALL_STOP_SECONDS,
                    "bootstrap",
                ):
                    planner = create_s4_planner(
                        simulator, profile=planner_profile
                    )
            except _PlannerPerformanceStop as stop:
                planner_stop_reason = str(stop)
            finally:
                planner_elapsed_seconds = perf_counter() - planner_started
            if planner is not None:
                plans.append(planner.get_plan())
                planner_checkpoint = _record_planner_checkpoint(
                    observer,
                    planner,
                    plans[-1],
                    len(plans),
                    planner_metrics_path,
                )
                planner_checkpoints.append(planner_checkpoint)
                planner_stop_reason = _planner_stop_reason(
                    planner_checkpoint,
                    planner_checkpoints[:-1],
                )
            while (
                planner is not None
                and planner_stop_reason is None
                and plans[-1].end_slice_id < slicer.get_latest_slice_id()
            ):
                try:
                    with _planner_deadline(
                        PLANNER_STEADY_WALL_STOP_SECONDS,
                        f"steady publication {len(plans)}",
                    ):
                        plan = planner.synchronize()
                except _PlannerPerformanceStop as stop:
                    planner_stop_reason = str(stop)
                    break
                plans.append(plan)
                planner_checkpoint = _record_planner_checkpoint(
                    observer,
                    planner,
                    plans[-1],
                    len(plans),
                    planner_metrics_path,
                )
                planner_checkpoints.append(planner_checkpoint)
                planner_stop_reason = _planner_stop_reason(
                    planner_checkpoint,
                    planner_checkpoints[:-1],
                )
            planner_elapsed_seconds = perf_counter() - planner_started
            planner_finished = perf_counter()
            domain_timeline.append(
                _execution_event("planner", "end", planner_finished)
            )
            if planner is not None:
                difference_records.extend(
                    _planner_difference_metrics(
                        observer,
                        planner,
                        slicer,
                        context,
                        difference_metrics_path,
                    )
                )
            if (
                planner_stop_reason is None
                and len(difference_records) != final_slice_id
            ):
                raise RuntimeError(
                    "S4 Planner did not consume exactly one Difference per "
                    "real Slice"
                )
            if planner_stop_reason is None and planner is not None:
                assert planner_configuration is not None
                planner_policy_evidence = _planner_policy_evidence(
                    planner,
                    plans,
                    planner_configuration,
                    planner_checkpoints,
                )
            if planner_stop_reason is not None:
                print(
                    f"S4 Planner qualification intentionally stopped: "
                    f"{planner_stop_reason}",
                    flush=True,
                )
            elif planner is not None:
                assert planner_configuration is not None
                planner_tool_configuration = _planner_tool_configuration(
                    planner_configuration,
                    observer,
                    planner_policy_evidence,
                    planner_checkpoints,
                )
                inspection_executor.submit(
                    "planner",
                    {
                        "slicer": slicer,
                        "plans": tuple(plans),
                        "differences": planner.get_consumed_differences(),
                        "decisions": planner.get_planning_decisions(),
                        "configuration": planner_tool_configuration,
                    },
                    output / "inspection/planner",
                )
        if include_planner and planner_stop_reason is None:
            compiler_started = perf_counter()
            domain_timeline.append(
                _execution_event("compiler", "start", compiler_started)
            )
            previous_realization: BpfCompiledRealization | None = None
            print(
                "S4 BPF Compiler qualification starting: "
                f"{len(plans)} accepted Plans",
                flush=True,
            )
            for compile_index, plan in enumerate(plans):
                compile_started = perf_counter()
                try:
                    with _compiler_deadline(
                        COMPILER_COMPILE_WALL_STOP_SECONDS,
                        compile_index,
                    ):
                        realization = _compile_bpf_observed(
                            plan,
                            slicer=slicer,
                            previous=previous_realization,
                            execution_observer=observer,
                            compile_index=compile_index,
                        )
                except _CompilerPerformanceStop as stop:
                    compiler_stop_reason = str(stop)
                    break
                compile_seconds = perf_counter() - compile_started
                compiled_realizations.append(realization)
                previous_realization = realization
                memory = _memory_observation()
                observer.record(
                    component="compiler",
                    stage="resource-sample",
                    phase="one-shot",
                    duration_seconds=0.0,
                    duration_role="sample",
                    scope={
                        "compile_index": compile_index,
                        "realization_index": compile_index,
                        "plan_index": compile_index,
                        "publication_index": compile_index,
                        "publication_start_slice_id": plan.start_slice_id,
                        "publication_end_slice_id": plan.end_slice_id,
                        "start_slice_id": plan.start_slice_id,
                        "end_slice_id": plan.end_slice_id,
                        "start_timestamp": (
                            None
                            if plan.start_timestamp is None
                            else plan.start_timestamp.isoformat()
                        ),
                        "end_timestamp": plan.end_timestamp.isoformat(),
                    },
                    resources={
                        "process_rss_bytes": memory["process_rss_kib"] * 1024,
                        "process_peak_rss_bytes": (
                            memory["process_peak_rss_kib"] * 1024
                        ),
                        "host_memory_available_bytes": (
                            memory["host_memory_available_kib"] * 1024
                        ),
                    },
                )
                checkpoint = _compiler_checkpoint(
                    compile_index,
                    plan,
                    realization,
                    compile_seconds,
                    memory,
                )
                compiler_checkpoints.append(checkpoint)
                _append_jsonl(compiler_metrics_path, checkpoint)
                print(
                    f"S4 BPF Compiler C{compile_index}/R{compile_index}: "
                    f"{compile_seconds:.3f}s, "
                    f"{checkpoint['input']['total_transitions']} transitions, "
                    f"{checkpoint['baseline']['total_records']} baseline records, "
                    f"RSS {memory['process_rss_kib'] / 1024:.1f} MiB",
                    flush=True,
                )
                _raise_if_memory_exhaustion_is_imminent(memory)
            compiler_elapsed_seconds = perf_counter() - compiler_started
            compiler_finished = perf_counter()
            domain_timeline.append(
                _execution_event("compiler", "end", compiler_finished)
            )
            if compiler_stop_reason is not None:
                print(
                    "S4 BPF Compiler qualification intentionally stopped: "
                    f"{compiler_stop_reason}",
                    flush=True,
                )
            elif len(compiled_realizations) != len(plans):
                raise RuntimeError(
                    "BPF Compiler did not produce one realization per Plan"
                )
            if compiler_stop_reason is None:
                inspection_executor.submit(
                    "compiler",
                    {
                        "plans": tuple(plans),
                        "realizations": tuple(compiled_realizations),
                        "compile_evidence": tuple(compiler_checkpoints),
                    },
                    output / "inspection/compiler",
                )
    except BaseException as error:
        inspection_executor.terminate()
        diagnostic_failures = list(
            _write_observation_diagnostics_fail_open(
                simulator,
                observation_path,
                observation_directory,
            )
        )
        try:
            blocked = _blocked_record(
                error,
                simulator,
                static_construction_seconds,
                started,
                slice_records,
                difference_records,
                checkpoints,
                slice_tool_record,
            )
            if include_planner:
                blocked["planner_qualification"] = _planner_run_record(
                    planner,
                    plans,
                    planner_configuration,
                    planner_checkpoints,
                    planner_elapsed_seconds,
                    policy_evidence=planner_policy_evidence,
                    status="BLOCKED",
                    stop_reason=None,
                )
                blocked["compiler_qualification"] = _compiler_run_record(
                    plans,
                    compiled_realizations,
                    compiler_checkpoints,
                    compiler_elapsed_seconds,
                    status="BLOCKED",
                    stop_reason=compiler_stop_reason,
                )
            if diagnostic_failures:
                blocked["observation_diagnostic_failures"] = (
                    diagnostic_failures
                )
            _write_json(run_path, blocked)
            _write_checksums(
                checksums_path,
                (run_path, *metric_paths),
                evidence_directory,
            )
        except BaseException as diagnostic_error:
            diagnostic_failures.append(
                _diagnostic_failure("blocked-evidence", diagnostic_error)
            )
        for failure in diagnostic_failures:
            error.add_note(
                "secondary S4 diagnostic failure: "
                f"{failure['operation']}: {failure['exception_type']}: "
                f"{failure['message']}"
            )
        raise

    simulator._write_execution_observations()
    inspection_executor.submit(
        "observation",
        {"observation_path": observation_path},
        observation_directory,
    )
    final_join_started = perf_counter()
    print("S4 domain chain complete; joining detached Inspection workers", flush=True)
    inspection_tasks = inspection_executor.join()
    final_join_finished = perf_counter()
    slice_result = inspection_executor.result("slice")
    observation_result = inspection_executor.result("observation")
    planner_result = inspection_executor.result("planner")
    compiler_result = inspection_executor.result("compiler")
    slice_tool_record = _detached_tool_record(
        "slice",
        slice_result,
        inspection_tasks,
        output / "inspection/slice",
        slicer.get_latest_slice_id(),
    )
    observation_tool_record = _detached_tool_record(
        "observation",
        observation_result,
        inspection_tasks,
        observation_directory,
        slicer.get_latest_slice_id(),
    )
    planner_tool_record = (
        None
        if not include_planner or planner_stop_reason is not None
        else _detached_tool_record(
            "planner",
            planner_result,
            inspection_tasks,
            output / "inspection/planner",
            slicer.get_latest_slice_id(),
        )
    )
    compiler_tool_record = (
        None
        if not include_planner
        or planner_stop_reason is not None
        or compiler_stop_reason is not None
        else _detached_tool_record(
            "compiler",
            compiler_result,
            inspection_tasks,
            output / "inspection/compiler",
            slicer.get_latest_slice_id(),
        )
    )
    required_results = [slice_result, observation_result]
    if include_planner and planner_stop_reason is None:
        required_results.append(planner_result)
    if (
        include_planner
        and planner_stop_reason is None
        and compiler_stop_reason is None
    ):
        required_results.append(compiler_result)
    artifact_gate = all(
        isinstance(result, Mapping) and result.get("status") == "PASS"
        for result in required_results
    )
    domain_status = (
        "STOPPED"
        if planner_stop_reason is not None or compiler_stop_reason is not None
        else "COMPLETE"
    )
    completed = _completed_record(
        simulator,
        static_construction_seconds,
        started,
        slice_records,
        difference_records,
        checkpoints,
        slice_tool_record,
        output,
    )
    completed["observation_tool"] = observation_tool_record
    completed["inspection_execution"] = _inspection_execution_record(
        inspection_tasks,
        domain_timeline,
        started,
        (
            compiler_finished
            or planner_finished
            or slicer_finished
            or perf_counter()
        ),
        final_join_started,
        final_join_finished,
        inspection_executor.max_workers,
        artifact_gate,
        domain_status,
    )
    if include_planner:
        completed["scope"] = (
            "Position -> Connection -> Route -> Slice -> Difference -> "
            "Planner -> BPF Compiler"
        )
        completed["status"] = domain_status
        completed["planner_qualification"] = _planner_run_record(
            planner,
            plans,
            planner_configuration,
            planner_checkpoints,
            planner_elapsed_seconds,
            policy_evidence=planner_policy_evidence,
            status=str(completed["status"]),
            stop_reason=planner_stop_reason,
        )
        completed["planner_tool"] = planner_tool_record
        completed["compiler_qualification"] = _compiler_run_record(
            plans,
            compiled_realizations,
            compiler_checkpoints,
            compiler_elapsed_seconds,
            status=str(completed["status"]),
            stop_reason=compiler_stop_reason,
        )
        if compiler_result is not None:
            completed["compiler_qualification"]["realization_inventory"] = (
                compiler_result["realization_inventory"]
            )
        completed["compiler_tool"] = compiler_tool_record
        completed["excluded_stages"] = [
            "BPF Runner",
            "Compose/runtime containers",
        ]
    completed["artifact_gate"] = "PASS" if artifact_gate else "FAILED"
    _write_json(run_path, completed)
    evidence_files = tuple(
        path
        for directory in (
            evidence_directory / "inspection-tasks",
            evidence_directory / "tool-handoffs",
        )
        if directory.exists()
        for path in sorted(directory.iterdir())
        if path.is_file()
    )
    _write_checksums(
        checksums_path,
        (run_path, *metric_paths, observation_path, *evidence_files),
        evidence_directory,
    )
    if slice_result is not None:
        completed["slice_difference_tool"] = dict(slice_result)
    if observation_result is not None:
        completed["observation_tool"] = dict(observation_result)
    if planner_result is not None:
        completed["planner_tool"] = dict(planner_result)
    if compiler_result is not None:
        completed["compiler_tool"] = dict(compiler_result)
    return completed


def _planner_tool_configuration(
    configuration: dict[str, object],
    observer,
    policy_evidence: dict[str, object] | None,
    checkpoints: list[dict[str, object]],
) -> dict[str, object]:
    """Attach compact exact run evidence used only by the detached Tool."""

    records = observer.snapshot()["records"]
    planning_windows = tuple(
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
    )
    return {
        **configuration,
        "planning_windows": planning_windows,
        "policy_evidence": policy_evidence or {},
        "publication_checkpoints": tuple(checkpoints),
    }


@contextmanager
def _planner_deadline(
    seconds: float,
    label: str,
) -> Iterable[None]:
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)

    def stop_planner(_signum, _frame) -> None:
        raise _PlannerPerformanceStop(
            f"{label} exceeded the {seconds:.0f}s wall-clock stop threshold"
        )

    signal.signal(signal.SIGALRM, stop_planner)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer != (0.0, 0.0):
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)


@contextmanager
def _compiler_deadline(
    seconds: float,
    compile_index: int,
) -> Iterable[None]:
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)

    def stop_compiler(_signum, _frame) -> None:
        raise _CompilerPerformanceStop(
            f"C{compile_index}/R{compile_index} exceeded the "
            f"{seconds:.0f}s wall-clock stop threshold"
        )

    signal.signal(signal.SIGALRM, stop_compiler)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer != (0.0, 0.0):
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)


def _compiler_checkpoint(
    compile_index: int,
    plan: Plan,
    realization: BpfCompiledRealization,
    compile_seconds: float,
    memory: Mapping[str, int],
) -> dict[str, object]:
    input_counts = {
        "connection_transitions": len(plan.connection_transitions),
        "parameter_transitions": len(plan.parameter_transitions),
        "forwarding_transitions": len(plan.forwarding_transitions),
        "forwarding_precedences": len(plan.forwarding_precedences),
    }
    interval_counts = {
        "resource_intents": len(realization.resource_realizations),
        "parameter_intents": len(realization.parameter_realizations),
        "forwarding_intents": len(realization.forwarding_realizations),
        "zone_edge_intents": len(realization.zone_edge_realizations),
        "constraints": len(realization.constraints),
    }
    baseline_counts = {
        "resources": len(realization.intended_resources),
        "parameters": len(realization.intended_parameters),
        "forwarding": len(realization.intended_forwarding),
        "zone_edges": len(realization.intended_zone_edges),
        "zone_prefixes": len(realization.zone_prefixes),
    }
    return {
        "record_type": "s4_bpf_compiler_checkpoint",
        "compile_index": compile_index,
        "realization_index": compile_index,
        "plan_index": compile_index,
        "coverage": {
            "start_slice_id": plan.start_slice_id,
            "start_timestamp": (
                None
                if plan.start_timestamp is None
                else plan.start_timestamp.isoformat()
            ),
            "end_slice_id": plan.end_slice_id,
            "end_timestamp": plan.end_timestamp.isoformat(),
        },
        "compile_seconds": compile_seconds,
        "input": {
            **input_counts,
            "total_transitions": sum(
                input_counts[name]
                for name in (
                    "connection_transitions",
                    "parameter_transitions",
                    "forwarding_transitions",
                )
            ),
        },
        "interval_output": {
            **interval_counts,
            "total_records": sum(interval_counts.values()),
        },
        "baseline": {
            **baseline_counts,
            "total_records": sum(
                baseline_counts[name]
                for name in (
                    "resources",
                    "parameters",
                    "forwarding",
                    "zone_edges",
                )
            ),
        },
        "validation_result": "PASS",
        "memory": dict(memory),
    }


def _compiler_run_record(
    plans: list[Plan],
    realizations: list[BpfCompiledRealization],
    checkpoints: list[dict[str, object]],
    elapsed_seconds: float | None,
    *,
    status: str,
    stop_reason: str | None,
) -> dict[str, object]:
    complete = len(realizations) == len(plans) and stop_reason is None
    return {
        "status": status if complete else "STOPPED",
        "source_plan_count": len(plans),
        "compiled_realization_count": len(realizations),
        "qualification_wall_seconds": elapsed_seconds,
        "validation_result": "PASS" if complete else "INCOMPLETE",
        "stop_reason": stop_reason,
        "compile_units": tuple(checkpoints),
        "public_shape": "Plan -> compile_bpf -> BpfCompiledRealization",
        "runtime_lifecycle": "excluded",
    }


def _record_planner_checkpoint(
    observer,
    planner: Planner,
    plan: Plan,
    publication_ordinal: int,
    path: Path,
) -> dict[str, object]:
    publication_index = publication_ordinal - 1
    phase = "bootstrap" if publication_index == 0 else "steady"
    before_sample = observer.snapshot()
    records = tuple(before_sample["records"])
    route_records = tuple(
        record
        for record in records
        if record["component"] == "planner"
        and record["stage"] == "route-context"
    )
    consumed_differences = planner.get_consumed_differences()
    retained_state = {
        "retained_difference_count": len(consumed_differences),
        "retained_route_context_count": len(
            set(planner._route_next_hops)
            | set(planner._route_path_connection_ids)
        ),
        "retained_decision_count": len(planner.get_planning_decisions()),
        "consumed_difference_count": len(consumed_differences),
        "unpublished_connection_transition_count": len(
            planner._connection_transitions
        ),
        "unpublished_parameter_transition_count": len(
            planner._parameter_transitions
        ),
        "unpublished_forwarding_transition_count": len(
            planner._forwarding_transitions
        ),
        "unpublished_forwarding_precedence_count": len(
            planner._forwarding_precedences
        ),
        "planned_forwarding_route_count": len(
            planner._planned_route_next_hops
        ),
        "planned_forwarding_path_count": len(
            planner._planned_route_path_connection_ids
        ),
        "published_forwarding_route_count": len(
            planner._published_route_next_hops
        ),
        "published_forwarding_path_count": len(
            planner._published_route_path_connection_ids
        ),
        "retained_route_key_references": sum(
            int(record["work"].get("route_entries_processed", 0))
            for record in route_records
        ),
        "retained_path_edge_references": sum(
            int(record["work"].get("path_edges_processed", 0))
            for record in route_records
        ),
        "caller_retained_plan_count": publication_ordinal,
    }
    memory = _memory_observation()
    observer.record(
        component="planner",
        stage="retained-state-sample",
        phase=phase,
        duration_seconds=0.0,
        duration_role="sample",
        scope={
            "publication_index": publication_index,
            "planning_end_slice_id": consumed_differences[-1].current_slice_id,
            "publication_start_slice_id": plan.start_slice_id,
            "publication_end_slice_id": plan.end_slice_id,
        },
        work=retained_state,
        resources={
            "process_rss_bytes": memory["process_rss_kib"] * 1024,
            "process_peak_rss_bytes": memory["process_peak_rss_kib"] * 1024,
            "host_memory_available_bytes": (
                memory["host_memory_available_kib"] * 1024
            ),
        },
    )
    snapshot = observer.snapshot()
    publication_records = {
        str(record["stage"]): record
        for record in snapshot["records"]
        if record["component"] == "planner"
        and record["scope"].get("publication_index") == publication_index
    }
    stages = {
        stage: _planner_stage_duration(publication_records, stage)
        for stage in (
            "difference-acquisition",
            "window-selection",
            "route-context",
            "connection-planning",
            "connection-output-validation",
            "parameter-planning",
            "parameter-output-validation",
            "forwarding-planning",
            "forwarding-output-validation",
            "suppression-candidate-discovery",
            "coordination-policy",
            "coordination-output-validation",
            "coordination",
            "forwarding-precedence",
            "forwarding-precedence-output-validation",
            "state-advancement",
            "fixed-plan-region-validation",
            "validation",
            "publication",
            "total-synchronize",
        )
    }
    total = publication_records["total-synchronize"]
    window = publication_records["window-selection"]
    connection = publication_records.get("connection-planning")
    parameter = publication_records.get("parameter-planning")
    forwarding = publication_records.get("forwarding-planning")
    candidate_discovery = publication_records.get(
        "suppression-candidate-discovery"
    )
    coordination = publication_records.get("coordination")
    precedence = publication_records.get("forwarding-precedence")
    publication = publication_records["publication"]
    checkpoint = {
        "record_type": "s4_planner_publication_checkpoint",
        "publication_index": publication_index,
        "publication_ordinal": publication_ordinal,
        "phase": phase,
        "covered_difference_range": [
            plan.start_slice_id + 1,
            plan.end_slice_id,
        ],
        "stage_seconds": stages,
        "work": {
            "planning_window_differences": _record_work(
                window, "planning_window_differences"
            ),
            "publication_window_differences": _record_work(
                window, "publication_window_differences"
            ),
            "affected_differences": _record_work(
                window, "affected_differences"
            ),
            "input_difference_patches": _record_work(
                window, "input_difference_patches"
            ),
            "model_connection_intents": _record_work(
                connection, "connection_intents"
            ),
            "model_parameter_intents": _record_work(
                parameter, "parameter_intents"
            ),
            "model_forwarding_intents": _record_work(
                forwarding, "forwarding_intents"
            ),
            "suppression_candidates": _record_work(
                candidate_discovery, "suppression_candidates"
            ),
            "coordination_input_forwarding_intents": _record_work(
                coordination, "input_forwarding_intents"
            ),
            "coordinated_connection_intents": _record_work(
                coordination, "coordinated_connection_intents"
            ),
            "coordinated_parameter_intents": _record_work(
                coordination, "coordinated_parameter_intents"
            ),
            "coordinated_forwarding_intents": _record_work(
                coordination, "coordinated_forwarding_intents"
            ),
            "coordination_decisions": _record_work(
                coordination, "coordination_decisions"
            ),
            "precedence_bucket_pair_iterations": _record_work(
                precedence, "precedence_bucket_pair_iterations"
            ),
            "precedence_candidate_pairs": _record_work(
                precedence, "precedence_candidate_pairs"
            ),
            "emitted_precedences": _record_work(
                precedence, "emitted_precedences"
            ),
            "route_entries_processed": _record_work(
                publication_records.get("route-context"),
                "route_entries_processed",
            ),
            "path_edges_processed": _record_work(
                publication_records.get("route-context"),
                "path_edges_processed",
            ),
            "published_connection_transitions": _record_work(
                publication, "published_connection_transitions"
            ),
            "published_parameter_transitions": _record_work(
                publication, "published_parameter_transitions"
            ),
            "published_forwarding_transitions": _record_work(
                publication, "published_forwarding_transitions"
            ),
            "published_forwarding_precedences": _record_work(
                publication, "published_forwarding_precedences"
            ),
        },
        "retained_state": retained_state,
        "memory": memory,
    }
    _append_jsonl(path, checkpoint)
    print(
        "S4 Planner checkpoint "
        + json.dumps(checkpoint, separators=(",", ":"), sort_keys=True),
        flush=True,
    )
    return checkpoint


def _planner_stage_duration(
    records: Mapping[str, Mapping[str, object]],
    stage: str,
) -> float | None:
    record = records.get(stage)
    return None if record is None else float(record["duration_seconds"])


def _record_work(
    record: Mapping[str, object] | None,
    name: str,
) -> int | float | None:
    if record is None:
        return None
    work = record["work"]
    assert isinstance(work, Mapping)
    value = work.get(name)
    return value if isinstance(value, (int, float)) else None


def _planner_stop_reason(
    checkpoint: Mapping[str, object],
    previous: list[dict[str, object]],
) -> str | None:
    phase = str(checkpoint["phase"])
    stages = checkpoint["stage_seconds"]
    assert isinstance(stages, Mapping)
    threshold = (
        PLANNER_BOOTSTRAP_STAGE_STOP_SECONDS
        if phase == "bootstrap"
        else PLANNER_STEADY_STAGE_STOP_SECONDS
    )
    for stage, value in stages.items():
        if stage == "total-synchronize" or value is None:
            continue
        if float(value) >= threshold:
            return (
                f"{phase} {stage} took {float(value):.3f}s, at or above "
                f"the {threshold:.0f}s single-stage stop threshold"
            )
    total = stages.get("total-synchronize")
    if total is not None and float(total) >= threshold * 2.0:
        return (
            f"{phase} total-synchronize took {float(total):.3f}s, at or "
            f"above the {threshold * 2.0:.0f}s rollup stop threshold"
        )
    memory = checkpoint["memory"]
    assert isinstance(memory, Mapping)
    total_memory = int(memory["host_memory_total_kib"])
    available_memory = int(memory["host_memory_available_kib"])
    reserve = max(2 * 1024 * 1024, ceil(total_memory * 0.05))
    if total_memory > 0 and available_memory < reserve:
        return (
            "host MemAvailable fell below the larger of 2 GiB and 5% of "
            "MemTotal"
        )
    if phase != "steady" or total is None:
        return None
    prior_totals = [
        float(value["stage_seconds"]["total-synchronize"])
        for value in previous
        if value["phase"] == "steady"
        and value["stage_seconds"]["total-synchronize"] is not None
    ]
    if len(prior_totals) < 3:
        return None
    baseline = median(prior_totals[-5:])
    if float(total) > max(
        PLANNER_STEADY_GROWTH_FLOOR_SECONDS,
        baseline * PLANNER_STEADY_GROWTH_FACTOR,
    ):
        return (
            f"steady total-synchronize grew to {float(total):.3f}s from a "
            f"recent {baseline:.3f}s median"
        )
    return None


def _planner_difference_metrics(
    observer,
    planner: Planner,
    slicer: Slicer,
    context: Mapping[str, object],
    path: Path,
) -> tuple[dict[str, object], ...]:
    durations = {
        int(record["scope"]["current_slice_id"]): float(
            record["duration_seconds"]
        )
        for record in observer.snapshot()["records"]
        if record["component"] == "slicer"
        and record["stage"] == "difference"
    }
    differences = planner.get_consumed_differences()
    if tuple(durations) != tuple(
        difference.current_slice_id for difference in differences
    ):
        raise RuntimeError(
            "S4 Planner Difference timing records are incomplete or duplicated"
        )
    result = []
    for difference in differences:
        record = _difference_metrics(
            difference,
            slicer.get_slice(difference.previous_slice_id),
            slicer.get_slice(difference.current_slice_id),
            context,
        )
        record["difference_generation_seconds"] = durations[
            difference.current_slice_id
        ]
        result.append(record)
        _append_jsonl(path, record)
    return tuple(result)


def _planner_run_record(
    planner: Planner | None,
    plans: list[Plan],
    configuration: Mapping[str, object] | None,
    checkpoints: list[dict[str, object]],
    elapsed_seconds: float | None,
    *,
    policy_evidence: Mapping[str, object] | None,
    status: str,
    stop_reason: str | None,
) -> dict[str, object]:
    decisions = () if planner is None else planner.get_planning_decisions()
    return {
        "status": status,
        "stop_reason": stop_reason,
        "configuration": dict(configuration or {}),
        "qualification_wall_seconds": elapsed_seconds,
        "plan_publication_count": len(plans),
        "final_published_slice_id": (
            None if not plans else plans[-1].end_slice_id
        ),
        "consumed_difference_count": (
            0 if planner is None else len(planner.get_consumed_differences())
        ),
        "caller_retained_plan_count": len(plans),
        "published_transition_totals": {
            "connection": sum(len(plan.connection_transitions) for plan in plans),
            "parameter": sum(len(plan.parameter_transitions) for plan in plans),
            "forwarding": sum(len(plan.forwarding_transitions) for plan in plans),
            "precedence": sum(len(plan.forwarding_precedences) for plan in plans),
        },
        "planning_decisions": dict(
            sorted(Counter(decision.decision for decision in decisions).items())
        ),
        "fixed_validation_result": (
            "PASS" if status == "COMPLETE" else "NOT_COMPLETED"
        ),
        "policy_evidence": dict(policy_evidence or {}),
        "publication_checkpoints": checkpoints,
        "final_retained_state": (
            None if not checkpoints else checkpoints[-1]["retained_state"]
        ),
        "final_memory": (
            None if not checkpoints else checkpoints[-1]["memory"]
        ),
    }


def _planner_policy_evidence(
    planner: Planner,
    plans: list[Plan],
    configuration: Mapping[str, object],
    checkpoints: list[dict[str, object]],
) -> dict[str, object]:
    """Replay published semantics into compact S4 policy evidence."""

    differences = planner.get_consumed_differences()
    connection_transitions = tuple(
        transition
        for plan in plans
        for transition in plan.connection_transitions
    )
    parameter_transitions = tuple(
        transition
        for plan in plans
        for transition in plan.parameter_transitions
    )
    forwarding_transitions = tuple(
        transition
        for plan in plans
        for transition in plan.forwarding_transitions
    )
    formal = _formal_planner_workload_evidence(
        planner, differences, configuration
    )
    planned_paths_by_slice = _planned_paths_by_slice(
        differences,
        forwarding_transitions,
        planner._route_next_hops,
        planner._route_path_connection_ids,
        planner._connection_values_by_slice_id,
        {},
        {},
    )
    forwarding = _forwarding_policy_evidence(
        planner,
        differences,
        planned_paths_by_slice,
    )
    parameter = _parameter_policy_evidence(
        planner,
        differences,
        connection_transitions,
        parameter_transitions,
    )
    decisions = planner.get_planning_decisions()
    coordination_decisions = tuple(
        decision
        for decision in decisions
        if decision.decided_by
        == type(planner._coordination_policy).__name__
    )
    window_work = tuple(
        checkpoint["work"] for checkpoint in checkpoints
    )
    return {
        "measurement_basis": (
            "exact consumed Differences and replayed published Plan state at "
            "each formal Slice boundary"
        ),
        "formal_difference_workload": formal,
        "connection": {
            "candidates": formal["connection"]["configured_candidates"],
            "accepted_omissions": sum(
                decision.decision == "suppressed" for decision in decisions
            ),
            "materialized_transitions": len(connection_transitions),
        },
        "parameter": {
            **parameter,
            "raw_patch_count": formal["parameter"]["raw_patch_count"],
            "emitted_transition_count": len(parameter_transitions),
        },
        "forwarding": {
            **forwarding,
            "formal_route_patch_count": formal["forwarding"][
                "formal_route_patch_count"
            ],
            "emitted_transition_count": len(forwarding_transitions),
            "transition_reduction": (
                formal["forwarding"]["formal_route_patch_count"]
                - len(forwarding_transitions)
            ),
        },
        "coordination": {
            "decision_count": len(coordination_decisions),
            "decisions_by_result": dict(
                sorted(
                    Counter(
                        value.decision for value in coordination_decisions
                    ).items()
                )
            ),
        },
        "planning_decisions": {
            "decision_count": len(decisions),
            "decisions_by_model_and_result": {
                f"{model}:{result}": count
                for (model, result), count in sorted(
                    Counter(
                        (value.decided_by, value.decision)
                        for value in decisions
                    ).items()
                )
            },
        },
        "precedence_cardinality": sum(
            len(plan.forwarding_precedences) for plan in plans
        ),
        "publication": {
            "plan_count": len(plans),
            "planning_differences_per_synchronization": _numeric_statistics(
                float(value["planning_window_differences"])
                for value in window_work
            ),
            "publication_differences_per_synchronization": (
                _numeric_statistics(
                    float(value["publication_window_differences"])
                    for value in window_work
                )
            ),
        },
    }


def _formal_planner_workload_evidence(
    planner: Planner,
    differences: tuple[TopologyDifference, ...],
    configuration: Mapping[str, object],
) -> dict[str, object]:
    additions = Counter()
    removals = Counter()
    open_episodes: dict[object, tuple[datetime, datetime, ConnectionKind]] = {}
    boundary_durations: dict[ConnectionKind, Counter[int]] = {
        kind: Counter() for kind in ConnectionKind
    }
    midpoint_durations: dict[ConnectionKind, Counter[int]] = {
        kind: Counter() for kind in ConnectionKind
    }
    raw_patch_fields = Counter()
    absolute_patch_deltas = {
        name: Counter() for name in (
            "bandwidth_bps",
            "delay_us",
            "packet_loss_ppm",
        )
    }
    relative_patch_deltas = {
        name: [] for name in absolute_patch_deltas
    }
    route_operations = Counter()
    hop_gains = Counter()
    delay_gains = Counter()

    for difference in differences:
        midpoint = _difference_midpoint(difference)
        for connection in difference.added_connections:
            kind = connection.connection_id.kind
            additions[kind.value] += 1
            open_episodes[connection.connection_id] = (
                difference.current_timestamp,
                midpoint,
                kind,
            )
        for connection_id in difference.removed_connection_ids:
            removals[connection_id.kind.value] += 1
            started = open_episodes.pop(connection_id, None)
            if started is not None:
                boundary_started, midpoint_started, kind = started
                boundary_durations[kind][
                    round(
                        (
                            difference.current_timestamp - boundary_started
                        ).total_seconds()
                    )
                ] += 1
                midpoint_durations[kind][
                    round((midpoint - midpoint_started).total_seconds())
                ] += 1
        previous_connections = {
            value.connection_id: value
            for value in planner._connection_values_by_slice_id.get(
                difference.previous_slice_id, {}
            ).values()
        }
        for patch in difference.connection_parameter_patches:
            previous_connection = previous_connections[patch.connection_id]
            previous_parameters = _directional_parameters(
                previous_connection, patch.direction
            )
            for field_name in absolute_patch_deltas:
                target = getattr(patch, field_name)
                if target is None:
                    continue
                raw_patch_fields[field_name] += 1
                previous = getattr(previous_parameters, field_name)
                delta = abs(target - previous)
                absolute_patch_deltas[field_name][delta] += 1
                relative_patch_deltas[field_name].append(
                    delta / abs(previous) if previous != 0 else float(delta > 0)
                )

        previous_paths = planner._route_path_connection_ids.get(
            difference.previous_slice_id, {}
        )
        current_paths = planner._route_path_connection_ids.get(
            difference.current_slice_id, {}
        )
        current_connections = planner._connection_values_by_slice_id.get(
            difference.current_slice_id, {}
        )
        for patch in difference.route_patches:
            route_operations[patch.operation.value] += 1
            old_path = previous_paths.get(patch.route_key)
            new_path = current_paths.get(patch.route_key)
            if old_path is None or new_path is None or old_path == new_path:
                continue
            old_nodes = _path_nodes(
                patch.route_key.source, old_path, current_connections
            )
            new_nodes = _path_nodes(
                patch.route_key.source, new_path, current_connections
            )
            if old_nodes is None or new_nodes is None:
                continue
            hop_gains[len(old_path) - len(new_path)] += 1
            delay_gains[
                _path_delay_us(old_nodes, old_path, current_connections)
                - _path_delay_us(new_nodes, new_path, current_connections)
            ] += 1

    limits = configuration.get(
        "connection_episode_candidate_max_seconds_by_kind", {}
    )
    if not isinstance(limits, Mapping):
        limits = {}
    candidate_count = sum(
        count
        for kind, durations in midpoint_durations.items()
        for duration, count in durations.items()
        if isinstance(limits.get(kind.value), (int, float))
        and duration <= float(limits[kind.value])
    )
    return {
        "difference_count": len(differences),
        "connection": {
            "additions_by_kind": dict(sorted(additions.items())),
            "removals_by_kind": dict(sorted(removals.items())),
            "completed_episode_boundary_duration_seconds_by_kind": {
                kind.value: _counter_numeric_statistics(values)
                for kind, values in boundary_durations.items()
            },
            "completed_episode_midpoint_duration_seconds_by_kind": {
                kind.value: _counter_numeric_statistics(values)
                for kind, values in midpoint_durations.items()
            },
            "right_censored_episode_count": len(open_episodes),
            "configured_candidates": candidate_count,
        },
        "parameter": {
            "raw_patch_count": sum(
                len(value.connection_parameter_patches)
                for value in differences
            ),
            "raw_field_patch_count": dict(sorted(raw_patch_fields.items())),
            "absolute_delta_by_field": {
                name: _counter_numeric_statistics(values)
                for name, values in absolute_patch_deltas.items()
            },
            "relative_delta_by_field": {
                name: _numeric_statistics(values)
                for name, values in relative_patch_deltas.items()
            },
        },
        "forwarding": {
            "formal_route_patch_count": sum(route_operations.values()),
            "formal_route_operations": dict(sorted(route_operations.items())),
            "supported_prior_path_hop_gain": _counter_numeric_statistics(
                hop_gains
            ),
            "supported_prior_path_directional_delay_gain_us": (
                _counter_numeric_statistics(delay_gains)
            ),
        },
    }


def _parameter_policy_evidence(
    planner: Planner,
    differences: tuple[TopologyDifference, ...],
    connection_transitions: tuple,
    parameter_transitions: tuple,
) -> dict[str, object]:
    connections_by_slice = _transitions_by_slice(
        differences, connection_transitions
    )
    parameters_by_slice = _transitions_by_slice(
        differences, parameter_transitions
    )
    active_connections: dict[object, Connection] = {}
    planned_parameters: dict[
        tuple[object, ConnectionDirection], DirectionalConnectionParameters
    ] = {}
    deviations = {
        name: Counter() for name in (
            "bandwidth_bps",
            "delay_us",
            "packet_loss_ppm",
        )
    }
    relative_deviations = {name: [] for name in deviations}
    emitted_fields = Counter()
    for difference in differences:
        for transition in connections_by_slice.get(
            difference.current_slice_id, ()
        ):
            if transition.operation is ConnectionTransitionOperation.ESTABLISH:
                connection = transition.connection
                assert connection is not None
                active_connections[transition.connection_id] = connection
                for direction in ConnectionDirection:
                    planned_parameters[(transition.connection_id, direction)] = (
                        _directional_parameters(connection, direction)
                    )
            else:
                active_connections.pop(transition.connection_id, None)
                for direction in ConnectionDirection:
                    planned_parameters.pop(
                        (transition.connection_id, direction), None
                    )
        for transition in parameters_by_slice.get(
            difference.current_slice_id, ()
        ):
            key = (transition.connection_id, transition.direction)
            if key not in planned_parameters:
                continue
            previous = planned_parameters[key]
            values = {}
            for field_name in deviations:
                target = getattr(transition, field_name)
                if target is not None:
                    emitted_fields[field_name] += 1
                values[field_name] = (
                    target if target is not None else getattr(previous, field_name)
                )
            planned_parameters[key] = DirectionalConnectionParameters(**values)

        formal_connections = planner._connection_values_by_slice_id.get(
            difference.current_slice_id, {}
        )
        for (connection_id, direction), planned in planned_parameters.items():
            formal_connection = formal_connections.get(connection_id)
            if formal_connection is None:
                continue
            formal = _directional_parameters(formal_connection, direction)
            for field_name in deviations:
                absolute = abs(
                    getattr(planned, field_name) - getattr(formal, field_name)
                )
                if absolute == 0:
                    continue
                deviations[field_name][absolute] += 1
                formal_value = getattr(formal, field_name)
                relative_deviations[field_name].append(
                    absolute / abs(formal_value)
                    if formal_value != 0
                    else float(absolute > 0)
                )
    return {
        "emitted_field_count": dict(sorted(emitted_fields.items())),
        "held_absolute_deviation_by_field": {
            name: _counter_numeric_statistics(values)
            for name, values in deviations.items()
        },
        "held_relative_deviation_by_field": {
            name: _numeric_statistics(values)
            for name, values in relative_deviations.items()
        },
    }


def _forwarding_policy_evidence(
    planner: Planner,
    differences: tuple[TopologyDifference, ...],
    planned_paths_by_slice: Mapping[int, Mapping],
) -> dict[str, object]:
    decisions = Counter()
    hop_stretch = Counter()
    delay_stretch = Counter()
    divergence_started: dict[object, datetime] = {}
    completed_hold_durations = Counter()

    for difference in differences:
        previous_planned = planned_paths_by_slice.get(
            difference.previous_slice_id, {}
        )
        current_planned = planned_paths_by_slice.get(
            difference.current_slice_id, {}
        )
        current_formal = planner._route_path_connection_ids.get(
            difference.current_slice_id, {}
        )
        connections = planner._connection_values_by_slice_id.get(
            difference.current_slice_id, {}
        )
        for patch in difference.route_patches:
            planned_path = current_planned.get(patch.route_key)
            formal_path = current_formal.get(patch.route_key)
            if planned_path == formal_path:
                decisions["FOLLOW"] += 1
                continue
            if planned_path == previous_planned.get(patch.route_key):
                decisions["HOLD"] += 1
            else:
                decisions["OTHER"] += 1
            if planned_path is None or formal_path is None:
                continue
            planned_nodes = _path_nodes(
                patch.route_key.source, planned_path, connections
            )
            formal_nodes = _path_nodes(
                patch.route_key.source, formal_path, connections
            )
            if planned_nodes is None or formal_nodes is None:
                continue
            hop_stretch[len(planned_path) - len(formal_path)] += 1
            delay_stretch[
                _path_delay_us(planned_nodes, planned_path, connections)
                - _path_delay_us(formal_nodes, formal_path, connections)
            ] += 1

        divergent_routes = {
            route_key
            for route_key in current_planned.keys() | current_formal.keys()
            if current_planned.get(route_key) != current_formal.get(route_key)
        }
        for route_key in divergent_routes:
            divergence_started.setdefault(
                route_key, difference.current_timestamp
            )
        for route_key in tuple(divergence_started):
            if route_key in divergent_routes:
                continue
            started = divergence_started.pop(route_key)
            if started is not None:
                completed_hold_durations[
                    round((difference.current_timestamp - started).total_seconds())
                ] += 1

    final_timestamp = differences[-1].current_timestamp
    right_censored = Counter(
        round((final_timestamp - started).total_seconds())
        for started in divergence_started.values()
    )
    return {
        "formal_change_decisions": dict(sorted(decisions.items())),
        "hold_duration_seconds": {
            "completed": _counter_numeric_statistics(
                completed_hold_durations
            ),
            "right_censored": _counter_numeric_statistics(right_censored),
        },
        "held_hop_stretch": _counter_numeric_statistics(hop_stretch),
        "held_directional_propagation_delay_stretch_us": (
            _counter_numeric_statistics(delay_stretch)
        ),
    }


def _transitions_by_slice(
    differences: tuple[TopologyDifference, ...], transitions: tuple
) -> dict[int, list]:
    current_timestamps = tuple(
        difference.current_timestamp for difference in differences
    )
    result: dict[int, list] = {}
    for transition in transitions:
        position = bisect_left(current_timestamps, transition.nominal_time)
        if position >= len(differences):
            raise ValueError("published transition is outside consumed Differences")
        difference = differences[position]
        if (
            difference.previous_timestamp is not None
            and transition.nominal_time <= difference.previous_timestamp
        ):
            raise ValueError("published transition is outside its Difference")
        result.setdefault(difference.current_slice_id, []).append(transition)
    return result


def _difference_midpoint(difference: TopologyDifference) -> datetime:
    if difference.previous_timestamp is None:
        return difference.current_timestamp
    return difference.previous_timestamp + (
        difference.current_timestamp - difference.previous_timestamp
    ) / 2


def _directional_parameters(
    connection: Connection, direction: ConnectionDirection
) -> DirectionalConnectionParameters:
    if direction is ConnectionDirection.ENDPOINT_A_TO_B:
        return connection.endpoint_a_to_b
    return connection.endpoint_b_to_a


def _counter_numeric_statistics(
    values: Counter[int],
) -> dict[str, float | int | None]:
    count = sum(values.values())
    if count == 0:
        return {
            "count": 0,
            "minimum": None,
            "median": None,
            "p95": None,
            "maximum": None,
        }

    def percentile(fraction: float) -> int:
        target = max(1, ceil(fraction * count))
        accumulated = 0
        for value in sorted(values):
            accumulated += values[value]
            if accumulated >= target:
                return value
        raise AssertionError("Counter percentile is unreachable")

    return {
        "count": count,
        "minimum": min(values),
        "median": percentile(0.5),
        "p95": percentile(0.95),
        "maximum": max(values),
    }


def _write_observation_diagnostics_fail_open(
    simulator: Simulator,
    observation_path: Path,
    observation_directory: Path,
) -> tuple[dict[str, str], ...]:
    """Attempt failure-path Observation evidence without replacing cause."""

    try:
        simulator._write_execution_observations()
    except BaseException as error:
        return (_diagnostic_failure("observation-flush", error),)
    try:
        write_observation_tool(observation_path, observation_directory)
    except BaseException as error:
        return (_diagnostic_failure("observation-tool", error),)
    return ()


def _diagnostic_failure(
    operation: str,
    error: BaseException,
) -> dict[str, str]:
    return {
        "operation": operation,
        "exception_module": type(error).__module__,
        "exception_type": type(error).__qualname__,
        "message": _exception_message(error),
    }


def _exception_message(error: BaseException) -> str:
    try:
        return str(error)
    except BaseException as message_error:
        return (
            "<exception message unavailable: "
            f"{type(message_error).__module__}."
            f"{type(message_error).__qualname__}>"
        )


def _topology_context(topology: Topology) -> dict[str, object]:
    satellites: list[NodeId] = []
    ground_stations: list[NodeId] = []
    orbit_by_satellite: dict[NodeId, str] = {}
    orbit_index_by_satellite: dict[NodeId, int] = {}
    expected_adjacent_pairs: set[str] = set()
    for autonomous_system in topology.autonomous_systems:
        ground_stations.extend(
            station.node_id for station in autonomous_system.ground_stations
        )
        for shell in autonomous_system.shells:
            orbit_ids = [orbit.orbit_id for orbit in shell.orbits]
            if len(orbit_ids) > 1:
                expected_adjacent_pairs.update(
                    _plane_pair(
                        orbit_ids[index],
                        orbit_ids[(index + 1) % len(orbit_ids)],
                    )
                    for index in range(len(orbit_ids))
                )
            for orbit in shell.orbits:
                for satellite in orbit.satellites:
                    satellites.append(satellite.node_id)
                    orbit_by_satellite[satellite.node_id] = orbit.orbit_id
                    orbit_index_by_satellite[satellite.node_id] = (
                        satellite.orbit_index
                    )
    return {
        "satellites": tuple(satellites),
        "satellite_set": frozenset(satellites),
        "ground_stations": tuple(ground_stations),
        "ground_station_set": frozenset(ground_stations),
        "known_nodes": frozenset((*satellites, *ground_stations)),
        "zone_ids": frozenset(
            zone.zone_id for zone in topology.get_zone_layout().zones
        ),
        "orbit_by_satellite": orbit_by_satellite,
        "orbit_index_by_satellite": orbit_index_by_satellite,
        "expected_adjacent_pairs": frozenset(expected_adjacent_pairs),
    }


def _verify_slice(
    topology_slice: TopologySlice,
    topology: Topology,
    topology_identity: int,
    current_topology: Topology,
    context: Mapping[str, object],
) -> None:
    if current_topology is not topology or id(current_topology) != topology_identity:
        raise RuntimeError("S4 static Topology identity changed during progression")
    expected_timestamp = REFERENCE_EPOCH + (
        topology_slice.slice_id - 1
    ) * DYNAMIC_SLICE_INTERVAL
    if topology_slice.timestamp != expected_timestamp:
        raise RuntimeError("S4 Slice timestamp does not match SimulationTimeline")
    known_nodes = context["known_nodes"]
    assert isinstance(known_nodes, frozenset)
    if frozenset(topology_slice.positions) != known_nodes:
        raise RuntimeError("S4 Position state is incomplete or contains unknown IDs")
    satellite_set = context["satellite_set"]
    ground_station_set = context["ground_station_set"]
    assert isinstance(satellite_set, frozenset)
    assert isinstance(ground_station_set, frozenset)
    if len(satellite_set) != 348 or len(ground_station_set) != 355:
        raise RuntimeError("S4 static node body changed during dynamic progression")
    for connection in topology_slice.connection_slice.connections:
        identifier = connection.connection_id
        if (
            identifier.endpoint_a not in known_nodes
            or identifier.endpoint_b not in known_nodes
        ):
            raise RuntimeError("S4 Connection references an unknown node")
    zone_ids = context["zone_ids"]
    assert isinstance(zone_ids, frozenset)
    if not set(
        topology_slice.connection_slice.zone_service_assignments
    ).issubset(zone_ids):
        raise RuntimeError("S4 service assignment references an unknown Zone")


def _slice_metrics(
    topology_slice: TopologySlice,
    context: Mapping[str, object],
) -> dict[str, object]:
    satellite_ids = context["satellites"]
    ground_station_ids = context["ground_stations"]
    orbit_by_satellite = context["orbit_by_satellite"]
    orbit_index_by_satellite = context["orbit_index_by_satellite"]
    expected_adjacent_pairs = context["expected_adjacent_pairs"]
    assert isinstance(satellite_ids, tuple)
    assert isinstance(ground_station_ids, tuple)
    assert isinstance(orbit_by_satellite, dict)
    assert isinstance(orbit_index_by_satellite, dict)
    assert isinstance(expected_adjacent_pairs, frozenset)

    total_degree = Counter({node_id: 0 for node_id in satellite_ids})
    total_degree.update({node_id: 0 for node_id in ground_station_ids})
    isl_degree = Counter({node_id: 0 for node_id in satellite_ids})
    satellite_gs_degree = Counter({node_id: 0 for node_id in satellite_ids})
    gs_degree = Counter({node_id: 0 for node_id in ground_station_ids})
    connection_counts = Counter()
    plane_pair_counts = Counter()
    phase_offset_counts = Counter()
    isl_distances: list[float] = []
    longest_inter_plane: list[dict[str, object]] = []

    for connection in topology_slice.connection_slice.connections:
        identifier = connection.connection_id
        first = identifier.endpoint_a
        second = identifier.endpoint_b
        total_degree[first] += 1
        total_degree[second] += 1
        if identifier.kind is ConnectionKind.GROUND_STATION_ACCESS:
            connection_counts["ground_station_access"] += 1
            ground_station = (
                first if first.entity_kind == "ground-station" else second
            )
            satellite = first if first.entity_kind == "satellite" else second
            gs_degree[ground_station] += 1
            satellite_gs_degree[satellite] += 1
            continue

        isl_degree[first] += 1
        isl_degree[second] += 1
        distance = ecef_distance_m(
            topology_slice.positions[first], topology_slice.positions[second]
        )
        isl_distances.append(distance)
        first_orbit = str(orbit_by_satellite[first])
        second_orbit = str(orbit_by_satellite[second])
        if first_orbit == second_orbit:
            connection_counts["intra_orbit_isl"] += 1
            continue
        connection_counts["inter_orbit_isl"] += 1
        pair = _plane_pair(first_orbit, second_orbit)
        plane_pair_counts[pair] += 1
        if first_orbit <= second_orbit:
            first_index = int(orbit_index_by_satellite[first])
            second_index = int(orbit_index_by_satellite[second])
        else:
            first_index = int(orbit_index_by_satellite[second])
            second_index = int(orbit_index_by_satellite[first])
        offset = (second_index - first_index) % 58
        phase_offset_counts[f"{pair}:offset-{offset:02d}"] += 1
        longest_inter_plane.append(
            {
                "endpoint_a": first.value,
                "endpoint_b": second.value,
                "plane_pair": pair,
                "orbit_index_offset_mod_58": offset,
                "distance_m": distance,
            }
        )

    assignments = topology_slice.connection_slice.zone_service_assignments
    zones_per_satellite = Counter({node_id: 0 for node_id in satellite_ids})
    zones_per_satellite.update(assignments.values())
    route_entries = sum(
        len(targets) for targets in topology_slice.route_slice.routes.values()
    )
    represented_targets = {
        _target_identity(target)
        for targets in topology_slice.route_slice.routes.values()
        for target in targets
    }
    longest_inter_plane.sort(
        key=lambda value: float(value["distance_m"]), reverse=True
    )

    return {
        "slice_id": topology_slice.slice_id,
        "timestamp": topology_slice.timestamp.isoformat(),
        "positions": {
            "total": len(topology_slice.positions),
            "satellites": sum(
                node_id.entity_kind == "satellite"
                for node_id in topology_slice.positions
            ),
            "ground_stations": sum(
                node_id.entity_kind == "ground-station"
                for node_id in topology_slice.positions
            ),
        },
        "connections": {
            "total": len(topology_slice.connection_slice.connections),
            "intra_orbit_isl": connection_counts["intra_orbit_isl"],
            "inter_orbit_isl": connection_counts["inter_orbit_isl"],
            "ground_station_access": connection_counts[
                "ground_station_access"
            ],
            "isl_distance_m": _numeric_statistics(isl_distances),
            "inter_orbit_plane_pair_counts": dict(
                sorted(plane_pair_counts.items())
            ),
            "expected_adjacent_plane_pairs_without_links": sorted(
                expected_adjacent_pairs - set(plane_pair_counts)
            ),
            "inter_orbit_index_offset_counts": dict(
                sorted(phase_offset_counts.items())
            ),
            "longest_inter_orbit_links": longest_inter_plane[:10],
        },
        "degrees": {
            "ground_station_access": _distribution(gs_degree.values()),
            "satellite_ground_station_access": _distribution(
                satellite_gs_degree.values()
            ),
            "satellite_total": _distribution(
                total_degree[node_id] for node_id in satellite_ids
            ),
            "satellite_isl": _distribution(
                isl_degree[node_id] for node_id in satellite_ids
            ),
            "isolated_satellites": sum(
                total_degree[node_id] == 0 for node_id in satellite_ids
            ),
        },
        "zones": {
            "served": len(assignments),
            "unserved": len(context["zone_ids"]) - len(assignments),
            "service_satellite_distribution": _distribution(
                zones_per_satellite.values()
            ),
        },
        "routes": {
            "entries": route_entries,
            "sources": len(topology_slice.route_slice.routes),
            "represented_targets": len(represented_targets),
            "demand_policy": (
                "fixed Zone/Gateway candidates; reachable directions only"
            ),
        },
    }


def _difference_metrics(
    difference: TopologyDifference,
    previous: TopologySlice,
    current: TopologySlice,
    context: Mapping[str, object],
) -> dict[str, object]:
    added_classes = Counter(
        _connection_class(connection, context)
        for connection in difference.added_connections
    )
    removed_classes = Counter(
        _connection_id_class(identifier, context)
        for identifier in difference.removed_connection_ids
    )
    parameter_classes = Counter(
        _connection_id_class(patch.connection_id, context)
        for patch in difference.connection_parameter_patches
    )
    zone_operations = Counter(
        patch.operation.value
        for patch in difference.zone_service_assignment_patches
    )
    route_operations = Counter(
        patch.operation.value for patch in difference.route_patches
    )
    route_target_operations = Counter(
        (
            "ground-station"
            if isinstance(patch.route_key.target, GroundStationRouteTarget)
            else "zone"
        )
        + ":"
        + patch.operation.value
        for patch in difference.route_patches
    )
    polar_added = sum(
        _connection_has_polar_satellite(connection, current)
        for connection in difference.added_connections
    )
    polar_removed = sum(
        _connection_id_has_polar_satellite(identifier, previous)
        for identifier in difference.removed_connection_ids
    )
    return {
        "previous_slice_id": difference.previous_slice_id,
        "current_slice_id": difference.current_slice_id,
        "current_timestamp": difference.current_timestamp.isoformat(),
        "connections": {
            "added": len(difference.added_connections),
            "removed": len(difference.removed_connection_ids),
            "directional_parameter_patches": len(
                difference.connection_parameter_patches
            ),
            "added_by_class": dict(sorted(added_classes.items())),
            "removed_by_class": dict(sorted(removed_classes.items())),
            "parameter_patches_by_class": dict(
                sorted(parameter_classes.items())
            ),
            "polar_observation_added": polar_added,
            "polar_observation_removed": polar_removed,
            "polar_observation_latitude_threshold_deg": 70.0,
        },
        "zones": {
            "patches": len(difference.zone_service_assignment_patches),
            "operations": dict(sorted(zone_operations.items())),
        },
        "routes": {
            "patches": len(difference.route_patches),
            "operations": dict(sorted(route_operations.items())),
            "target_operations": dict(sorted(route_target_operations.items())),
        },
        "is_empty": difference.is_empty,
    }


def _route_hop_count_distribution(
    slicer: Slicer,
    topology_slice: TopologySlice,
) -> dict[str, int]:
    hop_counts = Counter()
    for source, targets in topology_slice.route_slice.routes.items():
        for target in targets:
            path = slicer.resolve_path(topology_slice.slice_id, source, target)
            hop_counts[len(path) - 1] += 1
    return {str(key): hop_counts[key] for key in sorted(hop_counts)}


def _connection_class(
    connection: Connection, context: Mapping[str, object]
) -> str:
    return _connection_id_class(connection.connection_id, context)


def _connection_id_class(identifier, context: Mapping[str, object]) -> str:
    if identifier.kind is ConnectionKind.GROUND_STATION_ACCESS:
        return "ground_station_access"
    orbit_by_satellite = context["orbit_by_satellite"]
    assert isinstance(orbit_by_satellite, dict)
    if (
        orbit_by_satellite[identifier.endpoint_a]
        == orbit_by_satellite[identifier.endpoint_b]
    ):
        return "intra_orbit_isl"
    return "inter_orbit_isl"


def _connection_has_polar_satellite(
    connection: Connection, topology_slice: TopologySlice
) -> bool:
    return _connection_id_has_polar_satellite(
        connection.connection_id, topology_slice
    )


def _connection_id_has_polar_satellite(
    identifier, topology_slice: TopologySlice
) -> bool:
    return any(
        endpoint.entity_kind == "satellite"
        and abs(topology_slice.positions[endpoint].latitude_deg) >= 70.0
        for endpoint in (identifier.endpoint_a, identifier.endpoint_b)
    )


def _target_identity(target) -> str:
    if isinstance(target, GroundStationRouteTarget):
        return f"ground-station:{target.ground_station_id.value}"
    if isinstance(target, ZoneRouteTarget):
        return f"zone:{target.zone_id}"
    raise TypeError("RouteSlice contains an unknown RouteTarget")


def _plane_pair(first: str, second: str) -> str:
    return "::".join(sorted((first, second)))


def _distribution(values: Iterable[int]) -> dict[str, int]:
    counts = Counter(values)
    return {str(key): counts[key] for key in sorted(counts)}


def _numeric_statistics(values: Iterable[float]) -> dict[str, float | int | None]:
    ordered = sorted(values)
    if not ordered:
        return {
            "count": 0,
            "minimum": None,
            "median": None,
            "p95": None,
            "maximum": None,
        }
    return {
        "count": len(ordered),
        "minimum": ordered[0],
        "median": median(ordered),
        "p95": ordered[max(0, ceil(0.95 * len(ordered)) - 1)],
        "maximum": ordered[-1],
    }


def _memory_observation() -> dict[str, int]:
    status = _read_key_value_kib(Path("/proc/self/status"))
    host = _read_key_value_kib(Path("/proc/meminfo"))
    return {
        "process_rss_kib": status.get("VmRSS", 0),
        "process_peak_rss_kib": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),
        "host_memory_available_kib": host.get("MemAvailable", 0),
        "host_memory_total_kib": host.get("MemTotal", 0),
    }


def _read_key_value_kib(path: Path) -> dict[str, int]:
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, raw = line.partition(":")
        if not separator:
            continue
        fields = raw.strip().split()
        if fields and fields[0].isdigit():
            values[key] = int(fields[0])
    return values


def _raise_if_memory_exhaustion_is_imminent(memory: Mapping[str, int]) -> None:
    total = memory["host_memory_total_kib"]
    available = memory["host_memory_available_kib"]
    reserve = max(2 * 1024 * 1024, ceil(total * 0.05))
    if total > 0 and available < reserve:
        raise MemoryError(
            "host MemAvailable fell below the larger of 2 GiB and 5% of "
            "MemTotal; S4 progression stopped before host exhaustion"
        )


def _checkpoint_record(
    topology_slice: TopologySlice,
    started: float,
    memory: Mapping[str, int],
) -> dict[str, object]:
    return {
        "slice_id": topology_slice.slice_id,
        "timestamp": topology_slice.timestamp.isoformat(),
        "elapsed_wall_seconds": perf_counter() - started,
        "process_rss_kib": memory["process_rss_kib"],
        "process_peak_rss_kib": memory["process_peak_rss_kib"],
        "host_memory_available_kib": memory["host_memory_available_kib"],
    }


def _detached_tool_record(
    task: str,
    result: Mapping[str, object] | None,
    tasks: Iterable[Mapping[str, object]],
    directory: Path,
    through_slice_id: int,
) -> dict[str, object]:
    task_record = next(
        (value for value in tasks if value.get("task") == task),
        {"task": task, "status": "FAILED", "message": "not scheduled"},
    )
    return {
        "status": None if result is None else result.get("status"),
        "inspection_status": task_record.get("status"),
        "message": (
            task_record.get("message")
            if result is None
            else result.get("message")
        ),
        "through_slice_id": through_slice_id,
        "generation_seconds": task_record.get("wall_seconds"),
        "worker_pid": task_record.get("pid"),
        "worker_peak_rss_bytes": task_record.get("peak_rss_bytes"),
        "handoff_path": task_record.get("handoff_path"),
        "inventory": None if result is None else result.get("inventory"),
        "behavior": None if result is None else result.get("behavior"),
        "files": _directory_sizes(directory),
    }


def _execution_event(
    component: str, event: str, monotonic_time: float
) -> dict[str, object]:
    return {
        "component": component,
        "event": event,
        "timestamp": datetime.now().astimezone().isoformat(),
        "monotonic_seconds": monotonic_time,
        "pid": os.getpid(),
    }


def _inspection_execution_record(
    tasks: Iterable[Mapping[str, object]],
    domain_timeline: Iterable[Mapping[str, object]],
    domain_started: float,
    domain_finished: float,
    join_started: float,
    join_finished: float,
    max_workers: int,
    artifact_gate: bool,
    domain_status: str,
) -> dict[str, object]:
    task_values = tuple(dict(value) for value in tasks)
    starts = [
        float(value["started_monotonic"])
        for value in task_values
        if value.get("started_monotonic") is not None
    ]
    ends = [
        float(value["ended_monotonic"])
        for value in task_values
        if value.get("ended_monotonic") is not None
    ]
    planner_task = next(
        (value for value in task_values if value.get("task") == "planner"),
        None,
    )
    compiler_start = next(
        (
            float(value["monotonic_seconds"])
            for value in domain_timeline
            if value.get("component") == "compiler"
            and value.get("event") == "start"
        ),
        None,
    )
    compiler_end = next(
        (
            float(value["monotonic_seconds"])
            for value in domain_timeline
            if value.get("component") == "compiler"
            and value.get("event") == "end"
        ),
        None,
    )
    planner_overlap = 0.0
    planner_building_at_compiler_start = False
    if (
        planner_task is not None
        and compiler_start is not None
        and compiler_end is not None
        and planner_task.get("ended_monotonic") is not None
    ):
        scheduled = float(planner_task["scheduled_monotonic"])
        worker_start = float(
            planner_task.get("started_monotonic", scheduled)
        )
        worker_end = float(planner_task["ended_monotonic"])
        planner_building_at_compiler_start = (
            scheduled <= compiler_start < worker_end
        )
        planner_overlap = max(
            0.0,
            min(worker_end, compiler_end) - max(worker_start, compiler_start),
        )
    main_memory = _memory_observation()
    return {
        "domain": {
            "status": domain_status,
            "pid": os.getpid(),
            "events": tuple(domain_timeline),
            "wall_seconds": domain_finished - domain_started,
            "peak_rss_bytes": main_memory["process_peak_rss_kib"] * 1024,
        },
        "inspection": {
            "status": "COMPLETE" if artifact_gate else "FAILED",
            "max_process_workers": max_workers,
            "tasks": task_values,
            "materialization_wall_seconds": (
                None if not starts or not ends else max(ends) - min(starts)
            ),
            "final_join_started_monotonic": join_started,
            "final_join_ended_monotonic": join_finished,
            "final_join_seconds": join_finished - join_started,
        },
        "overlap": {
            "planner_tool_building_when_compiler_started": (
                planner_building_at_compiler_start
            ),
            "planner_worker_compiler_overlap_seconds": planner_overlap,
        },
        "end_to_end_cli_through_tool_join_seconds": (
            join_finished - domain_started
        ),
        "host": {
            "logical_cpu_count": os.cpu_count(),
            "memory_total_bytes": (
                _read_key_value_kib(Path("/proc/meminfo")).get(
                    "MemTotal", 0
                )
                * 1024
            ),
            "bounded_worker_policy": (
                "family-level processes; configurable, no per-chunk workers"
            ),
        },
    }


def _completed_record(
    simulator: Simulator,
    static_construction_seconds: float,
    started: float,
    slices: list[dict[str, object]],
    differences: list[dict[str, object]],
    checkpoints: Mapping[str, object],
    slice_tool: Mapping[str, object] | None,
    output: Path,
) -> dict[str, object]:
    slice_seconds = [float(value["slice_generation_seconds"]) for value in slices]
    difference_seconds = [
        float(value["difference_generation_seconds"]) for value in differences
    ]
    rss_values = [int(value["process_rss_kib"]) for value in slices]
    available_values = [int(value["host_memory_available_kib"]) for value in slices]
    zero_pair_slices = sum(
        bool(value["connections"]["expected_adjacent_plane_pairs_without_links"])
        for value in slices
    )
    isolated_satellite_slices = sum(
        int(value["degrees"]["isolated_satellites"]) > 0 for value in slices
    )
    return {
        "format_version": 1,
        "status": "COMPLETE",
        "scope": "Position -> Connection -> Route -> Slice -> Difference",
        "scenario": {
            "identity": "s4_satellite_internet",
            "satellites": 348,
            "ground_stations": 355,
            "dynamic_nodes": 703,
            "zones": 732,
        },
        "timeline": _timeline_record(simulator),
        "models": _model_record(),
        "formal_results": {
            "real_slice_count": len(slices),
            "difference_count": len(differences),
            "retained_slice_count_including_zero": len(
                simulator.get_slicer().get_retained_slice_ids()
            ),
            "topology_identity_preserved": True,
        },
        "checkpoints": checkpoints,
        "timing": {
            "static_construction_seconds": static_construction_seconds,
            "dynamic_qualification_wall_seconds": perf_counter() - started,
            "slice_generation_seconds": _numeric_statistics(slice_seconds),
            "slice_generation_total_seconds": sum(slice_seconds),
            "difference_generation_seconds": _numeric_statistics(
                difference_seconds
            ),
            "difference_generation_total_seconds": sum(difference_seconds),
            "stage_timing_evidence": (
                "Detached Observation records report Slicer and optional "
                "Planner stage series; harness totals here are explicit call "
                "wall clocks and are not inferred by summing overlapping "
                "Observation records."
            ),
        },
        "resources": {
            "process_rss_kib": _numeric_statistics(rss_values),
            "minimum_host_memory_available_kib": min(available_values),
            "resource_stop_rule": (
                "stop before host exhaustion if MemAvailable falls below "
                "max(2 GiB, 5% of MemTotal)"
            ),
        },
        "evolution": {
            "connection_count": _numeric_statistics(
                int(value["connections"]["total"]) for value in slices
            ),
            "route_entry_count": _numeric_statistics(
                int(value["routes"]["entries"]) for value in slices
            ),
            "served_zone_count": _numeric_statistics(
                int(value["zones"]["served"]) for value in slices
            ),
            "connection_additions_total": sum(
                int(value["connections"]["added"]) for value in differences
            ),
            "connection_removals_total": sum(
                int(value["connections"]["removed"]) for value in differences
            ),
            "route_patches_total": sum(
                int(value["routes"]["patches"]) for value in differences
            ),
            "slices_with_missing_adjacent_plane_pair_links": zero_pair_slices,
            "slices_with_isolated_satellites": isolated_satellite_slices,
        },
        "manual_review_observations": {
            "missing_adjacent_plane_pair_link_slices": zero_pair_slices,
            "isolated_satellite_slices": isolated_satellite_slices,
            "interpretation": (
                "Counts are observations from formal Slices; no Connection or "
                "Route policy was tuned from them."
            ),
        },
        "slice_difference_tool": slice_tool,
        "artifacts_before_run_summary": _directory_sizes(output),
        "excluded_stages": [
            "Planner",
            "BPF Compiler",
            "BPF Runner",
            "Compose/runtime containers",
        ],
    }


def _blocked_record(
    error: BaseException,
    simulator: Simulator,
    static_construction_seconds: float,
    started: float,
    slices: list[dict[str, object]],
    differences: list[dict[str, object]],
    checkpoints: Mapping[str, object],
    slice_tool: Mapping[str, object] | None,
) -> dict[str, object]:
    return {
        "format_version": 1,
        "status": "BLOCKED",
        "scope": "Position -> Connection -> Route -> Slice -> Difference",
        "timeline": _timeline_record(simulator),
        "models": _model_record(),
        "static_construction_seconds": static_construction_seconds,
        "elapsed_wall_seconds": perf_counter() - started,
        "completed_real_slice_count": len(slices),
        "completed_difference_count": len(differences),
        "checkpoints": checkpoints,
        "slice_difference_tool": slice_tool,
        "blocker": {
            "type": type(error).__name__,
            "message": str(error),
        },
    }


def _timeline_record(simulator: Simulator) -> dict[str, object]:
    timeline = simulator.get_timeline()
    return {
        "start_time": timeline.start_time.isoformat(),
        "end_time": timeline.end_time.isoformat() if timeline.end_time else None,
        "slice_interval_seconds": timeline.slice_interval.total_seconds(),
        "target_duration_seconds": 30 * 60,
        "expected_real_slice_count_including_endpoints": DYNAMIC_REAL_SLICE_COUNT,
    }


def _model_record() -> dict[str, object]:
    return {
        "position": "CircularOrbitPositionModel",
        "connection": {
            "model": "GeometricConnectionModel",
            "maximum_isl_distance_m": MAXIMUM_ISL_DISTANCE_M,
            "ground_station_minimum_elevation_deg": (
                GROUND_STATION_MINIMUM_ELEVATION_DEG
            ),
            "maximum_ground_station_access_count": (
                MAXIMUM_GROUND_STATION_ACCESS_COUNT
            ),
            "satellite_max_simultaneous_ground_station_accesses": 2,
            "ground_station_maximum_distance_m": (
                GROUND_STATION_MAXIMUM_DISTANCE_M
            ),
            "zone_coverage_minimum_elevation_deg": (
                ZONE_COVERAGE_MINIMUM_ELEVATION_DEG
            ),
            "bandwidth_bps_by_kind": {
                key.value: value for key, value in BANDWIDTH_BPS_BY_KIND.items()
            },
            "packet_loss_ppm_by_kind": {
                key.value: value for key, value in PACKET_LOSS_PPM_BY_KIND.items()
            },
            "propagation_speed_m_per_s": PROPAGATION_SPEED_M_PER_S,
            "access_assignment": (
                "quality-first primary GroundStation access, then residual "
                "redundancy without displacing frozen primary selections"
            ),
            "parameter_authority": (
                "evidence-informed S4 model policy; individual site caps "
                "remain None"
            ),
        },
        "route": {
            "model": "GatewayHopCountRouteModel",
            "gateway_candidate_count": GATEWAY_CANDIDATE_COUNT,
            "gateway_candidate_max_distance_m": (
                GATEWAY_CANDIDATE_MAX_DISTANCE_M
            ),
            "candidate_selection": (
                "static representative-point geographic distance with "
                "stable GroundStation identity tie-break"
            ),
            "parameter_authority": "Skyseed S4 routing policy",
        },
    }


def _slice_tool_context() -> dict[str, object]:
    """Project S4 policy facts for non-authoritative Tool explanation."""

    return {
        "models": {
            "position": {
                "selected": "CircularOrbitPositionModel",
                "role": (
                    "computes the exact formal node positions at each Slice "
                    "timestamp"
                ),
                "configuration": "one accepted S4 circular-orbit shell",
            },
            "connection": {
                "selected": "GeometricConnectionModel",
                "role": (
                    "selects effective ISL, GroundStation access, and Zone "
                    "service facts from current formal geometry"
                ),
                "configuration": (
                    "distance/elevation, Earth-clear LOS, and access caps "
                    "shown below"
                ),
            },
            "route": {
                "selected": "GatewayHopCountRouteModel",
                "role": (
                    "publishes sparse next hops for current Gateway/Zone "
                    "demand on the effective Connection graph"
                ),
                "configuration": (
                    f"{GATEWAY_CANDIDATE_COUNT} static Gateway candidates "
                    "per Zone; minimum hop count"
                ),
            },
        },
        "zone_service": {
            "minimum_elevation_deg": (
                ZONE_COVERAGE_MINIMUM_ELEVATION_DEG
            ),
        },
        "ground_station_access": {
            "minimum_elevation_deg": (
                GROUND_STATION_MINIMUM_ELEVATION_DEG
            ),
            "maximum_slant_distance_km": (
                GROUND_STATION_MAXIMUM_DISTANCE_M / 1_000.0
            ),
            "ground_station_capacity": (
                MAXIMUM_GROUND_STATION_ACCESS_COUNT
            ),
        },
        "isl": {
            "same_orbit_policy": "fixed predecessor/successor ring",
            "cross_plane_policy": (
                "RAAN-neighbour fixed cyclic pairing"
            ),
            "maximum_distance_km": MAXIMUM_ISL_DISTANCE_M / 1_000.0,
            "earth_clearance": "exact Earth-clear LOS required",
        },
        "routing": {
            "model": "GatewayHopCountRouteModel",
            "gateway_candidates_per_zone": GATEWAY_CANDIDATE_COUNT,
            "metric": "minimum hop count on the published Connection graph",
        },
        "interpretation_notes": [
            (
                "Unserved Zones are expected: this workload maps a global "
                "732-Zone surface onto only one 348-Satellite shell."
            ),
            (
                "The 25° Zone-service minimum rejects distant, "
                "low-elevation service merely used to increase nominal "
                "coverage."
            ),
            (
                "Cross-plane ISLs concentrate toward high latitude because "
                "60°-RAAN neighbour planes still require Earth-clear LOS."
            ),
            (
                "A long formal Route can remain the minimum-hop path under "
                "the published access-capacity and ISL graph."
            ),
        ],
    }


def _directory_sizes(directory: Path) -> list[dict[str, object]]:
    if not directory.exists():
        return []
    return [
        {
            "path": path.relative_to(directory).as_posix(),
            "size_bytes": path.stat().st_size,
        }
        for path in sorted(
            value for value in directory.rglob("*") if value.is_file()
        )
    ]


def _append_jsonl(path: Path, value: Mapping[str, object]) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, sort_keys=True, separators=(",", ":")))
        stream.write("\n")


def _write_json(path: Path, value: Mapping[str, object]) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_checksums(
    path: Path,
    files: Iterable[Path],
    root: Path,
) -> None:
    path.write_text(
        "".join(
            f"{_sha256(value)}  {value.relative_to(root).as_posix()}\n"
            for value in files
        ),
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = ["run_s4_dynamic_qualification"]
