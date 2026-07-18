"""Build a self-contained large static Emulator and small ground Internet."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from json import dumps
from pathlib import Path
from time import perf_counter

from yaml import safe_load

from ._compose_support import materialization_expectations
from skyseed_v2 import (
    StaticEmulatorBuildResult,
    StaticEmulatorScenarioBuilder,
    StaticEmulatorScenarioLoader,
)
from skyseed_v2.seed import SkyseedDockerCompiler


EXAMPLE_ROOT = Path(__file__).resolve().parent
DEFAULT_SCENARIO = EXAMPLE_ROOT / "scenario.yaml"
DEFAULT_OUTPUT = EXAMPLE_ROOT / "output"
COMPOSE_PROJECT_NAME = "skyseed_v2_large_static_network"


@dataclass(frozen=True, slots=True)
class LargeStaticSummary:
    shell_count: int
    orbit_count: int
    satellite_count: int
    ground_station_count: int
    ordinary_as_count: int
    ix_count: int
    ebgp_peering_count: int
    catalog_node_count: int
    compose_service_count: int | None = None
    compose_file_size_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class LargeStaticScenario:
    construction: StaticEmulatorBuildResult
    summary: LargeStaticSummary
    timings_seconds: dict[str, float]


def build_scenario(
    scenario_file: Path = DEFAULT_SCENARIO,
) -> LargeStaticScenario:
    parse_started = perf_counter()
    specification = StaticEmulatorScenarioLoader().load(scenario_file)
    input_parse_seconds = perf_counter() - parse_started

    construction_started = perf_counter()
    construction = StaticEmulatorScenarioBuilder().build(specification)
    construction_seconds = perf_counter() - construction_started
    summary = LargeStaticSummary(
        shell_count=len(construction.shell_population.shells),
        orbit_count=len(construction.shell_population.orbits),
        satellite_count=len(construction.shell_population.satellites),
        ground_station_count=len(
            construction.ground_station_population.ground_stations
        ),
        ordinary_as_count=len(construction.ordinary_autonomous_systems),
        ix_count=len(specification.internet_exchanges),
        ebgp_peering_count=len(specification.ebgp_peerings),
        catalog_node_count=len(construction.catalog.list_nodes()),
    )
    return LargeStaticScenario(
        construction=construction,
        summary=summary,
        timings_seconds={
            "input_parse": input_parse_seconds,
            "static_construction": construction_seconds,
        },
    )


def compile_scenario(
    scenario: LargeStaticScenario,
    output: Path,
    *,
    override: bool,
    internet_map_enabled: bool,
) -> tuple[LargeStaticSummary, dict[str, float]]:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    render_started = perf_counter()
    scenario.construction.emulator.render()
    render_seconds = perf_counter() - render_started

    compile_started = perf_counter()
    scenario.construction.emulator.compile(
        SkyseedDockerCompiler(internet_map_enabled=internet_map_enabled),
        str(output),
        override=override,
    )
    compile_seconds = perf_counter() - compile_started

    compose_path = output / "docker-compose.yml"
    compose = safe_load(compose_path.read_text(encoding="utf-8"))
    if not isinstance(compose, dict) or not isinstance(compose.get("services"), dict):
        raise RuntimeError("compiled Compose must contain a services mapping")
    summary = LargeStaticSummary(
        **{
            **asdict(scenario.summary),
            "compose_service_count": len(compose["services"]),
            "compose_file_size_bytes": compose_path.stat().st_size,
        }
    )
    timings = {
        **scenario.timings_seconds,
        "render": render_seconds,
        "compile": compile_seconds,
    }
    report = {
        "scenario": "large-static-network",
        "semantics": {
            "dynamic_satellite_network": False,
            "v2_owned_static_input": True,
            "simulation_time_in_emulator": False,
            "historical_labels_imported": False,
        },
        "summary": asdict(summary),
        "timings_seconds": {
            key: round(value, 6) for key, value in sorted(timings.items())
        },
    }
    (output / "scenario-summary.json").write_text(
        dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary, timings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--override", action="store_true")
    parser.add_argument("--no-internet-map", action="store_true")
    arguments = parser.parse_args()

    scenario = build_scenario(arguments.scenario)
    summary, timings = compile_scenario(
        scenario,
        arguments.output,
        override=arguments.override,
        internet_map_enabled=not arguments.no_internet_map,
    )
    print(dumps(asdict(summary), sort_keys=True))
    print(
        dumps(
            {
                key: round(value, 6)
                for key, value in sorted(timings.items())
            },
            sort_keys=True,
        )
    )
    print(f"compiled large static network to {arguments.output}")


if __name__ == "__main__":
    main()
