#!/usr/bin/env python3
# encoding: utf-8

"""NetworkMutationPlan helpers for Iteration 2 experiment transitions."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

from contracts import (
    NETWORK_MUTATION_PLAN_SCHEMA_VERSION,
    NetworkMutationPlan,
)


MANAGED_DYNAMIC_SLOT_TYPES = (
    "ut_access",
    "gw_access",
    "same_plane_isl",
    "cross_plane_isl",
)
DEFAULT_RUNTIME_PARAMETER_MUTATION_IGNORED_FIELDS = (
    "distance_km",
    "visibility_elevation_deg",
)
_RUNTIME_PARAMETER_EXCLUDED_FIELDS = {
    "slot_name",
    "link_id",
    "slot_type",
    "occupied",
    "binding",
    "endpoints",
    "dynamic_endpoints",
    "preconnected_endpoints",
    "candidate_bindings",
    "visibility_owner_node_id",
}


def _fingerprint(payload: Dict[str, Any]) -> str:
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()[:16]


def load_link_state(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def make_runtime_parameter_mutation_policy(
    *,
    delay_threshold_ms: float,
    loss_threshold_pct: float,
    bandwidth_threshold_pct: float,
    routing_cost_threshold: int,
    ignored_fields: Sequence[str] | None = None,
) -> Dict[str, Any]:
    return {
        "delay_threshold_ms": float(delay_threshold_ms),
        "loss_threshold_pct": float(loss_threshold_pct),
        "bandwidth_threshold_pct": float(bandwidth_threshold_pct),
        "routing_cost_threshold": int(routing_cost_threshold),
        "ignored_fields": tuple(
            str(field_name)
            for field_name in (
                ignored_fields or DEFAULT_RUNTIME_PARAMETER_MUTATION_IGNORED_FIELDS
            )
            if str(field_name)
        ),
    }


def _runtime_parameters_from_slot_record(slot_record: Dict[str, Any]) -> Dict[str, Any]:
    explicit_parameters = slot_record.get("runtime_parameters")
    if isinstance(explicit_parameters, dict):
        return dict(explicit_parameters)
    return {
        str(key): value
        for key, value in slot_record.items()
        if key not in _RUNTIME_PARAMETER_EXCLUDED_FIELDS
    }


def _normalized_runtime_parameters(
    runtime_parameters: Mapping[str, Any],
    *,
    ignored_fields: Sequence[str],
) -> Dict[str, Any]:
    ignored = {str(field_name) for field_name in ignored_fields if str(field_name)}
    return {
        str(key): value
        for key, value in dict(runtime_parameters).items()
        if str(key) not in ignored
    }


def _parameter_delta(
    *,
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
) -> Dict[str, Any]:
    previous_delay = float(previous.get("delay_ms", 0.0))
    current_delay = float(current.get("delay_ms", 0.0))
    previous_loss = float(previous.get("loss_percent", 0.0))
    current_loss = float(current.get("loss_percent", 0.0))
    previous_bandwidth = float(previous.get("bandwidth_mbps", 0.0))
    current_bandwidth = float(current.get("bandwidth_mbps", 0.0))
    bandwidth_delta_pct = 0.0
    if previous_bandwidth > 0.0:
        bandwidth_delta_pct = (
            abs(current_bandwidth - previous_bandwidth) / previous_bandwidth * 100.0
        )
    elif previous_bandwidth != current_bandwidth:
        bandwidth_delta_pct = 100.0
    return {
        "delay_delta_ms": abs(current_delay - previous_delay),
        "loss_delta_pct": abs(current_loss - previous_loss),
        "bandwidth_delta_pct": bandwidth_delta_pct,
        "routing_cost_delta": abs(
            int(current.get("routing_cost", 0)) - int(previous.get("routing_cost", 0))
        ),
    }


def _runtime_parameters_require_mutation(
    *,
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
    mutation_policy: Mapping[str, Any] | None,
) -> bool:
    ignored_fields = tuple(
        str(field_name)
        for field_name in (
            (mutation_policy or {}).get(
                "ignored_fields",
                DEFAULT_RUNTIME_PARAMETER_MUTATION_IGNORED_FIELDS,
            )
        )
        if str(field_name)
    )
    previous_normalized = _normalized_runtime_parameters(
        previous,
        ignored_fields=ignored_fields,
    )
    current_normalized = _normalized_runtime_parameters(
        current,
        ignored_fields=ignored_fields,
    )
    if previous_normalized == current_normalized:
        return False
    delta = _parameter_delta(previous=previous_normalized, current=current_normalized)
    if delta["delay_delta_ms"] >= float((mutation_policy or {}).get("delay_threshold_ms", 0.0)):
        return True
    if delta["loss_delta_pct"] >= float((mutation_policy or {}).get("loss_threshold_pct", 0.0)):
        return True
    if (
        delta["bandwidth_delta_pct"]
        >= float((mutation_policy or {}).get("bandwidth_threshold_pct", 0.0))
    ):
        return True
    if (
        int(delta["routing_cost_delta"])
        >= int((mutation_policy or {}).get("routing_cost_threshold", 0))
    ):
        return True
    threshold_keys = {
        "delay_ms",
        "loss_percent",
        "bandwidth_mbps",
        "routing_cost",
    }
    for key in sorted(set(previous_normalized) | set(current_normalized)):
        if key in threshold_keys:
            continue
        if previous_normalized.get(key) != current_normalized.get(key):
            return True
    return False


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
            "runtime_parameters": _runtime_parameters_from_slot_record(dict(slot)),
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
            "runtime_parameters": _runtime_parameters_from_slot_record(dict(link)),
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
    runtime_parameter_mutation_policy: Mapping[str, Any] | None = None,
) -> Dict[str, Any] | None:
    source_binding = dict(source_slot.get("binding", {})) if source_slot else {}
    target_binding = dict(target_slot.get("binding", {})) if target_slot else {}
    source_occupied = bool(source_slot.get("occupied", False)) if source_slot else False
    target_occupied = bool(target_slot.get("occupied", False)) if target_slot else False
    source_endpoints = [dict(endpoint) for endpoint in (source_slot or {}).get("endpoints", [])]
    target_endpoints = [dict(endpoint) for endpoint in (target_slot or {}).get("endpoints", [])]
    source_runtime_parameters = dict((source_slot or {}).get("runtime_parameters", {}) or {})
    target_runtime_parameters = dict((target_slot or {}).get("runtime_parameters", {}) or {})
    slot_type = str(
        (target_slot or source_slot or {}).get("slot_type", "unknown_dynamic_slot")
    )

    parameter_requires_mutation = _runtime_parameters_require_mutation(
        previous=source_runtime_parameters,
        current=target_runtime_parameters,
        mutation_policy=runtime_parameter_mutation_policy,
    )

    if (
        source_occupied == target_occupied
        and source_binding == target_binding
        and not parameter_requires_mutation
    ):
        return None
    if (
        source_occupied == target_occupied
        and source_binding == target_binding
        and parameter_requires_mutation
    ):
        operation = "update_slot_parameters"
    elif source_occupied and target_occupied:
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
        "source_endpoints": source_endpoints,
        "target_endpoints": target_endpoints,
        "source_runtime_parameters": source_runtime_parameters,
        "target_runtime_parameters": target_runtime_parameters,
        "delta_hints": {
            "endpoint_unchanged": source_binding == target_binding,
            "parameter_change_only": (
                source_occupied == target_occupied
                and source_binding == target_binding
                and source_runtime_parameters != target_runtime_parameters
            ),
            "reuses_slot_runtime": True,
        },
    }


def build_network_mutation_plan(
    *,
    source_state: Dict[str, Dict[str, Any]],
    source_state_id: str,
    target_snapshot: Any,
    transition_name: str,
    runtime_parameter_mutation_policy: Mapping[str, Any] | None = None,
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
            runtime_parameter_mutation_policy=runtime_parameter_mutation_policy,
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
