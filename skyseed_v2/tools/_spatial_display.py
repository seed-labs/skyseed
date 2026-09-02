"""Shared display-only interaction policy for spatial inspection canvases."""

from __future__ import annotations


def spatial_interaction_policy() -> dict[str, object]:
    """Return non-semantic camera controls shared by repository Tools."""

    return {
        "globe": {
            "initial_yaw_radians": -0.55,
            "initial_pitch_radians": 0.28,
            "minimum_zoom": 0.55,
            "maximum_zoom": 5.0,
            "wheel_sensitivity": 0.0015,
        },
        "map": {
            "minimum_zoom": 1.0,
            "maximum_zoom": 12.0,
            "wheel_sensitivity": 0.0015,
        },
        "selection_radius_css_pixels": 9,
        "drag_threshold_css_pixels": 4,
        "semantics": (
            "camera, pan, zoom, focus, and display projection are Tool-local "
            "and never change authoritative ECEF or planning evidence"
        ),
    }


__all__ = ["spatial_interaction_policy"]
