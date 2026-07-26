"""Compose the Topology Tool's explicit read-only stages."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from skyseed_v2.simulator import Topology

from .artifact_writer import write_topology_artifacts
from .projection import project_topology_data
from .reader import read_topology
from .renderer import render_topology_page


def collect_topology_data(topology: Topology) -> dict[str, Any]:
    return project_topology_data(read_topology(topology))


def write_topology_tool(
    topology: Topology,
    directory: Path,
) -> dict[str, object]:
    data = collect_topology_data(topology)
    return write_topology_artifacts(
        data,
        render_topology_page(data),
        directory,
    )


__all__ = ["collect_topology_data", "write_topology_tool"]
