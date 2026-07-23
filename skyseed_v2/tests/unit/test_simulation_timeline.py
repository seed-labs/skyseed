from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from itertools import islice

import pytest

from skyseed_v2.simulator import SimulationTimeline


def test_bounded_timeline_yields_only_interval_aligned_timestamps() -> None:
    start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    timeline = SimulationTimeline(
        start_time=start,
        end_time=start + timedelta(seconds=25),
        slice_interval=timedelta(seconds=10),
    )

    assert timeline.is_bounded is True
    assert tuple(timeline.iter_timestamps()) == (
        start,
        start + timedelta(seconds=10),
        start + timedelta(seconds=20),
    )


def test_unbounded_timeline_is_lazy_and_normalizes_to_utc() -> None:
    local_timezone = timezone(timedelta(hours=8))
    start = datetime(2026, 1, 1, 8, 0, tzinfo=local_timezone)
    timeline = SimulationTimeline(
        start_time=start,
        end_time=None,
        slice_interval=timedelta(seconds=10),
    )

    timestamps = timeline.iter_timestamps()
    assert timeline.is_bounded is False
    assert iter(timestamps) is timestamps
    expected_start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert tuple(islice(timestamps, 4)) == tuple(
        expected_start + timedelta(seconds=10 * index) for index in range(4)
    )
    assert all(
        timestamp.tzinfo is timezone.utc
        for timestamp in islice(timestamps, 3)
    )


@pytest.mark.parametrize(
    "interval",
    (timedelta(0), timedelta(microseconds=-1)),
)
def test_timeline_rejects_non_positive_intervals(interval: timedelta) -> None:
    with pytest.raises(ValueError, match="slice_interval"):
        SimulationTimeline(
            datetime(2026, 1, 1, tzinfo=timezone.utc), None, interval
        )


def test_timeline_rejects_invalid_order_and_naive_datetimes() -> None:
    naive = datetime(2026, 1, 1)
    aware = datetime(2026, 1, 1, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="timezone-aware"):
        SimulationTimeline(
            start_time=naive,
            end_time=naive - timedelta(seconds=1),
            slice_interval=timedelta(seconds=10),
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        SimulationTimeline(naive, aware, timedelta(seconds=10))
    with pytest.raises(ValueError, match="end_time"):
        SimulationTimeline(
            start_time=aware,
            end_time=aware - timedelta(seconds=1),
            slice_interval=timedelta(seconds=10),
        )


@pytest.mark.parametrize(
    ("start", "end", "interval", "message"),
    (
        ("2026-01-01", None, timedelta(seconds=10), "start_time"),
        (datetime(2026, 1, 1, tzinfo=timezone.utc), "2026-01-02", timedelta(seconds=10), "end_time"),
        (datetime(2026, 1, 1, tzinfo=timezone.utc), None, 10, "slice_interval"),
    ),
)
def test_timeline_rejects_wrong_value_types(start, end, interval, message) -> None:
    with pytest.raises(TypeError, match=message):
        SimulationTimeline(start, end, interval)


def test_timeline_is_immutable_and_includes_an_equal_end_point() -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    timeline = SimulationTimeline(start, start, timedelta(seconds=10))

    assert tuple(timeline.iter_timestamps()) == (start,)
    with pytest.raises(FrozenInstanceError):
        timeline.end_time = None
