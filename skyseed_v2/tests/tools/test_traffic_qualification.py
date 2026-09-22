from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tools.observation.traffic.projection import _project_probe_qualification


START = datetime(2026, 1, 1, tzinfo=timezone.utc)
ORIGIN_SECONDS = 1_000.0
ORIGIN_NS = 1_000_000_000_000


def _episode(
    suffix: str,
    *,
    start_seconds: int = 0,
    end_seconds: int = 20,
    reachable: bool = True,
) -> dict[str, object]:
    return {
        "episode_id": f"episode-{suffix}",
        "probe_id": f"probe-{suffix}",
        "mode": "persistent",
        "start_time": (START + timedelta(seconds=start_seconds)).isoformat(),
        "end_time": (START + timedelta(seconds=end_seconds)).isoformat(),
        "state_intervals": [
            {
                "start_time": (
                    START + timedelta(seconds=start_seconds)
                ).isoformat(),
                "end_time": (START + timedelta(seconds=end_seconds)).isoformat(),
                "expected_reachable": reachable,
            }
        ],
    }


def _identity(suffix: str) -> dict[str, object]:
    return {
        "probe_id": f"probe-{suffix}",
        "direction": "ground-station-to-zone",
        "ground_station_id": f"ground-{suffix}",
        "zone_id": f"zone-{suffix}",
        "path_stability": "stable",
        "path_length_class": "short",
        "selection_reasons": ["persistent-stable-control"],
    }


def _record(
    record_type: str,
    suffix: str,
    seconds: float,
    **facts: object,
) -> dict[str, object]:
    return {
        "record_type": record_type,
        "episode_id": f"episode-{suffix}",
        "probe_id": f"probe-{suffix}",
        "host_monotonic_ns": ORIGIN_NS + round(seconds * 1_000_000_000),
        "published_active_frontier": (
            START + timedelta(seconds=10)
        ).isoformat(),
        **facts,
    }


def _qualification() -> dict[str, object]:
    suffixes = ("isolated", "activation", "blackhole", "unreachable")
    episodes = [
        _episode("isolated"),
        _episode("activation"),
        _episode("blackhole"),
        _episode("unreachable", reachable=False),
        _episode("future", start_seconds=15, end_seconds=20),
    ]
    execution: list[dict[str, object]] = [
        {
            "record_type": "execution_header",
            "plan_revision_id": "revision-a",
        }
    ]
    patterns = {
        "isolated": ((2.0, "timeout", 1, None), (3.0, "reply", 2, 1.5)),
        "activation": (
            (5.1, "timeout", 1, None),
            (5.2, "timeout", 2, None),
            (6.0, "reply", 3, 2.5),
        ),
        "blackhole": (
            (7.0, "timeout", 1, None),
            (8.0, "timeout", 2, None),
            (9.0, "timeout", 3, None),
        ),
        "unreachable": ((4.0, "timeout", 1, None),),
    }
    for index, suffix in enumerate(suffixes, start=1):
        execution.extend(
            (
                _record("episode_armed", suffix, 0.01),
                _record(
                    "generator_launched",
                    suffix,
                    0.02,
                    segment_index=1,
                    icmp_identifier=100 + index,
                    source_ipv4=f"100.64.0.{index}",
                    destination_ipv4=f"198.18.0.{index}",
                ),
            )
        )
        for timestamp, observation, sequence, rtt in patterns[suffix]:
            execution.append(
                _record(
                    "ping_observation",
                    suffix,
                    timestamp,
                    segment_index=1,
                    observation=observation,
                    icmp_sequence=sequence,
                    rtt_ms=rtt,
                )
            )
        received = sum(value[1] == "reply" for value in patterns[suffix])
        execution.extend(
            (
                _record(
                    "generator_stopped",
                    suffix,
                    10.01,
                    segment_index=1,
                    sent=len(patterns[suffix]),
                    received=received,
                    cleanup_result="cleaned",
                ),
                _record(
                    "episode_cancelled",
                    suffix,
                    10.02,
                    rearmable=True,
                ),
            )
        )
    execution.append(
        _record(
            "episode_not_run",
            "future",
            10.03,
            rearmable=True,
        )
    )
    execution.append(
        {
            "record_type": "executor_end",
            "host_monotonic_ns": ORIGIN_NS + 10_100_000_000,
            "complete": True,
            "cleanup_complete": True,
            "active_generators": 0,
            "plan_revision_id": "revision-a",
        }
    )
    return {
        "plan": {
            "planning_horizon": {
                "coverage_start_time": START.isoformat(),
                "coverage_end_time": (START + timedelta(seconds=20)).isoformat(),
                "revision_id": "revision-a",
                "terminality": "unknown",
                "revision_semantics": "revisable-lookahead",
            },
            "planning": {
                "duration_seconds": 0.25,
                "persistent_episode_count": len(episodes),
                "activation_window_episode_count": 0,
                "direction_counts": {"ground-station-to-zone": len(episodes)},
                "path_length_class_counts": {"short": len(episodes)},
                "selection_reason_counts": {
                    "persistent-stable-control": len(episodes)
                },
                "estimated_echo_requests": 100,
                "estimated_telemetry_events": 200,
            },
            "probe_identities": [
                _identity(suffix) for suffix in (*suffixes, "future")
            ],
            "episodes": episodes,
        },
        "execution": execution,
        "runner_events": [
            {
                "event": "automatic_run_start",
                "wall_origin": ORIGIN_SECONDS,
                "nominal_origin": START.isoformat(),
            },
            {
                "event": "activation_end",
                "generation_publication_occurred": True,
                "active_frontier": (START + timedelta(seconds=10)).isoformat(),
                "main_seconds": 0.04,
                "ideal_start_lag_seconds": 0.02,
                "ideal_completion_lag_seconds": 0.06,
                "runtime_apply_seconds": 0.03,
                "activation_switch_seconds": 0.01,
                "foundation_validation_succeeded": True,
                "foundation_validation_timing_complete": True,
                "foundation_validation_timing_status": "complete",
                "foundation_validation_seconds": 0.005,
                "foundation_validation_included_in_runtime_apply": True,
                "generation_switch_start_monotonic_ns": ORIGIN_NS
                + 5_000_000_000,
                "generation_switch_end_monotonic_ns": ORIGIN_NS
                + 5_010_000_000,
            },
            {
                "event": "ready_published",
                "preparation_seconds": 0.2,
            },
            {
                "event": "boundary_scheduled",
                "actual_readiness_wait_seconds": 0.02,
                "ready_publication_lag_seconds": 0.01,
            },
            {
                "event": "resource_health_checkpoint",
                "runner_rss_bytes": 123_456,
            },
        ],
    }


