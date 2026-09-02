"""Construct the complete static Emulator with public Skyseed and SEED APIs."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys
import tempfile


EXAMPLE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = EXAMPLE_ROOT.parents[1]
DEFAULT_OUTPUT = EXAMPLE_ROOT / "output"
COMPOSE_PROJECT_NAME = "skyseed_v2_complete_static_emulator"

if __package__ in (None, ""):
    sys.path.insert(0, str(PROJECT_ROOT))

from examples.complete_static_emulator.emu import build_emulator
from examples.complete_static_emulator.simu import (
    create_complete_static_planner,
    create_complete_static_simulator,
)
from seedemu.compiler import Docker
from skyseed_v2.compose.launcher import (
    _prepare_generated_output,
    _write_compose_project_scripts,
)
from skyseed_v2.experiment._realization_producer import (
    _IncrementalRealizationProducer,
)
from skyseed_v2.simulator import PhysicalWorld, Simulator
from tools._html import (
    write_aggregate_index,
    write_failed_tool,
)
from tools.bpf_compiler import write_bpf_compiler_tool
from tools.observation import write_observation_tool
from tools.physical_world import write_physical_world_tool
from tools.planner import write_planner_tool
from tools.planner.reader import read_planner_history
from tools.zone import write_zone_tool


ENABLED_TOOLS = (
    "physical-world",
    "zone",
    "planner",
    "bpf-compiler",
    "observation",
)


def _create_simulator(emulator: object) -> Simulator:
    """Use the visible formal assembly chain for this one example run."""

    return create_complete_static_simulator(emulator)


def _write_tool_snapshot(
    physical_world: PhysicalWorld,
    output: Path,
    *,
    physical_link_model,
    physical_frame,
    physical_link_observation,
    plans,
    realizations,
    observation_path: Path,
) -> tuple[dict[str, dict[str, object]], bool]:
    staging = Path(tempfile.mkdtemp(prefix=".inspection-", dir=output))
    results: dict[str, dict[str, object]] = {}
    aggregate_ok = True
    try:
        for tool_name in ENABLED_TOOLS:
            tool_directory = staging / tool_name
            try:
                if tool_name == "physical-world":
                    result = write_physical_world_tool(
                        physical_world,
                        tool_directory,
                        physical_frames_path=(
                            output / "domain-history/physical-frames.jsonl"
                            if (
                                output
                                / "domain-history/physical-frames.jsonl"
                            ).is_file()
                            else None
                        ),
                        physical_frame=physical_frame,
                        physical_link_observation=physical_link_observation,
                        physical_link_model=physical_link_model,
                    )
                elif tool_name == "zone":
                    result = write_zone_tool(physical_world, tool_directory)
                elif tool_name == "planner":
                    result = write_planner_tool(
                        read_planner_history(
                            output / "domain-history/planning.jsonl.gz"
                        ),
                        tool_directory,
                        physical_frames_path=(
                            output / "domain-history/physical-frames.jsonl"
                        ),
                    )
                elif tool_name == "bpf-compiler":
                    result = write_bpf_compiler_tool(
                        plans,
                        realizations,
                        tool_directory,
                        observation_path=observation_path,
                    )
                elif tool_name == "observation":
                    result = write_observation_tool(
                        observation_path, tool_directory
                    )
                else:  # pragma: no cover - frozen tuple is explicit
                    raise ValueError(f"unknown enabled tool: {tool_name}")
                results[tool_name] = dict(result)
            except Exception as error:
                results[tool_name] = {
                    "status": "FAILED",
                    "message": f"{type(error).__name__}: {error}",
                }
                try:
                    write_failed_tool(tool_directory, tool_name, error)
                except Exception as artifact_error:
                    results[tool_name]["message"] = (
                        f"{results[tool_name]['message']}; failed to write "
                        f"FAILED artifact: {type(artifact_error).__name__}: "
                        f"{artifact_error}"
                    )

        physical_world_result = results.get("physical-world", {})
        raw_counts = physical_world_result.get("counts", {})
        counts = (
            {key: int(value) for key, value in raw_counts.items()}
            if isinstance(raw_counts, dict)
            else {}
        )
        try:
            write_aggregate_index(staging, results, counts)
        except Exception as error:
            aggregate_ok = False
            write_failed_tool(staging, "Tooling aggregate", error)

        final_directory = output / "inspection"
        if final_directory.is_symlink() or final_directory.is_file():
            final_directory.unlink()
        elif final_directory.exists():
            shutil.rmtree(final_directory)
        staging.replace(final_directory)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return results, aggregate_ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--override",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="replace an existing output directory (default: enabled)",
    )
    parser.add_argument(
        "--no-internet-map",
        action="store_true",
        help="disable InternetMap for fault isolation only",
    )
    arguments = parser.parse_args()

    emulator = build_emulator()
    simulator = _create_simulator(emulator)
    physical_world = simulator.get_physical_world()
    runtime_output = _prepare_generated_output(
        arguments.output,
        replace=arguments.override,
    )
    emulator.render()
    emulator.compile(
        Docker(
            namingScheme="as{asn}{role}-{displayName}",
            internetMapEnabled=not arguments.no_internet_map,
        ),
        str(runtime_output),
    )
    _write_compose_project_scripts(arguments.output, COMPOSE_PROJECT_NAME)
    planner = create_complete_static_planner(simulator)
    observation_path = (
        arguments.output
        / "inspection/observation/execution-observations.jsonl"
    )
    simulator._enable_execution_observation(
        observation_path,
        metadata={
            "scenario_identity": "complete_static_emulator",
            "timeline_start": simulator.get_timeline().start_time.isoformat(),
            "timeline_end": simulator.get_timeline().end_time.isoformat(),
            "observation_interval_seconds": (
                simulator.get_timeline().observation_interval.total_seconds()
            ),
            "progression": "physical-world-planner-plan-compiler",
        },
    )
    producer = _IncrementalRealizationProducer(
        simulator.get_timeline(),
        physical_world,
        simulator.get_physical_link_model(),
        planner,
        evidence_path=arguments.output / "domain-history",
    )
    plans = []
    realizations = []
    try:
        while True:
            realization = producer.produce_next_realization()
            if realization is None:
                break
            plans.append(producer.latest_plan)
            realizations.append(realization)
        producer_snapshot = producer.snapshot()
        physical_frame, physical_link_observation = (
            producer.first_formal_input()
        )
    finally:
        producer.close()
    simulator._write_execution_observations()
    try:
        tool_results, aggregate_ok = _write_tool_snapshot(
            physical_world,
            arguments.output,
            physical_link_model=simulator.get_physical_link_model(),
            physical_frame=physical_frame,
            physical_link_observation=physical_link_observation,
            plans=tuple(plans),
            realizations=tuple(realizations),
            observation_path=observation_path,
        )
    except Exception as error:
        message = f"{type(error).__name__}: {error}"
        tool_results = {
            name: {"status": "FAILED", "message": message}
            for name in ENABLED_TOOLS
        }
        aggregate_ok = False
    print(f"compiled complete static Emulator to {runtime_output}")
    print(f"start with {arguments.output / 'start.sh'}")
    print(f"cleanup with {arguments.output / 'cleanup.sh'}")
    print(f"Inspection index: {arguments.output / 'inspection/index.html'}")
    print(
        "Planner/Compiler: "
        f"{len(plans)} Plans, {len(realizations)} realizations, "
        f"{producer_snapshot['transition_count']} formal transitions"
    )
    for tool_name in ENABLED_TOOLS:
        result = tool_results[tool_name]
        tool_title = {
            "physical-world": "Physical World",
            "zone": "Zone",
            "planner": "Planner",
            "bpf-compiler": "BPF Compiler",
            "observation": "System Observation",
        }[tool_name]
        print(
            f"{tool_title} Tool: {result['status']} "
            f"({arguments.output / 'inspection' / tool_name / 'index.html'})"
        )
    overall_pass = aggregate_ok and all(
        result.get("status") == "PASS" for result in tool_results.values()
    )
    print(f"Tooling overall: {'PASS' if overall_pass else 'FAIL'}")
    if not overall_pass:
        print(
            "Inspection side-work failure did not invalidate the completed "
            "Planner/Compiler domain output.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
