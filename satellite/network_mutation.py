#!/usr/bin/env python3
# encoding: utf-8

"""NetworkMutationPlan helpers for Iteration 2 experiment transitions."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List

from contracts import (
    NETWORK_MUTATION_PLAN_SCHEMA_VERSION,
    NetworkMutationPlan,
)


MANAGED_DYNAMIC_SLOT_TYPES = ("ut_access", "gw_access", "cross_plane_isl")


def _fingerprint(payload: Dict[str, Any]) -> str:
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()[:16]


def load_link_state(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def dynamic_slot_state_from_link_state(link_state: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    slot_state: Dict[str, Dict[str, Any]] = {}
    for slot in link_state.get("slots", []):
        slot_type = str(slot.get("slot_type"))
        if slot_type not in MANAGED_DYNAMIC_SLOT_TYPES:
            continue
        slot_name = str(slot.get("slot_name"))
        binding = dict(slot.get("binding") or {})
        slot_state[slot_name] = {
            "slot_name": slot_name,
            "slot_type": slot_type,
            "occupied": bool(slot.get("occupied", False)),
            "binding": binding,
            "endpoints": [
                dict(endpoint)
                for endpoint in list(slot.get("preconnected_endpoints", []))
                + list(slot.get("dynamic_endpoints", []))
            ],
        }
    return slot_state


def dynamic_slot_state_from_snapshot(snapshot: Any) -> Dict[str, Dict[str, Any]]:
    slot_state: Dict[str, Dict[str, Any]] = {}
    for link in snapshot.active_links:
        slot_type = str(link.get("slot_type"))
        if slot_type not in MANAGED_DYNAMIC_SLOT_TYPES:
            continue
        slot_name = str(link.get("link_id"))
        slot_state[slot_name] = {
            "slot_name": slot_name,
            "slot_type": slot_type,
            "occupied": True,
            "binding": dict(link.get("binding", {})),
            "endpoints": [dict(endpoint) for endpoint in link.get("endpoints", [])],
        }
    return slot_state


def _managed_slot_names(
    *,
    source_state: Dict[str, Dict[str, Any]],
    target_state: Dict[str, Dict[str, Any]],
) -> List[str]:
    if target_state:
        return sorted(target_state)
    return sorted(source_state)


def _slot_transition_operation(
    *,
    slot_name: str,
    source_slot: Dict[str, Any] | None,
    target_slot: Dict[str, Any] | None,
) -> Dict[str, Any] | None:
    source_binding = dict(source_slot.get("binding", {})) if source_slot else {}
    target_binding = dict(target_slot.get("binding", {})) if target_slot else {}
    source_occupied = bool(source_slot.get("occupied", False)) if source_slot else False
    target_occupied = bool(target_slot.get("occupied", False)) if target_slot else False
    slot_type = str(
        (target_slot or source_slot or {}).get("slot_type", "unknown_dynamic_slot")
    )

    if source_occupied == target_occupied and source_binding == target_binding:
        return None
    if source_occupied and target_occupied:
        operation = "rebind_slot_attachment"
    elif target_occupied:
        operation = "activate_slot_attachment"
    else:
        operation = "deactivate_slot_attachment"

    return {
        "operation": operation,
        "slot_name": slot_name,
        "slot_type": slot_type,
        "execution_domain": "host_network_mutation",
        "source_binding": source_binding,
        "target_binding": target_binding,
        "source_occupied": source_occupied,
        "target_occupied": target_occupied,
    }


def build_network_mutation_plan(
    *,
    source_state: Dict[str, Dict[str, Any]],
    source_state_id: str,
    target_snapshot: Any,
    transition_name: str,
) -> NetworkMutationPlan:
    target_state = dynamic_slot_state_from_snapshot(target_snapshot)
    managed_slot_names = _managed_slot_names(
        source_state=source_state,
        target_state=target_state,
    )
    operations: List[Dict[str, Any]] = []
    for slot_name in managed_slot_names:
        operation = _slot_transition_operation(
            slot_name=slot_name,
            source_slot=source_state.get(slot_name),
            target_slot=target_state.get(slot_name),
        )
        if operation is not None:
            operations.append(operation)

    mutation_seed = {
        "source_state_id": source_state_id,
        "target_snapshot_id": target_snapshot.snapshot_id,
        "managed_slot_names": managed_slot_names,
        "operations": operations,
    }
    mutation_id = f"netmut-{_fingerprint(mutation_seed)}"
    return NetworkMutationPlan(
        schema_version=NETWORK_MUTATION_PLAN_SCHEMA_VERSION,
        mutation_id=mutation_id,
        source_snapshot={
            "source_state_id": source_state_id,
            "transition_name": transition_name,
        },
        target_scope={
            "state_model": "delta_mutation",
            "target_snapshot_id": target_snapshot.snapshot_id,
            "target_slice_id": target_snapshot.time_slice.get("slice_id"),
            "managed_slot_names": managed_slot_names,
        },
        execution_boundary={
            "consumer": "host_network_runtime_manager",
            "separate_from_runtime_update_plan": True,
            "mutation_scope": "host_side_link_and_slot_state_only",
            "dry_run_mode": True,
        },
        operations=operations,
        validation_expectations={
            "pre_apply_validation": "required",
            "post_apply_validation": "delta_state_reconciliation_required",
            "runtime_forwarding_state_carried_elsewhere": "RuntimeUpdatePlan",
        },
    )


def summarize_network_mutation_plan(plan: NetworkMutationPlan) -> Dict[str, Any]:
    operation_counts: Dict[str, int] = {}
    for operation in plan.operations:
        operation_name = str(operation.get("operation"))
        operation_counts[operation_name] = operation_counts.get(operation_name, 0) + 1
    return {
        "mutation_id": plan.mutation_id,
        "operation_count": len(plan.operations),
        "operation_counts": operation_counts,
        "slot_names": [
            str(operation.get("slot_name"))
            for operation in plan.operations
        ],
    }
