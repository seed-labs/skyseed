"""Load and apply the large v2-owned static specification."""

from __future__ import annotations

import argparse
from pathlib import Path

from skyseed_v2.application import (
    StaticEmulatorApplyResult,
    StaticEmulatorSpecificationApplier,
)
from skyseed_v2.serialization import StaticEmulatorScenarioLoader
from skyseed_v2.seedemu_integration import SkyseedDockerCompiler


EXAMPLE_ROOT = Path(__file__).resolve().parent
DEFAULT_SCENARIO = EXAMPLE_ROOT / "scenario.yaml"
DEFAULT_OUTPUT = EXAMPLE_ROOT / "output"
COMPOSE_PROJECT_NAME = "skyseed_v2_large_static_network"


def apply_scenario_file(
    scenario_file: Path = DEFAULT_SCENARIO,
) -> StaticEmulatorApplyResult:
    """Load immutable input, then apply it through public create methods."""

    specification = StaticEmulatorScenarioLoader().load(scenario_file)
    return StaticEmulatorSpecificationApplier().apply(specification)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--override", action="store_true")
    parser.add_argument("--no-internet-map", action="store_true")
    arguments = parser.parse_args()

    result = apply_scenario_file(arguments.scenario)
    satellite_count = sum(
        len(autonomous_system.satellites())
        for autonomous_system in result.skyseed_autonomous_systems
    )
    ground_station_count = sum(
        len(autonomous_system.ground_stations())
        for autonomous_system in result.skyseed_autonomous_systems
    )
    print(
        f"constructed {satellite_count} Satellites, "
        f"{ground_station_count} GroundStations and "
        f"{len(result.simulator_input.list_nodes())} static Simulator inputs"
    )

    result.emulator.render()
    result.emulator.compile(
        SkyseedDockerCompiler(
            internet_map_enabled=not arguments.no_internet_map
        ),
        str(arguments.output),
        override=arguments.override,
    )
    print(f"compiled large static network to {arguments.output}")


if __name__ == "__main__":
    main()
