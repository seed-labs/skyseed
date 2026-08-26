"""Compose the Planner Tool's read-only inspection stages."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from skyseed_v2.simulator import (
    Plan,
    PlanningDecision,
    Slicer,
    TopologyDifference,
)

from .artifact_writer import write_planner_artifacts
from .projection import project_planner_data
from .reader import read_planner_inspection
from .renderer import render_planner_page


def collect_planner_data(
    slicer: Slicer,
    plans: tuple[Plan, ...],
    differences: tuple[TopologyDifference, ...],
    decisions: tuple[PlanningDecision, ...],
    configuration: Mapping[str, object],
) -> dict[str, Any]:
    """Read exact formal results and create the private Tool projection."""

    return project_planner_data(
        read_planner_inspection(
            slicer,
            plans,
            differences,
            decisions,
            configuration,
        )
    )


def write_planner_tool(
    slicer: Slicer,
    plans: tuple[Plan, ...],
    differences: tuple[TopologyDifference, ...],
    decisions: tuple[PlanningDecision, ...],
    configuration: Mapping[str, object],
    directory: Path,
) -> dict[str, object]:
    """Produce the complete primary Planner inspection artifact."""

    data = collect_planner_data(
        slicer,
        plans,
        differences,
        decisions,
        configuration,
    )
    return write_planner_artifacts(
        data,
        render_planner_page(data),
        directory,
    )


__all__ = ["collect_planner_data", "write_planner_tool"]
