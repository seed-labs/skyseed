"""Construct the Emulator for the richer Slice / Difference reference example."""

from __future__ import annotations

from pathlib import Path

from seedemu.core import Emulator

from skyseed_v2.configuration.emulator import load_static_emulator_config
from skyseed_v2.emulator import create_static_emulator


EXAMPLE_ROOT = Path(__file__).resolve().parent
DEFAULT_SCENARIO = EXAMPLE_ROOT / "scenario.yaml"


def build_mini_satellite_internet_emulator(
    scenario_path: Path = DEFAULT_SCENARIO,
) -> Emulator:
    """Load the explicit static input and return the upstream Emulator."""

    config = load_static_emulator_config(scenario_path)
    return create_static_emulator(config)


__all__ = ["build_mini_satellite_internet_emulator"]
