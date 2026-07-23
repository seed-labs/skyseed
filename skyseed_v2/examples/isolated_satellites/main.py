"""Construct two isolated Satellites with the public entity API."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys


EXAMPLE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = EXAMPLE_ROOT.parents[1]
DEFAULT_OUTPUT = EXAMPLE_ROOT / "output"
COMPOSE_PROJECT_NAME = "skyseed_v2_isolated_satellites"

if __package__ in (None, ""):
    sys.path.insert(0, str(PROJECT_ROOT))

from examples._direct_execution import prepare_direct_file_execution

prepare_direct_file_execution(__file__)

from seedemu.core import Emulator

from examples._compose_project_scripts import write_compose_project_scripts
from skyseed_v2 import OrbitParameters
from skyseed_v2.seedemu_integration import (
    SatelliteBase,
    SkyseedAutonomousSystem,
    SkyseedDockerCompiler,
)


def build_emulator() -> tuple[Emulator, SkyseedAutonomousSystem]:
    """Show the shortest approved AS → Shell → Orbit → Satellite path."""

    emulator = Emulator()
    base = SatelliteBase()
    satellite_as = SkyseedAutonomousSystem(65000)
    base.setAutonomousSystem(satellite_as)

    shell = satellite_as.create_shell("shell-1")
    orbit = shell.create_orbit(
        "orbit-1",
        parameters=OrbitParameters(
            altitude_km=550.0,
            inclination_deg=53.0,
            right_ascension_deg=15.0,
            epoch_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
    )
    orbit.create_satellite(
        "satellite-0",
        orbit_index=0,
        mean_anomaly_deg=0.0,
    )
    orbit.create_satellite(
        "satellite-1",
        orbit_index=1,
        mean_anomaly_deg=180.0,
    )

    emulator.addLayer(base)
    return emulator, satellite_as


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--no-internet-map", action="store_true")
    parser.add_argument(
        "--override",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="replace an existing output directory (default: enabled)",
    )
    arguments = parser.parse_args()

    emulator, satellite_as = build_emulator()
    emulator.render()
    emulator.compile(
        SkyseedDockerCompiler(
            internet_map_enabled=not arguments.no_internet_map
        ),
        str(arguments.output),
        override=arguments.override,
    )
    write_compose_project_scripts(arguments.output, COMPOSE_PROJECT_NAME)

    satellites = satellite_as.satellites()
    print(f"compiled {len(satellites)} Satellites to {arguments.output}")
    print(f"start with {arguments.output / 'start.sh'}")
    for satellite in satellites:
        print(f"- {satellite.satellite_id}")


if __name__ == "__main__":
    main()
