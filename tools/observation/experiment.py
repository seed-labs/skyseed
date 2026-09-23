"""Optional reporting after ExperimentController material generation."""
import json
from pathlib import Path
import sys
from time import perf_counter
from uuid import uuid4
from skyseed.experiment.output.history import _reading_completed_range, _completed_range
from tools.observation._presentation.html import write_json as _write_json
from tools.observation._detached_inspection import _DetachedInspectionExecutor
from tools.observation._presentation.html import write_aggregate_index
from tools.observation.physical_world import write_physical_world_tool
from tools.observation.zone import write_zone_tool

def _append_phase(records, name, category, seconds, *, additive,
                  started_monotonic=None, ended_monotonic=None):
    if records is not None:
        ended = perf_counter() if ended_monotonic is None else ended_monotonic
        records.append({"phase": name, "category": category, "wall_seconds": seconds,
                        "additive": additive,
                        "started_monotonic": ended - seconds if started_monotonic is None else started_monotonic,
                        "ended_monotonic": ended})


def _run_dynamic_tools(
    output: Path,
    *,
    through: str,
    model_descriptors: dict[str, object] | None = None,
    phase_records: list[dict[str, object]] | None = None,
    runtime_invocation: Path | None = None,
) -> tuple[dict[str, dict[str, object]], tuple[dict[str, object], ...]]:
    """Run semantic Tools as detached side work after domain success."""

    batch_started = perf_counter()
    inspection = output / "visualizations"
    executor = _DetachedInspectionExecutor(
        output / ".control/tools", max_workers=2
    )
    try:
        planning_path = output / "results/planner/planning.jsonl.gz"
        executor.submit(
            "planner",
            {
                "planner_history_path": planning_path,
                "physical_frames_path": (
                    output / "results/physical-world/physical-frames.jsonl"
                ),
                "physical_link_inspection_path": (
                    output
                    / "results/physical-world/candidates/physical-link-observations.jsonl.gz"
                ),
                "model_descriptors": model_descriptors or {},
            },
            inspection / "planner",
        )
        if through == "compiler":
            executor.submit(
                "compiler",
                {
                    "compiler_history_path": (
                        output / "results/compiler/compiler.jsonl"
                    ),
                    "execution_definition_path": output / "results/compiler/execution.json",
                    "observation_path": (
                        output / "results/experiment/generation-performance.jsonl"
                    ),
                },
                inspection / "bpf-compiler",
            )
        observation_path = output / "results/experiment/generation-performance.jsonl"
        if runtime_invocation is not None:
            observation_path = runtime_invocation / "performance/execution-observations.jsonl"
        executor.submit(
            "performance",
            {"observation_path": observation_path},
            inspection / "performance",
        )
        records = executor.join()
    except BaseException:
        executor.terminate()
        raise
    batch_ended = perf_counter()
    _append_phase(
        phase_records,
        "detached-tool-batch",
        "detached-inspection",
        batch_ended - batch_started,
        additive=True,
        started_monotonic=batch_started,
        ended_monotonic=batch_ended,
    )
    results = {}
    for record in records:
        task = str(record["task"])
        key = "bpf-compiler" if task == "compiler" else task
        result = executor.result(task)
        results[key] = (
            dict(result)
            if result is not None and record["status"] == "COMPLETE"
            else {
                "status": "FAILED",
                "message": str(record.get("message", "Tool failed")),
            }
        )
        results[key]["duration_seconds"] = float(record.get("wall_seconds", 0.0))
        _append_phase(
            phase_records,
            f"tool:{key}",
            "detached-inspection",
            float(record.get("wall_seconds", 0.0)),
            additive=False,
            started_monotonic=(
                None
                if record.get("started_monotonic") is None
                else float(record["started_monotonic"])
            ),
            ended_monotonic=(
                None
                if record.get("ended_monotonic") is None
                else float(record["ended_monotonic"])
            ),
        )
    return results, records

