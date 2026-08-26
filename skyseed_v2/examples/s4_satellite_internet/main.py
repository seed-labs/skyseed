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
from examples.s4_satellite_internet.simu import (
    DYNAMIC_REAL_SLICE_COUNT,
    S4_PLANNER_PROFILES,
    create_s4_dynamic_simulator,
    create_s4_static_simulator,
)
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
    qualification_mode = parser.add_mutually_exclusive_group()
    qualification_mode.add_argument(
        "--dynamic",
        action="store_true",
        help="materialize the bounded 30-minute dynamic qualification run",
    )
    parser.add_argument(
        "--slice-limit",
        type=int,
        default=DYNAMIC_REAL_SLICE_COUNT,
        help=(
            "stop dynamic qualification at this real Slice "
            f"(1..{DYNAMIC_REAL_SLICE_COUNT}; default: full Timeline)"
        ),
    )
    parser.add_argument(
        "--tool-workers",
        type=int,
        default=2,
        help=(
            "bounded independent process workers for heavy Inspection Tool "
            "materialization (default: 2)"
        ),
    )
    parser.add_argument(
        "--planner-profile",
        choices=S4_PLANNER_PROFILES,
        default=None,
        help=(
            "optional diagnostic Planner profile; omitted selects the normal "
            "explicit S4 model/policy composition"
        ),
    )
    qualification_mode.add_argument(
        "--planner",
        action="store_true",
        help=(
            "materialize the bounded dynamic run and qualify the normal "
            "explicit Planner composition"
        ),
    )
    arguments = parser.parse_args()

    prepare_example_output(arguments.output, override=arguments.override)
    static_started = perf_counter()
    emulator = build_s4_satellite_internet_emulator()
    dynamic_requested = arguments.dynamic or arguments.planner
    simulator = (
        create_s4_dynamic_simulator(emulator)
        if dynamic_requested
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
    if dynamic_requested:
        from examples.s4_satellite_internet.qualification import (
            run_s4_dynamic_qualification,
        )

        dynamic_result = run_s4_dynamic_qualification(
            simulator,
            arguments.output,
            static_construction_seconds=static_construction_seconds,
            include_planner=arguments.planner,
            planner_profile=arguments.planner_profile,
            final_slice_id=arguments.slice_limit,
            tool_workers=arguments.tool_workers,
        )
        for result_name, result_key in (
            ("slice", "slice_difference_tool"),
            ("observation", "observation_tool"),
            ("planner", "planner_tool"),
            ("compiler", "compiler_tool"),
        ):
            result = dynamic_result.get(result_key)
            if isinstance(result, dict):
                results[result_name] = dict(result)

    write_aggregate_index(inspection, results, counts)

    print(f"Inspection index: {inspection / 'index.html'}")
    print(f"Topology Tool entry: {inspection / 'topology/index.html'}")
    print(f"Zone Tool entry: {inspection / 'zone/index.html'}")
    if dynamic_result is not None:
        if "slice" in results:
            print(f"Slice Tool entry: {inspection / 'slice/index.html'}")
        if "observation" in results:
            print(
                f"Observation Tool entry: "
                f"{inspection / 'observation/index.html'}"
            )
        if "planner" in results:
            print(f"Planner Tool entry: {inspection / 'planner/index.html'}")
        if "compiler" in results:
            print(
                f"BPF Compiler Tool entry: "
                f"{inspection / 'compiler/index.html'}"
            )
        print(f"Qualification evidence: {arguments.output / 'qualification/run.json'}")
    overall_pass = all(result.get("status") == "PASS" for result in results.values())
    print(f"Tooling overall: {'PASS' if overall_pass else 'FAIL'}")
    return 0 if overall_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
