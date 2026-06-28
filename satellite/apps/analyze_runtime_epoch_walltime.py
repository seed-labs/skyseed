#!/usr/bin/env python3
# encoding: utf-8

"""Build per-epoch wall-time attribution for live Skyseed runtime artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean
from typing import Any


def _safe_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _num(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _operation_wall(metrics: dict[str, Any], names: set[str]) -> float:
    by_operation = _safe_dict(metrics.get("wall_seconds_by_operation"))
    return sum(_num(by_operation.get(name)) for name in names)


def _operation_count(metrics: dict[str, Any], names: set[str]) -> int:
    by_operation = _safe_dict(metrics.get("counts_by_operation"))
    return sum(int(_num(by_operation.get(name))) for name in names)


def _node_metrics(iteration: dict[str, Any], key: str) -> dict[str, Any]:
    timing = _safe_dict(_safe_dict(iteration.get("node_local_execution")).get("timing"))
    return _safe_dict(timing.get(key))


def _epoch_row(iteration: dict[str, Any]) -> dict[str, Any]:
    timing = _safe_dict(iteration.get("timing"))
    semantic = _safe_dict(timing.get("semantic_breakdown"))
    marks = _safe_dict(iteration.get("epoch_timing_marks"))
    mark_derived = _safe_dict(marks.get("derived_seconds"))
    host = _safe_dict(_safe_dict(iteration.get("host_mutation_execution")).get("outcome"))
    manager = _safe_dict(host.get("manager_timing_breakdown"))
    host_metrics = _safe_dict(host.get("typed_operation_metrics")) or _safe_dict(
        iteration.get("host_typed_operation_metrics")
    )
    node_timing = _safe_dict(_safe_dict(iteration.get("node_local_execution")).get("timing"))
    apply_metrics = _node_metrics(iteration, "apply_typed_operation_metrics")
    verify_metrics = _node_metrics(iteration, "verify_typed_operation_metrics")
    backend = _safe_dict(_safe_dict(iteration.get("node_local_execution")).get("backend"))
    readiness = _safe_dict(backend.get("bpf_owned_readiness"))
    prepare = _safe_dict(
        _safe_dict(_safe_dict(readiness.get("prepare_steps")).get("real_backend_prepare"))
    )
    prepare_timing = _safe_dict(prepare.get("timing_breakdown"))

    total_wall = _num(timing.get("transition_wall_seconds"))
    bpf_prepare = _num(node_timing.get("bpf_prepare_after_incremental_cleanup_wall_seconds"))
    bpf_map_update = _num(prepare_timing.get("bpf_map_population_wall_seconds"))
    bpf_map_delta_build = _num(
        prepare_timing.get("interface_discovery_logical_binding_wall_seconds")
    )
    bpf_attach = _num(prepare_timing.get("bpf_attach_tc_qdisc_setup_wall_seconds"))
    bpf_qdisc_cleanup = _num(prepare_timing.get("tc_qdisc_cleanup_before_attach_wall_seconds"))
    bpf_role_read = _num(prepare_timing.get("bpf_interface_role_live_read_wall_seconds"))
    bpf_object_compile = _num(prepare_timing.get("bpf_object_compile_or_cache_wall_seconds"))
    host_apply = _num(timing.get("host_operation_apply_wall_seconds"))
    node_apply = _num(timing.get("node_local_apply_wall_seconds"))
    incremental_cleanup = _num(timing.get("node_local_incremental_cleanup_wall_seconds"))
    targeted_validation = _num(timing.get("targeted_validation_seconds"))
    ping_validation = _num(timing.get("post_transition_connectivity_probe_wall_seconds"))
    packet_observation = _num(timing.get("packet_observation_wall_seconds"))
    audit_write = _num(timing.get("audit_write_seconds"))
    dirty_reconcile = _num(timing.get("parameter_dirty_reconcile_seconds"))
    qdisc_apply = _num(timing.get("parameter_qdisc_apply_seconds"))
    metadata_update = _num(timing.get("parameter_metadata_update_seconds"))
    event_wait = _num(
        timing.get("event_wait_seconds")
        or semantic.get("event_wait_seconds")
        or mark_derived.get("event_wait_seconds")
    )
    network_update = _num(
        timing.get("network_update_seconds")
        or semantic.get("network_update_seconds")
        or mark_derived.get("network_update_seconds")
    )
    post_apply_overhead = _num(
        timing.get("post_apply_overhead_seconds")
        or semantic.get("post_apply_overhead_seconds")
        or mark_derived.get("post_apply_overhead_seconds")
    )
    unknown_blocking = _num(
        timing.get("unknown_blocking_seconds")
        or semantic.get("unknown_blocking_seconds")
        or mark_derived.get("unknown_blocking_seconds")
    )
    timing_classification = str(
        timing.get("runtime_timing_classification")
        or semantic.get("runtime_timing_classification")
        or "NETWORK_UPDATE_DOMINATES"
    )

    qdisc_delete = _operation_wall(host_metrics, {"qdisc_delete"})
    qdisc_update = _operation_wall(
        host_metrics,
        {"qdisc_add", "qdisc_change", "qdisc_replace", "qdisc_other"},
    )
    veth_link = _operation_wall(
        host_metrics,
        {
            "veth_create",
            "veth_delete",
            "veth_down",
            "veth_move_netns",
            "veth_rename",
            "veth_up",
            "link_set",
            "addr_add",
            "addr_flush",
            "bridge_create",
        },
    )
    docker_shell = (
        _operation_wall(host_metrics, {"shell_batch"})
        + _operation_wall(apply_metrics, set(_safe_dict(apply_metrics.get("wall_seconds_by_operation"))))
        + _operation_wall(verify_metrics, set(_safe_dict(verify_metrics.get("wall_seconds_by_operation"))))
    )

    accounted_components = {
        "planning_transaction_build_seconds": _num(
            timing.get("network_mutation_plan_wall_seconds")
        ),
        "fec_diff_seconds": _num(manager.get("runtime_reconcile_wall_seconds")),
        "bpf_prepare_total_seconds": bpf_prepare,
        "bpf_map_delta_build_seconds": bpf_map_delta_build,
        "bpf_map_update_seconds": bpf_map_update,
        "active_generation_switch_seconds": _num(
            prepare_timing.get("active_generation_switch_wall_seconds")
        ),
        "qdisc_parameter_update_seconds": qdisc_apply,
        "qdisc_delete_recreate_seconds": qdisc_delete + qdisc_update + bpf_qdisc_cleanup,
        "veth_link_operation_seconds": veth_link,
        "tc_attach_filter_operation_seconds": bpf_attach,
        "interface_discovery_ifindex_binding_seconds": bpf_map_delta_build,
        "docker_exec_shell_batch_seconds": docker_shell,
        "bpf_acceptance_probe_seconds": packet_observation,
        "ping_return_validation_seconds": max(targeted_validation, ping_validation),
        "audit_report_write_seconds": audit_write,
        "incremental_cleanup_seconds": incremental_cleanup,
        "event_wait_seconds": event_wait,
        "network_update_seconds": network_update,
        "post_apply_overhead_seconds": post_apply_overhead,
        "unknown_blocking_seconds": unknown_blocking,
        "polling_sleep_wait_seconds": event_wait,
        "rollback_preflight_check_seconds": _num(
            timing.get("pre_transition_connectivity_probe_wall_seconds")
        ),
        "parameter_dirty_reconcile_seconds": dirty_reconcile,
        "parameter_metadata_update_seconds": metadata_update,
        "bpf_object_compile_or_cache_seconds": bpf_object_compile,
        "bpf_interface_role_read_seconds": bpf_role_read,
    }
    # Some high-level buckets contain sub-buckets. Use an exclusive estimate for
    # unaccounted: total minus the broad phases that are known to occur on the
    # critical path, while still reporting the nested details above.
    exclusive_accounted = (
        _num(timing.get("network_mutation_plan_wall_seconds"))
        + bpf_prepare
        + _num(timing.get("node_local_apply_wall_seconds"))
        + incremental_cleanup
        + max(targeted_validation, ping_validation)
        + packet_observation
        + audit_write
    )
    unaccounted = max(0.0, total_wall - exclusive_accounted)
    unaccounted_ratio = unaccounted / total_wall if total_wall > 0.0 else 0.0
    explanation = ""
    if unaccounted_ratio >= 0.05:
        explanation = (
            "remaining wall is dominated by legacy timing-mark placement or "
            "runner overhead not separately timestamped in this artifact"
        )

    return {
        "sequence_index": iteration.get("sequence_index"),
        "epoch_id": iteration.get("epoch_id"),
        "generation": _safe_dict(prepare.get("active_generation")).get("value")
        or prepare.get("active_generation"),
        "event_time_utc": iteration.get("activation_time_utc"),
        "epoch_type": (
            "ParameterUpdateBatch"
            if iteration.get("epoch_type") == "parameter_epoch"
            else "RuntimeTransaction"
        ),
        "raw_epoch_type": iteration.get("epoch_type"),
        "total_wall_seconds": round(total_wall, 6),
        "timing": {key: round(value, 6) for key, value in accounted_components.items()},
        "operation_counts": {
            "qdisc_delete_count": _operation_count(host_metrics, {"qdisc_delete"}),
            "qdisc_update_count": _operation_count(
                host_metrics,
                {"qdisc_add", "qdisc_change", "qdisc_replace", "qdisc_other"},
            ),
            "veth_link_operation_count": _operation_count(
                host_metrics,
                {
                    "veth_create",
                    "veth_delete",
                    "veth_down",
                    "veth_move_netns",
                    "veth_rename",
                    "veth_up",
                    "link_set",
                    "addr_add",
                    "addr_flush",
                    "bridge_create",
                },
            ),
            "docker_exec_batch_count": int(
                _num(apply_metrics.get("docker_exec_batch_count"))
                + _num(verify_metrics.get("docker_exec_batch_count"))
            ),
            "node_local_apply_command_count": int(_num(apply_metrics.get("command_count"))),
            "changed_gz_fec_count": int(_num(iteration.get("forwarding_mutation_count"))),
            "changed_classifier_map_entries": int(
                _num(prepare.get("loaded_classifier_count"))
            ),
            "changed_core_lfib_entries": int(_num(prepare.get("loaded_core_lfib_count"))),
            "changed_egress_handoff_entries": int(
                _num(prepare.get("loaded_egress_handoff_count"))
            ),
            "changed_interface_role_entries": int(
                _num(prepare.get("loaded_interface_role_count"))
            ),
            "active_generation_update_count": int(
                _num(prepare.get("active_generation_switch_count"))
            ),
        },
        "bpf_update_mode": prepare.get("prepare_mode", "unknown"),
        "runtime_timing_classification": timing_classification,
        "bpf_maps_delta_updated": prepare.get("prepare_mode")
        == "reuse_attached_bpf_map_reload",
        "linux_gz_fallback_command_count": int(
            _num(iteration.get("linux_gz_fallback_command_count"))
        ),
        "eligible_core_fallback_to_linux": int(
            _num(iteration.get("eligible_core_fallback_to_linux"))
        ),
        "backend_apply_fallback_to_linux": int(
            _num(
                _safe_dict(
                    _safe_dict(
                        _safe_dict(iteration.get("node_local_execution")).get("preflight")
                    ).get("backend_apply_contract")
                ).get("fallback_to_linux_count")
            )
        ),
        "packet_keyed_ip_route_fallback": False,
        "unaccounted_wall_seconds": round(unaccounted, 6),
        "unaccounted_wall_ratio": round(unaccounted_ratio, 6),
        "unaccounted_explanation": explanation,
        "legacy_semantic_unclassified_wall_seconds": semantic.get(
            "runtime_unclassified_wall_seconds"
        ),
        "legacy_scheduler_blocking_after_data_plane_ready_seconds": semantic.get(
            "scheduler_blocking_after_data_plane_ready_seconds"
        ),
        "event_wait_is_expected_pacing": timing_classification
        == "EVENT_PACING_EXPECTED",
    }


def build_report(run_dir: Path) -> dict[str, Any]:
    runtime_report = json.loads((run_dir / "real_runtime_report.json").read_text())
    epochs = [_epoch_row(dict(item)) for item in _safe_list(runtime_report.get("iterations"))]
    runtime_epochs = [item for item in epochs if item["raw_epoch_type"] == "runtime_epoch"]
    steady_runtime = runtime_epochs[1:] if len(runtime_epochs) > 1 else []
    parameter_epochs = [item for item in epochs if item["raw_epoch_type"] == "parameter_epoch"]

    def avg(rows: list[dict[str, Any]], key: str) -> float:
        values = [_num(row.get(key)) for row in rows]
        return round(mean(values), 6) if values else 0.0

    classification_counts: dict[str, int] = {}
    for row in epochs:
        label = str(row.get("runtime_timing_classification") or "UNKNOWN")
        classification_counts[label] = int(classification_counts.get(label, 0)) + 1

    return {
        "schema_version": "skyseed-runtime-epoch-walltime-attribution-v1",
        "run_dir": str(run_dir),
        "summary": {
            "runtime_epoch_count": len(runtime_epochs),
            "parameter_epoch_count": len(parameter_epochs),
            "average_runtime_epoch_wall_seconds": avg(runtime_epochs, "total_wall_seconds"),
            "average_steady_runtime_epoch_wall_seconds": avg(
                steady_runtime,
                "total_wall_seconds",
            ),
            "average_steady_bpf_prepare_seconds": avg(
                [
                    {"value": row["timing"]["bpf_prepare_total_seconds"]}
                    for row in steady_runtime
                ],
                "value",
            ),
            "average_steady_incremental_cleanup_seconds": avg(
                [
                    {"value": row["timing"]["incremental_cleanup_seconds"]}
                    for row in steady_runtime
                ],
                "value",
            ),
            "average_parameter_epoch_wall_seconds": avg(
                parameter_epochs,
                "total_wall_seconds",
            ),
            "timing_classification_counts": dict(
                sorted(classification_counts.items())
            ),
            "root_cause": "see timing_classification_counts and per-epoch four-bucket accounting",
        },
        "epochs": epochs,
    }


def write_markdown(report: dict[str, Any], path: Path) -> None:
    summary = _safe_dict(report.get("summary"))
    lines = [
        "# Runtime Epoch Wall-Time Attribution",
        "",
        f"- runtime epochs: `{summary.get('runtime_epoch_count')}`",
        f"- parameter epochs: `{summary.get('parameter_epoch_count')}`",
        f"- average runtime wall: `{summary.get('average_runtime_epoch_wall_seconds')}` seconds",
        f"- average steady runtime wall: `{summary.get('average_steady_runtime_epoch_wall_seconds')}` seconds",
        f"- average steady BPF prepare: `{summary.get('average_steady_bpf_prepare_seconds')}` seconds",
        f"- average steady incremental cleanup: `{summary.get('average_steady_incremental_cleanup_seconds')}` seconds",
        f"- average parameter wall: `{summary.get('average_parameter_epoch_wall_seconds')}` seconds",
        f"- root cause: {summary.get('root_cause')}",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    report = build_report(args.run_dir)
    json_path = args.run_dir / "runtime_epoch_walltime_attribution.json"
    md_path = args.run_dir / "runtime_epoch_walltime_attribution.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    write_markdown(report, md_path)
    print(json.dumps({"json": str(json_path), "md": str(md_path)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
