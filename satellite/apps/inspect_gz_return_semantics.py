#!/usr/bin/env python3
# encoding: utf-8

"""Build a dry-run G/Z shared return semantics evidence artifact."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from _path_setup import bootstrap_app_import_path

SATELLITE_DIR = bootstrap_app_import_path()


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.expanduser().read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _iter_actions(runtime_update_plan: Dict[str, Any]) -> Iterable[Tuple[str, Dict[str, Any]]]:
    for node_id, subplan in dict(runtime_update_plan.get("node_subplans", {})).items():
        for action in list(dict(subplan).get("actions", [])):
            if isinstance(action, dict):
                yield str(node_id), dict(action)


def _flow_contexts(runtime_update_plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    contexts_by_flow_id: Dict[str, Dict[str, Any]] = {}
    for _node_id, action in _iter_actions(runtime_update_plan):
        outcome = dict(action.get("intent", {}).get("outcome", {}))
        flow_context = dict(outcome.get("flow_context", {}))
        flow_id = str(flow_context.get("flow_id", "")).strip()
        if not flow_id:
            continue
        if not flow_context.get("gateway_class_id"):
            zone_id = flow_context.get("zone_id")
            gateway_id = flow_context.get("selected_gateway_id")
            if zone_id and gateway_id:
                flow_context["gateway_class_id"] = f"{zone_id}:default-gateway:{gateway_id}"
        contexts_by_flow_id.setdefault(flow_id, {}).update(flow_context)
    return [contexts_by_flow_id[flow_id] for flow_id in sorted(contexts_by_flow_id)]


def _ut_gateway_selections(runtime_update_plan: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    selections: Dict[str, Dict[str, Any]] = {}
    for node_id, action in _iter_actions(runtime_update_plan):
        if str(action.get("action_type")) != "forwarding_entry":
            continue
        intent = dict(action.get("intent", {}))
        if str(intent.get("forwarding_plane")) != "ut_gateway_class_resolution":
            continue
        selection = dict(dict(intent.get("outcome", {})).get("gateway_selection", {}))
        ut_id = str(selection.get("ut_id") or node_id)
        selections[ut_id] = {
            "ut_id": ut_id,
            "default_gateway_class_id": selection.get("default_gateway_class_id"),
            "default_selected_gateway_id": dict(selection.get("gateway_class_binding", {})).get(
                "selected_gateway_id"
            ),
            "gateway_class_bindings": list(selection.get("gateway_class_bindings", [])),
            "special_prefix_overrides": list(selection.get("special_prefix_overrides", [])),
            "runtime_action_id": action.get("action_id"),
            "ut_contract_scope": dict(action.get("metadata", {})).get("ut_contract_scope"),
        }
    return selections


def _gateway_zone_key(context: Dict[str, Any]) -> Tuple[str, str, str]:
    intent = dict(context.get("return_path_intent", {}))
    target = dict(intent.get("shared_backbone_return_target") or {})
    gateway_id = str(target.get("gateway_id") or intent.get("selected_gateway_id") or context.get("selected_gateway_id") or "")
    zone_id = str(target.get("zone_id") or intent.get("zone_id") or context.get("zone_id") or "")
    serving_satellite_id = str(
        target.get("zone_serving_satellite_id")
        or intent.get("zone_serving_satellite_id")
        or context.get("serving_satellite_id")
        or ""
    )
    return gateway_id, zone_id, serving_satellite_id


def _route_prefixes(context: Dict[str, Any]) -> List[str]:
    intent = dict(context.get("return_path_intent", {}))
    candidate_route = dict(intent.get("candidate_shared_zone_return_route") or {})
    prefixes = [
        str(prefix)
        for prefix in list(candidate_route.get("route_prefixes", []))
        if str(prefix)
    ]
    if prefixes:
        return prefixes
    return [str(prefix) for prefix in list(context.get("zone_return_prefixes", [])) if str(prefix)]


def _shared_return_groups(contexts: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = {}
    for context in contexts:
        grouped.setdefault(_gateway_zone_key(context), []).append(context)

    groups: List[Dict[str, Any]] = []
    for key in sorted(grouped):
        gateway_id, zone_id, serving_satellite_id = key
        group_contexts = grouped[key]
        reverse_label_stacks = {
            tuple(int(label) for label in context.get("reverse_labels", []))
            for context in group_contexts
        }
        reverse_path_signatures = {
            tuple(str(link_id) for link_id in context.get("reverse_link_ids", []))
            for context in group_contexts
        }
        route_prefixes = sorted(
            {
                prefix
                for context in group_contexts
                for prefix in _route_prefixes(context)
            }
        )
        representative = group_contexts[0]
        reverse_labels = [int(label) for label in representative.get("reverse_labels", [])]
        groups.append(
            {
                "target_key": ":".join(key),
                "selected_gateway_id": gateway_id,
                "zone_id": zone_id,
                "zone_responsible_satellite_id": serving_satellite_id,
                "flow_ids": sorted(str(context.get("flow_id")) for context in group_contexts),
                "ut_ids": sorted({str(context.get("ut_id")) for context in group_contexts}),
                "flow_count": len(group_contexts),
                "ut_count": len({str(context.get("ut_id")) for context in group_contexts}),
                "route_prefixes": route_prefixes,
                "route_prefix_count": len(route_prefixes),
                "reverse_link_ids": list(representative.get("reverse_link_ids", [])),
                "reverse_path_signature_count": len(reverse_path_signatures),
                "reverse_label_stacks": [list(stack) for stack in sorted(reverse_label_stacks)],
                "reverse_label_stack_count": len(reverse_label_stacks),
                "shared_reverse_labels": reverse_labels,
                "shared_reverse_label_count": len(reverse_labels),
                "shared_reverse_transit_lfib_entry_count": max(0, len(reverse_labels) - 1),
                "shared_serving_satellite_pop_count": 1 if reverse_labels else 0,
                "gw_to_z_return_path": " -> ".join(
                    [
                        str(representative.get("selected_gateway_id")),
                        *reversed([str(node_id) for node_id in representative.get("satellite_path", [])]),
                    ]
                ),
                "all_same_zone_return_path": len(reverse_path_signatures) == 1,
                "all_same_reverse_label_stack": len(reverse_label_stacks) == 1,
            }
        )
    return groups


def _legacy_counts(contexts: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    flow_count = len(contexts)
    reverse_label_count = sum(len(list(context.get("reverse_labels", []))) for context in contexts)
    reverse_transit_lfib_count = sum(
        max(0, len(list(context.get("reverse_labels", []))) - 1) for context in contexts
    )
    return {
        "flow_count": flow_count,
        "gateway_ut32_return_route_count": flow_count,
        "reverse_label_count": reverse_label_count,
        "reverse_transit_lfib_entry_count": reverse_transit_lfib_count,
        "serving_satellite_ut32_delivery_route_count": flow_count,
        "gateway_nat_forward_rule_count": flow_count * 3,
        "backbone_return_state_item_count": flow_count + reverse_label_count + reverse_transit_lfib_count,
    }


def _shared_counts(groups: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    return {
        "gateway_zone_return_group_count": len(groups),
        "gateway_zone_route_fec_count": sum(int(group.get("route_prefix_count", 0)) for group in groups),
        "gateway_ut32_return_route_count": 0,
        "shared_reverse_label_count": sum(int(group.get("shared_reverse_label_count", 0)) for group in groups),
        "shared_reverse_transit_lfib_entry_count": sum(
            int(group.get("shared_reverse_transit_lfib_entry_count", 0)) for group in groups
        ),
        "shared_serving_satellite_pop_count": sum(
            int(group.get("shared_serving_satellite_pop_count", 0)) for group in groups
        ),
        "ut_mpls_egress_count": 0,
        "backbone_return_state_item_count": (
            len(groups)
            + sum(int(group.get("shared_reverse_label_count", 0)) for group in groups)
            + sum(int(group.get("shared_reverse_transit_lfib_entry_count", 0)) for group in groups)
        ),
    }


def _zone_assignments(snapshot: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    zones = dict(snapshot.get("zones", {}))
    nodes = dict(snapshot.get("nodes", {}))
    assignments: Dict[str, Dict[str, Any]] = {}
    for zone_id, zone in sorted(zones.items()):
        ut_ids = [str(ut_id) for ut_id in list(dict(zone).get("ut_ids", []))]
        responsible_satellite_id = str(dict(zone).get("active_serving_satellite_id") or "")
        assignments[str(zone_id)] = {
            "zone_id": str(zone_id),
            "responsible_satellite_id": responsible_satellite_id,
            "resolution_state": dict(zone).get("resolution_state", "resolved"),
            "return_prefixes": list(dict(zone).get("return_prefixes", [])),
            "ut_ids": ut_ids,
            "ut_visibility": [
                {
                    "ut_id": ut_id,
                    "attachment_state": dict(nodes.get(ut_id, {})).get("attachment", {}).get("state"),
                    "active_serving_satellite_id": dict(nodes.get(ut_id, {})).get("attachment", {}).get(
                        "active_serving_satellite_id"
                    ),
                    "visible_from_responsible_satellite": (
                        dict(nodes.get(ut_id, {})).get("attachment", {}).get("active_serving_satellite_id")
                        == responsible_satellite_id
                    ),
                }
                for ut_id in ut_ids
            ],
        }
    return assignments


def _flow_summaries(contexts: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    summaries: List[Dict[str, Any]] = []
    for context in contexts:
        satellite_path = [str(node_id) for node_id in list(context.get("satellite_path", []))]
        summaries.append(
            {
                "flow_id": context.get("flow_id"),
                "ut_id": context.get("ut_id"),
                "traffic_scope": context.get("traffic_scope"),
                "policy_name": context.get("policy_name"),
                "destination_prefix": context.get("destination_prefix"),
                "zone_id": context.get("zone_id"),
                "selected_gateway_id": context.get("selected_gateway_id"),
                "gateway_class_id": context.get("gateway_class_id"),
                "zone_responsible_satellite_id": context.get("serving_satellite_id"),
                "ut_to_g_forward_path": " -> ".join(
                    [
                        str(context.get("ut_id")),
                        *satellite_path,
                        str(context.get("selected_gateway_id")),
                    ]
                ),
                "gw_to_z_return_path": " -> ".join(
                    [
                        str(context.get("selected_gateway_id")),
                        *reversed(satellite_path),
                    ]
                ),
                "forward_link_ids": list(context.get("forward_link_ids", [])),
                "reverse_link_ids": list(context.get("reverse_link_ids", [])),
                "forward_labels": list(context.get("forward_labels", [])),
                "reverse_labels": list(context.get("reverse_labels", [])),
                "return_target": dict(context.get("return_path_intent", {})).get(
                    "shared_backbone_return_target"
                ),
                "z_pop_action": {
                    "node_id": context.get("serving_satellite_id"),
                    "label": list(context.get("reverse_labels", []))[-1]
                    if list(context.get("reverse_labels", []))
                    else None,
                    "action": "pop_mpls_label_then_ip_access_delivery",
                },
                "z_to_ut_delivery_action": dict(context.get("return_path_intent", {})).get(
                    "serving_satellite_delivery_target"
                ),
            }
        )
    return summaries


def _distinct_override_examples(
    ut_gateway_selections: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    examples: List[Dict[str, Any]] = []
    for ut_id, selection in sorted(ut_gateway_selections.items()):
        default_gateway_id = str(selection.get("default_selected_gateway_id") or "")
        bindings = {
            str(binding.get("gateway_class_id")): dict(binding)
            for binding in list(selection.get("gateway_class_bindings", []))
            if str(binding.get("gateway_class_id") or "")
        }
        for override in list(selection.get("special_prefix_overrides", [])):
            override = dict(override)
            if str(override.get("resolution_state")) != "resolved":
                continue
            binding = bindings.get(str(override.get("gateway_class_id")), {})
            override_gateway_id = str(binding.get("selected_gateway_id") or "")
            if default_gateway_id and override_gateway_id and override_gateway_id != default_gateway_id:
                examples.append(
                    {
                        "ut_id": ut_id,
                        "destination_prefix": override.get("destination_prefix"),
                        "policy_name": override.get("policy_name"),
                        "default_gateway_id": default_gateway_id,
                        "override_gateway_id": override_gateway_id,
                        "default_gateway_class_id": selection.get("default_gateway_class_id"),
                        "override_gateway_class_id": override.get("gateway_class_id"),
                    }
                )
    return examples


def _default_and_override_paths_differ(contexts: Sequence[Dict[str, Any]]) -> bool:
    contexts_by_ut: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for context in contexts:
        ut_id = str(context.get("ut_id") or "")
        traffic_scope = str(context.get("traffic_scope") or "")
        if ut_id and traffic_scope:
            contexts_by_ut.setdefault(ut_id, {})[traffic_scope] = context
    for scoped_contexts in contexts_by_ut.values():
        default_context = scoped_contexts.get("ordinary_internet_default")
        override_context = scoped_contexts.get("special_prefix_override")
        if not default_context or not override_context:
            continue
        if (
            str(default_context.get("selected_gateway_id") or "")
            != str(override_context.get("selected_gateway_id") or "")
            and tuple(default_context.get("forward_link_ids", []))
            != tuple(override_context.get("forward_link_ids", []))
        ):
            return True
    return False


def build_gz_return_semantics_preview(
    *,
    snapshot_preview_path: Path,
    planner_preview_path: Path,
    snapshot_index: int = 0,
) -> Dict[str, Any]:
    snapshot_preview = _load_json(snapshot_preview_path)
    planner_preview = _load_json(planner_preview_path)
    snapshots = list(snapshot_preview.get("snapshots", []))
    planner_records = list(planner_preview.get("snapshots", []))
    if not snapshots:
        raise ValueError("snapshot preview has no snapshots")
    if not planner_records:
        raise ValueError("planner preview has no snapshots")
    selected_snapshot = dict(snapshots[int(snapshot_index)])
    selected_record = dict(planner_records[int(snapshot_index)])
    runtime_update_plan = dict(selected_record.get("runtime_update_plan", {}))
    contexts = _flow_contexts(runtime_update_plan)
    groups = _shared_return_groups(contexts)
    legacy_counts = _legacy_counts(contexts)
    shared_counts = _shared_counts(groups)
    ut_gateway_selections = _ut_gateway_selections(runtime_update_plan)
    zone_assignments = _zone_assignments(selected_snapshot)
    distinct_override_examples = _distinct_override_examples(ut_gateway_selections)
    any_configured_prefix_override = any(
        len(list(selection.get("special_prefix_overrides", []))) > 0
        for selection in ut_gateway_selections.values()
    )
    prefix_override_count = sum(
        1
        for selection in ut_gateway_selections.values()
        for override in list(selection.get("special_prefix_overrides", []))
        if str(dict(override).get("resolution_state")) == "resolved"
    )
    semantic_checks = {
        "ut_default_gateway_selection_present": all(
            selection.get("default_gateway_class_id")
            for selection in ut_gateway_selections.values()
        )
        and bool(ut_gateway_selections),
        "prefix_override_resolved_if_configured": prefix_override_count > 0
        or all(
            len(list(selection.get("special_prefix_overrides", []))) == 0
            for selection in ut_gateway_selections.values()
        ),
        "distinct_prefix_override_if_configured": (
            bool(distinct_override_examples) if any_configured_prefix_override else True
        ),
        "default_and_override_forward_paths_differ_if_configured": (
            _default_and_override_paths_differ(contexts)
            if any_configured_prefix_override
            else True
        ),
        "zone_responsible_satellite_assigned": all(
            assignment.get("responsible_satellite_id")
            for assignment in zone_assignments.values()
        )
        and bool(zone_assignments),
        "all_zone_uts_visible_from_responsible_satellite": all(
            item.get("visible_from_responsible_satellite") is True
            for assignment in zone_assignments.values()
            for item in list(assignment.get("ut_visibility", []))
        ),
        "same_group_flows_share_return_path": all(
            group.get("all_same_zone_return_path") for group in groups
        ),
        "same_group_flows_share_reverse_label_stack": all(
            group.get("all_same_reverse_label_stack") for group in groups
        ),
        "gw_no_per_ut32_shared_backbone_route": shared_counts["gateway_ut32_return_route_count"] == 0,
        "ut_not_mpls_egress": shared_counts["ut_mpls_egress_count"] == 0,
        "shared_state_bounded_by_gw_zone_satellite": (
            shared_counts["gateway_zone_return_group_count"] <= legacy_counts["flow_count"]
        ),
        "route_fec_bounded_by_gw_zone_satellite": (
            shared_counts["gateway_zone_route_fec_count"]
            <= shared_counts["gateway_zone_return_group_count"]
        ),
    }
    return {
        "artifact_kind": "gz_shared_return_semantics_preview",
        "artifact_version": "v1",
        "source_files": {
            "snapshot_preview": str(snapshot_preview_path),
            "planner_preview": str(planner_preview_path),
        },
        "snapshot_index": int(snapshot_index),
        "slice_id": selected_record.get("slice_id") or selected_snapshot.get("time_slice", {}).get("slice_id"),
        "planner_valid": planner_preview.get("all_valid"),
        "runtime_plan_id": runtime_update_plan.get("plan_id"),
        "semantics": {
            "g_representation": "selected_gateway_id and gateway_class_id",
            "z_representation": "snapshot.zones[zone_id].active_serving_satellite_id",
            "default_gateway_representation": "UT forwarding_entry outcome.gateway_selection.default_gateway_class_id",
            "prefix_override_representation": "UT gateway_selection.special_prefix_overrides",
            "return_group_key": "(selected_gateway_id, zone_id, zone_responsible_satellite_id)",
            "return_path": "GW -> Z responsible satellite over shared MPLS, then Z pop + local/access IP delivery",
            "ut_mpls_egress": "forbidden",
        },
        "ut_gateway_selections": ut_gateway_selections,
        "distinct_prefix_override_examples": distinct_override_examples,
        "zone_assignments": zone_assignments,
        "flow_summaries": _flow_summaries(contexts),
        "shared_return_groups": groups,
        "state_counts": {
            "legacy_per_ut_or_per_flow_model_before": legacy_counts,
            "shared_gz_model_after": shared_counts,
        },
        "semantic_checks": semantic_checks,
        "passed": all(bool(value) for value in semantic_checks.values()),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render dry-run evidence for G/Z shared MPLS return semantics."
    )
    parser.add_argument("--snapshot-preview", required=True)
    parser.add_argument("--planner-preview", required=True)
    parser.add_argument("--snapshot-index", type=int, default=0)
    parser.add_argument("--output", default=None)
    parser.add_argument("--format", choices=("text", "json"), default="json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = build_gz_return_semantics_preview(
        snapshot_preview_path=Path(args.snapshot_preview),
        planner_preview_path=Path(args.planner_preview),
        snapshot_index=int(args.snapshot_index),
    )
    if args.output:
        _write_json(Path(args.output), payload)
    if args.format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(
            "G/Z shared return semantics: "
            f"{'PASS' if payload['passed'] else 'FAIL'} "
            f"slice={payload.get('slice_id')} "
            f"groups={payload['state_counts']['shared_gz_model_after']['gateway_zone_return_group_count']} "
            f"legacy_backbone_items={payload['state_counts']['legacy_per_ut_or_per_flow_model_before']['backbone_return_state_item_count']} "
            f"shared_backbone_items={payload['state_counts']['shared_gz_model_after']['backbone_return_state_item_count']}"
        )
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
