"""Project a detached Zone read snapshot into final Tool view data."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .display_analysis import add_zone_display_analysis
from .reader import ZoneRead


def project_zone_data(observation: ZoneRead) -> dict[str, Any]:
    """Copy Tool-owned reads and attach display-only analysis."""

    if not isinstance(observation, ZoneRead):
        raise TypeError("observation must be a ZoneRead")
    data = deepcopy(observation.snapshot)
    add_zone_display_analysis(data)
    return data


__all__ = ["project_zone_data"]
