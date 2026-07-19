"""Construct the complete static Emulator with public Skyseed and SEED APIs."""

from __future__ import annotations

import argparse
from pathlib import Path

from examples._compose_project_scripts import write_compose_project_scripts
from skyseed_v2.seedemu_integration import SkyseedDockerCompiler

from .emu import build_emulator


EXAMPLE_ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = EXAMPLE_ROOT / "output"
COMPOSE_PROJECT_NAME = "skyseed_v2_complete_static_emulator"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--override", action="store_true")
    parser.add_argument("--no-internet-map", action="store_true")
    arguments = parser.parse_args()

    emulator = build_emulator()
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    emulator.render()
    emulator.compile(
        SkyseedDockerCompiler(
            internet_map_enabled=not arguments.no_internet_map
        ),
        str(arguments.output),
        override=arguments.override,
    )
    write_compose_project_scripts(arguments.output, COMPOSE_PROJECT_NAME)
    print(f"compiled complete static Emulator to {arguments.output}")
    print(f"start with {arguments.output / 'start.sh'}")


if __name__ == "__main__":
    main()
