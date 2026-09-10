"""Build the Mini reference through the sole Planner/Compiler producer."""

from __future__ import annotations

import argparse
import json
from time import perf_counter
from pathlib import Path
import re
import subprocess
import sys
from uuid import uuid4

from yaml import safe_load


EXAMPLE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = EXAMPLE_ROOT.parents[1]
DEFAULT_SCENARIO = EXAMPLE_ROOT / "scenario.yaml"
DEFAULT_ZONES = EXAMPLE_ROOT / "zones.yaml"
DEFAULT_OUTPUT = EXAMPLE_ROOT / "output"
COMPOSE_PROJECT_PREFIX = "skyseed_v2_mini_satellite_internet"
_PROJECT_NAME_FILE = "runtime/compose-project-name"
_REALIZATION_PRODUCER_IDENTITY = (
    "skyseed-v2-common-incremental-realization-producer-v1"
)

if __package__ in (None, ""):
    sys.path.insert(0, str(PROJECT_ROOT))

from examples.mini_satellite_internet.emu import (
    build_mini_satellite_internet_emulator,
)
from examples.mini_satellite_internet.simu import (
    create_mini_satellite_internet_producer,
    mini_satellite_internet_configuration,
)
from examples.mini_satellite_internet.traffic_workload import (
    planned_zone_service_segments,
    write_canonical_traffic_workload,
)
from seedemu.compiler import Docker
from skyseed_v2.compose.launcher import (
    _prepare_generated_output,
    _write_compose_project_scripts,
)
from skyseed_v2.backend.bpf._runner_artifact import (
    _write_runner_artifact,
)
from skyseed_v2.experiment.execution import (
    _write_execution_definition,
    _write_generated_run_script,
)
from skyseed_v2.experiment.traffic.probe_plan import (
    IncrementalTrafficProbePlanBuilder,
    write_traffic_probe_plan,
)
from tools._html import write_aggregate_index
from tools.bpf_compiler import write_bpf_compiler_tool
from tools.observation import write_observation_tool
from tools.physical_world import write_physical_world_tool
from tools.planner import write_planner_history_tool
from tools.zone import write_zone_tool


def _capture_input_files(
    scenario_path: Path,
    zones_path: Path,
) -> dict[Path, bytes]:
    """Read exact external Mini inputs before replacing any output."""

    scenario = Path(scenario_path).resolve()
    zones = Path(zones_path).resolve()
    try:
        scenario_bytes = scenario.read_bytes()
        zones_bytes = zones.read_bytes()
    except OSError as error:
        raise RuntimeError(f"cannot snapshot Mini input: {error}") from error
    try:
        payload = safe_load(scenario_bytes)
    except Exception as error:
        raise RuntimeError("Mini scenario is not valid YAML") from error
    if not isinstance(payload, dict):
        raise RuntimeError("Mini scenario must contain a YAML mapping")

    snapshots = {
        Path("scenario.yaml"): scenario_bytes,
        Path("zones.yaml"): zones_bytes,
    }
    autonomous_systems = payload.get("skyseed_autonomous_systems", [])
    if not isinstance(autonomous_systems, list):
        raise RuntimeError(
            "Mini scenario skyseed_autonomous_systems must be a list"
        )
    for index, value in enumerate(autonomous_systems):
        if not isinstance(value, dict) or "ground_stations_file" not in value:
            continue
        raw_reference = value["ground_stations_file"]
        if not isinstance(raw_reference, str) or not raw_reference.strip():
            raise RuntimeError(
                "Mini scenario ground_stations_file must be a relative path"
            )
        reference = Path(raw_reference)
        if reference.is_absolute() or ".." in reference.parts:
            raise RuntimeError(
                "Mini scenario ground_stations_file must stay within its source root"
            )
        source = (scenario.parent / reference).resolve()
        try:
            source.relative_to(scenario.parent)
        except ValueError as error:
            raise RuntimeError(
                "Mini scenario ground_stations_file resolves outside its source root"
            ) from error
        if reference in {Path("scenario.yaml"), Path("zones.yaml")}:
            raise RuntimeError(
                "Mini scenario ground_stations_file conflicts with a canonical input name"
            )
        if reference in snapshots:
            continue
        try:
            snapshots[reference] = source.read_bytes()
        except OSError as error:
            raise RuntimeError(
                "cannot snapshot ground_stations_file for autonomous system "
                f"{index}"
            ) from error
    return snapshots


