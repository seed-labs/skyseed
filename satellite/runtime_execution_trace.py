#!/usr/bin/env python3
# encoding: utf-8

"""Execution trace evidence for runtime/update cost attribution.

This module is intentionally reporting-only. It converts module-owned runtime
artifacts into a small span model and summarizes timing categories without
putting BPF, qdisc, or runtime dataplane semantics into the profile runner.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence


EXECUTION_TRACE_LABEL = "EXECUTION_TRACE_CONTRACT_ADDED"
MODULE_SPANS_LABEL = "MODULE_OWNED_TRACE_SPANS_ADDED"
TRACE_AGGREGATION_LABEL = "TRACE_AGGREGATION_ADDED"
CRITICAL_PATH_LABEL = "CRITICAL_PATH_SUMMARY_ADDED"
COST_ATTRIBUTION_LABEL = "COST_ATTRIBUTION_SUMMARY_ADDED"
BACKFILL_LIMITED_LABEL = "EXISTING_ARTIFACT_TRACE_BACKFILL_LIMITED"

EXECUTION_MODES = {
    "docker",
    "ip_link",
    "tc",
    "bpftool",
    "bpf_syscall",
    "node_local_agent",
    "report_only",
}
BLOCKING_SCOPES = {
    "startup_blocking",
    "epoch_blocking",
    "current_dataplane_affecting",
    "dataplane_nonblocking_prepare",
    "cleanup_blocking",
    "diagnostic_nonblocking",
}
TIMING_SEMANTICS = {
    "wall_clock",
    "cumulative",
    "nested",
    "inferred",
    "non_additive",
}
SPAN_SOURCE_TYPES = {
    "native_module_instrumentation",
    "profile_runner_aggregation",
    "artifact_backfill",
    "inferred_estimate",
}

FUTURE_NATIVE_OWNER_BY_OPERATION = {
    "compose_stage": "satellite/apps/run_starlink_shell_experiment.py",
    "event_wait": "satellite/apps/run_starlink_shell_experiment.py",
    "host_update_apply": "satellite/network_runtime.py",
    "generation_gc_delete": "satellite/bpf_mpls_live.py",
    "target_generation_map_update": "satellite/bpf_mpls_live.py",
    "active_generation_flip": "satellite/bpf_mpls_live.py",
    "qdisc_update": "satellite/network_runtime.py",
    "node_local_apply": "satellite/apps/run_starlink_shell_experiment.py",
    "post_apply_reporting_validation": "satellite/apps/run_skyseed_validation_profile.py",
    "compose_cleanup": "satellite/apps/run_starlink_shell_experiment.py",
    "direct_interface_cleanup": "satellite/network_runtime.py",
    "bpf_pin_cleanup": "satellite/apps/run_starlink_shell_experiment.py",
    "external_residue_check": "satellite/apps/run_skyseed_validation_profile.py",
}

TRACE_FIELD_CONTRACT: Dict[str, Dict[str, Any]] = {
    field: {
        "owner_module": "satellite/runtime_execution_trace.py",
        "scope": "global",
        "lifetime": "permanent evidence contract",
        "not_ad_hoc_reason": (
            "The field is part of the stable execution trace schema for every "
            "runtime/update profile, not keyed to the current S4 run."
        ),
    }
    for field in (
        "trace_id",
        "span_id",
        "parent_span_id",
        "run_id",
        "epoch_id",
        "generation",
        "module",
        "operation",
        "resource",
        "start_monotonic",
        "end_monotonic",
        "duration_seconds",
        "count",
        "execution_mode",
        "blocking_scope",
        "timing_semantics",
        "parallel_group",
        "critical_path_candidate",
        "result",
        "source_type",
        "source_detail",
        "native_span",
        "inferred_estimate",
        "future_native_owner_module",
    )
}

SPAN_SCHEMA_FIELDS = tuple(TRACE_FIELD_CONTRACT)


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists() or path.stat().st_size == 0:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    records: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or isinstance(value, bool):
            return default
        return round(float(value), 6)
    except (TypeError, ValueError):
        return default


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _span(
    *,
    trace_id: str,
    index: int,
    run_id: str,
    module: str,
    operation: str,
    resource: str,
    duration_seconds: float,
    start_monotonic: float,
    epoch_id: str | None = None,
    generation: int | None = None,
    parent_span_id: str | None = None,
    count: int | None = None,
    execution_mode: str,
    blocking_scope: str,
    timing_semantics: str,
    parallel_group: str | None = None,
    critical_path_candidate: bool = False,
    result: str = "ok",
    category: str,
    owner_module: str,
    scope: str = "global",
    lifetime: str = "permanent evidence contract",
    not_ad_hoc_reason: str | None = None,
    backfilled_from_existing_artifact: bool = True,
    backfill_limit: str | None = None,
    source_type: str = "artifact_backfill",
    source_detail: str | None = None,
    future_native_owner_module: str | None = None,
) -> Dict[str, Any]:
    duration = max(_num(duration_seconds), 0.0)
    inferred_estimate = source_type == "inferred_estimate" or timing_semantics == "inferred"
    span = {
        "trace_id": trace_id,
        "span_id": f"span-{index:06d}",
        "parent_span_id": parent_span_id,
        "run_id": run_id,
        "epoch_id": epoch_id,
        "generation": generation,
        "module": module,
        "operation": operation,
        "resource": resource,
        "start_monotonic": round(start_monotonic, 6),
        "end_monotonic": round(start_monotonic + duration, 6),
        "duration_seconds": duration,
        "count": count,
        "execution_mode": execution_mode,
        "blocking_scope": blocking_scope,
        "timing_semantics": timing_semantics,
        "parallel_group": parallel_group,
        "critical_path_candidate": bool(critical_path_candidate),
        "result": result,
        "source_type": source_type,
        "source_detail": source_detail
        or (
            "constructed from persisted run artifacts by the trace aggregator; "
            "not native module instrumentation"
        ),
        "native_span": source_type == "native_module_instrumentation",
        "inferred_estimate": inferred_estimate,
        "future_native_owner_module": future_native_owner_module
        or FUTURE_NATIVE_OWNER_BY_OPERATION.get(operation)
        or owner_module,
        "category": category,
        "owner_module": owner_module,
        "scope": scope,
        "lifetime": lifetime,
        "not_ad_hoc_reason": not_ad_hoc_reason
        or (
            "This span is derived from module-owned artifacts for any run that "
            "emits the source fields; it is not keyed to the current S4 run."
        ),
        "backfilled_from_existing_artifact": bool(backfilled_from_existing_artifact),
    }
    if backfill_limit:
        span["backfill_limit"] = backfill_limit
    return span


def validate_trace_span(span: Mapping[str, Any]) -> List[str]:
    errors: List[str] = []
    for field in SPAN_SCHEMA_FIELDS:
        if field not in span:
            errors.append(f"missing:{field}")
    if span.get("execution_mode") not in EXECUTION_MODES:
        errors.append("invalid:execution_mode")
    if span.get("blocking_scope") not in BLOCKING_SCOPES:
        errors.append("invalid:blocking_scope")
    if span.get("timing_semantics") not in TIMING_SEMANTICS:
        errors.append("invalid:timing_semantics")
    if span.get("source_type") not in SPAN_SOURCE_TYPES:
        errors.append("invalid:source_type")
    if span.get("source_type") == "native_module_instrumentation" and not span.get("native_span"):
        errors.append("invalid:native_span_flag")
    if span.get("source_type") == "inferred_estimate" and not span.get("inferred_estimate"):
        errors.append("invalid:inferred_estimate_flag")
    if str(span.get("result")) not in {"ok", "failed", "skipped"}:
        errors.append("invalid:result")
    if _num(span.get("end_monotonic"), -1.0) < _num(span.get("start_monotonic"), 0.0):
        errors.append("invalid:monotonic_order")
    return errors


def _bpf_load(record: Mapping[str, Any]) -> Dict[str, Any]:
    node = dict(record.get("node_local_execution") or {})
    backend = dict(node.get("backend") or {})
    readiness = dict(backend.get("bpf_owned_readiness") or {})
    prepare_steps = dict(readiness.get("prepare_steps") or {})
    load = prepare_steps.get("load")
    return dict(load) if isinstance(load, Mapping) else {}


def _delete_count(load: Mapping[str, Any]) -> int:
    cleanup = dict(load.get("generation_gc_cleanup") or {})
    total = 0
    for map_contract in dict(cleanup.get("maps") or {}).values():
        if not isinstance(map_contract, Mapping):
            continue
        total += int(map_contract.get("old_generation_delete_update_count") or 0)
        total += int(map_contract.get("target_generation_delete_update_count") or 0)
    return total


def _event_type(record: Mapping[str, Any]) -> str:
    event = str(record.get("event_timeline_type") or record.get("epoch_type") or "")
    if event == "runtime_transaction" or str(record.get("epoch_type") or "") == "runtime_epoch":
        return "runtime_transaction"
    if event == "parameter_update_batch" or str(record.get("epoch_type") or "") == "parameter_epoch":
        return "parameter_update_batch"
    return event


def _iter_compose_spans(
    *,
    trace_id: str,
    run_id: str,
    report: Mapping[str, Any],
    start_index: int,
) -> Iterable[Dict[str, Any]]:
    cursor = 0.0
    index = start_index
    startup = dict(report.get("startup_strategy") or {})
    for stage in list(startup.get("compose_stage_results") or []):
        duration = _num(stage.get("wall_seconds"))
        yield _span(
            trace_id=trace_id,
            index=index,
            run_id=run_id,
            module="host_materialization",
            operation="compose_stage",
            resource=str(stage.get("stage_id") or "compose-stage"),
            duration_seconds=duration,
            start_monotonic=cursor,
            count=_int_or_none(stage.get("service_count")),
            execution_mode="docker",
            blocking_scope="startup_blocking",
            timing_semantics="wall_clock",
            parallel_group=str(stage.get("stage_type") or "") or None,
            critical_path_candidate=True,
            result="failed" if stage.get("timed_out") else "ok",
            category="startup/materialization",
            owner_module="satellite/apps/run_starlink_shell_experiment.py",
        )
        cursor += duration
        index += 1


def _iter_epoch_spans(
    *,
    trace_id: str,
    run_id: str,
    records: Sequence[Mapping[str, Any]],
    start_index: int,
) -> Iterable[Dict[str, Any]]:
    cursor = 0.0
    index = start_index
    for record in records:
        timing = dict(record.get("timing") or {})
        epoch_id = str(record.get("epoch_id") or "")
        event_type = _event_type(record)
        load = _bpf_load(record)
        generation = _int_or_none(load.get("active_generation"))
        event_wait = _num(timing.get("event_wait_seconds"))
        if event_wait:
            yield _span(
                trace_id=trace_id,
                index=index,
                run_id=run_id,
                module="event_timeline",
                operation="event_wait",
                resource=event_type,
                duration_seconds=event_wait,
                start_monotonic=cursor,
                epoch_id=epoch_id,
                generation=generation,
                execution_mode="report_only",
                blocking_scope="diagnostic_nonblocking",
                timing_semantics="wall_clock",
                critical_path_candidate=False,
                category="event wait",
                owner_module="satellite/apps/run_starlink_shell_experiment.py",
            )
            cursor += event_wait
            index += 1

        host_apply = _num(timing.get("host_operation_apply_wall_seconds"))
        yield _span(
            trace_id=trace_id,
            index=index,
            run_id=run_id,
            module="host_materialization",
            operation="host_update_apply",
            resource=str(record.get("segment_epoch_key") or event_type),
            duration_seconds=host_apply,
            start_monotonic=cursor,
            epoch_id=epoch_id,
            generation=generation,
            count=_int_or_none(
                dict(record.get("host_operation_timing_summary") or {}).get("operation_count")
            ),
            execution_mode="ip_link",
            blocking_scope="epoch_blocking",
            timing_semantics="wall_clock",
            critical_path_candidate=True,
            category="runtime update work",
            owner_module="satellite/network_runtime.py",
        )
        cursor += host_apply
        index += 1

        if event_type == "runtime_transaction" and load:
            delete_count = _delete_count(load)
            yield _span(
                trace_id=trace_id,
                index=index,
                run_id=run_id,
                module="bpf_map_loader",
                operation="generation_gc_delete",
                resource="generation_keyed_maps",
                duration_seconds=0.0,
                start_monotonic=cursor,
                epoch_id=epoch_id,
                generation=generation,
                count=delete_count,
                execution_mode="bpftool",
                blocking_scope="dataplane_nonblocking_prepare",
                timing_semantics="inferred",
                critical_path_candidate=False,
                result="ok",
                category="dataplane-nonblocking prepare",
                owner_module="satellite/bpf_mpls_live.py",
                backfill_limit="existing artifacts expose delete counts but not elapsed seconds",
                source_type="inferred_estimate",
                source_detail=(
                    "delete count backfilled from BPF loader readiness artifact; "
                    "elapsed time is unavailable in the existing run"
                ),
            )
            index += 1
            map_write = _num(load.get("map_write_wall_seconds"))
            yield _span(
                trace_id=trace_id,
                index=index,
                run_id=run_id,
                module="bpf_map_loader",
                operation="target_generation_map_update",
                resource="generation_keyed_maps",
                duration_seconds=map_write,
                start_monotonic=cursor,
                epoch_id=epoch_id,
                generation=generation,
                count=_int_or_none(load.get("total_update_count")),
                execution_mode="bpftool",
                blocking_scope="dataplane_nonblocking_prepare",
                timing_semantics="nested",
                critical_path_candidate=False,
                result="ok" if load.get("ok", True) else "failed",
                category="dataplane-nonblocking prepare",
                owner_module="satellite/bpf_mpls_live.py",
                source_detail=(
                    "map write count and elapsed time backfilled from BPF loader "
                    "readiness artifact"
                ),
            )
            index += 1
            flip = _num(
                load.get("active_generation_flip_wall_seconds"),
                _num(timing.get("active_generation_flip_seconds")),
            )
            yield _span(
                trace_id=trace_id,
                index=index,
                run_id=run_id,
                module="bpf_map_loader",
                operation="active_generation_flip",
                resource="active_generation",
                duration_seconds=flip,
                start_monotonic=cursor + host_apply,
                epoch_id=epoch_id,
                generation=generation,
                count=_int_or_none(load.get("active_generation_update_count")),
                execution_mode="bpftool",
                blocking_scope="current_dataplane_affecting",
                timing_semantics="nested",
                critical_path_candidate=True,
                result="ok" if load.get("active_generation_flip_attempted") else "skipped",
                category="active dataplane-affecting switch",
                owner_module="satellite/bpf_mpls_live.py",
                source_detail=(
                    "active generation flip timing backfilled from BPF loader "
                    "readiness artifact"
                ),
            )
            index += 1

        qdisc_count = _int_or_none(timing.get("parameter_qdisc_required_update_count"))
        qdisc_seconds = _num(timing.get("parameter_qdisc_apply_seconds"))
        if qdisc_count or qdisc_seconds:
            yield _span(
                trace_id=trace_id,
                index=index,
                run_id=run_id,
                module="qdisc_backend",
                operation="qdisc_update",
                resource=str(record.get("segment_epoch_key") or "parameter-qdisc"),
                duration_seconds=qdisc_seconds,
                start_monotonic=cursor,
                epoch_id=epoch_id,
                generation=generation,
                count=qdisc_count,
                execution_mode="tc",
                blocking_scope="current_dataplane_affecting",
                timing_semantics="cumulative",
                critical_path_candidate=False,
                category="runtime update work",
                owner_module="satellite/network_runtime.py",
                backfill_limit="existing qdisc timing may be cumulative command time",
                source_detail=(
                    "qdisc update count and elapsed time backfilled from runtime "
                    "timing summary; future native span must come from qdisc backend"
                ),
            )
            index += 1

        node_local = _num(timing.get("node_local_apply_wall_seconds"))
        yield _span(
            trace_id=trace_id,
            index=index,
            run_id=run_id,
            module="node_local_agent",
            operation="node_local_apply",
            resource=str(record.get("segment_epoch_key") or event_type),
            duration_seconds=node_local,
            start_monotonic=cursor + host_apply,
            epoch_id=epoch_id,
            generation=generation,
            count=_int_or_none(timing.get("node_local_reapplied_flow_count")),
            execution_mode="node_local_agent",
            blocking_scope="epoch_blocking",
            timing_semantics="wall_clock",
            critical_path_candidate=True,
            result="ok",
            category="runtime update work",
            owner_module="satellite/apps/run_starlink_shell_experiment.py",
        )
        index += 1

        overhead = _num(timing.get("post_apply_overhead_seconds"))
        validation = _num(timing.get("targeted_validation_seconds")) + _num(
            timing.get("post_transition_connectivity_probe_wall_seconds")
        )
        if overhead or validation:
            yield _span(
                trace_id=trace_id,
                index=index,
                run_id=run_id,
                module="profile_runner",
                operation="post_apply_reporting_validation",
                resource=str(record.get("segment_epoch_key") or event_type),
                duration_seconds=round(overhead + validation, 6),
                start_monotonic=cursor + host_apply + node_local,
                epoch_id=epoch_id,
                generation=generation,
                execution_mode="report_only",
                blocking_scope="diagnostic_nonblocking",
                timing_semantics="wall_clock",
                critical_path_candidate=False,
                category="post-apply/reporting overhead",
                owner_module="satellite/apps/run_starlink_shell_experiment.py",
                source_type="profile_runner_aggregation",
                source_detail=(
                    "profile/reporting overhead aggregated from persisted timing fields; "
                    "does not claim BPF or qdisc module ownership"
                ),
            )
            index += 1
        cursor += _num(timing.get("network_update_seconds"), host_apply + node_local)


def _iter_cleanup_spans(
    *,
    trace_id: str,
    run_id: str,
    report: Mapping[str, Any],
    progress: Mapping[str, Any],
    cleanup_report: Mapping[str, Any],
    start_index: int,
) -> Iterable[Dict[str, Any]]:
    index = start_index
    phase_timing = dict(progress.get("phase_timing") or report.get("phase_timing") or {})
    final_cleanup = dict(report.get("final_cleanup") or {})
    host_cleanup = dict(final_cleanup.get("host_artifact_cleanup") or {})
    bpf_cleanup = dict(final_cleanup.get("bpf_backend_cleanup") or {})
    residue_after = dict(cleanup_report.get("after") or {})
    external_clean = bool(cleanup_report.get("external_cleanup_success") or residue_after.get("clean"))
    compose_down = _num(phase_timing.get("compose_down_after_wall_seconds"))
    if compose_down:
        yield _span(
            trace_id=trace_id,
            index=index,
            run_id=run_id,
            module="cleanup",
            operation="compose_cleanup",
            resource="docker_compose_project",
            duration_seconds=compose_down,
            start_monotonic=0.0,
            count=None,
            execution_mode="docker",
            blocking_scope="cleanup_blocking",
            timing_semantics="wall_clock",
            critical_path_candidate=True,
            result="ok" if external_clean else "failed",
            category="cleanup",
            owner_module="satellite/apps/run_starlink_shell_experiment.py",
        )
        index += 1
    host_timing = dict(host_cleanup.get("timing") or {})
    host_duration = _num(host_timing.get("cleanup_wall_seconds"))
    if host_duration:
        metrics = dict(host_cleanup.get("cleanup_metrics") or {})
        yield _span(
            trace_id=trace_id,
            index=index,
            run_id=run_id,
            module="cleanup",
            operation="direct_interface_cleanup",
            resource="direct_backend_runtime_artifacts",
            duration_seconds=host_duration,
            start_monotonic=compose_down,
            count=_int_or_none(metrics.get("cleanup_resource_attempted_count")),
            execution_mode="ip_link",
            blocking_scope="cleanup_blocking",
            timing_semantics="wall_clock",
            critical_path_candidate=True,
            result="ok" if host_cleanup.get("cleanup_completed", True) else "failed",
            category="cleanup",
            owner_module="satellite/network_runtime.py",
        )
        index += 1
    bpf_steps = list(bpf_cleanup.get("steps") or [])
    if bpf_steps:
        yield _span(
            trace_id=trace_id,
            index=index,
            run_id=run_id,
            module="cleanup",
            operation="bpf_pin_cleanup",
            resource="tc_globals",
            duration_seconds=0.0,
            start_monotonic=compose_down + host_duration,
            count=len(bpf_steps),
            execution_mode="bpftool",
            blocking_scope="cleanup_blocking",
            timing_semantics="inferred",
            critical_path_candidate=False,
            result="ok" if dict(bpf_cleanup.get("residue") or {}).get("clean", True) else "failed",
            category="cleanup",
            owner_module="satellite/apps/run_starlink_shell_experiment.py",
            backfill_limit="existing cleanup artifact exposes steps but not elapsed seconds",
            source_type="inferred_estimate",
            source_detail=(
                "BPF cleanup step count backfilled from cleanup artifact; elapsed "
                "time is unavailable in the existing run"
            ),
        )
        index += 1
    lock_count = int(dict(residue_after.get("counts") or {}).get("runtime_locks") or 0)
    yield _span(
        trace_id=trace_id,
        index=index,
        run_id=run_id,
        module="cleanup",
        operation="external_residue_check",
        resource="owned_skyseed_residue",
        duration_seconds=0.0,
        start_monotonic=compose_down + host_duration,
        count=lock_count,
        execution_mode="report_only",
        blocking_scope="cleanup_blocking",
        timing_semantics="inferred",
        critical_path_candidate=False,
        result="ok" if external_clean else "failed",
        category="cleanup",
        owner_module="satellite/apps/run_skyseed_validation_profile.py",
        backfill_limit="existing residue detector does not record elapsed seconds",
        source_type="inferred_estimate",
        source_detail=(
            "external residue proof is backfilled from cleanup report; elapsed "
            "time is unavailable in the existing run"
        ),
    )


def _span_sequence_valid(spans: Sequence[Mapping[str, Any]]) -> List[str]:
    errors: List[str] = []
    for span in spans:
        for error in validate_trace_span(span):
            errors.append(f"{span.get('span_id')}:{error}")
        if span.get("native_span") and span.get("backfilled_from_existing_artifact"):
            errors.append(f"{span.get('span_id')}:native_span_marked_backfilled")
        if span.get("native_span") and span.get("inferred_estimate"):
            errors.append(f"{span.get('span_id')}:native_span_marked_inferred")
        if span.get("module") in {"bpf_map_loader", "qdisc_backend"} and span.get(
            "source_type"
        ) == "profile_runner_aggregation":
            errors.append(f"{span.get('span_id')}:profile_runner_owns_dataplane_semantics")
        if span.get("source_type") == "native_module_instrumentation" and span.get(
            "owner_module"
        ) != span.get("future_native_owner_module"):
            errors.append(f"{span.get('span_id')}:native_owner_mismatch")
    by_epoch: Dict[str, List[Mapping[str, Any]]] = {}
    for span in spans:
        epoch_id = span.get("epoch_id")
        if epoch_id:
            by_epoch.setdefault(str(epoch_id), []).append(span)
    for epoch_id, epoch_spans in by_epoch.items():
        update_indices = [
            index
            for index, span in enumerate(epoch_spans)
            if span.get("operation") == "target_generation_map_update"
        ]
        flip_indices = [
            index
            for index, span in enumerate(epoch_spans)
            if span.get("operation") == "active_generation_flip"
        ]
        if update_indices and flip_indices and min(flip_indices) <= max(update_indices):
            errors.append(f"{epoch_id}:active_generation_flip_before_map_update")
    return errors


def _sum_category(spans: Sequence[Mapping[str, Any]], category: str) -> float:
    return round(
        sum(
            float(span.get("duration_seconds") or 0.0)
            for span in spans
            if span.get("category") == category
            and span.get("timing_semantics") not in {"non_additive", "cumulative"}
        ),
        6,
    )


def _span_source_counts(spans: Sequence[Mapping[str, Any]]) -> Dict[str, int]:
    return {
        "native": sum(1 for span in spans if span.get("source_type") == "native_module_instrumentation"),
        "backfilled": sum(1 for span in spans if span.get("source_type") == "artifact_backfill"),
        "inferred": sum(1 for span in spans if span.get("source_type") == "inferred_estimate"),
        "profile_runner_aggregation": sum(
            1 for span in spans if span.get("source_type") == "profile_runner_aggregation"
        ),
    }


def _confidence_for_spans(spans: Sequence[Mapping[str, Any]]) -> str:
    if not spans:
        return "low"
    counts = _span_source_counts(spans)
    if counts["inferred"] or any(span.get("timing_semantics") == "cumulative" for span in spans):
        return "low"
    if counts["backfilled"] or counts["profile_runner_aggregation"]:
        return "medium"
    return "high"


def _coverage_summary(spans: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[tuple[str, str], List[Mapping[str, Any]]] = {}
    for span in spans:
        groups.setdefault((str(span.get("module")), str(span.get("operation"))), []).append(span)
    summary: List[Dict[str, Any]] = []
    for (module, operation), group in sorted(groups.items()):
        counts = _span_source_counts(group)
        future_owners = sorted(
            {str(span.get("future_native_owner_module")) for span in group if span.get("future_native_owner_module")}
        )
        only_profile_aggregation = (
            counts["profile_runner_aggregation"] == len(group)
            and len(group) > 0
        )
        missing_native = counts["native"] == 0 and not only_profile_aggregation
        summary.append(
            {
                "module": module,
                "operation": operation,
                "native_span_count": counts["native"],
                "backfilled_span_count": counts["backfilled"],
                "inferred_span_count": counts["inferred"],
                "profile_runner_aggregation_span_count": counts[
                    "profile_runner_aggregation"
                ],
                "missing_native_spans": missing_native,
                "future_native_owner_module": future_owners[0] if len(future_owners) == 1 else future_owners,
                "profile_runner_may_aggregate_only": module
                in {"bpf_map_loader", "qdisc_backend"},
            }
        )
    return summary


def _category_provenance_summary(
    spans: Sequence[Mapping[str, Any]],
    categories: Mapping[str, float],
) -> Dict[str, Dict[str, Any]]:
    summary: Dict[str, Dict[str, Any]] = {}
    for category in categories:
        category_spans = [span for span in spans if span.get("category") == category]
        counts = _span_source_counts(category_spans)
        summary[category] = {
            "optimization_decision_confidence": _confidence_for_spans(category_spans),
            "native_span_count": counts["native"],
            "backfilled_span_count": counts["backfilled"],
            "inferred_span_count": counts["inferred"],
            "profile_runner_aggregation_span_count": counts[
                "profile_runner_aggregation"
            ],
            "source_types": sorted({str(span.get("source_type")) for span in category_spans}),
            "timing_semantics": sorted(
                {str(span.get("timing_semantics")) for span in category_spans}
            ),
            "backfill_limited": any(
                span.get("backfill_limit")
                or span.get("source_type")
                in {"artifact_backfill", "inferred_estimate", "profile_runner_aggregation"}
                for span in category_spans
            ),
        }
    return summary


def write_execution_trace_artifacts(run_dir: Path) -> Dict[str, Any]:
    runner_report = _read_json(run_dir / "profile_runner_stdout.json")
    runtime_report = _read_json(run_dir / "real_runtime_report.json")
    progress = _read_json(run_dir / "runtime_progress.json")
    cleanup_report = _read_json(run_dir / "normal_failure_cleanup_report.json")
    records = _read_jsonl(run_dir / "epoch_execution_details.jsonl")
    run_id = str(runner_report.get("run_id") or run_dir.name)
    trace_id = f"{run_id}:execution-trace"
    spans: List[Dict[str, Any]] = []
    spans.extend(
        _iter_compose_spans(
            trace_id=trace_id,
            run_id=run_id,
            report=runner_report,
            start_index=len(spans) + 1,
        )
    )
    spans.extend(
        _iter_epoch_spans(
            trace_id=trace_id,
            run_id=run_id,
            records=records,
            start_index=len(spans) + 1,
        )
    )
    spans.extend(
        _iter_cleanup_spans(
            trace_id=trace_id,
            run_id=run_id,
            report=runtime_report or runner_report,
            progress=progress,
            cleanup_report=cleanup_report,
            start_index=len(spans) + 1,
        )
    )
    validation_errors = _span_sequence_valid(spans)
    categories = {
        "startup/materialization": _sum_category(spans, "startup/materialization"),
        "event wait": _sum_category(spans, "event wait"),
        "runtime update work": _sum_category(spans, "runtime update work"),
        "dataplane-nonblocking prepare": _sum_category(
            spans, "dataplane-nonblocking prepare"
        ),
        "active dataplane-affecting switch": _sum_category(
            spans, "active dataplane-affecting switch"
        ),
        "post-apply/reporting overhead": _sum_category(
            spans, "post-apply/reporting overhead"
        ),
        "cleanup": _sum_category(spans, "cleanup"),
    }
    coverage = _coverage_summary(spans)
    category_provenance = _category_provenance_summary(spans, categories)
    critical_spans = [
        span
        for span in spans
        if span.get("critical_path_candidate")
        and span.get("timing_semantics") not in {"non_additive", "cumulative"}
    ]
    critical_summary = {
        "schema_version": "skyseed-critical-path-summary-v1",
        "trace_id": trace_id,
        "critical_path_seconds_by_category": {
            category: _sum_category(critical_spans, category)
            for category in sorted({str(span.get("category")) for span in critical_spans})
        },
        "critical_path_span_count": len(critical_spans),
        "coverage_summary": coverage,
        "category_provenance": {
            category: category_provenance[category]
            for category in sorted(
                {str(span.get("category")) for span in critical_spans}
            )
        },
        "validation_errors": validation_errors,
        "non_additive_excluded": True,
        "event_wait_excluded_from_runtime_update_bottleneck": True,
        "backfill_limited": any(span.get("backfill_limit") for span in critical_spans),
    }
    cost_summary = {
        "schema_version": "skyseed-cost-attribution-summary-v1",
        "trace_id": trace_id,
        "span_count": len(spans),
        "categories": categories,
        "category_provenance": category_provenance,
        "coverage_summary": coverage,
        "execution_mode_seconds": {
            mode: round(
                sum(
                    float(span.get("duration_seconds") or 0.0)
                    for span in spans
                    if span.get("execution_mode") == mode
                    and span.get("timing_semantics") not in {"non_additive", "cumulative"}
                ),
                6,
            )
            for mode in sorted(EXECUTION_MODES)
        },
        "blocking_scope_seconds": {
            scope: round(
                sum(
                    float(span.get("duration_seconds") or 0.0)
                    for span in spans
                    if span.get("blocking_scope") == scope
                    and span.get("timing_semantics") not in {"non_additive", "cumulative"}
                ),
                6,
            )
            for scope in sorted(BLOCKING_SCOPES)
        },
        "module_seconds": {
            module: round(
                sum(
                    float(span.get("duration_seconds") or 0.0)
                    for span in spans
                    if span.get("module") == module
                    and span.get("timing_semantics") not in {"non_additive", "cumulative"}
                ),
                6,
            )
            for module in sorted({str(span.get("module")) for span in spans})
        },
        "non_additive_span_count": sum(
            1 for span in spans if span.get("timing_semantics") in {"non_additive", "cumulative"}
        ),
        "backfill_limited": any(
            span.get("backfill_limit")
            or span.get("source_type")
            in {"artifact_backfill", "inferred_estimate", "profile_runner_aggregation"}
            for span in spans
        ),
        "cleanup_external_residue_proof": {
            "available": bool(cleanup_report),
            "external_cleanup_success": bool(
                cleanup_report.get("external_cleanup_success")
                or dict(cleanup_report.get("after") or {}).get("clean")
            ),
            "after_counts": dict(dict(cleanup_report.get("after") or {}).get("counts") or {}),
        },
        "trace_contract": {
            "schema_fields": list(SPAN_SCHEMA_FIELDS),
            "field_contract": TRACE_FIELD_CONTRACT,
            "allowed_execution_modes": sorted(EXECUTION_MODES),
            "allowed_blocking_scopes": sorted(BLOCKING_SCOPES),
            "allowed_timing_semantics": sorted(TIMING_SEMANTICS),
            "allowed_span_source_types": sorted(SPAN_SOURCE_TYPES),
            "profile_runner_role": "aggregate_validate_summarize_only",
            "profile_runner_does_not_own_bpf_or_qdisc_semantics": True,
            "future_native_owner_by_operation": FUTURE_NATIVE_OWNER_BY_OPERATION,
            "no_s4_specific_counts_required": True,
        },
        "outcome_labels": [
            EXECUTION_TRACE_LABEL,
            MODULE_SPANS_LABEL,
            TRACE_AGGREGATION_LABEL,
            CRITICAL_PATH_LABEL,
            COST_ATTRIBUTION_LABEL,
            BACKFILL_LIMITED_LABEL,
        ],
        "validation_errors": validation_errors,
    }
    _write_jsonl(run_dir / "execution_trace.jsonl", spans)
    _write_json(run_dir / "critical_path_summary.json", critical_summary)
    _write_json(run_dir / "cost_attribution_summary.json", cost_summary)
    return cost_summary
