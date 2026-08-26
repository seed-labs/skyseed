"""Build the mini satellite Internet reference and all inspection views."""

from __future__ import annotations

import argparse
from datetime import timedelta
from pathlib import Path
import re
from shlex import quote
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
_PROJECT_NAME_FILE = ".compose-project-name"
_SIMULATION_GENERATION_LOOKAHEAD = timedelta(seconds=300)
_RUNNER_PREPARATION_LOOKAHEAD = timedelta(seconds=60)
_RUNNER_PREPARATION_BOUNDARY_DEPTH = 8

if __package__ in (None, ""):
    sys.path.insert(0, str(PROJECT_ROOT))

from examples._direct_execution import prepare_direct_file_execution

prepare_direct_file_execution(__file__)

from examples._compose_project_scripts import (
    prepare_example_output,
    write_compose_project_scripts,
)
from examples.mini_satellite_internet.emu import (
    build_mini_satellite_internet_emulator,
)
from examples.mini_satellite_internet.simu import (
    REAL_SLICE_COUNT,
    create_mini_satellite_internet_progression,
    prepare_mini_satellite_internet_planning,
)
from examples.mini_satellite_internet.traffic_workload import (
    planned_zone_service_segments,
    write_canonical_traffic_workload,
)
from seedemu.compiler import Docker
from skyseed_v2.backend.bpf import BpfCompiledRealization, compile_bpf
from skyseed_v2._generated_bpf_execution import _write_execution_definition
from skyseed_v2.simulator import (
    Plan,
    Planner,
    Slicer,
)
from tools.slice import write_slice_difference_tool
from tools.bpf_compiler import write_bpf_compiler_tool
from tools.observation import write_observation_tool
from tools.planner import write_planner_tool
from tools.topology import write_topology_tool
from tools.zone import write_zone_tool


def _compile_bpf_realizations(
    slicer: Slicer,
    plans: tuple[Plan, ...],
) -> tuple[BpfCompiledRealization, ...]:
    """Compile the continuous Mini Plan chain for the inspection view."""

    compiled = []
    previous = None
    for plan in plans:
        result = compile_bpf(plan, slicer=slicer, previous=previous)
        compiled.append(result)
        previous = result
    return tuple(compiled)


def write_planner_inspection(
    slicer: Slicer,
    planner: Planner,
    plans: tuple[Plan, ...],
    configuration: dict[str, object],
    output: Path,
) -> dict[str, object]:
    """Write a Tool view from already-prepared Planner results."""

    return write_planner_tool(
        slicer,
        plans,
        planner.get_consumed_differences(),
        planner.get_planning_decisions(),
        configuration,
        output,
    )


def _with_planning_windows(
    configuration: dict[str, object], observer
) -> dict[str, object]:
    """Attach compact exact Planner synchronization coverage for the Tool."""

    return {
        **configuration,
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
            for record in observer.snapshot()["records"]
            if record["component"] == "planner"
            and record["stage"] == "total-synchronize"
        ),
    }


def _capture_input_files(
    scenario_path: Path,
    zones_path: Path,
) -> dict[Path, bytes]:
    """Read the exact external Mini inputs before replacing any output."""

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
        raise RuntimeError("Mini scenario skyseed_autonomous_systems must be a list")
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
                f"cannot snapshot ground_stations_file for autonomous system {index}"
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
        raise RuntimeError(
            "existing generated output uses an unsupported older layout; "
            "clean/remove its deployment and output before regenerating"
        )
    try:
        project_name = project_file.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise RuntimeError("cannot read existing output generation identity") from error
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
            "cannot verify whether the existing generated deployment is still present"
        ) from error
    if result.returncode != 0:
        raise RuntimeError(
            "cannot verify whether the existing generated deployment is still present; "
            f"docker ps failed: {result.stderr.strip()}"
        )
    if any(value.strip() for value in result.stdout.splitlines()):
        raise RuntimeError(
            "the existing generated deployment still has containers; "
            f"run {output / 'cleanup.sh'} before regenerating"
        )


