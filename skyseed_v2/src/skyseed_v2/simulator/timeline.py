"""Immutable time rules for one Simulator definition."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True, slots=True)
class SimulationTimeline:
    """Define lazily generated timestamps for one simulation.

    Layer: immutable Simulator domain value. Users create it and
    ``SimulationDefinition`` retains it. It is not a clock, timestamp cache,
    pacing service or Slice store. It is public from ``skyseed_v2.simulator``
    because every Simulator definition requires explicit time rules.
    """

    start_time: datetime
    end_time: datetime | None
    slice_interval: timedelta

    def __post_init__(self) -> None:
        if not isinstance(self.start_time, datetime):
            raise TypeError("start_time must be a datetime")
        if self.end_time is not None and not isinstance(self.end_time, datetime):
            raise TypeError("end_time must be a datetime or None")
        if not isinstance(self.slice_interval, timedelta):
            raise TypeError("slice_interval must be a timedelta")
        if self.slice_interval <= timedelta(0):
            raise ValueError("slice_interval must be greater than zero")

        start_is_aware = _is_aware(self.start_time)
        if self.end_time is not None:
            if _is_aware(self.end_time) != start_is_aware:
                raise ValueError(
                    "start_time and end_time must have matching timezone awareness"
                )
            if self.end_time < self.start_time:
                raise ValueError("end_time must be greater than or equal to start_time")

    @property
    def is_bounded(self) -> bool:
        return self.end_time is not None

    def iter_timestamps(self) -> Iterator[datetime]:
        """Yield interval-aligned timestamps without precomputing a sequence."""

        timestamp = self.start_time
        while self.end_time is None or timestamp <= self.end_time:
            yield timestamp
            timestamp += self.slice_interval


def _is_aware(value: datetime) -> bool:
    try:
        return value.utcoffset() is not None
    except Exception as error:  # pragma: no cover - defensive custom tzinfo guard
        raise ValueError("datetime timezone information is invalid") from error


__all__ = ["SimulationTimeline"]
