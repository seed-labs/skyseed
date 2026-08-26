"""Compose the System Observation Tool's read-only stages."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .artifact_writer import write_observation_artifacts
from .projection import project_observations
from .reader import ObservationRead, read_execution_observations
from .renderer import render_observation_page


def collect_observation_data(path: Path) -> dict[str, Any]:
    """Read and aggregate one completed observation stream."""

    return project_observations(read_execution_observations(path))


def write_observation_tool(path: Path, directory: Path) -> dict[str, object]:
    """Parse, aggregate, render, and write without domain execution."""

    observations: ObservationRead = read_execution_observations(path)
    data = project_observations(observations)
    return write_observation_artifacts(
        observations,
        data,
        render_observation_page(data),
        directory,
    )


__all__ = ["collect_observation_data", "write_observation_tool"]
