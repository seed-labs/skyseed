#!/usr/bin/env python3
# encoding: utf-8

"""Inspect in-progress or completed temporal run artifacts."""

from __future__ import annotations

import json
import statistics
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List


DEFAULT_STALL_THRESHOLD_SECONDS = 300.0
INSPECTED_ARTIFACTS = (
    "run_manifest.json",
    "temporal_audit.json",
    "real_runtime_report.json",
    "post_run_preflight.json",
    "timing.json",
    "timing.csv",
    "return_path_audit.json",
    "runtime_progress.json",
    "events.jsonl",
)


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _read_events(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    events: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def _iter_dicts(value: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _iter_dicts(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _iter_dicts(nested)


def _artifact_report(run_dir: Path) -> Dict[str, Dict[str, Any]]:
    now = time.time()
    report: Dict[str, Dict[str, Any]] = {}
    for name in INSPECTED_ARTIFACTS:
        path = run_dir / name
        if not path.exists():
            report[name] = {"exists": False}
            continue
        stat = path.stat()
        report[name] = {
            "exists": True,
            "size_bytes": stat.st_size,
            "mtime_epoch": stat.st_mtime,
            "age_seconds": round(max(0.0, now - stat.st_mtime), 6),
            "path": str(path),
        }
    return report


def _avg(values: Iterable[float]) -> float:
    values = list(values)
    if not values:
        return 0.0
    return round(sum(values) / float(len(values)), 6)


def _stats(values: Iterable[float]) -> Dict[str, Any]:
    rendered = [float(value) for value in values]
    if not rendered:
        return {
            "count": 0,
            "average": 0.0,
            "minimum": 0.0,
            "maximum": 0.0,
            "median": 0.0,
        }
    return {
        "count": len(rendered),
        "average": round(sum(rendered) / float(len(rendered)), 6),
        "minimum": round(min(rendered), 6),
        "maximum": round(max(rendered), 6),
        "median": round(float(statistics.median(rendered)), 6),
    }


def _initial_activation_from_timing_analysis(
    timing_analysis: Dict[str, Any],
) -> Dict[str, Any]:
    startup = dict(timing_analysis.get("startup_preparation", {}))
    first_runtime = dict(timing_analysis.get("first_runtime_epoch", {}))
    steady_runtime = dict(timing_analysis.get("steady_state_runtime_epochs", {}))
    cold_start = float(
        startup.get("cold_start_before_first_epoch_execution_wall_seconds", 0.0)
        or 0.0
    )
    first_transition = float(
        first_runtime.get("transition_wall_seconds", 0.0) or 0.0
    )
    first_execution = first_runtime.get(
        "execution_after_prepare_reconcile_wall_seconds"
    )
    if first_execution is None:
        first_execution = first_transition
    first_execution = float(first_execution or 0.0)
    source_runtime_count = int(
        steady_runtime.get("source_runtime_epoch_count", 0) or 0
    )
    return {
        "classification": "bootstrap_plus_initial_runtime_epoch",
        "includes_first_runtime_epoch": source_runtime_count > 0 or first_transition > 0.0,
        "steady_runtime_excludes_initial_activation": True,
        "cold_start_before_first_epoch_execution_wall_seconds": round(cold_start, 6),
        "first_runtime_epoch_transition_wall_seconds": round(first_transition, 6),
        "first_runtime_epoch_execution_after_prepare_reconcile_wall_seconds": round(
            first_execution,
            6,
        ),
        "initial_activation_wall_seconds": round(cold_start + first_execution, 6),
    }


def _teardown_cleanup_summary(
    *,
    runtime_report: Dict[str, Any],
    timing: Dict[str, Any],
) -> Dict[str, Any]:
    final_cleanup = dict(runtime_report.get("final_cleanup") or {})
    phase_timing = dict(timing.get("phase_timing", {}))
    node_local = dict(final_cleanup.get("node_local") or {})
    node_timing = dict(node_local.get("timing") or {})
    node_rollback_seconds = float(
        node_timing.get("rollback_wall_seconds", 0.0) or 0.0
    )
    host_rollbacks = [
        dict(rollback)
        for rollback in list(final_cleanup.get("host_rollbacks", []))
        if isinstance(rollback, dict)
    ]
    host_rollback_seconds = [
        float(dict(rollback.get("timing") or {}).get("rollback_wall_seconds", 0.0) or 0.0)
        for rollback in host_rollbacks
    ]
    host_artifact_cleanup = dict(final_cleanup.get("host_artifact_cleanup") or {})
    host_artifact_timing = dict(host_artifact_cleanup.get("timing") or {})
    host_artifact_cleanup_seconds = (
        float(host_artifact_timing.get("cleanup_wall_seconds"))
        if host_artifact_timing.get("cleanup_wall_seconds") is not None
        else None
    )
    post_cleanup_artifacts = dict(
        final_cleanup.get("post_cleanup_direct_backend_artifacts") or {}
    )
    post_run_preflight_seconds = float(
        phase_timing.get("post_run_preflight_wall_seconds", 0.0) or 0.0
    )
    known_wall_seconds = (
        node_rollback_seconds
        + sum(host_rollback_seconds)
        + post_run_preflight_seconds
    )
    teardown_wall_seconds = (
        known_wall_seconds + host_artifact_cleanup_seconds
        if host_artifact_cleanup_seconds is not None
        else None
    )
    cleanup_success = final_cleanup.get("all_cleanup_success")
    return {
        "present": bool(final_cleanup) or post_run_preflight_seconds > 0.0,
        "all_cleanup_success": cleanup_success,
        "node_local_rollback_wall_seconds": round(node_rollback_seconds, 6),
        "node_local_session_cleanup_count": int(
            node_local.get("session_cleanup_count", 0) or 0
        ),
        "node_local_rollback_result_count": len(
            list(node_local.get("rollback_results", []))
        ),
        "host_rollback_count": len(host_rollbacks),
        "host_rollback_wall_seconds": round(sum(host_rollback_seconds), 6),
        "host_rollback_wall_max_seconds": round(
            max(host_rollback_seconds) if host_rollback_seconds else 0.0,
            6,
        ),
        "host_artifact_cleanup_completed": host_artifact_cleanup.get(
            "cleanup_completed"
        ),
        "host_artifact_cleanup_removed_host_interface_count": int(
            host_artifact_cleanup.get("removed_host_interface_count", 0) or 0
        ),
        "host_artifact_cleanup_removed_bridge_count": int(
            host_artifact_cleanup.get("removed_bridge_count", 0) or 0
        ),
        "host_artifact_cleanup_wall_seconds": (
            None
            if host_artifact_cleanup_seconds is None
            else round(host_artifact_cleanup_seconds, 6)
        ),
        "post_cleanup_remaining_host_interface_count": max(
            len(list(post_cleanup_artifacts.get("interfaces", []))),
            int(host_artifact_cleanup.get("remaining_host_interface_count", 0) or 0),
        ),
        "post_cleanup_remaining_bridge_count": max(
            len(list(post_cleanup_artifacts.get("bridges", []))),
            int(host_artifact_cleanup.get("remaining_bridge_count", 0) or 0),
        ),
        "post_run_preflight_wall_seconds": round(post_run_preflight_seconds, 6),
        "known_teardown_cleanup_wall_seconds": round(known_wall_seconds, 6),
        "teardown_cleanup_wall_seconds": (
            None if teardown_wall_seconds is None else round(teardown_wall_seconds, 6)
        ),
        "teardown_cleanup_wall_time_complete": host_artifact_cleanup_seconds is not None,
        "known_wall_time_note": (
            "node_local_rollback_plus_host_rollbacks_plus_post_run_preflight; "
            "host_artifact_cleanup_wall_time_recorded_when_available"
        ),
    }


def _event_metrics(events: List[Dict[str, Any]], event_type: str) -> Dict[str, Any]:
    selected = [event for event in events if str(event.get("event_type")) == event_type]
    live_selected = [
        event
        for event in selected
        if event.get("segment_epoch_key") is not None or event.get("epoch_id") is not None
    ]
    if live_selected:
        selected = live_selected
    deduped: Dict[str, Dict[str, Any]] = {}
    for index, event in enumerate(selected):
        event_key = str(
            event.get("segment_epoch_key")
            or event.get("epoch_id")
            or event.get("sequence_index")
            or f"{event_type}:{index}"
        )
        deduped[event_key] = event
    selected = [deduped[key] for key in sorted(deduped)]
    lag_values = [
        float(event.get("activation_lag_seconds", 0.0))
        for event in selected
        if event.get("activation_lag_seconds") is not None
    ]
    wall_values = [
        float(event.get("transition_wall_seconds", 0.0))
        for event in selected
        if event.get("transition_wall_seconds") is not None
    ]
    return {
        "count": len(selected),
        "activation_lag_seconds": _stats(lag_values),
        "transition_wall_seconds": _stats(wall_values),
    }


def _event_type_counts(events: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for event in events:
        name = str(event.get("event_type", "unknown"))
        counts[name] = counts.get(name, 0) + 1
    return {name: counts[name] for name in sorted(counts)}


def _runtime_zone_return_boundary_summary(runtime_report: Dict[str, Any]) -> Dict[str, Any]:
    boundaries: List[Dict[str, Any]] = []
    for payload in _iter_dicts(runtime_report):
        boundary = payload.get("zone_return_install_boundary")
        if isinstance(boundary, dict):
            boundaries.append(dict(boundary))
    all_target_keys: set[str] = set()
    reapplied_target_keys: set[str] = set()
    install_behaviors: set[str] = set()
    shared_zone_plan_behaviors: set[str] = set()
    max_all_intents = 0
    max_reapplied_intents = 0
    max_all_shared_zone_return_install_plans = 0
    max_reapplied_shared_zone_return_install_plans = 0
    max_all_current_backbone_items = 0
    max_all_target_backbone_items = 0
    max_all_backbone_ratio = 0.0
    max_all_candidate_zone_routes = 0
    max_all_candidate_label_stacks = 0
    max_all_reverse_to_candidate_ratio = 0.0
    max_reapplied_current_backbone_items = 0
    max_reapplied_target_backbone_items = 0
    max_reapplied_backbone_ratio = 0.0
    max_reapplied_candidate_zone_routes = 0
    max_reapplied_candidate_label_stacks = 0
    max_reapplied_reverse_to_candidate_ratio = 0.0
    for boundary in boundaries:
        behavior = str(boundary.get("install_behavior") or "").strip()
        if behavior:
            install_behaviors.add(behavior)
        plan_behavior = str(
            boundary.get("shared_zone_return_install_plan_behavior") or ""
        ).strip()
        if plan_behavior:
            shared_zone_plan_behaviors.add(plan_behavior)
        all_intents = [
            dict(intent)
            for intent in list(boundary.get("all_intents", []))
            if isinstance(intent, dict)
        ]
        reapplied_intents = [
            dict(intent)
            for intent in list(boundary.get("reapplied_intents", []))
            if isinstance(intent, dict)
        ]
        all_shared_zone_return_install_plans = [
            dict(plan)
            for plan in list(boundary.get("all_shared_zone_return_install_plans", []))
            if isinstance(plan, dict)
        ]
        reapplied_shared_zone_return_install_plans = [
            dict(plan)
            for plan in list(
                boundary.get("reapplied_shared_zone_return_install_plans", [])
            )
            if isinstance(plan, dict)
        ]
        max_all_intents = max(
            max_all_intents,
            int(boundary.get("all_intent_count", len(all_intents)) or 0),
        )
        max_reapplied_intents = max(
            max_reapplied_intents,
            int(boundary.get("reapplied_intent_count", len(reapplied_intents)) or 0),
        )
        max_all_shared_zone_return_install_plans = max(
            max_all_shared_zone_return_install_plans,
            int(
                boundary.get(
                    "all_shared_zone_return_install_plan_count",
                    len(all_shared_zone_return_install_plans),
                )
                or 0
            ),
        )
        max_reapplied_shared_zone_return_install_plans = max(
            max_reapplied_shared_zone_return_install_plans,
            int(
                boundary.get(
                    "reapplied_shared_zone_return_install_plan_count",
                    len(reapplied_shared_zone_return_install_plans),
                )
                or 0
            ),
        )
        scale_summary = dict(boundary.get("scale_summary", {}))
        all_scale = dict(scale_summary.get("all", {}))
        reapplied_scale = dict(scale_summary.get("reapplied", {}))
        max_all_current_backbone_items = max(
            max_all_current_backbone_items,
            int(all_scale.get("current_backbone_return_state_item_count", 0) or 0),
        )
        max_all_target_backbone_items = max(
            max_all_target_backbone_items,
            int(all_scale.get("target_shared_backbone_return_state_item_count", 0) or 0),
        )
        max_all_backbone_ratio = max(
            max_all_backbone_ratio,
            float(all_scale.get("current_to_target_backbone_state_ratio", 0.0) or 0.0),
        )
        max_all_candidate_zone_routes = max(
            max_all_candidate_zone_routes,
            int(all_scale.get("candidate_shared_zone_route_count", 0) or 0),
        )
        max_all_candidate_label_stacks = max(
            max_all_candidate_label_stacks,
            int(all_scale.get("candidate_shared_label_stack_count", 0) or 0),
        )
        max_all_reverse_to_candidate_ratio = max(
            max_all_reverse_to_candidate_ratio,
            float(
                all_scale.get(
                    "current_reverse_labels_to_candidate_label_stack_ratio",
                    0.0,
                )
                or 0.0
            ),
        )
        max_reapplied_current_backbone_items = max(
            max_reapplied_current_backbone_items,
            int(reapplied_scale.get("current_backbone_return_state_item_count", 0) or 0),
        )
        max_reapplied_target_backbone_items = max(
            max_reapplied_target_backbone_items,
            int(
                reapplied_scale.get(
                    "target_shared_backbone_return_state_item_count",
                    0,
                )
                or 0
            ),
        )
        max_reapplied_backbone_ratio = max(
            max_reapplied_backbone_ratio,
            float(
                reapplied_scale.get(
                    "current_to_target_backbone_state_ratio",
                    0.0,
                )
                or 0.0
            ),
        )
        max_reapplied_candidate_zone_routes = max(
            max_reapplied_candidate_zone_routes,
            int(reapplied_scale.get("candidate_shared_zone_route_count", 0) or 0),
        )
        max_reapplied_candidate_label_stacks = max(
            max_reapplied_candidate_label_stacks,
            int(reapplied_scale.get("candidate_shared_label_stack_count", 0) or 0),
        )
        max_reapplied_reverse_to_candidate_ratio = max(
            max_reapplied_reverse_to_candidate_ratio,
            float(
                reapplied_scale.get(
                    "current_reverse_labels_to_candidate_label_stack_ratio",
                    0.0,
                )
                or 0.0
            ),
        )
        all_target_keys.update(
            str(intent.get("target_key"))
            for intent in all_intents
            if intent.get("target_key")
        )
        reapplied_target_keys.update(
            str(intent.get("target_key"))
            for intent in reapplied_intents
            if intent.get("target_key")
        )
    return {
        "present": bool(boundaries),
        "boundary_record_count": len(boundaries),
        "install_behaviors": sorted(install_behaviors),
        "shared_zone_return_install_plan_behaviors": sorted(
            shared_zone_plan_behaviors
        ),
        "max_all_intent_count": max_all_intents,
        "max_reapplied_intent_count": max_reapplied_intents,
        "max_all_shared_zone_return_install_plan_count": max_all_shared_zone_return_install_plans,
        "max_reapplied_shared_zone_return_install_plan_count": max_reapplied_shared_zone_return_install_plans,
        "max_all_current_backbone_return_state_item_count": max_all_current_backbone_items,
        "max_all_target_shared_backbone_return_state_item_count": max_all_target_backbone_items,
        "max_all_current_to_target_backbone_state_ratio": max_all_backbone_ratio,
        "max_all_candidate_shared_zone_route_count": max_all_candidate_zone_routes,
        "max_all_candidate_shared_label_stack_count": max_all_candidate_label_stacks,
        "max_all_reverse_labels_to_candidate_label_stack_ratio": max_all_reverse_to_candidate_ratio,
        "max_reapplied_current_backbone_return_state_item_count": max_reapplied_current_backbone_items,
        "max_reapplied_target_shared_backbone_return_state_item_count": max_reapplied_target_backbone_items,
        "max_reapplied_current_to_target_backbone_state_ratio": max_reapplied_backbone_ratio,
        "max_reapplied_candidate_shared_zone_route_count": max_reapplied_candidate_zone_routes,
        "max_reapplied_candidate_shared_label_stack_count": max_reapplied_candidate_label_stacks,
        "max_reapplied_reverse_labels_to_candidate_label_stack_ratio": max_reapplied_reverse_to_candidate_ratio,
        "unique_all_target_key_count": len(all_target_keys),
        "unique_reapplied_target_key_count": len(reapplied_target_keys),
        "unique_all_target_keys": sorted(all_target_keys),
        "unique_reapplied_target_keys": sorted(reapplied_target_keys),
    }


def _runtime_ping_validation_summary(runtime_report: Dict[str, Any]) -> Dict[str, Any]:
    selections: List[Dict[str, Any]] = []
    for payload in _iter_dicts(runtime_report):
        selection = payload.get("ping_validation_selection")
        if isinstance(selection, dict):
            selections.append(dict(selection))

    scopes: set[str] = set()
    sample_sources: set[str] = set()
    fallback_reasons: set[str] = set()
    max_active_flows = 0
    max_reapplied_flows = 0
    max_sample_source_flows = 0
    max_selected_flows = 0
    max_sample_source_gateway_classes = 0
    max_selected_gateway_classes = 0
    max_reduction_count = 0
    ratio_values: List[float] = []

    for selection in selections:
        scope = str(selection.get("validation_scope") or "").strip()
        if scope:
            scopes.add(scope)
        sample_source = str(selection.get("sample_source") or "").strip()
        if sample_source:
            sample_sources.add(sample_source)
        fallback_reason = str(selection.get("fallback_reason") or "").strip()
        if fallback_reason and fallback_reason != "None":
            fallback_reasons.add(fallback_reason)

        selected_flow_count = int(
            selection.get("selected_flow_count")
            or len(list(selection.get("target_flow_ids", [])))
            or 0
        )
        max_active_flows = max(
            max_active_flows,
            int(selection.get("active_flow_count", 0) or 0),
        )
        max_reapplied_flows = max(
            max_reapplied_flows,
            int(selection.get("reapplied_flow_count", 0) or 0),
        )
        max_sample_source_flows = max(
            max_sample_source_flows,
            int(selection.get("sample_source_flow_count", selected_flow_count) or 0),
        )
        max_selected_flows = max(max_selected_flows, selected_flow_count)
        max_sample_source_gateway_classes = max(
            max_sample_source_gateway_classes,
            int(selection.get("sample_source_gateway_class_count", 0) or 0),
        )
        max_selected_gateway_classes = max(
            max_selected_gateway_classes,
            int(selection.get("selected_gateway_class_count", 0) or 0),
        )
        max_reduction_count = max(
            max_reduction_count,
            int(selection.get("gateway_class_sample_reduction_count", 0) or 0),
        )
        if selection.get("selected_to_sample_source_flow_ratio") is not None:
            ratio_values.append(
                float(selection.get("selected_to_sample_source_flow_ratio") or 0.0)
            )

    return {
        "present": bool(selections),
        "selection_record_count": len(selections),
        "validation_scopes": sorted(scopes),
        "sample_sources": sorted(sample_sources),
        "fallback_reasons": sorted(fallback_reasons),
        "max_active_flow_count": max_active_flows,
        "max_reapplied_flow_count": max_reapplied_flows,
        "max_sample_source_flow_count": max_sample_source_flows,
        "max_selected_flow_count": max_selected_flows,
        "max_sample_source_gateway_class_count": max_sample_source_gateway_classes,
        "max_selected_gateway_class_count": max_selected_gateway_classes,
        "max_gateway_class_sample_reduction_count": max_reduction_count,
        "min_selected_to_sample_source_flow_ratio": min(ratio_values)
        if ratio_values
        else 0.0,
        "max_selected_to_sample_source_flow_ratio": max(ratio_values)
        if ratio_values
        else 0.0,
    }


def _runtime_incremental_reuse_summary(runtime_report: Dict[str, Any]) -> Dict[str, Any]:
    slices: List[Dict[str, Any]] = []
    for payload in _iter_dicts(runtime_report):
        target_slice = payload.get("target_slice")
        if isinstance(target_slice, dict) and any(
            key in target_slice
            for key in (
                "executed_flow_ids",
                "reapplied_flow_ids",
                "held_flow_ids",
                "incremental_cleanup_flow_ids",
            )
        ):
            slices.append(dict(target_slice))

    max_active_flows = 0
    max_reapplied_flows = 0
    max_held_flows = 0
    max_cleanup_flows = 0
    max_reapply_reduction = 0
    reapply_ratios: List[float] = []
    held_ratios: List[float] = []
    slices_with_holds = 0
    slices_with_cleanup = 0

    for target_slice in slices:
        active_flow_count = int(
            target_slice.get("active_flow_count")
            or len(list(target_slice.get("executed_flow_ids", [])))
            or 0
        )
        reapplied_flow_count = int(
            target_slice.get("reapplied_flow_count")
            or len(list(target_slice.get("reapplied_flow_ids", [])))
            or 0
        )
        held_flow_count = int(
            target_slice.get("held_flow_count")
            or len(list(target_slice.get("held_flow_ids", [])))
            or 0
        )
        cleanup_flow_count = int(
            target_slice.get("incremental_cleanup_flow_count")
            or len(list(target_slice.get("incremental_cleanup_flow_ids", [])))
            or 0
        )
        max_active_flows = max(max_active_flows, active_flow_count)
        max_reapplied_flows = max(max_reapplied_flows, reapplied_flow_count)
        max_held_flows = max(max_held_flows, held_flow_count)
        max_cleanup_flows = max(max_cleanup_flows, cleanup_flow_count)
        max_reapply_reduction = max(
            max_reapply_reduction,
            max(0, active_flow_count - reapplied_flow_count),
        )
        if held_flow_count > 0:
            slices_with_holds += 1
        if cleanup_flow_count > 0:
            slices_with_cleanup += 1
        if active_flow_count > 0:
            reapply_ratios.append(
                round(float(reapplied_flow_count) / float(active_flow_count), 6)
            )
            held_ratios.append(
                round(float(held_flow_count) / float(active_flow_count), 6)
            )

    return {
        "present": bool(slices),
        "slice_record_count": len(slices),
        "max_active_flow_count": max_active_flows,
        "max_reapplied_flow_count": max_reapplied_flows,
        "max_held_flow_count": max_held_flows,
        "max_incremental_cleanup_flow_count": max_cleanup_flows,
        "max_reapply_reduction_count": max_reapply_reduction,
        "slice_count_with_held_flows": slices_with_holds,
        "slice_count_with_incremental_cleanup": slices_with_cleanup,
        "min_reapplied_to_active_flow_ratio": min(reapply_ratios)
        if reapply_ratios
        else 0.0,
        "max_reapplied_to_active_flow_ratio": max(reapply_ratios)
        if reapply_ratios
        else 0.0,
        "max_held_to_active_flow_ratio": max(held_ratios)
        if held_ratios
        else 0.0,
    }


def _return_path_projection(report: Dict[str, Any], projected_flow_count: int = 10000) -> Dict[str, Any]:
    return dict(
        dict(dict(report.get("scale_projection", {})).get("projections", {})).get(
            str(projected_flow_count),
            {},
        )
    )


def _return_path_migration_blocker_count(report: Dict[str, Any]) -> int:
    if report.get("return_path_migration_blocker_count") is not None:
        return int(report.get("return_path_migration_blocker_count") or 0)
    blocker_state_keys = (
        "gateway_ut32_return_route_count",
        "reverse_label_count",
        "reverse_transit_lfib_entry_count",
        "serving_satellite_ut32_delivery_route_count",
        "gateway_nat_forward_rule_count",
    )
    blocker_ids: set[str] = set()
    key_to_blocker_id = {
        "gateway_ut32_return_route_count": "gateway_ut32_return_route",
        "reverse_label_count": "reverse_lfib_label_stack",
        "reverse_transit_lfib_entry_count": "reverse_transit_lfib_entry",
        "serving_satellite_ut32_delivery_route_count": "serving_satellite_ut32_delivery_route",
        "gateway_nat_forward_rule_count": "gateway_nat_forward_rules",
    }
    for snapshot in list(report.get("snapshot_reports", [])):
        per_flow_state = dict(dict(snapshot).get("per_flow_return_state", {}))
        for state_key in blocker_state_keys:
            if int(per_flow_state.get(state_key, 0) or 0) > 0:
                blocker_ids.add(key_to_blocker_id[state_key])
    return len(blocker_ids)


def _return_path_backfilled_migration_blockers(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    key_to_blocker = {
        "gateway_ut32_return_route_count": (
            "gateway_ut32_return_route",
            "backbone_return",
        ),
        "reverse_label_count": (
            "reverse_lfib_label_stack",
            "backbone_return",
        ),
        "reverse_transit_lfib_entry_count": (
            "reverse_transit_lfib_entry",
            "backbone_return",
        ),
        "serving_satellite_ut32_delivery_route_count": (
            "serving_satellite_ut32_delivery_route",
            "edge_local_delivery",
        ),
        "gateway_nat_forward_rule_count": (
            "gateway_nat_forward_rules",
            "gateway_edge_policy",
        ),
    }
    totals: Dict[str, Dict[str, Any]] = {}
    for snapshot in list(report.get("snapshot_reports", [])):
        per_flow_state = dict(dict(snapshot).get("per_flow_return_state", {}))
        for state_key, (blocker_id, category) in key_to_blocker.items():
            current_item_count = int(per_flow_state.get(state_key, 0) or 0)
            if current_item_count <= 0:
                continue
            blocker = totals.setdefault(
                blocker_id,
                {
                    "blocker_id": blocker_id,
                    "migration_category": category,
                    "total_current_item_count": 0,
                },
            )
            blocker["total_current_item_count"] = (
                int(blocker["total_current_item_count"]) + current_item_count
            )
    return [totals[blocker_id] for blocker_id in sorted(totals)]


def _return_path_migration_category_total(report: Dict[str, Any], category: str) -> int:
    category_total = dict(
        dict(report.get("return_path_migration_category_totals", {})).get(
            category,
            {},
        )
    )
    if category_total.get("total_current_item_count") is not None:
        return int(category_total.get("total_current_item_count") or 0)
    return sum(
        int(blocker.get("total_current_item_count", 0) or 0)
        for blocker in _return_path_backfilled_migration_blockers(report)
        if str(blocker.get("migration_category") or "") == category
    )


def _return_path_top_migration_blocker(report: Dict[str, Any]) -> str:
    blockers = [
        dict(blocker)
        for blocker in list(report.get("return_path_migration_blockers", []))
        if isinstance(blocker, dict)
    ]
    if not blockers:
        blockers = _return_path_backfilled_migration_blockers(report)
    if not blockers:
        return ""
    top = max(
        blockers,
        key=lambda blocker: (
            int(blocker.get("total_current_item_count", 0) or 0),
            str(blocker.get("blocker_id") or ""),
        ),
    )
    blocker_id = str(top.get("blocker_id") or "")
    if not blocker_id:
        return ""
    return f"{blocker_id}:{int(top.get('total_current_item_count', 0) or 0)}"


def _return_path_top_migration_blocker_for_category(
    report: Dict[str, Any],
    category: str,
) -> str:
    blockers = [
        dict(blocker)
        for blocker in list(report.get("return_path_migration_blockers", []))
        if isinstance(blocker, dict)
    ]
    if not blockers:
        blockers = _return_path_backfilled_migration_blockers(report)
    blockers = [
        blocker
        for blocker in blockers
        if str(blocker.get("migration_category") or "") == category
    ]
    if not blockers:
        return ""
    top = max(
        blockers,
        key=lambda blocker: (
            int(blocker.get("total_current_item_count", 0) or 0),
            str(blocker.get("blocker_id") or ""),
        ),
    )
    blocker_id = str(top.get("blocker_id") or "")
    if not blocker_id:
        return ""
    return f"{blocker_id}:{int(top.get('total_current_item_count', 0) or 0)}"


def _return_path_total_current_backbone_items(report: Dict[str, Any]) -> int:
    if report.get("total_current_backbone_return_state_item_count") is not None:
        return int(report.get("total_current_backbone_return_state_item_count") or 0)
    total = 0
    for snapshot in list(report.get("snapshot_reports", [])):
        scale_model = dict(dict(snapshot).get("return_state_scale_model", {}))
        if scale_model.get("current_backbone_return_state_item_count") is not None:
            total += int(scale_model.get("current_backbone_return_state_item_count") or 0)
            continue
        per_flow_state = dict(dict(snapshot).get("per_flow_return_state", {}))
        total += int(per_flow_state.get("gateway_ut32_return_route_count", 0) or 0)
        total += int(per_flow_state.get("reverse_label_count", 0) or 0)
        total += int(per_flow_state.get("reverse_transit_lfib_entry_count", 0) or 0)
    if total > 0:
        return total
    return _return_path_migration_category_total(report, "backbone_return")


def _return_path_total_target_backbone_items(report: Dict[str, Any]) -> int:
    if report.get("total_target_shared_backbone_return_state_item_count") is not None:
        return int(report.get("total_target_shared_backbone_return_state_item_count") or 0)
    total = 0
    for snapshot in list(report.get("snapshot_reports", [])):
        scale_model = dict(dict(snapshot).get("return_state_scale_model", {}))
        if scale_model.get("target_shared_backbone_return_state_item_count") is not None:
            total += int(scale_model.get("target_shared_backbone_return_state_item_count") or 0)
            continue
        shared = dict(dict(snapshot).get("shared_return_targets", {}))
        total += max(
            int(shared.get("gateway_zone_return_target_count", 0) or 0),
            int(shared.get("zone_return_target_count", 0) or 0),
        )
    return total


def _return_path_migration_readiness(report: Dict[str, Any]) -> Dict[str, Any]:
    explicit = dict(report.get("return_path_migration_readiness") or {})
    if explicit:
        return explicit
    total_flow_count = int(report.get("total_flow_count", 0) or 0)
    current_backbone_items = _return_path_total_current_backbone_items(report)
    target_backbone_items = _return_path_total_target_backbone_items(report)
    backbone_blocker_items = _return_path_migration_category_total(
        report,
        "backbone_return",
    )
    if (
        backbone_blocker_items <= 0
        and total_flow_count > 0
        and current_backbone_items > target_backbone_items
    ):
        backbone_blocker_items = current_backbone_items
    backbone_blocker_ids = [
        str(blocker.get("blocker_id"))
        for blocker in (
            list(report.get("return_path_migration_blockers", []))
            or _return_path_backfilled_migration_blockers(report)
        )
        if str(dict(blocker).get("migration_category") or "") == "backbone_return"
        and str(dict(blocker).get("blocker_id") or "")
    ]
    shared_ready = (
        total_flow_count > 0
        and target_backbone_items > 0
        and current_backbone_items <= target_backbone_items
        and backbone_blocker_items == 0
    )
    return {
        "current_realization": "per_ut_flow_compatibility",
        "target_realization": "shared_gateway_zone_or_zone_return_reachability",
        "shared_backbone_return_ready": shared_ready,
        "backbone_return_migration_required": not shared_ready
        and total_flow_count > 0,
        "current_backbone_state_scales_with_flow_count": total_flow_count > 0
        and current_backbone_items > 0,
        "target_backbone_state_independent_of_ut_count": True,
        "current_backbone_return_state_item_count": current_backbone_items,
        "target_shared_backbone_return_state_item_count": target_backbone_items,
        "backbone_return_blocker_item_count": backbone_blocker_items,
        "gateway_edge_policy_item_count": _return_path_migration_category_total(
            report,
            "gateway_edge_policy",
        ),
        "edge_local_delivery_item_count": _return_path_migration_category_total(
            report,
            "edge_local_delivery",
        ),
        "backbone_return_blocker_ids": sorted(set(backbone_blocker_ids)),
        "non_backbone_items_are_separated": True,
    }


def _row_metrics(
    rows: List[Dict[str, Any]],
    *,
    epoch_type: str,
    noop_skipped: bool | None = None,
) -> Dict[str, Any]:
    selected = [
        row
        for row in rows
        if str(row.get("epoch_type")) == epoch_type
        and (noop_skipped is None or bool(row.get("noop_skipped", False)) is noop_skipped)
    ]
    lag_values = [
        float(row.get("activation_lag_seconds", 0.0))
        for row in selected
        if row.get("activation_lag_seconds") is not None
    ]
    wall_values = [
        float(row.get("transition_wall_seconds", 0.0))
        for row in selected
        if row.get("transition_wall_seconds") is not None
    ]
    host_apply_values = [
        float(row.get("host_operation_apply_wall_seconds", 0.0))
        for row in selected
        if row.get("host_operation_apply_wall_seconds") is not None
    ]
    post_apply_refresh_values = [
        float(row.get("post_apply_state_refresh_wall_seconds", 0.0))
        for row in selected
        if row.get("post_apply_state_refresh_wall_seconds") is not None
    ]
    post_apply_verify_values = [
        float(row.get("post_apply_verification_wall_seconds", 0.0))
        for row in selected
        if row.get("post_apply_verification_wall_seconds") is not None
    ]
    return {
        "count": len(selected),
        "activation_lag_seconds": _stats(lag_values),
        "transition_wall_seconds": _stats(wall_values),
        "host_operation_apply_wall_seconds": _stats(host_apply_values),
        "post_apply_state_refresh_wall_seconds": _stats(post_apply_refresh_values),
        "post_apply_verification_wall_seconds": _stats(post_apply_verify_values),
    }


def inspect_temporal_run(
    run_dir: Path,
    *,
    stall_threshold_seconds: float = DEFAULT_STALL_THRESHOLD_SECONDS,
) -> Dict[str, Any]:
    run_dir = run_dir.expanduser().resolve()
    artifacts = _artifact_report(run_dir)
    progress = _read_json(run_dir / "runtime_progress.json")
    timing = _read_json(run_dir / "timing.json")
    runtime_report = _read_json(run_dir / "real_runtime_report.json")
    temporal_audit = _read_json(run_dir / "temporal_audit.json")
    return_path_audit = _read_json(run_dir / "return_path_audit.json")
    selected_return_path_audit = dict(return_path_audit.get("selected_runtime_scope", {}))
    return_path_projection_10000 = _return_path_projection(return_path_audit, 10000)
    selected_return_path_projection_10000 = _return_path_projection(
        selected_return_path_audit,
        10000,
    )
    return_path_migration_blocker_count = _return_path_migration_blocker_count(
        return_path_audit
    )
    return_path_top_migration_blocker = _return_path_top_migration_blocker(
        return_path_audit
    )
    return_path_top_backbone_migration_blocker = (
        _return_path_top_migration_blocker_for_category(
            return_path_audit,
            "backbone_return",
        )
    )
    return_path_backbone_return_items = _return_path_migration_category_total(
        return_path_audit,
        "backbone_return",
    )
    return_path_gateway_edge_policy_items = _return_path_migration_category_total(
        return_path_audit,
        "gateway_edge_policy",
    )
    return_path_edge_local_delivery_items = _return_path_migration_category_total(
        return_path_audit,
        "edge_local_delivery",
    )
    return_path_readiness = _return_path_migration_readiness(return_path_audit)
    selected_return_path_readiness = _return_path_migration_readiness(
        selected_return_path_audit
    )
    run_manifest = _read_json(run_dir / "run_manifest.json")
    events = _read_events(run_dir / "events.jsonl")

    phase = str(progress.get("current_phase", "unknown"))
    seconds_since_progress = float(progress.get("seconds_since_progress", 0.0) or 0.0)
    live_runtime_complete_inferred = phase.startswith("runtime_rollback") or phase == "completed"
    checkpoint_finalized = {
        "timing": timing.get("finalized"),
        "real_runtime_report": runtime_report.get("finalized"),
        "temporal_audit": temporal_audit.get("finalized"),
    }
    timing_rows = list(timing.get("rows", [])) if isinstance(timing.get("rows"), list) else []
    metric_source = "timing_rows" if timing_rows else "events"
    run_manifest_present = bool(run_manifest)
    legacy_final_artifacts_complete = (
        run_manifest_present
        and bool(artifacts["timing.json"]["exists"])
        and bool(artifacts["temporal_audit.json"]["exists"])
        and phase == "completed"
    )

    runtime_epoch_metrics = (
        _row_metrics(timing_rows, epoch_type="runtime_epoch")
        if timing_rows
        else _event_metrics(events, "runtime_epoch_result")
    )
    parameter_epoch_metrics = (
        _row_metrics(timing_rows, epoch_type="parameter_epoch", noop_skipped=False)
        if timing_rows
        else _event_metrics(events, "parameter_epoch_result")
    )
    parameter_epoch_noop_metrics = (
        _row_metrics(timing_rows, epoch_type="parameter_epoch", noop_skipped=True)
        if timing_rows
        else _event_metrics(events, "parameter_epoch_noop_skipped")
    )
    timing_analysis = dict(timing.get("timing_analysis", {}))
    steady_runtime_analysis = dict(timing_analysis.get("steady_state_runtime_epochs", {}))
    if steady_runtime_analysis.get("source_runtime_epoch_count") is None:
        runtime_metric_count = dict(
            dict(runtime_epoch_metrics).get("activation_lag_seconds", {})
        ).get("count")
        if runtime_metric_count is None:
            runtime_metric_count = runtime_epoch_metrics.get("count")
        if runtime_metric_count is not None:
            steady_runtime_analysis["source_runtime_epoch_count"] = int(
                runtime_metric_count or 0
            )
    if steady_runtime_analysis.get("excluded_initial_runtime_epoch_count") is None:
        source_count = int(steady_runtime_analysis.get("source_runtime_epoch_count", 0) or 0)
        steady_count = int(steady_runtime_analysis.get("count", 0) or 0)
        steady_runtime_analysis["excluded_initial_runtime_epoch_count"] = max(
            0,
            source_count - steady_count,
        )
    if steady_runtime_analysis:
        timing_analysis["steady_state_runtime_epochs"] = steady_runtime_analysis
    if not isinstance(timing_analysis.get("initial_activation"), dict):
        timing_analysis["initial_activation"] = _initial_activation_from_timing_analysis(
            timing_analysis
        )

    summary = {
        "run_dir": str(run_dir),
        "artifacts": artifacts,
        "timing_analysis": timing_analysis,
        "timing_summary": dict(timing.get("timing_summary", {})),
        "runtime_zone_return_boundary_summary": _runtime_zone_return_boundary_summary(
            runtime_report
        ),
        "runtime_ping_validation_summary": _runtime_ping_validation_summary(
            runtime_report
        ),
        "runtime_incremental_reuse_summary": _runtime_incremental_reuse_summary(
            runtime_report
        ),
        "teardown_cleanup_summary": _teardown_cleanup_summary(
            runtime_report=runtime_report,
            timing=timing,
        ),
        "return_path_audit_summary": {
            "present": bool(return_path_audit),
            "snapshot_count": return_path_audit.get("snapshot_count"),
            "total_flow_count": return_path_audit.get("total_flow_count"),
            "total_return_path_intent_count": return_path_audit.get(
                "total_return_path_intent_count"
            ),
            "current_return_state_scales_with_flow_count": return_path_audit.get(
                "current_return_state_scales_with_flow_count"
            ),
            "shared_target_reduction_available": return_path_audit.get(
                "shared_target_reduction_available"
            ),
            "total_current_backbone_return_state_item_count": return_path_audit.get(
                "total_current_backbone_return_state_item_count"
            ),
            "total_target_shared_backbone_return_state_item_count": return_path_audit.get(
                "total_target_shared_backbone_return_state_item_count"
            ),
            "current_to_target_backbone_state_ratio": return_path_audit.get(
                "current_to_target_backbone_state_ratio"
            ),
            "return_path_migration_blocker_count": return_path_audit.get(
                "return_path_migration_blocker_count"
            )
            if return_path_audit.get("return_path_migration_blocker_count") is not None
            else return_path_migration_blocker_count,
            "return_path_migration_blocker_ids": return_path_audit.get(
                "return_path_migration_blocker_ids"
            ),
            "return_path_top_migration_blocker": return_path_top_migration_blocker,
            "return_path_top_backbone_migration_blocker": return_path_top_backbone_migration_blocker,
            "return_path_backbone_return_item_count": return_path_backbone_return_items,
            "return_path_gateway_edge_policy_item_count": return_path_gateway_edge_policy_items,
            "return_path_edge_local_delivery_item_count": return_path_edge_local_delivery_items,
            "return_path_shared_backbone_ready": return_path_readiness.get(
                "shared_backbone_return_ready"
            ),
            "return_path_backbone_migration_required": return_path_readiness.get(
                "backbone_return_migration_required"
            ),
            "return_path_backbone_blocker_item_count": return_path_readiness.get(
                "backbone_return_blocker_item_count"
            ),
            "return_path_current_realization": return_path_readiness.get(
                "current_realization"
            ),
            "return_path_target_realization": return_path_readiness.get(
                "target_realization"
            ),
            "projection_10000_current_backbone_items": return_path_projection_10000.get(
                "current_per_flow_compatibility_backbone_items"
            ),
            "projection_10000_target_constant_backbone_items": return_path_projection_10000.get(
                "target_shared_backbone_items_if_target_count_constant"
            ),
            "projection_10000_target_ratio_scaled_backbone_items": return_path_projection_10000.get(
                "target_shared_backbone_items_if_observed_target_ratio_scales"
            ),
            "projection_10000_ratio_constant": return_path_projection_10000.get(
                "current_to_constant_target_ratio"
            ),
            "projection_10000_ratio_scaled": return_path_projection_10000.get(
                "current_to_ratio_scaled_target_ratio"
            ),
            "selected_runtime_snapshot_count": selected_return_path_audit.get(
                "snapshot_count"
            ),
            "selected_runtime_total_flow_count": selected_return_path_audit.get(
                "total_flow_count"
            ),
            "selected_runtime_return_path_intent_count": selected_return_path_audit.get(
                "total_return_path_intent_count"
            ),
            "selected_runtime_per_flow_state": selected_return_path_audit.get(
                "current_return_state_scales_with_flow_count"
            ),
            "selected_runtime_shared_reduction_available": selected_return_path_audit.get(
                "shared_target_reduction_available"
            ),
            "selected_runtime_current_backbone_return_state_item_count": selected_return_path_audit.get(
                "total_current_backbone_return_state_item_count"
            ),
            "selected_runtime_target_shared_backbone_return_state_item_count": selected_return_path_audit.get(
                "total_target_shared_backbone_return_state_item_count"
            ),
            "selected_runtime_current_to_target_backbone_state_ratio": selected_return_path_audit.get(
                "current_to_target_backbone_state_ratio"
            ),
            "selected_runtime_shared_backbone_ready": selected_return_path_readiness.get(
                "shared_backbone_return_ready"
            ),
            "selected_runtime_backbone_migration_required": selected_return_path_readiness.get(
                "backbone_return_migration_required"
            ),
            "selected_runtime_backbone_blocker_item_count": selected_return_path_readiness.get(
                "backbone_return_blocker_item_count"
            ),
            "selected_runtime_projection_10000_current_backbone_items": selected_return_path_projection_10000.get(
                "current_per_flow_compatibility_backbone_items"
            ),
            "selected_runtime_projection_10000_target_constant_backbone_items": selected_return_path_projection_10000.get(
                "target_shared_backbone_items_if_target_count_constant"
            ),
            "selected_runtime_projection_10000_ratio_constant": selected_return_path_projection_10000.get(
                "current_to_constant_target_ratio"
            ),
        },
        "progress": {
            "phase": phase,
            "last_event_type": progress.get("last_event_type"),
            "runtime_epochs_completed": int(progress.get("runtime_epochs_completed", 0) or 0),
            "parameter_epochs_completed": int(progress.get("parameter_epochs_completed", 0) or 0),
            "seconds_since_progress": round(seconds_since_progress, 6),
            "stalled": seconds_since_progress >= float(stall_threshold_seconds),
            "heartbeat_utc": progress.get("heartbeat_utc"),
            "last_progress_utc": progress.get("last_progress_utc"),
            "phase_detail": dict(progress.get("phase_detail", {})),
        },
        "event_summary": {
            "metric_source": metric_source,
            "total_event_count": len(events),
            "last_event_type": events[-1].get("event_type") if events else None,
            "last_event_timestamp_utc": events[-1].get("timestamp_utc") if events else None,
            "event_type_counts": _event_type_counts(events),
            "runtime_epoch_results": runtime_epoch_metrics,
            "parameter_epoch_results": parameter_epoch_metrics,
            "parameter_epoch_noop_skipped": parameter_epoch_noop_metrics,
            "horizon_replenishment_completed_count": sum(
                1
                for event in events
                if str(event.get("event_type")) == "horizon_replenishment_completed"
            ),
        },
        "checkpoint_status": {
            "run_manifest_present": run_manifest_present,
            "finalized": checkpoint_finalized,
        },
        "inference": {
            "live_runtime_complete_inferred": live_runtime_complete_inferred,
            "rollback_in_progress": phase.startswith("runtime_rollback"),
            "final_artifacts_complete": run_manifest_present
            and (
                (
                    checkpoint_finalized["timing"] is True
                    and checkpoint_finalized["temporal_audit"] is True
                )
                or legacy_final_artifacts_complete
            ),
        },
    }
    return summary


def render_temporal_run_json(report: Dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True)


def render_temporal_run_summary(report: Dict[str, Any]) -> str:
    progress = dict(report.get("progress", {}))
    event_summary = dict(report.get("event_summary", {}))
    runtime_metrics = dict(event_summary.get("runtime_epoch_results", {}))
    parameter_metrics = dict(event_summary.get("parameter_epoch_results", {}))
    noop_metrics = dict(event_summary.get("parameter_epoch_noop_skipped", {}))
    checkpoint_status = dict(report.get("checkpoint_status", {}))
    inference = dict(report.get("inference", {}))
    timing_analysis = dict(report.get("timing_analysis", {}))
    startup = dict(timing_analysis.get("startup_preparation", {}))
    initial_activation = dict(timing_analysis.get("initial_activation", {}))
    first_runtime = dict(timing_analysis.get("first_runtime_epoch", {}))
    steady_runtime = dict(timing_analysis.get("steady_state_runtime_epochs", {}))
    return_path_audit = dict(report.get("return_path_audit_summary", {}))
    zone_return_boundary = dict(report.get("runtime_zone_return_boundary_summary", {}))
    ping_validation = dict(report.get("runtime_ping_validation_summary", {}))
    incremental_reuse = dict(report.get("runtime_incremental_reuse_summary", {}))
    teardown_cleanup = dict(report.get("teardown_cleanup_summary", {}))

    lines = [
        f"Run dir: {report.get('run_dir')}",
        (
            "Progress: "
            f"phase={progress.get('phase')} "
            f"runtime_epochs={progress.get('runtime_epochs_completed')} "
            f"parameter_epochs={progress.get('parameter_epochs_completed')} "
            f"seconds_since_progress={progress.get('seconds_since_progress')} "
            f"stalled={progress.get('stalled')}"
        ),
        (
            "Inference: "
            f"live_runtime_complete_inferred={inference.get('live_runtime_complete_inferred')} "
            f"rollback_in_progress={inference.get('rollback_in_progress')} "
            f"final_artifacts_complete={inference.get('final_artifacts_complete')}"
        ),
        (
            "Checkpoints: "
            f"run_manifest_present={checkpoint_status.get('run_manifest_present')} "
            f"timing_finalized={dict(checkpoint_status.get('finalized', {})).get('timing')} "
            f"runtime_report_finalized={dict(checkpoint_status.get('finalized', {})).get('real_runtime_report')} "
            f"temporal_audit_finalized={dict(checkpoint_status.get('finalized', {})).get('temporal_audit')}"
        ),
        (
            "Events: "
            f"total={event_summary.get('total_event_count')} "
            f"metric_source={event_summary.get('metric_source')} "
            f"last={event_summary.get('last_event_type')} "
            f"replenishments={event_summary.get('horizon_replenishment_completed_count')}"
        ),
        (
            "Return path audit: "
            f"present={return_path_audit.get('present')} "
            f"snapshots={return_path_audit.get('snapshot_count')} "
            f"flows={return_path_audit.get('total_flow_count')} "
            f"return_intents={return_path_audit.get('total_return_path_intent_count')} "
            f"per_flow_state={return_path_audit.get('current_return_state_scales_with_flow_count')} "
            f"shared_reduction_available={return_path_audit.get('shared_target_reduction_available')} "
            f"backbone_items={return_path_audit.get('total_current_backbone_return_state_item_count')} "
            f"target_backbone_items={return_path_audit.get('total_target_shared_backbone_return_state_item_count')} "
            f"backbone_ratio={return_path_audit.get('current_to_target_backbone_state_ratio')} "
            f"shared_backbone_ready={return_path_audit.get('return_path_shared_backbone_ready')} "
            f"backbone_migration_required={return_path_audit.get('return_path_backbone_migration_required')} "
            f"backbone_blocker_items={return_path_audit.get('return_path_backbone_blocker_item_count')} "
            f"migration_blockers={return_path_audit.get('return_path_migration_blocker_count')} "
            f"backbone_return_items={return_path_audit.get('return_path_backbone_return_item_count')} "
            f"gateway_edge_policy_items={return_path_audit.get('return_path_gateway_edge_policy_item_count')} "
            f"edge_local_delivery_items={return_path_audit.get('return_path_edge_local_delivery_item_count')} "
            f"top_migration_blocker={return_path_audit.get('return_path_top_migration_blocker')} "
            f"top_backbone_migration_blocker={return_path_audit.get('return_path_top_backbone_migration_blocker')} "
            f"projection_10000_current={return_path_audit.get('projection_10000_current_backbone_items')} "
            f"projection_10000_target_constant={return_path_audit.get('projection_10000_target_constant_backbone_items')} "
            f"projection_10000_ratio_constant={return_path_audit.get('projection_10000_ratio_constant')} "
            f"selected_runtime_snapshots={return_path_audit.get('selected_runtime_snapshot_count')} "
            f"selected_runtime_flows={return_path_audit.get('selected_runtime_total_flow_count')} "
            f"selected_runtime_per_flow_state={return_path_audit.get('selected_runtime_per_flow_state')} "
            f"selected_runtime_backbone_items={return_path_audit.get('selected_runtime_current_backbone_return_state_item_count')} "
            f"selected_runtime_target_backbone_items={return_path_audit.get('selected_runtime_target_shared_backbone_return_state_item_count')} "
            f"selected_runtime_backbone_ratio={return_path_audit.get('selected_runtime_current_to_target_backbone_state_ratio')} "
            f"selected_runtime_shared_backbone_ready={return_path_audit.get('selected_runtime_shared_backbone_ready')} "
            f"selected_runtime_backbone_migration_required={return_path_audit.get('selected_runtime_backbone_migration_required')} "
            f"selected_runtime_backbone_blocker_items={return_path_audit.get('selected_runtime_backbone_blocker_item_count')} "
            f"selected_projection_10000_current={return_path_audit.get('selected_runtime_projection_10000_current_backbone_items')} "
            f"selected_projection_10000_target_constant={return_path_audit.get('selected_runtime_projection_10000_target_constant_backbone_items')} "
            f"selected_projection_10000_ratio_constant={return_path_audit.get('selected_runtime_projection_10000_ratio_constant')}"
        ),
        (
            "Runtime zone-return boundary: "
            f"present={zone_return_boundary.get('present')} "
            f"records={zone_return_boundary.get('boundary_record_count')} "
            f"max_all_intents={zone_return_boundary.get('max_all_intent_count')} "
            f"max_reapplied_intents={zone_return_boundary.get('max_reapplied_intent_count')} "
            f"all_backbone_items={zone_return_boundary.get('max_all_current_backbone_return_state_item_count')} "
            f"all_target_backbone_items={zone_return_boundary.get('max_all_target_shared_backbone_return_state_item_count')} "
            f"all_backbone_ratio={zone_return_boundary.get('max_all_current_to_target_backbone_state_ratio')} "
            f"all_candidate_zone_routes={zone_return_boundary.get('max_all_candidate_shared_zone_route_count')} "
            f"all_candidate_label_stacks={zone_return_boundary.get('max_all_candidate_shared_label_stack_count')} "
            f"all_reverse_to_candidate_ratio={zone_return_boundary.get('max_all_reverse_labels_to_candidate_label_stack_ratio')} "
            f"reapplied_backbone_items={zone_return_boundary.get('max_reapplied_current_backbone_return_state_item_count')} "
            f"reapplied_target_backbone_items={zone_return_boundary.get('max_reapplied_target_shared_backbone_return_state_item_count')} "
            f"reapplied_backbone_ratio={zone_return_boundary.get('max_reapplied_current_to_target_backbone_state_ratio')} "
            f"reapplied_candidate_zone_routes={zone_return_boundary.get('max_reapplied_candidate_shared_zone_route_count')} "
            f"reapplied_candidate_label_stacks={zone_return_boundary.get('max_reapplied_candidate_shared_label_stack_count')} "
            f"reapplied_reverse_to_candidate_ratio={zone_return_boundary.get('max_reapplied_reverse_labels_to_candidate_label_stack_ratio')} "
            f"unique_targets={zone_return_boundary.get('unique_all_target_key_count')} "
            f"install_behaviors={','.join(zone_return_boundary.get('install_behaviors') or [])}"
        ),
        (
            "Runtime ping validation: "
            f"present={ping_validation.get('present')} "
            f"records={ping_validation.get('selection_record_count')} "
            f"scopes={','.join(ping_validation.get('validation_scopes') or [])} "
            f"sample_sources={','.join(ping_validation.get('sample_sources') or [])} "
            f"active_max={ping_validation.get('max_active_flow_count')} "
            f"reapplied_max={ping_validation.get('max_reapplied_flow_count')} "
            f"sample_source_max={ping_validation.get('max_sample_source_flow_count')} "
            f"selected_max={ping_validation.get('max_selected_flow_count')} "
            f"reduction_max={ping_validation.get('max_gateway_class_sample_reduction_count')} "
            f"selected_ratio_min={ping_validation.get('min_selected_to_sample_source_flow_ratio')} "
            f"fallbacks={','.join(ping_validation.get('fallback_reasons') or [])}"
        ),
        (
            "Runtime incremental reuse: "
            f"present={incremental_reuse.get('present')} "
            f"records={incremental_reuse.get('slice_record_count')} "
            f"active_max={incremental_reuse.get('max_active_flow_count')} "
            f"reapplied_max={incremental_reuse.get('max_reapplied_flow_count')} "
            f"held_max={incremental_reuse.get('max_held_flow_count')} "
            f"cleanup_max={incremental_reuse.get('max_incremental_cleanup_flow_count')} "
            f"reapply_reduction_max={incremental_reuse.get('max_reapply_reduction_count')} "
            f"held_slices={incremental_reuse.get('slice_count_with_held_flows')} "
            f"cleanup_slices={incremental_reuse.get('slice_count_with_incremental_cleanup')} "
            f"reapplied_ratio_min={incremental_reuse.get('min_reapplied_to_active_flow_ratio')} "
            f"held_ratio_max={incremental_reuse.get('max_held_to_active_flow_ratio')}"
        ),
        (
            "Bootstrap timing: "
            f"cold_before_first_epoch={startup.get('cold_start_before_first_epoch_execution_wall_seconds')} "
            f"ready_to_execute={startup.get('ready_to_execute_epochs_wall_seconds')} "
            f"prewarm={startup.get('pre_epoch_host_runtime_prewarm_wall_seconds')} "
            f"prepare={startup.get('pre_epoch_host_runtime_prepare_wall_seconds')} "
            f"reconcile={startup.get('pre_epoch_host_runtime_reconcile_wall_seconds')}"
        ),
        (
            "Initial activation timing: "
            f"classification={initial_activation.get('classification')} "
            f"wall={initial_activation.get('initial_activation_wall_seconds')} "
            f"cold_before_first_epoch={initial_activation.get('cold_start_before_first_epoch_execution_wall_seconds')} "
            f"first_runtime_execution={initial_activation.get('first_runtime_epoch_execution_after_prepare_reconcile_wall_seconds')} "
            f"steady_excludes_initial={initial_activation.get('steady_runtime_excludes_initial_activation')}"
        ),
        (
            "First RuntimeEpoch: "
            f"wall={first_runtime.get('transition_wall_seconds')} "
            f"network_mutation={first_runtime.get('network_mutation_plan_wall_seconds')} "
            f"node_local_apply={first_runtime.get('node_local_apply_wall_seconds')}"
        ),
        (
            "RuntimeEpoch results: "
            f"count={runtime_metrics.get('count')} "
            f"lag_avg={dict(runtime_metrics.get('activation_lag_seconds', {})).get('average')} "
            f"lag_max={dict(runtime_metrics.get('activation_lag_seconds', {})).get('maximum')} "
            f"wall_avg={dict(runtime_metrics.get('transition_wall_seconds', {})).get('average')} "
            f"host_apply_avg={dict(runtime_metrics.get('host_operation_apply_wall_seconds', {})).get('average')}"
        ),
        (
            "Steady RuntimeEpoch: "
            f"source_count={steady_runtime.get('source_runtime_epoch_count')} "
            f"excluded_initial={steady_runtime.get('excluded_initial_runtime_epoch_count')} "
            f"count={steady_runtime.get('count')} "
            f"wall_avg={steady_runtime.get('average_transition_wall_seconds')} "
            f"host_apply_avg={steady_runtime.get('average_host_mutation_apply_wall_seconds')}"
        ),
        (
            "ParameterEpoch results: "
            f"count={parameter_metrics.get('count')} "
            f"lag_avg={dict(parameter_metrics.get('activation_lag_seconds', {})).get('average')} "
            f"lag_max={dict(parameter_metrics.get('activation_lag_seconds', {})).get('maximum')} "
            f"wall_avg={dict(parameter_metrics.get('transition_wall_seconds', {})).get('average')} "
            f"host_apply_avg={dict(parameter_metrics.get('host_operation_apply_wall_seconds', {})).get('average')} "
            f"post_refresh_avg={dict(parameter_metrics.get('post_apply_state_refresh_wall_seconds', {})).get('average')}"
        ),
        (
            "ParameterEpoch noop skipped: "
            f"count={noop_metrics.get('count')} "
            f"lag_avg={dict(noop_metrics.get('activation_lag_seconds', {})).get('average')} "
            f"wall_avg={dict(noop_metrics.get('transition_wall_seconds', {})).get('average')} "
            f"host_apply_avg={dict(noop_metrics.get('host_operation_apply_wall_seconds', {})).get('average')}"
        ),
        (
            "Teardown cleanup: "
            f"present={teardown_cleanup.get('present')} "
            f"success={teardown_cleanup.get('all_cleanup_success')} "
            f"known_wall={teardown_cleanup.get('known_teardown_cleanup_wall_seconds')} "
            f"wall={teardown_cleanup.get('teardown_cleanup_wall_seconds')} "
            f"wall_complete={teardown_cleanup.get('teardown_cleanup_wall_time_complete')} "
            f"node_local_rollback={teardown_cleanup.get('node_local_rollback_wall_seconds')} "
            f"host_rollback={teardown_cleanup.get('host_rollback_wall_seconds')} "
            f"host_rollback_count={teardown_cleanup.get('host_rollback_count')} "
            f"host_artifact_cleanup={teardown_cleanup.get('host_artifact_cleanup_wall_seconds')} "
            f"post_run_preflight={teardown_cleanup.get('post_run_preflight_wall_seconds')} "
            f"remaining_host_ifaces={teardown_cleanup.get('post_cleanup_remaining_host_interface_count')}"
        ),
    ]
    return "\n".join(lines)
