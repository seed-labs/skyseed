#!/usr/bin/env python3
# encoding: utf-8

"""Offline producer/consumer coverage audits for frozen runtime queues."""

from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple, Type, TypeVar

from skyseed_v1.core.ir.contracts import RuntimeUpdatePlan, TopologySnapshot
from skyseed_v1.backends.linux.mpls import (
    DirectGatewayExperimentContext,
    _bpf_owned_edge_route_key,
    _bpf_owned_gateway_local_return_key,
    _bpf_owned_prepared_route_identity_evidence_payload,
    _capture_bpf_owned_preflight_state,
    _realization_contexts_from_topology,
)


T = TypeVar("T")

QUEUE_COVERAGE_SCHEMA_VERSION = "skyseed-runtime-queue-coverage-v1"
ROUTE_COVERAGE_SCHEMA_VERSION = "skyseed-runtime-queue-route-identity-coverage-v1"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _dataclass_from_mapping(cls: Type[T], payload: Mapping[str, Any]) -> T:
    names = {field.name for field in fields(cls)}
    return cls(**{name: payload.get(name) for name in names})  # type: ignore[arg-type]


def _segment_key_for_entry(entry: Mapping[str, Any], *, segment_id: str) -> str:
    explicit = str(entry.get("segment_epoch_key") or "")
    if explicit:
        return explicit
    slice_id = str(entry.get("slice_id") or "")
    if not slice_id:
        return ""
    return f"{segment_id}:{slice_id}"


def _slice_id_from_segment_key(segment_key: str) -> str:
    if ":" in segment_key:
        return segment_key.split(":", 1)[1]
    return segment_key


