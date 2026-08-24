"""Project a detached Slice / Difference snapshot into Tool view data."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .display_analysis import add_slice_display_analysis
from .reader import SliceDifferenceRead


def project_slice_difference_data(
    observation: SliceDifferenceRead,
) -> dict[str, Any]:
    """Copy Tool-owned reads and attach non-authoritative display analysis."""

    if not isinstance(observation, SliceDifferenceRead):
        raise TypeError("observation must be a SliceDifferenceRead")
    data = deepcopy(observation.snapshot)
    add_slice_display_analysis(data)
    return data


__all__ = ["project_slice_difference_data"]
