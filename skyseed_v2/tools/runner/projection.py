"""Project detached Runner facts into a curated post-run inspection view."""

from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from numbers import Real
from math import isfinite
from typing import Any, Mapping


_PRIMARY_EVENTS = {
    "preparation_start": ("Preparation", "Preparation started"),
    "preparation_end": ("Preparation", "Initial preparation completed"),
    "runtime_foundation_ready": ("Preparation", "Runtime foundation ready"),
    "automatic_run_start": ("Main", "Automatic execution started"),
    "automatic_run_end": ("Main", "Automatic execution completed"),
    "boundary_scheduled": ("Main", "Nominal boundary scheduled"),
    "activation_end": ("Main", "Main activation completed"),
    "capacity_audit_failure": ("Failure", "Capacity audit failed"),
    "failure": ("Failure", "Runner failure recorded"),
    "runtime_cleanup_end": ("Cleanup", "Dataplane cleanup completed"),
    "cleanup_end": ("Cleanup", "Runner cleanup completed"),
    "batch_appended": ("Maintenance", "Sealed batch registered"),
    "batch_load_start": ("Maintenance", "Background batch loading started"),
    "batch_ready": ("Maintenance", "Batch ready before activation"),
    "batch_activation_start": ("Main", "Delivery seam activation started"),
    "batch_plan_reclaimed": ("Maintenance", "Consumed batch plans reclaimed"),
    "generation_reclaim": ("Maintenance", "Quiescent generations reclaimed"),
    "batch_exhausted": ("Failure", "Delivery deadline exhausted"),
}


