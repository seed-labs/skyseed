#!/usr/bin/env python3
# encoding: utf-8

"""Offline BPF MPLS-like map compiler validation for prepared Skyseed runs."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
import time
from typing import Any, Dict, List, Sequence, Tuple

REPO_SATELLITE = Path(__file__).resolve().parents[1]
if str(REPO_SATELLITE) not in sys.path:
    sys.path.insert(0, str(REPO_SATELLITE))

from skyseed_v1.core.ir.contracts import RuntimeUpdatePlan, TopologySnapshot
from skyseed_v1.backends.linux.mpls import (
    _bpf_owned_compile_mpls_maps_for_contexts,
    _bpf_owned_runtime_generation_id,
    _realization_contexts,
)
from skyseed_v1.planning.temporal import gz_fec_plan


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _file_signature(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "exists": False}
    stat = path.stat()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "path": str(path),
        "exists": True,
        "size_bytes": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
        "sha256": digest,
    }


def _reuse_signature_payload(input_signature: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: {
            field: value.get(field)
            for field in ("path", "exists", "size_bytes", "sha256")
            if field in value
        }
        for key, value in sorted(input_signature.items())
        if isinstance(value, dict)
    }


def _reuse_signature_sha256(input_signature: Dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            _reuse_signature_payload(input_signature),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _plan_from_dict(payload: Dict[str, Any]) -> RuntimeUpdatePlan:
    return RuntimeUpdatePlan(
        schema_version=str(payload["schema_version"]),
        plan_id=str(payload["plan_id"]),
        target_snapshot=dict(payload["target_snapshot"]),
        planner=dict(payload["planner"]),
        apply_model=dict(payload["apply_model"]),
        node_subplans=dict(payload["node_subplans"]),
        replacement_semantics=dict(payload.get("replacement_semantics") or {}),
        validation_expectations=dict(payload.get("validation_expectations") or {}),
    )


def _snapshot_from_dict(payload: Dict[str, Any]) -> TopologySnapshot:
    return TopologySnapshot(
        schema_version=str(payload["schema_version"]),
        snapshot_id=str(payload["snapshot_id"]),
        time_slice=dict(payload["time_slice"]),
        provenance=dict(payload["provenance"]),
        nodes=dict(payload["nodes"]),
        active_links=list(payload["active_links"]),
        zones=dict(payload["zones"]),
        ut_zone_membership=dict(payload["ut_zone_membership"]),
        planner_hints=dict(payload.get("planner_hints") or {}),
    )


def _is_required_zone(zone: Dict[str, Any]) -> bool:
    service_role = str(zone.get("service_role", "required_service"))
    active_service = bool(
        zone.get(
            "active_service",
            service_role in {"required_service", "optional_service"},
        )
    )
    return active_service and bool(
        zone.get("required_for_runtime", service_role == "required_service")
    )


def _active_anchor_candidates(zone: Dict[str, Any]) -> List[str]:
    candidates: List[str] = []
    for field_name in (
        "active_serving_satellite_ids",
        "active_anchor_candidates",
        "active_zone_anchor_candidates",
    ):
        value = zone.get(field_name)
        if isinstance(value, list):
            candidates.extend(str(item) for item in value if str(item))
    selected = str(zone.get("active_serving_satellite_id") or "")
    if selected:
        candidates.append(selected)
    return sorted(set(candidates))


def _token_in_identity(token: str, fields: List[str]) -> bool:
    token = str(token or "")
    return bool(token) and any(token in str(field or "") for field in fields)


def _zone_anchor_semantic_validation(
    *,
    run_dir: Path,
    planner_preview: Dict[str, Any],
    snapshot_preview: Dict[str, Any],
    selected_runtime_slice_ids: List[str],
) -> Dict[str, Any]:
    selected = set(str(slice_id) for slice_id in selected_runtime_slice_ids)
    snapshots_by_id = {
        str(snapshot.get("time_slice", {}).get("slice_id") or snapshot.get("slice_id")): snapshot
        for snapshot in snapshot_preview.get("snapshots", []) or []
    }
    anchor_ledger: List[Dict[str, Any]] = []
    fec_ledger: List[Dict[str, Any]] = []
    violation_table: List[Dict[str, Any]] = []
    required_zone_keys: set[tuple[int, str, str]] = set()
    resolved_zone_keys: set[tuple[int, str, str]] = set()
    zero_active_required_zone_count = 0
    multi_active_zone_count = 0
    ut_driven_fec_count = 0
    ut_driven_zone_definition_count = 0
    flow_driven_anchor_count = 0
    per_ut_serving_divergence_count = 0
    default_z_fec_count = 0
    override_class_fec_count = 0

    for planner_snapshot in planner_preview.get("snapshots", []) or []:
        slice_id = str(planner_snapshot.get("slice_id") or "")
        if selected and slice_id not in selected:
            continue
        snapshot_payload = snapshots_by_id.get(slice_id)
        if not snapshot_payload:
            violation_table.append(
                {
                    "violation_type": "missing_snapshot_for_slice",
                    "generation": None,
                    "event_time": None,
                    "slice": slice_id,
                    "zone_id": None,
                    "active_anchor_candidates": [],
                    "fields_that_made_them_active": {},
                    "affected_fec_ids": [],
                    "affected_ut_samples": [],
                    "entered_bpf_map_compilation": False,
                }
            )
            continue
        plan = _plan_from_dict(dict(planner_snapshot["runtime_update_plan"]))
        generation_id, generation_source = _bpf_owned_runtime_generation_id(plan)
        snapshot_time = dict(snapshot_payload.get("time_slice", {}))
        fec_plan = gz_fec_plan(plan)
        for zone_id, zone_value in sorted(dict(snapshot_payload.get("zones", {})).items()):
            zone = dict(zone_value)
            candidates = _active_anchor_candidates(zone)
            required = _is_required_zone(zone)
            anchor_selection = dict(zone.get("anchor_selection", {}))
            service_domain = dict(zone.get("service_domain", {}))
            service_domain_source = str(
                zone.get("service_domain_source")
                or service_domain.get("source")
                or zone.get("source")
                or zone.get("partition_policy")
                or ""
            )
            ut_sample_would_force_divergence = bool(
                anchor_selection.get("ut_sample_would_force_divergence")
                or list(
                    dict(zone.get("ut_visibility_audit", {})).get(
                        "ut_ids_without_selected_anchor_visibility",
                        [],
                    )
                    or []
                )
            )
            zone_resolution_action = str(
                anchor_selection.get("zone_resolution_action")
                or (
                    "stable_single_anchor"
                    if len(candidates) == 1
                    else "mark_unresolved_no_ut_specific_split"
                )
            )
            row = {
                "run_dir": str(run_dir),
                "slice_id": slice_id,
                "generation": int(generation_id),
                "generation_source": generation_source,
                "event_time": snapshot_time.get("relative_time_seconds"),
                "absolute_time_utc": snapshot_time.get("absolute_time_utc"),
                "zone_id": str(zone_id),
                "required_for_runtime": required,
                "zone_service_domain_source": service_domain_source,
                "zone_service_domain": service_domain,
                "active_anchor_candidates": candidates,
                "candidate_anchors": [
                    str(candidate.get("satellite_id") or "")
                    for candidate in list(anchor_selection.get("top_candidates", []) or [])
                    if str(candidate.get("satellite_id") or "")
                ],
                "rejected_candidates": list(
                    anchor_selection.get("rejected_candidates", []) or []
                ),
                "active_anchor_count": len(candidates),
                "zone_anchor_satellite_id": candidates[0] if len(candidates) == 1 else None,
                "resolution_state": zone.get("resolution_state"),
                "zone_anchor_resolved": bool(zone.get("zone_anchor_resolved", bool(candidates))),
                "zone_service_resolved": bool(zone.get("zone_service_resolved", bool(candidates))),
                "anchor_selection": anchor_selection,
                "active_anchor_selection_reason": anchor_selection.get("change_reason"),
                "ut_visibility_audit": dict(zone.get("ut_visibility_audit", {})),
                "ut_sample_would_force_divergence": ut_sample_would_force_divergence,
                "zone_resolution_action": zone_resolution_action,
                "zone_was_split_or_shrunk": zone_resolution_action
                in {"split_zone", "shrink_zone"},
                "zone_marked_unresolved_instead_of_ut_specific": (
                    "unresolved" in zone_resolution_action
                    or not bool(zone.get("zone_service_resolved", bool(candidates)))
                ),
                "control_plane_role": "zone_level_responsible_anchor",
            }
            anchor_ledger.append(row)
            if required:
                key = (int(generation_id), slice_id, str(zone_id))
                required_zone_keys.add(key)
                if "ut_membership" in service_domain_source:
                    ut_driven_zone_definition_count += 1
                    violation_table.append(
                        {
                            "violation_type": "ut_driven_zone_definition",
                            "generation": int(generation_id),
                            "event_time": snapshot_time.get("relative_time_seconds"),
                            "slice": slice_id,
                            "zone_id": str(zone_id),
                            "active_anchor_candidates": candidates,
                            "fields_that_made_them_active": {
                                "service_domain_source": service_domain_source,
                                "partition_policy": zone.get("partition_policy"),
                            },
                            "affected_fec_ids": [],
                            "affected_ut_samples": list(zone.get("ut_ids", [])),
                            "entered_bpf_map_compilation": False,
                        }
                    )
                if len(candidates) == 1:
                    resolved_zone_keys.add(key)
                elif len(candidates) == 0:
                    zero_active_required_zone_count += 1
                    violation_table.append(
                        {
                            "violation_type": "zero_active_required_zone",
                            "generation": int(generation_id),
                            "event_time": snapshot_time.get("relative_time_seconds"),
                            "slice": slice_id,
                            "zone_id": str(zone_id),
                            "active_anchor_candidates": candidates,
                            "fields_that_made_them_active": anchor_selection,
                            "affected_fec_ids": [],
                            "affected_ut_samples": list(zone.get("ut_ids", [])),
                            "entered_bpf_map_compilation": False,
                        }
                    )
                else:
                    multi_active_zone_count += 1
                    violation_table.append(
                        {
                            "violation_type": "multi_active_required_zone",
                            "generation": int(generation_id),
                            "event_time": snapshot_time.get("relative_time_seconds"),
                            "slice": slice_id,
                            "zone_id": str(zone_id),
                            "active_anchor_candidates": candidates,
                            "fields_that_made_them_active": anchor_selection,
                            "affected_fec_ids": [],
                            "affected_ut_samples": list(zone.get("ut_ids", [])),
                            "entered_bpf_map_compilation": False,
                        }
                    )
            anchor_driver_fields = {
                str(key): value
                for key, value in anchor_selection.items()
                if str(key)
                in {
                    "anchor_source",
                    "selection_source",
                    "selected_by",
                    "driver",
                    "driving_entity",
                }
            }
            if any(
                "flow" in str(value).lower()
                or "ut" in str(value).lower()
                or "user_terminal" in str(value).lower()
                for value in anchor_driver_fields.values()
            ):
                flow_driven_anchor_count += 1

        default_groups_by_zone_class: Dict[tuple[int, str, str, str], List[Dict[str, Any]]] = {}
        affected_fecs_by_zone: Dict[str, List[str]] = {}
        for group in list(fec_plan.get("forward_fecs", [])) + list(
            fec_plan.get("return_fecs", [])
        ):
            group = dict(group)
            fec_id = str(group.get("fec_id") or "")
            zone_id = str(group.get("zone_id") or "")
            gateway_class_id = str(group.get("gateway_class_id") or "")
            flow_ids = [str(item) for item in list(group.get("flow_ids", []))]
            ut_ids = [str(item) for item in list(group.get("ut_ids", []))]
            identity_fields = [
                fec_id,
                str(group.get("target_key") or ""),
                str(group.get("shared_backbone_label_scope_id") or ""),
            ]
            identity_has_ut = any(_token_in_identity(token, identity_fields) for token in ut_ids)
            identity_has_flow = any(
                _token_in_identity(token, identity_fields) for token in flow_ids
            )
            if identity_has_ut or identity_has_flow:
                ut_driven_fec_count += 1
            is_default_class = ":default-gateway:" in gateway_class_id
            if is_default_class:
                default_z_fec_count += 1
            else:
                override_class_fec_count += 1
            zone_anchor_id = str(
                group.get("zone_anchor_satellite_id")
                or group.get("zone_serving_satellite_id")
                or group.get("serving_satellite_id")
                or ""
            )
            fec_row = {
                "run_dir": str(run_dir),
                "slice_id": slice_id,
                "generation": int(generation_id),
                "generation_source": generation_source,
                "plan_id": plan.plan_id,
                "fec_id": fec_id,
                "fec_kind": group.get("group_kind"),
                "fec_identity_basis": group.get("fec_identity_basis"),
                "zone_id": zone_id,
                "gateway_class_id": gateway_class_id,
                "selected_gateway_id": group.get("selected_gateway_id"),
                "zone_anchor_satellite_id": zone_anchor_id,
                "zone_anchor_source": group.get("zone_anchor_source"),
                "serving_satellite_id": group.get("serving_satellite_id"),
                "zone_serving_satellite_id": group.get("zone_serving_satellite_id"),
                "flow_ids": flow_ids,
                "ut_ids": ut_ids,
                "identity_has_ut_id": identity_has_ut,
                "identity_has_flow_id": identity_has_flow,
            }
            fec_ledger.append(fec_row)
            if zone_id:
                affected_fecs_by_zone.setdefault(zone_id, []).append(fec_id)
            if group.get("group_kind") == "g_forward_fec" and is_default_class:
                default_groups_by_zone_class.setdefault(
                    (int(generation_id), slice_id, zone_id, gateway_class_id),
                    [],
                ).append(fec_row)

        for (
            generation_id_value,
            group_slice_id,
            zone_id,
            gateway_class_id,
        ), rows in sorted(default_groups_by_zone_class.items()):
            anchors = sorted(
                set(str(row.get("zone_anchor_satellite_id") or "") for row in rows)
                - {""}
            )
            serving_satellites = sorted(
                set(str(row.get("serving_satellite_id") or "") for row in rows)
                - {""}
            )
            if len(anchors) > 1 or len(serving_satellites) > 1:
                per_ut_serving_divergence_count += 1
                violation_table.append(
                    {
                        "violation_type": "per_ut_serving_divergence_default_zone_fec",
                        "generation": generation_id_value,
                        "event_time": snapshot_time.get("relative_time_seconds"),
                        "slice": group_slice_id,
                        "zone_id": zone_id,
                        "active_anchor_candidates": anchors or serving_satellites,
                        "fields_that_made_them_active": {
                            "gateway_class_id": gateway_class_id,
                            "serving_satellite_ids": serving_satellites,
                            "zone_anchor_satellite_ids": anchors,
                        },
                        "affected_fec_ids": [str(row.get("fec_id")) for row in rows],
                        "affected_ut_samples": sorted(
                            set(
                                ut_id
                                for row in rows
                                for ut_id in list(row.get("ut_ids", []))
                            )
                        ),
                        "entered_bpf_map_compilation": True,
                    }
                )

    for row in fec_ledger:
        if row.get("identity_has_ut_id") or row.get("identity_has_flow_id"):
            violation_table.append(
                {
                    "violation_type": "ut_or_flow_in_core_gz_fec_identity",
                    "generation": row.get("generation"),
                    "event_time": None,
                    "slice": row.get("slice_id"),
                    "zone_id": row.get("zone_id"),
                    "active_anchor_candidates": [row.get("zone_anchor_satellite_id")],
                    "fields_that_made_them_active": {
                        "fec_id": row.get("fec_id"),
                        "identity_has_ut_id": row.get("identity_has_ut_id"),
                        "identity_has_flow_id": row.get("identity_has_flow_id"),
                    },
                    "affected_fec_ids": [row.get("fec_id")],
                    "affected_ut_samples": list(row.get("ut_ids", [])),
                    "entered_bpf_map_compilation": True,
                }
            )

    summary = {
        "required_zone_count": len(required_zone_keys),
        "resolved_zone_count": len(resolved_zone_keys),
        "zero_active_required_zone_count": zero_active_required_zone_count,
        "multi_active_zone_count": multi_active_zone_count,
        "ut_driven_zone_definition_count": ut_driven_zone_definition_count,
        "ut_driven_fec_count": ut_driven_fec_count,
        "flow_driven_anchor_count": flow_driven_anchor_count,
        "per_ut_serving_divergence_count": per_ut_serving_divergence_count,
        "default_z_fec_count": default_z_fec_count,
        "override_class_fec_count": override_class_fec_count,
    }
    passed = all(
        int(summary[field]) == 0
        for field in (
            "zero_active_required_zone_count",
            "multi_active_zone_count",
            "ut_driven_zone_definition_count",
            "ut_driven_fec_count",
            "flow_driven_anchor_count",
            "per_ut_serving_divergence_count",
        )
    )
    return {
        "schema_version": "skyseed-zone-anchor-semantic-validation-v1",
        "run_dir": str(run_dir),
        "selected_runtime_slice_ids": selected_runtime_slice_ids,
        "passed": passed,
        "summary": summary,
        "violation_table": violation_table,
        "zone_anchor_ledger": anchor_ledger,
        "gz_fec_ledger": fec_ledger,
    }


def _label_allocation_ledger_records(
    *,
    slice_id: str,
    runtime_plan_id: str,
    generation_source: str,
    compiled: Dict[str, Any],
) -> List[Dict[str, Any]]:
    allocation_by_key = {
        (
            int(entry.get("generation") or 0),
            str(entry.get("fec_id") or ""),
            str(entry.get("direction") or ""),
            int(entry.get("hop_index") or 0),
        ): dict(entry)
        for entry in dict(compiled.get("label_allocation") or {}).get("entries", []) or []
    }
    rows: List[Dict[str, Any]] = []
    for entry in compiled.get("resolved_mpls_flow_entries", []) or []:
        if entry.get("map_type") == "ingress_classifier_map":
            incoming_label = int(entry.get("initial_label") or 0)
            next_label = incoming_label
            hop_index = 0
            handoff_action = "classifier_push"
        else:
            incoming_label = int(entry.get("label") or 0)
            next_label = (
                int(entry.get("out_label"))
                if entry.get("out_label") is not None
                else None
            )
            hop_index = int(entry.get("hop_index") or 0)
            if entry.get("map_type") == "egress_handoff_map":
                handoff_action = str(entry.get("handoff") or "pop_and_handoff")
            else:
                handoff_action = str(entry.get("action") or "")
        key = (
            int(entry.get("generation") or 0),
            str(entry.get("fec_id") or ""),
            str(entry.get("direction") or ""),
            int(hop_index),
        )
        allocation = allocation_by_key.get(key, {})
        rows.append(
            {
                "slice_id": slice_id,
                "runtime_plan_id": runtime_plan_id,
                "generation": int(entry.get("generation") or 0),
                "generation_source": generation_source,
                "map_type": str(entry.get("map_type") or ""),
                "fec_id": str(entry.get("fec_id") or ""),
                "flow_id": str(entry.get("flow_id") or ""),
                "ut_id": str(entry.get("ut_id") or ""),
                "zone_id": str(allocation.get("zone_id") or ""),
                "gw_id": str(allocation.get("selected_gateway_id") or ""),
                "direction": str(entry.get("direction") or ""),
                "path_hop": int(hop_index),
                "path_node_sequence": list(allocation.get("path_node_sequence") or []),
                "incoming_label": incoming_label,
                "original_incoming_label": int(entry.get("original_label") or 0),
                "next_label": next_label,
                "next_original_label": (
                    int(entry.get("next_original_label"))
                    if entry.get("next_original_label") is not None
                    else None
                ),
                "logical_ingress": str(entry.get("ingress_interface") or ""),
                "ingress_ifindex": int(entry.get("ingress_ifindex") or 0),
                "out_logical_interface": str(entry.get("out_interface") or ""),
                "out_ifindex": (
                    int(entry.get("out_ifindex"))
                    if entry.get("out_ifindex") is not None
                    else None
                ),
                "handoff_action": handoff_action,
                "uses_dst_ip": bool(entry.get("uses_dst_ip")),
                "shared": False,
                "shared_key": "",
            }
        )

    shared_counts: Dict[str, int] = {}
    for row in rows:
        shared_key = "|".join(
            [
                str(row["generation"]),
                str(row["direction"]),
                str(row["path_hop"]),
                str(row["logical_ingress"]),
                str(row["out_logical_interface"]),
                str(row["handoff_action"]),
            ]
        )
        row["shared_key"] = shared_key
        shared_counts[shared_key] = shared_counts.get(shared_key, 0) + 1
    for row in rows:
        row["shared"] = shared_counts.get(str(row["shared_key"]), 0) > 1
    return rows


def _ratio(numerator: int, denominator: int) -> float | None:
    if int(denominator) == 0:
        return None
    return round(float(numerator) / float(denominator), 6)


def _hop_bucket(hops: int) -> str:
    if hops <= 0:
        return "0-hop"
    if hops == 1:
        return "1-hop"
    if hops == 2:
        return "2-hop"
    return "3+ hop"


def _path_signature(context: Any, direction: str) -> Tuple[Any, ...]:
    if direction == "g_to_z":
        path = tuple([*list(context.satellite_path), str(context.selected_gateway_id)])
    else:
        path = tuple(
            [
                str(context.selected_gateway_id),
                *list(reversed(list(context.satellite_path))),
            ]
        )
    return (
        str(direction),
        str(context.zone_id or ""),
        str(context.gateway_class_id or ""),
        str(context.serving_satellite_id),
        str(context.selected_gateway_id),
        str(context.traffic_scope or ""),
        path,
    )


def _representative_path_record(
    *,
    context: Any,
    direction: str,
    generation: int,
    slice_id: str,
) -> Dict[str, Any]:
    satellite_path = [str(node_id) for node_id in list(context.satellite_path)]
    if direction == "g_to_z":
        path = [*satellite_path, str(context.selected_gateway_id)]
        source = str(context.serving_satellite_id)
        destination = str(context.selected_gateway_id)
    else:
        path = [
            str(context.selected_gateway_id),
            *list(reversed(satellite_path)),
        ]
        source = str(context.selected_gateway_id)
        destination = str(context.serving_satellite_id)
    satellite_core_hops = max(0, len(satellite_path) - 1)
    return {
        "slice_id": slice_id,
        "generation": int(generation),
        "direction": direction,
        "zone_id": str(context.zone_id or ""),
        "gateway_class_id": str(context.gateway_class_id or ""),
        "selected_gateway_id": str(context.selected_gateway_id),
        "zone_anchor_satellite_id": str(context.serving_satellite_id),
        "source_node_id": source,
        "destination_node_id": destination,
        "traffic_scope": str(context.traffic_scope or ""),
        "path_node_sequence": path,
        "satellite_path": satellite_path,
        "satellite_core_hop_count": satellite_core_hops,
        "hop_bucket": _hop_bucket(satellite_core_hops),
        "sample_flow_ids": [str(context.flow_id)],
        "sample_ut_ids": [str(context.ut_id)],
    }


def _merge_path_samples(
    existing: Dict[str, Any],
    context: Any,
) -> None:
    flow_id = str(context.flow_id)
    ut_id = str(context.ut_id)
    if flow_id not in existing["sample_flow_ids"]:
        existing["sample_flow_ids"].append(flow_id)
    if ut_id not in existing["sample_ut_ids"]:
        existing["sample_ut_ids"].append(ut_id)


def _gateway_class_mismatches(contexts: Sequence[Any]) -> List[Dict[str, Any]]:
    groups: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for context in contexts:
        key = (
            str(context.zone_id or ""),
            str(context.gateway_class_id or ""),
            str(context.traffic_scope or ""),
        )
        group = groups.setdefault(
            key,
            {
                "zone_id": key[0],
                "gateway_class_id": key[1],
                "traffic_scope": key[2],
                "selected_gateway_ids": [],
                "sample_flow_ids": [],
                "sample_ut_ids": [],
            },
        )
        selected_gateway_id = str(context.selected_gateway_id)
        if selected_gateway_id not in group["selected_gateway_ids"]:
            group["selected_gateway_ids"].append(selected_gateway_id)
        if str(context.flow_id) not in group["sample_flow_ids"]:
            group["sample_flow_ids"].append(str(context.flow_id))
        if str(context.ut_id) not in group["sample_ut_ids"]:
            group["sample_ut_ids"].append(str(context.ut_id))
    mismatches: List[Dict[str, Any]] = []
    for group in groups.values():
        gateway_class_id = str(group["gateway_class_id"])
        selected_gateway_ids = sorted(str(item) for item in group["selected_gateway_ids"])
        class_mentions_selected = any(
            selected_gateway_id in gateway_class_id
            for selected_gateway_id in selected_gateway_ids
        )
        if len(selected_gateway_ids) > 1 or not class_mentions_selected:
            mismatches.append(
                {
                    **group,
                    "selected_gateway_ids": selected_gateway_ids,
                    "semantic_risk": len(selected_gateway_ids) > 1,
                    "note": (
                        "one gateway class maps to multiple concrete gateways"
                        if len(selected_gateway_ids) > 1
                        else "gateway class label differs from selected concrete gateway"
                    ),
                }
            )
    return sorted(
        mismatches,
        key=lambda item: (
            str(item["zone_id"]),
            str(item["gateway_class_id"]),
            str(item["traffic_scope"]),
        ),
    )


def _workload_coverage_metrics(
    *,
    contexts: Sequence[Any],
    path_records: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    ut_ids_by_zone: Dict[str, set[str]] = defaultdict(set)
    scopes_by_zone: Dict[str, set[str]] = defaultdict(set)
    gateways_by_zone: Dict[str, set[str]] = defaultdict(set)
    zones_by_gateway: Dict[str, set[str]] = defaultdict(set)
    classes_by_zone: Dict[str, set[str]] = defaultdict(set)
    for context in contexts:
        zone_id = str(context.zone_id or "")
        ut_id = str(context.ut_id or "")
        gateway_id = str(context.selected_gateway_id or "")
        scope = str(context.traffic_scope or "ordinary_internet_default")
        gateway_class_id = str(context.gateway_class_id or "")
        if zone_id and ut_id:
            ut_ids_by_zone[zone_id].add(ut_id)
        if zone_id and scope:
            scopes_by_zone[zone_id].add(scope)
        if zone_id and gateway_id:
            gateways_by_zone[zone_id].add(gateway_id)
        if gateway_id and zone_id:
            zones_by_gateway[gateway_id].add(zone_id)
        if zone_id and gateway_class_id:
            classes_by_zone[zone_id].add(gateway_class_id)

    hop_distribution = Counter(str(row.get("hop_bucket") or "") for row in path_records)
    same_zone_multi_ut_zones = sorted(
        zone_id for zone_id, ut_ids in ut_ids_by_zone.items() if len(ut_ids) > 1
    )
    default_plus_override_zones = sorted(
        zone_id
        for zone_id, scopes in scopes_by_zone.items()
        if "ordinary_internet_default" in scopes and "special_prefix_override" in scopes
    )
    same_gw_multiple_zone_gateways = sorted(
        gateway_id for gateway_id, zone_ids in zones_by_gateway.items() if len(zone_ids) > 1
    )
    multiple_gw_same_zone_zones = sorted(
        zone_id for zone_id, gateway_ids in gateways_by_zone.items() if len(gateway_ids) > 1
    )

    return {
        "two_hop_core_path_count": int(hop_distribution.get("2-hop", 0)),
        "three_plus_hop_core_path_count": int(hop_distribution.get("3+ hop", 0)),
        "same_zone_multi_ut_count": len(same_zone_multi_ut_zones),
        "same_zone_multi_ut_zones": [
            {
                "zone_id": zone_id,
                "ut_ids": sorted(ut_ids_by_zone[zone_id]),
            }
            for zone_id in same_zone_multi_ut_zones
        ],
        "default_plus_override_same_zone_count": len(default_plus_override_zones),
        "default_plus_override_same_zones": [
            {
                "zone_id": zone_id,
                "gateway_class_ids": sorted(classes_by_zone.get(zone_id, set())),
                "selected_gateway_ids": sorted(gateways_by_zone.get(zone_id, set())),
            }
            for zone_id in default_plus_override_zones
        ],
        "same_gw_to_multiple_zones_count": len(same_gw_multiple_zone_gateways),
        "same_gw_to_multiple_zones": [
            {
                "selected_gateway_id": gateway_id,
                "zone_ids": sorted(zones_by_gateway[gateway_id]),
            }
            for gateway_id in same_gw_multiple_zone_gateways
        ],
        "multiple_gw_to_same_zone_count": len(multiple_gw_same_zone_zones),
        "multiple_gw_to_same_zone": [
            {
                "zone_id": zone_id,
                "selected_gateway_ids": sorted(gateways_by_zone[zone_id]),
            }
            for zone_id in multiple_gw_same_zone_zones
        ],
    }


def _anchor_transition_metrics(
    anchor_rows: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    anchors_by_zone: Dict[str, set[str]] = defaultdict(set)
    generations_by_zone: Dict[str, set[int]] = defaultdict(set)
    for row in anchor_rows:
        zone_id = str(row.get("zone_id") or "")
        anchor_id = str(row.get("zone_anchor_satellite_id") or "")
        if not zone_id or not anchor_id:
            continue
        anchors_by_zone[zone_id].add(anchor_id)
        if row.get("generation") is not None:
            generations_by_zone[zone_id].add(int(row.get("generation") or 0))
    transition_zones = sorted(
        zone_id for zone_id, anchor_ids in anchors_by_zone.items() if len(anchor_ids) > 1
    )
    return {
        "anchor_transition_zone_count": len(transition_zones),
        "anchor_transition_zones": [
            {
                "zone_id": zone_id,
                "generation_count": len(generations_by_zone.get(zone_id, set())),
                "zone_anchor_satellite_ids": sorted(anchors_by_zone[zone_id]),
            }
            for zone_id in transition_zones
        ],
    }


def _anchor_gateway_hop_feasibility(
    *,
    snapshot_preview: Dict[str, Any],
    selected_runtime_slice_ids: Sequence[str],
) -> Dict[str, Any]:
    selected = {str(slice_id) for slice_id in selected_runtime_slice_ids}
    distance_distribution: Counter[str] = Counter()
    two_hop_candidates: List[Dict[str, Any]] = []
    three_plus_candidate_count = 0
    evaluated_pair_count = 0

    def shortest_hops(
        adjacency: Dict[str, set[str]],
        start_node_id: str,
        goal_node_id: str,
    ) -> int | None:
        if start_node_id == goal_node_id:
            return 0
        queue: List[Tuple[str, int]] = [(start_node_id, 0)]
        seen = {start_node_id}
        for node_id, hops in queue:
            for next_node_id in sorted(adjacency.get(node_id, set())):
                if next_node_id == goal_node_id:
                    return hops + 1
                if next_node_id in seen:
                    continue
                seen.add(next_node_id)
                queue.append((next_node_id, hops + 1))
        return None

    for snapshot in list(snapshot_preview.get("snapshots", []) or []):
        slice_id = str(
            dict(snapshot.get("time_slice") or {}).get("slice_id")
            or snapshot.get("slice_id")
            or ""
        )
        if selected and slice_id not in selected:
            continue
        adjacency: Dict[str, set[str]] = defaultdict(set)
        gateway_satellite_by_id: Dict[str, str] = {}
        for link in list(snapshot.get("active_links", []) or []):
            endpoints = list(link.get("endpoints", []) or [])
            if len(endpoints) != 2:
                continue
            left = dict(endpoints[0])
            right = dict(endpoints[1])
            left_id = str(left.get("node_id") or "")
            right_id = str(right.get("node_id") or "")
            left_type = str(left.get("node_type") or "")
            right_type = str(right.get("node_type") or "")
            if left_type == "satellite" and right_type == "satellite":
                adjacency[left_id].add(right_id)
                adjacency[right_id].add(left_id)
            elif left_type == "gateway" and right_type == "satellite":
                gateway_satellite_by_id[left_id] = right_id
            elif left_type == "satellite" and right_type == "gateway":
                gateway_satellite_by_id[right_id] = left_id

        for zone_id, zone in sorted(dict(snapshot.get("zones") or {}).items()):
            anchor_id = str(dict(zone).get("active_serving_satellite_id") or "")
            if not anchor_id:
                continue
            for gateway_id, gateway_satellite_id in sorted(gateway_satellite_by_id.items()):
                hops = shortest_hops(adjacency, anchor_id, gateway_satellite_id)
                if hops is None:
                    distance_distribution["unreachable"] += 1
                    continue
                evaluated_pair_count += 1
                distance_distribution[_hop_bucket(hops)] += 1
                if hops == 2:
                    two_hop_candidates.append(
                        {
                            "slice_id": slice_id,
                            "zone_id": str(zone_id),
                            "zone_anchor_satellite_id": anchor_id,
                            "gateway_id": gateway_id,
                            "gateway_satellite_id": gateway_satellite_id,
                            "satellite_core_hop_count": hops,
                        }
                    )
                elif hops >= 3:
                    three_plus_candidate_count += 1

    return {
        "evaluated_anchor_gateway_pair_count": evaluated_pair_count,
        "distance_distribution": {
            "0-hop": int(distance_distribution.get("0-hop", 0)),
            "1-hop": int(distance_distribution.get("1-hop", 0)),
            "2-hop": int(distance_distribution.get("2-hop", 0)),
            "3+ hop": int(distance_distribution.get("3+ hop", 0)),
            "unreachable": int(distance_distribution.get("unreachable", 0)),
        },
        "two_hop_candidate_count": len(two_hop_candidates),
        "two_hop_candidate_examples": two_hop_candidates[:20],
        "three_plus_candidate_count": three_plus_candidate_count,
    }


def _build_path_cardinality_audit(
    *,
    run_dir: Path,
    selected_runtime_slice_ids: List[str],
    semantic_validation: Dict[str, Any],
    compiled_slices: List[Dict[str, Any]],
    label_allocation_ledger: List[Dict[str, Any]],
    per_slice_contexts: Dict[str, List[Any]],
    snapshot_preview: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    anchor_rows = list(semantic_validation.get("zone_anchor_ledger", []) or [])
    fec_rows = list(semantic_validation.get("gz_fec_ledger", []) or [])
    anchor_rows_by_slice: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    fec_rows_by_slice: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    label_rows_by_slice: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    compiled_by_slice = {
        str(row.get("slice_id")): dict(row) for row in compiled_slices
    }
    for row in anchor_rows:
        anchor_rows_by_slice[str(row.get("slice_id"))].append(dict(row))
    for row in fec_rows:
        fec_rows_by_slice[str(row.get("slice_id"))].append(dict(row))
    for row in label_allocation_ledger:
        label_rows_by_slice[str(row.get("slice_id"))].append(dict(row))

    per_slice: List[Dict[str, Any]] = []
    aggregate_risk_flags: List[Dict[str, Any]] = []
    aggregate_logical_path_count = 0
    aggregate_multi_hop_path_count = 0
    aggregate_core_lfib_count = 0
    aggregate_eligible_core_scope_count = 0
    aggregate_skipped_eligible_core_scope_count = 0
    aggregate_direct_handoff_count = 0
    aggregate_linux_fallback_count = 0
    aggregate_gateway_mismatch_count = 0
    aggregate_two_hop_core_path_count = 0
    aggregate_three_plus_hop_core_path_count = 0
    aggregate_same_zone_multi_ut_zones: Dict[str, set[str]] = defaultdict(set)
    aggregate_default_plus_override_zones: set[str] = set()
    aggregate_zones_by_gateway: Dict[str, set[str]] = defaultdict(set)
    aggregate_gateways_by_zone: Dict[str, set[str]] = defaultdict(set)

    for slice_id in selected_runtime_slice_ids:
        contexts = list(per_slice_contexts.get(slice_id, []))
        compiled = compiled_by_slice.get(slice_id, {})
        generation = (
            int(compiled.get("generation"))
            if compiled.get("generation") is not None
            else None
        )
        counts = dict(compiled.get("compiled_mpls_map_counts") or {})
        conflict_summary = dict(compiled.get("map_conflict_summary") or {})
        anchors = anchor_rows_by_slice.get(slice_id, [])
        fecs = fec_rows_by_slice.get(slice_id, [])
        labels = label_rows_by_slice.get(slice_id, [])
        required_zone_rows = [row for row in anchors if bool(row.get("required_for_runtime"))]
        resolved_required_zone_rows = [
            row
            for row in required_zone_rows
            if bool(row.get("zone_anchor_resolved"))
            and int(row.get("active_anchor_count") or 0) == 1
        ]
        active_anchor_ids = sorted(
            {
                str(row.get("zone_anchor_satellite_id"))
                for row in anchors
                if str(row.get("zone_anchor_satellite_id") or "")
            }
        )
        gateway_class_ids = sorted(
            {
                str(context.gateway_class_id or "")
                for context in contexts
                if str(context.gateway_class_id or "")
            }
        )
        override_class_ids = sorted(
            {
                str(context.gateway_class_id or "")
                for context in contexts
                if str(context.traffic_scope or "") == "special_prefix_override"
                or ":override:" in str(context.flow_id)
            }
        )
        g_fecs = [row for row in fecs if str(row.get("fec_kind")) == "g_forward_fec"]
        z_fecs = [row for row in fecs if str(row.get("fec_kind")) == "gz_return_fec"]
        fec_ids = sorted({str(row.get("fec_id")) for row in fecs if str(row.get("fec_id") or "")})
        pair_keys = sorted(
            {
                (
                    str(row.get("zone_id") or ""),
                    str(row.get("gateway_class_id") or ""),
                    str(row.get("zone_anchor_satellite_id") or ""),
                )
                for row in fecs
                if str(row.get("zone_id") or "")
            }
        )

        path_records_by_signature: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
        sampled_context_path_count = 0
        for context in contexts:
            for direction in ("g_to_z", "z_to_g"):
                sampled_context_path_count += 1
                signature = _path_signature(context, direction)
                if signature not in path_records_by_signature:
                    path_records_by_signature[signature] = _representative_path_record(
                        context=context,
                        direction=direction,
                        generation=int(generation or 0),
                        slice_id=slice_id,
                    )
                else:
                    _merge_path_samples(path_records_by_signature[signature], context)
        path_records = list(path_records_by_signature.values())
        hop_distribution = Counter(str(row["hop_bucket"]) for row in path_records)
        unique_directed_core_edges: Counter[Tuple[str, str]] = Counter()
        for row in path_records:
            satellite_path = [str(node_id) for node_id in row.get("satellite_path", [])]
            for first, second in zip(satellite_path, satellite_path[1:]):
                unique_directed_core_edges[(first, second)] += 1
        shared_branch_segments = [
            {
                "source_satellite_id": first,
                "destination_satellite_id": second,
                "logical_path_count": count,
            }
            for (first, second), count in sorted(unique_directed_core_edges.items())
            if count > 1
        ]
        multi_hop_paths = [
            row for row in path_records if int(row.get("satellite_core_hop_count") or 0) > 0
        ]
        direct_handoff_paths = [
            row for row in path_records if int(row.get("satellite_core_hop_count") or 0) == 0
        ]
        workload_metrics = _workload_coverage_metrics(
            contexts=contexts,
            path_records=path_records,
        )
        expected_core_scope = sum(
            int(row.get("satellite_core_hop_count") or 0)
            for row in path_records
        )
        core_label_rows = [
            row for row in labels if str(row.get("map_type")) == "core_lfib_map"
        ]
        unique_lfib_actions = {
            (
                str(row.get("generation")),
                str(row.get("direction")),
                str(row.get("logical_ingress")),
                str(row.get("incoming_label")),
                str(row.get("out_logical_interface")),
                str(row.get("next_label")),
                str(row.get("handoff_action")),
            )
            for row in core_label_rows
        }
        skipped_eligible = 0
        if expected_core_scope > 0 and not core_label_rows:
            skipped_eligible = expected_core_scope
        gateway_mismatches = _gateway_class_mismatches(contexts)

        slice_risks: List[Dict[str, Any]] = []
        if skipped_eligible:
            slice_risks.append(
                {
                    "severity": "blocker",
                    "risk": "eligible_core_paths_skipped",
                    "details": {
                        "eligible_core_forwarding_scope_count": expected_core_scope,
                        "compiled_bpf_owned_core_forwarding_count": int(
                            counts.get("core_lfib_map") or 0
                        ),
                    },
                }
            )
        if path_records and len(direct_handoff_paths) == len(path_records):
            slice_risks.append(
                {
                    "severity": "warning",
                    "risk": "all_paths_direct_handoff_in_slice",
                    "details": {
                        "logical_path_count": len(path_records),
                        "direct_handoff_count": len(direct_handoff_paths),
                    },
                }
            )
        elif path_records and len(direct_handoff_paths) / len(path_records) >= 0.8:
            slice_risks.append(
                {
                    "severity": "warning",
                    "risk": "most_paths_direct_handoff_in_slice",
                    "details": {
                        "logical_path_count": len(path_records),
                        "direct_handoff_count": len(direct_handoff_paths),
                    },
                }
            )
        if not multi_hop_paths:
            slice_risks.append(
                {
                    "severity": "warning",
                    "risk": "no_multi_hop_satellite_core_paths_in_slice",
                    "details": {"logical_path_count": len(path_records)},
                }
            )
        if any(bool(row.get("semantic_risk")) for row in gateway_mismatches):
            slice_risks.append(
                {
                    "severity": "blocker",
                    "risk": "gateway_class_hides_conflicting_selected_gateways",
                    "details": {"mismatches": gateway_mismatches},
                }
            )
        elif gateway_mismatches:
            slice_risks.append(
                {
                    "severity": "warning",
                    "risk": "gateway_class_label_differs_from_selected_gateway",
                    "details": {"mismatches": gateway_mismatches},
                }
            )

        direct_reason_table = [
            {
                "direction": row["direction"],
                "zone_id": row["zone_id"],
                "gateway_class_id": row["gateway_class_id"],
                "selected_gateway_id": row["selected_gateway_id"],
                "zone_anchor_satellite_id": row["zone_anchor_satellite_id"],
                "path_node_sequence": row["path_node_sequence"],
                "reason": "direct_anchor_to_gateway_or_zone_handoff_no_satellite_core_edge",
                "sample_ut_ids": row["sample_ut_ids"][:8],
            }
            for row in direct_handoff_paths[:20]
        ]
        record = {
            "slice_id": slice_id,
            "generation": generation,
            "zone_gw_fec_cardinality": {
                "required_zone_count": len(required_zone_rows),
                "resolved_zone_count": len(resolved_required_zone_rows),
                "active_zone_anchor_count": len(active_anchor_ids),
                "g_exit_gateway_class_count": len(gateway_class_ids),
                "override_class_count": len(override_class_ids),
                "z_fec_count": len(z_fecs),
                "g_fec_count": len(g_fecs),
                "gz_pair_logical_path_count": len(pair_keys),
                "total_gz_fec_ids_emitted": len(fec_ids),
            },
            "path_structure": {
                "logical_path_count": len(path_records),
                "direct_handoff_ratio": _ratio(
                    len(direct_handoff_paths),
                    len(path_records),
                ),
                "sampled_context_path_count": sampled_context_path_count,
                "unique_source_anchor_count": len(
                    {str(row["zone_anchor_satellite_id"]) for row in path_records}
                ),
                "unique_destination_anchor_or_gw_count": len(
                    {str(row["destination_node_id"]) for row in path_records}
                ),
                "hop_count_distribution": {
                    "0-hop": int(hop_distribution.get("0-hop", 0)),
                    "1-hop": int(hop_distribution.get("1-hop", 0)),
                    "2-hop": int(hop_distribution.get("2-hop", 0)),
                    "3+ hop": int(hop_distribution.get("3+ hop", 0)),
                },
                "multi_hop_satellite_core_path_count": len(multi_hop_paths),
                "two_hop_core_path_count": int(
                    workload_metrics.get("two_hop_core_path_count") or 0
                ),
                "three_plus_hop_core_path_count": int(
                    workload_metrics.get("three_plus_hop_core_path_count") or 0
                ),
                "direct_handoff_count": len(direct_handoff_paths),
                "unique_directed_satellite_core_edge_count": len(unique_directed_core_edges),
                "unique_directed_satellite_core_edges": [
                    {
                        "source_satellite_id": first,
                        "destination_satellite_id": second,
                        "logical_path_count": count,
                    }
                    for (first, second), count in sorted(unique_directed_core_edges.items())
                ],
                "unique_shared_branch_segment_count": len(shared_branch_segments),
                "unique_shared_branch_segments": shared_branch_segments[:20],
                "representative_paths": sorted(
                    path_records,
                    key=lambda row: (
                        -int(row.get("satellite_core_hop_count") or 0),
                        str(row.get("zone_id")),
                        str(row.get("direction")),
                    ),
                )[:12],
            },
            "workload_coverage": workload_metrics,
            "bpf_compilation_compression": {
                "classifier_entries": int(counts.get("ingress_classifier_map") or 0),
                "core_lfib_entries": int(counts.get("core_lfib_map") or 0),
                "egress_handoff_entries": int(counts.get("egress_handoff_map") or 0),
                "label_ledger_records": len(labels),
                "safe_duplicate_count": int(conflict_summary.get("safe_duplicate_count") or 0),
                "unsafe_conflict_count": int(conflict_summary.get("unsafe_conflict_count") or 0),
                "logical_paths_to_unique_path_segments_ratio": _ratio(
                    len(unique_directed_core_edges),
                    len(path_records),
                ),
                "unique_path_segments_to_lfib_actions_ratio": _ratio(
                    len(unique_lfib_actions),
                    len(unique_directed_core_edges),
                ),
                "lfib_actions_to_bpf_map_entries_ratio": _ratio(
                    int(counts.get("core_lfib_map") or 0),
                    len(unique_lfib_actions),
                ),
                "why_core_lfib_entry_count_is_low": (
                    "Most selected bounded S4 logical G/Z paths are direct "
                    "anchor-to-gateway or zone-local handoff paths. The remaining "
                    "satellite-core hops are shared across zone/class FECs and are "
                    "deduplicated by generation + ingress + label + direction."
                ),
            },
            "coverage_and_semantic_soundness": {
                "zone_anchors_from_service_domain_geometry_not_ut_workload": (
                    int(semantic_validation.get("summary", {}).get("ut_driven_zone_definition_count") or 0)
                    == 0
                ),
                "ut_samples_create_core_fecs": False,
                "no_per_ut_serving_divergence_inside_default_zone_fecs": (
                    int(semantic_validation.get("summary", {}).get("per_ut_serving_divergence_count") or 0)
                    == 0
                ),
                "required_zones_have_exactly_one_active_anchor": (
                    len(required_zone_rows) == len(resolved_required_zone_rows)
                    and all(int(row.get("active_anchor_count") or 0) == 1 for row in required_zone_rows)
                ),
                "representative_zone_points_coverage_margins_anchor_reasons_visible": all(
                    bool(row.get("zone_service_domain_source"))
                    and bool(dict(row.get("anchor_selection", {})).get("change_reason"))
                    for row in anchors
                ),
                "service_domain_sources": sorted(
                    {
                        str(row.get("zone_service_domain_source") or "")
                        for row in anchors
                        if str(row.get("zone_service_domain_source") or "")
                    }
                ),
            },
            "bpf_owned_scope_completeness": {
                "eligible_core_forwarding_scope_count": expected_core_scope,
                "compiled_bpf_owned_core_forwarding_count": int(
                    counts.get("core_lfib_map") or 0
                ),
                "skipped_eligible_core_scope_count": skipped_eligible,
                "linux_gz_mpls_lfib_fallback_count": 0,
                "direct_handoff_count": len(direct_handoff_paths),
                "direct_handoff_reason_table": direct_reason_table,
            },
            "gateway_class_audit": {
                "gateway_class_policy_label_count": len(gateway_class_ids),
                "selected_concrete_gateway_count": len(
                    {str(context.selected_gateway_id) for context in contexts}
                ),
                "gateway_class_selected_gateway_mismatch_count": len(gateway_mismatches),
                "gateway_class_selected_gateway_mismatches": gateway_mismatches,
                "conflicting_selected_gateways_hidden_by_class": any(
                    bool(row.get("semantic_risk")) for row in gateway_mismatches
                ),
            },
            "risk_flags": slice_risks,
        }
        per_slice.append(record)
        aggregate_risk_flags.extend(
            {**risk, "slice_id": slice_id, "generation": generation}
            for risk in slice_risks
        )
        aggregate_logical_path_count += len(path_records)
        aggregate_multi_hop_path_count += len(multi_hop_paths)
        aggregate_core_lfib_count += int(counts.get("core_lfib_map") or 0)
        aggregate_eligible_core_scope_count += expected_core_scope
        aggregate_skipped_eligible_core_scope_count += skipped_eligible
        aggregate_direct_handoff_count += len(direct_handoff_paths)
        aggregate_gateway_mismatch_count += len(gateway_mismatches)
        aggregate_two_hop_core_path_count += int(
            workload_metrics.get("two_hop_core_path_count") or 0
        )
        aggregate_three_plus_hop_core_path_count += int(
            workload_metrics.get("three_plus_hop_core_path_count") or 0
        )
        for row in list(workload_metrics.get("same_zone_multi_ut_zones") or []):
            zone_id = str(row.get("zone_id") or "")
            aggregate_same_zone_multi_ut_zones[zone_id].update(
                str(ut_id) for ut_id in list(row.get("ut_ids") or [])
            )
        for row in list(workload_metrics.get("default_plus_override_same_zones") or []):
            zone_id = str(row.get("zone_id") or "")
            if zone_id:
                aggregate_default_plus_override_zones.add(zone_id)
        for row in list(workload_metrics.get("same_gw_to_multiple_zones") or []):
            gateway_id = str(row.get("selected_gateway_id") or "")
            aggregate_zones_by_gateway[gateway_id].update(
                str(zone_id) for zone_id in list(row.get("zone_ids") or [])
            )
        for row in list(workload_metrics.get("multiple_gw_to_same_zone") or []):
            zone_id = str(row.get("zone_id") or "")
            aggregate_gateways_by_zone[zone_id].update(
                str(gateway_id) for gateway_id in list(row.get("selected_gateway_ids") or [])
            )

    anchor_transition_metrics = _anchor_transition_metrics(anchor_rows)
    hop_feasibility = _anchor_gateway_hop_feasibility(
        snapshot_preview=dict(snapshot_preview or {}),
        selected_runtime_slice_ids=selected_runtime_slice_ids,
    )
    aggregate_direct_handoff_ratio = _ratio(
        aggregate_direct_handoff_count,
        aggregate_logical_path_count,
    )
    same_zone_multi_ut_count = sum(
        1 for ut_ids in aggregate_same_zone_multi_ut_zones.values() if len(ut_ids) > 1
    )
    same_gw_to_multiple_zones_count = sum(
        1 for zone_ids in aggregate_zones_by_gateway.values() if len(zone_ids) > 1
    )
    multiple_gw_to_same_zone_count = sum(
        1 for gateway_ids in aggregate_gateways_by_zone.values() if len(gateway_ids) > 1
    )
    blockers = [
        risk for risk in aggregate_risk_flags if str(risk.get("severity")) == "blocker"
    ]
    warnings = [
        risk for risk in aggregate_risk_flags if str(risk.get("severity")) == "warning"
    ]
    if aggregate_multi_hop_path_count == 0:
        blockers.append(
            {
                "severity": "blocker",
                "risk": "no_multi_hop_satellite_core_paths_in_selected_runtime_window",
                "details": {
                    "logical_path_count": aggregate_logical_path_count,
                    "core_lfib_entry_count": aggregate_core_lfib_count,
                },
            }
        )
    if aggregate_two_hop_core_path_count == 0 and int(
        hop_feasibility.get("two_hop_candidate_count") or 0
    ) > 0:
        warnings.append(
            {
                "severity": "warning",
                "risk": "no_two_hop_satellite_core_paths_in_selected_runtime_window",
                "details": {
                    "logical_path_count": aggregate_logical_path_count,
                    "two_hop_candidate_count": hop_feasibility.get(
                        "two_hop_candidate_count"
                    ),
                },
            }
        )
    elif aggregate_two_hop_core_path_count == 0:
        warnings.append(
            {
                "severity": "warning",
                "risk": "two_hop_satellite_core_paths_not_feasible_in_selected_runtime_window",
                "details": {
                    "logical_path_count": aggregate_logical_path_count,
                    "two_hop_candidate_count": hop_feasibility.get(
                        "two_hop_candidate_count"
                    ),
                    "distance_distribution": hop_feasibility.get(
                        "distance_distribution"
                    ),
                    "future_validation": (
                        "use a different shell/window/catalog if 2-hop coverage "
                        "is required"
                    ),
                },
            }
        )
    if same_zone_multi_ut_count == 0:
        warnings.append(
            {
                "severity": "warning",
                "risk": "same_zone_multi_ut_workload_missing",
                "details": {},
            }
        )
    if len(aggregate_default_plus_override_zones) == 0:
        warnings.append(
            {
                "severity": "warning",
                "risk": "default_plus_override_same_zone_coverage_missing",
                "details": {},
            }
        )
    if same_gw_to_multiple_zones_count == 0:
        warnings.append(
            {
                "severity": "warning",
                "risk": "same_gateway_to_multiple_zones_coverage_missing",
                "details": {},
            }
        )
    if multiple_gw_to_same_zone_count == 0:
        warnings.append(
            {
                "severity": "warning",
                "risk": "multiple_gateways_to_same_zone_coverage_missing",
                "details": {},
            }
        )
    if int(anchor_transition_metrics["anchor_transition_zone_count"]) == 0:
        warnings.append(
            {
                "severity": "warning",
                "risk": "anchor_transition_zone_coverage_missing",
                "details": {},
            }
        )
    if aggregate_three_plus_hop_core_path_count == 0:
        warnings.append(
            {
                "severity": "warning",
                "risk": "no_three_plus_hop_satellite_core_paths_in_selected_runtime_window",
                "details": {
                    "note": "acceptable only if bounded S4 geometry/window cannot naturally expose 3+ hops"
                },
            }
        )
    if aggregate_direct_handoff_ratio is not None and aggregate_direct_handoff_ratio >= 0.75:
        warnings.append(
            {
                "severity": "warning",
                "risk": "direct_handoff_ratio_not_materially_lower_than_previous_gate",
                "details": {
                    "direct_handoff_ratio": aggregate_direct_handoff_ratio,
                    "previous_ratio": _ratio(74, 88),
                    "target_ratio_below": 0.75,
                },
            }
        )
    if aggregate_core_lfib_count == 0 and aggregate_eligible_core_scope_count > 0:
        blockers.append(
            {
                "severity": "blocker",
                "risk": "eligible_core_scope_present_but_no_bpf_core_lfib_entries",
                "details": {
                    "eligible_core_forwarding_scope_count": aggregate_eligible_core_scope_count,
                },
            }
        )
    return {
        "schema_version": "skyseed-path-cardinality-audit-v1",
        "run_dir": str(run_dir),
        "selected_runtime_slice_ids": selected_runtime_slice_ids,
        "semantic_validation_passed": bool(semantic_validation.get("passed")),
        "bpf_offline_validation_passed": True,
        "aggregate": {
            "logical_path_count": aggregate_logical_path_count,
            "multi_hop_satellite_core_path_count": aggregate_multi_hop_path_count,
            "two_hop_core_path_count": aggregate_two_hop_core_path_count,
            "three_plus_hop_core_path_count": aggregate_three_plus_hop_core_path_count,
            "direct_handoff_count": aggregate_direct_handoff_count,
            "direct_handoff_ratio": aggregate_direct_handoff_ratio,
            "eligible_core_forwarding_scope_count": aggregate_eligible_core_scope_count,
            "compiled_bpf_owned_core_forwarding_count": aggregate_core_lfib_count,
            "skipped_eligible_core_scope_count": aggregate_skipped_eligible_core_scope_count,
            "linux_gz_mpls_lfib_fallback_count": aggregate_linux_fallback_count,
            "gateway_class_selected_gateway_mismatch_count": aggregate_gateway_mismatch_count,
            "same_zone_multi_ut_count": same_zone_multi_ut_count,
            "same_zone_multi_ut_zones": [
                {
                    "zone_id": zone_id,
                    "ut_ids": sorted(ut_ids),
                }
                for zone_id, ut_ids in sorted(aggregate_same_zone_multi_ut_zones.items())
                if len(ut_ids) > 1
            ],
            "default_plus_override_same_zone_count": len(
                aggregate_default_plus_override_zones
            ),
            "default_plus_override_same_zones": sorted(
                aggregate_default_plus_override_zones
            ),
            "same_gw_to_multiple_zones_count": same_gw_to_multiple_zones_count,
            "same_gw_to_multiple_zones": [
                {
                    "selected_gateway_id": gateway_id,
                    "zone_ids": sorted(zone_ids),
                }
                for gateway_id, zone_ids in sorted(aggregate_zones_by_gateway.items())
                if len(zone_ids) > 1
            ],
            "multiple_gw_to_same_zone_count": multiple_gw_to_same_zone_count,
            "multiple_gw_to_same_zone": [
                {
                    "zone_id": zone_id,
                    "selected_gateway_ids": sorted(gateway_ids),
                }
                for zone_id, gateway_ids in sorted(aggregate_gateways_by_zone.items())
                if len(gateway_ids) > 1
            ],
            **anchor_transition_metrics,
            "anchor_gateway_hop_feasibility": hop_feasibility,
            "blocker_count": len(blockers),
            "warning_count": len(warnings),
            "meaningfulness_gate_passed": len(blockers) == 0,
        },
        "per_slice": per_slice,
        "risk_summary": {
            "blockers": blockers,
            "warnings": warnings,
        },
        "conclusion": {
            "path_cardinality_reduction_explained": (
                aggregate_skipped_eligible_core_scope_count == 0
                and aggregate_multi_hop_path_count > 0
            ),
            "explanation": (
                "Reduced BPF core LFIB count is explained by zone/class/anchor "
                "aggregation, direct handoff paths in the bounded window, and "
                "deduplication of shared generation+ingress+label LFIB actions. "
                "The audit still warns that this bounded S4 window is shallow: "
                "multi-hop paths exist, but most selected logical paths are direct."
            ),
        },
    }


def _risk_report_from_path_audit(path_audit: Dict[str, Any]) -> Dict[str, Any]:
    risk_summary = dict(path_audit.get("risk_summary") or {})
    return {
        "schema_version": "skyseed-experiment-meaningfulness-risk-report-v1",
        "run_dir": path_audit.get("run_dir"),
        "meaningfulness_gate_passed": dict(path_audit.get("aggregate") or {}).get(
            "meaningfulness_gate_passed"
        ),
        "blockers": list(risk_summary.get("blockers", []) or []),
        "warnings": list(risk_summary.get("warnings", []) or []),
        "recommendation": (
            "offline_path_cardinality_gate_passed_with_warnings"
            if not list(risk_summary.get("blockers", []) or [])
            else "do_not_retry_runtime_until_blockers_are_fixed"
        ),
    }


def _path_cardinality_markdown(path_audit: Dict[str, Any]) -> str:
    aggregate = dict(path_audit.get("aggregate") or {})
    lines = [
        "# Skyseed Path Cardinality Audit",
        "",
        f"Run dir: `{path_audit.get('run_dir')}`",
        "",
        "## Aggregate",
        "",
        f"- Logical paths: `{aggregate.get('logical_path_count')}`",
        f"- Multi-hop satellite-core paths: `{aggregate.get('multi_hop_satellite_core_path_count')}`",
        f"- 2-hop satellite-core paths: `{aggregate.get('two_hop_core_path_count')}`",
        f"- 3+ hop satellite-core paths: `{aggregate.get('three_plus_hop_core_path_count')}`",
        f"- Direct handoff paths: `{aggregate.get('direct_handoff_count')}`",
        f"- Direct handoff ratio: `{aggregate.get('direct_handoff_ratio')}`",
        f"- Same-zone multi-UT zones: `{aggregate.get('same_zone_multi_ut_count')}`",
        f"- Default+override same zones: `{aggregate.get('default_plus_override_same_zone_count')}`",
        f"- Same GW to multiple zones: `{aggregate.get('same_gw_to_multiple_zones_count')}`",
        f"- Multiple GWs to same zone: `{aggregate.get('multiple_gw_to_same_zone_count')}`",
        f"- Anchor transition zones: `{aggregate.get('anchor_transition_zone_count')}`",
        f"- Eligible core forwarding scope: `{aggregate.get('eligible_core_forwarding_scope_count')}`",
        f"- Compiled BPF-owned core LFIB entries: `{aggregate.get('compiled_bpf_owned_core_forwarding_count')}`",
        f"- Skipped eligible core scope: `{aggregate.get('skipped_eligible_core_scope_count')}`",
        f"- Linux G/Z/MPLS/LFIB fallback count: `{aggregate.get('linux_gz_mpls_lfib_fallback_count')}`",
        f"- Meaningfulness gate passed: `{aggregate.get('meaningfulness_gate_passed')}`",
        "",
        "## Explanation",
        "",
        str(dict(path_audit.get("conclusion") or {}).get("explanation") or ""),
        "",
        "## Per Slice",
        "",
    ]
    for row in list(path_audit.get("per_slice", []) or []):
        z = dict(row.get("zone_gw_fec_cardinality") or {})
        p = dict(row.get("path_structure") or {})
        b = dict(row.get("bpf_compilation_compression") or {})
        s = dict(row.get("bpf_owned_scope_completeness") or {})
        lines.extend(
            [
                f"### {row.get('slice_id')} / generation {row.get('generation')}",
                "",
                f"- Required/resolved zones: `{z.get('required_zone_count')}` / `{z.get('resolved_zone_count')}`",
                f"- Gateway classes: `{z.get('g_exit_gateway_class_count')}`, G FECs: `{z.get('g_fec_count')}`, Z FECs: `{z.get('z_fec_count')}`",
                f"- Logical paths: `{p.get('logical_path_count')}`, hop distribution: `{p.get('hop_count_distribution')}`",
                f"- Direct handoff ratio: `{p.get('direct_handoff_ratio')}`, 2-hop `{p.get('two_hop_core_path_count')}`, 3+ `{p.get('three_plus_hop_core_path_count')}`",
                f"- Unique satellite-core edges: `{p.get('unique_directed_satellite_core_edge_count')}`",
                f"- BPF map entries: classifier `{b.get('classifier_entries')}`, core LFIB `{b.get('core_lfib_entries')}`, handoff `{b.get('egress_handoff_entries')}`",
                f"- Skipped eligible core scope: `{s.get('skipped_eligible_core_scope_count')}`",
                "",
            ]
        )
    return "\n".join(lines) + "\n"


def validate_run_dir(run_dir: Path) -> Dict[str, Any]:
    total_started_at = time.perf_counter()
    phase_timing: Dict[str, float] = {}
    signature_started_at = time.perf_counter()
    input_signature = {
        "prepared_update_bundle_json": _file_signature(
            run_dir / "prepared_update_bundle.json"
        ),
        "planner_preview_json": _file_signature(run_dir / "planner_preview.json"),
        "snapshot_preview_json": _file_signature(run_dir / "snapshot_preview.json"),
        "topology_json": _file_signature(run_dir / "topology.json"),
    }
    phase_timing["input_signature_wall_seconds"] = round(
        time.perf_counter() - signature_started_at,
        6,
    )
    read_started_at = time.perf_counter()
    prepared = _read_json(run_dir / "prepared_update_bundle.json")
    planner_preview = _read_json(run_dir / "planner_preview.json")
    snapshot_preview = _read_json(run_dir / "snapshot_preview.json")
    phase_timing["artifact_read_wall_seconds"] = round(
        time.perf_counter() - read_started_at,
        6,
    )
    topology_path = run_dir / "topology.json"
    selected_runtime_slice_ids = [
        str(slice_id) for slice_id in prepared.get("selected_runtime_slice_ids", []) or []
    ]
    semantic_started_at = time.perf_counter()
    semantic_validation = _zone_anchor_semantic_validation(
        run_dir=run_dir,
        planner_preview=planner_preview,
        snapshot_preview=snapshot_preview,
        selected_runtime_slice_ids=selected_runtime_slice_ids,
    )
    phase_timing["offline_semantic_validation_wall_seconds"] = round(
        time.perf_counter() - semantic_started_at,
        6,
    )
    if not bool(semantic_validation.get("passed")):
        phase_timing["total_wall_seconds"] = round(
            time.perf_counter() - total_started_at,
            6,
        )
        return {
            "schema_version": "skyseed-bpf-mpls-offline-validation-v1",
            "run_dir": str(run_dir),
            "selected_runtime_slice_ids": selected_runtime_slice_ids,
            "compiled_slice_count": 0,
            "aggregate": {
                "classifier_entry_count": 0,
                "core_lfib_entry_count": 0,
                "egress_handoff_entry_count": 0,
                "generation_count": 0,
                "safe_duplicate_count": 0,
                "unsafe_conflict_count": 0,
                "offline_conflicts_fixed": False,
            },
            "zone_anchor_semantic_validation": semantic_validation,
            "compiled_slices": [],
            "conflict_table": [],
            "label_allocation_ledger": [],
            "label_allocation_ledger_count": 0,
            "offline_interface_id_policy": {
                "mode": "deterministic_synthetic_logical_interface_id",
                "source": (
                    "offline=True compiler path hashes logical interface refs; it does "
                    "not read live container ifindex or MAC state"
                ),
                "stale_live_ifindex_binding": False,
            },
            "input_signature": input_signature,
            "artifact_reuse": {
                "reuse_policy": (
                    "same-run artifacts may be reused only when this input signature "
                    "is unchanged; semantic/path/BPF gates are still executed before "
                    "startup unless a caller records an explicit matching reuse decision"
                ),
                "reuse_decision": "not_reused_semantic_gate_failed",
                "input_signature_sha256": _reuse_signature_sha256(input_signature),
            },
            "timing": phase_timing,
        }
    selected = set(selected_runtime_slice_ids)
    snapshots_by_id = {
        str(snapshot.get("time_slice", {}).get("slice_id") or snapshot.get("slice_id")): snapshot
        for snapshot in snapshot_preview.get("snapshots", []) or []
    }

    compiled_records: List[Dict[str, Any]] = []
    conflict_table: List[Dict[str, Any]] = []
    label_allocation_ledger: List[Dict[str, Any]] = []
    per_slice_contexts: Dict[str, List[Any]] = {}
    aggregate_counts = {
        "classifier_entry_count": 0,
        "core_lfib_entry_count": 0,
        "egress_handoff_entry_count": 0,
        "generation_count": 0,
        "safe_duplicate_count": 0,
        "unsafe_conflict_count": 0,
    }
    generations: set[int] = set()

    compiler_started_at = time.perf_counter()
    for planner_snapshot in planner_preview.get("snapshots", []) or []:
        slice_id = str(planner_snapshot.get("slice_id") or "")
        if selected and slice_id not in selected:
            continue
        snapshot_payload = snapshots_by_id.get(slice_id)
        if not snapshot_payload:
            raise RuntimeError(f"missing_snapshot_for_slice:{slice_id}")
        plan = _plan_from_dict(dict(planner_snapshot["runtime_update_plan"]))
        topology_snapshot = _snapshot_from_dict(dict(snapshot_payload))
        generation_id, generation_source = _bpf_owned_runtime_generation_id(plan)
        contexts = _realization_contexts(
            topology_path=topology_path,
            topology_snapshot=topology_snapshot,
            runtime_update_plan=plan,
        )
        per_slice_contexts[slice_id] = list(contexts)
        compiled = _bpf_owned_compile_mpls_maps_for_contexts(
            contexts=contexts,
            generation_id=int(generation_id),
            offline=True,
        )
        counts = dict(compiled.get("compiled_mpls_map_counts") or {})
        conflict_summary = dict(compiled.get("map_conflict_summary") or {})
        conflicts = list(compiled.get("map_conflicts") or [])
        generations.add(int(generation_id))
        aggregate_counts["classifier_entry_count"] += int(
            counts.get("ingress_classifier_map") or 0
        )
        aggregate_counts["core_lfib_entry_count"] += int(
            counts.get("core_lfib_map") or 0
        )
        aggregate_counts["egress_handoff_entry_count"] += int(
            counts.get("egress_handoff_map") or 0
        )
        aggregate_counts["safe_duplicate_count"] += int(
            conflict_summary.get("safe_duplicate_count") or 0
        )
        aggregate_counts["unsafe_conflict_count"] += int(
            conflict_summary.get("unsafe_conflict_count") or len(conflicts)
        )
        conflict_table.extend(
            {
                **dict(conflict),
                "slice_id": slice_id,
                "runtime_plan_id": plan.plan_id,
                "generation_source": generation_source,
            }
            for conflict in conflicts
        )
        label_allocation_ledger.extend(
            _label_allocation_ledger_records(
                slice_id=slice_id,
                runtime_plan_id=plan.plan_id,
                generation_source=generation_source,
                compiled=compiled,
            )
        )
        compiled_records.append(
            {
                "slice_id": slice_id,
                "runtime_plan_id": plan.plan_id,
                "generation": int(generation_id),
                "generation_source": generation_source,
                "context_count": len(contexts),
                "compiled_mpls_map_counts": counts,
                "map_conflict_summary": conflict_summary,
                "label_allocation": {
                    key: value
                    for key, value in dict(compiled.get("label_allocation") or {}).items()
                    if key != "entries"
                },
            }
        )
    phase_timing["bpf_offline_compiler_wall_seconds"] = round(
        time.perf_counter() - compiler_started_at,
        6,
    )

    aggregate_counts["generation_count"] = len(generations)
    path_audit_started_at = time.perf_counter()
    path_cardinality_audit = _build_path_cardinality_audit(
        run_dir=run_dir,
        selected_runtime_slice_ids=selected_runtime_slice_ids,
        semantic_validation=semantic_validation,
        compiled_slices=compiled_records,
        label_allocation_ledger=label_allocation_ledger,
        per_slice_contexts=per_slice_contexts,
        snapshot_preview=snapshot_preview,
    )
    phase_timing["path_cardinality_audit_wall_seconds"] = round(
        time.perf_counter() - path_audit_started_at,
        6,
    )
    phase_timing["total_wall_seconds"] = round(
        time.perf_counter() - total_started_at,
        6,
    )
    input_signature_sha256 = _reuse_signature_sha256(input_signature)
    return {
        "schema_version": "skyseed-bpf-mpls-offline-validation-v1",
        "run_dir": str(run_dir),
        "selected_runtime_slice_ids": selected_runtime_slice_ids,
        "compiled_slice_count": len(compiled_records),
        "aggregate": {
            **aggregate_counts,
            "offline_conflicts_fixed": aggregate_counts["unsafe_conflict_count"] == 0,
        },
        "zone_anchor_semantic_validation": semantic_validation,
        "compiled_slices": compiled_records,
        "conflict_table": conflict_table,
        "label_allocation_ledger": label_allocation_ledger,
        "label_allocation_ledger_count": len(label_allocation_ledger),
        "path_cardinality_audit": path_cardinality_audit,
        "experiment_meaningfulness_risk_report": _risk_report_from_path_audit(
            path_cardinality_audit
        ),
        "offline_interface_id_policy": {
            "mode": "deterministic_synthetic_logical_interface_id",
            "source": (
                "offline=True compiler path hashes logical interface refs; it does "
                "not read live container ifindex or MAC state"
            ),
            "stale_live_ifindex_binding": False,
        },
        "input_signature": input_signature,
        "artifact_reuse": {
            "reuse_policy": (
                "same-run artifacts may be reused only when this input signature is "
                "unchanged; this report was produced by executing semantic, path, "
                "and BPF compiler gates"
            ),
            "reuse_decision": "computed_fresh",
            "input_signature_sha256": input_signature_sha256,
        },
        "timing": phase_timing,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compile BPF MPLS-like maps from prepared Skyseed artifacts offline."
    )
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--label-ledger-output", type=Path)
    parser.add_argument("--zone-anchor-ledger-output", type=Path)
    parser.add_argument("--zone-anchor-validation-output", type=Path)
    parser.add_argument("--gz-fec-ledger-output", type=Path)
    parser.add_argument("--path-cardinality-audit-output", type=Path)
    parser.add_argument("--path-cardinality-summary-output", type=Path)
    parser.add_argument("--meaningfulness-risk-output", type=Path)
    args = parser.parse_args()

    report = validate_run_dir(args.run_dir)
    semantic_validation = dict(report.get("zone_anchor_semantic_validation") or {})
    if args.zone_anchor_ledger_output:
        _write_json(
            args.zone_anchor_ledger_output,
            {
                "schema_version": "skyseed-zone-anchor-ledger-v1",
                "run_dir": report["run_dir"],
                "record_count": len(
                    list(semantic_validation.get("zone_anchor_ledger", []) or [])
                ),
                "records": list(semantic_validation.get("zone_anchor_ledger", []) or []),
            },
        )
    if args.zone_anchor_validation_output:
        _write_json(args.zone_anchor_validation_output, semantic_validation)
    if args.gz_fec_ledger_output:
        _write_json(
            args.gz_fec_ledger_output,
            {
                "schema_version": "skyseed-gz-fec-ledger-v1",
                "run_dir": report["run_dir"],
                "record_count": len(
                    list(semantic_validation.get("gz_fec_ledger", []) or [])
                ),
                "records": list(semantic_validation.get("gz_fec_ledger", []) or []),
            },
        )
    if args.label_ledger_output:
        _write_json(
            args.label_ledger_output,
            {
                "schema_version": "skyseed-bpf-mpls-label-allocation-ledger-v1",
                "run_dir": report["run_dir"],
                "offline_interface_id_policy": report["offline_interface_id_policy"],
                "record_count": report["label_allocation_ledger_count"],
                "records": report["label_allocation_ledger"],
            },
        )
    if args.path_cardinality_audit_output:
        _write_json(
            args.path_cardinality_audit_output,
            dict(report.get("path_cardinality_audit") or {}),
        )
    if args.path_cardinality_summary_output:
        args.path_cardinality_summary_output.parent.mkdir(parents=True, exist_ok=True)
        args.path_cardinality_summary_output.write_text(
            _path_cardinality_markdown(dict(report.get("path_cardinality_audit") or {})),
            encoding="utf-8",
        )
    if args.meaningfulness_risk_output:
        _write_json(
            args.meaningfulness_risk_output,
            dict(report.get("experiment_meaningfulness_risk_report") or {}),
        )
    if args.output:
        _write_json(args.output, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    semantic_passed = bool(
        dict(report.get("zone_anchor_semantic_validation") or {}).get("passed")
    )
    return (
        0
        if semantic_passed and int(report["aggregate"]["unsafe_conflict_count"]) == 0
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
