#!/usr/bin/env python3
# encoding: utf-8

"""Audit G/Z return-path scale semantics in planner artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

DEFAULT_RETURN_PATH_PROJECTION_FLOW_COUNTS: Tuple[int, ...] = (1000, 10000)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_dicts(value: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _iter_dicts(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _iter_dicts(nested)


def _flow_contexts_from_runtime_update_plan(
    runtime_update_plan: Dict[str, Any],
) -> List[Dict[str, Any]]:
    contexts_by_flow_id: Dict[str, Dict[str, Any]] = {}
    for subplan in dict(runtime_update_plan.get("node_subplans", {})).values():
        for action in list(subplan.get("actions", [])):
            outcome = dict(action.get("intent", {}).get("outcome", {}))
            flow_context = dict(outcome.get("flow_context", {}))
            flow_id = str(flow_context.get("flow_id", "")).strip()
            if not flow_id:
                continue
            contexts_by_flow_id.setdefault(flow_id, {}).update(flow_context)
    return [contexts_by_flow_id[flow_id] for flow_id in sorted(contexts_by_flow_id)]


def _flow_key(context: Dict[str, Any]) -> Tuple[str, str, str]:
    return (
        str(context.get("flow_id", "")),
        str(context.get("ut_id", "")),
        str(context.get("traffic_scope", "")),
    )


def _gateway_class_key(context: Dict[str, Any]) -> str:
    gateway_class_id = str(context.get("gateway_class_id") or "").strip()
    if gateway_class_id:
        return gateway_class_id
    zone_id = str(context.get("zone_id", "")).strip()
    gateway_id = str(context.get("selected_gateway_id", "")).strip()
    if zone_id and gateway_id:
        return f"{zone_id}:default-gateway:{gateway_id}"
    return gateway_id or "unknown-gateway-class"


def _zone_return_key(context: Dict[str, Any]) -> Tuple[str, str]:
    intent = dict(context.get("return_path_intent", {}))
    explicit_target = dict(intent.get("shared_backbone_return_target") or {})
    explicit_zone_id = str(explicit_target.get("zone_id") or "").strip()
    explicit_serving_satellite_id = str(
        explicit_target.get("zone_serving_satellite_id") or ""
    ).strip()
    if explicit_zone_id and explicit_serving_satellite_id:
        return explicit_zone_id, explicit_serving_satellite_id

    zone_id = str(intent.get("zone_id") or context.get("zone_id") or "")
    serving_satellite_id = str(
        intent.get("zone_serving_satellite_id")
        or context.get("serving_satellite_id")
        or ""
    )
    return zone_id, serving_satellite_id


def _gateway_zone_return_key(context: Dict[str, Any]) -> Tuple[str, str, str]:
    intent = dict(context.get("return_path_intent", {}))
    explicit_target = dict(intent.get("shared_backbone_return_target") or {})
    explicit_gateway_id = str(explicit_target.get("gateway_id") or "").strip()
    explicit_zone_id = str(explicit_target.get("zone_id") or "").strip()
    explicit_serving_satellite_id = str(
        explicit_target.get("zone_serving_satellite_id") or ""
    ).strip()
    if explicit_gateway_id and explicit_zone_id and explicit_serving_satellite_id:
        return explicit_gateway_id, explicit_zone_id, explicit_serving_satellite_id

    gateway_id = str(intent.get("selected_gateway_id") or context.get("selected_gateway_id") or "")
    zone_id, serving_satellite_id = _zone_return_key(context)
    return gateway_id, zone_id, serving_satellite_id


def _reverse_path_signature(context: Dict[str, Any]) -> Tuple[str, ...]:
    intent = dict(context.get("return_path_intent", {}))
    candidate = dict(intent.get("candidate_shared_return_label_stack") or {})
    candidate_signature = str(candidate.get("reverse_path_signature") or "").strip()
    if candidate_signature:
        return tuple(part for part in candidate_signature.split(":") if part)
    reverse_link_ids = [
        str(link_id)
        for link_id in list(context.get("reverse_link_ids", []))
        if str(link_id)
    ]
    if reverse_link_ids:
        return tuple(reverse_link_ids)
    satellite_path = [
        str(node_id)
        for node_id in list(context.get("satellite_path", []))
        if str(node_id)
    ]
    if satellite_path:
        return tuple(
            [
                str(context.get("selected_gateway_id") or ""),
                *reversed(satellite_path),
            ]
        )
    return (
        str(context.get("selected_gateway_id") or ""),
        str(context.get("serving_satellite_id") or ""),
    )


def _has_explicit_candidate_shared_label_stack(context: Dict[str, Any]) -> bool:
    intent = dict(context.get("return_path_intent", {}))
    candidate = dict(intent.get("candidate_shared_return_label_stack") or {})
    return bool(
        str(candidate.get("target_key") or "").strip()
        or str(candidate.get("reverse_path_signature") or "").strip()
    )


def _candidate_shared_zone_route_key(context: Dict[str, Any]) -> str:
    intent = dict(context.get("return_path_intent", {}))
    candidate = dict(intent.get("candidate_shared_zone_return_route") or {})
    candidate_key = str(candidate.get("target_key") or "").strip()
    if candidate_key:
        return candidate_key
    explicit_target = dict(intent.get("shared_backbone_return_target") or {})
    shared_target_key = str(explicit_target.get("target_key") or "").strip()
    if shared_target_key:
        return f"{shared_target_key}:zone-route"
    return ":".join(_gateway_zone_return_key(context)) + ":zone-route"


def _has_explicit_candidate_shared_zone_route(context: Dict[str, Any]) -> bool:
    intent = dict(context.get("return_path_intent", {}))
    candidate = dict(intent.get("candidate_shared_zone_return_route") or {})
    return bool(str(candidate.get("target_key") or "").strip())


def _shared_backbone_return_reachability_preview(
    contexts: Sequence[Dict[str, Any]],
    *,
    shared_gz_backbone_active: bool = False,
) -> Dict[str, Any]:
    groups: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = {}
    for context in contexts:
        groups.setdefault(_gateway_zone_return_key(context), []).append(context)

    target_groups: List[Dict[str, Any]] = []
    current_reverse_label_count = 0
    target_shared_label_stack_count = 0
    target_shared_zone_route_count = 0
    explicit_candidate_metadata_flow_count = 0
    fallback_candidate_metadata_flow_count = 0
    explicit_candidate_zone_route_flow_count = 0
    fallback_candidate_zone_route_flow_count = 0
    for key in sorted(groups):
        gateway_id, zone_id, serving_satellite_id = key
        grouped_contexts = groups[key]
        explicit_group_flow_count = sum(
            1
            for context in grouped_contexts
            if _has_explicit_candidate_shared_label_stack(context)
        )
        fallback_group_flow_count = len(grouped_contexts) - explicit_group_flow_count
        explicit_route_group_flow_count = sum(
            1
            for context in grouped_contexts
            if _has_explicit_candidate_shared_zone_route(context)
        )
        fallback_route_group_flow_count = (
            len(grouped_contexts) - explicit_route_group_flow_count
        )
        candidate_zone_route_keys = {
            _candidate_shared_zone_route_key(context) for context in grouped_contexts
        }
        reverse_path_signatures = {
            _reverse_path_signature(context) for context in grouped_contexts
        }
        reverse_label_count = sum(
            len(list(context.get("reverse_labels", [])))
            for context in grouped_contexts
        )
        shared_stack_count = len(reverse_path_signatures)
        shared_zone_route_count = len(candidate_zone_route_keys)
        current_reverse_label_count += 0 if shared_gz_backbone_active else reverse_label_count
        target_shared_label_stack_count += shared_stack_count
        target_shared_zone_route_count += shared_zone_route_count
        explicit_candidate_metadata_flow_count += explicit_group_flow_count
        fallback_candidate_metadata_flow_count += fallback_group_flow_count
        explicit_candidate_zone_route_flow_count += explicit_route_group_flow_count
        fallback_candidate_zone_route_flow_count += fallback_route_group_flow_count
        target_groups.append(
            {
                "target_key": ":".join(key),
                "selected_gateway_id": gateway_id,
                "zone_id": zone_id,
                "zone_serving_satellite_id": serving_satellite_id,
                "flow_count": len(grouped_contexts),
                "ut_count": len(
                    {
                        str(context.get("ut_id"))
                        for context in grouped_contexts
                        if context.get("ut_id")
                    }
                ),
                "unique_reverse_path_count": shared_stack_count,
                "current_model": (
                    "shared_gz_backbone"
                    if shared_gz_backbone_active
                    else "per_flow_reverse_lfib"
                ),
                "current_reverse_label_count": (
                    0 if shared_gz_backbone_active else reverse_label_count
                ),
                "legacy_compatibility_reverse_label_count": reverse_label_count,
                "actual_shared_reverse_lfib_entry_count": (
                    shared_stack_count if shared_gz_backbone_active else 0
                ),
                "candidate_shared_zone_route_count": shared_zone_route_count,
                "candidate_shared_label_stack_count": shared_stack_count,
                "explicit_candidate_zone_route_flow_count": explicit_route_group_flow_count,
                "fallback_candidate_zone_route_flow_count": fallback_route_group_flow_count,
                "candidate_zone_route_coverage_ratio": _ratio(
                    explicit_route_group_flow_count,
                    len(grouped_contexts),
                ),
                "explicit_candidate_metadata_flow_count": explicit_group_flow_count,
                "fallback_candidate_metadata_flow_count": fallback_group_flow_count,
                "fallback_reverse_path_signature_flow_count": fallback_group_flow_count,
                "candidate_metadata_coverage_ratio": _ratio(
                    explicit_group_flow_count,
                    len(grouped_contexts),
                ),
                "candidate_metadata_complete": fallback_group_flow_count == 0
                and len(grouped_contexts) > 0,
                "reverse_label_reduction_available": (
                    reverse_label_count > shared_stack_count
                ),
                "flow_to_shared_path_ratio": _ratio(
                    len(grouped_contexts),
                    shared_stack_count,
                ),
            }
        )

    return {
        "target_kind": "gateway_zone_return_reachability",
        "state_model": "shared_gateway_zone_or_zone_return_reachability",
        "current_model": (
            "shared_gz_backbone"
            if shared_gz_backbone_active
            else "per_flow_reverse_lfib"
        ),
        "target_group_count": len(target_groups),
        "current_reverse_label_count": current_reverse_label_count,
        "legacy_compatibility_reverse_label_count": sum(
            int(group.get("legacy_compatibility_reverse_label_count", 0) or 0)
            for group in target_groups
        ),
        "actual_shared_reverse_lfib_entry_count": sum(
            int(group.get("actual_shared_reverse_lfib_entry_count", 0) or 0)
            for group in target_groups
        ),
        "candidate_shared_zone_route_count": target_shared_zone_route_count,
        "candidate_shared_label_stack_count": target_shared_label_stack_count,
        "explicit_candidate_zone_route_flow_count": explicit_candidate_zone_route_flow_count,
        "fallback_candidate_zone_route_flow_count": fallback_candidate_zone_route_flow_count,
        "candidate_zone_route_coverage_ratio": _ratio(
            explicit_candidate_zone_route_flow_count,
            len(contexts),
        ),
        "candidate_zone_route_complete": fallback_candidate_zone_route_flow_count == 0
        and len(contexts) > 0,
        "explicit_candidate_metadata_flow_count": explicit_candidate_metadata_flow_count,
        "fallback_candidate_metadata_flow_count": fallback_candidate_metadata_flow_count,
        "fallback_reverse_path_signature_flow_count": fallback_candidate_metadata_flow_count,
        "candidate_metadata_coverage_ratio": _ratio(
            explicit_candidate_metadata_flow_count,
            len(contexts),
        ),
        "candidate_metadata_complete": fallback_candidate_metadata_flow_count == 0
        and len(contexts) > 0,
        "reverse_label_reduction_available": (
            current_reverse_label_count > target_shared_label_stack_count
        ),
        "current_to_candidate_label_stack_ratio": _ratio(
            current_reverse_label_count,
            target_shared_label_stack_count,
        ),
        "target_groups": target_groups,
    }


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(float(numerator) / float(denominator), 6)


def _return_state_scale_model(
    *,
    flow_count: int,
    per_flow_state: Dict[str, int],
    shared_targets: Dict[str, int],
    current_backbone_items_override: int | None = None,
    current_backbone_scales_with_flow_count: bool = True,
    current_backbone_state_model: str = "per_ut_flow_compatibility",
) -> Dict[str, Any]:
    current_backbone_items = (
        int(current_backbone_items_override)
        if current_backbone_items_override is not None
        else (
            int(per_flow_state.get("gateway_ut32_return_route_count", 0))
            + int(per_flow_state.get("reverse_label_count", 0))
            + int(per_flow_state.get("reverse_transit_lfib_entry_count", 0))
        )
    )
    current_edge_items = (
        int(per_flow_state.get("serving_satellite_ut32_delivery_route_count", 0))
        + int(per_flow_state.get("gateway_nat_forward_rule_count", 0))
    )
    target_backbone_items = int(
        shared_targets.get("gateway_zone_return_target_count", 0)
    )
    target_backbone_items = max(target_backbone_items, int(shared_targets.get("zone_return_target_count", 0)))
    scale_projection = _return_state_scale_projection(
        flow_count=flow_count,
        current_backbone_items=current_backbone_items,
        target_backbone_items=target_backbone_items,
    )
    return {
        "current_backbone_return_state_item_count": current_backbone_items,
        "target_shared_backbone_return_state_item_count": target_backbone_items,
        "current_edge_return_state_item_count": current_edge_items,
        "current_backbone_state_scales_with_flow_count": (
            flow_count > 0 and current_backbone_scales_with_flow_count
        ),
        "target_backbone_state_independent_of_ut_count": True,
        "backbone_state_reduction_available": current_backbone_items > target_backbone_items,
        "current_to_target_backbone_state_ratio": _ratio(
            current_backbone_items,
            target_backbone_items,
        ),
        "current_backbone_state_model": current_backbone_state_model,
        "target_backbone_state_model": "shared_gateway_zone_or_zone_return_reachability",
        "edge_delivery_state_model": "serving_satellite_local_ut_delivery",
        "scale_projection": scale_projection,
    }


def _return_path_migration_blockers(per_flow_state: Dict[str, int]) -> List[Dict[str, Any]]:
    definitions = [
        {
            "blocker_id": "gateway_ut32_return_route",
            "migration_category": "backbone_return",
            "current_state_key": "gateway_ut32_return_route_count",
            "current_owner": "gateway",
            "current_scope": "per_ut_prefix",
            "current_state_model": "ut_32_return_route",
            "target_owner": "satellite_backbone",
            "target_scope": "gateway_zone_or_zone_return_target",
            "target_state_model": "shared_gateway_zone_or_zone_return_reachability",
            "migration_action": "replace_ut32_gateway_return_routes_with_shared_zone_return_reachability",
        },
        {
            "blocker_id": "reverse_lfib_label_stack",
            "migration_category": "backbone_return",
            "current_state_key": "reverse_label_count",
            "current_owner": "satellite_backbone",
            "current_scope": "per_flow_reverse_label",
            "current_state_model": "per_flow_reverse_lfib",
            "target_owner": "satellite_backbone",
            "target_scope": "gateway_zone_or_zone_return_target",
            "target_state_model": "shared_gateway_zone_or_zone_return_reachability",
            "migration_action": "replace_per_flow_reverse_lfib_with_shared_zone_return_labels_or_equivalent",
        },
        {
            "blocker_id": "reverse_transit_lfib_entry",
            "migration_category": "backbone_return",
            "current_state_key": "reverse_transit_lfib_entry_count",
            "current_owner": "satellite_backbone",
            "current_scope": "per_flow_reverse_transit_hop",
            "current_state_model": "per_flow_reverse_transit_lfib",
            "target_owner": "satellite_backbone",
            "target_scope": "gateway_zone_or_zone_return_target",
            "target_state_model": "shared_gateway_zone_or_zone_return_reachability",
            "migration_action": "collapse_reverse_transit_entries_to_shared_zone_return_reachability",
        },
        {
            "blocker_id": "serving_satellite_ut32_delivery_route",
            "migration_category": "edge_local_delivery",
            "current_state_key": "serving_satellite_ut32_delivery_route_count",
            "current_owner": "serving_satellite_edge",
            "current_scope": "per_ut_prefix",
            "current_state_model": "ut_32_local_delivery_route",
            "target_owner": "serving_satellite_edge",
            "target_scope": "local_ut_delivery",
            "target_state_model": "serving_satellite_local_ut_delivery",
            "migration_action": "keep_ut_specific_delivery_local_to_serving_satellite_edge_not_backbone",
        },
        {
            "blocker_id": "gateway_nat_forward_rules",
            "migration_category": "gateway_edge_policy",
            "current_state_key": "gateway_nat_forward_rule_count",
            "current_owner": "gateway",
            "current_scope": "per_flow_or_per_ut_policy_rule",
            "current_state_model": "per_flow_gateway_nat_forward_snat_rules",
            "target_owner": "gateway_edge",
            "target_scope": "gateway_class_or_zone_policy",
            "target_state_model": "shared_gateway_class_edge_policy_where_possible",
            "migration_action": "separate_gateway_edge_policy_from_backbone_return_reachability",
        },
    ]
    blockers: List[Dict[str, Any]] = []
    for definition in definitions:
        count = int(per_flow_state.get(str(definition["current_state_key"]), 0) or 0)
        blockers.append(
            {
                **definition,
                "current_item_count": count,
                "present": count > 0,
                "scales_with_flow_count": count > 0,
            }
        )
    return blockers


def _project_count(per_flow_value: float, projected_flow_count: int) -> int:
    return int(round(per_flow_value * float(projected_flow_count)))


def _return_state_scale_projection(
    *,
    flow_count: int,
    current_backbone_items: int,
    target_backbone_items: int,
    projected_flow_counts: Sequence[int] = DEFAULT_RETURN_PATH_PROJECTION_FLOW_COUNTS,
) -> Dict[str, Any]:
    current_per_flow = (
        float(current_backbone_items) / float(flow_count)
        if flow_count > 0
        else 0.0
    )
    target_per_flow = (
        float(target_backbone_items) / float(flow_count)
        if flow_count > 0
        else 0.0
    )
    projections: Dict[str, Dict[str, Any]] = {}
    for projected_flow_count in projected_flow_counts:
        projected_flow_count = int(projected_flow_count)
        if projected_flow_count <= 0:
            continue
        current_projected = _project_count(current_per_flow, projected_flow_count)
        target_constant = int(target_backbone_items) if flow_count > 0 else 0
        target_ratio_scaled = _project_count(target_per_flow, projected_flow_count)
        projections[str(projected_flow_count)] = {
            "projected_flow_count": projected_flow_count,
            "current_per_flow_compatibility_backbone_items": current_projected,
            "target_shared_backbone_items_if_target_count_constant": target_constant,
            "target_shared_backbone_items_if_observed_target_ratio_scales": target_ratio_scaled,
            "current_to_constant_target_ratio": _ratio(
                current_projected,
                target_constant,
            ),
            "current_to_ratio_scaled_target_ratio": _ratio(
                current_projected,
                target_ratio_scaled,
            ),
        }
    return {
        "projection_flow_counts": [
            int(value)
            for value in projected_flow_counts
            if int(value) > 0
        ],
        "observed_current_backbone_items_per_flow": round(current_per_flow, 6),
        "observed_target_backbone_items_per_flow": round(target_per_flow, 6),
        "assumptions": {
            "current_per_flow_compatibility": "current_backbone_items_scale_linearly_with_flow_count",
            "target_count_constant": "new_UTs_share_existing_gateway_zone_or_zone_targets",
            "target_ratio_scaled": "new_UTs_add_gateway_zone_or_zone_targets_at_the_observed_ratio",
        },
        "projections": projections,
    }


def _return_path_migration_readiness_summary(
    *,
    total_flow_count: int,
    current_backbone_items: int,
    target_backbone_items: int,
    migration_blockers: Sequence[Dict[str, Any]],
    category_totals: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    category_item_count = {
        category: int(dict(total).get("total_current_item_count", 0) or 0)
        for category, total in category_totals.items()
    }
    backbone_blockers = [
        str(blocker.get("blocker_id"))
        for blocker in migration_blockers
        if str(blocker.get("migration_category") or "") == "backbone_return"
    ]
    backbone_blocker_items = int(category_item_count.get("backbone_return", 0))
    shared_backbone_ready = (
        total_flow_count > 0
        and current_backbone_items <= target_backbone_items
        and backbone_blocker_items == 0
    )
    return {
        "current_realization": (
            "shared_gz_backbone"
            if shared_backbone_ready
            else "per_ut_flow_compatibility"
        ),
        "target_realization": "shared_gateway_zone_or_zone_return_reachability",
        "shared_backbone_return_ready": shared_backbone_ready,
        "backbone_return_migration_required": not shared_backbone_ready
        and total_flow_count > 0,
        "current_backbone_state_scales_with_flow_count": (
            total_flow_count > 0
            and current_backbone_items > 0
            and not shared_backbone_ready
        ),
        "target_backbone_state_independent_of_ut_count": True,
        "current_backbone_return_state_item_count": current_backbone_items,
        "target_shared_backbone_return_state_item_count": target_backbone_items,
        "current_to_target_backbone_state_ratio": _ratio(
            current_backbone_items,
            target_backbone_items,
        ),
        "backbone_return_blocker_item_count": backbone_blocker_items,
        "gateway_edge_policy_item_count": int(
            category_item_count.get("gateway_edge_policy", 0)
        ),
        "edge_local_delivery_item_count": int(
            category_item_count.get("edge_local_delivery", 0)
        ),
        "backbone_return_blocker_ids": sorted(backbone_blockers),
        "non_backbone_items_are_separated": True,
        "migration_boundary": (
            "separate_satellite_backbone_return_reachability_from_gateway_edge_policy_"
            "and_serving_satellite_local_delivery"
        ),
    }


def audit_flow_contexts(
    flow_contexts: Sequence[Dict[str, Any]],
    *,
    scope_id: str,
) -> Dict[str, Any]:
    unique_contexts: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for context in flow_contexts:
        key = _flow_key(context)
        if not key[0]:
            continue
        unique_contexts.setdefault(key, dict(context))
    contexts = list(unique_contexts.values())

    flow_count = len(contexts)
    ut_ids = {str(context.get("ut_id")) for context in contexts if context.get("ut_id")}
    zone_ids = {str(context.get("zone_id")) for context in contexts if context.get("zone_id")}
    gateway_class_keys = {_gateway_class_key(context) for context in contexts}
    zone_return_keys = {_zone_return_key(context) for context in contexts}
    gateway_zone_return_keys = {_gateway_zone_return_key(context) for context in contexts}

    contexts_with_return_intent = [
        context for context in contexts if isinstance(context.get("return_path_intent"), dict)
    ]
    compatibility_contexts = [
        context
        for context in contexts_with_return_intent
        if dict(context.get("return_path_intent", {}))
        .get("current_linux_realization", {})
        .get("mode")
        == "per_ut_flow_compatibility"
    ]
    linux_realization_modes = sorted(
        {
            str(
                dict(context.get("return_path_intent", {}))
                .get("current_linux_realization", {})
                .get("mode", "per_ut_flow_compatibility")
            )
            for context in contexts_with_return_intent
        }
    )
    shared_gz_backbone_active = bool(contexts_with_return_intent) and linux_realization_modes == [
        "shared_gz_backbone"
    ]
    reverse_label_count = sum(len(list(context.get("reverse_labels", []))) for context in contexts)
    reverse_transit_lfib_count = sum(
        max(0, len(list(context.get("reverse_labels", []))) - 1)
        for context in contexts
    )
    shared_targets = {
        "zone_return_target_count": len(zone_return_keys),
        "gateway_zone_return_target_count": len(gateway_zone_return_keys),
        "gateway_class_count": len(gateway_class_keys),
    }
    target_backbone_items = max(
        int(shared_targets["gateway_zone_return_target_count"]),
        int(shared_targets["zone_return_target_count"]),
    )
    legacy_per_flow_state = {
        "gateway_ut32_return_route_count": flow_count,
        "serving_satellite_ut32_delivery_route_count": flow_count,
        "reverse_label_count": reverse_label_count,
        "reverse_transit_lfib_entry_count": reverse_transit_lfib_count,
        "gateway_forward_filter_rule_count": flow_count,
        "gateway_reverse_filter_rule_count": flow_count,
        "gateway_snat_rule_count": flow_count,
        "gateway_nat_forward_rule_count": flow_count * 3,
    }
    if shared_gz_backbone_active:
        per_flow_state = {
            **legacy_per_flow_state,
            "gateway_ut32_return_route_count": 0,
            "reverse_label_count": 0,
            "reverse_transit_lfib_entry_count": 0,
        }
    else:
        per_flow_state = dict(legacy_per_flow_state)
    state_scale_model = _return_state_scale_model(
        flow_count=flow_count,
        per_flow_state=per_flow_state,
        shared_targets=shared_targets,
        current_backbone_items_override=(
            target_backbone_items if shared_gz_backbone_active else None
        ),
        current_backbone_scales_with_flow_count=not shared_gz_backbone_active,
        current_backbone_state_model=(
            "shared_gz_backbone"
            if shared_gz_backbone_active
            else "per_ut_flow_compatibility"
        ),
    )
    shared_reachability_preview = _shared_backbone_return_reachability_preview(
        contexts,
        shared_gz_backbone_active=shared_gz_backbone_active,
    )

    return {
        "scope_id": scope_id,
        "flow_count": flow_count,
        "ut_count": len(ut_ids),
        "zone_count": len(zone_ids),
        "return_path_intent_count": len(contexts_with_return_intent),
        "current_linux_realization_modes": linux_realization_modes,
        "shared_gz_backbone_active": shared_gz_backbone_active,
        "per_ut_flow_compatibility_count": len(compatibility_contexts),
        "per_flow_return_state": per_flow_state,
        "legacy_compatibility_projection": legacy_per_flow_state,
        "return_path_migration_blockers": _return_path_migration_blockers(
            per_flow_state
        ),
        "shared_return_targets": shared_targets,
        "shared_backbone_return_reachability_preview": shared_reachability_preview,
        "return_state_scale_model": state_scale_model,
        "scale_gap": {
            "flow_to_zone_return_target_ratio": _ratio(
                flow_count,
                len(zone_return_keys),
            ),
            "flow_to_gateway_zone_return_target_ratio": _ratio(
                flow_count,
                len(gateway_zone_return_keys),
            ),
            "flow_to_gateway_class_ratio": _ratio(flow_count, len(gateway_class_keys)),
            "current_per_flow_return_state_present": flow_count > 0,
            "current_return_state_scales_with_flow_count": (
                flow_count > 0 and not shared_gz_backbone_active
            ),
            "shared_target_reduction_available": flow_count > len(gateway_zone_return_keys),
            "target_backbone_state": "shared_zone_or_gateway_zone_return_reachability",
            "current_backbone_state": (
                "shared_gz_backbone"
                if shared_gz_backbone_active
                else "per_ut_flow_compatibility"
            ),
        },
    }


def audit_runtime_update_plan(
    runtime_update_plan: Dict[str, Any],
    *,
    scope_id: str = "runtime_update_plan",
) -> Dict[str, Any]:
    return audit_flow_contexts(
        _flow_contexts_from_runtime_update_plan(runtime_update_plan),
        scope_id=scope_id,
    )


def _aggregate_snapshot_reports(snapshot_reports: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    total_flow_count = sum(int(report.get("flow_count", 0)) for report in snapshot_reports)
    total_return_intents = sum(
        int(report.get("return_path_intent_count", 0)) for report in snapshot_reports
    )
    total_current_backbone_items = sum(
        int(
            dict(report.get("return_state_scale_model", {})).get(
                "current_backbone_return_state_item_count",
                0,
            )
        )
        for report in snapshot_reports
    )
    total_target_backbone_items = sum(
        int(
            dict(report.get("return_state_scale_model", {})).get(
                "target_shared_backbone_return_state_item_count",
                0,
            )
        )
        for report in snapshot_reports
    )
    max_flow_count = max(
        (int(report.get("flow_count", 0)) for report in snapshot_reports),
        default=0,
    )
    max_gateway_zone_targets = max(
        (
            int(
                dict(report.get("shared_return_targets", {})).get(
                    "gateway_zone_return_target_count",
                    0,
                )
            )
            for report in snapshot_reports
        ),
        default=0,
    )
    total_candidate_shared_label_stacks = sum(
        int(
            dict(report.get("shared_backbone_return_reachability_preview", {})).get(
                "candidate_shared_label_stack_count",
                0,
            )
            or 0
        )
        for report in snapshot_reports
    )
    total_candidate_shared_zone_routes = sum(
        int(
            dict(report.get("shared_backbone_return_reachability_preview", {})).get(
                "candidate_shared_zone_route_count",
                0,
            )
            or 0
        )
        for report in snapshot_reports
    )
    total_explicit_candidate_zone_route_flows = sum(
        int(
            dict(report.get("shared_backbone_return_reachability_preview", {})).get(
                "explicit_candidate_zone_route_flow_count",
                0,
            )
            or 0
        )
        for report in snapshot_reports
    )
    total_fallback_candidate_zone_route_flows = sum(
        int(
            dict(report.get("shared_backbone_return_reachability_preview", {})).get(
                "fallback_candidate_zone_route_flow_count",
                0,
            )
            or 0
        )
        for report in snapshot_reports
    )
    total_current_reverse_labels = sum(
        int(
            dict(report.get("shared_backbone_return_reachability_preview", {})).get(
                "current_reverse_label_count",
                0,
            )
            or 0
        )
        for report in snapshot_reports
    )
    total_legacy_reverse_labels = sum(
        int(
            dict(report.get("shared_backbone_return_reachability_preview", {})).get(
                "legacy_compatibility_reverse_label_count",
                dict(report.get("shared_backbone_return_reachability_preview", {})).get(
                    "current_reverse_label_count",
                    0,
                ),
            )
            or 0
        )
        for report in snapshot_reports
    )
    total_actual_shared_reverse_lfib_entries = sum(
        int(
            dict(report.get("shared_backbone_return_reachability_preview", {})).get(
                "actual_shared_reverse_lfib_entry_count",
                0,
            )
            or 0
        )
        for report in snapshot_reports
    )
    current_models = sorted(
        {
            str(
                dict(report.get("shared_backbone_return_reachability_preview", {})).get(
                    "current_model",
                    "per_flow_reverse_lfib",
                )
            )
            for report in snapshot_reports
            if int(report.get("flow_count", 0) or 0) > 0
        }
    )
    total_explicit_candidate_metadata_flows = sum(
        int(
            dict(report.get("shared_backbone_return_reachability_preview", {})).get(
                "explicit_candidate_metadata_flow_count",
                0,
            )
            or 0
        )
        for report in snapshot_reports
    )
    total_fallback_candidate_metadata_flows = sum(
        int(
            (
                dict(report.get("shared_backbone_return_reachability_preview", {})).get(
                    "fallback_reverse_path_signature_flow_count"
                )
                if dict(report.get("shared_backbone_return_reachability_preview", {})).get(
                    "fallback_reverse_path_signature_flow_count"
                )
                is not None
                else dict(report.get("shared_backbone_return_reachability_preview", {})).get(
                    "fallback_candidate_metadata_flow_count",
                    0,
                )
            )
            or 0
        )
        for report in snapshot_reports
    )
    blockers_by_id: Dict[str, Dict[str, Any]] = {}
    for report in snapshot_reports:
        for blocker in list(report.get("return_path_migration_blockers", [])):
            if not isinstance(blocker, dict) or not blocker.get("present"):
                continue
            blocker_id = str(blocker.get("blocker_id") or "")
            if not blocker_id:
                continue
            current_item_count = int(blocker.get("current_item_count", 0) or 0)
            existing = blockers_by_id.setdefault(
                blocker_id,
                {
                    **dict(blocker),
                    "total_current_item_count": 0,
                    "max_current_item_count_per_snapshot": 0,
                    "snapshot_count_present": 0,
                },
            )
            existing["total_current_item_count"] = (
                int(existing.get("total_current_item_count", 0) or 0)
                + current_item_count
            )
            existing["max_current_item_count_per_snapshot"] = max(
                int(existing.get("max_current_item_count_per_snapshot", 0) or 0),
                current_item_count,
            )
            existing["snapshot_count_present"] = (
                int(existing.get("snapshot_count_present", 0) or 0) + 1
            )
    migration_blockers = [
        blockers_by_id[blocker_id] for blocker_id in sorted(blockers_by_id)
    ]
    blocker_ids = [str(blocker.get("blocker_id")) for blocker in migration_blockers]
    category_totals: Dict[str, Dict[str, Any]] = {}
    for blocker in migration_blockers:
        category = str(blocker.get("migration_category") or "uncategorized")
        category_total = category_totals.setdefault(
            category,
            {
                "migration_category": category,
                "blocker_count": 0,
                "total_current_item_count": 0,
                "max_current_item_count_per_snapshot": 0,
                "blocker_ids": [],
            },
        )
        category_total["blocker_count"] = int(category_total["blocker_count"]) + 1
        category_total["total_current_item_count"] = (
            int(category_total["total_current_item_count"])
            + int(blocker.get("total_current_item_count", 0) or 0)
        )
        category_total["max_current_item_count_per_snapshot"] = max(
            int(category_total["max_current_item_count_per_snapshot"]),
            int(blocker.get("max_current_item_count_per_snapshot", 0) or 0),
        )
        category_total["blocker_ids"] = sorted(
            [
                *list(category_total.get("blocker_ids", [])),
                str(blocker.get("blocker_id")),
            ]
        )
    rendered_category_totals = {
        category: category_totals[category]
        for category in sorted(category_totals)
    }
    return {
        "snapshot_count": len(snapshot_reports),
        "total_flow_count": total_flow_count,
        "total_return_path_intent_count": total_return_intents,
        "total_current_backbone_return_state_item_count": total_current_backbone_items,
        "total_target_shared_backbone_return_state_item_count": total_target_backbone_items,
        "current_to_target_backbone_state_ratio": _ratio(
            total_current_backbone_items,
            total_target_backbone_items,
        ),
        "scale_projection": _return_state_scale_projection(
            flow_count=total_flow_count,
            current_backbone_items=total_current_backbone_items,
            target_backbone_items=total_target_backbone_items,
        ),
        "max_flow_count_per_snapshot": max_flow_count,
        "max_gateway_zone_return_target_count_per_snapshot": max_gateway_zone_targets,
        "shared_backbone_return_reachability_preview": {
            "target_kind": "gateway_zone_return_reachability",
            "state_model": "shared_gateway_zone_or_zone_return_reachability",
            "current_model": (
                current_models[0]
                if len(current_models) == 1
                else ",".join(current_models)
            ),
            "current_reverse_label_count": total_current_reverse_labels,
            "legacy_compatibility_reverse_label_count": total_legacy_reverse_labels,
            "actual_shared_reverse_lfib_entry_count": total_actual_shared_reverse_lfib_entries,
            "candidate_shared_zone_route_count": total_candidate_shared_zone_routes,
            "candidate_shared_label_stack_count": total_candidate_shared_label_stacks,
            "explicit_candidate_zone_route_flow_count": total_explicit_candidate_zone_route_flows,
            "fallback_candidate_zone_route_flow_count": total_fallback_candidate_zone_route_flows,
            "candidate_zone_route_coverage_ratio": _ratio(
                total_explicit_candidate_zone_route_flows,
                total_explicit_candidate_zone_route_flows
                + total_fallback_candidate_zone_route_flows,
            ),
            "candidate_zone_route_complete": total_fallback_candidate_zone_route_flows == 0
            and (
                total_explicit_candidate_zone_route_flows
                + total_fallback_candidate_zone_route_flows
            )
            > 0,
            "explicit_candidate_metadata_flow_count": total_explicit_candidate_metadata_flows,
            "fallback_candidate_metadata_flow_count": total_fallback_candidate_metadata_flows,
            "fallback_reverse_path_signature_flow_count": total_fallback_candidate_metadata_flows,
            "candidate_metadata_coverage_ratio": _ratio(
                total_explicit_candidate_metadata_flows,
                total_explicit_candidate_metadata_flows
                + total_fallback_candidate_metadata_flows,
            ),
            "candidate_metadata_complete": total_fallback_candidate_metadata_flows == 0
            and (
                total_explicit_candidate_metadata_flows
                + total_fallback_candidate_metadata_flows
            )
            > 0,
            "reverse_label_reduction_available": (
                total_current_reverse_labels > total_candidate_shared_label_stacks
            ),
            "current_to_candidate_label_stack_ratio": _ratio(
                total_current_reverse_labels,
                total_candidate_shared_label_stacks,
            ),
        },
        "return_path_migration_blocker_count": len(blocker_ids),
        "return_path_migration_blocker_ids": blocker_ids,
        "return_path_migration_blockers": migration_blockers,
        "return_path_migration_category_totals": rendered_category_totals,
        "return_path_migration_readiness": _return_path_migration_readiness_summary(
            total_flow_count=total_flow_count,
            current_backbone_items=total_current_backbone_items,
            target_backbone_items=total_target_backbone_items,
            migration_blockers=migration_blockers,
            category_totals=rendered_category_totals,
        ),
        "current_return_state_scales_with_flow_count": any(
            bool(dict(report.get("scale_gap", {})).get("current_return_state_scales_with_flow_count"))
            for report in snapshot_reports
        ),
        "shared_target_reduction_available": any(
            bool(dict(report.get("scale_gap", {})).get("shared_target_reduction_available"))
            for report in snapshot_reports
        ),
        "backbone_state_reduction_available": any(
            bool(
                dict(report.get("return_state_scale_model", {})).get(
                    "backbone_state_reduction_available"
                )
            )
            for report in snapshot_reports
        ),
    }


def audit_planner_preview(
    planner_preview: Dict[str, Any],
    *,
    selected_scope_ids: Sequence[str] | None = None,
) -> Dict[str, Any]:
    snapshots = list(planner_preview.get("snapshots", []))
    snapshot_reports: List[Dict[str, Any]] = []
    for snapshot in snapshots:
        slice_id = str(snapshot.get("slice_id") or snapshot.get("snapshot_id") or "")
        snapshot_reports.append(
            audit_runtime_update_plan(
                dict(snapshot.get("runtime_update_plan", {})),
                scope_id=slice_id or f"snapshot-{len(snapshot_reports) + 1}",
            )
        )

    selected_ids = {str(scope_id) for scope_id in (selected_scope_ids or []) if str(scope_id)}
    selected_snapshot_reports = [
        report for report in snapshot_reports if str(report.get("scope_id")) in selected_ids
    ]
    aggregate = _aggregate_snapshot_reports(snapshot_reports)
    selected_aggregate = _aggregate_snapshot_reports(selected_snapshot_reports)

    return {
        "audit_kind": "skyseed_return_path_scaling",
        "schema_version": "skyseed-return-path-audit-v1alpha1",
        **aggregate,
        "selected_scope_ids": sorted(selected_ids),
        "selected_runtime_scope": {
            **selected_aggregate,
            "scope_ids": sorted(str(report.get("scope_id")) for report in selected_snapshot_reports),
        },
        "snapshot_reports": snapshot_reports,
    }


def audit_planner_preview_path(
    path: Path,
    *,
    selected_scope_ids: Sequence[str] | None = None,
) -> Dict[str, Any]:
    return audit_planner_preview(
        dict(_load_json(path)),
        selected_scope_ids=selected_scope_ids,
    )


def render_return_path_audit_json(report: Dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True)


def _projection_summary_fields(report: Dict[str, Any], projected_flow_count: int = 10000) -> str:
    projection = (
        dict(report.get("scale_projection", {}))
        .get("projections", {})
    )
    projected = dict(dict(projection).get(str(projected_flow_count), {}))
    if not projected:
        return f"projection_{projected_flow_count}=none"
    return (
        f"projection_{projected_flow_count}_current="
        f"{projected.get('current_per_flow_compatibility_backbone_items')} "
        f"projection_{projected_flow_count}_target_constant="
        f"{projected.get('target_shared_backbone_items_if_target_count_constant')} "
        f"projection_{projected_flow_count}_target_ratio_scaled="
        f"{projected.get('target_shared_backbone_items_if_observed_target_ratio_scales')} "
        f"projection_{projected_flow_count}_ratio_constant="
        f"{projected.get('current_to_constant_target_ratio')} "
        f"projection_{projected_flow_count}_ratio_scaled="
        f"{projected.get('current_to_ratio_scaled_target_ratio')}"
    )


def _shared_reachability_summary_fields(report: Dict[str, Any]) -> str:
    reachability = dict(
        report.get("shared_backbone_return_reachability_preview", {})
    )
    if not reachability:
        return "shared_reachability=none"
    return (
        "shared_current_reverse_labels="
        f"{reachability.get('current_reverse_label_count')} "
        "shared_candidate_zone_routes="
        f"{reachability.get('candidate_shared_zone_route_count')} "
        "candidate_zone_route_coverage="
        f"{reachability.get('candidate_zone_route_coverage_ratio')} "
        "candidate_zone_route_complete="
        f"{reachability.get('candidate_zone_route_complete')} "
        "shared_candidate_label_stacks="
        f"{reachability.get('candidate_shared_label_stack_count')} "
        "shared_label_reduction_available="
        f"{reachability.get('reverse_label_reduction_available')} "
        "shared_label_ratio="
        f"{reachability.get('current_to_candidate_label_stack_ratio')} "
        "candidate_metadata_coverage="
        f"{reachability.get('candidate_metadata_coverage_ratio')} "
        "candidate_metadata_complete="
        f"{reachability.get('candidate_metadata_complete')} "
        "explicit_candidate_metadata_flows="
        f"{reachability.get('explicit_candidate_metadata_flow_count')} "
        "fallback_reverse_path_signature_flows="
        f"{reachability.get('fallback_reverse_path_signature_flow_count')}"
    )


def _top_migration_blocker_summary(report: Dict[str, Any]) -> str:
    blockers = [
        dict(blocker)
        for blocker in list(report.get("return_path_migration_blockers", []))
        if isinstance(blocker, dict)
    ]
    if not blockers:
        return "top_migration_blocker=none"
    top = max(
        blockers,
        key=lambda blocker: (
            int(blocker.get("total_current_item_count", 0) or 0),
            str(blocker.get("blocker_id") or ""),
        ),
    )
    return (
        f"top_migration_blocker={top.get('blocker_id')}:"
        f"{top.get('total_current_item_count')}"
    )


def _migration_category_item_count(report: Dict[str, Any], category: str) -> int:
    return int(
        dict(
            dict(report.get("return_path_migration_category_totals", {})).get(
                category,
                {},
            )
        ).get("total_current_item_count", 0)
        or 0
    )


def _top_migration_blocker_summary_for_category(
    report: Dict[str, Any],
    category: str,
) -> str:
    blockers = [
        dict(blocker)
        for blocker in list(report.get("return_path_migration_blockers", []))
        if isinstance(blocker, dict)
        and str(blocker.get("migration_category") or "") == category
    ]
    if not blockers:
        return f"top_{category}_migration_blocker=none"
    top = max(
        blockers,
        key=lambda blocker: (
            int(blocker.get("total_current_item_count", 0) or 0),
            str(blocker.get("blocker_id") or ""),
        ),
    )
    return (
        f"top_{category}_migration_blocker={top.get('blocker_id')}:"
        f"{top.get('total_current_item_count')}"
    )


def render_return_path_audit_summary(report: Dict[str, Any]) -> str:
    readiness = dict(report.get("return_path_migration_readiness", {}))
    lines = [
        (
            "Return path audit: "
            f"snapshots={report.get('snapshot_count')} "
            f"total_flows={report.get('total_flow_count')} "
            f"return_intents={report.get('total_return_path_intent_count')} "
            f"per_flow_state={report.get('current_return_state_scales_with_flow_count')} "
            f"shared_reduction_available={report.get('shared_target_reduction_available')} "
            f"backbone_items={report.get('total_current_backbone_return_state_item_count')} "
            f"target_backbone_items={report.get('total_target_shared_backbone_return_state_item_count')} "
            f"backbone_ratio={report.get('current_to_target_backbone_state_ratio')} "
            f"shared_backbone_ready={readiness.get('shared_backbone_return_ready')} "
            f"backbone_migration_required={readiness.get('backbone_return_migration_required')} "
            f"backbone_blocker_items={readiness.get('backbone_return_blocker_item_count')} "
            f"migration_blockers={report.get('return_path_migration_blocker_count')} "
            f"backbone_return_items={_migration_category_item_count(report, 'backbone_return')} "
            f"gateway_edge_policy_items={_migration_category_item_count(report, 'gateway_edge_policy')} "
            f"edge_local_delivery_items={_migration_category_item_count(report, 'edge_local_delivery')} "
            f"{_top_migration_blocker_summary(report)} "
            f"{_top_migration_blocker_summary_for_category(report, 'backbone_return')} "
            f"{_projection_summary_fields(report)} "
            f"{_shared_reachability_summary_fields(report)}"
        )
    ]
    selected = dict(report.get("selected_runtime_scope", {}))
    if selected.get("snapshot_count"):
        selected_readiness = dict(selected.get("return_path_migration_readiness", {}))
        lines.append(
            "Selected runtime scope: "
            f"snapshots={selected.get('snapshot_count')} "
            f"total_flows={selected.get('total_flow_count')} "
            f"return_intents={selected.get('total_return_path_intent_count')} "
            f"per_flow_state={selected.get('current_return_state_scales_with_flow_count')} "
            f"shared_reduction_available={selected.get('shared_target_reduction_available')} "
            f"backbone_items={selected.get('total_current_backbone_return_state_item_count')} "
            f"target_backbone_items={selected.get('total_target_shared_backbone_return_state_item_count')} "
            f"backbone_ratio={selected.get('current_to_target_backbone_state_ratio')} "
            f"shared_backbone_ready={selected_readiness.get('shared_backbone_return_ready')} "
            f"backbone_migration_required={selected_readiness.get('backbone_return_migration_required')} "
            f"backbone_blocker_items={selected_readiness.get('backbone_return_blocker_item_count')} "
            f"migration_blockers={selected.get('return_path_migration_blocker_count')} "
            f"backbone_return_items={_migration_category_item_count(selected, 'backbone_return')} "
            f"gateway_edge_policy_items={_migration_category_item_count(selected, 'gateway_edge_policy')} "
            f"edge_local_delivery_items={_migration_category_item_count(selected, 'edge_local_delivery')} "
            f"{_top_migration_blocker_summary(selected)} "
            f"{_top_migration_blocker_summary_for_category(selected, 'backbone_return')} "
            f"{_projection_summary_fields(selected)} "
            f"{_shared_reachability_summary_fields(selected)}"
        )
    for snapshot in list(report.get("snapshot_reports", [])):
        shared = dict(snapshot.get("shared_return_targets", {}))
        gap = dict(snapshot.get("scale_gap", {}))
        per_flow = dict(snapshot.get("per_flow_return_state", {}))
        reachability = dict(
            snapshot.get("shared_backbone_return_reachability_preview", {})
        )
        scale_model = dict(snapshot.get("return_state_scale_model", {}))
        blockers = [
            dict(blocker)
            for blocker in list(snapshot.get("return_path_migration_blockers", []))
            if isinstance(blocker, dict) and blocker.get("present")
        ]
        lines.append(
            " | ".join(
                [
                    str(snapshot.get("scope_id")),
                    f"flows={snapshot.get('flow_count')}",
                    f"uts={snapshot.get('ut_count')}",
                    f"zones={snapshot.get('zone_count')}",
                    f"gateway_zone_targets={shared.get('gateway_zone_return_target_count')}",
                    f"gateway_classes={shared.get('gateway_class_count')}",
                    f"reverse_labels={per_flow.get('reverse_label_count')}",
                    f"shared_zone_routes={reachability.get('candidate_shared_zone_route_count')}",
                    f"shared_label_stacks={reachability.get('candidate_shared_label_stack_count')}",
                    f"gw_nat_forward_rules={per_flow.get('gateway_nat_forward_rule_count')}",
                    f"backbone_items={scale_model.get('current_backbone_return_state_item_count')}",
                    f"target_backbone_items={scale_model.get('target_shared_backbone_return_state_item_count')}",
                    f"backbone_ratio={scale_model.get('current_to_target_backbone_state_ratio')}",
                    f"migration_blockers={len(blockers)}",
                    f"flow_to_gateway_zone_ratio={gap.get('flow_to_gateway_zone_return_target_ratio')}",
                    f"shared_reduction_available={gap.get('shared_target_reduction_available')}",
                ]
            )
        )
    return "\n".join(lines)
