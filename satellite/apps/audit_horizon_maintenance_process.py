#!/usr/bin/env python3
# encoding: utf-8

"""Offline audit for process-owned horizon maintenance artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


FORBIDDEN_TRUE_FIELDS = (
    "active_generation_flip_allowed",
    "runtime_full_reload_allowed",
    "full_attach_fallback_allowed",
    "future_host_link_activation_allowed",
    "future_host_link_state_apply_allowed",
    "preapply_qdisc_parameter_updates_allowed",
    "runtime_recompute_network_mutation_plan_allowed",
    "full_prepare_allowed",
)

MAX_INLINE_WORK_AUDIT_BYTES = 5 * 1024 * 1024


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size == 0:
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return dict(value) if isinstance(value, dict) else {}


def _read_json_if_small(path: Path, *, max_bytes: int) -> tuple[dict[str, Any], bool, int]:
    if not path.exists() or path.stat().st_size == 0:
        return {}, False, 0
    size = int(path.stat().st_size)
    if size > max_bytes:
        return {}, True, size
    return _read_json(path), False, size


def _find_true_fields(value: Any, *, path: str = "") -> list[str]:
    findings: list[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            next_path = f"{path}.{key}" if path else str(key)
            if key in FORBIDDEN_TRUE_FIELDS and nested is True:
                findings.append(next_path)
            findings.extend(_find_true_fields(nested, path=next_path))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            findings.extend(_find_true_fields(nested, path=f"{path}[{index}]"))
    return findings


def _resident_attach_budget_present(work: dict[str, Any]) -> bool:
    budget = dict(
        work.get("resident_attach_coverage_budget")
        or work.get("bpf_attach_budget")
        or {}
    )
    return int(
        budget.get("max_attach_interfaces")
        or budget.get("max_resident_attach_interfaces")
        or 0
    ) > 0


def _resident_attach_budget_max(work: dict[str, Any]) -> int:
    budget = dict(
        work.get("resident_attach_coverage_budget")
        or work.get("bpf_attach_budget")
        or {}
    )
    return int(
        budget.get("max_attach_interfaces")
        or budget.get("max_resident_attach_interfaces")
        or 0
    )


def _attach_report_budget(terminal_artifact: dict[str, Any]) -> dict[str, Any]:
    attach_report = dict(
        terminal_artifact.get("bpf_attach_capability_report")
        or terminal_artifact.get("attach_capability_prepare")
        or {}
    )
    prepare_steps = dict(attach_report.get("prepare_steps") or {})
    nested_prepare = dict(prepare_steps.get("preactivation_attach_prepare") or {})
    return dict(
        attach_report.get("attach_cardinality_budget")
        or nested_prepare.get("attach_cardinality_budget")
        or {}
    )


def audit_horizon_maintenance_process(run_dir: Path) -> dict[str, Any]:
    work_paths = sorted(run_dir.glob("replenished_segments/segment_*/horizon_maintenance_process_work.json"))
    rows: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    for work_path in work_paths:
        segment_dir = work_path.parent
        queued = _read_json(segment_dir / "horizon_maintenance_queued.json")
        summary = _read_json(segment_dir / "horizon_maintenance_process_summary.json")
        ready = _read_json(segment_dir / "horizon_maintenance_ready.json")
        failure = _read_json(segment_dir / "horizon_maintenance_failure.json")
        work, work_too_large, work_bytes = _read_json_if_small(
            work_path,
            max_bytes=MAX_INLINE_WORK_AUDIT_BYTES,
        )
        horizon_id = str(work.get("horizon_id") or queued.get("horizon_id") or "")
        forbidden_paths = _find_true_fields(work) + _find_true_fields(ready)
        status = str(summary.get("status") or ready.get("status") or failure.get("status") or "missing")
        ready_present = bool(ready)
        failure_present = bool(failure)
        summary_failed = bool(str(summary.get("status") or "") == "failed")
        compact_manifest = bool(
            work.get("process_input_compact_manifest")
            or queued.get("process_worker_input_compact_manifest")
        )
        resident_attach_budget_present = _resident_attach_budget_present(work)
        resident_attach_budget_max = _resident_attach_budget_max(work)
        terminal_artifact = ready if ready_present else failure
        observed_attach_budget = _attach_report_budget(terminal_artifact)
        observed_attach_budget_source = str(
            observed_attach_budget.get("budget_source") or ""
        )
        observed_attach_budget_max = int(
            observed_attach_budget.get("max_attach_interfaces")
            or observed_attach_budget.get("max_resident_attach_interfaces")
            or 0
        )
        expected_preload_count = int(
            dict(queued.get("request") or {}).get("expected_preload_count")
            or queued.get("expected_preload_count")
            or 0
        )
        inactive_count = int(ready.get("inactive_preload_record_count") or 0)
        row = {
            "segment_dir": str(segment_dir),
            "horizon_id": horizon_id,
            "queued_present": bool(queued),
            "process_work_present": work_path.exists(),
            "process_work_bytes": work_bytes,
            "process_work_too_large_for_inline_audit": work_too_large,
            "process_input_compact_manifest": compact_manifest,
            "process_summary_present": bool(summary),
            "ready_present": ready_present,
            "failure_present": failure_present,
            "status": status,
            "expected_preload_count": expected_preload_count,
            "inactive_preload_record_count": inactive_count,
            "process_boundary": bool(ready.get("process_boundary")),
            "worker_mode": str(ready.get("worker_mode") or queued.get("worker_mode") or ""),
            "forbidden_true_fields": forbidden_paths,
            "resident_attach_budget_present": resident_attach_budget_present,
            "resident_attach_budget_max": resident_attach_budget_max,
            "observed_attach_budget_source": observed_attach_budget_source,
            "observed_attach_budget_max": observed_attach_budget_max,
        }
        rows.append(row)
        if forbidden_paths:
            blockers.append(
                {
                    "horizon_id": horizon_id,
                    "reason": "forbidden_true_fields",
                    "fields": forbidden_paths,
                }
            )
        if failure_present:
            blockers.append(
                {
                    "horizon_id": horizon_id,
                    "reason": "failure_artifact_present",
                    "artifact": str(segment_dir / "horizon_maintenance_failure.json"),
                }
            )
        if summary_failed:
            blockers.append(
                {
                    "horizon_id": horizon_id,
                    "reason": "process_worker_summary_failed",
                    "summary_path": str(
                        segment_dir / "horizon_maintenance_process_summary.json"
                    ),
                    "summary_reason": str(summary.get("reason") or ""),
                }
            )
            if not failure_present:
                blockers.append(
                    {
                        "horizon_id": horizon_id,
                        "reason": "process_worker_failed_without_failure_artifact",
                        "segment_dir": str(segment_dir),
                    }
                )
        if work_too_large and not compact_manifest:
            blockers.append(
                {
                    "horizon_id": horizon_id,
                    "reason": "process_work_artifact_too_large_not_compact_manifest",
                    "bytes": work_bytes,
                    "max_inline_audit_bytes": MAX_INLINE_WORK_AUDIT_BYTES,
                }
            )
        if (
            str(queued.get("worker_mode") or "") == "process"
            and compact_manifest
            and expected_preload_count
            and not resident_attach_budget_present
        ):
            blockers.append(
                {
                    "horizon_id": horizon_id,
                    "reason": "process_worker_compact_manifest_missing_resident_attach_budget",
                    "work_path": str(work_path),
                }
            )
        if (
            str(queued.get("worker_mode") or "") == "process"
            and resident_attach_budget_max > 0
            and terminal_artifact
            and (
                observed_attach_budget_source == "default"
                or observed_attach_budget_max < resident_attach_budget_max
            )
        ):
            blockers.append(
                {
                    "horizon_id": horizon_id,
                    "reason": "process_worker_attach_prepare_resident_budget_not_consumed",
                    "work_path": str(work_path),
                    "observed_attach_budget_source": observed_attach_budget_source,
                    "observed_attach_budget_max": observed_attach_budget_max,
                    "resident_attach_budget_max": resident_attach_budget_max,
                }
            )
        if expected_preload_count and ready_present and inactive_count < expected_preload_count:
            blockers.append(
                {
                    "horizon_id": horizon_id,
                    "reason": "ready_inactive_preload_count_short",
                    "expected": expected_preload_count,
                    "actual": inactive_count,
                }
            )
        if (
            queued.get("worker_mode") == "process"
            and not ready_present
            and not failure_present
            and not summary_failed
        ):
            blockers.append(
                {
                    "horizon_id": horizon_id,
                    "reason": "process_worker_no_terminal_artifact",
                    "segment_dir": str(segment_dir),
                }
            )
    return {
        "schema_version": "skyseed-horizon-maintenance-process-audit-v1",
        "run_dir": str(run_dir),
        "process_horizon_count": len(rows),
        "ok": not blockers,
        "blocker_count": len(blockers),
        "blockers": blockers,
        "rows": rows,
        "audit_semantics": "offline_only_not_runtime_gate",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = audit_horizon_maintenance_process(args.run_dir)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
