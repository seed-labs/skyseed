"""Read Config, create the Emulator and generate its Simulator static input."""

from __future__ import annotations

import argparse
from pathlib import Path

from examples._compose_project_scripts import write_compose_project_scripts
from skyseed_v2.configuration import load_static_emulator_config
from skyseed_v2.seedemu_integration import SkyseedDockerCompiler
from skyseed_v2.simulator_input_generation import (
    create_emulator_node_index,
    create_simulator_static_input,
)
from skyseed_v2.static_emulator import create_static_emulator


EXAMPLE_ROOT = Path(__file__).resolve().parent
DEFAULT_SCENARIO = EXAMPLE_ROOT / "scenario.yaml"
DEFAULT_OUTPUT = EXAMPLE_ROOT / "output"
COMPOSE_PROJECT_NAME = "skyseed_v2_large_static_network"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--override", action="store_true")
    parser.add_argument("--no-internet-map", action="store_true")
    arguments = parser.parse_args()

    config = load_static_emulator_config(arguments.scenario)
    creation = create_static_emulator(config)
    simulator_input = create_simulator_static_input(
        creation.skyseed_autonomous_systems
    )
    emulator_node_index = create_emulator_node_index(
        creation.skyseed_autonomous_systems
    )
    simulator_node_ids = {
        simulation_input.node_id
        for simulation_input in simulator_input.list_nodes()
    }
    if simulator_node_ids != set(emulator_node_index):
        raise ValueError(
            "SimulatorStaticInput NodeIds do not match Emulator node index"
        )
    satellite_count = sum(
        len(autonomous_system.satellites())
        for autonomous_system in creation.skyseed_autonomous_systems
    )
    ground_station_count = sum(
        len(autonomous_system.ground_stations())
        for autonomous_system in creation.skyseed_autonomous_systems
    )
    print(
        f"constructed {satellite_count} Satellites, "
        f"{ground_station_count} GroundStations and "
        f"{len(simulator_input.list_nodes())} static Simulator inputs"
    )

    creation.emulator.render()
    creation.emulator.compile(
        SkyseedDockerCompiler(
            internet_map_enabled=not arguments.no_internet_map
        ),
        str(arguments.output),
        override=arguments.override,
    )
    write_compose_project_scripts(arguments.output, COMPOSE_PROJECT_NAME)
    print(f"compiled large static network to {arguments.output}")
    print(f"start with {arguments.output / 'start.sh'}")


if __name__ == "__main__":
    main()
