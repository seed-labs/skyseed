#!/usr/bin/env python3
# encoding: utf-8

"""Out-of-band horizon maintenance worker for operational-clean BPF runs."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence


def _bootstrap_worker_import_path() -> None:
    project_root = Path(__file__).resolve().parents[3]
    repo_root = project_root.parent
    satellite_dir = repo_root / "satellite"
    source_root = project_root / "src"
    for path in (repo_root, satellite_dir, source_root):
        rendered = str(path)
        if rendered in sys.path:
            sys.path.remove(rendered)
        sys.path.insert(0, rendered)


_bootstrap_worker_import_path()

from skyseed_v1.runtime.horizons import (
    HorizonMaintenanceRequest,
    classify_horizon_maintenance,
    publish_horizon_maintenance_result,
    retired_cleanup_dependency_report,
)
from skyseed_v1.backends.linux.mpls import (
    MinimalRealLinuxMplsInstaller,
    _bpf_owned_freeze_generation_compiled_runtime_inputs,
    _bpf_owned_frozen_compiled_runtime_inputs_check,
    _bpf_owned_interface_binding_refs_for_contexts,
    _prepare_bpf_owned_preactivation_attach_capability_from_frozen_inputs,
    _prepare_bpf_owned_preactivation_preload_from_frozen_inputs,
    _realization_contexts,
)
from skyseed_v1.backends.linux.network import MinimalRealNetworkMutationExecutor
from skyseed_v1.backends.bpf.canary import consume_frozen_container_pid_cache
from skyseed_v1.core.ir.contracts import RuntimeUpdatePlan, TopologySnapshot


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists() or path.stat().st_size == 0:
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return dict(value) if isinstance(value, Mapping) else {}


def _read_jsonl(path: Path) -> list[Dict[str, Any]]:
    records: list[Dict[str, Any]] = []
    if not path.exists() or path.stat().st_size == 0:
        return records
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            value = json.loads(stripped)
            if isinstance(value, Mapping):
                records.append(dict(value))
    return records


def _required_non_directory_path(value: Any, *, field_name: str) -> Path:
    rendered = str(value or "").strip()
    if not rendered:
        raise RuntimeError(f"{field_name}_missing")
    path = Path(rendered)
    if path == Path("."):
        raise RuntimeError(f"{field_name}_missing")
    if not path.exists():
        raise RuntimeError(f"{field_name}_not_found:{path}")
    if path.exists() and path.is_dir():
        raise RuntimeError(f"{field_name}_is_directory:{path}")
    return path


def _load_retired_cleanup_work_items(payload: Mapping[str, Any]) -> list[Dict[str, Any]]:
    artifact_value = str(
        dict(payload or {}).get("retired_cleanup_work_items_artifact") or ""
    )
    if artifact_value:
        return _read_jsonl(Path(artifact_value))
    return [
        dict(item)
        for item in list(dict(payload or {}).get("retired_cleanup_work_items") or [])
        if isinstance(item, Mapping)
    ]


def _merge_created_interface_bindings(
    *,
    interface_ifindex_cache: Dict[str, int],
    interface_mac_cache: Dict[str, str],
    bindings: Sequence[Mapping[str, Any]],
) -> None:
    for binding_any in bindings:
        binding = dict(binding_any or {})
        interface_ref = str(binding.get("interface_ref") or "")
        ifindex = binding.get("ifindex")
        mac_address = str(binding.get("mac_address") or "")
        binding_source = str(binding.get("binding_source") or "")
        if (
            not interface_ref
            or ifindex in (None, "", "None")
            or not mac_address
            or binding_source != "created_veth_operation_result"
        ):
            raise RuntimeError(
                "horizon_maintenance_created_interface_binding_incomplete:"
                + json.dumps(binding, sort_keys=True)
            )
        if (
            interface_ref in interface_ifindex_cache
            and int(interface_ifindex_cache[interface_ref]) != int(ifindex)
        ):
            raise RuntimeError(
                f"horizon_maintenance_created_ifindex_conflict:{interface_ref}"
            )
        if (
            interface_ref in interface_mac_cache
            and str(interface_mac_cache[interface_ref]) != mac_address
        ):
            raise RuntimeError(
                f"horizon_maintenance_created_mac_conflict:{interface_ref}"
            )
        interface_ifindex_cache[interface_ref] = int(ifindex)
        interface_mac_cache[interface_ref] = mac_address


def _compact_retired_cleanup_record(record: Mapping[str, Any]) -> Dict[str, Any]:
    source = dict(record or {})
    compact = {
        key: value
        for key, value in source.items()
        if key not in {"delete_manifest", "cleanup_load"}
    }
    if "delete_manifest" in source:
        compact["delete_manifest_summary"] = {
            "kind": "list",
            "present": True,
            "count": len(list(source.get("delete_manifest") or [])),
            "payload_elided": True,
        }
    if "cleanup_load" in source:
        load = dict(source.get("cleanup_load") or {})
        compact["cleanup_load_summary"] = {
            "ok": bool(load.get("ok")),
            "manifest_only_loader": bool(load.get("manifest_only_loader")),
            "batch_map_write_used": bool(load.get("batch_map_write_used")),
            "actual_data_delete_count": int(
                load.get("actual_data_delete_count") or 0
            ),
            "map_write_wall_seconds": float(load.get("map_write_wall_seconds") or 0.0),
            "outcome_labels": list(load.get("outcome_labels") or []),
            "payload_elided": True,
        }
    return compact


def _execute_retired_cleanup_work_items(
    work_items: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    from skyseed_v1.backends.bpf.live import load_mpls_maps

    completed_records: list[Dict[str, Any]] = []
    blocked_records: list[Dict[str, Any]] = []
    started = time.perf_counter()
    for item_any in list(work_items or []):
        work_item = dict(item_any or {})
        generation_id = int(work_item.get("generation_id") or 0)
        cleanup_started = time.perf_counter()
        cleanup_load = load_mpls_maps(
            {
                "active_generation": {"value": generation_id},
                "bpf_runtime_update_phase": "retired_generation_cleanup",
                "defer_active_generation_flip": True,
                "bpf_map_write_manifest_only": True,
                "batch_map_write_required": True,
                "skip_interface_role_map_updates": True,
                "bpf_map_delete_manifest": list(
                    work_item.get("delete_manifest") or []
                ),
            }
        )
        record = {
            **work_item,
            "schema_version": "skyseed-retired-bpf-generation-cleanup-completed-v1",
            "status": "completed" if cleanup_load.get("ok") is True else "failed",
            "ok": bool(cleanup_load.get("ok")),
            "reason": str(
                cleanup_load.get("reason")
                or dict(cleanup_load.get("first_failed_update") or {}).get("reason")
                or ""
            ),
            "cleanup_load": cleanup_load,
            "wall_seconds": round(time.perf_counter() - cleanup_started, 6),
            "process_owned": True,
        }
        if cleanup_load.get("ok") is True:
            completed_records.append(record)
        else:
            blocked_records.append(
                {
                    **record,
                    "schema_version": (
                        "skyseed-retired-bpf-generation-cleanup-blocked-v1"
                    ),
                    "status": "blocked",
                    "blocked_reason": str(
                        record.get("reason") or "retired_cleanup_loader_failed"
                    ),
                }
            )
    return {
        "schema_version": "skyseed-retired-cleanup-process-execution-v1",
        "process_owned": True,
        "requested_count": len(list(work_items or [])),
        "completed_count": len(completed_records),
        "blocked_count": len(blocked_records),
        "completed_records": completed_records,
        "blocked_records": blocked_records,
        "wall_seconds": round(time.perf_counter() - started, 6),
    }


def _request_from_payload(payload: Mapping[str, Any]) -> HorizonMaintenanceRequest:
    source = dict(payload or {})
    return HorizonMaintenanceRequest(
        segment_index=int(source.get("segment_index") or 0),
        segment_id=str(source.get("segment_id") or ""),
        current_horizon_id=str(source.get("current_horizon_id") or ""),
        next_horizon_id=str(source.get("next_horizon_id") or ""),
        previous_horizon_id=str(source.get("previous_horizon_id") or ""),
        source_state_id=str(source.get("source_state_id") or ""),
        base_generation_id=int(source.get("base_generation_id") or 0),
        prepared_generation_ids=tuple(
            int(item) for item in list(source.get("prepared_generation_ids") or [])
        ),
        expected_preload_count=int(source.get("expected_preload_count") or 0),
        worker_mode=str(source.get("worker_mode") or "process"),
        forbidden_work_policy=dict(source.get("forbidden_work_policy") or {}),
    )


def _empty_host_prepare_report(payload: Mapping[str, Any]) -> Dict[str, Any]:
    existing_scope = {
        str(slot_name): sorted(
            {
                str(resource_key)
                for resource_key in list(resource_keys or [])
                if str(resource_key)
            }
        )
        for slot_name, resource_keys in dict(
            payload.get("existing_prepared_resource_scope_by_slot") or {}
        ).items()
        if str(slot_name)
    }
    return {
        "schema_version": "skyseed-additive-resource-prepare-v1",
        "ok": True,
        "additive_prepare_completed": True,
        "scope": "bounded_additive_delta_prepare",
        "slot_count": 0,
        "candidate_resource_count": 0,
        "candidate_endpoint_count": 0,
        "bridge_created_count": 0,
        "bridge_reused_count": 0,
        "resource_created_count": 0,
        "resource_reused_count": 0,
        "resource_skipped_count": 0,
        "preconnected_endpoint_created_count": 0,
        "preconnected_endpoint_reused_count": 0,
        "prepared_resource_scope_by_slot": {},
        "prepared_owned_resource_scope_by_slot": existing_scope,
        "full_sweep": False,
        "full_host_scan_skipped": True,
        "reason": "rolling_horizon_additive_prepare_delta_empty",
        "segment_id": str(payload.get("segment_id") or ""),
        "horizon_id": str(payload.get("horizon_id") or ""),
        "maintenance_stage": "runtime/horizon_replenishment/additive_host_prepare",
        "future_host_link_state_apply_allowed": False,
        "preapply_qdisc_parameter_updates_allowed": False,
        "active_generation_flip_allowed": False,
        "runtime_recompute_network_mutation_plan_allowed": False,
        "full_prepare_allowed": False,
    }


def _merge_prepared_resource_scopes(
    *scopes: Mapping[str, Sequence[str]],
) -> Dict[str, list[str]]:
    merged: Dict[str, set[str]] = {}
    for scope in scopes:
        for slot_name, resource_keys in dict(scope or {}).items():
            rendered_slot = str(slot_name)
            if not rendered_slot:
                continue
            for resource_key in list(resource_keys or []):
                rendered_key = str(resource_key)
                if rendered_key:
                    merged.setdefault(rendered_slot, set()).add(rendered_key)
    return {
        slot_name: sorted(resource_keys)
        for slot_name, resource_keys in sorted(merged.items())
        if resource_keys
    }


def _topology_snapshot_from_payload(payload: Mapping[str, Any]) -> TopologySnapshot:
    source = dict(payload or {})
    return TopologySnapshot(
        schema_version=str(source.get("schema_version") or ""),
        snapshot_id=str(source.get("snapshot_id") or ""),
        time_slice=dict(source.get("time_slice") or {}),
        provenance=dict(source.get("provenance") or {}),
        nodes=dict(source.get("nodes") or {}),
        active_links=list(source.get("active_links") or []),
        zones=dict(source.get("zones") or {}),
        ut_zone_membership=dict(source.get("ut_zone_membership") or {}),
        planner_hints=dict(source.get("planner_hints") or {}),
    )


def _runtime_update_plan_from_payload(payload: Mapping[str, Any]) -> RuntimeUpdatePlan:
    source = dict(payload or {})
    return RuntimeUpdatePlan(
        schema_version=str(source.get("schema_version") or ""),
        plan_id=str(source.get("plan_id") or ""),
        target_snapshot=dict(source.get("target_snapshot") or {}),
        planner=dict(source.get("planner") or {}),
        apply_model=dict(source.get("apply_model") or {}),
        node_subplans=dict(source.get("node_subplans") or {}),
        replacement_semantics=dict(source.get("replacement_semantics") or {}),
        validation_expectations=dict(source.get("validation_expectations") or {}),
    )


def _required_interface_refs_for_preload_work_items(
    *,
    items: Sequence[Mapping[str, Any]],
    topology_path: Path,
) -> tuple[set[str], set[str], tuple[Any, ...]]:
    required_ifindex_refs: set[str] = set()
    required_mac_refs: set[str] = set()
    route_contexts: list[Any] = []
    for item_any in items:
        item = dict(item_any or {})
        if dict(item.get("frozen_compiled_runtime_inputs") or {}):
            raise RuntimeError("process_worker_target_generation_precompile_forbidden")
        context_payload = dict(item.get("context") or {})
        if not dict(context_payload.get("snapshot") or {}):
            raise RuntimeError("process_worker_context_snapshot_missing")
        if not dict(context_payload.get("runtime_update_plan") or {}):
            raise RuntimeError("process_worker_context_runtime_update_plan_missing")
        snapshot = _topology_snapshot_from_payload(
            dict(context_payload.get("snapshot") or {})
        )
        runtime_update_plan = _runtime_update_plan_from_payload(
            dict(context_payload.get("runtime_update_plan") or {})
        )
        contexts = _realization_contexts(
            topology_path=topology_path,
            topology_snapshot=snapshot,
            runtime_update_plan=runtime_update_plan,
        )
        route_contexts.extend(contexts)
        required_refs = set(
            _bpf_owned_interface_binding_refs_for_contexts(tuple(contexts))
        )
        required_ifindex_refs.update(required_refs)
        required_mac_refs.update(required_refs)
    return required_ifindex_refs, required_mac_refs, tuple(route_contexts)


def _prepare_bpf_owned_edge_route_baseline(
    *,
    contexts: Sequence[Any],
    max_node_workers: int,
    prepared_route_identity_evidence: Mapping[str, Any],
) -> Dict[str, Any]:
    return MinimalRealLinuxMplsInstaller().prepare_bpf_owned_edge_route_baseline(
        contexts,
        max_node_workers=max_node_workers,
        prepared_route_identity_evidence=prepared_route_identity_evidence,
    )


def _merge_final_interface_bindings(
    *,
    interface_ifindex_cache: Dict[str, int],
    interface_mac_cache: Dict[str, str],
    bindings: Sequence[Mapping[str, Any]],
) -> Dict[str, int]:
    superseded_ifindex_count = 0
    superseded_mac_count = 0
    for binding_any in bindings:
        binding = dict(binding_any or {})
        interface_ref = str(binding.get("interface_ref") or "")
        ifindex = int(binding.get("ifindex") or 0)
        mac_address = str(binding.get("mac_address") or "")
        if (
            not interface_ref
            or ifindex <= 0
            or not mac_address
            or binding.get("binding_source")
            != "targeted_existing_interface_prepare_operation_result"
        ):
            raise RuntimeError(
                "horizon_maintenance_final_interface_binding_incomplete:"
                + json.dumps(binding, sort_keys=True)
            )
        if interface_ref in interface_ifindex_cache and int(
            interface_ifindex_cache[interface_ref]
        ) != ifindex:
            superseded_ifindex_count += 1
        if interface_ref in interface_mac_cache and str(
            interface_mac_cache[interface_ref]
        ) != mac_address:
            superseded_mac_count += 1
        interface_ifindex_cache[interface_ref] = ifindex
        interface_mac_cache[interface_ref] = mac_address
    return {
        "superseded_ifindex_count": superseded_ifindex_count,
        "superseded_mac_count": superseded_mac_count,
    }


def _validate_final_interface_binding_scope(
    *,
    required_interface_refs: Sequence[str],
    bindings: Sequence[Mapping[str, Any]],
) -> None:
    required_refs = {str(interface_ref) for interface_ref in required_interface_refs}
    captured_refs = {
        str(dict(binding or {}).get("interface_ref") or "")
        for binding in bindings
        if isinstance(binding, Mapping)
    }
    if captured_refs != required_refs:
        raise RuntimeError(
            "horizon_maintenance_final_binding_prepare_scope_mismatch:"
            + json.dumps(
                {
                    "missing": sorted(required_refs - captured_refs),
                    "unexpected": sorted(captured_refs - required_refs),
                },
                sort_keys=True,
            )
        )


def _load_preload_work_items(source: Mapping[str, Any]) -> list[Dict[str, Any]]:
    inline_items = [
        dict(item)
        for item in list(dict(source).get("preload_work_items") or [])
        if isinstance(item, Mapping)
    ]
    if inline_items:
        return inline_items
    artifact_value = str(dict(source).get("preload_work_items_artifact") or "")
    if artifact_value:
        return _read_jsonl(Path(artifact_value))
    return []


def _hydrate_preload_work_item_contexts(
    source: Mapping[str, Any],
    items: Sequence[Mapping[str, Any]],
) -> list[Dict[str, Any]]:
    artifact = dict(dict(source).get("preload_context_artifact") or {})
    if artifact.get("schema_version") != (
        "skyseed-horizon-maintenance-context-artifact-v1"
    ):
        raise RuntimeError("horizon_maintenance_context_artifact_invalid")
    path = _required_non_directory_path(
        artifact.get("path"),
        field_name="horizon_maintenance_context_artifact_path",
    )
    expected_hash = str(artifact.get("sha256") or "")
    expected_count = int(artifact.get("record_count") or 0)
    digest = hashlib.sha256()
    context_by_key: Dict[str, Dict[str, Any]] = {}
    with path.open("rb") as handle:
        for raw_line in handle:
            digest.update(raw_line)
            stripped = raw_line.strip()
            if not stripped:
                continue
            value = json.loads(stripped.decode("utf-8"))
            record = dict(value or {}) if isinstance(value, Mapping) else {}
            segment_epoch_key = str(record.get("segment_epoch_key") or "")
            context = dict(record.get("context") or {})
            if not segment_epoch_key or not context:
                raise RuntimeError("horizon_maintenance_context_record_invalid")
            if segment_epoch_key in context_by_key:
                raise RuntimeError(
                    "horizon_maintenance_context_record_duplicate:"
                    + segment_epoch_key
                )
            context_by_key[segment_epoch_key] = context
    if expected_hash and digest.hexdigest() != expected_hash:
        raise RuntimeError("horizon_maintenance_context_artifact_hash_mismatch")
    if expected_count and len(context_by_key) != expected_count:
        raise RuntimeError("horizon_maintenance_context_artifact_count_mismatch")
    hydrated: list[Dict[str, Any]] = []
    requested_keys: set[str] = set()
    for item_any in items:
        item = dict(item_any or {})
        context_ref = dict(item.get("context_ref") or {})
        segment_epoch_key = str(context_ref.get("segment_epoch_key") or "")
        if (
            context_ref.get("schema_version")
            != "skyseed-horizon-maintenance-context-ref-v1"
            or not segment_epoch_key
        ):
            raise RuntimeError("process_worker_preload_context_ref_missing")
        context = dict(context_by_key.get(segment_epoch_key) or {})
        if not context:
            raise RuntimeError(
                "process_worker_preload_context_ref_unresolved:"
                + segment_epoch_key
            )
        requested_keys.add(segment_epoch_key)
        hydrated.append({**item, "context": context})
    if len(requested_keys) != len(hydrated):
        raise RuntimeError("process_worker_preload_context_ref_duplicate")
    return hydrated


def _freeze_preload_work_item(
    *,
    item: Mapping[str, Any],
    base_generation_compiled_inputs: Mapping[str, Any] | None = None,
    resident_attach_budget: Mapping[str, Any] | None = None,
    topology_path: Path,
    interface_ifindex_cache: Dict[str, int],
    interface_mac_cache: Dict[str, str],
) -> Dict[str, Any]:
    frozen_inputs = dict(item.get("frozen_compiled_runtime_inputs") or {})
    if frozen_inputs:
        raise RuntimeError("process_worker_target_generation_precompile_forbidden")
    context_payload = dict(item.get("context") or {})
    if not dict(context_payload.get("snapshot") or {}):
        raise RuntimeError("process_worker_context_snapshot_missing")
    if not dict(context_payload.get("runtime_update_plan") or {}):
        raise RuntimeError("process_worker_context_runtime_update_plan_missing")
    snapshot = _topology_snapshot_from_payload(dict(context_payload.get("snapshot") or {}))
    runtime_update_plan = _runtime_update_plan_from_payload(
        dict(context_payload.get("runtime_update_plan") or {})
    )
    generation_id = int(
        item.get("physical_generation_id")
        if item.get("physical_generation_id") not in (None, "", "None")
        else item.get("generation_id") or 0
    )
    contexts = tuple(
        _realization_contexts(
            topology_path=topology_path,
            topology_snapshot=snapshot,
            runtime_update_plan=runtime_update_plan,
        )
    )
    frozen_inputs = _bpf_owned_freeze_generation_compiled_runtime_inputs(
        contexts=contexts,
        generation_id=generation_id,
        base_generation_compiled_inputs=(
            item.get("base_generation_compiled_inputs")
            or base_generation_compiled_inputs
            or {}
        ),
        base_generation_id=int(item.get("base_generation_id") or 0),
        interface_ifindex_cache=interface_ifindex_cache,
        interface_mac_cache=interface_mac_cache,
    )
    budget = dict(
        frozen_inputs.get("resident_attach_coverage_budget")
        or frozen_inputs.get("bpf_attach_budget")
        or resident_attach_budget
        or {}
    )
    if budget:
        frozen_inputs["resident_attach_coverage_budget"] = dict(budget)
        frozen_inputs["bpf_attach_budget"] = dict(budget)
    return {
        **dict(item),
        "frozen_compiled_runtime_inputs": frozen_inputs,
        "frozen_compiled_runtime_inputs_check": (
            _bpf_owned_frozen_compiled_runtime_inputs_check(frozen_inputs)
        ),
    }


def _union_frozen_inputs(records: list[Mapping[str, Any]]) -> Dict[str, Any]:
    seed = dict(dict(records[0]).get("frozen_compiled_runtime_inputs") or {}) if records else {}
    if not seed:
        return {}
    selected_budget: Dict[str, Any] = {}
    merged_plan: dict[str, set[str]] = {}
    merged_details: dict[str, dict[str, Any]] = {}
    merged_interfaces: set[str] = set()
    merged_maps: dict[str, dict[str, dict[str, Any]]] = {
        "ingress_classifier_map": {},
        "ut_gateway_policy_map": {},
        "gateway_selector_ingress_map": {},
        "core_lfib_map": {},
        "egress_handoff_map": {},
        "zone_edge_delivery_map": {},
        "generation_base_map": {},
    }
    for record in records:
        inputs = dict(dict(record).get("frozen_compiled_runtime_inputs") or {})
        budget = dict(
            inputs.get("resident_attach_coverage_budget")
            or inputs.get("bpf_attach_budget")
            or {}
        )
        if budget and int(budget.get("max_attach_interfaces") or 0) >= int(
            selected_budget.get("max_attach_interfaces") or 0
        ):
            selected_budget = budget
        for interface_ref, directions in dict(inputs.get("attach_plan") or {}).items():
            key = str(interface_ref)
            if key:
                merged_plan.setdefault(key, set()).update(
                    str(direction) for direction in list(directions or []) if str(direction)
                )
        merged_interfaces.update(
            str(interface_ref)
            for interface_ref in list(inputs.get("attached_interfaces") or [])
            if str(interface_ref)
        )
        for detail_any in list(inputs.get("attached_interface_details") or []):
            detail = dict(detail_any or {})
            interface_ref = str(detail.get("interface_ref") or detail.get("interface_name") or "")
            if not interface_ref:
                continue
            previous = dict(merged_details.get(interface_ref) or {})
            roles = sorted(
                {
                    *[str(role) for role in list(previous.get("expected_roles") or []) if str(role)],
                    *[str(role) for role in list(detail.get("expected_roles") or []) if str(role)],
                }
            )
            directions = sorted(
                {
                    *[str(direction) for direction in list(previous.get("attach_directions") or []) if str(direction)],
                    *[str(direction) for direction in list(detail.get("attach_directions") or []) if str(direction)],
                    *list(merged_plan.get(interface_ref) or []),
                }
            )
            merged = {**previous, **detail, "interface_ref": interface_ref}
            if roles:
                merged["expected_roles"] = roles
            if directions:
                merged["attach_directions"] = directions
            merged_details[interface_ref] = merged
        for map_name in merged_maps:
            for entry_any in list(inputs.get(map_name) or []):
                entry = dict(entry_any or {})
                key = json.dumps(entry, sort_keys=True, separators=(",", ":"))
                merged_maps[map_name][key] = entry
    attach_plan = {key: sorted(value) for key, value in sorted(merged_plan.items())}
    merged_interfaces.update(attach_plan)
    merged_interfaces.update(merged_details)
    merged = {
        **seed,
        "attach_plan": attach_plan,
        "attached_interfaces": sorted(merged_interfaces),
        "attached_interface_details": [
            merged_details[interface_ref] for interface_ref in sorted(merged_details)
        ],
        "ingress_classifier_map": list(merged_maps["ingress_classifier_map"].values()),
        "ut_gateway_policy_map": list(merged_maps["ut_gateway_policy_map"].values()),
        "gateway_selector_ingress_map": list(
            merged_maps["gateway_selector_ingress_map"].values()
        ),
        "core_lfib_map": list(merged_maps["core_lfib_map"].values()),
        "egress_handoff_map": list(merged_maps["egress_handoff_map"].values()),
        "zone_edge_delivery_map": list(
            merged_maps["zone_edge_delivery_map"].values()
        ),
        "generation_base_map": list(merged_maps["generation_base_map"].values()),
        "compiled_runtime_inputs_source": "process_horizon_maintenance_frozen_manifest_union",
    }
    if selected_budget:
        merged["resident_attach_coverage_budget"] = dict(selected_budget)
        merged["bpf_attach_budget"] = dict(selected_budget)
    return merged


def _base_generation_interface_binding_caches(
    base_generation_compiled_inputs: Mapping[str, Any],
) -> tuple[Dict[str, int], Dict[str, str]]:
    binding = dict(
        dict(base_generation_compiled_inputs or {}).get(
            "frozen_interface_binding_cache"
        )
        or {}
    )
    if (
        binding.get("complete") is not True
        or binding.get("live_discovery_allowed") is not False
    ):
        raise RuntimeError(
            "horizon_maintenance_base_generation_binding_cache_incomplete"
        )
    ifindex_by_ref = {
        str(interface_ref): int(ifindex)
        for interface_ref, ifindex in dict(
            binding.get("ifindex_by_interface_ref") or {}
        ).items()
        if str(interface_ref) and ifindex not in (None, "", "None")
    }
    mac_by_ref = {
        str(interface_ref): str(mac_address)
        for interface_ref, mac_address in dict(
            binding.get("mac_by_interface_ref") or {}
        ).items()
        if str(interface_ref) and mac_address not in (None, "", "None")
    }
    if not ifindex_by_ref or not mac_by_ref:
        raise RuntimeError("horizon_maintenance_base_generation_binding_cache_empty")
    return ifindex_by_ref, mac_by_ref


def _prepared_attach_capability_readiness(
    attach_report: Mapping[str, Any],
) -> Dict[str, Any]:
    source = dict(attach_report or {})
    prepare_steps = dict(source.get("prepare_steps") or {})
    real_prepare = dict(prepare_steps.get("real_backend_prepare") or {})
    attach_prepare = dict(
        source.get("preactivation_attach_prepare")
        or prepare_steps.get("preactivation_attach_prepare")
        or {}
    )
    attached_interface_details = [
        dict(item)
        for item in list(
            source.get("attached_interface_details")
            or real_prepare.get("attached_interface_details")
            or attach_prepare.get("attached_interface_details")
            or []
        )
        if isinstance(item, Mapping)
    ]
    attached_interfaces = sorted(
        {
            str(item)
            for item in list(
                source.get("attached_interfaces")
                or real_prepare.get("attached_interfaces")
                or attach_prepare.get("attached_interfaces")
                or []
            )
            if str(item)
        }
        | {
            str(
                dict(item).get("interface_ref")
                or dict(item).get("interface_name")
                or ""
            )
            for item in attached_interface_details
            if str(
                dict(item).get("interface_ref")
                or dict(item).get("interface_name")
                or ""
            )
        }
    )
    attach_plan = {
        str(interface_ref): sorted(
            {str(direction) for direction in list(directions or []) if str(direction)}
        )
        for interface_ref, directions in dict(
            source.get("attach_plan")
            or real_prepare.get("attach_plan")
            or attach_prepare.get("attach_plan")
            or {}
        ).items()
        if str(interface_ref)
    }
    compact_attach_prepare = {
        key: attach_prepare.get(key)
        for key in (
            "schema_version",
            "ok",
            "reason",
            "source",
            "runtime_apply_skipped",
            "source_runtime_apply_occurred",
            "active_generation_set",
            "map_ready",
            "runtime_full_generation_reload_count",
            "full_attach_count",
            "delta_attach_count",
            "prepared_bpf_attach_capability_count",
            "prepared_bpf_attach_interface_count",
            "artifact_path",
        )
        if key in attach_prepare
    }
    compact_attach_prepare.update(
        {
            "attach_plan": attach_plan,
            "attached_interfaces": attached_interfaces,
            "attached_interface_details": attached_interface_details,
        }
    )
    return {
        "schema_version": "skyseed-prepared-attach-capability-readiness-v1",
        "ready": bool(source.get("ok") is True),
        "ok": bool(source.get("ok", True)),
        "reason": str(source.get("reason") or ""),
        "attach_plan": attach_plan,
        "attached_interfaces": attached_interfaces,
        "attached_interface_details": attached_interface_details,
        "mpls_map_entry_counts": dict(
            real_prepare.get("compiled_mpls_map_counts")
            or source.get("mpls_map_entry_counts")
            or {}
        ),
        "map_capacities": dict(
            real_prepare.get("map_capacities") or source.get("map_capacities") or {}
        ),
        "prepare_steps": {
            "real_backend_prepare": {
                "attach_plan": attach_plan,
                "attached_interfaces": attached_interfaces,
                "attached_interface_details": attached_interface_details,
                "compiled_mpls_map_counts": dict(
                    real_prepare.get("compiled_mpls_map_counts")
                    or source.get("mpls_map_entry_counts")
                    or {}
                ),
                "map_capacities": dict(
                    real_prepare.get("map_capacities")
                    or source.get("map_capacities")
                    or {}
                ),
            },
            "preactivation_attach_prepare": compact_attach_prepare,
        },
        "preactivation_attach_prepare": compact_attach_prepare,
        "preactivation_attach_prepare_records": [
            {
                "schema_version": (
                    "skyseed-rolling-horizon-bpf-attach-capability-"
                    "prepare-record-v1"
                ),
                "ok": bool(source.get("ok")),
                "reason": str(source.get("reason") or ""),
                "delta_attach_count": int(source.get("delta_attach_count") or 0),
                "full_attach_count": int(source.get("full_attach_count") or 0),
                "runtime_full_generation_reload_count": int(
                    source.get("runtime_full_generation_reload_count") or 0
                ),
                "runtime_apply_skipped": True,
                "active_generation_set": False,
                "map_ready": False,
            }
        ],
        "prepared_bpf_attach_capability_count": int(
            source.get("prepared_bpf_attach_capability_count")
            or sum(
                len(list(dict(item).get("expected_roles") or []))
                for item in attached_interface_details
            )
            or 0
        ),
        "prepared_bpf_attach_interface_count": len(attached_interface_details),
    }


def _attach_report_uses_resident_budget(
    *,
    attach_report: Mapping[str, Any],
    resident_attach_budget: Mapping[str, Any],
) -> Dict[str, Any]:
    budget = dict(resident_attach_budget or {})
    expected_max = int(
        budget.get("max_attach_interfaces")
        or budget.get("max_resident_attach_interfaces")
        or 0
    )
    if expected_max <= 0:
        return dict(attach_report or {})
    report = dict(attach_report or {})
    prepare_steps = dict(report.get("prepare_steps") or {})
    nested_prepare = dict(prepare_steps.get("preactivation_attach_prepare") or {})
    observed = dict(
        report.get("attach_cardinality_budget")
        or nested_prepare.get("attach_cardinality_budget")
        or {}
    )
    observed_source = str(observed.get("budget_source") or "")
    observed_max = int(
        observed.get("max_attach_interfaces")
        or observed.get("max_resident_attach_interfaces")
        or 0
    )
    if observed_source and observed_source != "default" and observed_max >= expected_max:
        return report
    reason = "process_worker_attach_prepare_resident_budget_not_consumed"
    report.update(
        {
            "ok": False,
            "ready": False,
            "reason": reason,
            "fallback_reason": reason,
            "fallback_to_linux": False,
            "fail_closed": True,
            "blocked_stage": "bpf_attach_capability_prepare",
            "resident_attach_coverage_budget": dict(budget),
            "bpf_attach_budget": dict(budget),
            "attach_cardinality_budget": dict(observed),
            "resident_attach_budget_consumed": False,
        }
    )
    prepare_steps["preactivation_attach_prepare"] = nested_prepare
    report["prepare_steps"] = prepare_steps
    return report


def _inactive_preload_record(
    *,
    queued_record: Mapping[str, Any],
    preload_report: Mapping[str, Any],
    retain_generation_ids: list[int],
) -> Dict[str, Any]:
    queued = dict(queued_record or {})
    report = dict(preload_report or {})
    compiled = dict(report.get("compiled_runtime_inputs") or {})
    active_generation = dict(compiled.get("active_generation") or {})
    activation_inputs = {
        "schema_version": "skyseed-bpf-owned-compiled-runtime-inputs-v2",
        "active_generation": active_generation,
        "ingress_classifier_map": [],
        "ut_gateway_policy_map": [],
        "gateway_selector_ingress_map": [],
        "core_lfib_map": [],
        "egress_handoff_map": [],
        "zone_edge_delivery_map": [],
        "generation_base_map": [],
        "compiled_mpls_map_counts": dict(
            compiled.get("compiled_mpls_map_counts")
            or report.get("mpls_map_entry_counts")
            or {}
        ),
        "bpf_map_write_manifest_only": True,
        "batch_map_write_required": True,
        "activation_record_only": True,
    }
    compact_preload_proof = {
        key: report.get(key)
        for key in (
            "runtime_epoch_intent",
            "intent_hash",
            "eligible_satellite_core_gz_scope",
            "g_to_z_entries",
            "z_to_g_entries",
            "map_entry_count",
            "map_entry_hash",
            "attached_interfaces",
        )
        if key in report
    }
    return {
        **queued,
        "schema_version": "skyseed-queued-bpf-preloaded-generation-v1",
        "ok": bool(report.get("ok")),
        "reason": str(report.get("reason") or ""),
        "inactive_generation_written": bool(report.get("inactive_generation_written")),
        "active_generation_flip_skipped": bool(
            report.get("active_generation_flip_skipped")
        ),
        "runtime_apply_skipped": bool(report.get("runtime_apply_skipped")),
        "delta_attach_count": int(report.get("delta_attach_count") or 0),
        "full_attach_count": int(report.get("full_attach_count") or 0),
        "runtime_full_generation_reload_count": int(
            report.get("runtime_full_generation_reload_count") or 0
        ),
        "retain_inactive_generations": list(retain_generation_ids),
        "compiled_runtime_inputs": activation_inputs,
        "compiled_runtime_inputs_source": str(
            report.get("compiled_runtime_inputs_source") or ""
        ),
        "bpf_map_write_manifest_only": bool(
            report.get("bpf_map_write_manifest_only")
            or dict(report.get("preactivation_preload") or {}).get(
                "manifest_only_loader"
            )
        ),
        "manifest_only_loader": bool(
            report.get("manifest_only_loader")
            or dict(report.get("preactivation_preload") or {}).get(
                "manifest_only_loader"
            )
        ),
        "manifest_only_map_write_used": bool(
            report.get("manifest_only_map_write_used")
            or dict(report.get("preactivation_preload") or {}).get(
                "manifest_only_loader"
            )
        ),
        "batch_map_write_required": True,
        "batch_map_write_used": bool(
            report.get("batch_map_write_used")
            or dict(report.get("preactivation_preload") or {}).get(
                "batch_map_write_used"
            )
        ),
        "batch_map_write_update_count": int(
            report.get("batch_map_write_update_count")
            or dict(report.get("preactivation_preload") or {}).get(
                "batch_map_write_update_count"
            )
            or 0
        ),
        "actual_data_write_count": int(
            report.get("actual_data_write_count")
            or dict(report.get("preactivation_preload") or {}).get(
                "actual_data_write_count"
            )
            or 0
        ),
        "preload_report": compact_preload_proof,
        "preactivation_preload_attach_capability_contract": dict(
            report.get("preactivation_preload_attach_capability_contract") or {}
        ),
        "prepared_attach_capability_readiness_source": (
            "process_horizon_maintenance_prepared_attach_capability"
        ),
        "prepared_attach_capability_readiness_required": True,
    }


def run_horizon_maintenance_process_payload(
    payload: Mapping[str, Any],
) -> Dict[str, Any]:
    started = time.perf_counter()
    stage_wall_seconds: Dict[str, float] = {}
    source = dict(payload or {})
    if source.get("schema_version") != "skyseed-horizon-maintenance-process-work-v3":
        raise ValueError("horizon_maintenance_process_work_schema_invalid")
    max_attach_workers = int(source.get("max_node_workers") or 0)
    if max_attach_workers <= 0:
        raise RuntimeError("horizon_maintenance_max_attach_workers_missing")
    frozen_container_pid_cache = {
        str(node_id): int(pid)
        for node_id, pid in dict(source.get("frozen_container_pid_cache") or {}).items()
        if str(node_id) and int(pid) > 0
    }
    if not frozen_container_pid_cache or len(frozen_container_pid_cache) != len(
        dict(source.get("frozen_container_pid_cache") or {})
    ):
        raise RuntimeError("horizon_maintenance_frozen_container_pid_cache_invalid")
    consume_frozen_container_pid_cache(frozen_container_pid_cache)
    request = _request_from_payload(dict(source.get("request") or {}))
    segment_dir = Path(str(source.get("segment_dir") or ""))
    topology_path = _required_non_directory_path(
        source.get("topology_path"),
        field_name="process_worker_topology_path",
    )
    link_state_path = _required_non_directory_path(
        source.get("link_state_path"),
        field_name="process_worker_link_state_path",
    )
    retain_generation_ids = [
        int(item) for item in list(source.get("retain_generation_ids") or [])
    ]
    prepared_generation_window_count = int(
        source.get("prepared_generation_window_count") or 1
    )

    restore_started = time.perf_counter()
    executor = MinimalRealNetworkMutationExecutor()
    delta_scope = dict(source.get("delta_prepare_scope_by_slot") or {})
    existing_prepared_scope = dict(
        source.get("existing_prepared_resource_scope_by_slot") or {}
    )
    expected_owned_scope = _merge_prepared_resource_scopes(
        existing_prepared_scope,
        delta_scope,
    )
    restore_report = executor.restore_prepared_runtime_state(
        topology_path=topology_path,
        link_state_path=link_state_path,
        prepared_resource_scope_by_slot=existing_prepared_scope,
        prepared_resource_counts=dict(
            source.get("existing_prepared_resource_counts") or {}
        ),
        frozen_container_pid_cache=frozen_container_pid_cache,
    )
    stage_wall_seconds["prepared_state_restore"] = (
        time.perf_counter() - restore_started
    )
    additive_prepare_started = time.perf_counter()
    if delta_scope:
        host_prepare = executor.prepare_additive_resources(
            topology_path=topology_path,
            link_state_path=link_state_path,
            prepared_resource_scope_by_slot=delta_scope,
        )
    else:
        host_prepare = _empty_host_prepare_report(source)
    stage_wall_seconds["host_additive_prepare"] = (
        time.perf_counter() - additive_prepare_started
    )
    host_prepare["prepared_runtime_state_restore"] = restore_report
    actual_owned_scope = {
        str(slot_name): sorted(
            {
                str(resource_key)
                for resource_key in list(resource_keys or [])
                if str(resource_key)
            }
        )
        for slot_name, resource_keys in dict(
            host_prepare.get("prepared_owned_resource_scope_by_slot")
            or expected_owned_scope
        ).items()
        if str(slot_name)
    }
    if actual_owned_scope != expected_owned_scope:
        raise RuntimeError(
            "horizon_maintenance_prepared_owned_scope_mismatch:"
            + json.dumps(
                {
                    "expected_slot_count": len(expected_owned_scope),
                    "expected_resource_count": sum(
                        len(items) for items in expected_owned_scope.values()
                    ),
                    "actual_slot_count": len(actual_owned_scope),
                    "actual_resource_count": sum(
                        len(items) for items in actual_owned_scope.values()
                    ),
                },
                sort_keys=True,
            )
        )
    host_prepare = {
        **dict(host_prepare),
        "topology_path": str(topology_path),
        "link_state_path": str(link_state_path),
        "segment_id": str(source.get("segment_id") or request.segment_id),
        "horizon_id": str(source.get("horizon_id") or request.next_horizon_id),
        "maintenance_stage": "runtime/horizon_replenishment/additive_host_prepare",
        "resource_coverage_plan": dict(source.get("resource_coverage_plan") or {}),
        "required_resource_scope_by_slot": dict(
            source.get("prepared_resource_scope_by_slot") or {}
        ),
        "prepared_resource_scope_by_slot": actual_owned_scope,
        "prepared_owned_resource_scope_by_slot": actual_owned_scope,
        "prepared_resource_counts": dict(
            host_prepare.get("prepared_resource_counts")
            or {
                "bridge_count": len(actual_owned_scope),
                "preconnected_endpoint_count": 0,
                "candidate_resource_count": sum(
                    len(resource_keys)
                    for resource_keys in actual_owned_scope.values()
                ),
                "candidate_endpoint_count": int(
                    host_prepare.get("candidate_endpoint_count") or 0
                ),
            }
        ),
        "delta_prepare_resource_scope_by_slot": delta_scope,
        "source_of_truth_delta_from_prepared_owned_resource_index": True,
        "full_host_scan_skipped": True,
        "future_host_link_state_apply_allowed": False,
        "preapply_qdisc_parameter_updates_allowed": False,
        "active_generation_flip_allowed": False,
        "runtime_recompute_network_mutation_plan_allowed": False,
        "full_prepare_allowed": False,
    }

    preload_work_items = _hydrate_preload_work_item_contexts(
        source,
        _load_preload_work_items(source),
    )
    base_generation_record = dict(source.get("base_generation_record") or {})
    base_generation_compiled_inputs = dict(
        base_generation_record.get("compiled_runtime_inputs") or {}
    )
    resident_attach_budget = dict(
        source.get("resident_attach_coverage_budget")
        or source.get("bpf_attach_budget")
        or base_generation_compiled_inputs.get("resident_attach_coverage_budget")
        or base_generation_compiled_inputs.get("bpf_attach_budget")
        or {}
    )
    interface_ifindex_cache, interface_mac_cache = (
        _base_generation_interface_binding_caches(
            base_generation_compiled_inputs
        )
    )
    _merge_created_interface_bindings(
        interface_ifindex_cache=interface_ifindex_cache,
        interface_mac_cache=interface_mac_cache,
        bindings=list(host_prepare.get("created_container_interface_bindings") or []),
    )
    binding_requirement_started = time.perf_counter()
    required_ifindex_refs, required_mac_refs, route_contexts = (
        _required_interface_refs_for_preload_work_items(
            items=preload_work_items,
            topology_path=topology_path,
        )
    )
    stage_wall_seconds["binding_requirement_derivation"] = (
        time.perf_counter() - binding_requirement_started
    )
    final_binding_refs = sorted(required_ifindex_refs | required_mac_refs)
    final_binding_started = time.perf_counter()
    final_binding_prepare = executor.capture_existing_container_interface_bindings(
        interface_refs=final_binding_refs
    )
    if (
        final_binding_prepare.get("ok") is not True
        or final_binding_prepare.get("full_scan") is not False
    ):
        raise RuntimeError("horizon_maintenance_final_binding_prepare_failed")
    final_bindings = list(final_binding_prepare.get("bindings") or [])
    _validate_final_interface_binding_scope(
        required_interface_refs=final_binding_refs,
        bindings=final_bindings,
    )
    final_binding_merge = _merge_final_interface_bindings(
        interface_ifindex_cache=interface_ifindex_cache,
        interface_mac_cache=interface_mac_cache,
        bindings=final_bindings,
    )
    stage_wall_seconds["final_binding_prepare"] = (
        time.perf_counter() - final_binding_started
    )
    host_prepare["final_interface_binding_prepare"] = {
        key: value
        for key, value in dict(final_binding_prepare).items()
        if key != "bindings"
    }
    host_prepare["final_interface_binding_prepare"].update(
        {
            "binding_count": len(final_bindings),
            "required_ifindex_ref_count": len(required_ifindex_refs),
            "required_mac_ref_count": len(required_mac_refs),
            "creation_facts_superseded_at_final_prepare_boundary": True,
            **final_binding_merge,
        }
    )
    edge_route_started = time.perf_counter()
    edge_route_prepare = _prepare_bpf_owned_edge_route_baseline(
        contexts=route_contexts,
        max_node_workers=int(source.get("max_node_workers") or 2),
        prepared_route_identity_evidence=dict(
            source.get("prepared_bpf_owned_route_identity_evidence") or {}
        ),
    )
    stage_wall_seconds["edge_route_prepare"] = (
        time.perf_counter() - edge_route_started
    )
    edge_route_prepare_report = dict(edge_route_prepare.get("report") or {})
    if edge_route_prepare_report.get("ready") is not True:
        raise RuntimeError(
            "horizon_maintenance_edge_route_baseline_prepare_failed:"
            + str(edge_route_prepare_report.get("reason") or "not_ready")
        )
    edge_route_prepare_report = {
        **edge_route_prepare_report,
        "maintenance_stage": (
            "runtime/horizon_replenishment/edge_route_additive_prepare"
        ),
        "process_owned": True,
        "runtime_apply_allowed": False,
        "active_generation_flip_allowed": False,
    }
    frozen_compile_started = time.perf_counter()
    frozen_preload_items = [
        _freeze_preload_work_item(
            item=dict(item_any or {}),
            base_generation_compiled_inputs=base_generation_compiled_inputs,
            resident_attach_budget=resident_attach_budget,
            topology_path=topology_path,
            interface_ifindex_cache=interface_ifindex_cache,
            interface_mac_cache=interface_mac_cache,
        )
        for item_any in preload_work_items
    ]
    stage_wall_seconds["strict_generation_compile_from_frozen_context"] = (
        time.perf_counter() - frozen_compile_started
    )
    attach_inputs = dict(source.get("attach_frozen_compiled_runtime_inputs") or {})
    if not attach_inputs:
        attach_inputs = _union_frozen_inputs(frozen_preload_items)
    if resident_attach_budget:
        attach_inputs["resident_attach_coverage_budget"] = dict(resident_attach_budget)
        attach_inputs["bpf_attach_budget"] = dict(resident_attach_budget)
    attach_started = time.perf_counter()
    attach_report = _prepare_bpf_owned_preactivation_attach_capability_from_frozen_inputs(
        topology_path=topology_path,
        frozen_compiled_runtime_inputs=attach_inputs,
        bpf_owned_readiness=dict(source.get("bpf_owned_readiness") or {}),
        max_attach_workers=max_attach_workers,
        retain_inactive_generations=retain_generation_ids,
        prepared_generation_window_count=prepared_generation_window_count,
    )
    attach_report = _attach_report_uses_resident_budget(
        attach_report=attach_report,
        resident_attach_budget=resident_attach_budget,
    )
    attach_report = {
        **dict(attach_report),
        "segment_id": str(source.get("segment_id") or request.segment_id),
        "horizon_id": str(source.get("horizon_id") or request.next_horizon_id),
        "maintenance_stage": (
            "runtime/horizon_replenishment/bpf_attach_capability_prepare"
        ),
        "runtime_apply_allowed": False,
        "future_map_write_allowed": False,
        "active_generation_flip_allowed": False,
    }
    prepared_attach_readiness = _prepared_attach_capability_readiness(attach_report)
    stage_wall_seconds["bpf_attach_capability_prepare"] = (
        time.perf_counter() - attach_started
    )

    inactive_records: list[Dict[str, Any]] = []
    inactive_preload_started = time.perf_counter()
    for item in frozen_preload_items:
        preload_report = _prepare_bpf_owned_preactivation_preload_from_frozen_inputs(
            topology_path=topology_path,
            frozen_compiled_runtime_inputs={
                **dict(item.get("frozen_compiled_runtime_inputs") or {}),
                **(
                    {
                        "resident_attach_coverage_budget": dict(
                            resident_attach_budget
                        ),
                        "bpf_attach_budget": dict(resident_attach_budget),
                    }
                    if resident_attach_budget
                    else {}
                ),
            },
            bpf_owned_readiness=prepared_attach_readiness,
            retain_inactive_generations=retain_generation_ids,
            prepared_generation_window_count=prepared_generation_window_count,
            base_generation_compiled_inputs=dict(
                item.get("base_generation_compiled_inputs") or {}
            ),
            require_prepared_attach_capability=True,
        )
        inactive_records.append(
            _inactive_preload_record(
                queued_record=dict(item.get("queued_record") or {}),
                preload_report=preload_report,
                retain_generation_ids=retain_generation_ids,
            )
        )
    stage_wall_seconds["inactive_generation_preload"] = (
        time.perf_counter() - inactive_preload_started
    )
    retired_cleanup_work_items = _load_retired_cleanup_work_items(source)
    existing_cleanup_completed_records = [
        dict(item)
        for item in list(source.get("retired_cleanup_completed_records") or [])
        if isinstance(item, Mapping)
    ]
    existing_cleanup_blocked_records = [
        dict(item)
        for item in list(source.get("retired_cleanup_blocked_records") or [])
        if isinstance(item, Mapping)
    ]
    retired_cleanup_started = time.perf_counter()
    cleanup_execution = _execute_retired_cleanup_work_items(
        retired_cleanup_work_items
    )
    stage_wall_seconds["retired_generation_cleanup"] = (
        time.perf_counter() - retired_cleanup_started
    )
    cleanup_completed_records = [
        *existing_cleanup_completed_records,
        *list(cleanup_execution.get("completed_records") or []),
    ]
    cleanup_blocked_records = [
        *existing_cleanup_blocked_records,
        *list(cleanup_execution.get("blocked_records") or []),
    ]
    cleanup_handoff_artifact = (
        segment_dir / "horizon_maintenance_handoff_retired_cleanup.json"
    )
    _write_json(
        cleanup_handoff_artifact,
        {
            "schema_version": (
                "skyseed-horizon-maintenance-handoff-retired-cleanup-v1"
            ),
            "previous_horizon_id": str(request.previous_horizon_id),
            "process_owned": True,
            "requested_count": len(retired_cleanup_work_items),
            "completed_count": len(cleanup_completed_records),
            "blocked_count": len(cleanup_blocked_records),
            "completed_records": [
                _compact_retired_cleanup_record(record)
                for record in cleanup_completed_records
            ],
            "blocked_records": [
                _compact_retired_cleanup_record(record)
                for record in cleanup_blocked_records
            ],
            "execution_wall_seconds": float(cleanup_execution.get("wall_seconds") or 0.0),
        },
    )
    if not str(request.previous_horizon_id):
        cleanup_status = "not_required"
    elif cleanup_blocked_records:
        cleanup_status = "blocked"
    elif retired_cleanup_work_items or cleanup_completed_records:
        cleanup_status = "completed"
    else:
        cleanup_status = "unknown"
    cleanup_report = retired_cleanup_dependency_report(
        previous_horizon_id=str(request.previous_horizon_id),
        queued_count=0,
        completed_count=len(cleanup_completed_records),
        blocked_count=len(cleanup_blocked_records),
        blockers=cleanup_blocked_records,
        status=cleanup_status,
    )
    cleanup_report = {
        **cleanup_report,
        "process_owned": True,
        "handoff_artifact": str(cleanup_handoff_artifact),
        "requested_count": len(retired_cleanup_work_items),
        "execution_wall_seconds": float(cleanup_execution.get("wall_seconds") or 0.0),
    }
    attach_handoff_artifact = (
        segment_dir / "horizon_maintenance_handoff_attach_capability.json"
    )
    _write_json(
        attach_handoff_artifact,
        {
            "schema_version": (
                "skyseed-horizon-maintenance-handoff-attach-capability-v1"
            ),
            "horizon_id": str(request.next_horizon_id),
            "request_hash": request.request_hash,
            # The raw attach report includes the frozen compiler payload in
            # several nested diagnostic branches.  The parent consumes only
            # the immutable interface/role/binding capability, so publish
            # that canonical projection instead of duplicating tens of
            # megabytes at the process handoff.
            "report": prepared_attach_readiness,
            "source_report_summary": {
                "ok": bool(attach_report.get("ok")),
                "reason": str(attach_report.get("reason") or ""),
                "prepared_bpf_attach_capability_count": int(
                    attach_report.get("prepared_bpf_attach_capability_count") or 0
                ),
                "prepared_bpf_attach_interface_count": len(
                    list(attach_report.get("attached_interface_details") or [])
                ),
                "raw_report_embedded": False,
            },
        },
    )
    handoff_artifact = segment_dir / "horizon_maintenance_handoff_preloaded_generations.json"
    _write_json(
        handoff_artifact,
        {
            "schema_version": "skyseed-horizon-maintenance-handoff-preloaded-generations-v1",
            "horizon_id": str(request.next_horizon_id),
            "record_count": len(inactive_records),
            "records": inactive_records,
        },
    )
    result = classify_horizon_maintenance(
        request=request,
        host_additive_prepare=host_prepare,
        attach_capability_prepare=attach_report,
        base_generation_record=base_generation_record,
        queued_preload_records=[
            dict(item.get("queued_record") or {})
            for item in preload_work_items
        ],
        inactive_preload_records=inactive_records,
        retired_cleanup_report=cleanup_report,
        wall_seconds=time.perf_counter() - started,
    )
    result = {
        **dict(result),
        "worker_mode": "process",
        "process_boundary": True,
        "host_prepare_process_owned": True,
        "frozen_compile_process_owned": True,
        "active_generation_flip_allowed": False,
        "runtime_full_reload_allowed": False,
        "full_attach_fallback_allowed": False,
        "future_host_link_activation_allowed": False,
        "handoff_preloaded_generation_artifact": str(handoff_artifact),
        "handoff_attach_capability_artifact": str(attach_handoff_artifact),
        "retired_cleanup_process_owned": True,
        "retired_cleanup_handoff_artifact": str(cleanup_handoff_artifact),
        "retired_cleanup_completed_count": len(cleanup_completed_records),
        "retired_cleanup_blocked_count": len(cleanup_blocked_records),
        "edge_route_baseline_prepare_report": edge_route_prepare_report,
    }
    measured_stage_total = sum(stage_wall_seconds.values())
    result["subtask_wall_seconds"] = {
        key: round(value, 6)
        for key, value in stage_wall_seconds.items()
    }
    result["subtask_wall_seconds"].update(
        {
            "summed_subtask_wall_seconds": round(measured_stage_total, 6),
            "unattributed_before_publish": round(
                max(0.0, time.perf_counter() - started - measured_stage_total),
                6,
            ),
        }
    )
    artifact_path = publish_horizon_maintenance_result(
        result=result,
        segment_dir=segment_dir,
    )
    return {
        "schema_version": "skyseed-horizon-maintenance-process-summary-v1",
        "status": str(result.get("status") or ""),
        "ready": bool(result.get("ready")),
        "artifact_path": str(artifact_path),
        "request_hash": str(result.get("request_hash") or ""),
        "inactive_preload_record_count": len(inactive_records),
        "host_additive_prepare_ok": bool(host_prepare.get("ok") is True),
        "attach_capability_prepare_ok": bool(attach_report.get("ok") is True),
        "edge_route_baseline_prepare_ok": bool(
            edge_route_prepare_report.get("ready") is True
        ),
        "process_input_compact_manifest": bool(source.get("process_input_compact_manifest")),
        "preload_work_item_count": len(preload_work_items),
        "result_blocker_count": int(result.get("blocker_count") or 0),
        "result_blockers": list(result.get("blockers") or [])[:5],
        "retired_cleanup_process_owned": True,
        "retired_cleanup_handoff_artifact": str(cleanup_handoff_artifact),
        "retired_cleanup_completed_count": len(cleanup_completed_records),
        "retired_cleanup_blocked_count": len(cleanup_blocked_records),
        "wall_seconds": round(time.perf_counter() - started, 6),
    }


def _publish_process_exception_failure(
    *,
    input_path: Path,
    output_path: Path,
    exc: Exception,
) -> Dict[str, Any]:
    payload = _read_json(input_path)
    source = dict(payload or {})
    request = _request_from_payload(dict(source.get("request") or {}))
    segment_dir_value = str(source.get("segment_dir") or "")
    segment_dir = Path(segment_dir_value) if segment_dir_value else output_path.parent
    result = {
        "schema_version": "skyseed-horizon-maintenance-result-v1",
        "status": "failed",
        "ready": False,
        "request": request.to_payload(),
        "request_hash": request.request_hash,
        "segment_index": int(request.segment_index),
        "segment_id": str(request.segment_id),
        "current_horizon_id": str(request.current_horizon_id),
        "next_horizon_id": str(request.next_horizon_id),
        "previous_horizon_id": str(request.previous_horizon_id),
        "source_state_id": str(request.source_state_id),
        "base_generation_id": int(request.base_generation_id or 0),
        "prepared_generation_ids": list(request.prepared_generation_ids or []),
        "expected_preload_count": int(request.expected_preload_count or 0),
        "queued_preload_record_count": int(source.get("preload_work_item_count") or 0),
        "inactive_preload_record_count": 0,
        "blockers": [
            {
                "stage": "horizon_maintenance_process_worker",
                "reason": str(exc) or exc.__class__.__name__,
                "exception_type": exc.__class__.__name__,
            }
        ],
        "blocker_count": 1,
        "worker_mode": "process",
        "process_boundary": True,
        "process_exception": True,
        "process_input_artifact": str(input_path),
        "active_generation_flip_allowed": False,
        "runtime_full_reload_allowed": False,
        "full_attach_fallback_allowed": False,
        "future_host_link_activation_allowed": False,
        "live_full_map_dump_diff_allowed": False,
        "target_generation_cleanup_scan_allowed": False,
        "generation_gc_scan_allowed": False,
    }
    artifact_path = publish_horizon_maintenance_result(
        result=result,
        segment_dir=segment_dir,
    )
    return {
        "schema_version": "skyseed-horizon-maintenance-process-summary-v1",
        "status": "failed",
        "ready": False,
        "artifact_path": str(artifact_path),
        "request_hash": request.request_hash,
        "exception_type": exc.__class__.__name__,
        "reason": str(exc),
        "input": str(input_path),
        "failure_artifact_published": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
        summary = run_horizon_maintenance_process_payload(payload)
        _write_json(Path(args.output), summary)
        return 0
    except Exception as exc:  # noqa: BLE001
        summary = _publish_process_exception_failure(
            input_path=Path(args.input),
            output_path=Path(args.output),
            exc=exc,
        )
        _write_json(Path(args.output), summary)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
