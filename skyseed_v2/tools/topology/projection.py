"""Project a detached Topology read snapshot into final Tool view data."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .display_analysis import add_topology_display_analysis
from .reader import TopologyRead


def project_topology_data(observation: TopologyRead) -> dict[str, Any]:
    """Copy Tool-owned read values and add non-authoritative display data."""

    if not isinstance(observation, TopologyRead):
        raise TypeError("observation must be a TopologyRead")
    data = deepcopy(observation.snapshot)
    add_topology_display_analysis(data)
    return data


__all__ = ["project_topology_data"]
