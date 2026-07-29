"""Build the rich static deployment and its three read-only Tool views."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


EXAMPLE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = EXAMPLE_ROOT.parents[1]
DEFAULT_SCENARIO = EXAMPLE_ROOT / "scenario.yaml"
DEFAULT_ZONES = EXAMPLE_ROOT / "zones.yaml"
DEFAULT_OUTPUT = EXAMPLE_ROOT / "output"
COMPOSE_PROJECT_NAME = "skyseed_v2_dynamic_slice_network"

if __package__ in (None, ""):
    sys.path.insert(0, str(PROJECT_ROOT))

from examples._direct_execution import prepare_direct_file_execution

prepare_direct_file_execution(__file__)

from examples._compose_project_scripts import (
    prepare_example_output,
    write_compose_project_scripts,
)
from examples.dynamic_slice_network.emu import (
    build_dynamic_reference_emulator,
)
from examples.dynamic_slice_network.simu import (
    create_dynamic_reference_slicer,
)
from seedemu.compiler import Docker
from tools.dynamic_slice import write_dynamic_slice_tool
from tools.topology import write_topology_tool
from tools.zone import write_zone_tool


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
    parser.add_argument("--no-internet-map", action="store_true")
    parser.add_argument(
        "--internet-map-source",
        type=Path,
        help=(
            "explicit repaired InternetMap2 source/build context; required "
            "when InternetMap is enabled"
        ),
    )
    arguments = parser.parse_args()
    if (
        not arguments.no_internet_map
        and arguments.internet_map_source is None
    ):
        parser.error(
            "--internet-map-source is required unless --no-internet-map is used"
        )

    emulator = build_dynamic_reference_emulator(arguments.scenario)
    runtime_output = prepare_example_output(
        arguments.output,
        override=arguments.override,
    )
    topology_output = arguments.output / "inspection/topology"
    zone_output = arguments.output / "inspection/zone"
    dynamic_output = arguments.output / "inspection/dynamic-slice"

    emulator.render()
    emulator.compile(
        Docker(
            namingScheme="as{asn}{role}-{displayName}",
            internetMapEnabled=not arguments.no_internet_map,
            internetMapSource=arguments.internet_map_source,
        ),
        str(runtime_output),
    )
    write_compose_project_scripts(
        arguments.output,
        COMPOSE_PROJECT_NAME,
    )

    slicer = create_dynamic_reference_slicer(
        emulator,
        zone_layout_path=arguments.zones,
    )
    topology = slicer.get_topology()
    topology_result = write_topology_tool(topology, topology_output)
    zone_result = write_zone_tool(topology, zone_output)
    dynamic_result = write_dynamic_slice_tool(slicer, dynamic_output)

    print(f"runtime directory: {runtime_output}")
    print(f"start: {arguments.output / 'start.sh'}")
    print(f"cleanup: {arguments.output / 'cleanup.sh'}")
    print(f"Topology Tool entry: {topology_output / 'index.html'}")
    print(f"Zone Tool entry: {zone_output / 'index.html'}")
    print(f"Dynamic Tool entry: {dynamic_output / 'index.html'}")
    print(
        "boundary: static containers are not updated from Simulator Slices"
    )
    results = (topology_result, zone_result, dynamic_result)
    return 0 if all(result["status"] == "PASS" for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