def project_runner_inspection(
    runner_snapshot: Mapping[str, object],
    runtime_outcome: Mapping[str, object],
    *,
    deployment_startup: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    """Build view-only indexes from detached Runner and invocation facts."""

    snapshot = deepcopy(dict(runner_snapshot))
    outcome = deepcopy(dict(runtime_outcome))
    deployment_startup = deepcopy(deployment_startup)
    if snapshot.get("format_version") != 2:
        raise ValueError("unsupported Runner inspection snapshot format")
    runner = _mapping(snapshot.get("runner"), "runner")
    runtime_foundation = dict(_mapping(runner.get("runtime_foundation", {}), "runtime foundation"))
    binding_values = runtime_foundation.get("bindings", ())
    if not isinstance(binding_values, (tuple, list)):
        raise TypeError("runtime foundation bindings must be a sequence")
    runtime_foundation["bindings"] = tuple(
        dict(_mapping(value, "runtime binding"))
        for value in binding_values
    )
    events_value = snapshot.get("events")
    if not isinstance(events_value, (tuple, list)):
        raise TypeError("Runner inspection events must be a sequence")
    events = tuple(
        dict(_mapping(value, "event")) for value in events_value
    )
    event_history = snapshot.get("event_history", {})
    if outcome.get("status") not in {
        "completed",
        "interrupted",
        "failed",
        "cleanup-failed",
    }:
        raise ValueError("runtime outcome has an unsupported status")

    ordered = tuple(
        value
        for _, value in sorted(
            enumerate(events),
            key=lambda item: (_monotonic(item[1]), item[0]),
        )
    )
    timeline = _timeline(ordered)
    timing = _timing(ordered)
    failures = tuple(
        _event_details(value)
        for value in ordered
        if value.get("event") == "failure"
    )
    capacity_failures = tuple(
        _event_details(value)
        for value in ordered
        if value.get("event") == "capacity_audit_failure"
    )
    runtime_cleanup = _latest(ordered, "runtime_cleanup_end")
    cleanup_end = _latest(ordered, "cleanup_end")

    overview = {
        "outcome": outcome["status"],
        "exit_code": outcome.get("exit_code"),
        "lifecycle_state": runner.get("lifecycle_state"),
        "execution_mode": runner.get("execution_mode"),
        "frontiers": runner.get("frontiers", {}),
        "execution_contract": runner.get("execution_contract"),
        "artifact": runner.get("artifact", {}),
        "known_boundary_count": runner.get("working_set", {}).get("artifact_boundary_count", runner.get("artifact", {}).get("boundary_count")),
        "activation_count": sum(value["completed"] for value in timing["activations"]),
        "completed_boundary_count": runner.get("activation_count"),
        "prepared_boundary_count": runner.get("prepared_boundary_count"),
        "preparation_duration_seconds": timing["preparation_duration_seconds"],
        "cleanup_status": _mapping(
            outcome.get("cleanup", {}), "runtime cleanup outcome"
        ).get("status"),
        "cleanup_duration_seconds": timing["cleanup_duration_seconds"],
        "primary_error": outcome.get("primary_error"),
        "delivery": runner.get("delivery", {}),
    }
    scale_safety = _scale_safety(ordered, event_history)
    scale_safety["deployment_startup"] = (
        None if deployment_startup is None else dict(deployment_startup)
    )

    data = {
        "format_version": 1,
        "authority": {
            "status": "non-authoritative-view",
            "runner": (
                "detached BpfRunner lifecycle summary and supplied durable/recent "
                "event evidence"
            ),
            "dataplane": "evidence already returned during normal Runner lifecycle",
            "kernel": "not inspected by this Tool",
            "traffic": "not observed or generated by this Tool",
        },
        "runtime_outcome": outcome,
        "overview": overview,
        "delivery": {
            "session": runner.get("delivery", {}),
            "batches": tuple({"event": value["event"], **_event_details(value)} for value in ordered if value.get("event") in
                             {"batch_appended", "batch_load_start", "batch_ready", "batch_activation_start", "batch_exhausted"}),
            "reclamation": {"completed_generations": sum(_recorded_sequence_count(value.get("reclaimed_generations", ())) for value in ordered if value.get("event") == "generation_reclaim"),
                "deleted_map_entries": sum(value.get("deleted_entries", 0) for value in ordered if value.get("event") == "generation_reclaim"),
                "work_units": sum(value.get("work_units", 0) for value in ordered if value.get("event") == "generation_reclaim"),
                "released_plan_boundaries": sum(value.get("released_boundaries", 0) for value in ordered if value.get("event") == "batch_plan_reclaimed")},
            "history": "Immutable generation/domain/Traffic evidence remains available after runtime reclamation.",
        },
        "runtime_foundation": runtime_foundation,
        "timeline": timeline,
        "graphical_timeline": _graphical_timeline(timeline),
        "timing": timing,
        "detailed_timing": _detailed_timing(ordered),
        "scale_safety": scale_safety,
        "default_dataplane": _default_dataplane_evidence(ordered),
        "failures_cleanup": {
            "primary_error": outcome.get("primary_error"),
            "cleanup": outcome.get("cleanup"),
            "runner_failures": failures,
            "capacity_failures": capacity_failures,
            "runtime_cleanup_evidence": (
                None
                if runtime_cleanup is None
                else _event_details(runtime_cleanup)
            ),
            "cleanup_end": (
                None if cleanup_end is None else _event_details(cleanup_end)
            ),
        },
        "raw": {
            "runner": runner,
            "event_history": event_history,
            "events": {
                "embedded": False,
                "count": len(ordered),
                "exact_source_path": (
                    event_history.get("exact_source_path")
                    if isinstance(event_history, Mapping)
                    else None
                ),
                "note": (
                    "Exact events remain in the durable JSONL source; this page "
                    "does not duplicate the complete raw history."
                ),
            },
        },
    }
    data["inventory"] = {
        "events": len(ordered),
        "timeline_events": len(timeline),
        "activations": len(timing["activations"]),
        "runner_failures": len(failures),
        "capacity_failures": len(capacity_failures),
    }
    data["detailed_timing"]["cleanup"]["finalization"] = runner.get("cleanup_finalization")
    return data


def _scale_safety(
    events: tuple[dict[str, object], ...],
    event_history: object,
) -> dict[str, object]:
    preparation_stages = tuple(
        _event_details(value)
        for value in events
        if value.get("event") == "preparation_stage"
    )
    amplification = []
    worker_limits = []
    worker_high_waters = []
    parallel_phases = 0
    parallel_tasks = 0
    batch_sizes = []
    batch_count = 0
    batch_minimums = []
    batch_maximums = []
    batch_distribution: dict[str, int] = {}
    observed_counters = set()
    external_processes = 0
    namespace_processes = 0
    bpf_syscalls = 0
    for value in events:
        metrics = value.get("execution_metrics")
        if not isinstance(metrics, Mapping):
            continue
        worker = metrics.get("worker_limit")
        if isinstance(worker, Real):
            worker_limits.append(int(worker))
        worker_high_water = metrics.get("worker_high_water")
        if isinstance(worker_high_water, Real):
            worker_high_waters.append(int(worker_high_water))
        parallel_phases += _integer(metrics.get("parallel_phase_count"))
        parallel_tasks += _integer(metrics.get("parallel_task_count"))
        sizes = metrics.get("batch_sizes")
        if isinstance(sizes, (tuple, list)):
            observed_sizes = [
                int(size) for size in sizes if isinstance(size, Real)
            ]
            batch_sizes.extend(observed_sizes)
            batch_count += len(observed_sizes)
            if observed_sizes:
                batch_minimums.append(min(observed_sizes))
                batch_maximums.append(max(observed_sizes))
        else:
            batch_count += _integer(metrics.get("batch_count"))
            minimum = metrics.get("batch_size_min")
            maximum = metrics.get("batch_size_max")
            if isinstance(minimum, Real) and int(minimum) > 0:
                batch_minimums.append(int(minimum))
            if isinstance(maximum, Real) and int(maximum) > 0:
                batch_maximums.append(int(maximum))
        distribution = metrics.get("batch_size_distribution")
        if isinstance(distribution, Mapping):
            for label, count in distribution.items():
                if isinstance(count, Real):
                    batch_distribution[str(label)] = (
                        batch_distribution.get(str(label), 0) + int(count)
                    )
        observed_counters.update(key for key, value in metrics.items() if isinstance(value, Real))
        external_processes += _integer(metrics.get("external_processes"))
        namespace_processes += _integer(metrics.get("namespace_processes"))
        bpf_syscalls += _integer(metrics.get("bpf_syscalls"))
        if value.get("event") in {
            "preparation_stage",
            "activation_end",
            "runtime_cleanup_end",
        }:
            amplification.append(
                {
                    "event": value.get("event"),
                    "boundary_timestamp": value.get("boundary_timestamp"),
                    "logical_operations": _logical_operations(value),
                    "map_update_operations": value.get(
                        "generation_map_update_operations"
                    ),
                    "external_processes": metrics.get("external_processes"),
                    "namespace_processes": metrics.get("namespace_processes"),
                    "bpf_syscalls": metrics.get("bpf_syscalls"),
                    "bpf_map_update_syscalls": metrics.get(
                        "bpf_map_update_syscalls"
                    ),
                    "worker_limit": metrics.get("worker_limit"),
                    "batch_count": metrics.get("batch_count"),
                    "batch_operation_count": metrics.get(
                        "batch_operation_count"
                    ),
                    "batch_size_distribution": metrics.get("batch_size_distribution", {}),
                }
            )
    activation_subphases = tuple(
        {
            "boundary_timestamp": value.get("target_boundary")
            or value.get("active_frontier"),
            "last_completed_active_frontier": value.get(
                "last_completed_active_frontier"
            ),
            "active_frontier": value.get("active_frontier"),
            "lifecycle_state": value.get("lifecycle_state"),
            "main_seconds": value.get("main_seconds"),
            "semantic_outcome": value.get("semantic_outcome"),
            "live_mutation_started": value.get("live_mutation_started"),
            "generation_publication_occurred": value.get(
                "generation_publication_occurred"
            ),
            "target_transition_complete": value.get(
                "target_transition_complete"
            ),
            "live_state_may_be_partial": value.get(
                "live_state_may_be_partial"
            ),
            "failure_phase": value.get("failure_phase"),
            "failure_type": value.get("failure_type"),
            "failure_message": value.get("failure_message"),
            "deleted_connections_complete": value.get(
                "deleted_connections_complete"
            ),
            "candidate_rollback_attempted": value.get(
                "candidate_rollback_attempted"
            ),
            "candidate_rollback_complete": value.get(
                "candidate_rollback_complete"
            ),
            "cleanup_ownership_retained": value.get(
                "cleanup_ownership_retained"
            ),
            "cleanup_required": value.get("cleanup_required"),
            "cleanup_connections": value.get("cleanup_connections", ()),
            "runtime_apply_seconds": value.get("runtime_apply_seconds"),
            "activation_switch_seconds": value.get(
                "activation_switch_seconds"
            ),
            "generation_switch_start_monotonic_ns": value.get(
                "generation_switch_start_monotonic_ns"
            ),
            "generation_switch_end_monotonic_ns": value.get(
                "generation_switch_end_monotonic_ns"
            ),
            "retirement_seconds": value.get("retirement_seconds"),
            "bookkeeping_seconds": value.get("bookkeeping_seconds"),
            "observation_capture_seconds": value.get(
                "observation_capture_seconds"
            ),
            "grouping": value.get("grouping"),
            "activation_phases": value.get("activation_phases"),
            "activation_phase_reconciliation": value.get("activation_phase_reconciliation"),
            "parent_main_tail": value.get("parent_main_tail"),
            "parent_helper_envelope": value.get("parent_helper_envelope"),
        }
        for value in events
        if (value.get("event") == "activation_end" and value.get("preparation") is not True)
        or (
            value.get("event") == "failure"
            and value.get("phase") == "activation"
        )
    )
    history = event_history if isinstance(event_history, Mapping) else {}
    durable = history.get("durable")
    durable_status = durable if isinstance(durable, Mapping) else None
    evidence_complete = (
        True
        if not history.get("durable_history_enabled")
        else durable_status is None or durable_status.get("complete") is True
    )
    return {
        "preparation_breakdown": preparation_stages,
        "main_subphases": activation_subphases,
        "amplification": tuple(amplification),
        "batching": {
            "observed_batch_count": batch_count,
            "batch_sizes": tuple(batch_sizes),
            "batch_size_distribution": batch_distribution,
            "minimum_batch_size": min(batch_minimums, default=None),
            "maximum_batch_size": max(batch_maximums, default=None),
            "external_processes": external_processes if "external_processes" in observed_counters else None,
            "namespace_processes": namespace_processes if "namespace_processes" in observed_counters else None,
            "bpf_syscalls": bpf_syscalls if "bpf_syscalls" in observed_counters else None,
        },
        "workers": {
            "observed_limits": tuple(worker_limits),
            "maximum_observed_limit": max(worker_limits, default=None),
            "observed_high_water": tuple(worker_high_waters),
            "maximum_observed_high_water": max(
                worker_high_waters, default=None
            ),
            "parallel_phase_count": parallel_phases,
            "parallel_task_count": parallel_tasks,
        },
        "resource_health": tuple(
            _event_details(value)
            for value in events
            if value.get("event") == "resource_health_checkpoint"
        ),
        "observation": {
            "complete": evidence_complete,
            "durable": durable_status,
        },
        "teardown": {
            "runtime": (
                None
                if (value := _latest(events, "runtime_cleanup_end")) is None
                else _event_details(value)
            ),
            "runner": (
                None
                if (value := _latest(events, "cleanup_end")) is None
                else _event_details(value)
            ),
        },
    }


def _integer(value: object) -> int:
    return int(value) if isinstance(value, Real) else 0


def _logical_operations(event: Mapping[str, object]) -> object:
    grouping = event.get("grouping")
    if isinstance(grouping, Mapping):
        return grouping.get("grouped_operation_count")
    active = event.get("target_active_interface_entries")
    routes = event.get("target_route_entries")
    if isinstance(active, Real) and isinstance(routes, Real):
        return int(active) + int(routes)
    return None


def _recorded_sequence_count(value):
    # Large event sequences retain their original count instead of inline IDs.
    return value["item_count"] if isinstance(value, Mapping) else len(value)


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be a detached mapping")
    return value


def _monotonic(event: Mapping[str, object]) -> float:
    value = event.get("monotonic_seconds")
    return float(value) if isinstance(value, Real) else float("inf")


def _event_details(event: Mapping[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in event.items()
        if key not in {"event", "monotonic_seconds", "wall_time_seconds"}
    }


def _latest(
    events: tuple[dict[str, object], ...], event_name: str
) -> dict[str, object] | None:
    return next(
        (value for value in reversed(events) if value.get("event") == event_name),
        None,
    )


def _timeline(events: tuple[dict[str, object], ...]) -> tuple[dict[str, object], ...]:
    result = []
    first_monotonic = (
        _monotonic(events[0]) if events and _monotonic(events[0]) != float("inf") else None
    )
    for event in events:
        event_name = str(event.get("event", "unknown"))
        boundary = event.get("boundary_timestamp")
        if event_name not in _PRIMARY_EVENTS:
            continue
        category, headline = _PRIMARY_EVENTS[event_name]
        if event.get("preparation") is True:
            category = "Preparation"
            if event_name == "activation_end":
                headline = "Startup boundary activated"
        monotonic_value = _monotonic(event)
        result.append(
            {
                "timeline_index": len(result),
                "event": event_name,
                "category": category,
                "headline": headline,
                "monotonic_seconds": (
                    None if monotonic_value == float("inf") else monotonic_value
                ),
                "elapsed_seconds": (
                    None
                    if first_monotonic is None or monotonic_value == float("inf")
                    else monotonic_value - first_monotonic
                ),
                "wall_time_seconds": event.get("wall_time_seconds"),
                "boundary_timestamp": boundary,
                "summary": _event_summary(event_name, event, boundary),
                "details": _event_details(event),
            }
        )
    return tuple(result)


def _graphical_timeline(
    timeline: tuple[dict[str, object], ...],
) -> dict[str, object]:
    """Project lifecycle phases and duration work with attached event points."""

    lifecycle: dict[str, list[dict[str, object]]] = {
        "preparation_phase": [],
        "automatic_run_phase": [],
        "cleanup_phase": [],
    }
    work: dict[str, list[dict[str, object]]] = {
        "main_activation": [],
    }
    elapsed_values = [
        float(value["elapsed_seconds"])
        for value in timeline
        if isinstance(value.get("elapsed_seconds"), Real)
    ]
    last_event = max(
        (
            value
            for value in timeline
            if isinstance(value.get("elapsed_seconds"), Real)
        ),
        key=lambda value: float(value["elapsed_seconds"]),
        default=None,
    )

    preparation_start = _first_timeline_event(timeline, "preparation_start")
    preparation_end = _first_timeline_event(timeline, "preparation_end")
    if preparation_start is not None:
        preparation_phase = _graph_span_item(
            "preparation_phase",
            preparation_start,
            preparation_end or last_event or preparation_start,
            visual="phase",
            completed=preparation_end is not None,
            target_event=preparation_start,
        )
        foundation_ready = _first_timeline_event(
            timeline, "runtime_foundation_ready"
        )
        if foundation_ready is not None:
            preparation_phase["foundation_marker"] = _graph_point(
                "runtime_foundation_ready", foundation_ready
            )
        lifecycle["preparation_phase"].append(preparation_phase)

    automatic_start = _first_timeline_event(timeline, "automatic_run_start")
    automatic_end = _first_timeline_event(timeline, "automatic_run_end")
    cleanup_end = _first_timeline_event(timeline, "cleanup_end")
    if automatic_start is not None:
        automatic_phase = _graph_span_item(
            "automatic_run_phase",
            automatic_start,
            automatic_end or last_event or automatic_start,
            visual="phase",
            completed=automatic_end is not None,
            target_event=automatic_start,
        )
        if automatic_end is None and cleanup_end is not None:
            cleanup_details = _mapping(
                cleanup_end.get("details", {}), "cleanup timeline details"
            )
            cleanup_duration = cleanup_details.get("duration_seconds")
            if isinstance(cleanup_duration, Real):
                automatic_phase["elapsed_end_seconds"] = max(
                    float(automatic_phase["elapsed_start_seconds"]),
                    float(cleanup_end["elapsed_seconds"])
                    - float(cleanup_duration),
                )
        lifecycle["automatic_run_phase"].append(automatic_phase)

    if cleanup_end is not None:
        cleanup_details = _mapping(
            cleanup_end.get("details", {}), "cleanup timeline details"
        )
        lifecycle["cleanup_phase"].append(
            _graph_span_item(
                "cleanup_phase",
                None,
                cleanup_end,
                visual="phase",
                fallback_duration=cleanup_details.get("duration_seconds"),
                completed=True,
                target_event=cleanup_end,
            )
        )

    for value in timeline:
        if not isinstance(value.get("elapsed_seconds"), Real):
            continue
        event_name = value.get("event")
        details = _mapping(value.get("details", {}), "timeline details")
        if event_name == "batch_ready":
            start = next((event for event in timeline if event.get("event") == "batch_load_start"
                          and event["details"].get("batch_index") == details.get("batch_index")), None)
            work.setdefault("batch_loading", []).append(_graph_span_item(
                "batch_loading", start, value, visual="work", fallback_duration=details.get("total_seconds")))
        elif event_name in {"generation_reclaim", "batch_plan_reclaimed"}:
            work.setdefault("maintenance_reclaim", []).append(_graph_span_item(
                "maintenance_reclaim", None, value, visual="work", fallback_duration=details.get("duration_seconds")))
        if event_name == "activation_end" and details.get("preparation") is not True:
            work["main_activation"].append(
                _graph_span_item(
                    "main_activation",
                    None,
                    value,
                    visual="work",
                    fallback_duration=details.get("main_seconds"),
                )
            )
    groups = (
        ("lifecycle", lifecycle),
        ("work", work),
    )
    for _, rows in groups:
        for items in rows.values():
            items.sort(key=_graph_item_key)

    return {
        "time_basis": "elapsed monotonic seconds from first recorded event",
        "duration_seconds": max(elapsed_values, default=0.0),
        "groups": tuple(
            {
                "group": group,
                "rows": tuple(
                    {"row": row, "items": tuple(items)}
                    for row, items in rows.items()
                ),
            }
            for group, rows in groups
        ),
    }


def _first_timeline_event(
    timeline: tuple[dict[str, object], ...], event_name: str
) -> dict[str, object] | None:
    return next(
        (value for value in timeline if value.get("event") == event_name),
        None,
    )


def _graph_point(kind: str, event: Mapping[str, object]) -> dict[str, object]:
    details = _mapping(event.get("details", {}), "timeline details")
    return {
        "kind": kind,
        "elapsed_seconds": float(event["elapsed_seconds"]),
        "boundary_timestamp": event.get("boundary_timestamp"),
        "target_timeline_index": event.get("timeline_index"),
        "generation": details.get("generation"),
    }


def _graph_span_item(
    kind: str,
    start_event: Mapping[str, object] | None,
    end_event: Mapping[str, object],
    *,
    visual: str,
    fallback_duration: object = None,
    completed: bool = True,
    target_event: Mapping[str, object] | None = None,
) -> dict[str, object]:
    end = float(end_event["elapsed_seconds"])
    if start_event is not None and isinstance(
        start_event.get("elapsed_seconds"), Real
    ):
        start = float(start_event["elapsed_seconds"])
    elif isinstance(fallback_duration, Real):
        start = max(0.0, end - float(fallback_duration))
    else:
        start = end
    details = _mapping(end_event.get("details", {}), "timeline details")
    return {
        "kind": kind,
        "visual": visual,
        "elapsed_start_seconds": start,
        "elapsed_end_seconds": end,
        "boundary_timestamp": end_event.get("boundary_timestamp")
        or (None if start_event is None else start_event.get("boundary_timestamp")),
        "target_timeline_index": (target_event or end_event).get(
            "timeline_index"
        ),
        "generation": details.get("generation"),
        "completed": completed,
    }


def _graph_item_key(item: Mapping[str, object]) -> tuple[float, float]:
    return (
        float(item["elapsed_start_seconds"]),
        float(item["elapsed_end_seconds"]),
    )


def _event_summary(
    event_name: str,
    event: Mapping[str, object],
    boundary: object,
) -> str:
    if event_name == "boundary_scheduled":
        return f"Boundary {boundary or 'unknown'}"
    if event_name == "activation_end":
        duration = event.get("main_seconds")
        suffix = "" if duration is None else f"; duration {_seconds(duration)}"
        return f"Boundary {boundary or 'unknown'}{suffix}"
    if event_name == "failure":
        return (
            f"{event.get('phase') or 'unknown phase'}: "
            f"{event.get('error_type') or 'Error'}: {event.get('error') or ''}"
        )
    if event_name == "capacity_audit_failure":
        return f"{event.get('scope') or 'unknown scope'}: {event.get('reason') or ''}"
    if event_name in {"preparation_end", "cleanup_end"}:
        return f"Duration {_seconds(event.get('duration_seconds'))}"
    if event_name == "automatic_run_end":
        return f"Last active frontier {event.get('active_frontier') or 'none'}"
    return "Recorded Runner lifecycle evidence"


def _seconds(value: object) -> str:
    return "not recorded" if not isinstance(value, Real) else f"{float(value):.3f}s"


def _timing(events: tuple[dict[str, object], ...]) -> dict[str, object]:
    preparation_end = _latest(events, "preparation_end")
    cleanup_end = _latest(events, "cleanup_end")
    schedules = {
        value.get("boundary_timestamp"): value
        for value in events
        if value.get("event") == "boundary_scheduled" and value.get("preparation") is not True
    }
    activations = []
    completed_boundaries = set()
    for event in events:
        if event.get("event") != "activation_end" or event.get("preparation") is True:
            continue
        boundary = event.get("boundary_timestamp") or event.get("active_frontier")
        schedule = schedules.get(boundary, {})
        completed_boundaries.add(boundary)
        activations.append(
            {
                "boundary_timestamp": boundary,
                "start_lag_seconds": event.get("ideal_start_lag_seconds"),
                "duration_seconds": event.get("main_seconds"),
                "completion_lag_seconds": event.get(
                    "ideal_completion_lag_seconds"
                ),
                "completed": True,
                "active_frontier": event.get("active_frontier"),
            }
        )
    activations.extend(
        {
            "boundary_timestamp": boundary,
            "start_lag_seconds": None,
            "duration_seconds": None,
            "completion_lag_seconds": None,
            "completed": False,
            "active_frontier": None,
        }
        for boundary, schedule in schedules.items()
        if boundary not in completed_boundaries
    )

    complete_durations = [
        float(value["duration_seconds"])
        for value in activations
        if isinstance(value.get("duration_seconds"), Real)
    ]
    return {
        "preparation_duration_seconds": (
            None if preparation_end is None else preparation_end.get("duration_seconds")
        ),
        "cleanup_duration_seconds": (
            None if cleanup_end is None else cleanup_end.get("duration_seconds")
        ),
        "activations": tuple(activations),
        "startup_activations": tuple(dict(value) for value in events
                                     if value.get("event") == "activation_end"
                                     and value.get("preparation") is True),
        "recent_activation_duration_seconds": (
            None if not complete_durations else complete_durations[-1]
        ),
        "max_activation_duration_seconds": (
            None if not complete_durations else max(complete_durations)
        ),

    }


_OPERATION_CATEGORIES = (
    "pre_veth_creation", "pre_ifindex_resolution", "pre_mac_link_configuration",
    "pre_qdisc_creation", "pre_existing_qdisc_update", "pre_bpf_attachment",
    "pre_ingress_binding_update", "pre_egress_binding_update", "pre_route_replacement",
    "pre_neighbour_replacement", "pre_zone_address_addition", "pre_link_up",
    "pre_local_bookkeeping", "active_generation_update", "post_route_deletion",
    "post_neighbour_deletion", "post_zone_address_removal", "post_ingress_binding_deletion",
    "post_egress_binding_deletion", "post_interface_down", "post_veth_deletion",
    "post_local_bookkeeping",
)


def _timing_distribution(values) -> dict[str, Any]:
    ordered = sorted(float(value) for value in values
                     if isinstance(value, Real) and not isinstance(value, bool) and isfinite(value))
    def percentile(fraction):
        if not ordered:
            return None
        position = (len(ordered) - 1) * fraction
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
    return {"count": len(ordered), "total": sum(ordered) if ordered else None,
            "p50": percentile(.5), "p95": percentile(.95),
            "min": min(ordered) if ordered else None, "max": max(ordered) if ordered else None}


def _difference(total, *parts):
    if any(not isinstance(value, Real) or isinstance(value, bool) or not isfinite(value)
           for value in (total, *parts)):
        return None
    return total - sum(parts)


def _span_seconds(end, start):
    value = _difference(end, start)
    return None if value is None else value / 1e9


def _detailed_timing(events) -> dict[str, Any]:
    """Offline accounting only: nested timings are never added as exclusive work."""
    schedules = {v.get("boundary_timestamp"): v for v in events if v.get("event") == "boundary_scheduled"}
    observations = {v.get("boundary_timestamp"): v for v in events if v.get("event") == "activation_observation_end"}
    rows = []
    startup = []
    previous_anchor_lag = None
    for event in events:
        if event.get("event") != "activation_end":
            continue
        boundary = event.get("boundary_timestamp") or event.get("active_frontier")
        schedule = schedules.get(boundary, {})
        observation = observations.get(boundary, {})
        command = event.get("command_timing") or {}
        helper = command.get("helper") or {}
        parent = event.get("parent_runtime_timing") or {}
        operations = event.get("operation_timings") or {}
        phases = event.get("activation_phases") or {}
        envelope = {name: command.get(name) for name in (
            "command_seconds", "json_encode_seconds", "channel_write_seconds",
            "channel_wait_seconds", "response_decode_seconds")}
        envelope.update({
            "parent_request_preparation_seconds": parent.get("request_preparation_seconds"),
            "parent_result_ledger_seconds": parent.get("result_ledger_seconds"),
            "helper_request_decode_seconds": helper.get("request_decode_seconds"),
            "helper_dispatch_seconds": helper.get("dispatch_seconds"),
            "helper_activation_seconds": _span_seconds(event.get("helper_activation_evidence_monotonic_ns"), event.get("helper_activation_start_monotonic_ns")),
            "helper_pre_dispatch_seconds": _span_seconds(helper.get("dispatch_start_monotonic_ns"), helper.get("request_decoded_monotonic_ns")),
            **{name: observation.get(name) for name in (
                "formal_commit_seconds", "identity_enqueue_seconds", "activation_event_record_seconds",
                "post_main_observation_seconds")},
        })
        residuals = {
            "main_unattributed_seconds": _difference(event.get("main_seconds"),
                parent.get("request_preparation_seconds"), command.get("command_seconds"), parent.get("result_ledger_seconds")),
            "command_unattributed_seconds": _difference(command.get("command_seconds"),
                *[command.get(name) for name in ("json_encode_seconds", "channel_write_seconds", "channel_wait_seconds", "response_decode_seconds")]),
            "exchange_unattributed_seconds": _difference(
                _span_seconds(command.get("read_end_monotonic_ns"), command.get("write_start_monotonic_ns")),
                helper.get("request_decode_seconds"), envelope["helper_pre_dispatch_seconds"], helper.get("dispatch_seconds")),
            "helper_dispatch_unattributed_seconds": _difference(helper.get("dispatch_seconds"), envelope["helper_activation_seconds"]),
            "post_main_unattributed_seconds": _difference(observation.get("post_main_observation_seconds"),
                observation.get("formal_commit_seconds"), observation.get("identity_enqueue_seconds"), observation.get("activation_event_record_seconds")),
        }
        for prefix, phase in (("pre", "pre_publication"), ("post", "post_publication")):
            residuals[prefix + "_barrier_unattributed_seconds"] = (
                _difference((phases.get(phase) or {}).get("duration_seconds"),
                            sum(value["duration_seconds"] for name, value in operations.items() if name.startswith(prefix + "_")))
                if event.get("detailed_timing_version") in (1, 2) else None)
        residuals["helper_activation_outside_barriers_seconds"] = _difference(
            envelope["helper_activation_seconds"],
            (phases.get("pre_publication") or {}).get("duration_seconds"),
            (phases.get("post_publication") or {}).get("duration_seconds"),
            (phases.get("active_generation_publication") or {}).get("duration_seconds", 0)
            if event.get("generation_publication_occurred") is False else
            (phases.get("active_generation_publication") or {}).get("duration_seconds"))
        anchor_ns = event.get("helper_pacing_anchor_monotonic_ns")
        anchor = anchor_ns / 1e9 if isinstance(anchor_ns, int) else None
        ideal = schedule.get("ideal_activation_monotonic_seconds")
        paced = schedule.get("paced_not_before_monotonic_seconds")
        start = event.get("main_start_monotonic_seconds")
        pacing = {
            "start_lag_seconds": event.get("ideal_start_lag_seconds"),
            "completion_lag_seconds": event.get("ideal_completion_lag_seconds"),
            "pacing_carry_seconds": _difference(paced, ideal),
            "start_after_deadline_seconds": _difference(start, paced),
            "parent_to_helper_anchor_seconds": _difference(anchor, start),
            "anchor_lag_seconds": _difference(anchor, ideal),
        }
        pacing["carry_from_previous_anchor_residual_seconds"] = (
            _difference(pacing["pacing_carry_seconds"], max(0, previous_anchor_lag))
            if previous_anchor_lag is not None and schedule.get("pacing_mode") != "fixed-origin" else None)
        row = {"boundary_timestamp": boundary, "main_seconds": event.get("main_seconds"),
               "instrumented": event.get("detailed_timing_version") in (1, 2),
               "operations": operations, "barriers": phases, "envelope": envelope,
               "retirement": event.get("retirement"),
               "residuals": residuals, "pacing": pacing,
               "kernel_calls": helper.get("kernel_calls"),
               "timestamps": {"main_start_seconds": start, "main_end_seconds": event.get("main_end_monotonic_seconds"),
                              "ideal_deadline_seconds": ideal, "paced_deadline_seconds": paced,
                              "helper_anchor_ns": anchor_ns,
                              "publication_start_ns": event.get("generation_switch_start_monotonic_ns"),
                              "publication_end_ns": event.get("generation_switch_end_monotonic_ns")}}
        if event.get("preparation") is True:
            startup.append(row)
        else:
            rows.append(row)
            previous_anchor_lag = pacing["anchor_lag_seconds"]
    instrumented = [row for row in rows if row["instrumented"]]
    operation_summary = {}
    for name in _OPERATION_CATEGORIES:
        # A versioned aggregate with no category means no calls. Publication
        # duration is sampled only when a publication was actually attempted.
        values = [row["operations"].get(name, {"duration_seconds": 0, "operation_count": 0}) for row in instrumented]
        operation_summary[name] = {
            "seconds": _timing_distribution(value["duration_seconds"] for value in values
                                            if name != "active_generation_update" or value["operation_count"]),
            "operations_per_boundary": _timing_distribution(value["operation_count"] for value in values),
        }
    def summarize(group):
        return {name: _timing_distribution((row[group] or {}).get(name) for row in rows)
                for name in sorted({key for row in rows for key in (row[group] or {})})}
    stages = [dict(v) for v in events if v.get("event") == "preparation_stage"]
    preparation = _latest(events, "preparation_end") or {}
    cleanup = _latest(events, "cleanup_end") or {}
    runtime_cleanup = _latest(events, "runtime_cleanup_end") or {}
    return {
        "accounting": {
            "operations": "Attempted operation-family wall time; v2 physical deletion is joined batch critical wall time and pair count, with concurrent task durations reported separately; local bookkeeping counts blocks. Zero-call categories have measured absence; reused publication has no duration sample.",
            "inclusive": "command includes helper dispatch; dispatch includes activation; barriers include operation families. Do not add nested durations or phase percentiles.",
            "residuals": "Signed offline differences, including unmeasured local work, transport, scheduling, and instrumentation; no attribution to pure kernel CPU.",
            "observation": "Post-Main observation ends after activation_end recording; the compact activation_observation_end event and final condition notification are outside that span.",
            "counters": "Actual helper netlink request/BPF syscall calls, including joined worker counts and failed attempts. External process counts remain unavailable.",
        },
        "formal_boundary_count": len(rows), "instrumented_boundary_count": len(instrumented),
        "main_seconds": _timing_distribution(row["main_seconds"] for row in rows),
        "main_exceeding_five_seconds": sum(isinstance(row["main_seconds"], Real) and row["main_seconds"] > 5 for row in rows),
        "operations": operation_summary,
        "barriers": {name: _timing_distribution((row["barriers"].get(name) or {}).get("duration_seconds") for row in rows)
                     for name in ("pre_publication", "active_generation_publication", "post_publication", "withdrawal", "physical_deletion")},
        "envelope": summarize("envelope"), "residuals": summarize("residuals"), "pacing": summarize("pacing"),
        "retirement": summarize("retirement"),
        "kernel_calls": summarize("kernel_calls"), "boundaries": rows, "startup": startup,
        "preparation": {"total_seconds": preparation.get("duration_seconds"), "stages": stages,
            "unattributed_seconds": _difference(preparation.get("duration_seconds"),
                sum(v.get("duration_seconds", 0) for v in stages), sum(v["main_seconds"] for v in startup))},
        "cleanup": {"total_seconds": cleanup.get("duration_seconds"), "owner_phases": cleanup.get("phases"),
            "runtime": {key: runtime_cleanup.get(key) for key in (
                "retirement", "cleanup_phases", "cleanup_operations", "parent_cleanup_timing", "command_timing")},
            "owner_unattributed_seconds": _difference(cleanup.get("duration_seconds"), sum((cleanup.get("phases") or {}).values()))
                if cleanup.get("phases") else None},
    }


def _duration_total(
    values: tuple[dict[str, object], ...],
    *,
    field: str = "duration_seconds",
) -> float | None:
    if not values:
        return 0.0
    measured = [
        float(value[field])
        for value in values
        if isinstance(value.get(field), Real)
    ]
    return sum(measured) if len(measured) == len(values) else None


def _optional_duration_total(values: Iterable[object]) -> float | None:
    measured = [float(value) for value in values if isinstance(value, Real)]
    return None if not measured else sum(measured)


def _default_dataplane_evidence(
    events: tuple[dict[str, object], ...],
) -> dict[str, object]:
    foundation = _latest(events, "runtime_foundation_ready")
    capacity = _latest(events, "capacity_audit_end")
    activation = _latest(events, "activation_end")
    return {
        "kind": "default-tc-bpf",
        "label": "Current default tc-BPF dataplane",
        "evidence_source": "recorded normal Runner lifecycle events",
        "fresh_kernel_inspection": False,
        "foundation": None if foundation is None else _event_details(foundation),
        "latest_capacity_audit": (
            None if capacity is None else _event_details(capacity)
        ),
        "latest_activation": (
            None if activation is None else _event_details(activation)
        ),
    }


__all__ = ["project_runner_inspection"]
