"""Project a detached Dynamic Slice snapshot into final Tool view data."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .display_analysis import add_dynamic_display_analysis
from .reader import DynamicSliceRead


def project_dynamic_slice_data(
    observation: DynamicSliceRead,
) -> dict[str, Any]:
    """Copy Tool-owned reads and attach non-authoritative display analysis."""

    if not isinstance(observation, DynamicSliceRead):
        raise TypeError("observation must be a DynamicSliceRead")
    data = deepcopy(observation.snapshot)
    add_dynamic_display_analysis(data)
    return data


__all__ = ["project_dynamic_slice_data"]
