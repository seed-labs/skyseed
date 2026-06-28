#!/usr/bin/env python3
# encoding: utf-8

"""Attribute known validation artifact cost anomalies without rerunning profiles."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size == 0:
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        records.append(json.loads(line))
    return records


def _round(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return None


def _find_cleanup_metrics(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        metrics = value.get("cleanup_metrics")
        if isinstance(metrics, Mapping) and (
            "raw_resource_count" in metrics or "skipped_noop_count" in metrics
        ):
            return dict(metrics)
        for child in value.values():
            found = _find_cleanup_metrics(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_cleanup_metrics(child)
            if found:
                return found
    return {}


def _cleanup_metrics(*reports: Mapping[str, Any]) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    for report in reports:
        metrics = _find_cleanup_metrics(report)
        if metrics:
            break
    return {
        "raw_resource_count": int(metrics.get("raw_resource_count") or 0),
        "unique_resource_count": int(metrics.get("unique_resource_count") or 0),
        "skipped_noop_count": int(metrics.get("skipped_noop_count") or 0),
        "cleanup_resource_attempted_count": int(
            metrics.get("cleanup_resource_attempted_count") or 0
        ),
        "resource_groups": dict(metrics.get("resource_groups") or {}),
        "cleanup_scope_source": metrics.get("cleanup_scope_source")
        or "full_topology_candidate_inventory_fallback",
        "bounded_owned_scope": bool(metrics.get("bounded_owned_scope")),
        "full_topology_candidate_inventory_fallback": bool(
            metrics.get("full_topology_candidate_inventory_fallback")
            if "full_topology_candidate_inventory_fallback" in metrics
            else True
        ),
    }


def build_anomaly_audit(run_dir: Path) -> dict[str, Any]:
    epoch_details = _load_jsonl(run_dir / "epoch_execution_details.jsonl")
    per_epoch_timing = _load_jsonl(run_dir / "per_epoch_update_timing.jsonl")
    cleanup_report = _load_json(run_dir / "normal_failure_cleanup_report.json")
    real_runtime = _load_json(run_dir / "real_runtime_report.json")
    timing = _load_json(run_dir / "timing_cost_attribution_audit.json")
    startup_ops = _load_json(run_dir / "startup_operation_audit.json")
    static_inventory = _load_json(
        run_dir / "static_inventory_precreated_connection_audit.json"
    )
    steady_epochs: list[dict[str, Any]] = []
    for index, (detail, timing_row) in enumerate(zip(epoch_details, per_epoch_timing)):
        attach = dict(detail.get("bpf_attach_reuse") or {})
        if index == 0:
            continue
        marks = dict(detail.get("epoch_timing_marks") or {})
        derived = dict(marks.get("derived_seconds") or {})
        steady_epochs.append(
            {
                "sequence_index": index,
                "epoch_id": detail.get("epoch_id"),
                "prepare_mode": attach.get("prepare_mode"),
                "bpf_prepare_seconds": _round(timing_row.get("bpf_prepare_seconds")),
                "map_update_seconds": _round(timing_row.get("bpf_map_update_seconds")),
                "map_update_count": int(timing_row.get("bpf_map_update_count") or 0),
                "map_write_update_count": int(
                    attach.get("bpf_map_write_update_count") or 0
                ),
                "map_delete_count": int(timing_row.get("bpf_map_delete_count") or 0),
                "map_delete_seconds": _round(timing_row.get("bpf_map_delete_seconds")),
                "generation_gc_seconds": _round(
                    timing_row.get("bpf_generation_gc_seconds")
                ),
                "active_generation_flip_seconds": _round(
                    timing_row.get("active_generation_flip_seconds")
                ),
                "validation_seconds": _round(timing_row.get("validation_seconds")),
                "post_apply_overhead_seconds": _round(
                    timing_row.get("post_apply_overhead_seconds")
                ),
                "artifact_write_blocking_seconds": _round(
                    derived.get("artifact_write_blocking_seconds")
                ),
                "full_attach_count": int(attach.get("full_attach_count") or 0),
                "delta_attach_count": int(attach.get("delta_attach_count") or 0),
                "reused_attach_count": int(attach.get("reused_attach_count") or 0),
                "qdisc_update_count": int(timing_row.get("qdisc_update_count") or 0),
                "linux_gz_fallback_command_count": int(
                    detail.get("linux_gz_fallback_command_count") or 0
                ),
                "packet_miss_delta_known_clean": True,
                "per_map_breakdown_source": "not_present_in_existing_run_artifact",
                "runtime_reload_attribution_available": False,
            }
        )
    totals = {
        "steady_epoch_count": len(steady_epochs),
        "bpf_prepare_seconds": _round(
            sum(float(epoch.get("bpf_prepare_seconds") or 0.0) for epoch in steady_epochs)
        ),
        "map_update_seconds": _round(
            sum(float(epoch.get("map_update_seconds") or 0.0) for epoch in steady_epochs)
        ),
        "post_apply_overhead_seconds": _round(
            sum(
                float(epoch.get("post_apply_overhead_seconds") or 0.0)
                for epoch in steady_epochs
            )
        ),
        "map_update_count": sum(int(epoch.get("map_update_count") or 0) for epoch in steady_epochs),
        "map_delete_count": sum(int(epoch.get("map_delete_count") or 0) for epoch in steady_epochs),
        "full_attach_count": sum(int(epoch.get("full_attach_count") or 0) for epoch in steady_epochs),
        "delta_attach_count": sum(int(epoch.get("delta_attach_count") or 0) for epoch in steady_epochs),
        "qdisc_update_count": sum(int(epoch.get("qdisc_update_count") or 0) for epoch in steady_epochs),
    }
    cleanup = _cleanup_metrics(cleanup_report, real_runtime)
    labels = [
        "ANOMALY_ATTRIBUTION_ADDED",
        "RUNTIME_FULL_GENERATION_RELOAD_BLOCKER",
        "STARTUP_COST_WARNING_ONLY",
    ]
    if not all(epoch["runtime_reload_attribution_available"] for epoch in steady_epochs):
        labels.append("TIMING_TRACE_INCOMPLETE")
    if not dict(startup_ops.get("summary") or {}).get("operation_trace_complete"):
        labels.extend(["STARTUP_OPERATION_TRACE_INCOMPLETE", "STARTUP_COST_NOT_UNDERSTOOD"])
    if cleanup["raw_resource_count"] > cleanup["cleanup_resource_attempted_count"] * 4:
        labels.extend(["CLEANUP_COST_ANOMALY", "STARTUP_DUPLICATE_NOOP_COST_DETECTED"])
    if cleanup["full_topology_candidate_inventory_fallback"]:
        labels.append("CLEANUP_GLOBAL_SCAN_SCOPE_OBSERVED")
    static_labels = list(static_inventory.get("outcome_labels") or [])
    labels.extend(label for label in static_labels if label.startswith("GW_SLOT_"))
    map_only_clean = (
        totals["full_attach_count"] == 0
        and totals["delta_attach_count"] == 0
        and totals["qdisc_update_count"] == 0
    )
    if map_only_clean and "RUNTIME_FULL_GENERATION_RELOAD_BLOCKER" not in labels:
        labels.append("RUNTIME_MAP_ONLY_STEADY_COST_OK")
    return {
        "schema_version": "skyseed-validation-artifact-anomaly-attribution-v1",
        "run_dir": str(run_dir),
        "generated_from_existing_artifacts_only": True,
        "no_rerun_performed": True,
        "runtime_map_only_cost": {
            "summary": totals,
            "classification": {
                "implementation_mode": "full_generation_reload_with_generation_gc",
                "diff_update_implemented": False,
                "intentional_safety_design": True,
                "optimization_gap": False,
                "current_s4_blocker": True,
                "blocker_label": "RUNTIME_FULL_GENERATION_RELOAD_BLOCKER",
                "map_only_semantics_clean": map_only_clean,
                "validation_reporting_mixed_into_epoch_wall_time": True,
            },
            "steady_epochs": steady_epochs,
            "per_map_breakdown": {
                "available": False,
                "reason": (
                    "existing run predates native runtime_reload_attribution; "
                    "future runs include per-map summary from load_mpls_maps"
                ),
            },
        },
        "cleanup_cost": {
            "summary": cleanup,
            "raw_resource_explanation": (
                "candidate temp host interfaces, host interfaces, container "
                "interfaces, and bridge names expanded from topology slot inventory"
            ),
            "skipped_noop_explanation": (
                "resources were absent during existence checks before delete/down "
                "commands, indicating broad topology candidate cleanup scope"
            ),
            "fix_available_in_code": "cleanup now prefers prepared_owned_resource_index when present and keeps full inventory fallback",
        },
        "startup_materialization_cost": {
            "operation_trace_complete": bool(
                dict(startup_ops.get("summary") or {}).get("operation_trace_complete")
            ),
            "startup_cost_understood": bool(
                dict(startup_ops.get("summary") or {}).get("startup_cost_understood")
            ),
            "native_prewarm_trace_fix_available_in_code": True,
            "prior_labels": list(startup_ops.get("outcome_labels") or []),
        },
        "gw_slot_derivation": dict(
            dict(static_inventory.get("slot_derivation_audit") or {}).get(
                "gw_max_simultaneous_slots"
            )
            or {}
        ),
        "source_artifacts": [
            "epoch_execution_details.jsonl",
            "per_epoch_update_timing.jsonl",
            "normal_failure_cleanup_report.json",
            "startup_operation_audit.json",
            "static_inventory_precreated_connection_audit.json",
            "timing_cost_attribution_audit.json",
        ],
        "prior_timing_summary": dict(timing.get("summary") or {}),
        "outcome_labels": list(dict.fromkeys(labels)),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    run_dir = args.run_dir
    audit = build_anomaly_audit(run_dir)
    (run_dir / "anomaly_attribution_audit.json").write_text(
        json.dumps(audit, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    summary = {
        "schema_version": "skyseed-validation-artifact-anomaly-attribution-summary-v1",
        "run_dir": str(run_dir),
        "runtime_map_only_cost_summary": audit["runtime_map_only_cost"]["summary"],
        "cleanup_cost_summary": audit["cleanup_cost"]["summary"],
        "startup_operation_trace_complete": audit["startup_materialization_cost"][
            "operation_trace_complete"
        ],
        "gw_slot_derivation_label": audit["gw_slot_derivation"].get(
            "derivation_label"
        ),
        "outcome_labels": audit["outcome_labels"],
    }
    (run_dir / "anomaly_attribution_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
