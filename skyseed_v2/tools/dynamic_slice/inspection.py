"""Compose the Dynamic Slice Tool's five explicit read-only stages."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from skyseed_v2.simulator import Slicer

from .artifact_writer import write_dynamic_slice_artifacts
from .projection import project_dynamic_slice_data
from .reader import read_dynamic_slices
from .renderer import render_dynamic_slice_page


def collect_dynamic_slice_data(slicer: Slicer) -> dict[str, Any]:
    """Read formal values, then create the Tool-owned projection."""

    return project_dynamic_slice_data(read_dynamic_slices(slicer))


def write_dynamic_slice_tool(
    slicer: Slicer,
    directory: Path,
) -> dict[str, object]:
    """Run reader/projection/display/render/write without another authority."""

    data = collect_dynamic_slice_data(slicer)
    return write_dynamic_slice_artifacts(
        data,
        render_dynamic_slice_page(data),
        directory,
    )


__all__ = ["collect_dynamic_slice_data", "write_dynamic_slice_tool"]
