"""Build the S4 static views or opt into bounded dynamic qualification."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from time import perf_counter


EXAMPLE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = EXAMPLE_ROOT.parents[1]
DEFAULT_OUTPUT = EXAMPLE_ROOT / "output"

if __package__ in (None, ""):
    sys.path.insert(0, str(PROJECT_ROOT))

from examples._direct_execution import prepare_direct_file_execution

prepare_direct_file_execution(__file__)

from examples._compose_project_scripts import prepare_example_output
from examples.s4_satellite_internet.emu import (
    build_s4_satellite_internet_emulator,
)
from examples.s4_satellite_internet.simu import create_s4_static_simulator
from examples.s4_satellite_internet.simu import create_s4_dynamic_simulator
from tools._html import write_aggregate_index
from tools.topology import write_topology_tool
from tools.zone import write_zone_tool


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--override",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="replace an existing generated output directory (default: enabled)",
    )
    parser.add_argument(
        "--dynamic",
        action="store_true",
        help="materialize the bounded 30-minute dynamic qualification run",
    )
    arguments = parser.parse_args()

    prepare_example_output(arguments.output, override=arguments.override)
    static_started = perf_counter()
    emulator = build_s4_satellite_internet_emulator()
    simulator = (
        create_s4_dynamic_simulator(emulator)
        if arguments.dynamic
        else create_s4_static_simulator(emulator)
    )
    static_construction_seconds = perf_counter() - static_started
    topology = simulator.get_topology()

    inspection = arguments.output / "inspection"
    topology_result = write_topology_tool(topology, inspection / "topology")
    zone_result = write_zone_tool(topology, inspection / "zone")
    results = {
        "topology": dict(topology_result),
        "zone": dict(zone_result),
    }
    counts = {
        key: int(value)
        for key, value in dict(topology_result.get("counts", {})).items()
    }
    dynamic_result = None
    if arguments.dynamic:
        from examples.s4_satellite_internet.qualification import (
            run_s4_dynamic_qualification,
        )

        dynamic_result = run_s4_dynamic_qualification(
            simulator,
            arguments.output,
            static_construction_seconds=static_construction_seconds,
        )
        slice_result = dynamic_result.get("slice_difference_tool")
        if isinstance(slice_result, dict):
            results["slice"] = dict(slice_result)

    write_aggregate_index(inspection, results, counts)

    print(f"Inspection index: {inspection / 'index.html'}")
    print(f"Topology Tool entry: {inspection / 'topology/index.html'}")
    print(f"Zone Tool entry: {inspection / 'zone/index.html'}")
    if dynamic_result is not None:
        print(f"Slice Tool entry: {inspection / 'slice/index.html'}")
        print(f"Qualification evidence: {arguments.output / 'qualification/run.json'}")
    overall_pass = all(result.get("status") == "PASS" for result in results.values())
    print(f"Tooling overall: {'PASS' if overall_pass else 'FAIL'}")
    return 0 if overall_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
