#!/usr/bin/env python3
# encoding: utf-8

"""Map return-path migration blockers to Linux runtime install points."""

from __future__ import annotations

import json
from typing import Any, Dict, List


INSTALLER_SOURCE_FILE = "skyseed_v1.backends.linux.mpls"
MIGRATION_CATEGORY_PRIORITY = {
    "backbone_return": 0,
    "gateway_edge_policy": 1,
    "edge_local_delivery": 2,
}
INSTALLER_SOURCE_SYMBOLS = {
    "apply": "MinimalRealLinuxMplsInstaller._apply_specs_for_context",
    "preflight": "_capture_preflight_state",
    "rollback": "MinimalRealLinuxMplsInstaller._rollback_specs_for_context",
    "cleanup_verify": "MinimalRealLinuxMplsInstaller._verify_cleanup_for_context",
    "zone_boundary": "_zone_return_install_boundary_payload",
}


RETURN_PATH_INSTALL_POINT_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "blocker_id": "gateway_ut32_return_route",
        "migration_category": "backbone_return",
        "current_owner": "gateway",
        "current_scope": "per_ut_prefix",
        "current_linux_realization": "per_ut_flow_compatibility",
        "target_owner": "satellite_backbone",
        "target_scope": "gateway_zone_or_zone_return_target",
        "target_realization": "shared_gateway_zone_or_zone_return_reachability",
        "migration_action": "replace_ut32_gateway_return_routes_with_shared_zone_return_reachability",
        "install_points": [
            {
                "phase": "preflight",
                "source_symbol": INSTALLER_SOURCE_SYMBOLS["preflight"],
                "description": "capture gateway reverse ingress route",
                "command_family": "ip route show <ut /32 return_target_prefix>",
                "per_ut_or_per_flow_input": "DirectGatewayExperimentContext.return_target_prefix",
            },
            {
                "phase": "apply",
                "source_symbol": INSTALLER_SOURCE_SYMBOLS["apply"],
                "description": "install gateway reverse ingress route",
                "command_family": "ip route replace <ut /32> encap mpls <reverse_label>",
                "per_ut_or_per_flow_input": "return_target_prefix, reverse_labels[0], reverse_segments[0]",
            },
            {
                "phase": "rollback",
                "source_symbol": INSTALLER_SOURCE_SYMBOLS["rollback"],
                "description": "delete gateway reverse ingress route",
                "command_family": "ip route del <ut /32 return_target_prefix>",
                "per_ut_or_per_flow_input": "DirectGatewayExperimentContext.return_target_prefix",
            },
        ],
    },
    {
        "blocker_id": "reverse_lfib_label_stack",
        "migration_category": "backbone_return",
        "current_owner": "satellite_backbone",
        "current_scope": "per_flow_reverse_label",
        "current_linux_realization": "per_ut_flow_compatibility",
        "target_owner": "satellite_backbone",
        "target_scope": "gateway_zone_or_zone_return_target",
        "target_realization": "shared_gateway_zone_or_zone_return_reachability",
        "migration_action": "replace_per_flow_reverse_lfib_with_shared_zone_return_labels_or_equivalent",
        "install_points": [
            {
                "phase": "preflight",
                "source_symbol": INSTALLER_SOURCE_SYMBOLS["preflight"],
                "description": "capture reverse LFIB and serving-satellite reverse pop label",
                "command_family": "ip -f mpls route show <reverse_label>",
                "per_ut_or_per_flow_input": "DirectGatewayExperimentContext.reverse_labels",
            },
            {
                "phase": "apply",
                "source_symbol": INSTALLER_SOURCE_SYMBOLS["apply"],
                "description": "install reverse LFIB on transit satellites",
                "command_family": "ip -f mpls route replace <reverse_label> as <next_reverse_label>",
                "per_ut_or_per_flow_input": "reverse_labels, reverse_segments",
            },
            {
                "phase": "apply",
                "source_symbol": INSTALLER_SOURCE_SYMBOLS["apply"],
                "description": "install serving-satellite reverse pop entry",
                "command_family": "ip -f mpls route replace <last_reverse_label> dev lo",
                "per_ut_or_per_flow_input": "reverse_labels[-1]",
            },
            {
                "phase": "rollback",
                "source_symbol": INSTALLER_SOURCE_SYMBOLS["rollback"],
                "description": "delete reverse LFIB and reverse pop entries",
                "command_family": "ip -f mpls route del <reverse_label>",
                "per_ut_or_per_flow_input": "DirectGatewayExperimentContext.reverse_labels",
            },
        ],
    },
    {
        "blocker_id": "reverse_transit_lfib_entry",
        "migration_category": "backbone_return",
        "current_owner": "satellite_backbone",
        "current_scope": "per_flow_reverse_transit_hop",
        "current_linux_realization": "per_ut_flow_compatibility",
        "target_owner": "satellite_backbone",
        "target_scope": "gateway_zone_or_zone_return_target",
        "target_realization": "shared_gateway_zone_or_zone_return_reachability",
        "migration_action": "collapse_reverse_transit_entries_to_shared_zone_return_reachability",
        "install_points": [
            {
                "phase": "preflight",
                "source_symbol": INSTALLER_SOURCE_SYMBOLS["preflight"],
                "description": "capture reverse LFIB on each reverse transit hop",
                "command_family": "ip -f mpls route show <reverse_transit_label>",
                "per_ut_or_per_flow_input": "reverse_segments[1:], reverse_labels[:-1]",
            },
            {
                "phase": "apply",
                "source_symbol": INSTALLER_SOURCE_SYMBOLS["apply"],
                "description": "install reverse LFIB on each reverse transit hop",
                "command_family": "ip -f mpls route replace <reverse_label> as <next_reverse_label>",
                "per_ut_or_per_flow_input": "reverse_segments[1:], reverse_labels",
            },
            {
                "phase": "rollback",
                "source_symbol": INSTALLER_SOURCE_SYMBOLS["rollback"],
                "description": "delete reverse LFIB on each reverse transit hop",
                "command_family": "ip -f mpls route del <reverse_transit_label>",
                "per_ut_or_per_flow_input": "reverse_segments[1:], reverse_labels[:-1]",
            },
        ],
    },
    {
        "blocker_id": "serving_satellite_ut32_delivery_route",
        "migration_category": "edge_local_delivery",
        "current_owner": "serving_satellite_edge",
        "current_scope": "per_ut_prefix",
        "current_linux_realization": "per_ut_flow_compatibility",
        "target_owner": "serving_satellite_edge",
        "target_scope": "local_ut_delivery",
        "target_realization": "serving_satellite_local_ut_delivery",
        "migration_action": "keep_ut_specific_delivery_local_to_serving_satellite_edge_not_backbone",
        "install_points": [
            {
                "phase": "preflight",
                "source_symbol": INSTALLER_SOURCE_SYMBOLS["preflight"],
                "description": "capture serving-satellite UT delivery route",
                "command_family": "ip route show <ut /32 return_target_prefix>",
                "per_ut_or_per_flow_input": "DirectGatewayExperimentContext.return_target_prefix",
            },
            {
                "phase": "apply",
                "source_symbol": INSTALLER_SOURCE_SYMBOLS["apply"],
                "description": "install serving-satellite UT delivery route",
                "command_family": "ip route replace <ut /32> via <ut_access_ip> dev <ut_access_interface>",
                "per_ut_or_per_flow_input": "return_target_prefix, ut_access_ip, ut_access_interface",
            },
            {
                "phase": "rollback",
                "source_symbol": INSTALLER_SOURCE_SYMBOLS["rollback"],
                "description": "delete serving-satellite UT delivery route",
                "command_family": "ip route del <ut /32 return_target_prefix>",
                "per_ut_or_per_flow_input": "DirectGatewayExperimentContext.return_target_prefix",
            },
        ],
    },
    {
        "blocker_id": "gateway_nat_forward_rules",
        "migration_category": "gateway_edge_policy",
        "current_owner": "gateway",
        "current_scope": "per_flow_or_per_ut_policy_rule",
        "current_linux_realization": "per_ut_flow_compatibility",
        "target_owner": "gateway_edge",
        "target_scope": "gateway_class_or_zone_policy",
        "target_realization": "shared_gateway_class_edge_policy_where_possible",
        "migration_action": "separate_gateway_edge_policy_from_backbone_return_reachability",
        "install_points": [
            {
                "phase": "preflight",
                "source_symbol": INSTALLER_SOURCE_SYMBOLS["preflight"],
                "description": "probe gateway forward, reverse, and SNAT rules",
                "command_family": "iptables -C FORWARD / iptables -t nat -C POSTROUTING",
                "per_ut_or_per_flow_input": "ut_access_ip, gateway interfaces, gateway_nat_source_ip",
            },
            {
                "phase": "apply",
                "source_symbol": INSTALLER_SOURCE_SYMBOLS["apply"],
                "description": "install gateway forward filter rule",
                "command_family": "iptables -A FORWARD -s <ut_access_ip>/32",
                "per_ut_or_per_flow_input": "ut_access_ip, gateway_access_interface, gateway_nat_interface",
            },
            {
                "phase": "apply",
                "source_symbol": INSTALLER_SOURCE_SYMBOLS["apply"],
                "description": "install gateway reverse filter rule",
                "command_family": "iptables -A FORWARD -d <ut_access_ip>/32 --ctstate ESTABLISHED,RELATED",
                "per_ut_or_per_flow_input": "ut_access_ip, gateway_nat_interface, gateway_access_interface",
            },
            {
                "phase": "apply",
                "source_symbol": INSTALLER_SOURCE_SYMBOLS["apply"],
                "description": "install gateway SNAT rule",
                "command_family": "iptables -t nat -A POSTROUTING -s <ut_access_ip>/32 -j SNAT",
                "per_ut_or_per_flow_input": "ut_access_ip, gateway_nat_interface, gateway_nat_source_ip",
            },
            {
                "phase": "rollback",
                "source_symbol": INSTALLER_SOURCE_SYMBOLS["rollback"],
                "description": "delete gateway forward, reverse, and SNAT rules",
                "command_family": "iptables -D FORWARD / iptables -t nat -D POSTROUTING",
                "per_ut_or_per_flow_input": "ut_access_ip, gateway interfaces, gateway_nat_source_ip",
            },
        ],
    },
]