def _write_input_snapshots(output: Path, snapshots: dict[Path, bytes]) -> None:
    inputs = output / "inputs"
    for relative_path, content in snapshots.items():
        destination = inputs / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)


def _write_execution_scripts(output: Path) -> None:
    """Publish standard generated BPF execution controls."""

    python = quote(sys.executable)
    _write_executable(
        output / "run.sh",
        f"""#!/usr/bin/env bash
set -euo pipefail

if (( $# != 0 )); then
    echo "run.sh does not accept arguments" >&2
    exit 2
fi
SCRIPT_DIR="$(cd -- "$(dirname -- "${{BASH_SOURCE[0]}}")" && pwd)"
for required in \\
    "$SCRIPT_DIR/{_PROJECT_NAME_FILE}" \\
    "$SCRIPT_DIR/inputs/execution.json" \\
    "$SCRIPT_DIR/inputs/scenario.yaml" \\
    "$SCRIPT_DIR/inputs/zones.yaml" \\
    "$SCRIPT_DIR/runtime/docker-compose.yml"; do
    if [[ ! -f "$required" ]]; then
        echo "generated BPF execution input is missing: $required" >&2
        exit 1
    fi
done

execution_status=0
{python} -m skyseed_v2._generated_bpf_execution "$SCRIPT_DIR" \\
    || execution_status=$?

runner_tool_status=0
{python} -m tools.runner \\
    --handoff "$SCRIPT_DIR/inspection/runner/runner-invocation.json" \\
    --events "$SCRIPT_DIR/inspection/runner/runner-events.jsonl" \\
    --output "$SCRIPT_DIR/inspection/runner" \\
    || runner_tool_status=$?

traffic_tool_status=0
{python} -m tools.traffic \\
    --telemetry "$SCRIPT_DIR/inspection/traffic/traffic-telemetry.jsonl" \\
    --slice "$SCRIPT_DIR/inspection/slice/slice-manifest.json" \\
    --bpf-compiler "$SCRIPT_DIR/inspection/bpf-compiler/manifest.json" \\
    --output "$SCRIPT_DIR/inspection/traffic" \\
    || traffic_tool_status=$?

if (( execution_status != 0 )); then
    exit "$execution_status"
fi
if (( runner_tool_status != 0 )); then
    exit "$runner_tool_status"
fi
exit "$traffic_tool_status"
""",
    )
    _write_executable(
        output / "launch.sh",
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
    )


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO)
    parser.add_argument("--zones", type=Path, default=DEFAULT_ZONES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--override",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="replace the existing scenario output (default: enabled)",
    )
    parser.add_argument(
        "--no-internet-map",
        action="store_true",
        help="disable InternetMap for fault isolation only",
    )
    arguments = parser.parse_args()

    snapshots = _capture_input_files(arguments.scenario, arguments.zones)
    _guard_existing_output(arguments.output, override=arguments.override)
    runtime_output = prepare_example_output(
        arguments.output,
        override=True,
    )
    compose_project_name = (
        f"{COMPOSE_PROJECT_PREFIX}-{uuid4().hex[:16]}"
    )
    (arguments.output / _PROJECT_NAME_FILE).write_text(
        f"{compose_project_name}\n",
        encoding="utf-8",
    )
    _write_input_snapshots(arguments.output, snapshots)
    snapshot_scenario = arguments.output / "inputs/scenario.yaml"
    snapshot_zones = arguments.output / "inputs/zones.yaml"

    emulator = build_mini_satellite_internet_emulator(snapshot_scenario)
    topology_output = arguments.output / "inspection/topology"
    zone_output = arguments.output / "inspection/zone"
    slice_output = arguments.output / "inspection/slice"
    planner_output = arguments.output / "inspection/planner"
    observation_output = arguments.output / "inspection/observation"
    bpf_compiler_output = arguments.output / "inspection/bpf-compiler"
    traffic_workload_input = arguments.output / "inputs"

    emulator.render()
    emulator.compile(
        Docker(
            namingScheme="as{asn}{role}-{displayName}",
            internetMapEnabled=not arguments.no_internet_map,
        ),
        str(runtime_output),
    )
    simulator, planner = create_mini_satellite_internet_progression(
        arguments.output / "inputs",
        emulator=emulator,
    )
    slicer = simulator.get_slicer()
    observation_records = observation_output / "execution-observations.jsonl"
    observer = simulator._enable_execution_observation(
        observation_records,
        metadata={
            "scenario_identity": "mini_satellite_internet",
            "timeline_start": slicer.get_timeline().start_time.isoformat(),
            "slice_interval_seconds": (
                slicer.get_timeline().slice_interval.total_seconds()
            ),
            "real_slice_count": REAL_SLICE_COUNT,
        },
    )
    created_slices = slicer.create_all_slices()
    if len(created_slices) != REAL_SLICE_COUNT:
        raise AssertionError("bounded reference Timeline produced wrong count")
    planner, plans, planning_configuration = (
        prepare_mini_satellite_internet_planning(simulator, planner)
    )
    planning_configuration = _with_planning_windows(
        planning_configuration, observer
    )
    simulator._write_execution_observations()
    observation_result = write_observation_tool(
        observation_records,
        observation_output,
    )
    compiled_realizations = _compile_bpf_realizations(slicer, plans)
    topology = slicer.get_topology()
    topology_result = write_topology_tool(topology, topology_output)
    zone_result = write_zone_tool(topology, zone_output)
    slice_result = write_slice_difference_tool(slicer, slice_output)
    planner_result = write_planner_inspection(
        slicer,
        planner,
        plans,
        planning_configuration,
        planner_output,
    )
    bpf_compiler_result = write_bpf_compiler_tool(
        plans,
        compiled_realizations,
        bpf_compiler_output,
    )
    write_canonical_traffic_workload(
        traffic_workload_input,
        simulation_start=slicer.get_timeline().start_time,
        simulation_end=slicer.get_timeline().end_time,
        zone_prefixes=dict(compiled_realizations[-1].zone_prefixes),
        zone_service_segments=planned_zone_service_segments(
            compiled_realizations,
            simulation_start=slicer.get_timeline().start_time,
            simulation_end=slicer.get_timeline().end_time,
        ),
    )

    results = (
        topology_result,
        zone_result,
        slice_result,
        planner_result,
        observation_result,
        bpf_compiler_result,
    )
    if not all(result["status"] == "PASS" for result in results):
        return 1
    _write_execution_definition(
        arguments.output,
        builder_module="examples.mini_satellite_internet.simu",
        builder_callable="create_mini_satellite_internet_progression",
        generated_output_identity=compose_project_name,
        generation_lookahead=_SIMULATION_GENERATION_LOOKAHEAD,
        preparation_lookahead=_RUNNER_PREPARATION_LOOKAHEAD,
        preparation_boundary_depth=_RUNNER_PREPARATION_BOUNDARY_DEPTH,
        timeline_start=slicer.get_timeline().start_time,
        timeline_end=slicer.get_timeline().end_time,
        slice_interval=slicer.get_timeline().slice_interval,
        project_root=PROJECT_ROOT,
        example_root=EXAMPLE_ROOT,
    )
    write_compose_project_scripts(
        arguments.output,
        compose_project_name,
    )
    _write_execution_scripts(arguments.output)

    print(f"runtime directory: {runtime_output}")
    print(f"start: {arguments.output / 'start.sh'}")
    print(f"run: {arguments.output / 'run.sh'}")
    print(f"launch: {arguments.output / 'launch.sh'}")
    print(f"cleanup: {arguments.output / 'cleanup.sh'}")
    print(f"Topology Tool entry: {topology_output / 'index.html'}")
    print(f"Zone Tool entry: {zone_output / 'index.html'}")
    print(f"Slice / Difference Tool entry: {slice_output / 'index.html'}")
    print(f"Planner Tool entry: {planner_output / 'index.html'}")
    print(f"Observation Tool entry: {observation_output / 'index.html'}")
    print(
        f"BPF Compiler Tool entry: {bpf_compiler_output / 'index.html'}"
    )
    print(
        "Traffic workload schedule: "
        f"{traffic_workload_input / 'traffic-workload.json'}"
    )
    print(
        "boundary: static containers are not updated from Simulator Slices"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
