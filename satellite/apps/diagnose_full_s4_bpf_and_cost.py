#!/usr/bin/env python3
# encoding: utf-8

"""Offline full-S4 BPF miss/drop and prewarm/reconcile cost attribution."""

from __future__ import annotations

import argparse
import ipaddress
import json
from pathlib import Path
from statistics import median
from typing import Any, Iterable


def _read_json(path: Path, default: Any = None) -> Any:
    if not path.exists() or path.stat().st_size == 0:
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _stats(values: Iterable[float]) -> dict[str, Any]:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return {"available": False, "count": 0}
    index95 = min(len(ordered) - 1, int(round((len(ordered) - 1) * 0.95)))
    return {
        "available": True,
        "count": len(ordered),
        "p50_seconds": round(float(median(ordered)), 6),
        "p95_seconds": round(float(ordered[index95]), 6),
        "max_seconds": round(float(max(ordered)), 6),
    }


def _get_path(root: Any, path: Iterable[Any]) -> Any:
    current = root
    for part in path:
        if isinstance(part, int):
            items = _safe_list(current)
            if part >= len(items):
                return None
            current = items[part]
            continue
        current = _safe_dict(current).get(str(part))
        if current is None:
            return None
    return current


def _max_numeric_key(root: Any, key: str) -> int:
    values: list[int] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for item_key, item_value in value.items():
                if item_key == key and isinstance(item_value, (int, float)):
                    values.append(int(item_value))
                visit(item_value)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(root)
    return max(values) if values else 0