def summarize_return_path_install_points() -> Dict[str, Any]:
    blockers = [
        {
            **definition,
            "source_file": INSTALLER_SOURCE_FILE,
            "install_point_count": len(list(definition.get("install_points", []))),
        }
        for definition in RETURN_PATH_INSTALL_POINT_DEFINITIONS
    ]
    category_totals: Dict[str, Dict[str, Any]] = {}
    for blocker in blockers:
        category = str(blocker.get("migration_category") or "uncategorized")
        entry = category_totals.setdefault(
            category,
            {
                "migration_category": category,
                "blocker_count": 0,
                "install_point_count": 0,
                "blocker_ids": [],
            },
        )
        entry["blocker_count"] = int(entry["blocker_count"]) + 1
        entry["install_point_count"] = int(entry["install_point_count"]) + int(
            blocker.get("install_point_count", 0) or 0
        )
        entry["blocker_ids"].append(str(blocker.get("blocker_id")))
    for entry in category_totals.values():
        entry["blocker_ids"] = sorted(entry["blocker_ids"])

    backbone_blockers = [
        blocker
        for blocker in blockers
        if str(blocker.get("migration_category")) == "backbone_return"
    ]
    return {
        "current_linux_realization": "per_ut_flow_compatibility",
        "target_backbone_realization": "shared_gateway_zone_or_zone_return_reachability",
        "source_file": INSTALLER_SOURCE_FILE,
        "source_symbols": dict(INSTALLER_SOURCE_SYMBOLS),
        "blocker_count": len(blockers),
        "backbone_blocker_count": len(backbone_blockers),
        "install_point_count": sum(
            int(blocker.get("install_point_count", 0) or 0)
            for blocker in blockers
        ),
        "category_totals": {
            category: category_totals[category]
            for category in sorted(category_totals)
        },
        "blockers": blockers,
    }


