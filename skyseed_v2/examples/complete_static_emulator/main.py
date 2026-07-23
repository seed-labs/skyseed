"""Construct the complete static Emulator with public Skyseed and SEED APIs."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
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

from examples._direct_execution import prepare_direct_file_execution

prepare_direct_file_execution(__file__)

from examples._compose_project_scripts import write_compose_project_scripts
from examples.complete_static_emulator.emu import build_emulator
from skyseed_v2.configuration import load_zone_layout_definition
from skyseed_v2.seedemu_integration import SatelliteBase, SkyseedDockerCompiler
from skyseed_v2.simulator import (
    SimulationDefinition,
    SimulationTimeline,
    Simulator,
    Topology,
)
from skyseed_v2.simulator_input_generation import create_simulator_static_input
from tools._html import (
    write_aggregate_index,
    write_failed_tool,
)
from tools.topology import write_topology_tool
from tools.zone import write_zone_tool


DEFAULT_START_TIME = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
DEFAULT_END_TIME = datetime(2026, 1, 1, 0, 0, 30, tzinfo=timezone.utc)
DEFAULT_SLICE_INTERVAL = timedelta(seconds=10)
ZONE_LAYOUT_FILE = EXAMPLE_ROOT / "zones.yaml"
ENABLED_TOOLS = ("topology", "zone")


def _create_simulator_topology(emulator: object) -> Topology:
    """Use the visible formal assembly chain for this one example run."""

    if not hasattr(emulator, "getLayer"):
        raise TypeError("emulator must provide the upstream Emulator facade")
    base = emulator.getLayer("Base")
    if not isinstance(base, SatelliteBase):
        raise TypeError("complete example Base layer must be a SatelliteBase")
    skyseed_autonomous_systems = base.get_skyseed_autonomous_systems()
    simulator_input = create_simulator_static_input(skyseed_autonomous_systems)
    timeline = SimulationTimeline(
        start_time=DEFAULT_START_TIME,
        end_time=DEFAULT_END_TIME,
        slice_interval=DEFAULT_SLICE_INTERVAL,
    )
    definition = SimulationDefinition(
        static_input=simulator_input,
        timeline=timeline,
    )
    simulator = Simulator(definition)
    zone_layout_definition = load_zone_layout_definition(ZONE_LAYOUT_FILE)
    return simulator.create_topology(
        zone_layout_definition=zone_layout_definition,
    )


def _write_tool_snapshot(
    topology: Topology,
    output: Path,
) -> tuple[dict[str, dict[str, object]], bool]:
    staging = Path(tempfile.mkdtemp(prefix=".tools-", dir=output))
    results: dict[str, dict[str, object]] = {}
    aggregate_ok = True
    try:
        for tool_name in ENABLED_TOOLS:
            tool_directory = staging / tool_name
            try:
                if tool_name == "topology":
                    result = write_topology_tool(topology, tool_directory)
                elif tool_name == "zone":
                    result = write_zone_tool(topology, tool_directory)
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

        topology_result = results.get("topology", {})
        raw_counts = topology_result.get("counts", {})
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

        final_directory = output / "tools"
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
    parser.add_argument("--no-internet-map", action="store_true")
    arguments = parser.parse_args()

    emulator = build_emulator()
    topology = _create_simulator_topology(emulator)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    emulator.render()
    emulator.compile(
        SkyseedDockerCompiler(
            internet_map_enabled=not arguments.no_internet_map
        ),
        str(arguments.output),
        override=arguments.override,
    )
    write_compose_project_scripts(arguments.output, COMPOSE_PROJECT_NAME)
    tool_results, aggregate_ok = _write_tool_snapshot(
        topology,
        arguments.output,
    )
    print(f"compiled complete static Emulator to {arguments.output}")
    print(f"start with {arguments.output / 'start.sh'}")
    print(f"Tooling index: {arguments.output / 'tools/index.html'}")
    for tool_name in ENABLED_TOOLS:
        result = tool_results[tool_name]
        print(
            f"{tool_name.capitalize()} Tool: {result['status']} "
            f"({arguments.output / 'tools' / tool_name / 'index.html'})"
        )
    overall_pass = aggregate_ok and all(
        result.get("status") == "PASS" for result in tool_results.values()
    )
    print(f"Tooling overall: {'PASS' if overall_pass else 'FAIL'}")
    return 0 if overall_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
