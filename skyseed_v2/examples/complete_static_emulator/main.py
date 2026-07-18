"""Build the complete static Skyseed Emulator and logical Catalog."""

from __future__ import annotations

import argparse
from pathlib import Path

from skyseed_v2 import (
    SimulationEntityCatalog,
    StaticEmulatorBuildResult,
    StaticEmulatorScenarioBuilder,
    StaticEmulatorScenarioLoader,
)
from skyseed_v2.seed import SkyseedDockerCompiler


EXAMPLE_ROOT = Path(__file__).resolve().parent
DEFAULT_SCENARIO = EXAMPLE_ROOT / "scenario.yaml"
DEFAULT_OUTPUT = EXAMPLE_ROOT / "output"
COMPOSE_PROJECT_NAME = "skyseed_v2_complete_static_emulator"


def build_emulator(
    scenario_file: Path = DEFAULT_SCENARIO,
) -> StaticEmulatorBuildResult:
    specification = StaticEmulatorScenarioLoader().load(scenario_file)
    return StaticEmulatorScenarioBuilder().build(specification)


def catalog_summary(catalog: SimulationEntityCatalog) -> tuple[str, ...]:
    satellites = catalog.list_satellites()
    ground_stations = catalog.list_ground_stations()
    nodes = catalog.list_nodes()
    return (
        "Catalog summary: "
        f"{len(satellites)} SatelliteDescriptor, "
        f"{len(ground_stations)} GroundStationDescriptor, "
        f"{len(nodes)} unique SimulationNodeId",
        *(f"- {descriptor.node_id.value}" for descriptor in nodes),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--override", action="store_true")
    parser.add_argument("--no-internet-map", action="store_true")
    arguments = parser.parse_args()

    result = build_emulator(arguments.scenario)
    for line in catalog_summary(result.catalog):
        print(line)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    result.emulator.render()
    result.emulator.compile(
        SkyseedDockerCompiler(
            internet_map_enabled=not arguments.no_internet_map
        ),
        str(arguments.output),
        override=arguments.override,
    )
    print(f"compiled complete static Emulator to {arguments.output}")


if __name__ == "__main__":
    main()
