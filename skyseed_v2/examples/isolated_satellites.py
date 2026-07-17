"""Compile one Shell, one Orbit, and two explicitly isolated Satellites."""

from __future__ import annotations

import argparse
from pathlib import Path

from seedemu.core import Emulator

from skyseed_v2.domain import OrbitParameters
from skyseed_v2.seed import (
    SatelliteBase,
    SkyseedAutonomousSystem,
    SkyseedDockerCompiler,
)


def build_emulator() -> tuple[Emulator, SkyseedAutonomousSystem]:
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
        ),
    )
    orbit.create_satellites(
        count=2,
        satellite_id_prefix="satellite",
        payload={"profile": "baseline"},
    )

    emulator.addLayer(base)
    return emulator, satellite_as


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_name("output") / "isolated_satellites",
    )
    parser.add_argument(
        "--no-internet-map",
        action="store_true",
        help="omit the runtime InternetMap service",
    )
    parser.add_argument("--override", action="store_true")
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

    print(f"compiled {len(satellite_as.satellites())} Satellites to {arguments.output}")
    for descriptor in satellite_as.satellite_descriptors():
        print(f"- {descriptor.satellite_id}: {descriptor.runtime_node.value}")


if __name__ == "__main__":
    main()