def _runtime_queue_entry_records_from_normal_inputs(
    normal_runtime_inputs: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    seen: set[Tuple[str, str]] = set()
    segments = list(normal_runtime_inputs.get("prepared_horizon_segments") or [])
    if not segments:
        initial = dict(normal_runtime_inputs.get("initial_segment") or {})
        if initial:
            segments = [initial]
    for segment in segments:
        segment_dict = dict(segment or {})
        segment_id = str(segment_dict.get("segment_id") or "initial")
        horizon_id = str(segment_dict.get("horizon_id") or segment_id)
        for entry in list(segment_dict.get("current_execution_queue") or []):
            entry_dict = dict(entry or {})
            if str(entry_dict.get("classification") or "") not in {
                "runtime_epoch",
                "parameter_epoch",
            }:
                continue
            if bool(entry_dict.get("runtime_apply_allowed", True)) is not True:
                continue
            segment_key = _segment_key_for_entry(entry_dict, segment_id=segment_id)
            if not segment_key:
                continue
            dedupe_key = (segment_key, str(entry_dict.get("epoch_id") or ""))
            duplicate_queue_identity = dedupe_key in seen
            seen.add(dedupe_key)
            entries.append(
                {
                    "segment_id": segment_id,
                    "horizon_id": str(entry_dict.get("horizon_id") or horizon_id),
                    "segment_epoch_key": segment_key,
                    "slice_id": str(entry_dict.get("slice_id") or ""),
                    "epoch_id": str(entry_dict.get("epoch_id") or ""),
                    "classification": str(entry_dict.get("classification") or ""),
                    "runtime_apply_allowed": bool(
                        entry_dict.get("runtime_apply_allowed", True)
                    ),
                    "duplicate_queue_identity": duplicate_queue_identity,
                    "entry": entry_dict,
                }
            )
    return entries


def _runtime_queue_entries_from_normal_inputs(
    normal_runtime_inputs: Mapping[str, Any],
) -> List[Tuple[str, Mapping[str, Any]]]:
    seen: set[Tuple[str, str]] = set()
    entries: List[Tuple[str, Mapping[str, Any]]] = []
    for record in _runtime_queue_entry_records_from_normal_inputs(normal_runtime_inputs):
        segment_key = str(record.get("segment_epoch_key") or "")
        entry = dict(record.get("entry") or {})
        dedupe_key = (segment_key, str(entry.get("epoch_id") or ""))
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        entries.append((segment_key, entry))
    return entries


def _context_payload_for_segment_key(
    *,
    normal_runtime_inputs: Mapping[str, Any],
    segment_key: str,
) -> Mapping[str, Any]:
    context_by_slice_by_segment = dict(
        normal_runtime_inputs.get("context_by_slice_by_segment") or {}
    )
    segment_id = segment_key.split(":", 1)[0] if ":" in segment_key else "initial"
    slice_id = _slice_id_from_segment_key(segment_key)
    segment_contexts = dict(context_by_slice_by_segment.get(segment_id) or {})
    payload = segment_contexts.get(slice_id)
    if payload is None:
        raise RuntimeError(
            "runtime_queue_coverage_context_missing:"
            + json.dumps(
                {
                    "segment_epoch_key": segment_key,
                    "segment_id": segment_id,
                    "slice_id": slice_id,
                },
                sort_keys=True,
            )
        )
    return dict(payload)


def audit_runtime_queue_integrity(
    normal_runtime_inputs: Mapping[str, Any],
) -> Dict[str, Any]:
    entries = _runtime_queue_entry_records_from_normal_inputs(normal_runtime_inputs)
    context_by_slice_by_segment = dict(
        normal_runtime_inputs.get("context_by_slice_by_segment") or {}
    )
    blockers: List[Dict[str, Any]] = []
    duplicate_keys: List[Tuple[str, str]] = []
    seen_keys: set[Tuple[str, str]] = set()
    missing_contexts: List[Dict[str, Any]] = []
    for record in entries:
        key = (
            str(record.get("segment_epoch_key") or ""),
            str(record.get("epoch_id") or ""),
        )
        if key in seen_keys:
            duplicate_keys.append(key)
        seen_keys.add(key)
        segment_id = str(record.get("segment_id") or "")
        slice_id = str(record.get("slice_id") or "")
        payload = dict(
            dict(context_by_slice_by_segment.get(segment_id) or {}).get(slice_id)
            or {}
        )
        if not payload:
            missing_contexts.append(
                {
                    "segment_epoch_key": str(record.get("segment_epoch_key") or ""),
                    "segment_id": segment_id,
                    "slice_id": slice_id,
                    "epoch_id": str(record.get("epoch_id") or ""),
                }
            )
    if not entries:
        blockers.append(
            {
                "label": "RUNTIME_QUEUE_EMPTY",
                "reason": "no runtime-applicable entries found in frozen inputs",
            }
        )
    if duplicate_keys:
        blockers.append(
            {
                "label": "RUNTIME_QUEUE_DUPLICATE_ENTRY",
                "duplicate_count": len(duplicate_keys),
                "examples": [
                    {"segment_epoch_key": key[0], "epoch_id": key[1]}
                    for key in duplicate_keys[:20]
                ],
            }
        )
    for missing in missing_contexts[:20]:
        blockers.append(
            {
                "label": "RUNTIME_QUEUE_CONTEXT_MISSING",
                **missing,
            }
        )
    ok = not blockers
    return {
        "schema_version": "skyseed-runtime-queue-integrity-v1",
        "ok": ok,
        "passed": ok,
        "runtime_applicable_entry_count": len(entries),
        "unique_runtime_applicable_entry_count": len(seen_keys),
        "duplicate_entry_count": len(duplicate_keys),
        "missing_context_count": len(missing_contexts),
        "blockers": blockers,
    }


def _expected_bpf_physical_generation_id(
    *,
    horizon_sequence_index: int,
    logical_generation_id: int,
    generation_slot_width: int,
) -> int:
    local_generation_id = int(logical_generation_id)
    slot_width = max(1, int(generation_slot_width))
    if local_generation_id < 0 or local_generation_id >= slot_width:
        raise RuntimeError(
            "bpf_rolling_generation_slot_capacity_exceeded:"
            + json.dumps(
                {
                    "horizon_sequence_index": int(horizon_sequence_index),
                    "local_generation_id": local_generation_id,
                    "generation_slot_width": slot_width,
                },
                sort_keys=True,
            )
        )
    return int(horizon_sequence_index % 3) * slot_width + local_generation_id


def audit_bpf_generation_slot_coverage(
    normal_runtime_inputs: Mapping[str, Any],
) -> Dict[str, Any]:
    entries = _runtime_queue_entry_records_from_normal_inputs(normal_runtime_inputs)
    blockers: List[Dict[str, Any]] = []
    records: List[Dict[str, Any]] = []
    widths: set[int] = set()
    per_horizon_physical_ids: Dict[str, set[int]] = {}
    for record in entries:
        segment_key = str(record.get("segment_epoch_key") or "")
        try:
            payload = _context_payload_for_segment_key(
                normal_runtime_inputs=normal_runtime_inputs,
                segment_key=segment_key,
            )
        except RuntimeError:
            continue
        runtime_update_plan = _dataclass_from_mapping(
            RuntimeUpdatePlan,
            dict(payload.get("runtime_update_plan") or {}),
        )
        target = dict(runtime_update_plan.target_snapshot or {})
        planner = dict(runtime_update_plan.planner or {})
        allocator = dict(planner.get("bpf_generation_slot_allocator") or {})
        required_target_fields = (
            "bpf_logical_generation_id",
            "bpf_physical_generation_id",
            "bpf_generation_slot_width",
            "bpf_generation_horizon_sequence_index",
            "bpf_generation_buffer_index",
            "bpf_generation_horizon_id",
        )
        missing_fields = [
            field_name
            for field_name in required_target_fields
            if target.get(field_name) in (None, "", "None")
        ]
        if missing_fields or not allocator:
            blockers.append(
                {
                    "label": "BPF_GENERATION_SLOT_ASSIGNMENT_MISSING",
                    "segment_epoch_key": segment_key,
                    "epoch_id": str(record.get("epoch_id") or ""),
                    "missing_target_fields": missing_fields,
                    "planner_allocator_present": bool(allocator),
                }
            )
            continue
        logical_generation_id = int(target.get("bpf_logical_generation_id") or 0)
        physical_generation_id = int(target.get("bpf_physical_generation_id") or 0)
        slot_width = int(target.get("bpf_generation_slot_width") or 0)
        horizon_sequence_index = int(
            target.get("bpf_generation_horizon_sequence_index") or 0
        )
        buffer_index = int(target.get("bpf_generation_buffer_index") or 0)
        horizon_id = str(target.get("bpf_generation_horizon_id") or "")
        widths.add(slot_width)
        expected_buffer_index = int(horizon_sequence_index % 3)
        try:
            expected_physical_generation_id = _expected_bpf_physical_generation_id(
                horizon_sequence_index=horizon_sequence_index,
                logical_generation_id=logical_generation_id,
                generation_slot_width=slot_width,
            )
        except RuntimeError as exc:
            blockers.append(
                {
                    "label": "BPF_GENERATION_SLOT_CAPACITY_EXCEEDED",
                    "segment_epoch_key": segment_key,
                    "epoch_id": str(record.get("epoch_id") or ""),
                    "reason": str(exc),
                }
            )
            continue
        mismatches: List[str] = []
        if physical_generation_id != expected_physical_generation_id:
            mismatches.append("physical_generation_id")
        if buffer_index != expected_buffer_index:
            mismatches.append("buffer_index")
        allocator_checks = {
            "horizon_id": horizon_id,
            "horizon_sequence_index": horizon_sequence_index,
            "buffer_index": buffer_index,
            "logical_generation_id": logical_generation_id,
            "physical_generation_id": physical_generation_id,
            "generation_slot_width": slot_width,
        }
        for field_name, expected in allocator_checks.items():
            if allocator.get(field_name) != expected:
                mismatches.append(f"allocator.{field_name}")
        horizon_physical_ids = per_horizon_physical_ids.setdefault(horizon_id, set())
        if physical_generation_id in horizon_physical_ids:
            mismatches.append("duplicate_physical_generation_id_within_horizon")
        horizon_physical_ids.add(physical_generation_id)
        item = {
            "segment_epoch_key": segment_key,
            "epoch_id": str(record.get("epoch_id") or ""),
            "horizon_id": horizon_id,
            "horizon_sequence_index": horizon_sequence_index,
            "buffer_index": buffer_index,
            "logical_generation_id": logical_generation_id,
            "physical_generation_id": physical_generation_id,
            "expected_physical_generation_id": expected_physical_generation_id,
            "generation_slot_width": slot_width,
            "mismatches": mismatches,
        }
        records.append(item)
        if mismatches:
            blockers.append(
                {
                    "label": "BPF_GENERATION_SLOT_ASSIGNMENT_MISMATCH",
                    **item,
                }
            )
    if len(widths) > 1:
        blockers.append(
            {
                "label": "BPF_GENERATION_SLOT_WIDTH_MISMATCH",
                "generation_slot_widths": sorted(widths),
            }
        )
    ok = not blockers
    return {
        "schema_version": "skyseed-runtime-queue-bpf-generation-slot-coverage-v1",
        "ok": ok,
        "passed": ok,
        "runtime_applicable_entry_count": len(entries),
        "generation_slot_record_count": len(records),
        "generation_slot_widths": sorted(widths),
        "horizon_count": len(per_horizon_physical_ids),
        "blockers": blockers,
        "records": records[:50],
    }


def audit_prepared_horizon_scope(
    normal_runtime_inputs: Mapping[str, Any],
    *,
    expected_horizon_count: int | None = None,
) -> Dict[str, Any]:
    initial = dict(normal_runtime_inputs.get("initial_segment") or {})
    segments = [
        dict(segment or {})
        for segment in list(normal_runtime_inputs.get("prepared_horizon_segments") or [])
    ]
    if not segments and initial:
        segments = [initial]
    blockers: List[Dict[str, Any]] = []
    actual_horizon_count = len(segments)
    expected = int(expected_horizon_count or 0)
    if expected > 0 and actual_horizon_count != expected:
        blockers.append(
            {
                "label": "PREPARED_HORIZON_SCOPE_COUNT_MISMATCH",
                "expected_horizon_count": expected,
                "actual_horizon_count": actual_horizon_count,
                "reason": (
                    "prepared frozen horizon segment count does not match "
                    "the requested long-run horizon count"
                ),
            }
        )
    segment_records: List[Dict[str, Any]] = []
    seen_segment_ids: set[str] = set()
    duplicate_segment_ids: List[str] = []
    empty_segments: List[str] = []
    for index, segment in enumerate(segments):
        segment_id = str(segment.get("segment_id") or segment.get("horizon_id") or "")
        if not segment_id:
            segment_id = f"<missing:{index}>"
        if segment_id in seen_segment_ids:
            duplicate_segment_ids.append(segment_id)
        seen_segment_ids.add(segment_id)
        queue = [
            dict(entry or {})
            for entry in list(segment.get("current_execution_queue") or [])
            if str(dict(entry or {}).get("classification") or "")
            in {"runtime_epoch", "parameter_epoch"}
            and bool(dict(entry or {}).get("runtime_apply_allowed", True)) is True
        ]
        if not queue:
            empty_segments.append(segment_id)
        segment_records.append(
            {
                "segment_id": segment_id,
                "horizon_id": str(segment.get("horizon_id") or segment_id),
                "horizon_sequence_index": segment.get("horizon_sequence_index"),
                "runtime_applicable_entry_count": len(queue),
            }
        )
    if duplicate_segment_ids:
        blockers.append(
            {
                "label": "PREPARED_HORIZON_SCOPE_DUPLICATE_SEGMENT_ID",
                "duplicate_segment_ids": duplicate_segment_ids[:20],
            }
        )
    for segment_id in empty_segments[:20]:
        blockers.append(
            {
                "label": "PREPARED_HORIZON_SCOPE_EMPTY_SEGMENT",
                "segment_id": segment_id,
            }
        )
    ok = not blockers
    return {
        "schema_version": "skyseed-runtime-queue-prepared-horizon-scope-v1",
        "ok": ok,
        "passed": ok,
        "expected_horizon_count": expected,
        "actual_horizon_count": actual_horizon_count,
        "segment_ids": [record["segment_id"] for record in segment_records],
        "runtime_applicable_entry_count": sum(
            int(record.get("runtime_applicable_entry_count") or 0)
            for record in segment_records
        ),
        "segments": segment_records,
        "blockers": blockers,
    }


def bpf_route_identity_contexts_from_normal_runtime_inputs(
    normal_runtime_inputs: Mapping[str, Any],
    *,
    topology_path: Path | None = None,
) -> List[Dict[str, Any]]:
    topology_path_value = topology_path or Path(
        str(normal_runtime_inputs.get("topology_path") or "")
    )
    if not str(topology_path_value):
        raise RuntimeError("runtime_queue_coverage_topology_path_missing")
    topology = _read_json(topology_path_value)
    contexts: List[Dict[str, Any]] = []
    for order, (segment_key, entry) in enumerate(
        _runtime_queue_entries_from_normal_inputs(normal_runtime_inputs),
        start=1,
    ):
        payload = _context_payload_for_segment_key(
            normal_runtime_inputs=normal_runtime_inputs,
            segment_key=segment_key,
        )
        snapshot = _dataclass_from_mapping(
            TopologySnapshot,
            dict(payload.get("snapshot") or {}),
        )
        runtime_update_plan = _dataclass_from_mapping(
            RuntimeUpdatePlan,
            dict(payload.get("runtime_update_plan") or {}),
        )
        for context in _realization_contexts_from_topology(
            topology=topology,
            topology_snapshot=snapshot,
            runtime_update_plan=runtime_update_plan,
        ):
            contexts.append(
                {
                    "segment_epoch_key": segment_key,
                    "slice_id": str(entry.get("slice_id") or ""),
                    "epoch_id": str(entry.get("epoch_id") or ""),
                    "epoch_order": order,
                    "context": context,
                }
            )
    return contexts


def _route_identity_records_from_context_records(
    context_records: Sequence[Mapping[str, Any]],
    *,
    producer: bool,
) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    seen: set[Tuple[str, Tuple[str, ...]]] = set()
    for record in context_records:
        context = record.get("context")
        if not isinstance(context, DirectGatewayExperimentContext):
            continue
        common = {
            "segment_epoch_key": str(record.get("segment_epoch_key") or ""),
            "slice_id": str(record.get("slice_id") or ""),
            "epoch_id": str(record.get("epoch_id") or ""),
            "flow_id": str(context.flow_id),
            "ut_id": str(context.ut_id),
            "selected_gateway_id": str(context.selected_gateway_id),
        }
        ut_key = tuple(_bpf_owned_edge_route_key(context))
        if ("ut_default_route", ut_key) not in seen:
            seen.add(("ut_default_route", ut_key))
            records.append(
                {
                    **common,
                    "identity_kind": "ut_default_route",
                    "key": list(ut_key),
                    "producer": bool(producer),
                }
            )
        if context.reverse_segments and context.gateway_access_interface:
            gw_key = tuple(_bpf_owned_gateway_local_return_key(context))
            if ("gw_bpf_local_return_route", gw_key) not in seen:
                seen.add(("gw_bpf_local_return_route", gw_key))
                records.append(
                    {
                        **common,
                        "identity_kind": "gw_bpf_local_return_route",
                        "key": list(gw_key),
                        "producer": bool(producer),
                    }
                )
    return records


def planned_bpf_route_identity_evidence_from_context_records(
    context_records: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    contexts: List[DirectGatewayExperimentContext] = []
    preflights: List[Dict[str, Any]] = []
    for record in context_records:
        context = record.get("context")
        if not isinstance(context, DirectGatewayExperimentContext):
            continue
        contexts.append(context)
        preflights.append(
            {
                **_capture_bpf_owned_preflight_state(context),
                "ut_default_route_present": True,
                "ut_default_route_stdout": (
                    f"default via {context.ut_access_peer_ip} "
                    f"dev {context.ut_access_interface} src {context.ut_access_ip}"
                ),
                "gw_bpf_local_return_route_present": True,
                "gw_bpf_local_return_route_stdout": (
                    f"{context.return_target_prefix} via "
                    f"{context.gateway_access_peer_ip} "
                    f"dev {context.gateway_access_interface} "
                    f"src {context.gateway_access_ip}"
                ),
                "preflight_source": "planned_precompose_route_identity_coverage",
            }
        )
    return _bpf_owned_prepared_route_identity_evidence_payload(contexts, preflights)


def _route_identity_records_from_evidence(
    prepared_route_identity_evidence: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for record in list(prepared_route_identity_evidence.get("ut_default_routes") or []):
        record_dict = dict(record or {})
        key = [str(item) for item in list(record_dict.get("key") or [])]
        if len(key) != 4:
            continue
        records.append(
            {
                "identity_kind": "ut_default_route",
                "key": key,
                "ut_id": str(record_dict.get("ut_id") or ""),
                "selected_gateway_id": "",
                "producer": True,
            }
        )
    for record in list(
        prepared_route_identity_evidence.get("gateway_local_return_routes") or []
    ):
        record_dict = dict(record or {})
        key = [str(item) for item in list(record_dict.get("key") or [])]
        if len(key) != 5:
            continue
        records.append(
            {
                "identity_kind": "gw_bpf_local_return_route",
                "key": key,
                "ut_id": "",
                "selected_gateway_id": str(record_dict.get("selected_gateway_id") or ""),
                "producer": True,
            }
        )
    return records


def audit_bpf_route_identity_coverage(
    *,
    consumer_context_records: Sequence[Mapping[str, Any]],
    producer_context_records: Sequence[Mapping[str, Any]] | None = None,
    prepared_route_identity_evidence: Mapping[str, Any] | None = None,
    actual_evidence_required: bool = False,
) -> Dict[str, Any]:
    consumer_records = _route_identity_records_from_context_records(
        consumer_context_records,
        producer=False,
    )
    if prepared_route_identity_evidence:
        producer_records = _route_identity_records_from_evidence(
            prepared_route_identity_evidence
        )
        producer_source = "prepared_route_identity_evidence"
    elif producer_context_records is not None:
        producer_records = _route_identity_records_from_context_records(
            producer_context_records,
            producer=True,
        )
        producer_source = "planned_producer_contexts"
    else:
        producer_records = []
        producer_source = "missing"
    producer_keys = {
        (str(record.get("identity_kind") or ""), tuple(record.get("key") or ()))
        for record in producer_records
    }
    missing = [
        record
        for record in consumer_records
        if (str(record.get("identity_kind") or ""), tuple(record.get("key") or ()))
        not in producer_keys
    ]
    blockers: List[Dict[str, Any]] = []
    if actual_evidence_required and not prepared_route_identity_evidence:
        blockers.append(
            {
                "label": "PREPARED_ROUTE_IDENTITY_EVIDENCE_MISSING",
                "reason": "actual prepared route identity evidence is required",
            }
        )
    for record in missing[:20]:
        blockers.append(
            {
                "label": "ROUTE_IDENTITY_PRODUCER_MISSING",
                "identity_kind": str(record.get("identity_kind") or ""),
                "key": list(record.get("key") or []),
                "segment_epoch_key": str(record.get("segment_epoch_key") or ""),
                "epoch_id": str(record.get("epoch_id") or ""),
                "flow_id": str(record.get("flow_id") or ""),
            }
        )
    ok = not blockers and not missing
    return {
        "schema_version": ROUTE_COVERAGE_SCHEMA_VERSION,
        "ok": ok,
        "passed": ok,
        "producer_source": producer_source,
        "actual_evidence_required": bool(actual_evidence_required),
        "consumer_identity_count": len(consumer_records),
        "producer_identity_count": len(producer_records),
        "missing_identity_count": len(missing),
        "missing_identity_examples": missing[:20],
        "blockers": blockers,
        "summary": {
            "consumer_ut_default_route_count": sum(
                1
                for record in consumer_records
                if record.get("identity_kind") == "ut_default_route"
            ),
            "consumer_gateway_local_return_route_count": sum(
                1
                for record in consumer_records
                if record.get("identity_kind") == "gw_bpf_local_return_route"
            ),
            "producer_ut_default_route_count": sum(
                1
                for record in producer_records
                if record.get("identity_kind") == "ut_default_route"
            ),
            "producer_gateway_local_return_route_count": sum(
                1
                for record in producer_records
                if record.get("identity_kind") == "gw_bpf_local_return_route"
            ),
        },
    }


def audit_runtime_queue_coverage(
    normal_runtime_inputs: Mapping[str, Any],
    *,
    topology_path: Path | None = None,
    prepared_route_identity_evidence: Mapping[str, Any] | None = None,
    actual_evidence_required: bool = False,
    expected_horizon_count: int | None = None,
) -> Dict[str, Any]:
    horizon_scope = audit_prepared_horizon_scope(
        normal_runtime_inputs,
        expected_horizon_count=expected_horizon_count,
    )
    queue_integrity = audit_runtime_queue_integrity(normal_runtime_inputs)
    generation_slot_coverage = audit_bpf_generation_slot_coverage(
        normal_runtime_inputs
    )
    route_context_records: List[Dict[str, Any]] = []
    route_context_error = ""
    try:
        route_context_records = bpf_route_identity_contexts_from_normal_runtime_inputs(
            normal_runtime_inputs,
            topology_path=topology_path,
        )
    except Exception as exc:  # noqa: BLE001 - report contract, not repair path.
        route_context_error = str(exc) or exc.__class__.__name__
    if route_context_error:
        route_identity_coverage = {
            "schema_version": ROUTE_COVERAGE_SCHEMA_VERSION,
            "ok": False,
            "passed": False,
            "producer_source": "missing",
            "actual_evidence_required": bool(actual_evidence_required),
            "consumer_identity_count": 0,
            "producer_identity_count": 0,
            "missing_identity_count": 0,
            "missing_identity_examples": [],
            "blockers": [
                {
                    "label": "ROUTE_IDENTITY_CONTEXT_BUILD_FAILED",
                    "reason": route_context_error,
                }
            ],
            "summary": {},
        }
    else:
        route_identity_coverage = audit_bpf_route_identity_coverage(
            consumer_context_records=route_context_records,
            producer_context_records=(
                None if prepared_route_identity_evidence else route_context_records
            ),
            prepared_route_identity_evidence=prepared_route_identity_evidence,
            actual_evidence_required=actual_evidence_required,
        )
    blockers: List[Dict[str, Any]] = []
    blockers.extend(
        {"gate": "prepared_horizon_scope", **dict(blocker)}
        for blocker in list(horizon_scope.get("blockers") or [])
    )
    blockers.extend(
        {"gate": "runtime_queue_integrity", **dict(blocker)}
        for blocker in list(queue_integrity.get("blockers") or [])
    )
    blockers.extend(
        {"gate": "bpf_generation_slot_coverage", **dict(blocker)}
        for blocker in list(generation_slot_coverage.get("blockers") or [])
    )
    blockers.extend(
        {"gate": "route_identity_coverage", **dict(blocker)}
        for blocker in list(route_identity_coverage.get("blockers") or [])
    )
    ok = (
        bool(horizon_scope.get("ok"))
        and bool(queue_integrity.get("ok"))
        and bool(generation_slot_coverage.get("ok"))
        and bool(route_identity_coverage.get("ok"))
        and not blockers
    )
    report = {
        "schema_version": QUEUE_COVERAGE_SCHEMA_VERSION,
        "ok": ok,
        "passed": ok,
        "blockers": blockers,
        "horizon_scope": horizon_scope,
        "queue_integrity": queue_integrity,
        "bpf_generation_slot_coverage": generation_slot_coverage,
        "route_identity_coverage": route_identity_coverage,
        "context_record_count": len(route_context_records),
    }
    for key in (
        "producer_source",
        "actual_evidence_required",
        "consumer_identity_count",
        "producer_identity_count",
        "missing_identity_count",
        "missing_identity_examples",
        "summary",
    ):
        report[key] = route_identity_coverage.get(key)
    return report


def _prepared_route_identity_evidence_from_prepare_report(
    pre_runtime_bpf_owned_prepare: Mapping[str, Any],
) -> Dict[str, Any]:
    prepare = dict(pre_runtime_bpf_owned_prepare or {})
    edge_prepare = dict(prepare.get("edge_route_baseline_prepare") or {})
    return dict(edge_prepare.get("prepared_route_identity_evidence") or {})


def audit_bpf_route_identity_coverage_from_run_dir(
    run_dir: Path,
    *,
    actual_evidence_required: bool = False,
) -> Dict[str, Any]:
    normal_path = run_dir / "normal_runtime_inputs.json"
    if not normal_path.exists():
        return {
            "schema_version": ROUTE_COVERAGE_SCHEMA_VERSION,
            "ok": False,
            "passed": False,
            "producer_source": "missing",
            "blockers": [
                {
                    "label": "NORMAL_RUNTIME_INPUTS_MISSING",
                    "path": str(normal_path),
                }
            ],
        }
    normal_inputs = dict(_read_json(normal_path))
    consumer_context_records = bpf_route_identity_contexts_from_normal_runtime_inputs(
        normal_inputs,
    )
    pre_runtime_prepare_path = run_dir / "pre_runtime_bpf_owned_prepare.json"
    prepared_evidence: Dict[str, Any] = {}
    if pre_runtime_prepare_path.exists():
        prepared_evidence = _prepared_route_identity_evidence_from_prepare_report(
            dict(_read_json(pre_runtime_prepare_path))
        )
    producer_context_records = (
        None
        if prepared_evidence
        else bpf_route_identity_contexts_from_normal_runtime_inputs(normal_inputs)
    )
    report = audit_bpf_route_identity_coverage(
        consumer_context_records=consumer_context_records,
        producer_context_records=producer_context_records,
        prepared_route_identity_evidence=prepared_evidence,
        actual_evidence_required=actual_evidence_required,
    )
    report["run_dir"] = str(run_dir)
    report["normal_runtime_inputs_json"] = str(normal_path)
    report["pre_runtime_bpf_owned_prepare_json"] = str(pre_runtime_prepare_path)
    return report


def audit_runtime_queue_coverage_from_run_dir(
    run_dir: Path,
    *,
    actual_evidence_required: bool = False,
    expected_horizon_count: int | None = None,
) -> Dict[str, Any]:
    normal_path = run_dir / "normal_runtime_inputs.json"
    if not normal_path.exists():
        return {
            "schema_version": QUEUE_COVERAGE_SCHEMA_VERSION,
            "ok": False,
            "passed": False,
            "blockers": [
                {
                    "label": "NORMAL_RUNTIME_INPUTS_MISSING",
                    "path": str(normal_path),
                }
            ],
        }
    normal_inputs = dict(_read_json(normal_path))
    pre_runtime_prepare_path = run_dir / "pre_runtime_bpf_owned_prepare.json"
    prepared_evidence: Dict[str, Any] = {}
    if pre_runtime_prepare_path.exists():
        prepared_evidence = _prepared_route_identity_evidence_from_prepare_report(
            dict(_read_json(pre_runtime_prepare_path))
        )
    report = audit_runtime_queue_coverage(
        normal_inputs,
        topology_path=run_dir / "topology.json",
        prepared_route_identity_evidence=prepared_evidence,
        actual_evidence_required=actual_evidence_required,
        expected_horizon_count=expected_horizon_count,
    )
    report["run_dir"] = str(run_dir)
    report["normal_runtime_inputs_json"] = str(normal_path)
    report["pre_runtime_bpf_owned_prepare_json"] = str(pre_runtime_prepare_path)
    return report
