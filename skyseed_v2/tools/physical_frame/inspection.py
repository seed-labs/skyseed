"""Compose the Physical Frame Tool's explicit read-only stages."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from skyseed_v2.simulator import PhysicalFrame, Topology

from .artifact_writer import write_physical_frame_artifacts
from .projection import project_physical_frame_data
from .reader import read_physical_frame
from .renderer import render_physical_frame_page


def collect_physical_frame_data(
    topology: Topology,
    frame: PhysicalFrame,
) -> dict[str, Any]:
    """Read one exact physical input pair into detached view data."""

    return project_physical_frame_data(read_physical_frame(topology, frame))


def write_physical_frame_tool(
    topology: Topology,
    frame: PhysicalFrame,
    directory: Path,
) -> dict[str, object]:
    """Read, project, render, and write without invoking physical projection."""

    data = collect_physical_frame_data(topology, frame)
    return write_physical_frame_artifacts(
        data,
        render_physical_frame_page(data),
        directory,
    )


__all__ = ["collect_physical_frame_data", "write_physical_frame_tool"]
