"""Construct the v2-native Starlink Gen1 static Emulator."""

from __future__ import annotations

from pathlib import Path

from seedemu.core import Emulator

from skyseed_v2.configuration.emulator import load_static_emulator_config
from skyseed_v2.emulator import create_static_emulator


EXAMPLE_ROOT = Path(__file__).resolve().parent
DEFAULT_SCENARIO = EXAMPLE_ROOT / "scenario.yaml"


def build_starlink_gen1_satellite_internet_emulator(
    scenario_path: Path = DEFAULT_SCENARIO,
) -> Emulator:
    """Load the frozen Starlink Gen1 input and return the upstream Emulator."""

    return create_static_emulator(load_static_emulator_config(scenario_path))


__all__ = ["build_starlink_gen1_satellite_internet_emulator"]
