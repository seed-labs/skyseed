"""Construct the complete static Emulator with public Skyseed and SEED APIs."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


EXAMPLE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = EXAMPLE_ROOT.parents[1]
DEFAULT_OUTPUT = EXAMPLE_ROOT / "output"
COMPOSE_PROJECT_NAME = "skyseed_v2_complete_static_emulator"

if __package__ in (None, ""):
    sys.path.insert(0, str(PROJECT_ROOT))

from examples._direct_execution import prepare_direct_file_execution

prepare_direct_file_execution(__file__)

from examples._compose_project_scripts import write_compose_project_scripts
from examples.complete_static_emulator.emu import build_emulator
from skyseed_v2.seedemu_integration import SkyseedDockerCompiler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--override",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="replace an existing output directory (default: enabled)",
    )
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