def _write_tool_records(
    inspection: Path, records: tuple[dict[str, object], ...]
) -> None:
    inspection.mkdir(parents=True, exist_ok=True)
    (inspection / "tool-executions.json").write_text(
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


def _write_experiment_tools(output: Path, *, physical_world, results, phases) -> None:
    """Render maintained generation Tools from already-produced facts.

    ``physical_world`` supplies static geometry/identity. Frames, candidates,
    Plans and compiled evidence come only from the sealed output histories.
    Failures are reported as Tool failures and leave the domain result intact.
    """
    output = Path(output)
    status_path = output / "results/experiment/generation-status.json"
    status = _completed_range.get()[1]
    if status["status"] not in {"RANGE_READY", "MATERIALS_READY", "TOOLS_COMPLETE", "TOOLS_FAILED"}:
        raise ValueError("Tools require completed generation materials")
    inspection = output / "visualizations"
    inspection.mkdir(parents=True, exist_ok=True)
    descriptors = status["configuration"]
    # Runtime's final index is published after its evidence drains. A live
    # invocation must not divert completed generation views into partial records.
    runtime_invocation = None
    latest = output / "runs/latest.json"
    if latest.is_file():
        runtime = json.loads(latest.read_text())
        if runtime.get("status") != "running":
            runtime_invocation = output / "runs" / runtime["invocation_id"]
    timing = output / "results/experiment/orchestration-timing.json"
    if timing.is_file():
        try:
            payload = json.loads(timing.read_text())
            (inspection / "orchestration-timing.js").write_text("window.SKYSEED_ORCHESTRATION_TIMING=" + json.dumps(payload) + ";\n")
        except Exception as error:
            print(f"orchestration presentation degraded: {error}", file=sys.stderr)
    if status.get("observation_evidence_failure"):
        results["observation-evidence"] = {"status": "FAILED", "message": status["observation_evidence_failure"]}
    if status["producer"].get("formal_observation_sink_failure"):
        results["physical-link-evidence"] = {"status": "FAILED", "message": status["producer"]["formal_observation_sink_failure"]}
    calls = {"physical-world": lambda: write_physical_world_tool(
        physical_world, inspection / "physical-world",
        physical_frames_path=output / "results/physical-world/physical-frames.jsonl",
        physical_link_inspection_path=output / "results/physical-world/candidates/physical-link-observations.jsonl.gz",
        model_descriptors=descriptors)}
    if descriptors["zones"] is not None:
        calls["zone"] = lambda: write_zone_tool(physical_world, inspection / "zone")
    if (output / 'results/traffic/workload.json').is_file():
        from tools.generation.traffic.preview import write_traffic_preview
        calls['traffic-generation'] = lambda: write_traffic_preview(output, publication=status)
    for name, call in calls.items():
        tool_start = perf_counter()
        try:
            results[name] = dict(call())
        except Exception as error:
            results[name] = {"status": "FAILED", "message": f"{type(error).__name__}: {error}"}
        results[name]["duration_seconds"] = perf_counter() - tool_start
        _append_phase(phases, f"tool:{name}", "detached-inspection", results[name]["duration_seconds"], additive=True)
    records = ()
    if status["through"] != "physical-world":
        try:
            dynamic, records = _run_dynamic_tools(output, through=status["through"],
                                                  model_descriptors=descriptors, phase_records=phases,
                                                  runtime_invocation=runtime_invocation)
            results.update(dynamic)
        except Exception as error:
            results["inspection-batch"] = {"status": "FAILED", "message": f"{type(error).__name__}: {error}"}
    if runtime_invocation is not None:
        from tools.observation.runner import write_runner_tool
        from tools.observation.traffic import write_traffic_replay_tool
        invocation = runtime_invocation
        handoff = json.loads((invocation / "runner/runner-invocation.json").read_text())
        has_workload = (output / "results/traffic/workload.json").is_file()
        for name, call in {
            "runner": lambda: write_runner_tool(handoff["runner_snapshot"], handoff["runtime_outcome"],
                inspection / "runner", event_history_path=invocation / "runner/runner-events.jsonl",
                deployment_startup_path=output / "deployment/deployment-startup.json"),
            "traffic": lambda: write_traffic_replay_tool(invocation / "traffic/traffic-telemetry.jsonl",
                output / "results/physical-world/physical-frames.jsonl", inspection / "traffic",
                workload_path=output / "results/traffic/workload.json" if has_workload else None,
                probe_execution_path=invocation / "traffic/probe-execution.jsonl" if has_workload else None),
        }.items():
            if name == "traffic" and not has_workload and not (invocation / "traffic/traffic-telemetry.jsonl").is_file():
                continue
            tool_start = perf_counter()
            try:
                results[name] = dict(call())
                results[name]["directory"] = str(results[name]["directory"])
            except Exception as error:
                results[name] = {"status": "FAILED", "message": f"{type(error).__name__}: {error}"}
            results[name]["duration_seconds"] = perf_counter() - tool_start
    try:
        _write_tool_records(inspection, records)
        write_aggregate_index(inspection, results, dict(results.get("physical-world", {}).get("counts", {})))
    except Exception as error:
        results["aggregate"] = {"status": "FAILED", "message": f"{type(error).__name__}: {error}"}


__all__ = ["write_experiment_tools"]


def write_experiment_tools(output: Path, *, physical_world, publication=None):
    """Render optional presentation and report degraded output without revoking materials."""
    started = perf_counter()
    results, phases = {}, []
    inspection = Path(output) / "visualizations"
    try:
        if publication is None:
            publication = json.loads((Path(output) / "results/experiment/completed.json").read_text())
        with _reading_completed_range(output, publication):
            _write_experiment_tools(output, physical_world=physical_world,
                                    results=results, phases=phases)
    except Exception as error:
        message = f"{type(error).__name__}: {error}"
        print(f"detached presentation failed; materials remain available: {message}", file=sys.stderr)
        results["presentation"] = {"status": "FAILED", "message": message,
                                   "duration_seconds": perf_counter() - started}
    # Preserve each invocation, including partial results when optional evidence
    # cannot be read. Generation and runtime completion facts remain untouched.
    successful = {"PASS", "COMPLETE", "COMPLETED", "WARN"}
    failed = any(result.get("status") not in successful for result in results.values())
    for phase in phases:
        phase["run_relative_start_seconds"] = phase.pop("started_monotonic") - started
        phase["run_relative_end_seconds"] = phase.pop("ended_monotonic") - started
    invocation_id = uuid4().hex
    invocation = {"invocation_id": invocation_id, "status": "DEGRADED" if failed else "COMPLETE",
                  "results": results, "wall_seconds": perf_counter() - started, "records": phases,
                  "time_basis": "relative to this Tool invocation; independent of generation/runtime"}
    try:
        _write_json(Path(output) / "results/tools/tool-runs" / f"{invocation_id}.json", invocation)
        _write_json(inspection / "tool-timing.json", invocation)
    except Exception as error:
        results["tool-recording"] = {"status": "FAILED", "message": f"{type(error).__name__}: {error}"}
        failed = True
    if failed:
        print("Materials remain ready; Tool failures: " + ", ".join(
            name for name, result in results.items() if result.get("status") not in successful), file=sys.stderr)
    return results