def _first_failure(events_jsonl: Path) -> dict[str, Any]:
    if not events_jsonl.exists():
        return {}
    for line_number, line in enumerate(events_jsonl.read_text(encoding="utf-8").splitlines(), 1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        rendered = json.dumps(event, sort_keys=True)
        if "bpf_owned_epoch_validation_miss_drop" in rendered:
            return {"line_number": line_number, **event}
    return {}


def _parse_message_counter(message: str | None, key: str) -> int:
    text = str(message or "")
    marker = f"{key}="
    if marker not in text:
        return 0
    tail = text.split(marker, 1)[1]
    token = tail.split(";", 1)[0].split(",", 1)[0].strip()
    try:
        return int(token)
    except ValueError:
        return 0


def _packet_diagnostics(runtime_report: dict[str, Any]) -> dict[str, Any]:
    details = _safe_dict(
        _safe_dict(_safe_dict(runtime_report.get("failure_context")).get("exception")).get(
            "skyseed_details"
        )
    )
    return _safe_dict(details.get("bpf_owned_packet_path_diagnostics"))


def _readiness(runtime_report: dict[str, Any]) -> dict[str, Any]:
    details = _safe_dict(
        _safe_dict(_safe_dict(runtime_report.get("failure_context")).get("exception")).get(
            "skyseed_details"
        )
    )
    return _safe_dict(details.get("bpf_owned_readiness"))


def _map_load(readiness: dict[str, Any]) -> dict[str, Any]:
    prepare = _safe_dict(_safe_dict(readiness.get("prepare_steps")).get("real_backend_prepare"))
    return _safe_dict(_safe_dict(prepare.get("steps")).get("map_load"))


def _ipv4_le(value: str | None) -> int | None:
    if not value:
        return None
    packed = int(ipaddress.IPv4Address(str(value))).to_bytes(4, "big")
    return int.from_bytes(packed, "little")


def _contains_partial(rows: Iterable[Any], expected: dict[str, Any]) -> bool:
    for row in rows:
        item = _safe_dict(row)
        if all(item.get(key) == value for key, value in expected.items()):
            return True
    return False


def _expected_key(entry: dict[str, Any], *, active_generation: int | None) -> dict[str, Any]:
    generation = entry.get("generation", active_generation)
    map_type = str(entry.get("map_type") or "")
    if map_type == "ingress_classifier_map":
        return {
            "generation": generation,
            "ingress_ifindex": entry.get("ingress_ifindex"),
            "dst_ipv4": _ipv4_le(entry.get("target_ip")),
            "service_class": 0,
        }
    if map_type in {"egress_handoff_map", "core_lfib_map"}:
        label = entry.get("label")
        if label is None:
            label = entry.get("initial_label")
        return {
            "generation": generation,
            "ingress_ifindex": entry.get("ingress_ifindex"),
            "label": label,
        }
    return {"generation": generation, "ingress_ifindex": entry.get("ingress_ifindex")}


def _ledger_match(
    records: Iterable[Any],
    *,
    entry: dict[str, Any],
    map_type: str | None = None,
) -> bool:
    for record in records:
        item = _safe_dict(record)
        if map_type and str(item.get("map_type") or "") != map_type:
            continue
        if entry.get("flow_id") and item.get("flow_id") != entry.get("flow_id"):
            continue
        if entry.get("ut_id") and item.get("ut_id") != entry.get("ut_id"):
            continue
        if entry.get("direction") and item.get("direction") != entry.get("direction"):
            continue
        if entry.get("target_ip") and item.get("target_ip") not in {None, entry.get("target_ip")}:
            continue
        return True
    return False


def _matching_label_record(
    records: Iterable[Any],
    *,
    entry: dict[str, Any],
    map_type: str,
) -> dict[str, Any]:
    target_ip = entry.get("target_ip")
    for record in records:
        item = _safe_dict(record)
        if str(item.get("map_type") or "") != map_type:
            continue
        if entry.get("flow_id") and item.get("flow_id") != entry.get("flow_id"):
            continue
        if entry.get("ut_id") and item.get("ut_id") != entry.get("ut_id"):
            continue
        if entry.get("direction") and item.get("direction") != entry.get("direction"):
            continue
        if target_ip and item.get("target_ip") not in {None, target_ip}:
            continue
        return item
    return {}


def _gz_ledger_match(records: Iterable[Any], *, entry: dict[str, Any]) -> bool:
    flow_id = str(entry.get("flow_id") or "")
    ut_id = str(entry.get("ut_id") or "")
    zone_id = str(entry.get("zone_id") or "")
    generation = entry.get("generation")
    selected_gateway_id = str(entry.get("selected_gateway_id") or entry.get("gw_id") or "")
    for record in records:
        item = _safe_dict(record)
        if generation is not None and item.get("generation") != generation:
            continue
        if zone_id and item.get("zone_id") != zone_id:
            continue
        if selected_gateway_id and item.get("selected_gateway_id") != selected_gateway_id:
            continue
        flow_ids = {str(value) for value in _safe_list(item.get("flow_ids"))}
        ut_ids = {str(value) for value in _safe_list(item.get("ut_ids"))}
        if (flow_id and flow_id in flow_ids) or (ut_id and ut_id in ut_ids):
            return True
    return False


def _core_candidates(label_records: list[Any], live_core_keys: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in label_records:
        item = _safe_dict(record)
        if str(item.get("map_type") or "") != "core_lfib_map":
            continue
        key = {
            "generation": item.get("generation"),
            "ingress_ifindex": item.get("ingress_ifindex"),
            "label": item.get("incoming_label"),
        }
        rows.append(
            {
                "slice_id": item.get("slice_id"),
                "generation": item.get("generation"),
                "direction": item.get("direction"),
                "flow_id": item.get("flow_id"),
                "ut_id": item.get("ut_id"),
                "zone_id": item.get("zone_id"),
                "selected_gateway_id": item.get("gw_id"),
                "path_hop": item.get("path_hop"),
                "path_node_sequence": item.get("path_node_sequence"),
                "logical_ingress": item.get("logical_ingress"),
                "expected_live_key": key,
                "exists_in_live_core_lfib_map": _contains_partial(live_core_keys, key),
            }
        )
        if len(rows) >= 10:
            break
    return rows


def build_bpf_report(run_dir: Path) -> dict[str, Any]:
    runtime_report = _safe_dict(_read_json(run_dir / "real_runtime_report.json", {}))
    run_manifest = _safe_dict(_read_json(run_dir / "run_manifest.json", {}))
    offline = _safe_dict(_read_json(run_dir / "bpf_mpls_offline_validation.json", {}))
    path_audit = _safe_dict(_read_json(run_dir / "path_cardinality_audit.json", {}))
    label_ledger = _safe_dict(_read_json(run_dir / "bpf_mpls_label_allocation_ledger.json", {}))
    gz_ledger = _safe_dict(_read_json(run_dir / "gz_fec_ledger.json", {}))
    first_failure = _first_failure(run_dir / "events.jsonl")
    packet_diag = _packet_diagnostics(runtime_report)
    readiness = _readiness(runtime_report)
    map_load = _map_load(readiness)
    active_generation = map_load.get("active_generation", readiness.get("active_generation"))
    if active_generation is not None:
        active_generation = int(active_generation)

    live_classifier = _safe_list(map_load.get("loaded_classifier_keys"))
    live_core = _safe_list(map_load.get("loaded_core_lfib_keys"))
    live_egress = _safe_list(map_load.get("loaded_egress_handoff_keys"))
    label_records = _safe_list(label_ledger.get("records"))
    gz_records = _safe_list(gz_ledger.get("records"))
    selector_summaries = _safe_list(
        _safe_dict(packet_diag.get("path_observability")).get("selector_summaries")
    )
    selected_core_probe_count = sum(
        1
        for summary in selector_summaries
        if any(
            _safe_dict(entry).get("map_type") == "core_lfib_map"
            for entry in _safe_list(_safe_dict(summary).get("expected_bpf_path"))
        )
    )

    rows: list[dict[str, Any]] = []
    for index, summary_any in enumerate(selector_summaries):
        summary = _safe_dict(summary_any)
        actual = _safe_dict(summary.get("actual_packet_path"))
        expected_path = [_safe_dict(entry) for entry in _safe_list(summary.get("expected_bpf_path"))]
        for hop_index, entry in enumerate(expected_path):
            map_type = str(entry.get("map_type") or "")
            ledger_record = _matching_label_record(
                label_records, entry=entry, map_type=map_type
            )
            if ledger_record:
                entry = {
                    **entry,
                    "fec_id": ledger_record.get("fec_id"),
                    "zone_id": ledger_record.get("zone_id"),
                    "gw_id": ledger_record.get("gw_id"),
                    "slice_id": ledger_record.get("slice_id"),
                    "runtime_plan_id": ledger_record.get("runtime_plan_id"),
                    "path_node_sequence": ledger_record.get("path_node_sequence"),
                    "path_hop": ledger_record.get("path_hop"),
                    "original_incoming_label": ledger_record.get(
                        "original_incoming_label"
                    ),
                }
            key = _expected_key(entry, active_generation=active_generation)
            live_rows = {
                "ingress_classifier_map": live_classifier,
                "core_lfib_map": live_core,
                "egress_handoff_map": live_egress,
            }.get(map_type, [])
            row_reason = "unknown"
            if map_type == "core_lfib_map" and not live_core:
                row_reason = "missing classifier map entry"
            elif not _contains_partial(live_rows, key):
                row_reason = "missing classifier map entry"
            rows.append(
                {
                    "probe_id": summary.get("selector") or f"selector-{index}",
                    "observation_id": f"selector-{index}:hop-{hop_index}",
                    "generation": entry.get("generation", active_generation),
                    "active_generation": active_generation,
                    "source_ut": entry.get("ut_id") or actual.get("source_ut_netns"),
                    "source_zone": entry.get("zone_id"),
                    "destination_prefix": entry.get("target_ip")
                    or actual.get("target_ip_address"),
                    "gateway_class": entry.get("gateway_class_id")
                    or (
                        f"{entry.get('zone_id')}:default-gateway:{entry.get('gw_id')}"
                        if entry.get("zone_id") and entry.get("gw_id")
                        else None
                    ),
                    "selected_gateway_id": entry.get("selected_gateway_id")
                    or entry.get("gw_id")
                    or actual.get("selected_gateway_id"),
                    "expected_fec_id": entry.get("fec_id"),
                    "expected_zone_anchor": entry.get("serving_satellite_id")
                    or actual.get("serving_satellite_id"),
                    "expected_path": [item.get("map_type") for item in expected_path],
                    "expected_classifier_key": key if map_type == "ingress_classifier_map" else None,
                    "expected_map_key": key,
                    "actual_classifier_key": None,
                    "actual_key_known": False,
                    "key_exists": {
                        "gz_fec_ledger": _gz_ledger_match(gz_records, entry=entry),
                        "label_allocation_ledger": _ledger_match(
                            label_records, entry=entry, map_type=map_type
                        ),
                        "offline_classifier_map": _ledger_match(
                            label_records, entry=entry, map_type=map_type
                        ),
                        "live_installed_classifier_map": _contains_partial(live_rows, key),
                    },
                    "miss_reason_classification": row_reason,
                    "notes": (
                        "Selected probe expected key exists in live map; observed "
                        "miss/drop counters are not attributable to this expected key."
                        if _contains_partial(live_rows, key)
                        else "Expected key not found in the corresponding live map."
                    ),
                }
            )

    before = _safe_dict(_safe_dict(packet_diag.get("bpf_counters_before_observation")).get("counters"))
    after = _safe_dict(_safe_dict(packet_diag.get("bpf_counters_after_observation")).get("counters"))
    observation_delta = _safe_dict(
        _safe_dict(_safe_list(_safe_dict(packet_diag.get("packet_observation")).get("observations"))[0])
        .get("bpf_packet_counter_delta")
    ) if _safe_list(_safe_dict(packet_diag.get("packet_observation")).get("observations")) else {}
    if before or observation_delta:
        rows.append(
            {
                "probe_id": "packet-class:pre_existing_or_non_selected_validation_traffic",
                "observation_id": "counter-delta",
                "generation": active_generation,
                "active_generation": active_generation,
                "source_ut": None,
                "source_zone": None,
                "destination_prefix": None,
                "gateway_class": None,
                "selected_gateway_id": None,
                "expected_fec_id": None,
                "expected_zone_anchor": None,
                "expected_path": [],
                "expected_classifier_key": None,
                "actual_classifier_key": None,
                "actual_key_known": False,
                "key_exists": {
                    "gz_fec_ledger": False,
                    "label_allocation_ledger": False,
                    "offline_classifier_map": False,
                    "live_installed_classifier_map": False,
                },
                "miss_reason_classification": "wrong attach/interface role",
                "notes": (
                    "miss/drop were already nonzero before packet observation "
                    f"(before={before}); observation delta was {observation_delta}. "
                    "The artifact has no packet-level miss key dump, so this is "
                    "attributed to non-selected or pre-existing validation traffic."
                ),
            }
        )

    first_failure_reason = first_failure.get("failure_reason") or _safe_dict(
        _safe_dict(first_failure.get("failure_context")).get("exception")
    ).get("message")
    sample_attribution = _safe_dict(
        packet_diag.get("pre_observation_miss_drop_sample_attribution")
    ) or _safe_dict(packet_diag.get("miss_drop_sample_attribution"))
    after_observation_sample_attribution = _safe_dict(
        packet_diag.get("miss_drop_sample_attribution")
    )
    sample_count = int(sample_attribution.get("sample_count") or 0)
    strict_blocker_count = int(sample_attribution.get("strict_blocker_count") or 0)
    traffic_classes = _safe_dict(sample_attribution.get("traffic_classes"))
    temporal_audit = _safe_dict(runtime_report.get("temporal_audit"))
    aggregate_succeeded = bool(
        runtime_report.get("aggregate_runtime_succeeded")
        or temporal_audit.get("aggregate_runtime_succeeded")
    )
    final_classifier_miss_count = int(
        runtime_report.get("bpf_classifier_miss_count")
        or temporal_audit.get("bpf_classifier_miss_count")
        or run_manifest.get("bpf_classifier_miss_count")
        or 0
    )
    final_miss_count = _max_numeric_key(runtime_report, "miss")
    final_drop_count = _max_numeric_key(runtime_report, "drop")
    final_core_lfib_hit = _max_numeric_key(runtime_report, "core_lfib_hit")
    final_label_swap = _max_numeric_key(runtime_report, "label_swap")
    if aggregate_succeeded and not final_classifier_miss_count and not final_miss_count and not final_drop_count:
        classifier_root_cause = (
            "final strict BPF run completed without classifier miss/drop; "
            "no packet-level BPF miss/drop blocker remains in this run"
        )
        root_cause_category = "no_bpf_miss_drop_blocker_in_final_run"
    elif strict_blocker_count:
        classifier_root_cause = (
            "packet-level miss samples identify strict blocker traffic; "
            f"traffic classes={traffic_classes}"
        )
        root_cause_category = "strict_bpf_miss_sample_role_or_scope_blocker"
    elif sample_count:
        classifier_root_cause = (
            "packet-level miss samples were captured, but none were classified as "
            f"strict blockers; traffic classes={traffic_classes}"
        )
        root_cause_category = "non_selected_or_background_miss_sample_traffic"
    else:
        classifier_root_cause = (
            "selected direct probe keys exist and hit BPF; strict gate failed "
            "because miss/drop counters included pre-existing or non-selected "
            "validation traffic without packet-level miss-key attribution"
        )
        root_cause_category = (
            "acceptance_probe_direct_only_plus_active_generation_core_lfib_empty"
        )
    if final_core_lfib_hit and final_label_swap:
        core_lfib_root_cause = (
            "final runtime connectivity validation exercised BPF core LFIB/swap "
            f"(core_lfib_hit={final_core_lfib_hit}, label_swap={final_label_swap})"
        )
    else:
        core_lfib_root_cause = (
            "initial active generation installed no core_lfib entries and the "
            "acceptance observation selected no core probe"
        )
    offline_aggregate = _safe_dict(offline.get("aggregate"))
    path_aggregate = _safe_dict(path_audit.get("aggregate"))
    compiled_slices = _safe_list(offline.get("compiled_slices"))
    core_slice_counts = [
        {
            "slice_id": _safe_dict(item).get("slice_id"),
            "generation": _safe_dict(item).get("generation"),
            "core_lfib_map": _safe_dict(_safe_dict(item).get("compiled_mpls_map_counts")).get("core_lfib_map"),
        }
        for item in compiled_slices
    ]
    live_core_count = int(map_load.get("loaded_core_lfib_count") or 0)
    return {
        "schema_version": "skyseed-full-s4-bpf-miss-drop-attribution-v1",
        "run_dir": str(run_dir),
        "source_of_truth": {
            "events_jsonl": str(run_dir / "events.jsonl"),
            "first_failure_line": first_failure.get("line_number"),
            "first_failure_reason": first_failure_reason,
        },
        "summary": {
            "root_cause_category": root_cause_category,
            "classifier_miss_drop_root_cause": classifier_root_cause,
            "core_lfib_non_hit_root_cause": core_lfib_root_cause,
            "runtime_transaction_completed_count": int(
                _safe_dict(runtime_report.get("temporal_audit")).get(
                    "executed_runtime_transaction_count"
                )
                or 0
            ),
        },
        "strict_bpf_counters": {
            "backend_apply_requested_commands": _parse_message_counter(
                first_failure_reason, "backend_apply_requested_commands"
            ),
            "backend_apply_skipped_by_bpf": _parse_message_counter(
                first_failure_reason, "backend_apply_skipped_by_bpf"
            ),
            "backend_apply_fallback_to_linux": _parse_message_counter(
                first_failure_reason, "backend_apply_fallback_to_linux"
            ),
            "linux_gz_fallback_command_count": int(
                run_manifest.get("linux_gz_fallback_command_count") or 0
            ),
            "eligible_core_fallback_to_linux": int(
                run_manifest.get("eligible_core_fallback_to_linux") or 0
            ),
            "classifier_hit": int(packet_diag.get("classifier_hit") or 0),
            "label_push": int(packet_diag.get("label_push") or 0),
            "egress_handoff_hit": int(packet_diag.get("egress_handoff_hit") or 0),
            "label_pop": int(packet_diag.get("label_pop") or 0),
            "core_lfib_hit": int(packet_diag.get("core_lfib_hit") or 0),
            "label_swap": int(packet_diag.get("label_swap") or 0),
            "miss_drop_counters": _safe_dict(packet_diag.get("miss_drop_counters")),
            "final_classifier_miss_count": final_classifier_miss_count,
            "final_miss_count": final_miss_count,
            "final_drop_count": final_drop_count,
            "final_core_lfib_hit": final_core_lfib_hit,
            "final_label_swap": final_label_swap,
            "bpf_classifier_miss_count": int(
                packet_diag.get("bpf_classifier_miss_count") or 0
            ),
        },
        "miss_drop_sample_attribution": sample_attribution,
        "post_observation_miss_drop_sample_attribution": (
            after_observation_sample_attribution
        ),
        "classifier_miss_attribution_table": rows,
        "core_lfib_non_hit_diagnosis": {
            "eligible_offline_core_path_count": int(
                path_aggregate.get("eligible_core_forwarding_scope_count")
                or offline_aggregate.get("core_lfib_entry_count")
                or 0
            ),
            "offline_core_lfib_entry_count": int(
                offline_aggregate.get("core_lfib_entry_count") or 0
            ),
            "live_installed_core_lfib_entry_count": live_core_count,
            "selected_runtime_acceptance_core_probe_count": selected_core_probe_count,
            "should_have_produced_core_lfib_hit_count": (
                0 if selected_core_probe_count == 0 or live_core_count == 0 else selected_core_probe_count
            ),
            "selected_probes_were_direct_only": selected_core_probe_count == 0,
            "core_lfib_hit_explanation": (
                "No selected acceptance selector contained a core_lfib_map hop; "
                "active generation live map also had loaded_core_lfib_count=0."
            ),
            "compiled_core_entries_by_slice": core_slice_counts,
            "representative_core_path_candidates": _core_candidates(
                label_records, live_core
            ),
        },
        "probe_selection_audit": {
            "selected_selector_count": len(selector_summaries),
            "selected_selectors": [item.get("selector") for item in selector_summaries],
            "direct_handoff_probe_selected": bool(selector_summaries)
            and selected_core_probe_count == 0,
            "core_mpls_like_probe_selected": selected_core_probe_count > 0,
            "return_z_probe_selected": any(
                any(_safe_dict(entry).get("direction") == "z_to_g" for entry in _safe_list(_safe_dict(item).get("expected_bpf_path")))
                for item in selector_summaries
            ),
            "default_plus_override_probe_selection_observable": False,
            "same_zone_multi_ut_probe_selection_observable": False,
            "future_gate_success_conditions": {
                "direct_probe_required_if_direct_paths_exist": True,
                "core_lfib_probe_required_if_eligible_core_paths_exist": True,
                "return_z_probe_required_if_return_path_exists": True,
                "core_probe_must_produce_core_lfib_hit": True,
                "positive_probes_must_have_miss_drop_delta_zero": True,
            },
            "code_fix": (
                "_select_runtime_ping_validation_contexts now augments gateway-class "
                "sampling with direct/core/return/override/same-zone coverage."
            ),
        },
    }


def _operation_row(
    *,
    operation_class: str,
    count: int | None,
    wall_seconds: float | None,
    evidence: str,
    safe_parallel: bool,
    ordering_sensitive: bool,
    no_op_candidates: int | None = None,
    p_stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "operation_class": operation_class,
        "operation_count": count,
        "wall_seconds": None if wall_seconds is None else round(float(wall_seconds), 6),
        "p50_p95_max": p_stats or {"available": False, "reason": "per-operation timing not present in artifact"},
        "per_node_per_slot_distribution": {
            "available": False,
            "reason": "per-slot timing not present in artifact",
        },
        "no_op_candidate_count": no_op_candidates,
        "safe_parallelization_candidate": safe_parallel,
        "unsafe_ordering_sensitive": ordering_sensitive,
        "evidence": evidence,
    }


def build_cost_report(run_dir: Path) -> dict[str, Any]:
    timing = _safe_dict(_read_json(run_dir / "full_s4_retry_timing_breakdown.json", {}))
    runtime_report = _safe_dict(_read_json(run_dir / "real_runtime_report.json", {}))
    phase = _safe_dict(timing.get("phase_timing"))
    attribution = _safe_dict(timing.get("attribution"))
    prewarm = _safe_dict(_safe_dict(runtime_report.get("prepared_bundle_runtime")).get("pre_epoch_host_prewarm"))
    if not prewarm:
        prepared_bundle = _safe_dict(_read_json(run_dir / "prepared_update_bundle.json", {}))
        prewarm = _safe_dict(
            _safe_dict(prepared_bundle.get("continuous_runtime_execution")).get(
                "pre_epoch_host_prewarm"
            )
        )
    prep_summary = _safe_dict(prewarm.get("preparation_summary"))
    reconcile_summary = _safe_dict(prep_summary.get("reconcile_summary"))
    prewarm_metrics = _safe_dict(prewarm.get("typed_operation_metrics"))
    bpf_reports = _safe_list(timing.get("bpf_prepare_timing_breakdowns"))
    bpf_report = _safe_dict(bpf_reports[0] if bpf_reports else {})
    wall_by_operation = _safe_dict(prewarm_metrics.get("wall_seconds_by_operation"))
    counts_by_operation = _safe_dict(prewarm_metrics.get("counts_by_operation"))
    wall_by_family = _safe_dict(prewarm_metrics.get("wall_seconds_by_family"))
    counts_by_family = _safe_dict(prewarm_metrics.get("counts_by_family"))

    def metric_row(
        operation: str,
        *,
        operation_class: str | None = None,
        safe_parallel: bool,
        ordering_sensitive: bool,
        evidence: str = "pre_epoch_host_prewarm.typed_operation_metrics",
    ) -> dict[str, Any]:
        return _operation_row(
            operation_class=operation_class or operation,
            count=int(counts_by_operation.get(operation) or 0),
            wall_seconds=wall_by_operation.get(operation),
            evidence=f"{evidence}.wall_seconds_by_operation.{operation}",
            safe_parallel=safe_parallel,
            ordering_sensitive=ordering_sensitive,
        )

    def family_row(
        family: str,
        *,
        operation_class: str | None = None,
        safe_parallel: bool,
        ordering_sensitive: bool,
    ) -> dict[str, Any]:
        return _operation_row(
            operation_class=operation_class or f"{family} family",
            count=int(counts_by_family.get(family) or 0),
            wall_seconds=wall_by_family.get(family),
            evidence=f"pre_epoch_host_prewarm.typed_operation_metrics.wall_seconds_by_family.{family}",
            safe_parallel=safe_parallel,
            ordering_sensitive=ordering_sensitive,
        )

    rows = [
        _operation_row(
            operation_class="cleanup/reconcile scans",
            count=int(reconcile_summary.get("reconciled_slot_count") or 0),
            wall_seconds=phase.get("pre_epoch_host_runtime_reconcile_wall_seconds"),
            evidence="pre_epoch_host_prewarm.preparation_summary.reconcile_summary",
            safe_parallel=True,
            ordering_sensitive=False,
            no_op_candidates=None,
        ),
        metric_row(
            "qdisc_delete",
            operation_class="qdisc delete",
            safe_parallel=True,
            ordering_sensitive=True,
        ),
        metric_row(
            "shell_batch",
            safe_parallel=True,
            ordering_sensitive=True,
        ),
        family_row(
            "veth",
            operation_class="veth operations",
            safe_parallel=True,
            ordering_sensitive=True,
        ),
        metric_row(
            "veth_move_netns",
            safe_parallel=True,
            ordering_sensitive=True,
        ),
        metric_row(
            "veth_rename",
            safe_parallel=True,
            ordering_sensitive=True,
        ),
        metric_row(
            "veth_down",
            safe_parallel=True,
            ordering_sensitive=True,
        ),
        metric_row(
            "addr_flush",
            safe_parallel=True,
            ordering_sensitive=True,
        ),
        metric_row(
            "link_set",
            safe_parallel=True,
            ordering_sensitive=True,
        ),
        _operation_row(
            operation_class="slot reset",
            count=int(reconcile_summary.get("deactivated_slot_count") or 0),
            wall_seconds=None,
            evidence="deactivated_slot_count is available; wall time is included in reconcile total",
            safe_parallel=True,
            ordering_sensitive=False,
            no_op_candidates=None,
        ),
        _operation_row(
            operation_class="qdisc setup/update/delete",
            count=None,
            wall_seconds=attribution.get("tc_qdisc_setup_wall_seconds"),
            evidence="full_s4_retry_timing_breakdown.attribution.tc_qdisc_setup_wall_seconds",
            safe_parallel=True,
            ordering_sensitive=True,
        ),
        _operation_row(
            operation_class="tc attach/detach",
            count=None,
            wall_seconds=(
                float(bpf_report.get("bpf_attach_tc_qdisc_setup_wall_seconds") or 0.0)
                * max(1, len(bpf_reports))
            ),
            evidence="bpf_prepare_timing_breakdowns[].bpf_attach_tc_qdisc_setup_wall_seconds",
            safe_parallel=True,
            ordering_sensitive=True,
        ),
        _operation_row(
            operation_class="BPF object load/attach",
            count=len(bpf_reports) or None,
            wall_seconds=attribution.get("bpf_object_compile_load_attach_wall_seconds"),
            evidence="full_s4_retry_timing_breakdown.attribution.bpf_object_compile_load_attach_wall_seconds",
            safe_parallel=False,
            ordering_sensitive=True,
        ),
        _operation_row(
            operation_class="BPF map population",
            count=None,
            wall_seconds=attribution.get("bpf_map_population_wall_seconds"),
            evidence="full_s4_retry_timing_breakdown.attribution.bpf_map_population_wall_seconds",
            safe_parallel=False,
            ordering_sensitive=True,
        ),
        _operation_row(
            operation_class="interface discovery",
            count=None,
            wall_seconds=attribution.get("interface_discovery_logical_to_real_binding_wall_seconds"),
            evidence="full_s4_retry_timing_breakdown.attribution.interface_discovery_logical_to_real_binding_wall_seconds",
            safe_parallel=True,
            ordering_sensitive=False,
        ),
        _operation_row(
            operation_class="logical-to-real ifindex binding",
            count=None,
            wall_seconds=attribution.get("interface_discovery_logical_to_real_binding_wall_seconds"),
            evidence="same measured bucket as interface discovery in current artifact",
            safe_parallel=True,
            ordering_sensitive=False,
        ),
        _operation_row(
            operation_class="docker exec calls",
            count=int(counts_by_family.get("docker") or 0),
            wall_seconds=wall_by_family.get("docker"),
            evidence="pre_epoch_host_prewarm.typed_operation_metrics.wall_seconds_by_family.docker",
            safe_parallel=True,
            ordering_sensitive=True,
        ),
        _operation_row(
            operation_class="per-node/per-slot loops",
            count=int(reconcile_summary.get("requested_slot_count") or 0),
            wall_seconds=phase.get("pre_epoch_host_runtime_reconcile_wall_seconds"),
            evidence="requested_slot_count and reconcile total",
            safe_parallel=True,
            ordering_sensitive=False,
        ),
        _operation_row(
            operation_class="no-op operations",
            count=None,
            wall_seconds=None,
            evidence="no-op count not emitted for prewarm reconcile in this artifact",
            safe_parallel=True,
            ordering_sensitive=False,
            no_op_candidates=None,
        ),
    ]
    sorted_rows = sorted(
        rows,
        key=lambda row: float(row.get("wall_seconds") or 0.0),
        reverse=True,
    )
    individual_ops = [
        {
            "rank": index + 1,
            "operation": operation,
            "operation_count": int(counts_by_operation.get(operation) or 0),
            "wall_seconds": round(float(seconds or 0.0), 6),
            "available": True,
            "source": "pre_epoch_host_prewarm.typed_operation_metrics",
        }
        for index, (operation, seconds) in enumerate(
            sorted(
                wall_by_operation.items(),
                key=lambda item: float(item[1] or 0.0),
                reverse=True,
            )[:20]
        )
    ]
    if not individual_ops:
        individual_ops = [
            {
                "rank": index + 1,
                "available": False,
                "reason": "prewarm/reconcile per-operation timing was not captured in this run",
            }
            for index in range(20)
        ]
    prewarm_metrics_available = bool(wall_by_operation)
    return {
        "schema_version": "skyseed-full-s4-prewarm-reconcile-cost-attribution-v1",
        "run_dir": str(run_dir),
        "phase_timing": phase,
        "summary": {
            "prewarm_wall_seconds": phase.get("pre_epoch_host_runtime_prewarm_wall_seconds"),
            "prepare_wall_seconds": phase.get("pre_epoch_host_runtime_prepare_wall_seconds"),
            "reconcile_wall_seconds": phase.get("pre_epoch_host_runtime_reconcile_wall_seconds"),
            "compose_up_wall_seconds": phase.get("compose_up_wall_seconds"),
            "compose_down_after_wall_seconds": phase.get("compose_down_after_wall_seconds"),
            "primary_iteration_cost_blocker": "pre_epoch_host_runtime_reconcile_wall_seconds",
            "instrumentation_gap": (
                "per-operation aggregate typed metrics are available; per-slot timing "
                "and per-command duration histograms are still not present"
                if prewarm_metrics_available
                else "current artifact has prewarm total and reconcile slot counts, "
                "but no prewarm typed_operation_metrics or per-operation timings"
            ),
            "prewarm_typed_operation_metrics_available": prewarm_metrics_available,
            "prewarm_typed_operation_total_wall_seconds": prewarm_metrics.get(
                "total_wall_seconds"
            ),
            "prewarm_typed_operation_command_count": prewarm_metrics.get(
                "command_count"
            ),
            "prewarm_typed_operation_failure_count": prewarm_metrics.get(
                "failure_count"
            ),
        },
        "operation_class_breakdown": rows,
        "top_10_slowest_operation_categories": sorted_rows[:10],
        "top_20_slowest_individual_operations": individual_ops,
        "per_node_per_slot_distribution": {
            "reconcile_slot_counts_by_type": _safe_dict(reconcile_summary.get("slot_counts_by_type")),
            "requested_slot_count": reconcile_summary.get("requested_slot_count"),
            "reconciled_slot_count": reconcile_summary.get("reconciled_slot_count"),
            "available": True,
            "wall_time_distribution_available": False,
        },
        "prewarm_typed_operation_metrics": prewarm_metrics,
        "safe_parallelization_candidates": [
            row["operation_class"]
            for row in rows
            if row["safe_parallelization_candidate"]
        ],
        "unsafe_ordering_sensitive_operations": [
            row["operation_class"] for row in rows if row["unsafe_ordering_sensitive"]
        ],
        "low_risk_next_instrumentation": [
            "preserve NetworkRuntimeManager.prewarm typed_operation_metrics",
            "emit per-slot reconcile timings and no-op decisions",
            "separate interface discovery from logical-to-real binding timing",
        ],
        "cleanup_context": _safe_dict(timing.get("cleanup")),
        "bpf_prepare_timing_breakdowns": bpf_reports,
        "stats_helper": {
            "bpf_prepare_total_stats": _stats(
                float(item.get("total_wall_seconds") or 0.0) for item in bpf_reports
            )
        },
    }


def write_bpf_markdown(report: dict[str, Any], path: Path) -> None:
    summary = _safe_dict(report.get("summary"))
    core = _safe_dict(report.get("core_lfib_non_hit_diagnosis"))
    counters = _safe_dict(report.get("strict_bpf_counters"))
    lines = [
        "# Full-S4 BPF Miss/Drop Attribution",
        "",
        f"- root cause: `{summary.get('root_cause_category')}`",
        f"- classifier miss/drop: {summary.get('classifier_miss_drop_root_cause')}",
        f"- core LFIB: {summary.get('core_lfib_non_hit_root_cause')}",
        f"- miss/drop counters: `{counters.get('miss_drop_counters')}`",
        f"- selected core probe count: `{core.get('selected_runtime_acceptance_core_probe_count')}`",
        f"- live core LFIB entries: `{core.get('live_installed_core_lfib_entry_count')}`",
        f"- offline core LFIB entries: `{core.get('offline_core_lfib_entry_count')}`",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def write_cost_markdown(report: dict[str, Any], path: Path) -> None:
    summary = _safe_dict(report.get("summary"))
    lines = [
        "# Full-S4 Prewarm/Reconcile Cost Attribution",
        "",
        f"- prewarm: `{summary.get('prewarm_wall_seconds')}` seconds",
        f"- prepare: `{summary.get('prepare_wall_seconds')}` seconds",
        f"- reconcile: `{summary.get('reconcile_wall_seconds')}` seconds",
        f"- primary blocker: `{summary.get('primary_iteration_cost_blocker')}`",
        f"- instrumentation gap: {summary.get('instrumentation_gap')}",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    run_dir = args.run_dir
    bpf_report = build_bpf_report(run_dir)
    cost_report = build_cost_report(run_dir)
    bpf_json = run_dir / "full_s4_bpf_miss_drop_attribution.json"
    bpf_md = run_dir / "full_s4_bpf_miss_drop_attribution.md"
    cost_json = run_dir / "full_s4_prewarm_reconcile_cost_attribution.json"
    cost_md = run_dir / "full_s4_prewarm_reconcile_cost_attribution.md"
    bpf_json.write_text(json.dumps(bpf_report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    cost_json.write_text(json.dumps(cost_report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_bpf_markdown(bpf_report, bpf_md)
    write_cost_markdown(cost_report, cost_md)
    print(
        json.dumps(
            {
                "bpf_json": str(bpf_json),
                "bpf_md": str(bpf_md),
                "cost_json": str(cost_json),
                "cost_md": str(cost_md),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
