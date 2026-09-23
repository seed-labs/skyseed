"""Immutable time rules for one Simulator definition."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta

from skyseed.shared.time import normalize_utc_datetime


@dataclass(frozen=True, slots=True)
class SimulationTimeline:
    """Define lazily generated timestamps for one simulation.

    Layer: immutable Simulator domain value. Users create it and
    ``SimulationDefinition`` retains it. It is not a clock, timestamp cache,
    pacing service or operational-history store. It is public from
    ``skyseed.simulator``
    because every Simulator definition requires explicit time rules.
    """

    start_time: datetime
    end_time: datetime
    observation_interval: timedelta

    def __post_init__(self) -> None:
        if not isinstance(self.observation_interval, timedelta):
            raise TypeError("observation_interval must be a timedelta")
        if self.observation_interval <= timedelta(0):
            raise ValueError("observation_interval must be greater than zero")

        start = normalize_utc_datetime(self.start_time, "start_time")
        end = normalize_utc_datetime(self.end_time, "end_time")
        if end < start:
            raise ValueError(
                "end_time must be greater than or equal to start_time"
            )
        object.__setattr__(self, "start_time", start)
        object.__setattr__(self, "end_time", end)

    def iter_timestamps(self) -> Iterator[datetime]:
        """Yield interval-aligned timestamps without precomputing a sequence."""

        timestamp = self.start_time
        while timestamp <= self.end_time:
            yield timestamp
            timestamp += self.observation_interval


__all__ = ["SimulationTimeline"]
