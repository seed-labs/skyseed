from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import pytest

from skyseed_v2.simulator import SimulationTimeline


def test_bounded_timeline_yields_only_interval_aligned_timestamps() -> None:
    start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    timeline = SimulationTimeline(
        start_time=start,
        end_time=start + timedelta(seconds=25),
        observation_interval=timedelta(seconds=10),
    )

    assert not hasattr(timeline, "is_bounded")
    assert not hasattr(timeline, "slice_interval")
    assert tuple(timeline.iter_timestamps()) == (
        start,
        start + timedelta(seconds=10),
        start + timedelta(seconds=20),
    )


def test_timeline_requires_finite_end_and_normalizes_to_utc() -> None:
    local_timezone = timezone(timedelta(hours=8))
    start = datetime(2026, 1, 1, 8, 0, tzinfo=local_timezone)
    timeline = SimulationTimeline(
        start_time=start,
        end_time=start + timedelta(seconds=30),
        observation_interval=timedelta(seconds=10),
    )

    timestamps = timeline.iter_timestamps()
    assert iter(timestamps) is timestamps
    expected_start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert tuple(timestamps) == tuple(
        expected_start + timedelta(seconds=10 * index) for index in range(4)
    )


@pytest.mark.parametrize(
    "interval",
    (timedelta(0), timedelta(microseconds=-1)),
)
def test_timeline_rejects_non_positive_intervals(interval: timedelta) -> None:
    with pytest.raises(ValueError, match="observation_interval"):
        SimulationTimeline(
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            interval,
        )


def test_timeline_rejects_invalid_order_and_naive_datetimes() -> None:
    naive = datetime(2026, 1, 1)
    aware = datetime(2026, 1, 1, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="timezone-aware"):
        SimulationTimeline(
            start_time=naive,
            end_time=naive - timedelta(seconds=1),
            observation_interval=timedelta(seconds=10),
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        SimulationTimeline(naive, aware, timedelta(seconds=10))
    with pytest.raises(ValueError, match="end_time"):
        SimulationTimeline(
            start_time=aware,
            end_time=aware - timedelta(seconds=1),
            observation_interval=timedelta(seconds=10),
        )
    with pytest.raises(TypeError, match="end_time"):
        SimulationTimeline(aware, None, timedelta(seconds=10))


@pytest.mark.parametrize(
    ("start", "end", "interval", "message"),
    (
        ("2026-01-01", None, timedelta(seconds=10), "start_time"),
        (
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            "2026-01-02",
            timedelta(seconds=10),
            "end_time",
        ),
        (
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 2, tzinfo=timezone.utc),
            10,
            "observation_interval",
        ),
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
