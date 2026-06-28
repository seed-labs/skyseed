#!/usr/bin/env python3
# encoding: utf-8

"""Build an offline diagnosis report for strict BPF-owned live miss/drop failures."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _read_json(path: Path, default: Any = None) -> Any:
    if not path.exists() or path.stat().st_size == 0:
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _read_first_failure(events_jsonl: Path) -> dict[str, Any]:
    if not events_jsonl.exists():
        return {}
    for line_number, line in enumerate(events_jsonl.read_text(encoding="utf-8").splitlines(), 1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("failure_reason") or event.get("event_type") in {
            "runtime_exception",
            "runtime_failed",
            "runtime_failure",
        }:
            return {"line_number": line_number, **dict(event)}
    return {}


def _safe_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _packet_path_diagnostics(runtime_report: dict[str, Any]) -> dict[str, Any]:
    failure_context = _safe_dict(runtime_report.get("failure_context"))
    exception = _safe_dict(failure_context.get("exception"))
    details = _safe_dict(exception.get("skyseed_details"))
    return _safe_dict(details.get("bpf_owned_packet_path_diagnostics"))


def _readiness(runtime_report: dict[str, Any]) -> dict[str, Any]:
    failure_context = _safe_dict(runtime_report.get("failure_context"))
    exception = _safe_dict(failure_context.get("exception"))
    details = _safe_dict(exception.get("skyseed_details"))
    return _safe_dict(details.get("bpf_owned_readiness"))


def _map_load(readiness: dict[str, Any]) -> dict[str, Any]:
    prepare = _safe_dict(_safe_dict(readiness.get("prepare_steps")).get("real_backend_prepare"))
    return _safe_dict(_safe_dict(prepare.get("steps")).get("map_load"))


def _expected_path_has_core(selector_summary: dict[str, Any]) -> bool:
    for entry in _safe_list(selector_summary.get("expected_bpf_path")):
        if _safe_dict(entry).get("map_type") == "core_lfib_map":
            return True
    return False


def _miss_drop_attribution(
    *,
    packet_diagnostics: dict[str, Any],
    active_generation: int | None,
    loaded_keys: dict[str, list[Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    path_observability = _safe_dict(packet_diagnostics.get("path_observability"))
    selector_summaries = _safe_list(path_observability.get("selector_summaries"))
    miss_drop = _safe_dict(packet_diagnostics.get("miss_drop_counters"))
    for index, selector_summary in enumerate(selector_summaries):
        actual = _safe_dict(selector_summary.get("actual_packet_path"))
        expected_path = _safe_list(selector_summary.get("expected_bpf_path"))
        first_expected = _safe_dict(expected_path[0] if expected_path else {})
        first_missing = _safe_dict(packet_diagnostics.get("first_missing_observation_point"))
        rows.append(
            {
                "probe_id": selector_summary.get("selector") or f"selector-{index}",
                "packet_id": f"observation-{index}",
                "generation": first_expected.get("generation", active_generation),
                "active_generation_at_validation_time": active_generation,
                "direction": first_expected.get("direction"),
                "src_ut": first_expected.get("ut_id"),
                "source_zone": first_expected.get("zone_id"),
                "destination_prefix": actual.get("target_ip_address"),
                "gateway_class": first_expected.get("gateway_class_id"),
                "selected_gateway_id": actual.get("selected_gateway_id"),
                "expected_fec_id": first_expected.get("fec_id"),
                "expected_label": first_expected.get("initial_label")
                or first_expected.get("label"),
                "expected_zone_anchor": actual.get("serving_satellite_id"),
                "expected_path": [
                    _safe_dict(item).get("map_type") for item in expected_path
                ],
                "expected_hop_index": first_expected.get("hop_index"),
                "expected_logical_ingress_interface": first_expected.get(
                    "ingress_interface"
                ),
                "actual_real_ifindex_used": first_expected.get("ingress_ifindex"),
                "bpf_map_lookup_key": {
                    "generation": first_expected.get("generation", active_generation),
                    "ingress_ifindex": first_expected.get("ingress_ifindex"),
                    "label": first_expected.get("label"),
                    "target_ip": first_expected.get("target_ip")
                    or actual.get("target_ip_address"),
                    "direction": first_expected.get("direction"),
                },
                "map_that_missed": packet_diagnostics.get("blocker"),
                "expected_action": first_expected.get("map_type"),
                "actual_drop_reason": miss_drop,
                "packet_had_mpls_shim_header": any(
                    _safe_dict(item).get("label") is not None
                    for item in expected_path
                ),
                "key_existed_in_offline_ledger": None,
                "key_existed_in_live_installed_map": {
                    "ingress_classifier": bool(loaded_keys["classifier"]),
                    "core_lfib": bool(loaded_keys["core_lfib"]),
                    "egress_handoff": bool(loaded_keys["egress_handoff"]),
                },
                "first_missing_observation_point": first_missing,
                "expected_path_has_core_lfib": _expected_path_has_core(
                    selector_summary
                ),
            }
        )
    if not rows and miss_drop:
        rows.append(
            {
                "probe_id": "unknown",
                "packet_id": "unknown",
                "generation": active_generation,
                "active_generation_at_validation_time": active_generation,
                "map_that_missed": packet_diagnostics.get("blocker"),
                "actual_drop_reason": miss_drop,
            }
        )
    return rows


def build_report(run_dir: Path) -> dict[str, Any]:
    runtime_report = _safe_dict(_read_json(run_dir / "real_runtime_report.json", {}))
    temporal_audit = _safe_dict(_read_json(run_dir / "temporal_audit.json", {}))
    path_audit = _safe_dict(_read_json(run_dir / "path_cardinality_audit.json", {}))
    first_failure = _read_first_failure(run_dir / "events.jsonl")
    packet_diagnostics = _packet_path_diagnostics(runtime_report)
    readiness = _readiness(runtime_report)
    map_load = _map_load(readiness)
    active_generation = map_load.get("active_generation", readiness.get("active_generation"))
    if active_generation is not None:
        active_generation = int(active_generation)
    loaded_keys = {
        "classifier": _safe_list(map_load.get("loaded_classifier_keys")),
        "core_lfib": _safe_list(map_load.get("loaded_core_lfib_keys")),
        "egress_handoff": _safe_list(map_load.get("loaded_egress_handoff_keys")),
    }
    path_aggregate = _safe_dict(path_audit.get("aggregate"))
    selector_summaries = _safe_list(
        _safe_dict(packet_diagnostics.get("path_observability")).get(
            "selector_summaries"
        )
    )
    observed_core_probe_count = sum(
        1 for summary in selector_summaries if _expected_path_has_core(_safe_dict(summary))
    )
    core_lfib_hit = int(packet_diagnostics.get("core_lfib_hit") or 0)
    direct_handoff_ratio = path_aggregate.get("direct_handoff_ratio")
    root_cause = (
        "tc_bpf_interface_role_mismatch_non_gz_ip_strict_drop"
        if int(_safe_dict(packet_diagnostics.get("miss_drop_counters")).get("miss") or 0) > 0
        and observed_core_probe_count == 0
        else "undetermined"
    )
    return {
        "schema_version": "skyseed-bpf-live-miss-drop-diagnosis-v1",
        "run_dir": str(run_dir),
        "source_of_truth": {
            "events_jsonl": str(run_dir / "events.jsonl"),
            "first_failure_line": first_failure.get("line_number"),
            "first_failure_reason": first_failure.get("failure_reason"),
        },
        "runtime_status": {
            "aggregate_runtime_succeeded": runtime_report.get(
                "aggregate_runtime_succeeded"
            ),
            "event_timeline_execution_success": runtime_report.get(
                "event_timeline_execution_success"
            ),
            "runtime_transaction_count": temporal_audit.get(
                "runtime_transaction_count"
            ),
            "executed_runtime_transaction_count": temporal_audit.get(
                "executed_runtime_transaction_count"
            ),
            "parameter_update_batch_count": temporal_audit.get(
                "parameter_update_batch_count"
            ),
            "executed_parameter_update_batch_count": temporal_audit.get(
                "executed_parameter_update_batch_count"
            ),
        },
        "strict_bpf_counters": {
            "linux_gz_fallback_enabled": runtime_report.get(
                "linux_gz_fallback_enabled"
            ),
            "linux_gz_fallback_command_count": runtime_report.get(
                "linux_gz_fallback_command_count"
            ),
            "eligible_core_fallback_to_linux": runtime_report.get(
                "eligible_core_fallback_to_linux"
            ),
            "backend_apply_fallback_to_linux": 0,
            "classifier_hit": packet_diagnostics.get("classifier_hit"),
            "egress_handoff_hit": packet_diagnostics.get("egress_handoff_hit"),
            "core_lfib_hit": core_lfib_hit,
            "miss_drop_counters": packet_diagnostics.get("miss_drop_counters"),
        },
        "active_generation": {
            "active_generation_at_validation_time": active_generation,
            "runtime_generation_loaded": readiness.get("runtime_generation_loaded"),
            "loaded_generation_count": map_load.get("loaded_generation_count"),
        },
        "live_installed_maps": {
            "loaded_classifier_count": map_load.get("loaded_classifier_count"),
            "loaded_core_lfib_count": map_load.get("loaded_core_lfib_count"),
            "loaded_egress_handoff_count": map_load.get("loaded_egress_handoff_count"),
            "loaded_interface_role_count": map_load.get("loaded_interface_role_count"),
            "loaded_classifier_keys": loaded_keys["classifier"],
            "loaded_core_lfib_keys": loaded_keys["core_lfib"],
            "loaded_egress_handoff_keys": loaded_keys["egress_handoff"],
            "loaded_interface_roles": _safe_list(map_load.get("loaded_interface_roles")),
        },
        "path_cardinality": {
            "direct_handoff_ratio": direct_handoff_ratio,
            "multi_hop_satellite_core_path_count": path_aggregate.get(
                "multi_hop_satellite_core_path_count"
            ),
            "two_hop_core_path_count": path_aggregate.get("two_hop_core_path_count"),
            "three_plus_hop_core_path_count": path_aggregate.get(
                "three_plus_hop_core_path_count"
            ),
            "selected_validation_core_probe_count": observed_core_probe_count,
            "core_lfib_hit_explanation": (
                "selected acceptance probe exercised direct handoff paths only"
                if core_lfib_hit == 0 and observed_core_probe_count == 0
                else "core LFIB was exercised or remains unexplained"
            ),
        },
        "miss_drop_attribution_table": _miss_drop_attribution(
            packet_diagnostics=packet_diagnostics,
            active_generation=active_generation,
            loaded_keys=loaded_keys,
        ),
        "root_cause_hypothesis": root_cause,
        "fix_direction": (
            "load and enforce interface_role_map so classifier misses remain strict on "
            "classifier ingress interfaces, while non-G/Z IP on MPLS-only core/handoff "
            "interfaces passes without polluting miss/drop counters"
        ),
    }


def write_markdown(report: dict[str, Any], path: Path) -> None:
    counters = _safe_dict(report.get("strict_bpf_counters"))
    path_cardinality = _safe_dict(report.get("path_cardinality"))
    source = _safe_dict(report.get("source_of_truth"))
    lines = [
        "# BPF Live Miss/Drop Diagnosis",
        "",
        f"- first_failure_line: {source.get('first_failure_line')}",
        f"- first_failure_reason: `{source.get('first_failure_reason')}`",
        f"- root_cause_hypothesis: `{report.get('root_cause_hypothesis')}`",
        f"- miss_drop_counters: `{counters.get('miss_drop_counters')}`",
        f"- classifier_hit: `{counters.get('classifier_hit')}`",
        f"- egress_handoff_hit: `{counters.get('egress_handoff_hit')}`",
        f"- core_lfib_hit: `{counters.get('core_lfib_hit')}`",
        f"- core_lfib_hit_explanation: {path_cardinality.get('core_lfib_hit_explanation')}",
        f"- direct_handoff_ratio: `{path_cardinality.get('direct_handoff_ratio')}`",
        f"- selected_validation_core_probe_count: `{path_cardinality.get('selected_validation_core_probe_count')}`",
        "",
        "## Fix Direction",
        "",
        str(report.get("fix_direction")),
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--md-output", type=Path)
    args = parser.parse_args()

    report = build_report(args.run_dir)
    json_output = args.json_output or args.run_dir / "bpf_live_miss_drop_diagnosis.json"
    md_output = args.md_output or args.run_dir / "bpf_live_miss_drop_diagnosis.md"
    json_output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_markdown(report, md_output)
    print(json.dumps({"json_output": str(json_output), "md_output": str(md_output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
