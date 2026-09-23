"""Compose the Performance Tool's read-only stages."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .artifact_writer import write_performance_artifacts
from .projection import project_performance
from .reader import PerformanceRead, read_execution_observations
from .renderer import render_performance_page


def collect_performance_data(path: Path) -> dict[str, Any]:
    """Read and aggregate one completed observation stream."""

    return project_performance(read_execution_observations(path))


def write_performance_tool(path: Path, directory: Path) -> dict[str, object]:
    """Parse, aggregate, render, and write without domain execution."""

    observations: PerformanceRead = read_execution_observations(path)
    data = project_performance(observations)
    return write_performance_artifacts(
        observations,
        data,
        render_performance_page(data),
        directory,
    )


__all__ = ["collect_performance_data", "write_performance_tool"]
