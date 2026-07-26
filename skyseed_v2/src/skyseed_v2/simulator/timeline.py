"""Immutable time rules for one Simulator definition."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta

from skyseed_v2.shared.time import normalize_utc_datetime


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
        if not isinstance(self.slice_interval, timedelta):
            raise TypeError("slice_interval must be a timedelta")
        if self.slice_interval <= timedelta(0):
            raise ValueError("slice_interval must be greater than zero")

        start = normalize_utc_datetime(self.start_time, "start_time")
        end = (
            None
            if self.end_time is None
            else normalize_utc_datetime(self.end_time, "end_time")
        )
        if end is not None and end < start:
            raise ValueError("end_time must be greater than or equal to start_time")
        object.__setattr__(self, "start_time", start)
        object.__setattr__(self, "end_time", end)

    @property
    def is_bounded(self) -> bool:
        return self.end_time is not None

    def iter_timestamps(self) -> Iterator[datetime]:
        """Yield interval-aligned timestamps without precomputing a sequence."""

        timestamp = self.start_time
        while self.end_time is None or timestamp <= self.end_time:
            yield timestamp
            timestamp += self.slice_interval
__all__ = ["SimulationTimeline"]