def _guard_existing_output(output: Path, *, override: bool) -> None:
    """Refuse to replace controls for an existing Compose generation."""

    if not output.exists() and not output.is_symlink():
        return
    if not override:
        raise FileExistsError(f"example output already exists: {output}")
    project_file = output / _PROJECT_NAME_FILE
    if not project_file.is_file():
        legacy = output / ".compose-project-name"
        if legacy.is_file():
            project_file = legacy
    if not project_file.is_file():
        if (output / "runtime/docker-compose.yml").is_file():
            raise RuntimeError(
                "existing generated output has no verifiable Compose identity; "
                "remove it explicitly before regenerating"
            )
        return
    project_name = project_file.read_text(encoding="utf-8").strip()
    if re.fullmatch(r"[a-z0-9][a-z0-9_-]*", project_name) is None:
        raise RuntimeError("existing output generation identity is invalid")
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
        raise RuntimeError(
            "cannot verify whether the existing deployment is still present"
        ) from error
    if result.returncode != 0:
        raise RuntimeError(
            "cannot verify the existing deployment: " + result.stderr.strip()
        )
    if result.stdout.strip():
        raise RuntimeError(
            "the existing deployment still has containers; run "
            f"{output / 'cleanup.sh'} before regenerating"
        )


def _write_input_snapshots(
    output: Path, snapshots: dict[Path, bytes]
) -> None:
    inputs = output / "inputs"
    for relative_path, content in snapshots.items():
        destination = inputs / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)


def _write_inspection_tools(
    output: Path,
    *,
    simulator,
    plans,
    realizations,
    physical_frame,
    physical_link_observation,
    through="compiler",
) -> tuple[dict[str, dict[str, object]], dict[str, str]]:
    """Run detached observers without controlling Runner artifact validity."""

    inspection = output / "inspection"
    results: dict[str, dict[str, object]] = {}
    failures: dict[str, str] = {}
    calls = {
        "physical-world": lambda: write_physical_world_tool(
            simulator.get_physical_world(),
            inspection / "physical-world",
            physical_frames_path=(
                output / "domain-history/physical-frames.jsonl"
            ),
            physical_frame=physical_frame,
            physical_link_observation=physical_link_observation,
            physical_link_model=simulator.get_physical_link_model(),
        ),
        "zone": lambda: write_zone_tool(
            simulator.get_physical_world(), inspection / "zone"
        ),
        "planner": lambda: write_planner_history_tool(
            output / "domain-history/planning.jsonl.gz",
            inspection / "planner",
            physical_frames_path=(
                output / "domain-history/physical-frames.jsonl"
            ),
        ),
        "observation": lambda: write_observation_tool(
            inspection / "observation/execution-observations.jsonl",
            inspection / "observation",
        ),
        "bpf-compiler": lambda: write_bpf_compiler_tool(
            plans,
            realizations,
            inspection / "bpf-compiler",
            observation_path=(
                inspection / "observation/execution-observations.jsonl"
            ),
        ),
    }
    if through == 'physical-world':
        calls.pop('planner')
    if through != 'compiler':
        calls.pop('bpf-compiler')
    for name, call in calls.items():
        try:
            results[name] = dict(call())
        except Exception as error:
            failures[name] = f"{type(error).__name__}: {error}"
            results[name] = {
                "status": "FAILED",
                "message": failures[name],
            }
    counts = dict(results.get("physical-world", {}).get("counts", {}))
    try:
        write_aggregate_index(inspection, results, counts)
    except Exception as error:
        failures["aggregate"] = f"{type(error).__name__}: {error}"
    return results, failures


def _write_optional_observations(simulator):
    try:
        simulator._write_execution_observations()
    except Exception as error:
        return f"{type(error).__name__}: {error}"
    return None


