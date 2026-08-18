"""Build the mini satellite Internet reference and all inspection views."""

from __future__ import annotations

import argparse
from datetime import timedelta
from pathlib import Path
import sys


EXAMPLE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = EXAMPLE_ROOT.parents[1]
DEFAULT_SCENARIO = EXAMPLE_ROOT / "scenario.yaml"
DEFAULT_ZONES = EXAMPLE_ROOT / "zones.yaml"
DEFAULT_OUTPUT = EXAMPLE_ROOT / "output"
COMPOSE_PROJECT_NAME = "skyseed_v2_mini_satellite_internet"
PUBLICATION_DIFFERENCE_COUNT = 4
MINIMUM_DWELL_SECONDS = 240
PARAMETER_THRESHOLDS = {
    "bandwidth_bps": 10_000_000,
    "delay_us": 250,
    "packet_loss_ppm": 50,
}

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
    DEFAULT_SLICE_INTERVAL,
    REAL_SLICE_COUNT,
    create_mini_satellite_internet_simulator,
)
from seedemu.compiler import Docker
from skyseed_v2.simulator import (
    AccumulatedDeadbandParameterPlanningModel,
    DefaultCoordinationPolicy,
    DependencyAwareForwardingPlanningModel,
    MidpointConnectionPlanningModel,
    Simulator,
    Slicer,
)
from tools.dynamic_slice import write_dynamic_slice_tool
from tools.planner import write_planner_tool
from tools.topology import write_topology_tool
from tools.zone import write_zone_tool


class _VisibleAllPublishFour:
    """Keep all generated future visible and publish four Differences."""

    __slots__ = ()

    def select_differences(self, available_differences):
        return tuple(available_differences)

    def select_publication_differences(self, visible_differences):
        return tuple(visible_differences[:PUBLICATION_DIFFERENCE_COUNT])


def write_planner_inspection(
    simulator: Simulator,
    slicer: Slicer,
    output: Path,
) -> dict[str, object]:
    """Publish real Plans, then pass their formal results to the Tool."""

    planner = simulator.create_planner(
        horizon_policy=_VisibleAllPublishFour(),
        connection_model=MidpointConnectionPlanningModel(
            minimum_dwell=timedelta(seconds=MINIMUM_DWELL_SECONDS),
        ),
        parameter_model=AccumulatedDeadbandParameterPlanningModel(
            bandwidth_bps_threshold=PARAMETER_THRESHOLDS["bandwidth_bps"],
            delay_us_threshold=PARAMETER_THRESHOLDS["delay_us"],
            packet_loss_ppm_threshold=PARAMETER_THRESHOLDS[
                "packet_loss_ppm"
            ],
        ),
        forwarding_model=DependencyAwareForwardingPlanningModel(),
        coordination_policy=DefaultCoordinationPolicy(),
    )
    plans = [planner.get_plan()]
    while plans[-1].end_slice_id < slicer.get_latest_slice_id():
        plans.append(planner.synchronize())
    configuration = {
        "scenario_identity": "mini_satellite_internet",
        "slice_interval_seconds": DEFAULT_SLICE_INTERVAL.total_seconds(),
        "real_slice_count": REAL_SLICE_COUNT,
        "horizon_visibility": "all generated unpublished Differences",
        "visible_through_slice_id": slicer.get_latest_slice_id(),
        "publication_difference_count": PUBLICATION_DIFFERENCE_COUNT,
        "connection_timing": "midpoint",
        "minimum_dwell_seconds": MINIMUM_DWELL_SECONDS,
        "parameter_thresholds": PARAMETER_THRESHOLDS,
        "forwarding_model": "DependencyAwareForwardingPlanningModel",
        "coordination_policy": "DefaultCoordinationPolicy",
    }
    return write_planner_tool(
        slicer,
        tuple(plans),
        planner.get_planning_decisions(),
        configuration,
        output,
    )


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
    topology = slicer.get_topology()
    topology_result = write_topology_tool(topology, topology_output)
    zone_result = write_zone_tool(topology, zone_output)
    dynamic_result = write_dynamic_slice_tool(slicer, dynamic_output)
    planner_result = write_planner_inspection(
        simulator,
        slicer,
        planner_output,
    )

    print(f"runtime directory: {runtime_output}")
    print(f"start: {arguments.output / 'start.sh'}")
    print(f"cleanup: {arguments.output / 'cleanup.sh'}")
    print(f"Topology Tool entry: {topology_output / 'index.html'}")
    print(f"Zone Tool entry: {zone_output / 'index.html'}")
    print(f"Dynamic Tool entry: {dynamic_output / 'index.html'}")
    print(f"Planner Tool entry: {planner_output / 'index.html'}")
    print(
        "boundary: static containers are not updated from Simulator Slices"
    )
    results = (
        topology_result,
        zone_result,
        dynamic_result,
        planner_result,
    )
    return 0 if all(result["status"] == "PASS" for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
