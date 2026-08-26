from __future__ import annotations

import json

import pytest

from skyseed_v2.shared._execution_observation import _ExecutionObserver


def test_execution_observer_detaches_metadata_records_and_persists_jsonl(
    tmp_path,
) -> None:
    metadata = {"scenario": "probe", "nested": {"generation": 1}}
    scope = {"slice_id": 3}
    work = {"route_entries": 12}
    observer = _ExecutionObserver(metadata)
    observer.record(
        component="slicer",
        stage="route-compute",
        phase="steady",
        duration_seconds=0.125,
        scope=scope,
        work=work,
        resources={"rss_bytes": 2048},
    )
    metadata["nested"]["generation"] = 2
    scope["slice_id"] = 99
    work["route_entries"] = 99

    snapshot = observer.snapshot()
    assert snapshot["header"]["metadata"] == {
        "scenario": "probe",
        "nested": {"generation": 1},
    }
    assert snapshot["records"][0]["scope"] == {"slice_id": 3}
    assert snapshot["records"][0]["work"] == {"route_entries": 12}

    path = observer.write_jsonl(tmp_path / "observations.jsonl")
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows[0]["record_type"] == "execution_observation_header"
    assert rows[1]["sequence"] == 0
    assert rows[1]["resources"] == {"rss_bytes": 2048}


@pytest.mark.parametrize(
    ("field", "value", "error"),
    (
        ("phase", "warm", ValueError),
        ("duration_seconds", -1.0, ValueError),
        ("duration_seconds", float("inf"), ValueError),
        ("duration_seconds", True, TypeError),
        ("work", {"routes": float("nan")}, ValueError),
        ("resources", {"rss": "large"}, TypeError),
    ),
)
def test_execution_observer_fails_open_then_refuses_incomplete_evidence(
    field: str,
    value: object,
    error: type[Exception],
    tmp_path,
) -> None:
    arguments = {
        "component": "planner",
        "stage": "validation",
        "phase": "steady",
        "duration_seconds": 1.0,
        "work": {},
        "resources": {},
    }
    arguments[field] = value
    observer = _ExecutionObserver()

    observer.record(**arguments)

    assert isinstance(observer._get_recording_failure(), error)
    observer.record(
        component="planner",
        stage="publication",
        phase="steady",
        duration_seconds=2.0,
    )
    with pytest.raises(RuntimeError, match="observation is incomplete") as caught:
        observer.snapshot()
    assert isinstance(caught.value.__cause__, error)
    path = tmp_path / "incomplete.jsonl"
    with pytest.raises(RuntimeError, match="observation is incomplete"):
        observer.write_jsonl(path)
    assert not path.exists()
