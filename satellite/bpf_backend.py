#!/usr/bin/env python3
# encoding: utf-8

"""Experimental Skyseed BPF shadow backend compiler and audit helpers."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Iterable

from satellite.apps.compile_gz_fec_to_bpf_generation import (
    COUNTER_LABELS as GZ_COUNTER_LABELS,
    collect_linux_mpls_leakage,
    compile_generation_map,
    stable_u32,
)
from satellite.apps.compile_parameter_timeline_to_qdisc import (
    compile_qdisc_timeline,
    load_artifacts as load_parameter_artifacts,
    scaled_loss_u32,
)


BACKEND_MODE = "experimental_bpf_shadow"
QDISC_TIMELINE_BACKEND_STATUS = "blocked_or_trace_only"
PARAMETER_COUNTER_LABELS = {
    "parameter_generation_hit": "parameter_generation_hit",
    "parameter_entry_hit": "parameter_entry_hit",
    "pass_count": "pass_count",
    "drop_inactive": "drop_inactive",
    "drop_loss": "drop_loss",
    "drop_missing_generation": "drop_missing_generation",
    "drop_missing_parameter": "drop_missing_parameter",
    "future_generation_not_used": "future_generation_not_used",
    "drop_bad_packet": "drop_bad_packet",
}
PARAMETER_FLOW_CLASS = "default"
PARAMETER_FLOW_KEY_U32 = 0
LOSS_PPM_ALL = 1_000_000


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            value = json.loads(line)
            if isinstance(value, dict):
                yield value


def load_shadow_backend_artifacts(run_dir: Path) -> dict[str, Any]:
    """Load the existing runtime artifacts needed for the shadow backend."""

    gz_plan_path = _first_existing(
        run_dir / "gz_fec_plan.json",
        run_dir / "config" / "gz_fec_plan.json",
    )
    gz_diff_path = _first_existing(
        run_dir / "gz_update_diff_summary.json",
        run_dir / "config" / "gz_update_diff_summary.json",
    )
    temporal_plan_path = _first_existing(
        run_dir / "temporal_plan.json",
        run_dir / "config" / "temporal_plan.json",
    )
    parameter_summary_path = run_dir / "parameter_epoch_summary.json"
    details_path = run_dir / "epoch_execution_details.jsonl"
    missing = [
        str(path)
        for path in [
            gz_plan_path,
            gz_diff_path,
            temporal_plan_path,
            parameter_summary_path,
            details_path,
        ]
        if path is None or not path.exists()
    ]
    if missing:
        raise FileNotFoundError("missing required BPF shadow artifact(s): " + ", ".join(missing))

    assert gz_plan_path is not None
    assert gz_diff_path is not None
    assert temporal_plan_path is not None
    return {
        "source_root": str(run_dir),
        "gz_fec_plan_path": str(gz_plan_path),
        "gz_update_diff_summary_path": str(gz_diff_path),
        "temporal_plan_path": str(temporal_plan_path),
        "parameter_epoch_summary_path": str(parameter_summary_path),
        "epoch_execution_details_path": str(details_path),
        "gz_fec_plan": read_json(gz_plan_path),
        "gz_update_diff_summary": read_json(gz_diff_path),
        "temporal_plan": read_json(temporal_plan_path),
        "parameter_epoch_summary": read_json(parameter_summary_path),
        "epoch_execution_details": list(iter_jsonl(details_path)),
    }


def _first_existing(*paths: Path) -> Path | None:
    return next((path for path in paths if path.exists()), None)


def parameter_loss_ppm(loss_percent: float | int | None) -> int:
    if loss_percent is None:
        return 0
    return min(LOSS_PPM_ALL, max(0, int(round(float(loss_percent) * 10_000))))


def parameter_mark(link_id: str, generation_id: int) -> int:
    return ((stable_u32(link_id) & 0xFFFF) << 8) ^ (generation_id & 0xFF)


def expected_parameter_counter_labels(entry: dict[str, Any]) -> list[str]:
    if not entry.get("active"):
        return [PARAMETER_COUNTER_LABELS["drop_inactive"]]
    labels = [
        PARAMETER_COUNTER_LABELS["parameter_generation_hit"],
        PARAMETER_COUNTER_LABELS["parameter_entry_hit"],
    ]
    if int(entry.get("loss_ppm") or 0) >= LOSS_PPM_ALL:
        labels.append(PARAMETER_COUNTER_LABELS["drop_loss"])
    else:
        labels.append(PARAMETER_COUNTER_LABELS["pass_count"])
    return labels


def compile_parameter_map(
    artifacts: dict[str, Any],
    *,
    active_generation: int = 0,
) -> dict[str, Any]:
    """Compile ParameterEpoch execution details into shadow BPF parameter maps."""

    qdisc_artifacts = {
        "source_root": artifacts["source_root"],
        "temporal_plan_path": artifacts["temporal_plan_path"],
        "parameter_epoch_summary_path": artifacts["parameter_epoch_summary_path"],
        "epoch_execution_details_path": artifacts["epoch_execution_details_path"],
        "temporal_plan": artifacts["temporal_plan"],
        "parameter_epoch_summary": artifacts["parameter_epoch_summary"],
        "epoch_execution_details": artifacts["epoch_execution_details"],
    }
    timeline = compile_qdisc_timeline(qdisc_artifacts)
    active_by_key = parameter_active_index(artifacts["epoch_execution_details"])
    entries: list[dict[str, Any]] = []
    generation_ids: set[int] = set()
    generation_metadata: dict[int, dict[str, Any]] = {}

    for link in timeline["link_timelines"]:
        for segment in link.get("segments", []):
            generation_id = int(segment["parameter_generation"])
            generation_ids.add(generation_id)
            generation_metadata.setdefault(
                generation_id,
                {
                    "generation_id": generation_id,
                    "epoch_id": segment.get("epoch_id"),
                    "activation_group_id": segment.get("activation_group_id"),
                    "activation_relative_time_seconds": segment.get(
                        "activation_relative_time_seconds"
                    ),
                    "activation_time_utc": segment.get("activation_time_utc"),
                    "slice_id": segment.get("slice_id"),
                    "sample_id": segment.get("sample_id"),
                    "snapshot_id": segment.get("snapshot_id"),
                    "installed": True,
                    "active": generation_id == active_generation,
                },
            )
            params = segment["qdisc_parameters"]
            loss_percent = params["loss"]["loss_percent"]
            loss_ppm = parameter_loss_ppm(loss_percent)
            active = active_by_key.get((generation_id, segment["link_id"]), True)
            entry = {
                "generation_id": generation_id,
                "epoch_id": segment.get("epoch_id"),
                "link_id": segment["link_id"],
                "link_key_u32": stable_u32(segment["link_id"]),
                "flow_class": PARAMETER_FLOW_CLASS,
                "flow_key_u32": PARAMETER_FLOW_KEY_U32,
                "slot_type": segment.get("slot_type"),
                "binding": copy.deepcopy(segment.get("binding")),
                "active": active,
                "action": parameter_action(active=active, loss_ppm=loss_ppm),
                "loss_percent": loss_percent,
                "loss_ppm": loss_ppm,
                "loss_scaled_u32": scaled_loss_u32(loss_percent),
                "delay_ms": params["delay"]["delay_ms"],
                "bandwidth_mbps": params["rate"]["bandwidth_mbps"],
                "rate_bps": params["rate"]["rate_bps"],
                "routing_cost": params["routing_cost"]["value"],
                "mark": parameter_mark(segment["link_id"], generation_id),
                "event_time_metadata": {
                    "source": "epoch_execution_details.parameter_epoch",
                    "activation_relative_time_seconds": segment.get(
                        "activation_relative_time_seconds"
                    ),
                    "activation_time_utc": segment.get("activation_time_utc"),
                    "end_relative_time_seconds": segment.get("end_relative_time_seconds"),
                    "end_time_utc": segment.get("end_time_utc"),
                    "fixed_10s_assumed": False,
                },
                "source": "parameter_epoch_runtime_parameters",
            }
            entry["expected_counter_labels"] = expected_parameter_counter_labels(entry)
            entries.append(entry)

    generation_status = [
        generation_metadata[generation_id]
        for generation_id in sorted(generation_ids)
    ]
    link_ids = sorted({entry["link_id"] for entry in entries})
    compiled = {
        "schema_version": 1,
        "backend_mode": BACKEND_MODE,
        "compiler": "satellite/bpf_backend.py",
        "scope": {
            "shadow_only": True,
            "canonical_runtime_changed": False,
            "real_dataplane_touched": False,
            "qdisc_timeline_landing": False,
            "fixed_10s_assumed": False,
        },
        "bpf_shadow_model": {
            "parameter_generation": "array[0] -> active u32 parameter generation",
            "param_status": "array[generation] -> installed/active metadata",
            "parameter_values": (
                "hash[(generation, link_key_u32, flow_key_u32)] -> active/loss/mark"
            ),
            "parameter_counters": sorted(PARAMETER_COUNTER_LABELS.values()),
            "future_param_gen": "trace-only preloaded future generation ids",
        },
        "generation_cadence": timeline["generation_cadence"],
        "bpf_maps": {
            "parameter_generation": {
                "slot": 0,
                "value": active_generation,
                "epoch_id": (
                    generation_metadata.get(active_generation, {}).get("epoch_id")
                    if generation_metadata
                    else None
                ),
            },
            "param_status": generation_status,
            "parameter_values": entries,
            "parameter_counters": sorted(PARAMETER_COUNTER_LABELS.values()),
            "future_param_gen": [
                generation_id
                for generation_id in sorted(generation_ids)
                if generation_id > active_generation
            ][:8],
        },
        "summary": {
            "planned_parameter_epoch_count": len(
                artifacts["temporal_plan"].get("parameter_epochs", []) or []
            ),
            "parameter_generation_count": len(generation_ids),
            "parameter_link_count": len(link_ids),
            "parameter_entry_count": len(entries),
            "active_generation_id": active_generation,
            "drop_loss_entry_count": sum(
                1 for entry in entries if entry["action"] == "drop_loss"
            ),
            "inactive_tombstone_count": sum(1 for entry in entries if not entry["active"]),
            "slot_type_counts": timeline["summary"]["slot_type_counts"],
        },
        "link_ids": link_ids,
    }
    compiled["self_checks"] = build_parameter_self_checks(compiled)
    return compiled


def parameter_active_index(records: list[dict[str, Any]]) -> dict[tuple[int, str], bool]:
    active_by_key: dict[tuple[int, str], bool] = {}
    generation_id = 0
    for record in records:
        if not _is_parameter_record(record):
            continue
        apply_results = ((record.get("host_mutation_execution") or {}).get("apply_results")) or []
        for result in apply_results:
            if not isinstance(result, dict) or result.get("operation") != "update_slot_parameters":
                continue
            slot_name = result.get("slot_name")
            if not isinstance(slot_name, str):
                continue
            state = result.get("resulting_state") or {}
            active_by_key[(generation_id, slot_name)] = state.get("occupied") is not False
        generation_id += 1
    return active_by_key


def _is_parameter_record(record: dict[str, Any]) -> bool:
    return (
        record.get("epoch_type") == "parameter_epoch"
        or record.get("original_epoch_type") == "parameter_epoch"
        or str(record.get("epoch_id") or "").startswith("parameterepoch-")
    )


def parameter_action(*, active: bool, loss_ppm: int) -> str:
    if not active:
        return "drop_inactive"
    if loss_ppm >= LOSS_PPM_ALL:
        return "drop_loss"
    return "pass"


def parameter_lookup_decision(compiled: dict[str, Any], generation_id: int, link_id: str) -> dict[str, Any]:
    generations = {
        item["generation_id"]: item for item in compiled["bpf_maps"]["param_status"]
    }
    generation = generations.get(generation_id)
    if generation is None or not generation.get("installed"):
        return {
            "decision": "drop",
            "reason": "missing_parameter_generation",
            "expected_counter_labels": [
                PARAMETER_COUNTER_LABELS["drop_missing_generation"]
            ],
        }

    for entry in compiled["bpf_maps"]["parameter_values"]:
        if entry["generation_id"] == generation_id and entry["link_id"] == link_id:
            if not entry.get("active"):
                return {
                    "decision": "drop",
                    "reason": "inactive_parameter",
                    "expected_counter_labels": [
                        PARAMETER_COUNTER_LABELS["drop_inactive"]
                    ],
                }
            if int(entry.get("loss_ppm") or 0) >= LOSS_PPM_ALL:
                return {
                    "decision": "drop",
                    "reason": "loss_drop",
                    "expected_counter_labels": [
                        PARAMETER_COUNTER_LABELS["parameter_generation_hit"],
                        PARAMETER_COUNTER_LABELS["parameter_entry_hit"],
                        PARAMETER_COUNTER_LABELS["drop_loss"],
                    ],
                }
            return {
                "decision": "pass",
                "reason": "active_parameter",
                "expected_counter_labels": entry.get("expected_counter_labels", []),
                "mark": entry.get("mark"),
            }

    return {
        "decision": "drop",
        "reason": "missing_parameter",
        "expected_counter_labels": [
            PARAMETER_COUNTER_LABELS["parameter_generation_hit"],
            PARAMETER_COUNTER_LABELS["drop_missing_parameter"],
        ],
    }


def active_parameter_lookup_decision(compiled: dict[str, Any], link_id: str) -> dict[str, Any]:
    active_generation = int(compiled["bpf_maps"]["parameter_generation"]["value"])
    return parameter_lookup_decision(compiled, active_generation, link_id)


def build_parameter_self_checks(compiled: dict[str, Any]) -> dict[str, Any]:
    entries = compiled["bpf_maps"]["parameter_values"]
    active_generation = int(compiled["bpf_maps"]["parameter_generation"]["value"])
    generation_ids = [item["generation_id"] for item in compiled["bpf_maps"]["param_status"]]
    max_generation = max(generation_ids) if generation_ids else 0
    sample_link = entries[0]["link_id"] if entries else "__missing_link__"
    missing_generation = parameter_lookup_decision(compiled, max_generation + 1000, sample_link)
    missing_parameter = parameter_lookup_decision(
        compiled,
        active_generation,
        "__missing_parameter_link__",
    )

    inactive_probe = None
    for entry in entries:
        if not entry.get("active"):
            inactive_probe = parameter_lookup_decision(
                compiled,
                int(entry["generation_id"]),
                str(entry["link_id"]),
            )
            break

    future_probe = None
    for entry in entries:
        generation_id = int(entry["generation_id"])
        if generation_id <= active_generation:
            continue
        future_probe = {
            "future_generation_id": generation_id,
            "link_id": entry["link_id"],
            "active_generation_id": active_generation,
            "active_generation_decision": active_parameter_lookup_decision(
                compiled,
                entry["link_id"],
            ),
            "future_generation_decision": parameter_lookup_decision(
                compiled,
                generation_id,
                entry["link_id"],
            ),
            "ok": generation_id != active_generation,
            "expected_counter_labels": [
                PARAMETER_COUNTER_LABELS["future_generation_not_used"]
            ],
        }
        break

    return {
        "missing_parameter_generation_fail_closed": {
            "ok": missing_generation["reason"] == "missing_parameter_generation",
            "probe": missing_generation,
        },
        "missing_parameter_fail_closed": {
            "ok": missing_parameter["reason"] == "missing_parameter",
            "probe": missing_parameter,
        },
        "inactive_parameter_tombstone_fail_closed": {
            "ok": True if inactive_probe is None else inactive_probe["reason"] == "inactive_parameter",
            "probe": inactive_probe,
            "note": (
                "No inactive ParameterEpoch tombstones observed in source artifact; "
                "inactive rule remains part of the compiled fail-closed rule set."
                if inactive_probe is None
                else None
            ),
        },
        "future_parameter_generation_not_active": {
            "ok": bool(future_probe and future_probe["ok"]),
            "probe": future_probe,
        },
    }


def fail_closed_rules() -> list[dict[str, Any]]:
    return [
        {
            "name": "missing_runtime_generation",
            "map": "generation_status",
            "action": "drop",
            "counter": GZ_COUNTER_LABELS["drop_missing_generation"],
        },
        {
            "name": "missing_fec",
            "map": "fec_forwarding",
            "action": "drop",
            "counter": GZ_COUNTER_LABELS["drop_missing_fec"],
        },
        {
            "name": "inactive_fec_tombstone",
            "map": "fec_forwarding",
            "action": "drop",
            "counter": GZ_COUNTER_LABELS["drop_inactive"],
        },
        {
            "name": "future_runtime_generation_not_active",
            "map": "active_generation",
            "action": "ignore_future_generation",
            "counter": GZ_COUNTER_LABELS["future_generation_not_used"],
        },
        {
            "name": "missing_parameter_generation",
            "map": "param_status",
            "action": "drop",
            "counter": PARAMETER_COUNTER_LABELS["drop_missing_generation"],
        },
        {
            "name": "missing_parameter",
            "map": "parameter_values",
            "action": "drop",
            "counter": PARAMETER_COUNTER_LABELS["drop_missing_parameter"],
        },
        {
            "name": "inactive_parameter_tombstone",
            "map": "parameter_values",
            "action": "drop",
            "counter": PARAMETER_COUNTER_LABELS["drop_inactive"],
        },
        {
            "name": "future_parameter_generation_not_active",
            "map": "parameter_generation",
            "action": "ignore_future_generation",
            "counter": PARAMETER_COUNTER_LABELS["future_generation_not_used"],
        },
    ]


def compile_bpf_shadow_backend(
    artifacts: dict[str, Any],
    *,
    input_mode: str = "existing_artifact",
    active_runtime_generation: int = 0,
    active_parameter_generation: int = 0,
) -> dict[str, Any]:
    gz_map = compile_generation_map(
        artifacts["gz_fec_plan"],
        artifacts["gz_update_diff_summary"],
        artifacts["temporal_plan"],
        active_generation=active_runtime_generation,
    )
    parameter_map = compile_parameter_map(
        artifacts,
        active_generation=active_parameter_generation,
    )
    rules = fail_closed_rules()
    linux_mpls_label_leakage = collect_linux_mpls_leakage(
        artifacts["gz_fec_plan"],
        artifacts["gz_update_diff_summary"],
    )
    summary = {
        "backend_mode": BACKEND_MODE,
        "input_mode": input_mode,
        "runtime_generation_count": gz_map["summary"]["generation_count"],
        "gz_fec_entry_count": gz_map["summary"]["fec_entry_count"],
        "parameter_generation_count": parameter_map["summary"]["parameter_generation_count"],
        "planned_parameter_epoch_count": parameter_map["summary"][
            "planned_parameter_epoch_count"
        ],
        "parameter_link_count": parameter_map["summary"]["parameter_link_count"],
        "parameter_entry_count": parameter_map["summary"]["parameter_entry_count"],
        "fail_closed_rule_count": len(rules),
        "fixed_10s_assumed": False,
        "qdisc_timeline_backend_status": QDISC_TIMELINE_BACKEND_STATUS,
        "canonical_runtime_unchanged": True,
        "linux_mpls_label_leakage_found": linux_mpls_label_leakage["found"],
    }
    compiled = {
        "schema_version": 1,
        "goal": "Skyseed Goal 14 experimental BPF shadow backend landing",
        "generated_at": "not_recorded_for_reproducible_baseline",
        "backend_mode": BACKEND_MODE,
        "compiler": "satellite/bpf_backend.py",
        "input_mode": input_mode,
        "input_artifacts": {
            "source_root": artifacts["source_root"],
            "gz_fec_plan_path": artifacts["gz_fec_plan_path"],
            "gz_update_diff_summary_path": artifacts["gz_update_diff_summary_path"],
            "temporal_plan_path": artifacts["temporal_plan_path"],
            "parameter_epoch_summary_path": artifacts["parameter_epoch_summary_path"],
            "epoch_execution_details_path": artifacts["epoch_execution_details_path"],
        },
        "scope": {
            "shadow_only": True,
            "canonical_runtime_changed": False,
            "linux_direct_backend_replaced": False,
            "real_dataplane_touched": False,
            "gz_semantics_changed": False,
            "qdisc_timeline_landing": False,
            "theaterq_or_external_qdisc_module": False,
            "long_real_runtime": False,
        },
        "backend_compile_plan": {
            "name": "BackendCompilePlan",
            "backend_mode": BACKEND_MODE,
            "components": [
                "G/Z FEC generation map",
                "ParameterEpoch parameter map",
                "fail-closed rules",
                "counter and audit schema",
            ],
            "semantic_sources": {
                "gz_fec": "gz_fec_plan.json + gz_update_diff_summary.json",
                "runtime_epoch_metadata": "temporal_plan.runtime_epochs",
                "parameter_epoch": (
                    "epoch_execution_details parameter update records plus "
                    "temporal_plan/parameter_epoch_summary metadata"
                ),
            },
            "not_intent_sources": ["Linux MPLS labels", "LFIB operation estimates"],
        },
        "bpf_maps": {
            "runtime": gz_map["bpf_maps"],
            "parameter": parameter_map["bpf_maps"],
        },
        "runtime_generation_summary": gz_map["summary"],
        "parameter_generation_summary": parameter_map["summary"],
        "fail_closed_rules": rules,
        "counter_schema": {
            "runtime": sorted(GZ_COUNTER_LABELS.values()),
            "parameter": sorted(PARAMETER_COUNTER_LABELS.values()),
        },
        "generation_cadence": {
            "fixed_10s_assumed": False,
            "runtime_source": "G/Z generation map and temporal_plan runtime event metadata",
            "parameter_source": parameter_map["generation_cadence"]["cadence_source"],
            "parameter_observed_activation_interval_seconds": parameter_map[
                "generation_cadence"
            ]["observed_activation_interval_seconds"],
        },
        "backend_audit_report": build_backend_audit_report(
            gz_map,
            parameter_map,
            linux_mpls_label_leakage,
        ),
        "compact_summary": summary,
    }
    return compiled


def build_backend_audit_report(
    gz_map: dict[str, Any],
    parameter_map: dict[str, Any],
    linux_mpls_label_leakage: dict[str, Any],
) -> dict[str, Any]:
    gz_checks = gz_map["self_checks"]
    parameter_checks = parameter_map["self_checks"]
    checks = {
        "gz_fec_intent_matches_bpf_generation_map": {
            "ok": (
                gz_map["summary"]["fec_entry_count"]
                == len(gz_map["bpf_maps"]["fec_forwarding"])
            ),
            "active_fec_count": gz_map["summary"]["active_fec_count"],
            "inactive_fec_tombstone_count": gz_map["summary"][
                "inactive_fec_tombstone_count"
            ],
        },
        "parameter_intent_matches_bpf_parameter_map": {
            "ok": (
                parameter_map["summary"]["parameter_entry_count"]
                == len(parameter_map["bpf_maps"]["parameter_values"])
            ),
            "parameter_link_count": parameter_map["summary"]["parameter_link_count"],
            "drop_loss_entry_count": parameter_map["summary"]["drop_loss_entry_count"],
        },
        "runtime_fail_closed": {
            "ok": all(
                gz_checks[name]["ok"]
                for name in [
                    "missing_generation_fail_closed",
                    "missing_fec_fail_closed",
                    "inactive_fec_fail_closed",
                    "future_generation_not_active",
                ]
            ),
            "checks": gz_checks,
        },
        "parameter_fail_closed": {
            "ok": all(
                parameter_checks[name]["ok"]
                for name in [
                    "missing_parameter_generation_fail_closed",
                    "missing_parameter_fail_closed",
                    "inactive_parameter_tombstone_fail_closed",
                    "future_parameter_generation_not_active",
                ]
            ),
            "checks": parameter_checks,
        },
        "future_generation_not_early_active": {
            "ok": bool(
                gz_checks["future_generation_not_active"]["ok"]
                and parameter_checks["future_parameter_generation_not_active"]["ok"]
            ),
        },
        "linux_mpls_label_leakage_recorded_not_used_as_intent": {
            "ok": True,
            "leakage": linux_mpls_label_leakage,
        },
    }
    return {
        "name": "BackendAuditReport",
        "backend_mode": BACKEND_MODE,
        "checks": checks,
        "ok": all(check["ok"] for check in checks.values()),
    }


def compact_for_baseline(compiled: dict[str, Any]) -> dict[str, Any]:
    runtime_values = compiled["bpf_maps"]["runtime"]["fec_forwarding"]
    parameter_values = compiled["bpf_maps"]["parameter"]["parameter_values"]
    compact = {
        key: copy.deepcopy(compiled[key])
        for key in [
            "schema_version",
            "goal",
            "generated_at",
            "backend_mode",
            "compiler",
            "input_mode",
            "input_artifacts",
            "scope",
            "backend_compile_plan",
            "runtime_generation_summary",
            "parameter_generation_summary",
            "fail_closed_rules",
            "counter_schema",
            "generation_cadence",
            "backend_audit_report",
            "compact_summary",
        ]
    }
    compact["bpf_maps"] = {
        "runtime": {
            "active_generation": compiled["bpf_maps"]["runtime"]["active_generation"],
            "generation_status": compiled["bpf_maps"]["runtime"]["generation_status"],
            "fec_forwarding_entry_count": len(runtime_values),
            "samples": _runtime_samples(runtime_values),
        },
        "parameter": {
            "parameter_generation": compiled["bpf_maps"]["parameter"][
                "parameter_generation"
            ],
            "param_status_count": len(compiled["bpf_maps"]["parameter"]["param_status"]),
            "parameter_value_entry_count": len(parameter_values),
            "future_param_gen_sample": compiled["bpf_maps"]["parameter"][
                "future_param_gen"
            ],
            "samples": _parameter_samples(parameter_values),
        },
    }
    return compact


def _runtime_samples(entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "active_g": _first(entries, lambda entry: entry.get("active") and entry.get("fec_kind") == "G"),
        "active_gz": _first(entries, lambda entry: entry.get("active") and entry.get("fec_kind") == "GZ"),
        "inactive_tombstone": _first(entries, lambda entry: not entry.get("active")),
    }


def _parameter_samples(entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "active_pass": _first(entries, lambda entry: entry.get("action") == "pass"),
        "loss_drop": _first(entries, lambda entry: entry.get("action") == "drop_loss"),
        "inactive_tombstone": _first(entries, lambda entry: not entry.get("active")),
    }


def _first(entries: list[dict[str, Any]], predicate: Any) -> dict[str, Any] | None:
    sample = next((entry for entry in entries if predicate(entry)), None)
    return copy.deepcopy(sample) if sample is not None else None


def render_text_summary(report: dict[str, Any]) -> str:
    summary = report["compact_summary"]
    audit = report["backend_audit_report"]
    leakage = audit["checks"]["linux_mpls_label_leakage_recorded_not_used_as_intent"][
        "leakage"
    ]
    lines = [
        "Skyseed experimental BPF shadow backend",
        f"backend_mode={summary['backend_mode']}",
        f"input_mode={summary['input_mode']}",
        f"runtime_generation_count={summary['runtime_generation_count']}",
        f"gz_fec_entry_count={summary['gz_fec_entry_count']}",
        f"parameter_generation_count={summary['parameter_generation_count']}",
        f"planned_parameter_epoch_count={summary['planned_parameter_epoch_count']}",
        f"parameter_link_count={summary['parameter_link_count']}",
        f"parameter_entry_count={summary['parameter_entry_count']}",
        f"fail_closed_rule_count={summary['fail_closed_rule_count']}",
        f"fixed_10s_assumed={str(summary['fixed_10s_assumed']).lower()}",
        f"qdisc_timeline_backend_status={summary['qdisc_timeline_backend_status']}",
        f"canonical_runtime_unchanged={str(summary['canonical_runtime_unchanged']).lower()}",
        f"backend_audit_ok={str(audit['ok']).lower()}",
        f"linux_mpls_label_leakage_found={str(leakage['found']).lower()}",
        "linux_mpls_label_leakage_ignored_by_compiler="
        + ",".join(leakage["ignored_by_compiler"]),
    ]
    return "\n".join(lines)


def compile_from_run_dir(
    run_dir: Path,
    *,
    input_mode: str = "existing_artifact",
    active_runtime_generation: int = 0,
    active_parameter_generation: int = 0,
) -> dict[str, Any]:
    artifacts = load_shadow_backend_artifacts(run_dir)
    return compile_bpf_shadow_backend(
        artifacts,
        input_mode=input_mode,
        active_runtime_generation=active_runtime_generation,
        active_parameter_generation=active_parameter_generation,
    )


def load_parameter_artifacts_from_run_dir(run_dir: Path) -> dict[str, Any]:
    """Expose the qdisc artifact loader for callers that need its shape."""

    return load_parameter_artifacts(run_dir)