def test_qualification_classifies_only_active_published_runtime_evolution():
    result = _project_probe_qualification(
        _qualification(),
        ping_transactions=(),
        telemetry_complete=True,
        telemetry_losses=0,
    )
    assert result is not None
    by_id = {value["episode_id"]: value for value in result["episodes"]}

    assert by_id["episode-isolated"]["classifications"] == ["isolated-loss"]
    assert by_id["episode-activation"]["classifications"] == [
        "transient-disruption"
    ]
    assert by_id["episode-blackhole"]["classifications"] == [
        "persistent-blackhole"
    ]
    assert by_id["episode-unreachable"]["classifications"] == [
        "expected-unreachable"
    ]
    assert by_id["episode-future"]["classifications"] == [
        "not-executed-outside-active-horizon"
    ]
    assert by_id["episode-future"]["active_published_overlap"] is False
    assert by_id["episode-future"]["rearmable"] is True
    assert result["summary"]["status"] == "FAIL"
    assert result["summary"]["active_frontier"] == (
        START + timedelta(seconds=10)
    ).isoformat()
    assert result["runner_observer_effect"]["runner_rss_max_bytes"] == 123_456
    assert result["runner_observer_effect"]["main_seconds"] == {
        "count": 1,
        "p50": 0.04,
        "p95": 0.04,
        "max": 0.04,
    }
    assert result["runner_observer_effect"]["start_lag_seconds"]["max"] == 0.02
    assert result["runner_observer_effect"]["completion_lag_seconds"]["max"] == 0.06



def test_current_activation_missing_optional_main_timing_remains_unavailable():
    qualification = _qualification()
    activation = next(
        item
        for item in qualification["runner_events"]
        if item.get("event") == "activation_end"
    )
    activation.pop("main_seconds")
    activation["runtime_apply_seconds"] = 0.03
    activation["activation_switch_seconds"] = 0.01
    activation["foundation_validation_seconds"] = None
    activation["foundation_validation_timing_complete"] = False
    activation["foundation_validation_timing_status"] = "missing"

    result = _project_probe_qualification(
        qualification,
        ping_transactions=(),
        telemetry_complete=True,
        telemetry_losses=0,
    )

    assert result is not None
    assert result["runner_observer_effect"]["main_seconds"] == {
        "count": 0,
        "p50": None,
        "p95": None,
        "max": None,
    }
    assert result["runner_observer_effect"]["main_seconds"]["max"] != 0.0


def test_current_activation_unknown_main_timing_is_not_substituted() -> None:
    qualification = _qualification()
    activation = next(
        item
        for item in qualification["runner_events"]
        if item.get("event") == "activation_end"
    )
    activation["main_seconds"] = "unknown"
    activation["runtime_apply_seconds"] = 0.03
    activation["activation_switch_seconds"] = 0.01

    result = _project_probe_qualification(
        qualification,
        ping_transactions=(),
        telemetry_complete=True,
        telemetry_losses=0,
    )

    assert result is not None
    assert result["runner_observer_effect"]["main_seconds"] == {
        "count": 0,
        "p50": None,
        "p95": None,
        "max": None,
    }


def test_incomplete_telemetry_is_an_infrastructure_failure_not_network_truth():
    result = _project_probe_qualification(
        _qualification(),
        ping_transactions=(),
        telemetry_complete=False,
        telemetry_losses=3,
    )
    assert result is not None

    assert result["summary"]["status"] == "FAIL"
    assert result["summary"]["global_evidence_complete"] is False
    assert result["summary"]["infrastructure_failure_count"] == 4
    future = next(
        value
        for value in result["episodes"]
        if value["episode_id"] == "episode-future"
    )
    assert "probe-infrastructure-or-evidence-failure" not in future[
        "classifications"
    ]
