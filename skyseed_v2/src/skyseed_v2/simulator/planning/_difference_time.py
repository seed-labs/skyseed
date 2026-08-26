"""Resolve transition times within one private Planner Difference window."""

from __future__ import annotations

from bisect import bisect_left
from datetime import datetime

from ..slicing import TopologyDifference


class _DifferenceTimeResolver:
    """Cache exact legacy time-to-Difference resolution for one tuple."""

    __slots__ = ("_cache", "_current_timestamps", "_differences", "_indexed")

    def __init__(
        self,
        differences: tuple[TopologyDifference, ...],
    ) -> None:
        self._differences = differences
        self._cache: dict[datetime, TopologyDifference] = {}
        self._indexed = _is_contiguous_monotone_window(differences)
        self._current_timestamps = (
            tuple(value.current_timestamp for value in differences)
            if self._indexed
            else ()
        )

    def difference_for_time(
        self,
        nominal_time: datetime,
    ) -> TopologyDifference:
        try:
            return self._cache[nominal_time]
        except KeyError:
            pass

        if self._indexed:
            position = bisect_left(self._current_timestamps, nominal_time)
            if position < len(self._differences):
                candidate = self._differences[position]
                if _time_belongs_to_difference(candidate, nominal_time):
                    self._cache[nominal_time] = candidate
                    return candidate
        else:
            for candidate in self._differences:
                if _time_belongs_to_difference(candidate, nominal_time):
                    self._cache[nominal_time] = candidate
                    return candidate
        raise ValueError("transition nominal time is outside planning window")


def _is_contiguous_monotone_window(
    differences: tuple[TopologyDifference, ...],
) -> bool:
    previous: TopologyDifference | None = None
    for position, current in enumerate(differences):
        if current.previous_timestamp is None:
            if position != 0:
                return False
        elif current.previous_timestamp >= current.current_timestamp:
            return False
        if previous is not None:
            if (
                current.previous_timestamp != previous.current_timestamp
                or current.current_timestamp <= previous.current_timestamp
            ):
                return False
        previous = current
    return True


def _time_belongs_to_difference(
    difference: TopologyDifference,
    nominal_time: datetime,
) -> bool:
    if difference.previous_timestamp is None:
        return nominal_time == difference.current_timestamp
    return (
        difference.previous_timestamp
        < nominal_time
        <= difference.current_timestamp
    )


__all__: list[str] = []
