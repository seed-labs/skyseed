"""Compose the Slice / Difference Tool's explicit read-only stages."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from skyseed_v2.simulator import Slicer

from .artifact_writer import write_slice_difference_artifacts
from .projection import project_slice_difference_data
from .reader import read_slice_differences
from .renderer import render_slice_difference_page


def collect_slice_difference_data(slicer: Slicer) -> dict[str, Any]:
    """Read formal values, then create the Tool-owned projection."""

    return project_slice_difference_data(read_slice_differences(slicer))


def write_slice_difference_tool(
    slicer: Slicer,
    directory: Path,
) -> dict[str, object]:
    """Run reader/projection/display/render/write without another authority."""

    data = collect_slice_difference_data(slicer)
    return write_slice_difference_artifacts(
        data,
        render_slice_difference_page(data),
        directory,
    )


__all__ = ["collect_slice_difference_data", "write_slice_difference_tool"]
