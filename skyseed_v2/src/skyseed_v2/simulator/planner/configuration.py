"""Planner preparation and publication configuration, independent of Timeline cadence."""
from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True, slots=True)
class PlannerConfiguration:
    """Configure the sole Planner's acquisition and bounded publication batch.

    Layer: Simulator Planner configuration. It is public because callers create
    and supply it to Planner or Simulator assembly. It is not a Timeline, Model
    minimum-service policy, delivery target or operational state owner.
    """

    frames_per_batch: int = 32
    inter_satellite_acquisition_duration: timedelta = timedelta(seconds=5)
    ground_station_acquisition_duration: timedelta = timedelta(seconds=5)

    def __post_init__(self) -> None:
        for name in ("inter_satellite_acquisition_duration", "ground_station_acquisition_duration"):
            value = getattr(self, name)
            if not isinstance(value, timedelta):
                raise TypeError(f"{name} must be a timedelta")
            if value < timedelta(0):
                raise ValueError(f"{name} must be non-negative")
        if (isinstance(self.frames_per_batch, bool)
                or not isinstance(self.frames_per_batch, int)
                or self.frames_per_batch <= 0):
            raise ValueError("frames_per_batch must be a positive integer")
