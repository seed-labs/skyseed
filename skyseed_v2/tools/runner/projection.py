"""Project detached Runner facts into a curated post-run inspection view."""

from __future__ import annotations

from copy import deepcopy
from numbers import Real
from typing import Any, Mapping


_PRIMARY_EVENTS = {
    "preparation_start": ("Preparation", "Preparation started"),
    "preparation_end": ("Preparation", "Initial preparation completed"),
    "input_advanced": ("Input", "Input frontier advanced"),
    "input_exhausted": ("Input", "Input supply exhausted"),
    "runtime_foundation_ready": ("Preparation", "Runtime foundation ready"),
    "maintenance_prepare_start": ("Maintenance", "Future boundary preparation started"),
    "maintenance_prepare_end": ("Maintenance", "Future boundary preparation completed"),
    "ready_published": ("READY", "READY boundary published"),
    "automatic_run_start": ("Main", "Automatic execution started"),
    "automatic_run_end": ("Main", "Automatic execution completed"),
    "boundary_scheduled": ("Main", "Nominal boundary scheduled"),
    "activation_start": ("Main", "Main activation started"),
    "activation_end": ("Main", "Main activation completed"),
    "maintenance_forward_priority": ("Maintenance", "Forward coverage took priority"),
    "maintenance_capacity_pressure": (
        "Maintenance",
        "Generation reclaim enabled forward preparation",
    ),
    "generation_reclaimed": ("Reclaim", "Forwarding generation reclaimed"),
    "resources_reclaimed": ("Reclaim", "Physical resources reclaimed"),
    "capacity_audit_failure": ("Failure", "Capacity audit failed"),
    "deadline_miss": ("Failure", "Nominal deadline missed"),
    "failure": ("Failure", "Runner failure recorded"),
    "cleanup_start": ("Cleanup", "Cleanup started"),
    "runtime_cleanup_end": ("Cleanup", "Dataplane cleanup completed"),
    "cleanup_end": ("Cleanup", "Runner cleanup completed"),
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
    if snapshot.get("format_version") != 1:
        raise ValueError("unsupported Runner inspection snapshot format")
    runner = _mapping(snapshot.get("runner"), "runner")
    runtime_foundation = deepcopy(
        dict(_mapping(runner.get("runtime_foundation", {}), "runtime foundation"))
    )
    binding_values = runtime_foundation.get("bindings", ())
    if not isinstance(binding_values, (tuple, list)):
        raise TypeError("runtime foundation bindings must be a sequence")
    runtime_foundation["bindings"] = tuple(
        deepcopy(dict(_mapping(value, "runtime binding")))
        for value in binding_values
    )
    events_value = snapshot.get("events")
    if not isinstance(events_value, (tuple, list)):
        raise TypeError("Runner inspection events must be a sequence")
    events = tuple(
        deepcopy(dict(_mapping(value, "event"))) for value in events_value
    )
    event_history = deepcopy(snapshot.get("event_history", {}))
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
    deadline_misses = tuple(
        _event_details(value)
        for value in ordered
        if value.get("event") == "deadline_miss"
    )
    last_ready = _latest(ordered, "ready_published")
    runtime_cleanup = _latest(ordered, "runtime_cleanup_end")
    cleanup_end = _latest(ordered, "cleanup_end")

    overview = {
        "outcome": outcome["status"],
        "exit_code": outcome.get("exit_code"),
        "lifecycle_state": runner.get("lifecycle_state"),
        "execution_mode": runner.get("execution_mode"),
        "frontiers": deepcopy(runner.get("frontiers", {})),
        "last_ready_published": (
            None if last_ready is None else last_ready.get("boundary_timestamp")
        ),
        "input_exhausted": runner.get("input_exhausted"),
        "preparation_lookahead": deepcopy(
            runner.get("preparation_lookahead", {})
        ),
        "preparation_boundary_depth": runner.get("preparation_boundary_depth"),
        "activation_count": runner.get("activation_count"),
        "ready_count_at_capture": runner.get("ready_count_at_capture"),
        "ready_depth_at_capture": runner.get("ready_depth_at_capture"),
        "ready_limiting_reason": runner.get("ready_limiting_reason"),
        "deadline_misses": len(deadline_misses),
        "preparation_duration_seconds": timing["preparation_duration_seconds"],
        "cleanup_status": _mapping(
            outcome.get("cleanup", {}), "runtime cleanup outcome"
        ).get("status"),
        "cleanup_duration_seconds": timing["cleanup_duration_seconds"],
        "primary_error": deepcopy(outcome.get("primary_error")),
    }
    maintenance_summary = deepcopy(runner.get("maintenance", {}))
    maintenance_summary.update(
        {
            "input_advances": sum(
                value.get("event") == "input_advanced" for value in ordered
            ),
            "ready_publications": sum(
                value.get("event") == "ready_published" for value in ordered
            ),
            "future_preparations": len(timing["maintenance_preparations"]),
            "forward_priority_events": sum(
                value.get("event") == "maintenance_forward_priority"
                for value in ordered
            ),
            "generation_reclaims": len(timing["generation_reclaims"]),
            "physical_reclaims": len(timing["physical_reclaims"]),
        }
    )
    scale_safety = _scale_safety(ordered, event_history)
    scale_safety["deployment_startup"] = (
        None if deployment_startup is None else deepcopy(dict(deployment_startup))
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
        "runtime_foundation": runtime_foundation,
        "timeline": timeline,
        "graphical_timeline": _graphical_timeline(timeline),
        "timing": timing,
        "maintenance": maintenance_summary,
        "scale_safety": scale_safety,
        "default_dataplane": _default_dataplane_evidence(ordered),
        "failures_cleanup": {
            "primary_error": deepcopy(outcome.get("primary_error")),
            "cleanup": deepcopy(outcome.get("cleanup")),
            "runner_failures": failures,
            "capacity_failures": capacity_failures,
            "deadline_misses": deadline_misses,
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
        "ready_publications": maintenance_summary["ready_publications"],
        "runner_failures": len(failures),
        "capacity_failures": len(capacity_failures),
        "deadline_misses": len(deadline_misses),
    }
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
        external_processes += _integer(metrics.get("external_processes"))
        namespace_processes += _integer(metrics.get("namespace_processes"))
        bpf_syscalls += _integer(metrics.get("bpf_syscalls"))
        if value.get("event") in {
            "preparation_stage",
            "maintenance_prepare_end",
            "activation_end",
            "generation_reclaimed",
            "resources_reclaimed",
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
                    "batch_size_distribution": deepcopy(
                        metrics.get("batch_size_distribution", {})
                    ),
                }
            )
    activation_subphases = tuple(
        {
            "boundary_timestamp": value.get("active_frontier"),
            "duration_seconds": value.get("duration_seconds"),
            "readiness_dependency_wait_seconds": value.get(
                "readiness_dependency_wait_seconds"
            ),
            "foundation_validation_seconds": value.get(
                "foundation_validation_seconds"
            ),
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
            "grouping": deepcopy(value.get("grouping")),
        }
        for value in events
        if value.get("event") == "activation_end"
    )
    backlog = tuple(
        {
            "event": value.get("event"),
            "boundary_timestamp": value.get("boundary_timestamp")
            or value.get("active_frontier"),
            **deepcopy(dict(value["maintenance"])),
        }
        for value in events
        if value.get("event") in {"ready_published", "activation_end"}
        and isinstance(value.get("maintenance"), Mapping)
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
            "external_processes": external_processes,
            "namespace_processes": namespace_processes,
            "bpf_syscalls": bpf_syscalls,
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
        "maintenance_backlog": backlog,
        "maintenance_input": tuple(
            _event_details(value)
            for value in events
            if value.get("event") in {"input_advanced", "input_exhausted"}
        ),
        "resource_health": tuple(
            _event_details(value)
            for value in events
            if value.get("event") == "resource_health_checkpoint"
        ),
        "observation": {
            "complete": evidence_complete,
            "durable": deepcopy(durable_status),
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


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be a detached mapping")
    return value


def _monotonic(event: Mapping[str, object]) -> float:
    value = event.get("monotonic_seconds")
    return float(value) if isinstance(value, Real) else float("inf")


def _event_details(event: Mapping[str, object]) -> dict[str, object]:
    return {
        key: deepcopy(value)
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
    current_main_boundary = None
    first_monotonic = (
        _monotonic(events[0]) if events and _monotonic(events[0]) != float("inf") else None
    )
    for event in events:
        event_name = str(event.get("event", "unknown"))
        if event_name == "boundary_scheduled":
            current_main_boundary = event.get("boundary_timestamp")
        boundary = event.get("boundary_timestamp")
        if boundary is None and event_name in {"activation_start", "activation_end"}:
            boundary = current_main_boundary
        if event_name not in _PRIMARY_EVENTS:
            continue
        category, headline = _PRIMARY_EVENTS[event_name]
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
        if event_name == "activation_end":
            current_main_boundary = None
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
        "future_preparation": [],
        "main_activation": [],
        "reclaim_work": [],
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
    cleanup_start = _first_timeline_event(timeline, "cleanup_start")
    if automatic_start is not None:
        lifecycle["automatic_run_phase"].append(
            _graph_span_item(
                "automatic_run_phase",
                automatic_start,
                automatic_end or cleanup_start or last_event or automatic_start,
                visual="phase",
                completed=automatic_end is not None,
                target_event=automatic_start,
            )
        )

    cleanup_end = _first_timeline_event(timeline, "cleanup_end")
    if cleanup_start is not None:
        lifecycle["cleanup_phase"].append(
            _graph_span_item(
                "cleanup_phase",
                cleanup_start,
                cleanup_end or last_event or cleanup_start,
                visual="phase",
                completed=cleanup_end is not None,
                target_event=cleanup_start,
            )
        )

    maintenance_start = None
    activation_start = None
    future_completions: dict[
        tuple[object, object], dict[str, object]
    ] = {}

    for value in timeline:
        if not isinstance(value.get("elapsed_seconds"), Real):
            continue
        event_name = value.get("event")
        details = _mapping(value.get("details", {}), "timeline details")
        if event_name == "maintenance_prepare_start":
            maintenance_start = value
        elif event_name == "maintenance_prepare_end":
            prepared = _graph_span_item(
                "future_preparation",
                maintenance_start,
                value,
                visual="work",
                fallback_duration=details.get("duration_seconds"),
            )
            work["future_preparation"].append(prepared)
            future_completions[
                (value.get("boundary_timestamp"), details.get("generation"))
            ] = prepared
            maintenance_start = None
        elif event_name == "ready_published":
            prepared = future_completions.get(
                (value.get("boundary_timestamp"), details.get("generation"))
            )
            if prepared is not None:
                prepared["ready_marker"] = _graph_point(
                    "ready_published", value
                )
        elif event_name == "activation_start":
            activation_start = value
        elif event_name == "activation_end":
            work["main_activation"].append(
                _graph_span_item(
                    "main_activation",
                    activation_start,
                    value,
                    visual="work",
                    fallback_duration=details.get("duration_seconds"),
                )
            )
            activation_start = None
        elif event_name in {"generation_reclaimed", "resources_reclaimed"}:
            work["reclaim_work"].append(
                _graph_span_item(
                    (
                        "generation_reclaim_work"
                        if event_name == "generation_reclaimed"
                        else "physical_reclaim_work"
                    ),
                    None,
                    value,
                    visual="work",
                    fallback_duration=details.get("duration_seconds"),
                )
            )
    if maintenance_start is not None:
        work["future_preparation"].append(
            _graph_span_item(
                "future_preparation",
                maintenance_start,
                last_event or maintenance_start,
                visual="work",
                completed=False,
                target_event=maintenance_start,
            )
        )
    if activation_start is not None:
        work["main_activation"].append(
            _graph_span_item(
                "main_activation",
                activation_start,
                last_event or activation_start,
                visual="work",
                completed=False,
                target_event=activation_start,
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
    if event_name in {"input_advanced", "input_exhausted"}:
        return f"INPUT {event.get('input_frontier') or 'not yet available'}"
    if event_name in {"maintenance_prepare_start", "maintenance_prepare_end"}:
        duration = event.get("duration_seconds")
        suffix = "" if duration is None else f" in {float(duration):.3f}s"
        return f"Boundary {boundary or 'unknown'}{suffix}"
    if event_name == "ready_published":
        return (
            f"Boundary {boundary or 'unknown'}; predecessor "
            f"{event.get('predecessor') or 'bootstrap'}; headroom "
            f"{_seconds(event.get('preparation_headroom_seconds'))}"
        )
    if event_name in {"boundary_scheduled", "activation_start", "activation_end"}:
        duration = event.get("duration_seconds")
        suffix = "" if duration is None else f"; duration {_seconds(duration)}"
        return f"Boundary {boundary or 'unknown'}{suffix}"
    if event_name == "deadline_miss":
        return f"Boundary {boundary or 'unknown'} was not READY by its due time"
    if event_name == "failure":
        return (
            f"{event.get('phase') or 'unknown phase'}: "
            f"{event.get('error_type') or 'Error'}: {event.get('error') or ''}"
        )
    if event_name == "capacity_audit_failure":
        return f"{event.get('scope') or 'unknown scope'}: {event.get('reason') or ''}"
    if event_name == "generation_reclaimed":
        return (
            f"Generation {event.get('generation', 'unknown')}; "
            f"{_seconds(event.get('duration_seconds'))}"
        )
    if event_name == "resources_reclaimed":
        return (
            f"{event.get('deleted_connections', 0)} connections; "
            f"{_seconds(event.get('duration_seconds'))}"
        )
    if event_name in {
        "maintenance_forward_priority",
        "maintenance_capacity_pressure",
    }:
        return f"Next action: {event.get('action') or 'unknown'}"
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
    activations = []
    current = None
    for event in events:
        event_name = event.get("event")
        if event_name == "boundary_scheduled":
            if current is not None:
                activations.append(current)
            current = {
                "boundary_timestamp": event.get("boundary_timestamp"),
                "scheduled_wall": event.get("scheduled_wall"),
                "start_lag_seconds": None,
                "duration_seconds": None,
                "completion_lag_seconds": None,
                "completed": False,
            }
        elif event_name == "activation_start":
            if current is None:
                current = {
                    "boundary_timestamp": None,
                    "scheduled_wall": None,
                    "duration_seconds": None,
                    "completion_lag_seconds": None,
                    "completed": False,
                }
            current["start_lag_seconds"] = event.get("start_lag_seconds")
        elif event_name == "activation_end":
            if current is None:
                current = {
                    "boundary_timestamp": event.get("active_frontier"),
                    "scheduled_wall": None,
                    "start_lag_seconds": None,
                }
            current.update(
                {
                    "duration_seconds": event.get("duration_seconds"),
                    "completion_lag_seconds": event.get(
                        "completion_lag_seconds"
                    ),
                    "active_frontier": event.get("active_frontier"),
                    "completed": True,
                }
            )
            activations.append(current)
            current = None
    if current is not None:
        activations.append(current)

    complete_durations = [
        float(value["duration_seconds"])
        for value in activations
        if isinstance(value.get("duration_seconds"), Real)
    ]
    maintenance_preparations = tuple(
        {
            "boundary_timestamp": value.get("boundary_timestamp"),
            "runtime_preparation_seconds": value.get("duration_seconds"),
            "state_materialization_seconds": value.get(
                "state_materialization_seconds"
            ),
            "total_ready_preparation_seconds": _sum_complete_durations(
                value.get("state_materialization_seconds"),
                value.get("duration_seconds"),
            ),
            "generation": value.get("generation"),
        }
        for value in events
        if value.get("event") == "maintenance_prepare_end"
    )
    generation_reclaims = tuple(
        _event_details(value)
        for value in events
        if value.get("event") == "generation_reclaimed"
    )
    physical_reclaims = tuple(
        _event_details(value)
        for value in events
        if value.get("event") == "resources_reclaimed"
    )
    return {
        "preparation_duration_seconds": (
            None if preparation_end is None else preparation_end.get("duration_seconds")
        ),
        "cleanup_duration_seconds": (
            None if cleanup_end is None else cleanup_end.get("duration_seconds")
        ),
        "activations": tuple(activations),
        "recent_activation_duration_seconds": (
            None if not complete_durations else complete_durations[-1]
        ),
        "max_activation_duration_seconds": (
            None if not complete_durations else max(complete_durations)
        ),
        "maintenance_preparations": maintenance_preparations,
        "maintenance_preparation_total_seconds": _duration_total(
            maintenance_preparations,
            field="total_ready_preparation_seconds",
        ),
        "generation_reclaims": generation_reclaims,
        "generation_reclaim_total_seconds": _duration_total(generation_reclaims),
        "physical_reclaims": physical_reclaims,
        "physical_reclaim_total_seconds": _duration_total(physical_reclaims),
        "deadline_misses": tuple(
            _event_details(value)
            for value in events
            if value.get("event") == "deadline_miss"
        ),
    }


def _sum_complete_durations(*values: object) -> float | None:
    if any(not isinstance(value, Real) for value in values):
        return None
    return sum(float(value) for value in values)


def _duration_total(
    values: tuple[dict[str, object], ...],
    *,
    field: str = "duration_seconds",
) -> float:
    return sum(
        float(value[field])
        for value in values
        if isinstance(value.get(field), Real)
    )


def _default_dataplane_evidence(
    events: tuple[dict[str, object], ...],
) -> dict[str, object]:
    foundation = _latest(events, "runtime_foundation_ready")
    capacity = _latest(events, "capacity_audit_end")
    ready = _latest(events, "ready_published")
    activation = _latest(events, "activation_end")
    generation_reclaims = tuple(
        _event_details(value)
        for value in events
        if value.get("event") == "generation_reclaimed"
    )
    physical_reclaims = tuple(
        _event_details(value)
        for value in events
        if value.get("event") == "resources_reclaimed"
    )
    return {
        "kind": "default-tc-bpf",
        "label": "Current default tc-BPF dataplane",
        "evidence_source": "recorded normal Runner lifecycle events",
        "fresh_kernel_inspection": False,
        "foundation": None if foundation is None else _event_details(foundation),
        "latest_capacity_audit": (
            None if capacity is None else _event_details(capacity)
        ),
        "latest_ready": None if ready is None else _event_details(ready),
        "latest_activation": (
            None if activation is None else _event_details(activation)
        ),
        "generation_reclaims": generation_reclaims,
        "physical_reclaims": physical_reclaims,
    }


__all__ = ["project_runner_inspection"]
