"""Validate Skyseed Emulator entity values."""

from __future__ import annotations

from math import isfinite


def _validate_angle(
    value: float,
    field_name: str,
    *,
    inclusive_upper: bool = False,
) -> float:
    value = float(value)
    upper_valid = value <= 360.0 if inclusive_upper else value < 360.0
    if not isfinite(value) or value < 0.0 or not upper_valid:
        upper = "360" if inclusive_upper else "360 (exclusive)"
        raise ValueError(f"{field_name} must be between 0 and {upper}")
    return value