def main(argv: list[str] | None = None) -> int:
    total_started = perf_counter()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--through", choices=("physical-world", "planner", "compiler"), default="compiler")
    parser.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO)
    parser.add_argument("--zones", type=Path, default=DEFAULT_ZONES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--override",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="replace existing inactive generated output (default: enabled)",
    )
    parser.add_argument(
        "--no-internet-map",
        action="store_true",
        help="disable InternetMap for fault isolation only",
    )
    arguments = parser.parse_args(argv)
    arguments.output = arguments.output.expanduser().resolve()

    snapshots = _capture_input_files(arguments.scenario, arguments.zones)
    _guard_existing_output(arguments.output, override=arguments.override)
    runtime_output = _prepare_generated_output(arguments.output, replace=True)
    _write_input_snapshots(arguments.output, snapshots)
    snapshot_scenario = arguments.output / "inputs/scenario.yaml"

    emulator = build_mini_satellite_internet_emulator(snapshot_scenario)
    if arguments.through == "compiler":
        emulator.render()
        emulator.compile(
            Docker(
                namingScheme="as{asn}{role}-{displayName}",
                internetMapEnabled=not arguments.no_internet_map,
            ),
            str(runtime_output),
        )
    compose_project_name = f"{COMPOSE_PROJECT_PREFIX}-{uuid4().hex[:16]}"
    project_file = arguments.output / _PROJECT_NAME_FILE
    project_file.parent.mkdir(parents=True, exist_ok=True)
    project_file.write_text(f"{compose_project_name}\n", encoding="utf-8")

    simulator, producer = create_mini_satellite_internet_producer(
        arguments.output / "inputs",
        emulator=emulator,
        evidence_path=arguments.output / "domain-history",
    )
    observation_path = (
        arguments.output
        / "inspection/observation/execution-observations.jsonl"
    )
    simulator._enable_execution_observation(
        observation_path,
        metadata={
            "scenario_identity": "mini_satellite_internet",
            "timeline_start": simulator.get_timeline().start_time.isoformat(),
            "timeline_end": simulator.get_timeline().end_time.isoformat(),
            "observation_interval_seconds": (
                simulator.get_timeline().observation_interval.total_seconds()
            ),
            "progression": "physical-world-planner-plan-compiler",
        },
    )
    plans = []
    realizations = []
    traffic_builder = IncrementalTrafficProbePlanBuilder(physical_world=simulator.get_physical_world()) if arguments.through == 'compiler' else None
    try:
        while True:
            if arguments.through == 'physical-world':
                if producer.produce_next_observation() is None:
                    break
            elif arguments.through == 'planner':
                plan = producer.produce_next_plan()
                if plan is None:
                    break
                plans.append(plan)
            else:
                realization = producer.produce_next_realization()
                if realization is None:
                    break
                plans.append(producer.latest_plan)
                realizations.append(realization)
                traffic_builder.append(producer.latest_plan, realization)
        producer_snapshot = producer.snapshot()
        physical_frame, physical_link_observation = (
            producer.first_formal_input()
        )
    finally:
        producer.close()
    plan_values = tuple(plans)
    realization_values = tuple(realizations)
    def readiness(status, materials_seconds, tools=None):
        elapsed = perf_counter()-total_started
        value = {'format_version': 1, 'artifact_type': 'finite-generation-status',
            'through': arguments.through, 'status': status,
            'materials_ready_seconds': materials_seconds,
            'tools_completion_seconds': elapsed if tools is not None else None,
            'tools_wall_seconds': elapsed-materials_seconds if tools is not None else None,
            'producer': producer_snapshot, 'tools': tools,
            'configuration': mini_satellite_internet_configuration(),
            'python_executable': sys.executable}
        path = arguments.output / 'generation-status.json'
        staging = path.with_suffix('.tmp')
        staging.write_text(json.dumps(value, indent=2, sort_keys=True, default=str)+'\n', encoding='utf-8')
        staging.replace(path)
    if arguments.through != 'compiler':
        _write_optional_observations(simulator)
        materials_seconds = perf_counter()-total_started
        readiness('MATERIALS_READY', materials_seconds)
        print(f'Materials ready after {materials_seconds:.3f}s: {arguments.output}', flush=True)
        results, failures = _write_inspection_tools(arguments.output, simulator=simulator,
            plans=plan_values, realizations=realization_values, physical_frame=physical_frame,
            physical_link_observation=physical_link_observation, through=arguments.through)
        readiness('TOOLS_FAILED' if failures else 'TOOLS_COMPLETE', materials_seconds, results)
        print(f'{arguments.through}: {producer_snapshot}')
        return 0
    if not plan_values or len(plan_values) != len(realization_values):
        raise RuntimeError("Mini producer did not emit a complete Plan lineage")

    artifact_manifest = _write_runner_artifact(
        realization_values,
        arguments.output / "runtime/runner-artifact",
        execution_observer=simulator.get_planner()._get_execution_observer(),
    )
    traffic_output = arguments.output / "inspection/traffic"
    write_traffic_probe_plan(
        traffic_builder.finalize(),
        traffic_output / "probe-plan.json",
    )
    timeline = simulator.get_timeline()
    write_canonical_traffic_workload(
        arguments.output / "inputs",
        simulation_start=timeline.start_time,
        simulation_end=timeline.end_time,
        zone_prefixes=dict(realization_values[-1].zone_prefixes),
        zone_service_segments=planned_zone_service_segments(
            plan_values,
            realization_values,
            simulation_start=timeline.start_time,
            simulation_end=timeline.end_time,
        ),
    )
    _write_execution_definition(
        arguments.output,
        generated_output_identity=compose_project_name,
        timeline_start=timeline.start_time,
        timeline_end=timeline.end_time,
        observation_interval=timeline.observation_interval,
        project_root=PROJECT_ROOT,
        example_root=EXAMPLE_ROOT,
        runner_artifact=str(
            artifact_manifest.relative_to(arguments.output).as_posix()
        ),
        realization_producer=_REALIZATION_PRODUCER_IDENTITY,
    )
    _write_compose_project_scripts(arguments.output, compose_project_name)
    _write_generated_run_script(
        arguments.output,
        python_executable=sys.executable,
        compiler_tool_directory="bpf-compiler",
        tool_package_parent=PROJECT_ROOT,
        required_input_paths=(
            "inputs/scenario.yaml",
            "inputs/zones.yaml",
        ),
        traffic_workload_path="inputs/traffic-workload.json",
    )
    observation_failure = _write_optional_observations(simulator)
    materials_seconds = perf_counter()-total_started
    readiness('MATERIALS_READY', materials_seconds)
    print(f'Materials ready after {materials_seconds:.3f}s: {arguments.output}', flush=True)
    try:
        tool_results, tool_failures = _write_inspection_tools(
            arguments.output,
            simulator=simulator,
            plans=plan_values,
            realizations=realization_values,
            physical_frame=physical_frame,
            physical_link_observation=physical_link_observation,
        )
    except Exception as error:
        tool_failures = {
            "inspection-batch": f"{type(error).__name__}: {error}"
        }
    if observation_failure is not None:
        tool_failures["observation-evidence"] = observation_failure
    readiness("TOOLS_FAILED" if tool_failures else "TOOLS_COMPLETE", materials_seconds,
              tool_results if not tool_failures else {"failures": tool_failures})

    print(f"runtime directory: {runtime_output}")
    print(
        "producer: "
        f"{producer_snapshot['plan_count']} Plans / "
        f"{producer_snapshot['transition_count']} formal transitions"
    )
    print(f"Runner artifact: {artifact_manifest}")
    print(f"start: {arguments.output / 'start.sh'}")
    print(f"run: {arguments.output / 'run.sh'}")
    print(f"launch: {arguments.output / 'launch.sh'}")
    print(f"cleanup: {arguments.output / 'cleanup.sh'}")
    print(f"Inspection index: {arguments.output / 'inspection/index.html'}")
    if tool_failures:
        print(
            "Inspection side-work failures did not invalidate Runner artifact: "
            + ", ".join(sorted(tool_failures)),
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
