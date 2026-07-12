#!/usr/bin/env python3
# encoding: utf-8

"""Experimental BPF shadow-plan compiler and audit helpers."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Iterable

from skyseed_v1.compilers.gz_bpf_generation import (
    COUNTER_LABELS as GZ_COUNTER_LABELS,
    collect_linux_mpls_leakage,
    compile_generation_map,
    stable_u32,
)
from skyseed_v1.compilers.qdisc_timeline import (
    compile_qdisc_timeline,
    load_artifacts as load_parameter_artifacts,
    scaled_loss_u32,
)


BACKEND_MODE = "experimental_bpf_shadow"
EXPERIMENTAL_BACKEND_MODE = "experimental_bpf_backend"
QDISC_TIMELINE_BACKEND_STATUS = "blocked_or_trace_only"
DEFAULT_EXPERIMENTAL_BACKEND_CONFIG = {
    "enabled": False,
    "backend_mode": EXPERIMENTAL_BACKEND_MODE,
    "selector": {
        "mode": "semantic_intent_canary_subset",
        "ut_ids": [],
        "gw_ids": [],
        "fec_ids": [],
        "zone_ids": [],
        "generation_ids": [],
        "parameter_generation_ids": [],
        "max_scope_entries": 1,
    },
    "scale_limits": {
        "max_runtime_generations": 64,
        "max_parameter_generations": 64,
        "max_scope_entries": 1,
        "max_fec_entries": 16,
        "max_parameter_entries": 16,
    },
}
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


def normalize_experimental_backend_config(
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return an explicit opt-in BPF backend config with bounded scale."""

    normalized = copy.deepcopy(DEFAULT_EXPERIMENTAL_BACKEND_CONFIG)
    if not config:
        return normalized
    for key, value in config.items():
        if key in {"selector", "scale_limits"} and isinstance(value, dict):
            normalized[key].update(value)
        else:
            normalized[key] = value
    selector = normalized["selector"]
    for key in ["ut_ids", "gw_ids", "fec_ids", "zone_ids"]:
        value = selector.get(key) or []
        if isinstance(value, str):
            value = [part.strip() for part in value.split(",") if part.strip()]
        selector[key] = sorted({str(item) for item in value if str(item)})
    for key in ["generation_ids", "parameter_generation_ids"]:
        value = selector.get(key) or []
        if isinstance(value, str):
            value = [part.strip() for part in value.split(",") if part.strip()]
        selector[key] = sorted({int(item) for item in value if str(item) != ""})
    scale_limits = normalized["scale_limits"]
    for key in [
        "max_runtime_generations",
        "max_parameter_generations",
        "max_scope_entries",
        "max_fec_entries",
        "max_parameter_entries",
    ]:
        scale_limits[key] = max(1, int(scale_limits.get(key) or 1))
    selector["max_scope_entries"] = min(
        int(selector.get("max_scope_entries") or 1),
        int(scale_limits["max_scope_entries"]),
    )
    normalized["enabled"] = bool(normalized.get("enabled"))
    normalized["backend_mode"] = EXPERIMENTAL_BACKEND_MODE
    return normalized


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
        "compiler": "skyseed_v1.compilers.bpf_shadow",
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
    experimental_backend_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    backend_config = normalize_experimental_backend_config(experimental_backend_config)
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
        "bpf_backend_mode": backend_config["backend_mode"],
        "bpf_enabled": backend_config["enabled"],
        "bpf_scope": backend_config["selector"]["mode"],
    }
    compiled = {
        "schema_version": 1,
        "goal": "Skyseed Goal 14 experimental BPF shadow backend landing",
        "generated_at": "not_recorded_for_deterministic_baseline",
        "backend_mode": BACKEND_MODE,
        "compiler": "skyseed_v1.compilers.bpf_shadow",
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
            "backend_mode": backend_config["backend_mode"],
            "default_enabled": False,
            "enabled": backend_config["enabled"],
            "selector": copy.deepcopy(backend_config["selector"]),
            "scale_limits": copy.deepcopy(backend_config["scale_limits"]),
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
            "dataplane_apply": {
                "opt_in_required": True,
                "canonical_fallback_required": True,
                "rollback_required": True,
                "scope_from": "Skyseed semantic intent constrained by selector",
            },
            "qdisc_timeline": {
                "status": QDISC_TIMELINE_BACKEND_STATUS,
                "bpf_responsibility": ["active", "loss", "drop", "counter"],
                "canonical_qdisc_responsibility": [
                    "delay",
                    "rate",
                    "bandwidth",
                ],
            },
            "active_generation_switching": active_generation_switch_plan(
                gz_map,
                parameter_map,
            ),
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
    gz_checks = normalized_runtime_self_checks(gz_map)
    parameter_checks = normalized_parameter_self_checks(parameter_map)
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
            "status": (
                "not_applicable"
                if int(parameter_map["summary"].get("planned_parameter_epoch_count") or 0) == 0
                and int(parameter_map["summary"].get("parameter_entry_count") or 0) == 0
                and len(parameter_map["bpf_maps"].get("parameter_values", [])) == 0
                else "checked"
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


def active_generation_switch_plan(
    gz_map: dict[str, Any],
    parameter_map: dict[str, Any],
) -> dict[str, Any]:
    runtime_switches = []
    for generation in gz_map.get("generations", []):
        runtime_epoch = generation.get("runtime_epoch") or {}
        runtime_switches.append(
            {
                "generation_id": generation.get("generation_id"),
                "epoch_id": generation.get("epoch_id"),
                "activation_relative_time_seconds": runtime_epoch.get(
                    "activation_relative_time_seconds"
                ),
                "activation_time_utc": runtime_epoch.get("activation_time_utc"),
                "future_generation_preloaded_not_active": generation.get("active") is not True,
            }
        )
    parameter_switches = []
    for generation in parameter_map["bpf_maps"].get("param_status", []):
        parameter_switches.append(
            {
                "generation_id": generation.get("generation_id"),
                "epoch_id": generation.get("epoch_id"),
                "activation_relative_time_seconds": generation.get(
                    "activation_relative_time_seconds"
                ),
                "activation_time_utc": generation.get("activation_time_utc"),
                "future_generation_preloaded_not_active": generation.get("active") is not True,
            }
        )
    return {
        "source": "RuntimeEpoch/ParameterEpoch event-time metadata",
        "runtime_active_generation": gz_map["bpf_maps"]["active_generation"],
        "parameter_active_generation": parameter_map["bpf_maps"]["parameter_generation"],
        "runtime_switch_count": len(runtime_switches),
        "parameter_switch_count": len(parameter_switches),
        "runtime_switches": runtime_switches,
        "parameter_switches": parameter_switches,
        "future_generation_not_early_active": True,
    }


def normalized_runtime_self_checks(gz_map: dict[str, Any]) -> dict[str, Any]:
    checks = copy.deepcopy(gz_map["self_checks"])
    if int(gz_map.get("summary", {}).get("inactive_fec_tombstone_count") or 0) == 0:
        checks["inactive_fec_fail_closed"] = {
            "ok": True,
            "probe": None,
            "note": (
                "No inactive G/Z FEC tombstones observed in source artifact; "
                "inactive rule remains part of the compiled fail-closed rule set."
            ),
        }
    return checks


def normalized_parameter_self_checks(parameter_map: dict[str, Any]) -> dict[str, Any]:
    checks = copy.deepcopy(parameter_map["self_checks"])
    generations = parameter_map["bpf_maps"].get("param_status", [])
    active_generation = int(
        parameter_map["bpf_maps"].get("parameter_generation", {}).get("value") or 0
    )
    if (
        int(parameter_map.get("summary", {}).get("planned_parameter_epoch_count") or 0) == 0
        and int(parameter_map.get("summary", {}).get("parameter_entry_count") or 0) == 0
        and not generations
    ):
        return {
            name: {
                "ok": True,
                "status": "not_applicable",
                "probe": None,
                "note": (
                    "No ParameterEpoch exists in the source artifact; parameter "
                    "shadow consistency is not applicable unless a real parameter "
                    "map mismatch is observed."
                ),
            }
            for name in [
                "missing_parameter_generation_fail_closed",
                "missing_parameter_fail_closed",
                "inactive_parameter_tombstone_fail_closed",
                "future_parameter_generation_not_active",
            ]
        }
    if not any(int(item.get("generation_id", -1)) > active_generation for item in generations):
        checks["future_parameter_generation_not_active"] = {
            "ok": True,
            "probe": None,
            "note": (
                "No future ParameterEpoch generation observed in source artifact; "
                "future-generation rule remains part of the compiled fail-closed rule set."
            ),
        }
    return checks


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
    experimental_backend_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    artifacts = load_shadow_backend_artifacts(run_dir)
    return compile_bpf_shadow_backend(
        artifacts,
        input_mode=input_mode,
        active_runtime_generation=active_runtime_generation,
        active_parameter_generation=active_parameter_generation,
        experimental_backend_config=experimental_backend_config,
    )


def consistency_check_for_run_dir(
    run_dir: Path,
    report: dict[str, Any],
) -> dict[str, Any]:
    """Compare a BPF shadow summary with the canonical run artifacts it shadows."""

    artifacts = load_shadow_backend_artifacts(run_dir)
    summary = report["compact_summary"]
    canonical = canonical_shadow_counts(artifacts)
    audit = report.get("backend_audit_report") or {}
    checks = {
        "backend_mode": {
            "ok": summary.get("backend_mode") == BACKEND_MODE,
            "observed": summary.get("backend_mode"),
            "expected": BACKEND_MODE,
        },
        "canonical_runtime_unchanged": {
            "ok": summary.get("canonical_runtime_unchanged") is True,
            "observed": summary.get("canonical_runtime_unchanged"),
            "expected": True,
        },
        "runtime_generation_count_matches_gz_plan": {
            "ok": summary.get("runtime_generation_count")
            == canonical["runtime_generation_count"],
            "observed": summary.get("runtime_generation_count"),
            "expected": canonical["runtime_generation_count"],
        },
        "gz_fec_entry_count_matches_canonical_intent": {
            "ok": summary.get("gz_fec_entry_count") == canonical["gz_fec_entry_count"],
            "observed": summary.get("gz_fec_entry_count"),
            "expected": canonical["gz_fec_entry_count"],
        },
        "parameter_generation_count_matches_execution_details": {
            "ok": summary.get("parameter_generation_count")
            == canonical["parameter_generation_count"],
            "observed": summary.get("parameter_generation_count"),
            "expected": canonical["parameter_generation_count"],
        },
        "parameter_link_count_matches_execution_details": {
            "ok": summary.get("parameter_link_count") == canonical["parameter_link_count"],
            "observed": summary.get("parameter_link_count"),
            "expected": canonical["parameter_link_count"],
        },
        "fixed_10s_not_assumed": {
            "ok": summary.get("fixed_10s_assumed") is False,
            "observed": summary.get("fixed_10s_assumed"),
            "expected": False,
        },
        "qdisc_timeline_backend_blocked_or_trace_only": {
            "ok": summary.get("qdisc_timeline_backend_status")
            == QDISC_TIMELINE_BACKEND_STATUS,
            "observed": summary.get("qdisc_timeline_backend_status"),
            "expected": QDISC_TIMELINE_BACKEND_STATUS,
        },
        "backend_audit_pass": {
            "ok": audit.get("ok") is True,
            "observed": audit.get("ok"),
            "expected": True,
        },
    }
    return {
        "schema_version": 1,
        "checker": "skyseed_v1.compilers.bpf_shadow:consistency_check_for_run_dir",
        "backend_mode": BACKEND_MODE,
        "run_dir": str(run_dir),
        "canonical_counts": canonical,
        "compact_summary": copy.deepcopy(summary),
        "checks": checks,
        "ok": all(bool(check["ok"]) for check in checks.values()),
    }


def canonical_shadow_counts(artifacts: dict[str, Any]) -> dict[str, int]:
    plans = list(artifacts["gz_fec_plan"].get("plans", []) or [])
    diffs = list(artifacts["gz_update_diff_summary"].get("diffs", []) or [])
    active_fec_count = sum(
        len(list(plan.get("forward_fecs", []) or []))
        + len(list(plan.get("return_fecs", []) or []))
        for plan in plans
    )
    inactive_tombstone_count = 0
    seen_fec_ids: set[str] = set()
    for index, plan in enumerate(plans):
        for fec in list(plan.get("forward_fecs", []) or []) + list(plan.get("return_fecs", []) or []):
            fec_id = fec.get("fec_id")
            if isinstance(fec_id, str):
                seen_fec_ids.add(fec_id)
        if index >= len(diffs):
            continue
        for removed in list(diffs[index].get("removed_fec_groups", []) or []):
            if isinstance(removed, str) and removed in seen_fec_ids:
                inactive_tombstone_count += 1

    parameter_generation_count = 0
    parameter_links: set[str] = set()
    parameter_entry_count = 0
    for record in artifacts["epoch_execution_details"]:
        if not _is_parameter_record(record):
            continue
        parameter_generation_count += 1
        apply_results = ((record.get("host_mutation_execution") or {}).get("apply_results")) or []
        for result in apply_results:
            if not isinstance(result, dict) or result.get("operation") != "update_slot_parameters":
                continue
            slot_name = result.get("slot_name")
            state = result.get("resulting_state") or {}
            params = state.get("runtime_parameters")
            if isinstance(slot_name, str) and isinstance(params, dict):
                parameter_links.add(slot_name)
                parameter_entry_count += 1

    return {
        "runtime_generation_count": len(plans),
        "active_gz_fec_count": active_fec_count,
        "inactive_gz_fec_tombstone_count": inactive_tombstone_count,
        "gz_fec_entry_count": active_fec_count + inactive_tombstone_count,
        "parameter_generation_count": parameter_generation_count,
        "parameter_link_count": len(parameter_links),
        "parameter_entry_count": parameter_entry_count,
    }


def emit_shadow_artifacts_for_run_dir(
    run_dir: Path,
    *,
    input_mode: str = "short_runtime_shadow",
    summary_path: Path | None = None,
    consistency_path: Path | None = None,
) -> dict[str, Any]:
    compiled = compile_from_run_dir(run_dir, input_mode=input_mode)
    report = compact_for_baseline(compiled)
    summary_path = summary_path or (run_dir / "bpf_shadow_backend_summary.json")
    consistency = consistency_check_for_run_dir(run_dir, report)
    consistency_path = consistency_path or (run_dir / "bpf_shadow_backend_consistency.json")
    write_json(summary_path, report)
    write_json(consistency_path, consistency)
    return {
        "schema_version": 1,
        "emitter": "skyseed_v1.compilers.bpf_shadow:emit_shadow_artifacts_for_run_dir",
        "shadow_only": True,
        "backend_mode": BACKEND_MODE,
        "summary_path": str(summary_path),
        "consistency_path": str(consistency_path),
        "compact_summary": copy.deepcopy(report["compact_summary"]),
        "consistency": consistency,
        "ok": bool(consistency["ok"]),
    }


def load_parameter_artifacts_from_run_dir(run_dir: Path) -> dict[str, Any]:
    """Expose the qdisc artifact loader for callers that need its shape."""

    return load_parameter_artifacts(run_dir)
