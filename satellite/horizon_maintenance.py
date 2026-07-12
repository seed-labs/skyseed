"""Horizon maintenance readiness contract helpers.

This module owns the contract between rolling-horizon maintenance producers and
the runtime epoch boundary consumer. It deliberately does not activate a
generation or execute host/BPF work; callers provide the stage reports and this
module decides whether those reports may be published as a ready artifact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any, Dict, List, Mapping, Sequence


READY_ARTIFACT_NAME = "horizon_maintenance_ready.json"
FAILURE_ARTIFACT_NAME = "horizon_maintenance_failure.json"

FORBIDDEN_COUNTER_FIELDS = (
    "future_host_link_activation_count",
    "future_host_link_active_count",
    "runtime_full_generation_reload_count",
    "full_generation_reload_count",
    "full_attach_count",
    "full_attach_fallback_count",
    "active_generation_flip_count",
    "active_generation_set_count",
    "live_full_map_dump_count",
    "live_full_map_diff_count",
    "target_generation_cleanup_scan_count",
    "generation_gc_scan_count",
)

FORBIDDEN_BOOLEAN_FIELDS = (
    "future_host_link_state_applied",
    "future_host_link_active",
    "runtime_full_generation_reload_used",
    "full_attach_fallback_used",
    "active_generation_flip_used",
    "active_generation_set",
    "live_full_map_dump_used",
    "live_full_map_diff_used",
    "target_generation_cleanup_scan_used",
    "generation_gc_scan_used",
)


def _json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def canonical_json_hash(payload: Mapping[str, Any] | Sequence[Any]) -> str:
    encoded = json.dumps(
        _json_safe(payload),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_json_safe(payload), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _compact_compiled_runtime_inputs(compiled_inputs: Mapping[str, Any]) -> Dict[str, Any]:
    source = dict(compiled_inputs or {})
    map_counts: Dict[str, int] = {}
    for key in (
        "ingress_classifier_map",
        "ut_gateway_policy_map",
        "gateway_selector_ingress_map",
        "core_lfib_map",
        "egress_handoff_map",
        "zone_edge_delivery_map",
        "generation_base_map",
        "interface_role_map",
    ):
        value = source.get(key)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            map_counts[key] = len(list(value))
        elif isinstance(value, Mapping):
            entries = value.get("entries")
            if isinstance(entries, Sequence) and not isinstance(
                entries, (str, bytes, bytearray)
            ):
                map_counts[key] = len(list(entries))
            elif value:
                map_counts[key] = 1
        elif value is not None:
            map_counts[key] = 1
    active_generation = dict(source.get("active_generation") or {})
    return {
        "kind": "compiled_runtime_inputs_summary",
        "schema_version": str(source.get("schema_version") or ""),
        "active_generation": active_generation,
        "map_counts": map_counts,
        "hash_elided": True,
        "hash_elision_reason": "maintenance_result_artifact_size_guard",
    }


def _compact_preload_report_for_artifact(report: Mapping[str, Any]) -> Dict[str, Any]:
    source = dict(report or {})
    compact = {
        key: _json_safe(value)
        for key, value in source.items()
        if key
        not in {
            "compiled_runtime_inputs",
            "active_generation_compiled_runtime_inputs",
            "prepare_steps",
            "resolved_mpls_flow_entries",
            "g_to_z_entries",
            "z_to_g_entries",
            "packet_route_entries",
        }
    }
    if "compiled_runtime_inputs" in source:
        compact["compiled_runtime_inputs_summary"] = _compact_compiled_runtime_inputs(
            dict(source.get("compiled_runtime_inputs") or {})
        )
    if "active_generation_compiled_runtime_inputs" in source:
        compact["active_generation_compiled_runtime_inputs_summary"] = (
            _compact_compiled_runtime_inputs(
                dict(source.get("active_generation_compiled_runtime_inputs") or {})
            )
        )
    steps = dict(source.get("prepare_steps") or {})
    real_prepare = dict(steps.get("real_backend_prepare") or {})
    if real_prepare:
        compact["prepare_steps_summary"] = {
            "real_backend_prepare": {
                key: _json_safe(real_prepare.get(key))
                for key in (
                    "ok",
                    "reason",
                    "prepare_mode",
                    "preactivation_preload_only",
                    "inactive_generation_written",
                    "active_generation_flip_skipped",
                    "runtime_apply_skipped",
                    "delta_attach_count",
                    "full_attach_count",
                    "runtime_full_generation_reload_count",
                    "batch_map_write_used",
                    "batch_map_write_update_count",
                    "actual_data_write_count",
                    "preactivation_preload_attach_capability_contract",
                    "frozen_compiled_runtime_inputs_check",
                    "compiled_runtime_inputs_source",
                    "reused_attach_clsact_guard_metrics",
                    "timing_breakdown",
                )
                if key in real_prepare
            }
        }
    return compact


def _compact_maintenance_record_for_artifact(record: Mapping[str, Any]) -> Dict[str, Any]:
    source = dict(record or {})
    compact = {
        key: _json_safe(value)
        for key, value in source.items()
        if key
        not in {
            "compiled_runtime_inputs",
            "active_generation_compiled_runtime_inputs",
            "preload_report",
            "prepare_steps",
            "g_to_z_entries",
            "z_to_g_entries",
            "packet_route_entries",
        }
    }
    if "compiled_runtime_inputs" in source:
        compact["compiled_runtime_inputs_summary"] = _compact_compiled_runtime_inputs(
            dict(source.get("compiled_runtime_inputs") or {})
        )
    if "active_generation_compiled_runtime_inputs" in source:
        compact["active_generation_compiled_runtime_inputs_summary"] = (
            _compact_compiled_runtime_inputs(
                dict(source.get("active_generation_compiled_runtime_inputs") or {})
            )
        )
    if "preload_report" in source:
        compact["preload_report_summary"] = _compact_preload_report_for_artifact(
            dict(source.get("preload_report") or {})
        )
    if "prepare_steps" in source:
        compact["prepare_steps_externalized"] = True
    for field_name in ("g_to_z_entries", "z_to_g_entries", "packet_route_entries"):
        if field_name in source:
            compact[f"{field_name}_count"] = len(list(source.get(field_name) or []))
    return compact


def compiled_runtime_inputs_manifest_ready(compiled_inputs: Mapping[str, Any]) -> bool:
    source = dict(compiled_inputs or {})
    if not source:
        return False
    if "compiled_runtime_inputs_summary" in source:
        return False
    if source.get("schema_version") != "skyseed-bpf-owned-compiled-runtime-inputs-v2":
        return False
    return all(
        key in source
        for key in (
            "active_generation",
            "ingress_classifier_map",
            "ut_gateway_policy_map",
            "gateway_selector_ingress_map",
            "core_lfib_map",
            "egress_handoff_map",
            "zone_edge_delivery_map",
        )
    )


def base_compiled_inputs_ready(base_generation_record: Mapping[str, Any]) -> Dict[str, Any]:
    record = dict(base_generation_record or {})
    compiled_inputs = dict(record.get("compiled_runtime_inputs") or {})
    if record.get("ok") is not True:
        return {
            "ready": False,
            "reason": str(record.get("reason") or "base_generation_record_not_ok"),
            "compact_summary_rejected": False,
        }
    compact_summary_rejected = "compiled_runtime_inputs_summary" in compiled_inputs
    if not compiled_runtime_inputs_manifest_ready(compiled_inputs):
        return {
            "ready": False,
            "reason": "base_compiled_runtime_inputs_manifest_missing",
            "compact_summary_rejected": compact_summary_rejected,
            "schema_version": str(compiled_inputs.get("schema_version") or ""),
        }
    return {
        "ready": True,
        "reason": "",
        "compact_summary_rejected": False,
        "schema_version": str(compiled_inputs.get("schema_version") or ""),
        "generation_id": int(
            record.get("physical_generation_id")
            or record.get("generation_id")
            or dict(compiled_inputs.get("active_generation") or {}).get("value")
            or 0
        ),
    }


def retired_cleanup_candidate_ready(candidate: Mapping[str, Any]) -> Dict[str, Any]:
    record = dict(candidate or {})
    generation_value = record.get("generation_id")
    generation_present = generation_value not in (None, "", "None")
    compiled_inputs = dict(record.get("compiled_runtime_inputs") or {})
    if not generation_present:
        return {
            "ready": False,
            "reason": "retired_cleanup_generation_id_missing",
            "generation_id_present": False,
            "compiled_inputs_present": bool(compiled_inputs),
        }
    try:
        generation_id = int(generation_value)
    except (TypeError, ValueError):
        return {
            "ready": False,
            "reason": "retired_cleanup_generation_id_invalid",
            "generation_id_present": True,
            "compiled_inputs_present": bool(compiled_inputs),
            "generation_id": str(generation_value),
        }
    if generation_id < 0:
        return {
            "ready": False,
            "reason": "retired_cleanup_generation_id_invalid",
            "generation_id_present": True,
            "compiled_inputs_present": bool(compiled_inputs),
            "generation_id": generation_id,
        }
    compact_summary_rejected = "compiled_runtime_inputs_summary" in compiled_inputs
    if not compiled_runtime_inputs_manifest_ready(compiled_inputs):
        return {
            "ready": False,
            "reason": "retired_cleanup_compiled_runtime_inputs_manifest_missing",
            "generation_id_present": True,
            "compiled_inputs_present": bool(compiled_inputs),
            "compact_summary_rejected": compact_summary_rejected,
            "generation_id": generation_id,
            "schema_version": str(compiled_inputs.get("schema_version") or ""),
        }
    return {
        "ready": True,
        "reason": "",
        "generation_id_present": True,
        "compiled_inputs_present": True,
        "compact_summary_rejected": False,
        "generation_id": generation_id,
        "schema_version": str(compiled_inputs.get("schema_version") or ""),
    }


def successor_maintenance_gate(
    *,
    previous_horizon_id: str,
    consumed_horizon_ids: Sequence[str] = (),
    ready_horizon_ids: Sequence[str] = (),
    pending_horizon_ids: Sequence[str] = (),
    failed_horizon_ids: Sequence[str] = (),
) -> Dict[str, Any]:
    previous = str(previous_horizon_id or "")
    consumed = {str(item) for item in list(consumed_horizon_ids or []) if str(item)}
    ready = {str(item) for item in list(ready_horizon_ids or []) if str(item)}
    pending = {str(item) for item in list(pending_horizon_ids or []) if str(item)}
    failed = {str(item) for item in list(failed_horizon_ids or []) if str(item)}
    if not previous:
        return {
            "allowed": True,
            "status": "allowed",
            "reason": "no_previous_horizon_dependency",
            "previous_horizon_id": "",
        }
    if previous in failed:
        return {
            "allowed": False,
            "status": "failed",
            "reason": "previous_horizon_maintenance_failed",
            "previous_horizon_id": previous,
        }
    if previous in consumed:
        return {
            "allowed": True,
            "status": "allowed",
            "reason": "previous_horizon_ready_consumed",
            "previous_horizon_id": previous,
        }
    if previous in ready:
        return {
            "allowed": False,
            "status": "deferred",
            "reason": "previous_horizon_ready_not_consumed",
            "previous_horizon_id": previous,
        }
    if previous in pending:
        return {
            "allowed": False,
            "status": "deferred",
            "reason": "previous_horizon_maintenance_pending",
            "previous_horizon_id": previous,
        }
    return {
        "allowed": False,
        "status": "deferred",
        "reason": "previous_horizon_ready_missing",
        "previous_horizon_id": previous,
    }


@dataclass(frozen=True)
class HorizonMaintenanceRequest:
    segment_index: int
    segment_id: str
    current_horizon_id: str
    next_horizon_id: str
    previous_horizon_id: str = ""
    source_state_id: str = ""
    base_generation_id: int = 0
    prepared_generation_ids: Sequence[int] = field(default_factory=tuple)
    expected_preload_count: int = 0
    worker_mode: str = "process"
    forbidden_work_policy: Mapping[str, Any] = field(default_factory=dict)

    def to_payload(self) -> Dict[str, Any]:
        return {
            "schema_version": "skyseed-horizon-maintenance-request-v1",
            "segment_index": int(self.segment_index),
            "segment_id": str(self.segment_id),
            "current_horizon_id": str(self.current_horizon_id),
            "next_horizon_id": str(self.next_horizon_id),
            "previous_horizon_id": str(self.previous_horizon_id),
            "source_state_id": str(self.source_state_id),
            "base_generation_id": int(self.base_generation_id or 0),
            "prepared_generation_ids": [
                int(item) for item in list(self.prepared_generation_ids or [])
            ],
            "expected_preload_count": int(self.expected_preload_count or 0),
            "worker_mode": str(self.worker_mode),
            "forbidden_work_policy": dict(self.forbidden_work_policy or {}),
        }

    @property
    def request_hash(self) -> str:
        return canonical_json_hash(self.to_payload())


def forbidden_work_audit(*reports: Mapping[str, Any]) -> Dict[str, Any]:
    counters: Dict[str, int] = {field_name: 0 for field_name in FORBIDDEN_COUNTER_FIELDS}
    flags: Dict[str, bool] = {field_name: False for field_name in FORBIDDEN_BOOLEAN_FIELDS}
    violations: List[Dict[str, Any]] = []
    for index, report_any in enumerate(reports):
        report = dict(report_any or {})
        source = str(
            report.get("maintenance_stage")
            or report.get("schema_version")
            or f"report_{index}"
        )
        for field_name in FORBIDDEN_COUNTER_FIELDS:
            value = int(report.get(field_name) or 0)
            counters[field_name] += value
            if value:
                violations.append(
                    {
                        "source": source,
                        "field": field_name,
                        "value": value,
                    }
                )
        for field_name in FORBIDDEN_BOOLEAN_FIELDS:
            value = bool(report.get(field_name))
            flags[field_name] = flags[field_name] or value
            if value:
                violations.append(
                    {
                        "source": source,
                        "field": field_name,
                        "value": True,
                    }
                )
        if report.get("future_map_write_allowed") is True:
            violations.append(
                {
                    "source": source,
                    "field": "future_map_write_allowed",
                    "value": True,
                }
            )
        if report.get("active_generation_flip_allowed") is True:
            violations.append(
                {
                    "source": source,
                    "field": "active_generation_flip_allowed",
                    "value": True,
                }
            )
    return {
        "schema_version": "skyseed-horizon-maintenance-forbidden-work-audit-v1",
        "passed": not violations,
        "violation_count": len(violations),
        "violations": violations,
        "counters": counters,
        "flags": flags,
    }


def retired_cleanup_dependency_report(
    *,
    previous_horizon_id: str,
    queued_count: int = 0,
    completed_count: int = 0,
    blocked_count: int = 0,
    blockers: Sequence[Mapping[str, Any]] | None = None,
    status: str | None = None,
) -> Dict[str, Any]:
    cleanup_status = str(status or "")
    if not cleanup_status:
        cleanup_status = "not_required" if not str(previous_horizon_id or "") else "unknown"
    return {
        "schema_version": "skyseed-horizon-maintenance-retired-cleanup-dependency-v1",
        "previous_horizon_id": str(previous_horizon_id or ""),
        "status": cleanup_status,
        "required": bool(previous_horizon_id),
        "queued_count": int(queued_count or 0),
        "completed_count": int(completed_count or 0),
        "blocked_count": int(blocked_count or 0),
        "blockers": [dict(item) for item in list(blockers or [])],
        "active_generation_delete_allowed": False,
        "root_base_generation_delete_allowed": False,
        "target_generation_cleanup_scan_count": 0,
        "generation_gc_scan_count": 0,
    }


def classify_horizon_maintenance(
    *,
    request: HorizonMaintenanceRequest,
    host_additive_prepare: Mapping[str, Any],
    attach_capability_prepare: Mapping[str, Any],
    base_generation_record: Mapping[str, Any],
    queued_preload_records: Sequence[Mapping[str, Any]] | None = None,
    inactive_preload_records: Sequence[Mapping[str, Any]] | None = None,
    retired_cleanup_report: Mapping[str, Any] | None = None,
    wall_seconds: float = 0.0,
) -> Dict[str, Any]:
    host_report = dict(host_additive_prepare or {})
    attach_report = dict(attach_capability_prepare or {})
    queued_records = [dict(item) for item in list(queued_preload_records or [])]
    inactive_records = [dict(item) for item in list(inactive_preload_records or [])]
    cleanup_report = dict(retired_cleanup_report or {})
    base_ready = base_compiled_inputs_ready(base_generation_record)
    forbidden_audit = forbidden_work_audit(
        host_report,
        attach_report,
        *queued_records,
        *inactive_records,
        cleanup_report,
    )
    blockers: List[Dict[str, Any]] = []
    if host_report:
        if (
            host_report.get("ok") is not True
            or host_report.get("additive_prepare_completed") is not True
        ):
            blockers.append(
                {
                    "stage": "host_additive_prepare",
                    "reason": str(
                        host_report.get("reason")
                        or host_report.get("error")
                        or "host_additive_prepare_not_complete"
                    ),
                }
            )
    elif int(request.expected_preload_count or 0) > 0:
        blockers.append(
            {
                "stage": "host_additive_prepare",
                "reason": "host_additive_prepare_report_missing",
            }
        )
    if attach_report:
        if attach_report.get("ok") is not True:
            blockers.append(
                {
                    "stage": "bpf_attach_capability_prepare",
                    "reason": str(
                        attach_report.get("reason")
                        or attach_report.get("blocked_stage")
                        or "attach_capability_prepare_not_ok"
                    ),
                }
            )
    elif int(request.expected_preload_count or 0) > 0:
        blockers.append(
            {
                "stage": "bpf_attach_capability_prepare",
                "reason": "bpf_attach_capability_prepare_report_missing",
            }
        )
    if base_ready.get("ready") is not True:
        blockers.append(
            {
                "stage": "base_compiled_runtime_inputs",
                "reason": str(base_ready.get("reason") or "not_ready"),
                "compact_summary_rejected": bool(
                    base_ready.get("compact_summary_rejected")
                ),
            }
        )
    expected_preload_count = int(request.expected_preload_count or 0)
    preload_pending = False
    if expected_preload_count and len(inactive_records) < expected_preload_count:
        preload_pending = bool(queued_records) and not inactive_records
        blockers.append(
            {
                "stage": "inactive_generation_preload",
                "reason": (
                    "preload_records_queued_not_complete"
                    if preload_pending
                    else "preload_records_missing"
                ),
                "expected_preload_count": expected_preload_count,
                "queued_preload_record_count": len(queued_records),
                "inactive_preload_record_count": len(inactive_records),
            }
        )
    failed_preloads = [
        record
        for record in [*queued_records, *inactive_records]
        if record.get("ok") is False
    ]
    if failed_preloads:
        blockers.append(
            {
                "stage": "inactive_generation_preload",
                "reason": "preload_record_failed",
                "failed_count": len(failed_preloads),
            }
        )
    incomplete_preloads = [
        record
        for record in inactive_records
        if record.get("inactive_generation_written") is not True
    ]
    if incomplete_preloads:
        blockers.append(
            {
                "stage": "inactive_generation_preload",
                "reason": "preload_record_not_written",
                "incomplete_count": len(incomplete_preloads),
            }
        )
    if forbidden_audit.get("passed") is not True:
        blockers.append(
            {
                "stage": "forbidden_work_audit",
                "reason": "forbidden_work_observed",
                "violation_count": int(forbidden_audit.get("violation_count") or 0),
            }
        )
    cleanup_status = str(cleanup_report.get("status") or "")
    cleanup_required = bool(cleanup_report.get("required")) or bool(
        request.previous_horizon_id
    )
    cleanup_ok_statuses = {"completed", "not_required", "safely_retained"}
    if cleanup_required and cleanup_status not in cleanup_ok_statuses:
        blockers.append(
            {
                "stage": "retired_generation_cleanup",
                "reason": "previous_horizon_cleanup_not_complete",
                "previous_horizon_id": str(request.previous_horizon_id),
                "status": cleanup_status or "missing",
                "queued_count": int(cleanup_report.get("queued_count") or 0),
                "completed_count": int(cleanup_report.get("completed_count") or 0),
                "blocked_count": int(cleanup_report.get("blocked_count") or 0),
            }
        )
    status = "ready"
    if blockers:
        only_pending_preload = (
            len(blockers) == 1
            and blockers[0].get("stage") == "inactive_generation_preload"
            and blockers[0].get("reason") == "preload_records_queued_not_complete"
        )
        status = "blocked" if preload_pending and only_pending_preload else "failed"
    request_payload = request.to_payload()
    artifact_host_report = _compact_maintenance_record_for_artifact(host_report)
    artifact_attach_report = _compact_maintenance_record_for_artifact(attach_report)
    artifact_queued_records = [
        _compact_maintenance_record_for_artifact(record) for record in queued_records
    ]
    artifact_inactive_records = [
        _compact_maintenance_record_for_artifact(record) for record in inactive_records
    ]
    worker_wall_seconds = round(float(wall_seconds or 0.0), 6)
    inactive_preload_wall_seconds = 0.0
    for record in inactive_records:
        if record.get("wall_seconds") not in (None, "", "None"):
            inactive_preload_wall_seconds += float(record.get("wall_seconds") or 0.0)
            continue
        preload_report = dict(record.get("preload_report") or {})
        timing = dict(preload_report.get("timing_breakdown") or {})
        inactive_preload_wall_seconds += float(timing.get("total_wall_seconds") or 0.0)
    subtask_wall_seconds = {
        "host_additive_prepare_wall_seconds": round(
            float(host_report.get("wall_seconds") or 0.0),
            6,
        ),
        "bpf_attach_capability_prepare_wall_seconds": round(
            float(attach_report.get("wall_seconds") or 0.0),
            6,
        ),
        "inactive_preload_total_wall_seconds": round(
            inactive_preload_wall_seconds,
            6,
        ),
    }
    subtask_wall_seconds["summed_subtask_wall_seconds"] = round(
        sum(float(value or 0.0) for value in subtask_wall_seconds.values()),
        6,
    )
    return {
        "schema_version": "skyseed-horizon-maintenance-result-v1",
        "status": status,
        "ready": status == "ready",
        "request": request_payload,
        "request_hash": request.request_hash,
        "segment_index": int(request.segment_index),
        "segment_id": str(request.segment_id),
        "current_horizon_id": str(request.current_horizon_id),
        "next_horizon_id": str(request.next_horizon_id),
        "previous_horizon_id": str(request.previous_horizon_id),
        "source_state_id": str(request.source_state_id),
        "base_generation_id": int(request.base_generation_id or 0),
        "prepared_generation_ids": list(request.prepared_generation_ids or []),
        "expected_preload_count": expected_preload_count,
        "queued_preload_record_count": len(queued_records),
        "inactive_preload_record_count": len(inactive_records),
        "blockers": blockers,
        "blocker_count": len(blockers),
        "host_additive_prepare_report": artifact_host_report,
        "interface_binding_freeze_report": {
            "schema_version": "skyseed-horizon-maintenance-interface-binding-freeze-v1",
            "status": "frozen_with_prepared_host_plan",
            "future_host_link_activation_allowed": False,
        },
        "bpf_attach_capability_report": artifact_attach_report,
        "base_compiled_runtime_inputs_check": base_ready,
        "inactive_preload_records": artifact_inactive_records,
        "queued_preload_records": artifact_queued_records,
        "retired_cleanup_dependency_report": cleanup_report,
        "forbidden_work_audit": forbidden_audit,
        "worker_mode": str(request.worker_mode),
        "active_generation_flip_allowed": False,
        "runtime_full_reload_allowed": False,
        "full_attach_fallback_allowed": False,
        "future_host_link_activation_allowed": False,
        "live_full_map_dump_diff_allowed": False,
        "target_generation_cleanup_scan_allowed": False,
        "generation_gc_scan_allowed": False,
        "wall_seconds": worker_wall_seconds,
        "worker_wall_seconds": worker_wall_seconds,
        "subtask_wall_seconds": subtask_wall_seconds,
        "wall_seconds_scope": (
            "horizon_maintenance_worker_only; subtask reports may include work "
            "completed before the worker publish window"
        ),
    }


def publish_horizon_maintenance_result(
    *,
    result: Mapping[str, Any],
    segment_dir: Path,
) -> Path:
    payload = dict(result or {})
    status = str(payload.get("status") or "")
    artifact_name = READY_ARTIFACT_NAME if status == "ready" else FAILURE_ARTIFACT_NAME
    final_path = segment_dir / artifact_name
    staging_dir = segment_dir / ".horizon_maintenance_staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = staging_dir / f"{artifact_name}.{os.getpid()}.{time.time_ns()}.tmp"
    payload = {
        **payload,
        "artifact_path": str(final_path),
        "atomic_publish": True,
        "staging_path": str(tmp_path),
    }
    payload["artifact_hash"] = canonical_json_hash(
        {key: value for key, value in payload.items() if key != "artifact_hash"}
    )
    _write_json(tmp_path, payload)
    os.replace(tmp_path, final_path)
    return final_path


def load_ready_artifact(
    path: Path,
    *,
    expected_request_hash: str | None = None,
    expected_next_horizon_id: str | None = None,
) -> Dict[str, Any]:
    if path.name != READY_ARTIFACT_NAME:
        raise RuntimeError(f"horizon_maintenance_ready_artifact_name_invalid:{path}")
    if ".horizon_maintenance_staging" in path.parts:
        raise RuntimeError("horizon_maintenance_ready_artifact_staging_not_publish")
    payload = json.loads(path.read_text(encoding="utf-8"))
    artifact_hash = str(payload.get("artifact_hash") or "")
    expected_artifact_hash = canonical_json_hash(
        {key: value for key, value in payload.items() if key != "artifact_hash"}
    )
    if artifact_hash != expected_artifact_hash:
        raise RuntimeError("horizon_maintenance_ready_artifact_hash_mismatch")
    if payload.get("status") != "ready" or payload.get("ready") is not True:
        raise RuntimeError("horizon_maintenance_ready_artifact_not_ready")
    if expected_request_hash and payload.get("request_hash") != expected_request_hash:
        raise RuntimeError("horizon_maintenance_ready_request_hash_mismatch")
    if expected_next_horizon_id and payload.get("next_horizon_id") != expected_next_horizon_id:
        raise RuntimeError("horizon_maintenance_ready_horizon_mismatch")
    if dict(payload.get("forbidden_work_audit") or {}).get("passed") is not True:
        raise RuntimeError("horizon_maintenance_ready_forbidden_work_audit_failed")
    return payload


class MaintenanceLock:
    """Small filesystem lock for mutually exclusive host/tc/BPF maintenance work."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._fd: int | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._fd = os.open(
                self.path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o644,
            )
        except FileExistsError as exc:
            raise RuntimeError(f"horizon_maintenance_lock_busy:{self.path}") from exc
        os.write(
            self._fd,
            json.dumps(
                {
                    "pid": os.getpid(),
                    "created_at_unix": time.time(),
                },
                sort_keys=True,
            ).encode("utf-8"),
        )

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def __enter__(self) -> "MaintenanceLock":
        self.acquire()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.release()
