"""Shared UTC normalization helpers."""

from __future__ import annotations

from datetime import datetime, timezone


def normalize_utc_datetime(value: datetime, field_name: str) -> datetime:
    """Validate an aware datetime and return the equivalent UTC instant."""

    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime")
    try:
        offset = value.utcoffset()
    except Exception as error:  # pragma: no cover - defensive custom tzinfo guard
        raise ValueError(f"{field_name} timezone information is invalid") from error
    if offset is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)
