"""Compile one Shell, one Orbit, and two explicitly isolated Satellites."""

from __future__ import annotations

import argparse
from pathlib import Path

from skyseed_v2 import (
    StaticEmulatorBuildResult,
    StaticEmulatorScenarioBuilder,
    StaticEmulatorScenarioLoader,
)
from skyseed_v2.seed import SkyseedDockerCompiler


EXAMPLE_ROOT = Path(__file__).resolve().parent
DEFAULT_SCENARIO = EXAMPLE_ROOT / "scenario.yaml"
DEFAULT_OUTPUT = EXAMPLE_ROOT / "output"
COMPOSE_PROJECT_NAME = "skyseed_v2_isolated_satellites"


def build_emulator(
    scenario_file: Path = DEFAULT_SCENARIO,
) -> StaticEmulatorBuildResult:
    specification = StaticEmulatorScenarioLoader().load(scenario_file)
    return StaticEmulatorScenarioBuilder().build(specification)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--no-internet-map", action="store_true")
    parser.add_argument("--override", action="store_true")
    arguments = parser.parse_args()

    result = build_emulator(arguments.scenario)
    result.emulator.render()
    result.emulator.compile(
        SkyseedDockerCompiler(
            internet_map_enabled=not arguments.no_internet_map
        ),
        str(arguments.output),
        override=arguments.override,
    )

    satellites = result.shell_population.satellites
    print(f"compiled {len(satellites)} Satellites to {arguments.output}")
    for descriptor in result.catalog.list_satellites():
        print(f"- {descriptor.satellite_id}: {descriptor.node_id.value}")


if __name__ == "__main__":
    main()
