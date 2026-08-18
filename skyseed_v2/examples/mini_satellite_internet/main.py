"""Build the mini satellite Internet reference and all inspection views."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


EXAMPLE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = EXAMPLE_ROOT.parents[1]
DEFAULT_SCENARIO = EXAMPLE_ROOT / "scenario.yaml"
DEFAULT_ZONES = EXAMPLE_ROOT / "zones.yaml"
DEFAULT_OUTPUT = EXAMPLE_ROOT / "output"
COMPOSE_PROJECT_NAME = "skyseed_v2_mini_satellite_internet"

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
    create_mini_satellite_internet_simulator,
    prepare_mini_satellite_internet_planning,
)
from seedemu.compiler import Docker
from skyseed_v2.backend.bpf import BpfCompiledRealization, compile_bpf
from skyseed_v2.simulator import (
    Plan,
    Planner,
    Slicer,
)
from tools.dynamic_slice import write_dynamic_slice_tool
from tools.bpf_compiler import write_bpf_compiler_tool
from tools.planner import write_planner_tool
from tools.topology import write_topology_tool
from tools.zone import write_zone_tool


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
        planner.get_planning_decisions(),
        configuration,
        output,
    )


def _compile_bpf_realizations(
    slicer: Slicer,
    plans: tuple[Plan, ...],
) -> tuple[BpfCompiledRealization, ...]:
    """Prepare the already-compiled values consumed by the Tool."""

    compiled = []
    previous = None
    for plan in plans:
        result = compile_bpf(plan, slicer=slicer, previous=previous)
        compiled.append(result)
        previous = result
    return tuple(compiled)


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

    emulator = build_mini_satellite_internet_emulator(arguments.scenario)
    runtime_output = prepare_example_output(
        arguments.output,
        override=arguments.override,
    )
    topology_output = arguments.output / "inspection/topology"
    zone_output = arguments.output / "inspection/zone"
    dynamic_output = arguments.output / "inspection/dynamic-slice"
    planner_output = arguments.output / "inspection/planner"
    bpf_compiler_output = arguments.output / "inspection/bpf-compiler"

    emulator.render()
    emulator.compile(
        Docker(
            namingScheme="as{asn}{role}-{displayName}",
            internetMapEnabled=not arguments.no_internet_map,
        ),
        str(runtime_output),
    )
    write_compose_project_scripts(
        arguments.output,
        COMPOSE_PROJECT_NAME,
    )

    simulator = create_mini_satellite_internet_simulator(
        emulator,
        zone_layout_path=arguments.zones,
    )
    slicer = simulator.get_slicer()
    planner, plans, planning_configuration = (
        prepare_mini_satellite_internet_planning(simulator)
    )
    compiled_realizations = _compile_bpf_realizations(slicer, plans)
    topology = slicer.get_topology()
    topology_result = write_topology_tool(topology, topology_output)
    zone_result = write_zone_tool(topology, zone_output)
    dynamic_result = write_dynamic_slice_tool(slicer, dynamic_output)
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

    print(f"runtime directory: {runtime_output}")
    print(f"start: {arguments.output / 'start.sh'}")
    print(f"cleanup: {arguments.output / 'cleanup.sh'}")
    print(f"Topology Tool entry: {topology_output / 'index.html'}")
    print(f"Zone Tool entry: {zone_output / 'index.html'}")
    print(f"Dynamic Tool entry: {dynamic_output / 'index.html'}")
    print(f"Planner Tool entry: {planner_output / 'index.html'}")
    print(
        f"BPF Compiler Tool entry: {bpf_compiler_output / 'index.html'}"
    )
    print(
        "boundary: static containers are not updated from Simulator Slices"
    )
    results = (
        topology_result,
        zone_result,
        dynamic_result,
        planner_result,
        bpf_compiler_result,
    )
    return 0 if all(result["status"] == "PASS" for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
