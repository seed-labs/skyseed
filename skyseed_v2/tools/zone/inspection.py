"""Compose the Zone Tool's explicit read-only stages."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from skyseed_v2.simulator import PhysicalWorld

from .artifact_writer import write_zone_artifacts
from .projection import project_zone_data
from .reader import read_zones
from .renderer import render_zone_page


def collect_zone_data(physical_world: PhysicalWorld) -> dict[str, Any]:
    return project_zone_data(read_zones(physical_world))


def write_zone_tool(
    physical_world: PhysicalWorld,
    directory: Path,
) -> dict[str, object]:
    data = collect_zone_data(physical_world)
    return write_zone_artifacts(
        data,
        render_zone_page(data),
        directory,
    )


__all__ = ["collect_zone_data", "write_zone_tool"]
