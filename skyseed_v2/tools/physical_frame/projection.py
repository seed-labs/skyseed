"""Project detached PhysicalFrame reads into a final Tool view."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .reader import PhysicalFrameRead


def project_physical_frame_data(
    observation: PhysicalFrameRead,
) -> dict[str, Any]:
    """Add only Tool-owned counts and display explanations."""

    if not isinstance(observation, PhysicalFrameRead):
        raise TypeError("observation must be a PhysicalFrameRead")
    data = deepcopy(observation.snapshot)
    nodes = data["nodes"]
    orbits = data["orbits"]
    assert isinstance(nodes, tuple)
    assert isinstance(orbits, tuple)
    data["inventory"] = {
        "positions": len(nodes),
        "satellites": sum(value["kind"] == "satellite" for value in nodes),
        "ground_stations": sum(
            value["kind"] == "ground-station" for value in nodes
        ),
        "orbits": len(orbits),
    }
    data["display_notes"] = {
        "map": (
            "The map uses formal WGS84 geodetic views as a non-authoritative "
            "display projection."
        ),
        "height": (
            "Ellipsoidal height remains an exact per-node value in the detail "
            "panel and is not encoded by marker size."
        ),
    }
    return data


__all__ = ["project_physical_frame_data"]
