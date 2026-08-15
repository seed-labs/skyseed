"""Read Config, create the Emulator and generate its static boundary input."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


EXAMPLE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = EXAMPLE_ROOT.parents[1]
DEFAULT_SCENARIO = EXAMPLE_ROOT / "scenario.yaml"
DEFAULT_OUTPUT = EXAMPLE_ROOT / "output"
COMPOSE_PROJECT_NAME = "skyseed_v2_large_static_network"

if __package__ in (None, ""):
    sys.path.insert(0, str(PROJECT_ROOT))

from examples._direct_execution import prepare_direct_file_execution

prepare_direct_file_execution(__file__)

from examples._compose_project_scripts import (
    prepare_example_output,
    write_compose_project_scripts,
)
from seedemu.compiler import Docker
from seedemu.layers import Base
from skyseed_v2.configuration.emulator import load_static_emulator_config
from skyseed_v2.emulator.seedemu import get_skyseed_autonomous_systems
from skyseed_v2.emulator_to_simulator import create_simulator_static_input
from skyseed_v2.emulator import create_static_emulator


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO)
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

    config = load_static_emulator_config(arguments.scenario)
    emulator = create_static_emulator(config)
    base = emulator.getLayer("Base")
    if not isinstance(base, Base):
        raise TypeError("static Emulator Base layer must be a Base")
    skyseed_autonomous_systems = get_skyseed_autonomous_systems(base)
    simulator_input = create_simulator_static_input(skyseed_autonomous_systems)
    satellite_count = sum(
        len(autonomous_system.satellites())
        for autonomous_system in skyseed_autonomous_systems
    )
    ground_station_count = sum(
        len(autonomous_system.ground_stations())
        for autonomous_system in skyseed_autonomous_systems
    )
    print(
        f"constructed {satellite_count} Satellites, "
        f"{ground_station_count} GroundStations and "
        f"{len(simulator_input.list_nodes())} Simulator static inputs"
    )

    runtime_output = prepare_example_output(
        arguments.output,
        override=arguments.override,
    )
    emulator.render()
    emulator.compile(
        Docker(
            namingScheme="as{asn}{role}-{displayName}",
            internetMapEnabled=not arguments.no_internet_map,
        ),
        str(runtime_output),
    )
    write_compose_project_scripts(arguments.output, COMPOSE_PROJECT_NAME)
    print(f"compiled large static network to {runtime_output}")
    print(f"start with {arguments.output / 'start.sh'}")
    print(f"cleanup with {arguments.output / 'cleanup.sh'}")


if __name__ == "__main__":
    main()
