#!/usr/bin/env python3
# encoding: utf-8

"""Equivalence gate for shortened full-S4 replay regressions."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence


SCHEMA_VERSION = "skyseed-shortened-full-s4-replay-equivalence-v1"

VOLATILE_KEYS = {
    "actual_epoch_start_time_utc",
    "aggregate_runtime_succeeded",
    "applied_operations",
    "apply_end_time_utc",
    "apply_start_time_utc",
    "artifact_record_state",
    "bpf_classifier_miss_count",
    "bpf_first_failure_counters",
    "bpf_miss_drop_counters",
    "bpf_ping_path_hit_count",
    "bpf_return_handoff_hit_count",
    "continuous_runtime_execution",
    "deadline_miss",
    "detail_reference",
    "elapsed_seconds",
    "epoch_end_time_utc",
    "epoch_timing_marks",
    "event_timeline_execution_success",
    "final_connectivity",
    "final_cleanup",
    "host_mutation_execution",
    "input_signature",
    "iterations",
    "mtime_ns",
    "outcome",
    "path",
    "ping_success",
    "pre_rollback_connectivity_success",
    "post_run_preflight_clean",
    "rollback_success",
    "run_dir",
    "scheduled_activation_lag_seconds",
    "timing",
    "timing_breakdown",
    "timestamp_utc",
    "wall_seconds",
}


def _timestamp_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _first_existing_json(run_dir: Path, names: Sequence[str]) -> Any:
    for name in names:
        path = run_dir / name
        if path.exists():
            return _read_json(path)
    return None


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _clean(item)
            for key, item in sorted(value.items())
            if str(key) not in VOLATILE_KEYS
        }
    if isinstance(value, list):
        return [_clean(item) for item in value]
    return value


def _sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(_clean(value), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _ids(items: Iterable[Any], *keys: str) -> List[str]:
    result: List[str] = []
    for item in items:
        if isinstance(item, dict):
            for key in keys:
                if item.get(key) is not None:
                    result.append(str(item[key]))
                    break
        elif item is not None:
            result.append(str(item))
    return sorted(result)


def _counter(items: Iterable[Any]) -> Dict[str, int]:
    return dict(sorted(Counter(str(item) for item in items).items()))


def _projection(records: Sequence[Any], keys: Sequence[str]) -> List[Dict[str, Any]]:
    projected: List[Dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        projected.append({key: _clean(record.get(key)) for key in keys if key in record})
    return projected


def _catalog_summary(run_dir: Path) -> Dict[str, Any]:
    catalog = _first_existing_json(
        run_dir,
        (
            "config/effective_workload_catalog.json",
            "effective_workload_catalog.json",
        ),
    )
    shell_manifest = _first_existing_json(run_dir, ("shell_manifest.json", "config/shell_manifest.json")) or {}
    ground_model = dict(shell_manifest.get("ground_model") or {})
    if isinstance(catalog, dict):
        effective = dict(catalog.get("effective_catalogs") or {})
        zones = list(effective.get("zones") or [])
        uts = list(effective.get("uts") or [])
        gateways = list(effective.get("gateways") or [])
        coordinate_policy = catalog.get("coordinate_policy")
        coordinate_semantics = catalog.get("coordinate_semantics")
        source = "effective_workload_catalog"
    else:
        zones = list(ground_model.get("effective_zone_catalog") or [])
        uts = list(ground_model.get("effective_uts") or [])
        gateways = list(ground_model.get("effective_gateways") or [])
        coordinate_policy = dict(ground_model.get("coordinate_semantics") or {}).get("mode")
        coordinate_semantics = ground_model.get("coordinate_semantics")
        source = "shell_manifest_ground_model"
    return {
        "source": source,
        "coordinate_policy": coordinate_policy,
        "coordinate_semantics_sha256": _sha256(coordinate_semantics or {}),
        "selected_ut_ids": _ids(uts, "ut_id", "id"),
        "selected_gateway_ids": _ids(gateways, "gateway_id", "gw_id", "id"),
        "selected_zone_ids": _ids(zones, "zone_id", "id"),
        "ut_catalog_sha256": _sha256(uts),
        "gateway_catalog_sha256": _sha256(gateways),
        "zone_catalog_sha256": _sha256(zones),
    }


def _shell_summary(run_dir: Path) -> Dict[str, Any]:
    shell = _first_existing_json(run_dir, ("shell_manifest.json", "config/shell_manifest.json")) or {}
    selection = dict(shell.get("shell_selection") or {})
    layout = dict(shell.get("layout") or {})
    return {
        "orbit_input_mode": shell.get("orbit_input_mode"),
        "orbit_input_kind": shell.get("orbit_input_kind"),
        "orbit_source": shell.get("orbit_source"),
        "selection_input_mode": shell.get("selection_input_mode"),
        "selected_satellite_count": selection.get("selected_satellite_count"),
        "max_satellites_requested": selection.get("max_satellites_requested"),
        "included_satellite_ids": sorted(
            str(item) for item in list(selection.get("included_satellite_ids") or [])
        ),
        "plane_manifest_sha256": _sha256(selection.get("plane_manifest") or []),
        "layout_satellites_sha256": _sha256(layout.get("satellites") or []),
    }


def _temporal_summary(run_dir: Path) -> Dict[str, Any]:
    temporal = _first_existing_json(run_dir, ("temporal_plan.json", "config/temporal_plan.json")) or {}
    bundle = _first_existing_json(run_dir, ("prepared_update_bundle.json",)) or {}
    event_queue = list(temporal.get("event_queue") or [])
    runtime_epochs = list(temporal.get("runtime_epochs") or [])
    parameter_epochs = list(temporal.get("parameter_epochs") or [])
    observation_frames = list(temporal.get("observation_frames") or [])
    orbit_samples = list(temporal.get("orbit_samples") or [])
    all_sample_frames = list(bundle.get("all_sample_frames") or [])
    return {
        "policy": _clean(temporal.get("policy") or {}),
        "physical_sample_count": bundle.get("physical_sample_count", len(orbit_samples)),
        "orbit_samples": _projection(
            orbit_samples,
            ("sample_id", "sample_index", "slice_id", "relative_time_seconds", "absolute_time_utc", "satellite_count"),
        ),
        "physical_samples_sha256": _sha256(all_sample_frames or observation_frames or orbit_samples),
        "event_queue_count": len(event_queue),
        "event_queue_type_order": [str(item.get("event_type")) for item in event_queue if isinstance(item, dict)],
        "event_queue_sha256": _sha256(
            _projection(
                event_queue,
                (
                    "event_id",
                    "event_type",
                    "activation_group_id",
                    "activation_relative_time_seconds",
                    "activation_time_utc",
                    "detected_relative_time_seconds",
                    "conflict_owner_keys",
                    "source_slice_id",
                    "slice_id",
                ),
            )
        ),
        "runtime_epoch_count": len(runtime_epochs),
        "runtime_epochs_sha256": _sha256(runtime_epochs),
        "parameter_epoch_count": len(parameter_epochs),
        "parameter_epochs_sha256": _sha256(parameter_epochs),
        "refined_event_count": bundle.get("refined_event_count"),
        "sample_aligned_event_count": bundle.get("sample_aligned_event_count"),
        "skipped_noop_sample_count": bundle.get("skipped_noop_sample_count"),
    }


def _prepared_bundle_summary(run_dir: Path) -> Dict[str, Any]:
    bundle = _first_existing_json(run_dir, ("prepared_update_bundle.json",)) or {}
    selected_queue = list(bundle.get("selected_epoch_queue") or [])
    return {
        "strict_mode": bundle.get("strict_mode"),
        "source": bundle.get("source"),
        "selected_runtime_slice_ids": list(bundle.get("selected_runtime_slice_ids") or []),
        "selected_parameter_slice_ids": list(bundle.get("selected_parameter_slice_ids") or []),
        "runtime_transaction_count": bundle.get("runtime_transaction_count"),
        "parameter_update_batch_count": bundle.get("parameter_update_batch_count"),
        "physical_sample_count": bundle.get("physical_sample_count"),
        "refined_event_count": bundle.get("refined_event_count"),
        "selected_epoch_queue_count": len(selected_queue),
        "epoch_queue_type_order": [
            str(record.get("epoch_type") or record.get("event_timeline_type"))
            for record in selected_queue
            if isinstance(record, dict)
        ],
        "selected_epoch_queue_sha256": _sha256(
            _projection(
                selected_queue,
                (
                    "sequence_index",
                    "segment_epoch_key",
                    "epoch_type",
                    "event_timeline_type",
                    "source_event_type",
                    "slice_id",
                    "to_slice_id",
                    "epoch_id",
                    "activation_group_id",
                    "activation_relative_time_seconds",
                    "source_event_id",
                    "trigger_reasons",
                    "runtime_plan_id",
                    "parameter_batch_id",
                ),
            )
        ),
        "resource_budget_sha256": _sha256(bundle.get("resource_budget") or {}),
        "continuous_operation_prepared_sha256": _sha256(
            bundle.get("continuous_operation_prepared") or {}
        ),
        "runtime_epoch_summaries_sha256": _sha256(bundle.get("runtime_epoch_summaries") or []),
        "parameter_epoch_summaries_sha256": _sha256(bundle.get("parameter_epoch_summaries") or []),
    }


def _zone_anchor_summary(run_dir: Path) -> Dict[str, Any]:
    validation = _first_existing_json(run_dir, ("zone_anchor_semantic_validation.json",)) or {}
    ledger = list(validation.get("zone_anchor_ledger") or [])
    if not ledger:
        ledger_payload = _first_existing_json(run_dir, ("zone_anchor_ledger.json",)) or {}
        ledger = list(ledger_payload.get("records") or [])
    return {
        "semantic_passed": validation.get("passed"),
        "semantic_summary": _clean(validation.get("summary") or {}),
        "violation_count": len(list(validation.get("violation_table") or [])),
        "zone_anchor_record_count": len(ledger),
        "active_anchor_counts": _counter(
            record.get("active_anchor_count")
            for record in ledger
            if isinstance(record, dict)
        ),
        "active_anchors_per_generation_sha256": _sha256(
            _projection(
                ledger,
                (
                    "generation",
                    "generation_source",
                    "slice_id",
                    "zone_id",
                    "ut_id",
                    "active_anchor_count",
                    "active_anchor_candidates",
                    "active_anchor_selection_reason",
                    "anchor_selection",
                ),
            )
        ),
    }


def _gz_fec_summary(run_dir: Path) -> Dict[str, Any]:
    ledger_payload = _first_existing_json(run_dir, ("gz_fec_ledger.json",)) or {}
    records = list(ledger_payload.get("records") or [])
    return {
        "record_count": ledger_payload.get("record_count", len(records)),
        "fec_ids": _ids(records, "fec_id"),
        "fec_kind_counts": _counter(
            record.get("fec_kind")
            for record in records
            if isinstance(record, dict)
        ),
        "generation_counts": _counter(
            record.get("generation")
            for record in records
            if isinstance(record, dict)
        ),
        "records_sha256": _sha256(records),
    }


def _bpf_summary(run_dir: Path) -> Dict[str, Any]:
    report = _first_existing_json(run_dir, ("bpf_mpls_offline_validation.json",)) or {}
    aggregate = dict(report.get("aggregate") or {})
    compiled_slices = list(report.get("compiled_slices") or [])
    return {
        "selected_runtime_slice_ids": list(report.get("selected_runtime_slice_ids") or []),
        "compiled_slice_count": report.get("compiled_slice_count", len(compiled_slices)),
        "aggregate": {
            key: aggregate.get(key)
            for key in (
                "classifier_entry_count",
                "core_lfib_entry_count",
                "egress_handoff_entry_count",
                "generation_count",
                "unsafe_conflict_count",
                "safe_duplicate_count",
            )
        },
        "compiled_mpls_map_counts": [
            {
                "generation": item.get("generation"),
                "slice_id": item.get("slice_id"),
                "compiled_mpls_map_counts": item.get("compiled_mpls_map_counts"),
                "context_count": item.get("context_count"),
            }
            for item in compiled_slices
            if isinstance(item, dict)
        ],
        "label_allocation_ledger_count": report.get("label_allocation_ledger_count"),
        "label_allocation_ledger_sha256": _sha256(report.get("label_allocation_ledger") or []),
        "compiler_inputs_sha256": _sha256(
            {
                "selected_runtime_slice_ids": report.get("selected_runtime_slice_ids") or [],
                "compiled_slices": [
                    {
                        "generation": item.get("generation"),
                        "generation_source": item.get("generation_source"),
                        "runtime_plan_id": item.get("runtime_plan_id"),
                        "slice_id": item.get("slice_id"),
                        "context_count": item.get("context_count"),
                    }
                    for item in compiled_slices
                    if isinstance(item, dict)
                ],
            }
        ),
    }


def _path_cardinality_summary(run_dir: Path) -> Dict[str, Any]:
    audit = _first_existing_json(run_dir, ("path_cardinality_audit.json",)) or {}
    aggregate = dict(audit.get("aggregate") or {})
    return {
        "semantic_validation_passed": audit.get("semantic_validation_passed"),
        "bpf_offline_validation_passed": audit.get("bpf_offline_validation_passed"),
        "selected_runtime_slice_ids": list(audit.get("selected_runtime_slice_ids") or []),
        "aggregate": {
            key: aggregate.get(key)
            for key in (
                "eligible_core_forwarding_scope_count",
                "compiled_bpf_owned_core_forwarding_count",
                "linux_gz_mpls_lfib_fallback_count",
                "skipped_eligible_core_scope_count",
                "direct_handoff_count",
                "gateway_class_selected_gateway_mismatch_count",
                "blocker_count",
                "meaningfulness_gate_passed",
            )
        },
        "per_slice_sha256": _sha256(audit.get("per_slice") or []),
        "risk_summary_sha256": _sha256(audit.get("risk_summary") or {}),
    }


def build_replay_equivalence_fingerprint(run_dir: Path) -> Dict[str, Any]:
    run_dir = Path(run_dir)
    return {
        "catalog": _catalog_summary(run_dir),
        "shell": _shell_summary(run_dir),
        "temporal": _temporal_summary(run_dir),
        "prepared_bundle": _prepared_bundle_summary(run_dir),
        "zone_anchors": _zone_anchor_summary(run_dir),
        "gz_fec": _gz_fec_summary(run_dir),
        "bpf_offline": _bpf_summary(run_dir),
        "path_cardinality": _path_cardinality_summary(run_dir),
    }


def _sample_mismatch(source: Any, candidate: Any) -> Dict[str, Any]:
    if isinstance(source, list) and isinstance(candidate, list):
        max_len = max(len(source), len(candidate))
        for index in range(max_len):
            left = source[index] if index < len(source) else "<missing>"
            right = candidate[index] if index < len(candidate) else "<missing>"
            if _clean(left) != _clean(right):
                return {"index": index, "source": _clean(left), "candidate": _clean(right)}
    if isinstance(source, dict) and isinstance(candidate, dict):
        keys = sorted(set(source) | set(candidate))
        for key in keys:
            left = source.get(key, "<missing>")
            right = candidate.get(key, "<missing>")
            if _clean(left) != _clean(right):
                return {"field": key, "source": _clean(left), "candidate": _clean(right)}
    return {"source": _clean(source), "candidate": _clean(candidate)}


def _compare_section(name: str, source: Any, candidate: Any) -> Dict[str, Any]:
    source_clean = _clean(source)
    candidate_clean = _clean(candidate)
    source_hash = _sha256(source_clean)
    candidate_hash = _sha256(candidate_clean)
    equal = source_hash == candidate_hash
    result = {
        "name": name,
        "equal": equal,
        "source_sha256": source_hash,
        "candidate_sha256": candidate_hash,
    }
    if not equal:
        result["mismatch"] = _sample_mismatch(source_clean, candidate_clean)
    return result


def compare_replay_equivalence(
    *,
    source_run_dir: Path,
    candidate_run_dir: Path,
) -> Dict[str, Any]:
    source = build_replay_equivalence_fingerprint(source_run_dir)
    candidate = build_replay_equivalence_fingerprint(candidate_run_dir)
    comparisons = [
        _compare_section(section, source.get(section), candidate.get(section))
        for section in (
            "catalog",
            "shell",
            "temporal",
            "prepared_bundle",
            "zone_anchors",
            "gz_fec",
            "bpf_offline",
            "path_cardinality",
        )
    ]
    mismatches = [item for item in comparisons if not bool(item.get("equal"))]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": _timestamp_utc(),
        "source_run_dir": str(source_run_dir),
        "candidate_run_dir": str(candidate_run_dir),
        "equivalent": not mismatches,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
        "comparisons": comparisons,
        "source_fingerprint": source,
        "candidate_fingerprint": candidate,
        "runtime_conclusion_allowed": not mismatches,
        "runtime_conclusion": None,
    }


def render_replay_equivalence_markdown(report: Dict[str, Any]) -> str:
    lines = [
        "# Shortened Full-S4 Replay Equivalence",
        "",
        f"- source: `{report.get('source_run_dir')}`",
        f"- candidate: `{report.get('candidate_run_dir')}`",
        f"- equivalent: `{report.get('equivalent')}`",
        f"- mismatch_count: `{report.get('mismatch_count')}`",
        f"- runtime_conclusion_allowed: `{report.get('runtime_conclusion_allowed')}`",
        "",
        "## Comparisons",
        "",
    ]
    for comparison in list(report.get("comparisons") or []):
        lines.append(
            f"- {comparison.get('name')}: equal=`{comparison.get('equal')}`"
        )
    mismatches = list(report.get("mismatches") or [])
    if mismatches:
        lines.extend(["", "## First Mismatches", ""])
        for mismatch in mismatches:
            sample = mismatch.get("mismatch")
            lines.append(f"- {mismatch.get('name')}: `{json.dumps(sample, sort_keys=True)}`")
    return "\n".join(lines) + "\n"


def write_replay_equivalence_report(
    *,
    source_run_dir: Path,
    candidate_run_dir: Path,
    report_path: Path | None = None,
    markdown_path: Path | None = None,
) -> Dict[str, Any]:
    report = compare_replay_equivalence(
        source_run_dir=source_run_dir,
        candidate_run_dir=candidate_run_dir,
    )
    if report_path is None:
        report_path = Path(candidate_run_dir) / "shortened_full_s4_replay_equivalence_report.json"
    if markdown_path is None:
        markdown_path = Path(candidate_run_dir) / "shortened_full_s4_replay_equivalence_report.md"
    _write_json(Path(report_path), report)
    Path(markdown_path).parent.mkdir(parents=True, exist_ok=True)
    Path(markdown_path).write_text(render_replay_equivalence_markdown(report), encoding="utf-8")
    report["report_path"] = str(report_path)
    report["markdown_path"] = str(markdown_path)
    return report
