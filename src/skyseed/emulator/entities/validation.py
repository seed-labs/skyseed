"""Validate SkySEED Emulator entity values."""

from __future__ import annotations

from math import isfinite


def _validate_angle(
    value: float,
    field_name: str,
) -> float:
    value = float(value)
    if not isfinite(value) or not 0.0 <= value < 360.0:
        raise ValueError(f"{field_name} must be between 0 and 360 (exclusive)")
    return value