def _audit_blocker_counts_by_id(return_path_report: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    counts: Dict[str, Dict[str, Any]] = {}
    explicit_blockers = [
        blocker
        for blocker in list(return_path_report.get("return_path_migration_blockers", []))
        if isinstance(blocker, dict)
    ]
    if explicit_blockers:
        blockers = explicit_blockers
    else:
        blockers_by_id: Dict[str, Dict[str, Any]] = {}
        for snapshot in list(return_path_report.get("snapshot_reports", [])):
            per_flow_state = dict(dict(snapshot).get("per_flow_return_state", {}))
            for blocker in _migration_blockers_from_per_flow_state(per_flow_state):
                if not blocker.get("present"):
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
                        "count_inference": "snapshot_per_flow_return_state",
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
        blockers = [blockers_by_id[blocker_id] for blocker_id in sorted(blockers_by_id)]
        if not blockers:
            blockers = _migration_blockers_from_aggregate_counts(return_path_report)

    for blocker in blockers:
        if not isinstance(blocker, dict):
            continue
        blocker_id = str(blocker.get("blocker_id") or "").strip()
        if not blocker_id:
            continue
        total_count = int(
            blocker.get("total_current_item_count")
            if blocker.get("total_current_item_count") is not None
            else blocker.get("current_item_count", 0)
            or 0
        )
        counts[blocker_id] = {
            "blocker_id": blocker_id,
            "present": bool(blocker.get("present", total_count > 0))
            or total_count > 0,
            "total_current_item_count": total_count,
            "max_current_item_count_per_snapshot": int(
                blocker.get("max_current_item_count_per_snapshot")
                if blocker.get("max_current_item_count_per_snapshot") is not None
                else blocker.get("current_item_count", 0)
                or 0
            ),
            "snapshot_count_present": int(blocker.get("snapshot_count_present", 0) or 0),
            "count_inference": str(blocker.get("count_inference") or "explicit_blocker"),
        }
    return counts


def _migration_blockers_from_aggregate_counts(
    return_path_report: Dict[str, Any],
) -> List[Dict[str, Any]]:
    flow_count = int(return_path_report.get("total_flow_count", 0) or 0)
    if flow_count <= 0:
        return []
    current_backbone = int(
        return_path_report.get("total_current_backbone_return_state_item_count", 0)
        or 0
    )
    reverse_backbone_items = max(0, current_backbone - flow_count)
    snapshot_count = int(return_path_report.get("snapshot_count", 0) or 0)
    max_flow_count = int(
        return_path_report.get("max_flow_count_per_snapshot", 0) or flow_count
    )
    return [
        {
            "blocker_id": "gateway_ut32_return_route",
            "migration_category": "backbone_return",
            "present": flow_count > 0,
            "total_current_item_count": flow_count,
            "max_current_item_count_per_snapshot": max_flow_count,
            "snapshot_count_present": snapshot_count,
            "count_inference": "aggregate_flow_count",
        },
        {
            "blocker_id": "reverse_lfib_label_stack",
            "migration_category": "backbone_return",
            "present": reverse_backbone_items > 0,
            "total_current_item_count": reverse_backbone_items,
            "max_current_item_count_per_snapshot": 0,
            "snapshot_count_present": snapshot_count,
            "count_inference": "aggregate_backbone_remainder_after_gateway_ut32",
        },
        {
            "blocker_id": "serving_satellite_ut32_delivery_route",
            "migration_category": "edge_local_delivery",
            "present": flow_count > 0,
            "total_current_item_count": flow_count,
            "max_current_item_count_per_snapshot": max_flow_count,
            "snapshot_count_present": snapshot_count,
            "count_inference": "aggregate_flow_count",
        },
        {
            "blocker_id": "gateway_nat_forward_rules",
            "migration_category": "gateway_edge_policy",
            "present": flow_count > 0,
            "total_current_item_count": flow_count * 3,
            "max_current_item_count_per_snapshot": max_flow_count * 3,
            "snapshot_count_present": snapshot_count,
            "count_inference": "aggregate_flow_count_times_current_gateway_rule_factor",
        },
    ]


def _migration_blockers_from_per_flow_state(
    per_flow_state: Dict[str, Any],
) -> List[Dict[str, Any]]:
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
    blockers: List[Dict[str, Any]] = []
    for state_key, (blocker_id, category) in key_to_blocker.items():
        current_item_count = int(per_flow_state.get(state_key, 0) or 0)
        blockers.append(
            {
                "blocker_id": blocker_id,
                "migration_category": category,
                "current_item_count": current_item_count,
                "present": current_item_count > 0,
            }
        )
    return blockers


def _priority_category_rank(item: Dict[str, Any]) -> int:
    value = item.get("priority_category_rank")
    if value is None:
        return 99
    return int(value)


def _shared_reachability_metadata(report: Dict[str, Any]) -> Dict[str, Any]:
    reachability = dict(
        report.get("shared_backbone_return_reachability_preview", {})
    )
    candidate_zone_route_complete = bool(
        reachability.get("candidate_zone_route_complete")
    )
    candidate_metadata_complete = bool(
        reachability.get("candidate_metadata_complete")
    )
    return {
        "candidate_shared_zone_route_count": int(
            reachability.get("candidate_shared_zone_route_count", 0) or 0
        ),
        "candidate_zone_route_coverage_ratio": reachability.get(
            "candidate_zone_route_coverage_ratio"
        ),
        "candidate_zone_route_complete": candidate_zone_route_complete,
        "candidate_shared_label_stack_count": int(
            reachability.get("candidate_shared_label_stack_count", 0) or 0
        ),
        "candidate_metadata_coverage_ratio": reachability.get(
            "candidate_metadata_coverage_ratio"
        ),
        "candidate_metadata_complete": candidate_metadata_complete,
        "metadata_boundary_ready_for_backbone_migration": (
            candidate_zone_route_complete and candidate_metadata_complete
        ),
    }


def _next_backbone_migration_gate(
    *,
    metadata_boundary_ready: bool,
    present_backbone_items: List[Dict[str, Any]],
) -> str:
    if present_backbone_items and metadata_boundary_ready:
        return "replace_current_backbone_install_points_with_shared_zone_return_installation"
    if present_backbone_items:
        return "complete_planner_candidate_zone_route_and_label_metadata_first"
    if metadata_boundary_ready:
        return "backbone_install_points_clear_verify_runtime_artifacts"
    return "no_backbone_return_flows_or_metadata_yet"


def _worklist_scope(
    *,
    scope_name: str,
    return_path_report: Dict[str, Any],
    install_report: Dict[str, Any],
) -> Dict[str, Any]:
    counts_by_id = _audit_blocker_counts_by_id(return_path_report)
    items: List[Dict[str, Any]] = []
    for blocker in list(install_report.get("blockers", [])):
        blocker_id = str(blocker.get("blocker_id") or "")
        counts = counts_by_id.get(blocker_id, {})
        category = str(blocker.get("migration_category") or "uncategorized")
        total_count = int(counts.get("total_current_item_count", 0) or 0)
        items.append(
            {
                "blocker_id": blocker_id,
                "migration_category": category,
                "priority_category_rank": MIGRATION_CATEGORY_PRIORITY.get(category, 99),
                "present_in_return_path_audit": bool(counts.get("present")),
                "total_current_item_count": total_count,
                "max_current_item_count_per_snapshot": int(
                    counts.get("max_current_item_count_per_snapshot", 0) or 0
                ),
                "snapshot_count_present": int(
                    counts.get("snapshot_count_present", 0) or 0
                ),
                "count_inference": counts.get("count_inference"),
                "install_point_count": int(blocker.get("install_point_count", 0) or 0),
                "current_owner": blocker.get("current_owner"),
                "current_scope": blocker.get("current_scope"),
                "target_owner": blocker.get("target_owner"),
                "target_scope": blocker.get("target_scope"),
                "migration_action": blocker.get("migration_action"),
                "source_file": blocker.get("source_file"),
            }
        )

    ranked_items = sorted(
        items,
        key=lambda item: (
            _priority_category_rank(item),
            -int(item.get("total_current_item_count", 0) or 0),
            -int(item.get("install_point_count", 0) or 0),
            str(item.get("blocker_id") or ""),
        ),
    )
    for index, item in enumerate(ranked_items, start=1):
        item["priority_rank"] = index

    backbone_items = [
        item
        for item in ranked_items
        if str(item.get("migration_category")) == "backbone_return"
    ]
    present_backbone_items = [
        item for item in backbone_items if bool(item.get("present_in_return_path_audit"))
    ]
    metadata = _shared_reachability_metadata(return_path_report)
    return {
        "scope_name": scope_name,
        "snapshot_count": return_path_report.get("snapshot_count"),
        "total_flow_count": return_path_report.get("total_flow_count"),
        "total_current_backbone_return_state_item_count": return_path_report.get(
            "total_current_backbone_return_state_item_count"
        ),
        "total_target_shared_backbone_return_state_item_count": return_path_report.get(
            "total_target_shared_backbone_return_state_item_count"
        ),
        "current_to_target_backbone_state_ratio": return_path_report.get(
            "current_to_target_backbone_state_ratio"
        ),
        "work_item_count": len(ranked_items),
        "present_work_item_count": sum(
            1 for item in ranked_items if bool(item.get("present_in_return_path_audit"))
        ),
        "backbone_work_item_count": len(backbone_items),
        "present_backbone_work_item_count": len(present_backbone_items),
        "top_backbone_work_item": (
            present_backbone_items[0]["blocker_id"] if present_backbone_items else ""
        ),
        **metadata,
        "next_backbone_migration_gate": _next_backbone_migration_gate(
            metadata_boundary_ready=bool(
                metadata.get("metadata_boundary_ready_for_backbone_migration")
            ),
            present_backbone_items=present_backbone_items,
        ),
        "work_items": ranked_items,
    }


def summarize_return_path_migration_worklist(
    return_path_report: Dict[str, Any],
) -> Dict[str, Any]:
    install_report = summarize_return_path_install_points()
    scopes = [
        _worklist_scope(
            scope_name="full",
            return_path_report=return_path_report,
            install_report=install_report,
        )
    ]
    selected = return_path_report.get("selected_runtime_scope")
    if isinstance(selected, dict) and selected:
        scopes.append(
            _worklist_scope(
                scope_name="selected_runtime",
                return_path_report=selected,
                install_report=install_report,
            )
        )
    return {
        "worklist_kind": "return_path_migration_worklist",
        "current_linux_realization": install_report.get("current_linux_realization"),
        "target_backbone_realization": install_report.get("target_backbone_realization"),
        "source_file": install_report.get("source_file"),
        "scope_count": len(scopes),
        "scopes": scopes,
    }


def render_return_path_migration_worklist_json(report: Dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True) + "\n"


def render_return_path_migration_worklist_summary(report: Dict[str, Any]) -> str:
    lines = [
        "Return path migration worklist",
        f"  current_linux_realization={report.get('current_linux_realization')}",
        f"  target_backbone_realization={report.get('target_backbone_realization')}",
        f"  scope_count={report.get('scope_count')}",
    ]
    for scope in list(report.get("scopes", [])):
        lines.append(
            "  scope="
            + str(scope.get("scope_name"))
            + " flows="
            + str(scope.get("total_flow_count"))
            + " current_backbone_items="
            + str(scope.get("total_current_backbone_return_state_item_count"))
            + " target_backbone_items="
            + str(scope.get("total_target_shared_backbone_return_state_item_count"))
            + " ratio="
            + str(scope.get("current_to_target_backbone_state_ratio"))
            + " top_backbone_work_item="
            + str(scope.get("top_backbone_work_item"))
            + " candidate_zone_routes="
            + str(scope.get("candidate_shared_zone_route_count"))
            + " candidate_zone_route_coverage="
            + str(scope.get("candidate_zone_route_coverage_ratio"))
            + " candidate_zone_route_complete="
            + str(scope.get("candidate_zone_route_complete"))
            + " metadata_boundary_ready="
            + str(scope.get("metadata_boundary_ready_for_backbone_migration"))
            + " next_gate="
            + str(scope.get("next_backbone_migration_gate"))
        )
        for item in list(scope.get("work_items", [])):
            if not bool(item.get("present_in_return_path_audit")):
                continue
            lines.append(
                "    rank="
                + str(item.get("priority_rank"))
                + " blocker="
                + str(item.get("blocker_id"))
                + " category="
                + str(item.get("migration_category"))
                + " current_items="
                + str(item.get("total_current_item_count"))
                + " install_points="
                + str(item.get("install_point_count"))
                + " inference="
                + str(item.get("count_inference"))
                + " action="
                + str(item.get("migration_action"))
            )
    return "\n".join(lines)


def render_return_path_install_points_json(report: Dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True) + "\n"


def render_return_path_install_points_summary(report: Dict[str, Any]) -> str:
    lines = [
        "Return path install-point audit",
        f"  current_linux_realization={report.get('current_linux_realization')}",
        f"  target_backbone_realization={report.get('target_backbone_realization')}",
        f"  source_file={report.get('source_file')}",
        f"  blocker_count={report.get('blocker_count')}",
        f"  backbone_blocker_count={report.get('backbone_blocker_count')}",
        f"  install_point_count={report.get('install_point_count')}",
    ]
    for category, totals in dict(report.get("category_totals", {})).items():
        lines.append(
            "  category="
            + str(category)
            + " blocker_count="
            + str(totals.get("blocker_count"))
            + " install_points="
            + str(totals.get("install_point_count"))
            + " blockers="
            + ",".join(list(totals.get("blocker_ids", [])))
        )
    for blocker in list(report.get("blockers", [])):
        lines.append(
            "  blocker="
            + str(blocker.get("blocker_id"))
            + " category="
            + str(blocker.get("migration_category"))
            + " current_scope="
            + str(blocker.get("current_scope"))
            + " target_scope="
            + str(blocker.get("target_scope"))
            + " install_points="
            + str(blocker.get("install_point_count"))
        )
    return "\n".join(lines)
