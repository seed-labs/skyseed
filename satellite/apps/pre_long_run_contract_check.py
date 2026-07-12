#!/usr/bin/env python3
# encoding: utf-8

"""Offline long-run contract check for the active operational-clean flow.

This is not a broad regression test runner. Each check names the expensive-run
failure or lifecycle boundary it is meant to catch.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import inspect
import json
import mmap
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence
from unittest.mock import patch

import yaml

try:
    from ._path_setup import bootstrap_app_import_path
except ImportError:  # pragma: no cover - direct script execution path.
    from _path_setup import bootstrap_app_import_path

bootstrap_app_import_path()
SATELLITE_DIR = Path(__file__).resolve().parents[1]

from bpf_dataplane_canary import (  # noqa: E402
    collect_environment,
    resolve_bpftool,
    run_command,
)

from horizon_maintenance_process_worker import (  # noqa: E402
    _base_generation_interface_binding_caches,
    _freeze_preload_work_item,
    _hydrate_preload_work_item_contexts,
    _inactive_preload_record,
    _load_preload_work_items,
    _merge_created_interface_bindings,
    _merge_final_interface_bindings,
    _required_interface_refs_for_preload_work_items,
    _runtime_update_plan_from_payload,
    _topology_snapshot_from_payload,
    _validate_final_interface_binding_scope,
    run_horizon_maintenance_process_payload,
)
from network_runtime import (  # noqa: E402
    DirectNetnsVethSlotLinkNamespaceQdiscInstaller,
    _stable_local_mac,
)
from contracts import (  # noqa: E402
    RUNTIME_UPDATE_PLAN_SCHEMA_VERSION,
    TOPOLOGY_SNAPSHOT_SCHEMA_VERSION,
    RuntimeUpdatePlan,
    TopologySnapshot,
)
from linux_mpls_runtime import (  # noqa: E402
    _bpf_owned_compile_mpls_maps_for_contexts,
    _bpf_owned_find_preloaded_generation_record,
    _bpf_owned_prepare_mpls_backend,
    _bpf_owned_prepared_attach_capability_contract,
    _execute_bpf_delta_attach_tasks,
    _prepare_bpf_owned_readiness,
    _prepare_bpf_owned_preactivation_attach_capability_from_frozen_inputs,
    _prepare_bpf_owned_preactivation_preload,
    _realization_contexts,
    MinimalRealLinuxMplsInstaller,
)
from planner import UtInternetExperimentPlanner  # noqa: E402
from apps.replay_r134_shared_gz_generations import (  # noqa: E402
    DEFAULT_GATEWAY_BINDING,
    DEFAULT_POLICY_CATALOG,
    _current_policy_inputs,
    _snapshot_with_current_policy,
)
try:
    from .run_starlink_shell_experiment import (  # noqa: E402
        _accept_horizon_maintenance_host_prepare_handoff,
        _complete_initial_frozen_interface_binding_caches,
        _merge_bpf_owned_route_identity_evidence,
        _merge_prepared_attach_capability_readiness,
        _parameter_epoch_recovery_contract,
        _prepared_attach_capability_readiness_for_preload,
        _rolling_horizon_process_preload_work_item,
        _retired_cleanup_candidate_lifecycle_transition,
        _run_live_temporal_runtime,
        _run_preview,
        _terminate_horizon_maintenance_process_workers_for_cleanup,
    )
except ImportError:  # pragma: no cover - direct script execution path.
    from run_starlink_shell_experiment import (  # noqa: E402
        _accept_horizon_maintenance_host_prepare_handoff,
        _complete_initial_frozen_interface_binding_caches,
        _merge_bpf_owned_route_identity_evidence,
        _merge_prepared_attach_capability_readiness,
        _parameter_epoch_recovery_contract,
        _prepared_attach_capability_readiness_for_preload,
        _rolling_horizon_process_preload_work_item,
        _retired_cleanup_candidate_lifecycle_transition,
        _run_live_temporal_runtime,
        _run_preview,
        _terminate_horizon_maintenance_process_workers_for_cleanup,
    )


SCHEMA_VERSION = "skyseed-pre-long-run-contract-check-v17"


def _current_shared_gz_historical_context(
    context_payload: Mapping[str, Any],
) -> Dict[str, Any]:
    """Replan a frozen historical snapshot without consuming its v1 plan."""
    context = dict(context_payload or {})
    policies, services = _current_policy_inputs(
        DEFAULT_POLICY_CATALOG,
        DEFAULT_GATEWAY_BINDING,
    )
    snapshot = _snapshot_with_current_policy(
        dict(context.get("snapshot") or {}),
        policies=policies,
        services=services,
    )
    historical_target = dict(
        dict(context.get("runtime_update_plan") or {}).get("target_snapshot") or {}
    )
    plan = UtInternetExperimentPlanner().plan(snapshot)
    plan = replace(
        plan,
        target_snapshot={**dict(plan.target_snapshot), **historical_target},
    )
    context["snapshot"] = snapshot.to_dict()
    context["runtime_update_plan"] = plan.to_dict()
    return context


def _current_shared_gz_historical_work_item(
    item_payload: Mapping[str, Any],
) -> Dict[str, Any]:
    item = dict(item_payload or {})
    item["context"] = _current_shared_gz_historical_context(
        dict(item.get("context") or {})
    )
    return item


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _historical_compose_ix_bridge_name(network_name: str) -> str:
    return "sib" + hashlib.sha256(
        str(network_name).encode("utf-8")
    ).hexdigest()[:12]


def _historical_freeze_compose_ix_bridge_bindings(
    *,
    run_dir: Path,
    output_dir: Path,
) -> Dict[str, Any]:
    """Reproduce the retired r122 IX fix for historical evidence only."""
    compose_path = output_dir / "docker-compose.yml"
    payload = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    networks = dict(payload.get("networks") or {})
    records: list[Dict[str, str]] = []
    for network_name in sorted(networks):
        if not str(network_name).startswith("net_ix_"):
            continue
        network = dict(networks[network_name] or {})
        driver_opts = dict(network.get("driver_opts") or {})
        bridge_name = _historical_compose_ix_bridge_name(str(network_name))
        driver_opts["com.docker.network.bridge.name"] = bridge_name
        network["driver_opts"] = driver_opts
        networks[network_name] = network
        records.append(
            {"network_name": str(network_name), "bridge_name": bridge_name}
        )
    if not records:
        raise RuntimeError("historical_compose_ix_bridge_bindings_missing")
    payload["networks"] = networks
    compose_path.write_text(
        yaml.safe_dump(payload, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    report = {
        "schema_version": "skyseed-historical-compose-ix-bindings-v1",
        "ok": True,
        "bindings": records,
        "network_count": len(records),
    }
    (run_dir / "frozen_compose_ix_bridge_bindings.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def _historical_prepare_frozen_compose_ix_bridges_unicast(
    *,
    network_executor: Any,
    run_dir: Path,
) -> Dict[str, Any]:
    path = run_dir / "frozen_compose_ix_bridge_bindings.json"
    frozen = dict(_read_json(path))
    names = [
        str(dict(item).get("bridge_name") or "")
        for item in list(frozen.get("bindings") or [])
    ]
    if frozen.get("ok") is not True or not names or any(not name for name in names):
        raise RuntimeError("historical_compose_ix_bridge_bindings_invalid")
    report = dict(
        network_executor.prepare_existing_host_interfaces_unicast(
            interface_names=names
        )
    )
    if (
        report.get("ok") is not True
        or report.get("full_scan") is not False
        or report.get("live_discovery_allowed") is not False
        or int(report.get("prepared_interface_count") or 0) != len(names)
    ):
        raise RuntimeError("historical_compose_ix_unicast_prepare_failed")
    return {
        **report,
        "frozen_binding_count": len(names),
        "binding_source": "historical_frozen_compose_driver_option",
    }


def _read_compact_named_object(
    path: Path,
    *,
    section_name: str,
    object_name: str,
) -> Dict[str, Any]:
    section_marker = json.dumps(str(section_name), separators=(",", ":")).encode(
        "utf-8"
    ) + b":{"
    object_marker = json.dumps(str(object_name), separators=(",", ":")).encode(
        "utf-8"
    ) + b":"
    with path.open("rb") as handle:
        compact = mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            section_offset = compact.find(section_marker)
            if section_offset < 0:
                raise RuntimeError(
                    f"compact_named_object_section_missing:{section_name}"
                )
            object_offset = compact.find(
                object_marker,
                section_offset + len(section_marker),
            )
            if object_offset < 0:
                raise RuntimeError(
                    f"compact_named_object_missing:{section_name}:{object_name}"
                )
            start = compact.find(b"{", object_offset + len(object_marker))
            if start < 0:
                raise RuntimeError(
                    f"compact_named_object_value_invalid:{section_name}:{object_name}"
                )
            depth = 0
            in_string = False
            escaped = False
            end = -1
            for index in range(start, len(compact)):
                value = compact[index]
                if in_string:
                    if escaped:
                        escaped = False
                    elif value == 92:
                        escaped = True
                    elif value == 34:
                        in_string = False
                    continue
                if value == 34:
                    in_string = True
                elif value == 123:
                    depth += 1
                elif value == 125:
                    depth -= 1
                    if depth == 0:
                        end = index + 1
                        break
            if end < 0:
                raise RuntimeError(
                    f"compact_named_object_unterminated:{section_name}:{object_name}"
                )
            loaded = json.loads(compact[start:end])
        finally:
            compact.close()
    if not isinstance(loaded, dict):
        raise RuntimeError(
            f"compact_named_object_not_mapping:{section_name}:{object_name}"
        )
    return dict(loaded)


def _read_last_compact_named_object(path: Path, *, object_name: str) -> Dict[str, Any]:
    marker = json.dumps(str(object_name)).encode("utf-8") + b":"
    with path.open("rb") as handle:
        compact = mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            object_offset = compact.rfind(marker)
            if object_offset < 0:
                raise RuntimeError(f"compact_named_object_missing:{object_name}")
            start = compact.find(b"{", object_offset + len(marker))
            if start < 0:
                raise RuntimeError(f"compact_named_object_value_invalid:{object_name}")
            depth = 0
            in_string = False
            escaped = False
            end = -1
            for index in range(start, len(compact)):
                value = compact[index]
                if in_string:
                    if escaped:
                        escaped = False
                    elif value == 92:
                        escaped = True
                    elif value == 34:
                        in_string = False
                    continue
                if value == 34:
                    in_string = True
                elif value == 123:
                    depth += 1
                elif value == 125:
                    depth -= 1
                    if depth == 0:
                        end = index + 1
                        break
            if end < 0:
                raise RuntimeError(f"compact_named_object_unterminated:{object_name}")
            loaded = json.loads(compact[start:end])
        finally:
            compact.close()
    if not isinstance(loaded, dict):
        raise RuntimeError(f"compact_named_object_not_mapping:{object_name}")
    return dict(loaded)


def _read_compact_jsonl_record_containing(path: Path, *, marker: str) -> Dict[str, Any]:
    encoded = str(marker).encode("utf-8")
    with path.open("rb") as handle:
        compact = mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            marker_offset = compact.rfind(encoded)
            if marker_offset < 0:
                raise RuntimeError(f"compact_jsonl_marker_missing:{marker}")
            line_start = compact.rfind(b"\n", 0, marker_offset) + 1
            line_end = compact.find(b"\n", marker_offset)
            if line_end < 0:
                line_end = len(compact)
            loaded = json.loads(compact[line_start:line_end])
        finally:
            compact.close()
    if not isinstance(loaded, dict):
        raise RuntimeError(f"compact_jsonl_record_not_mapping:{marker}")
    return dict(loaded)


def _check(
    *,
    name: str,
    catches: Sequence[str],
    ok: bool,
    evidence: Mapping[str, Any] | None = None,
    blockers: Sequence[Mapping[str, Any]] | None = None,
) -> Dict[str, Any]:
    return {
        "name": name,
        "ok": bool(ok),
        "catches": list(catches),
        "evidence": dict(evidence or {}),
        "blockers": [dict(item) for item in list(blockers or [])],
    }


def _flag_value(command: Sequence[str], flag: str) -> str:
    try:
        index = list(command).index(flag)
    except ValueError:
        return ""
    if index + 1 >= len(command):
        return ""
    return str(command[index + 1])


def _profile_command_shape_check(
    run_dir: Path,
    *,
    expected_horizon_count: int,
) -> Dict[str, Any]:
    path = run_dir / "profile_run_summary.json"
    if not path.exists():
        return _check(
            name="profile_command_shape",
            catches=["r112 command rendered loop_count without --continuous"],
            ok=False,
            blockers=[{"reason": "profile_run_summary_missing", "path": str(path)}],
        )
    summary = dict(_read_json(path))
    command = [str(item) for item in list(summary.get("underlying_command") or [])]
    blockers: list[dict[str, Any]] = []
    if "--continuous" not in command:
        blockers.append({"reason": "continuous_flag_missing"})
    loop_count = _flag_value(command, "--loop-count")
    if expected_horizon_count and loop_count != str(expected_horizon_count):
        blockers.append(
            {
                "reason": "loop_count_mismatch",
                "expected": expected_horizon_count,
                "actual": loop_count,
            }
        )
    if "--require-visualization-links" not in command:
        blockers.append({"reason": "visualization_link_requirement_missing"})
    runtime_ping_observer_seconds = _flag_value(
        command, "--runtime-ping-observer-seconds"
    )
    if not runtime_ping_observer_seconds or float(runtime_ping_observer_seconds) <= 0:
        blockers.append({"reason": "nonblocking_runtime_ping_observer_missing"})
    bpftool_path = _flag_value(command, "--bpftool-path")
    if not bpftool_path:
        blockers.append({"reason": "explicit_bpftool_path_missing"})
    else:
        configured_bpftool = Path(bpftool_path)
        if (
            not configured_bpftool.is_absolute()
            or not configured_bpftool.is_file()
            or not os.access(configured_bpftool, os.X_OK)
        ):
            blockers.append(
                {
                    "reason": "explicit_bpftool_path_invalid",
                    "path": bpftool_path,
                }
            )
    compose_build_timeout_seconds = _flag_value(
        command, "--compose-build-timeout-seconds"
    )
    if compose_build_timeout_seconds != "0":
        blockers.append(
            {
                "reason": "useful_compose_build_hard_timeout_present",
                "actual": compose_build_timeout_seconds,
            }
        )
    forbidden_flags = [
        "--max-runtime-epochs",
        "--max-epochs",
    ]
    forbidden_present = [flag for flag in forbidden_flags if flag in command]
    if forbidden_present:
        blockers.append(
            {"reason": "short_or_stop_entry_flag_present", "flags": forbidden_present}
        )
    return _check(
        name="profile_command_shape",
        catches=["r112/r113 wrong launch shape before container startup"],
        ok=not blockers,
        evidence={
            "continuous": "--continuous" in command,
            "loop_count": loop_count,
            "require_visualization_links": "--require-visualization-links" in command,
            "runtime_ping_observer_seconds": runtime_ping_observer_seconds,
            "compose_build_timeout_seconds": compose_build_timeout_seconds,
            "explicit_bpftool_path": bpftool_path,
            "command_length": len(command),
        },
        blockers=blockers,
    )


def _frozen_horizon_scope_check(
    run_dir: Path,
    *,
    expected_horizon_count: int,
    expected_entries_per_horizon: int,
) -> Dict[str, Any]:
    path = run_dir / "frozen_prepared_horizon_segments.json"
    if not path.exists():
        return _check(
            name="frozen_horizon_scope",
            catches=["r112 only froze H0 for a requested multi-H run"],
            ok=False,
            blockers=[{"reason": "frozen_prepared_horizon_segments_missing"}],
        )
    payload = _read_json(path)
    segments = list(payload if isinstance(payload, list) else payload.get("segments") or [])
    blockers: list[dict[str, Any]] = []
    if expected_horizon_count and len(segments) != expected_horizon_count:
        blockers.append(
            {
                "reason": "horizon_count_mismatch",
                "expected": expected_horizon_count,
                "actual": len(segments),
            }
        )
    rows: list[dict[str, Any]] = []
    for index, item_any in enumerate(segments):
        item = dict(item_any or {})
        queue = list(item.get("current_execution_queue") or [])
        rows.append(
            {
                "index": index,
                "segment_id": str(item.get("segment_id") or ""),
                "horizon_id": str(item.get("horizon_id") or ""),
                "queue_count": len(queue),
            }
        )
        if expected_entries_per_horizon and len(queue) != expected_entries_per_horizon:
            blockers.append(
                {
                    "reason": "horizon_queue_count_mismatch",
                    "segment_id": str(item.get("segment_id") or ""),
                    "expected": expected_entries_per_horizon,
                    "actual": len(queue),
                }
            )
    return _check(
        name="frozen_horizon_scope",
        catches=["r112 prepared-horizon scope mismatch before compose"],
        ok=not blockers,
        evidence={"segments": rows},
        blockers=blockers,
    )


def _runtime_queue_coverage_check(
    run_dir: Path,
    *,
    expected_horizon_count: int,
) -> Dict[str, Any]:
    path = run_dir / "runtime_queue_route_identity_coverage.json"
    if not path.exists():
        return _check(
            name="runtime_queue_route_identity_coverage",
            catches=["runtime queue context/route identity producer-consumer drift"],
            ok=False,
            blockers=[{"reason": "runtime_queue_route_identity_coverage_missing"}],
        )
    report = dict(_read_json(path))
    horizon_scope = dict(report.get("horizon_scope") or {})
    blockers = [dict(item) for item in list(report.get("blockers") or [])]
    actual_horizons = int(horizon_scope.get("actual_horizon_count") or 0)
    if expected_horizon_count and actual_horizons != expected_horizon_count:
        blockers.append(
            {
                "reason": "coverage_horizon_count_mismatch",
                "expected": expected_horizon_count,
                "actual": actual_horizons,
            }
        )
    ok = bool(report.get("ok")) and not blockers
    return _check(
        name="runtime_queue_route_identity_coverage",
        catches=[
            "r112 missing queued context",
            "route identity evidence missing before runtime consumer",
        ],
        ok=ok,
        evidence={
            "actual_horizon_count": actual_horizons,
            "runtime_applicable_entry_count": int(
                horizon_scope.get("runtime_applicable_entry_count") or 0
            ),
            "context_record_count": int(report.get("context_record_count") or 0),
        },
        blockers=blockers,
    )


def _compiled_inputs(generation_id: int) -> Dict[str, Any]:
    interface_ref = "netns:offline-contract:if0"
    return {
        "schema_version": "skyseed-bpf-owned-compiled-runtime-inputs-v2",
        "active_generation": {"value": int(generation_id)},
        "ut_gateway_policy_map": [],
        "ingress_classifier_map": [],
        "gateway_selector_ingress_map": [],
        "core_lfib_map": [],
        "egress_handoff_map": [],
        "zone_edge_delivery_map": [],
        "generation_base_map": [],
        "attached_interfaces": [interface_ref],
        "attached_interface_details": [
            {
                "interface_ref": interface_ref,
                "expected_roles": ["g_to_z_classifier"],
                "attach_directions": ["ingress"],
            }
        ],
        "attach_plan": {interface_ref: ["ingress"]},
    }


def _upgrade_compiled_inputs_manifest(source: Mapping[str, Any]) -> Dict[str, Any]:
    upgraded = dict(source or {})
    upgraded["schema_version"] = "skyseed-bpf-owned-compiled-runtime-inputs-v2"
    for map_name in (
        "ut_gateway_policy_map",
        "ingress_classifier_map",
        "gateway_selector_ingress_map",
        "core_lfib_map",
        "egress_handoff_map",
        "zone_edge_delivery_map",
        "generation_base_map",
    ):
        upgraded.setdefault(map_name, [])
    return upgraded


def _r134_shared_gz_replay_report_check(report_path: Path) -> Dict[str, Any]:
    catches = [
        "shared G/Z ABI regression across r134's complete four-horizon corpus",
        "generation-zero loss at the H3 physical-generation wrap",
        "missing selector, static UT policy, or zone-edge delivery coverage",
    ]
    if not report_path.exists():
        return _check(
            name="r134_shared_gz_100_generation_offline_replay",
            catches=catches,
            ok=False,
            blockers=[{"reason": "r134_shared_gz_replay_report_missing"}],
        )
    report = dict(_read_json(report_path))
    records = [dict(item) for item in list(report.get("records") or [])]
    blockers = [dict(item) for item in list(report.get("blockers") or [])]
    horizon_counts = {
        str(key): int(value)
        for key, value in dict(report.get("horizon_counts") or {}).items()
    }
    if report.get("schema_version") != "skyseed-r134-shared-gz-offline-replay-v1":
        blockers.append({"reason": "r134_shared_gz_replay_schema_mismatch"})
    if report.get("current_compiled_schema") != (
        "skyseed-bpf-owned-compiled-runtime-inputs-v2"
    ):
        blockers.append({"reason": "r134_shared_gz_compiled_schema_mismatch"})
    if int(report.get("replayed_generation_count") or 0) != 100:
        blockers.append({"reason": "r134_shared_gz_generation_count_mismatch"})
    if horizon_counts != {"H0": 25, "H1": 25, "H2": 25, "H3": 25}:
        blockers.append({"reason": "r134_shared_gz_horizon_coverage_mismatch"})
    if len(records) != 100:
        blockers.append({"reason": "r134_shared_gz_record_count_mismatch"})
    if not any(int(item.get("physical_generation", -1)) == 0 for item in records):
        blockers.append({"reason": "r134_shared_gz_generation_zero_missing"})
    required_maps = {
        "ut_gateway_policy_map",
        "gateway_selector_ingress_map",
        "zone_edge_delivery_map",
    }
    invalid_record_count = 0
    for record in records:
        context_count = int(record.get("context_count") or 0)
        map_counts = dict(record.get("compiled_map_counts") or {})
        invalid = (
            record.get("ok") is not True
            or bool(record.get("blockers"))
            or context_count <= 0
            or int(record.get("observer_request_count") or 0) != context_count
            or int(record.get("unique_selector_flow_count") or 0) != context_count
            or int(record.get("unique_delivery_flow_count") or 0) != context_count
            or int(record.get("map_conflict_count") or 0) != 0
            or any(int(map_counts.get(name) or 0) != context_count for name in required_maps)
        )
        invalid_record_count += int(invalid)
    if invalid_record_count:
        blockers.append(
            {
                "reason": "r134_shared_gz_invalid_generation_records",
                "count": invalid_record_count,
            }
        )
    return _check(
        name="r134_shared_gz_100_generation_offline_replay",
        catches=catches,
        ok=report.get("ok") is True and not blockers,
        evidence={
            "report_path": str(report_path),
            "replayed_generation_count": len(records),
            "horizon_counts": horizon_counts,
            "generation_zero_present": any(
                int(item.get("physical_generation", -1)) == 0 for item in records
            ),
            "invalid_record_count": invalid_record_count,
        },
        blockers=blockers,
    )


def _frozen_live_state_alias_consumer_check() -> Dict[str, Any]:
    """Keep worker-frozen and scheduler-live host-state identities aligned."""
    record = {
        "record_id": "prelongrun-h1-generation-32",
        "ok": True,
        "horizon_id": "H1",
        "generation_id": 32,
        "intent_hash": "prelongrun-h1-intent-32",
        "source_state_id": (
            "frozen_prepare_host_state:topsnap-h0-final:runtimeepoch-h0-final"
        ),
        "inactive_generation_written": True,
        "compiled_runtime_inputs": {"active_generation": {"value": 32}},
    }
    alias_match = _bpf_owned_find_preloaded_generation_record(
        readiness={"preloaded_generation_records": [record]},
        target_generation_id=32,
        target_intent_hash="prelongrun-h1-intent-32",
        target_horizon_id="H1",
        current_source_state_id=(
            "live_state:topsnap-h0-final:runtimeepoch-h0-final"
        ),
    )
    stale_match = _bpf_owned_find_preloaded_generation_record(
        readiness={"preloaded_generation_records": [record]},
        target_generation_id=32,
        target_intent_hash="prelongrun-h1-intent-32",
        target_horizon_id="H1",
        current_source_state_id=(
            "live_state:topsnap-stale:runtimeepoch-h0-final"
        ),
    )
    blockers: list[Dict[str, Any]] = []
    if not alias_match:
        blockers.append({"reason": "frozen_live_state_alias_not_consumable"})
    if stale_match is not None:
        blockers.append({"reason": "stale_frozen_live_state_alias_accepted"})
    return _check(
        name="frozen_live_state_alias_consumer",
        catches=[
            "compact worker handoff used frozen_prepare_host_state while the H-boundary consumer used the equivalent live_state identity",
        ],
        ok=not blockers,
        evidence={
            "equivalent_alias_selected": bool(alias_match),
            "stale_alias_rejected": stale_match is None,
            "comparison_scope": "snapshot_id_and_epoch_id",
        },
        blockers=blockers,
    )


def _r135_retired_compose_ix_active_path_check(run_dir: Path) -> Dict[str, Any]:
    catches = [
        "r135 preview failed before compose because the retired Mini-Internet IX gate remained active"
    ]
    stderr_path = run_dir / "profile_runner_stderr.log"
    compose_path = run_dir / "seed_output" / "docker-compose.yml"
    blockers: list[Dict[str, Any]] = []
    historical_failure = (
        stderr_path.is_file()
        and "compose_ix_bridge_bindings_missing"
        in stderr_path.read_text(encoding="utf-8")
    )
    if not historical_failure:
        blockers.append({"reason": "r135_compose_ix_failure_not_reproduced"})
    network_names: list[str] = []
    if compose_path.is_file():
        network_names = sorted(
            str(name)
            for name in dict(
                dict(yaml.safe_load(compose_path.read_text(encoding="utf-8")) or {}).get(
                    "networks"
                )
                or {}
            )
        )
    if any(name.startswith("net_ix_") for name in network_names):
        blockers.append({"reason": "current_compose_still_contains_retired_ix_network"})
    active_source = inspect.getsource(_run_preview) + inspect.getsource(
        _run_live_temporal_runtime
    )
    if "compose_ix" in active_source or "frozen_compose_ix_bridge_bindings" in active_source:
        blockers.append({"reason": "retired_compose_ix_gate_still_in_active_runtime"})
    return _check(
        name="r135_retired_compose_ix_active_path",
        catches=catches,
        ok=not blockers,
        evidence={
            "historical_failure_reproduced": historical_failure,
            "compose_network_count": len(network_names),
            "retired_ix_network_count": sum(
                name.startswith("net_ix_") for name in network_names
            ),
            "active_runtime_compose_ix_reference_count": active_source.count(
                "compose_ix"
            ),
        },
        blockers=blockers,
    )


def _r136_useful_compose_build_timeout_check(run_dir: Path) -> Dict[str, Any]:
    catches = [
        "r136 killed a progressing 418-image cold build at the retired 900-second hard cap"
    ]
    failure_path = run_dir / "first_failure_summary.json"
    blockers: list[Dict[str, Any]] = []
    failure = dict(_read_json(failure_path)) if failure_path.is_file() else {}
    runtime_blocker = dict(failure.get("runtime_blocker") or {})
    if failure.get("first_failure_label") != "docker_compose_build_timeout":
        blockers.append({"reason": "r136_compose_build_timeout_not_reproduced"})
    if int(failure.get("runtime_epochs_completed") or 0) != 0:
        blockers.append({"reason": "r136_unexpectedly_reached_runtime"})
    if float(runtime_blocker.get("timeout_seconds") or 0.0) != 900.0:
        blockers.append({"reason": "r136_compose_build_timeout_evidence_mismatch"})
    profile = dict(
        _read_json(
            SATELLITE_DIR
            / "configs"
            / "validation_profiles"
            / "rich_s4_operational_clean_bpf.json"
        )
    )
    current_timeout = float(
        dict(profile.get("runner_args") or {}).get(
            "compose_build_timeout_seconds", -1
        )
    )
    if current_timeout != 0.0:
        blockers.append({"reason": "current_profile_compose_build_hard_cap_present"})
    return _check(
        name="r136_useful_compose_build_timeout_removed",
        catches=catches,
        ok=not blockers,
        evidence={
            "historical_timeout_seconds": runtime_blocker.get("timeout_seconds"),
            "historical_wall_seconds": runtime_blocker.get("wall_seconds"),
            "runtime_epochs_completed": int(
                failure.get("runtime_epochs_completed") or 0
            ),
            "current_profile_timeout_seconds": current_timeout,
        },
        blockers=blockers,
    )


def _r138_runtime_consumer_contract_replay_check(
    failed_run_dir: Path,
    replay_report_path: Path,
) -> Dict[str, Any]:
    catches = [
        "r138 completed all startup work then rejected the first valid empty reapplied consumer scope",
        "internal-only maintenance recomputed retired ix bindings after the prepared gate",
        "kernel 5.4 MPLS pop lost its BPF-owned manual fallback during the shared G/Z refactor",
    ]
    blockers: list[Dict[str, Any]] = []
    failure_path = failed_run_dir / "first_failure_summary.json"
    failure = dict(_read_json(failure_path)) if failure_path.is_file() else {}
    expected_failure = (
        "active return-state accounting requires "
        "shared_gz_fec_with_zone_edge_delivery"
    )
    if failure.get("failure_reason") != expected_failure:
        blockers.append({"reason": "r138_runtime_consumer_failure_not_reproduced"})
    if int(failure.get("runtime_epochs_completed") or 0) != 0:
        blockers.append({"reason": "r138_unexpected_runtime_epoch_completion"})
    startup = dict(failure.get("startup_materialization") or {})
    if float(startup.get("progress_ratio") or 0.0) != 1.0:
        blockers.append({"reason": "r138_full_startup_cost_evidence_missing"})

    report = (
        dict(_read_json(replay_report_path))
        if replay_report_path.is_file()
        else {}
    )
    gate = dict(report.get("gate") or {})
    horizon_counts = {
        str(key): int(value)
        for key, value in dict(gate.get("horizon_counts") or {}).items()
    }
    if report.get("schema_version") != (
        "skyseed-prepared-runtime-consumer-contract-replay-v1"
    ):
        blockers.append({"reason": "runtime_consumer_replay_schema_mismatch"})
    if report.get("ok") is not True or gate.get("ok") is not True:
        blockers.append({"reason": "runtime_consumer_replay_not_ready"})
    if int(gate.get("record_count") or 0) != 100:
        blockers.append({"reason": "runtime_consumer_replay_record_count_mismatch"})
    if horizon_counts != {"H0": 25, "H1": 25, "H2": 25, "H3": 25}:
        blockers.append({"reason": "runtime_consumer_replay_horizon_coverage_mismatch"})
    if dict(gate.get("transition_counts") or {}) != {"shared_gz_noop_hold": 100}:
        blockers.append({"reason": "runtime_consumer_replay_transition_mismatch"})
    if list(gate.get("blockers") or []) or list(report.get("blockers") or []):
        blockers.append({"reason": "runtime_consumer_replay_has_blockers"})
    if gate.get("mini_internet_explicitly_disabled") is not True:
        blockers.append({"reason": "runtime_consumer_replay_internal_only_mode_missing"})
    if int(gate.get("consumer_binding_ref_count") or 0) <= 0:
        blockers.append({"reason": "runtime_consumer_binding_replay_empty"})
    retired_ix_binding_count = gate.get(
        "retired_ix_consumer_binding_ref_count"
    )
    if retired_ix_binding_count is None or int(retired_ix_binding_count) != 0:
        blockers.append({"reason": "runtime_consumer_replay_retired_ix_binding_present"})
    bpf_source = (
        SATELLITE_DIR
        / "experimental"
        / "bpf"
        / "skyseed_tc_mpls_dataplane.c"
    ).read_text(encoding="utf-8")
    bpf_loader_source = (SATELLITE_DIR / "bpf_mpls_live.py").read_text(
        encoding="utf-8"
    )
    required_pop_fragments = (
        "static __always_inline int pop_mpls_manual",
        "bpf_skb_change_tail",
        "COUNTER_POP_MANUAL_FALLBACK",
        "COUNTER_POP_MANUAL_FAIL",
        "if (pop_mpls_manual(skb, handoff->inner_ethertype) < 0)",
    )
    missing_pop_fragments = [
        fragment for fragment in required_pop_fragments if fragment not in bpf_source
    ]
    if missing_pop_fragments:
        blockers.append(
            {
                "reason": "kernel_5_4_manual_mpls_pop_contract_missing",
                "missing_fragments": missing_pop_fragments,
            }
        )
    if (
        '16: "pop_manual_fallback"' not in bpf_loader_source
        or '17: "pop_manual_fail"' not in bpf_loader_source
    ):
        blockers.append({"reason": "manual_mpls_pop_observability_missing"})
    records = [dict(item or {}) for item in list(gate.get("records") or [])]
    invalid_record_count = sum(
        1
        for record in records
        if record.get("ok") is not True
        or int(record.get("active_flow_count") or 0) <= 0
        or int(record.get("changed_flow_count") or 0) != 0
        or int(record.get("reapplied_return_intent_count") or 0) != 0
        or record.get("ping_fallback_reason") != "no_reapplied_flows"
        or int(record.get("ping_selected_flow_count") or 0) <= 0
    )
    if invalid_record_count:
        blockers.append(
            {
                "reason": "runtime_consumer_replay_invalid_records",
                "count": invalid_record_count,
            }
        )
    return _check(
        name="r138_prepared_runtime_consumer_contract_replay",
        catches=catches,
        ok=not blockers,
        evidence={
            "historical_failure_reason": failure.get("failure_reason"),
            "historical_startup_progress_ratio": startup.get("progress_ratio"),
            "historical_runtime_epochs_completed": int(
                failure.get("runtime_epochs_completed") or 0
            ),
            "replay_report_path": str(replay_report_path),
            "replayed_record_count": int(gate.get("record_count") or 0),
            "horizon_counts": horizon_counts,
            "transition_counts": dict(gate.get("transition_counts") or {}),
            "mini_internet_explicitly_disabled": gate.get(
                "mini_internet_explicitly_disabled"
            ),
            "consumer_binding_ref_count": int(
                gate.get("consumer_binding_ref_count") or 0
            ),
            "retired_ix_consumer_binding_ref_count": int(
                gate.get("retired_ix_consumer_binding_ref_count") or 0
            ),
            "manual_mpls_pop_contract_present": not missing_pop_fragments,
            "invalid_record_count": invalid_record_count,
        },
        blockers=blockers,
    )


class _FakeExecutor:
    def __init__(self) -> None:
        self.restore_calls: list[Dict[str, Any]] = []
        self.prepare_calls: list[Dict[str, Any]] = []

    def restore_prepared_runtime_state(self, **kwargs: Any) -> Dict[str, Any]:
        self.restore_calls.append(dict(kwargs))
        return {"ok": True, "restored_for_process_worker": True}

    def prepare_additive_resources(self, **kwargs: Any) -> Dict[str, Any]:
        self.prepare_calls.append(dict(kwargs))
        return {
            "ok": True,
            "additive_prepare_completed": True,
            "prepared_resource_scope_by_slot": dict(
                kwargs.get("prepared_resource_scope_by_slot") or {}
            ),
            "delta_prepare_resource_scope_by_slot": dict(
                kwargs.get("prepared_resource_scope_by_slot") or {}
            ),
        }

    def capture_existing_container_interface_bindings(
        self, *, interface_refs: Sequence[str]
    ) -> Dict[str, Any]:
        return {
            "ok": True,
            "full_scan": False,
            "bindings": [
                {
                    "interface_ref": str(interface_ref),
                    "ifindex": 1_000_000 + index,
                    "mac_address": _stable_local_mac(str(interface_ref)),
                    "binding_source": (
                        "targeted_existing_interface_prepare_operation_result"
                    ),
                }
                for index, interface_ref in enumerate(interface_refs)
            ],
        }


def _run_worker_replay(
    work_path: Path,
    *,
    force_no_delta: bool = False,
) -> Dict[str, Any]:
    payload = dict(_read_json(work_path))
    payload["schema_version"] = "skyseed-horizon-maintenance-process-work-v3"
    payload["frozen_container_pid_cache"] = {"contract-replay": 1}
    # Historical failure artifacts predate the explicit attach worker limit.
    # The replay supplies the current canonical frozen value; production
    # payloads must carry it and the worker fails closed when it is absent.
    payload["max_node_workers"] = 2
    readiness = dict(payload.get("bpf_owned_readiness") or {})
    details = []
    for index, item in enumerate(
        list(readiness.get("attached_interface_details") or [])
    ):
        detail = dict(item or {})
        detail.setdefault("mac_address", f"02:00:00:00:{index // 256:02x}:{index % 256:02x}")
        details.append(detail)
    readiness["attached_interface_details"] = details
    compiled_runtime_inputs = dict(readiness.get("compiled_runtime_inputs") or {})
    existing_binding = dict(
        compiled_runtime_inputs.get("frozen_interface_binding_cache") or {}
    )
    if not (
        existing_binding.get("complete") is True
        and existing_binding.get("live_discovery_allowed") is False
        and existing_binding.get("ifindex_by_interface_ref")
        and existing_binding.get("mac_by_interface_ref")
    ):
        existing_binding = {
            "ifindex_by_interface_ref": {
                str(detail.get("interface_ref") or detail.get("interface_name") or ""):
                int(detail.get("ifindex") or index + 1)
                for index, detail in enumerate(details)
                if str(
                    detail.get("interface_ref")
                    or detail.get("interface_name")
                    or ""
                )
            },
            "mac_by_interface_ref": {
                str(detail.get("interface_ref") or detail.get("interface_name") or ""):
                str(detail.get("mac_address") or "")
                for detail in details
                if str(
                    detail.get("interface_ref")
                    or detail.get("interface_name")
                    or ""
                )
                and str(detail.get("mac_address") or "")
            },
            "complete": True,
            "live_discovery_allowed": False,
        }
    readiness["compiled_runtime_inputs"] = {
        **_upgrade_compiled_inputs_manifest(compiled_runtime_inputs),
        "frozen_interface_binding_cache": existing_binding,
    }
    payload["bpf_owned_readiness"] = readiness
    base_generation_record = dict(payload.get("base_generation_record") or {})
    base_generation_record["compiled_runtime_inputs"] = {
        **_upgrade_compiled_inputs_manifest(
            dict(base_generation_record.get("compiled_runtime_inputs") or {})
        ),
        "frozen_interface_binding_cache": existing_binding,
    }
    payload["base_generation_record"] = base_generation_record
    if force_no_delta:
        payload["delta_prepare_scope_by_slot"] = {}
        payload["prepared_resource_scope_by_slot"] = dict(
            payload.get("existing_prepared_resource_scope_by_slot") or {}
        )
    topology_path = Path(str(payload.get("topology_path") or ""))
    link_state_path = Path(str(payload.get("link_state_path") or ""))

    def compile_maps(**kwargs: Any) -> Dict[str, Any]:
        return _compiled_inputs(int(kwargs.get("generation_id") or 0))

    def attach_prepare(**_: Any) -> Dict[str, Any]:
        compiled = _compiled_inputs(0)
        budget = dict(payload.get("resident_attach_coverage_budget") or {})
        return {
            "ok": True,
            "attached_interfaces": list(compiled["attached_interfaces"]),
            "attached_interface_details": list(compiled["attached_interface_details"]),
            "attach_plan": dict(compiled["attach_plan"]),
            "attach_cardinality_budget": budget,
            "prepare_steps": {
                "preactivation_attach_prepare": {
                    "attach_cardinality_budget": budget,
                }
            },
        }

    def preload(**kwargs: Any) -> Dict[str, Any]:
        return {
            "ok": True,
            "inactive_generation_written": True,
            "active_generation_flip_skipped": True,
            "runtime_apply_skipped": True,
            "delta_attach_count": 0,
            "full_attach_count": 0,
            "runtime_full_generation_reload_count": 0,
            "compiled_runtime_inputs": dict(
                kwargs.get("frozen_compiled_runtime_inputs") or _compiled_inputs(0)
            ),
            "compiled_runtime_inputs_source": "pre_long_run_contract_replay",
            "preactivation_preload": {
                "manifest_only_loader": True,
                "batch_map_write_used": True,
                "actual_data_write_count": 1,
            },
        }

    fake_executor = _FakeExecutor()
    with tempfile.TemporaryDirectory() as tmpdir:
        payload["segment_dir"] = tmpdir
        raw_work_items = _load_preload_work_items(payload)
        if raw_work_items and all(
            dict(item or {}).get("context_ref") for item in raw_work_items
        ):
            raw_work_items = _hydrate_preload_work_item_contexts(
                payload,
                raw_work_items,
            )
        compact_work_items: list[Dict[str, Any]] = []
        context_records: list[Dict[str, Any]] = []
        for index, item_any in enumerate(raw_work_items):
            item = dict(item_any or {})
            queued_record = dict(item.get("queued_record") or {})
            segment_epoch_key = str(
                queued_record.get("segment_epoch_key")
                or dict(item.get("context_ref") or {}).get("segment_epoch_key")
                or f"contract-replay:{index}"
            )
            context = dict(item.pop("context", {}) or {})
            if not context:
                raise RuntimeError(
                    "contract_replay_missing_horizon_maintenance_context:"
                    + segment_epoch_key
                )
            item["context_ref"] = {
                "schema_version": "skyseed-horizon-maintenance-context-ref-v1",
                "segment_epoch_key": segment_epoch_key,
            }
            compact_work_items.append(item)
            context_records.append(
                {"segment_epoch_key": segment_epoch_key, "context": context}
            )
        context_path = Path(tmpdir) / "horizon_maintenance_contexts.jsonl"
        context_bytes = b"".join(
            (
                json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode("utf-8")
            for record in context_records
        )
        context_path.write_bytes(context_bytes)
        payload["preload_work_items"] = compact_work_items
        payload.pop("preload_work_items_artifact", None)
        payload["preload_context_artifact"] = {
            "schema_version": "skyseed-horizon-maintenance-context-artifact-v1",
            "path": str(context_path),
            "sha256": hashlib.sha256(context_bytes).hexdigest(),
            "record_count": len(context_records),
            "size_bytes": len(context_bytes),
        }
        with (
            patch(
                "horizon_maintenance_process_worker.MinimalRealNetworkMutationExecutor",
                return_value=fake_executor,
            ),
            patch(
                "horizon_maintenance_process_worker._realization_contexts",
                return_value=("offline-context",),
            ) as contexts_mock,
            patch(
                "horizon_maintenance_process_worker."
                "_bpf_owned_interface_binding_refs_for_contexts",
                return_value=["netns:offline-contract:if0"],
            ),
            patch(
                "horizon_maintenance_process_worker."
                "_bpf_owned_freeze_generation_compiled_runtime_inputs",
                side_effect=compile_maps,
            ),
            patch(
                "horizon_maintenance_process_worker."
                "_prepare_bpf_owned_edge_route_baseline",
                return_value={
                    "report": {
                        "ready": True,
                        "ok": True,
                        "prepared_route_identity_evidence": {
                            "schema_version": (
                                "skyseed-bpf-owned-prepared-route-identity-evidence-v1"
                            ),
                            "ut_default_route_count": 1,
                            "gateway_local_return_route_count": 1,
                            "ut_default_routes": [
                                {
                                    "key": [
                                        "ut-r128-replay",
                                        "ua0",
                                        "10.0.0.1",
                                        "10.0.0.2",
                                    ],
                                    "ut_default_route_present": True,
                                }
                            ],
                            "gateway_local_return_routes": [
                                {
                                    "key": [
                                        "gw-r128-replay",
                                        "ga0",
                                        "10.1.0.1",
                                        "10.1.0.2",
                                        "10.0.0.2",
                                    ],
                                    "gw_bpf_local_return_route_present": True,
                                }
                            ],
                        },
                    }
                },
            ),
            patch(
                "horizon_maintenance_process_worker._prepare_bpf_owned_preactivation_attach_capability_from_frozen_inputs",
                side_effect=attach_prepare,
            ),
            patch(
                "horizon_maintenance_process_worker._prepare_bpf_owned_preactivation_preload_from_frozen_inputs",
                side_effect=preload,
            ),
            patch(
                "horizon_maintenance_process_worker._execute_retired_cleanup_work_items",
                return_value={
                    "completed_records": [],
                    "blocked_records": [],
                    "wall_seconds": 0.0,
                },
            ),
        ):
            summary = run_horizon_maintenance_process_payload(payload)
    used_path = ""
    if contexts_mock.call_args is not None:
        used_path = str(contexts_mock.call_args.kwargs.get("topology_path") or "")
    return {
        "summary": summary,
        "force_no_delta": force_no_delta,
        "restore_call_count": len(fake_executor.restore_calls),
        "prepare_call_count": len(fake_executor.prepare_calls),
        "topology_path": str(topology_path),
        "link_state_path": str(link_state_path),
        "topology_path_exists": topology_path.exists() and topology_path.is_file(),
        "link_state_path_exists": link_state_path.exists() and link_state_path.is_file(),
        "topology_path_used_by_contexts": used_path,
    }


def _worker_process_replay_check(run_dir: Path) -> Dict[str, Any]:
    work_paths = sorted(
        run_dir.glob("replenished_segments/segment_*/horizon_maintenance_process_work.json")
    )
    blockers: list[dict[str, Any]] = []
    if not work_paths:
        return _check(
            name="horizon_worker_process_replay",
            catches=["r113 H1 worker Path('.') process-boundary failure"],
            ok=False,
            blockers=[{"reason": "horizon_worker_process_work_missing"}],
        )
    selected = work_paths[0]
    try:
        replay = _run_worker_replay(selected)
        no_delta_replay = _run_worker_replay(selected, force_no_delta=True)
    except Exception as exc:  # noqa: BLE001
        return _check(
            name="horizon_worker_process_replay",
            catches=["r113 H1 worker Path('.') process-boundary failure"],
            ok=False,
            evidence={"work_path": str(selected)},
            blockers=[
                {
                    "reason": "worker_replay_failed",
                    "exception_type": exc.__class__.__name__,
                    "message": str(exc),
                }
            ],
        )
    summary = dict(replay.get("summary") or {})
    no_delta_summary = dict(no_delta_replay.get("summary") or {})
    if summary.get("ready") is not True or str(summary.get("status") or "") != "ready":
        blockers.append(
            {
                "reason": "worker_replay_not_ready",
                "status": str(summary.get("status") or ""),
                "ready": bool(summary.get("ready")),
            }
        )
    if summary.get("edge_route_baseline_prepare_ok") is not True:
        blockers.append({"reason": "worker_edge_route_baseline_not_ready"})
    if replay.get("topology_path_exists") is not True:
        blockers.append({"reason": "payload_topology_path_missing"})
    if replay.get("link_state_path_exists") is not True:
        blockers.append({"reason": "payload_link_state_path_missing"})
    if replay.get("topology_path_used_by_contexts") != replay.get("topology_path"):
        blockers.append(
            {
                "reason": "worker_context_topology_path_not_payload_path",
                "expected": replay.get("topology_path"),
                "actual": replay.get("topology_path_used_by_contexts"),
            }
        )
    if (
        no_delta_summary.get("ready") is not True
        or str(no_delta_summary.get("status") or "") != "ready"
    ):
        blockers.append({"reason": "worker_no_delta_replay_not_ready"})
    if int(no_delta_replay.get("restore_call_count") or 0) != 1:
        blockers.append({"reason": "worker_no_delta_prepared_state_not_restored"})
    if int(no_delta_replay.get("prepare_call_count") or 0) != 0:
        blockers.append({"reason": "worker_no_delta_physical_prepare_reached"})
    return _check(
        name="horizon_worker_process_replay",
        catches=[
            "r113 H1 worker Path('.') process-boundary failure",
            "worker payload/context physical path drift",
        ],
        ok=not blockers,
        evidence={
            "work_path": str(selected),
            "preload_work_item_count": int(summary.get("preload_work_item_count") or 0),
            "inactive_preload_record_count": int(
                summary.get("inactive_preload_record_count") or 0
            ),
            "edge_route_baseline_prepare_ok": bool(
                summary.get("edge_route_baseline_prepare_ok")
            ),
            "topology_path_used_by_contexts": replay.get(
                "topology_path_used_by_contexts"
            ),
            "no_delta_ready": bool(no_delta_summary.get("ready")),
            "no_delta_restore_call_count": int(
                no_delta_replay.get("restore_call_count") or 0
            ),
            "no_delta_physical_prepare_call_count": int(
                no_delta_replay.get("prepare_call_count") or 0
            ),
        },
        blockers=blockers,
    )


def _simulated_additive_creation_bindings(
    *,
    source: Mapping[str, Any],
    topology_path: Path,
    interface_ifindex_cache: Mapping[str, int],
    interface_mac_cache: Mapping[str, str],
) -> list[Dict[str, Any]]:
    installer = DirectNetnsVethSlotLinkNamespaceQdiscInstaller()
    topology = installer._load_topology(topology_path)
    inventory = installer._inventory(topology, topology_path)
    existing_scope = {
        str(slot_name): [str(resource_key) for resource_key in list(resource_keys or [])]
        for slot_name, resource_keys in dict(
            source.get("existing_prepared_resource_scope_by_slot") or {}
        ).items()
    }
    delta_scope = {
        str(slot_name): [str(resource_key) for resource_key in list(resource_keys or [])]
        for slot_name, resource_keys in dict(
            source.get("delta_prepare_scope_by_slot") or {}
        ).items()
    }
    endpoints: Dict[str, tuple[str, Dict[str, Any]]] = {}
    for scope, include_preconnected in (
        (existing_scope, True),
        (delta_scope, False),
    ):
        for slot_name in sorted(scope):
            slot = inventory.get(slot_name)
            if slot is None:
                raise RuntimeError(f"gate_additive_prepare_missing_slot:{slot_name}")
            if include_preconnected or slot_name not in existing_scope:
                preconnected = installer._preconnected_endpoint(slot)
                if preconnected is not None:
                    interface_ref = (
                        f"netns:{preconnected['node_id']}:{preconnected['interface_name']}"
                    )
                    endpoints[interface_ref] = (slot_name, dict(preconnected))
            resources = installer._prepared_resources_for_slot(
                slot=slot,
                prepared_resource_scope_by_slot={slot_name: scope[slot_name]},
            )
            if len(resources) != len(scope[slot_name]):
                raise RuntimeError(
                    f"gate_additive_prepare_resource_scope_mismatch:{slot_name}:"
                    f"expected={len(scope[slot_name])}:actual={len(resources)}"
                )
            for resource in resources.values():
                for endpoint_any in list(resource.get("endpoints") or []):
                    endpoint = dict(endpoint_any)
                    interface_ref = (
                        f"netns:{endpoint['node_id']}:{endpoint['interface_name']}"
                    )
                    endpoints[interface_ref] = (slot_name, endpoint)
    bindings: list[Dict[str, Any]] = []
    next_synthetic_ifindex = 1_000_000
    for interface_ref in sorted(endpoints):
        slot_name, endpoint = endpoints[interface_ref]
        ifindex = interface_ifindex_cache.get(interface_ref)
        if ifindex is None:
            ifindex = next_synthetic_ifindex
            next_synthetic_ifindex += 1
        ip_address = str(endpoint.get("ip_address") or "")
        bindings.append(
            {
                "interface_ref": interface_ref,
                "ifindex": int(ifindex),
                "mac_address": str(
                    interface_mac_cache.get(interface_ref)
                    or _stable_local_mac(
                        f"{slot_name}:{endpoint['interface_name']}:{ip_address}"
                    )
                ),
                "binding_source": "created_veth_operation_result",
            }
        )
    return bindings


def _actual_frozen_generation_compile_check(run_dir: Path) -> Dict[str, Any]:
    work_paths = sorted(
        run_dir.glob(
            "replenished_segments/segment_*/horizon_maintenance_process_work.json"
        )
    )
    if not work_paths:
        return _check(
            name="actual_frozen_generation_compile",
            catches=["r116 attach-only binding source omitted UT handoff endpoint MAC"],
            ok=False,
            blockers=[{"reason": "worker_process_work_missing"}],
        )
    work_path = work_paths[0]
    source = dict(_read_json(work_path))
    topology_path = Path(str(source.get("topology_path") or ""))
    base_record = dict(source.get("base_generation_record") or {})
    base_inputs = dict(base_record.get("compiled_runtime_inputs") or {})
    budget = dict(
        source.get("resident_attach_coverage_budget")
        or source.get("bpf_attach_budget")
        or base_inputs.get("resident_attach_coverage_budget")
        or base_inputs.get("bpf_attach_budget")
        or {}
    )
    work_items = _load_preload_work_items(source)
    blockers: list[dict[str, Any]] = []
    compiled_count = 0
    started = time.perf_counter()
    try:
        ifindex_cache, mac_cache = _base_generation_interface_binding_caches(
            base_inputs
        )
        creation_bindings = _simulated_additive_creation_bindings(
            source=source,
            topology_path=topology_path,
            interface_ifindex_cache=ifindex_cache,
            interface_mac_cache=mac_cache,
        )
        _merge_created_interface_bindings(
            interface_ifindex_cache=ifindex_cache,
            interface_mac_cache=mac_cache,
            bindings=creation_bindings,
        )
        with patch(
            "satellite.bpf_mpls_live.interface_ifindex",
            side_effect=AssertionError("live ifindex discovery reached"),
        ), patch(
            "satellite.bpf_mpls_live.interface_mac",
            side_effect=AssertionError("live MAC discovery reached"),
        ):
            for item_any in work_items:
                frozen = _freeze_preload_work_item(
                    item=_current_shared_gz_historical_work_item(
                        dict(item_any or {})
                    ),
                    base_generation_compiled_inputs=base_inputs,
                    resident_attach_budget=budget,
                    topology_path=topology_path,
                    interface_ifindex_cache=ifindex_cache,
                    interface_mac_cache=mac_cache,
                )
                check = dict(
                    frozen.get("frozen_compiled_runtime_inputs_check") or {}
                )
                if check.get("ready") is not True:
                    raise RuntimeError(
                        "actual_frozen_generation_compile_not_ready:"
                        + str(check.get("reason") or "unknown")
                    )
                compiled_count += 1
    except Exception as exc:
        blockers.append(
            {
                "reason": "actual_frozen_generation_compile_failed",
                "exception_type": type(exc).__name__,
                "message": str(exc),
                "compiled_count": compiled_count,
            }
        )
    expected_count = len(work_items)
    if not blockers and compiled_count != expected_count:
        blockers.append(
            {
                "reason": "actual_frozen_generation_compile_count_mismatch",
                "expected": expected_count,
                "actual": compiled_count,
            }
        )
    return _check(
        name="actual_frozen_generation_compile",
        catches=[
            "r115 repeated live interface discovery inside each generation compile",
            "r116 attach-only binding source omitted UT handoff endpoint MAC",
        ],
        ok=not blockers,
        evidence={
            "work_path": str(work_path),
            "expected_compile_count": expected_count,
            "compiled_count": compiled_count,
            "simulated_physical_creation_binding_count": len(
                locals().get("creation_bindings", [])
            ),
            "wall_seconds": round(time.perf_counter() - started, 6),
        },
        blockers=blockers,
    )


def _final_binding_conflict_replay_check(run_dir: Path) -> Dict[str, Any]:
    work_paths = sorted(
        run_dir.glob(
            "replenished_segments/segment_*/horizon_maintenance_process_work.json"
        )
    )
    if not work_paths:
        return _check(
            name="final_binding_conflict_replay",
            catches=["r119 readiness detail conflicted with the active base cache"],
            ok=False,
            blockers=[{"reason": "r119_worker_process_work_missing"}],
        )
    work_path = work_paths[0]
    source = dict(_read_json(work_path))
    topology_path = Path(str(source.get("topology_path") or ""))
    link_state_path = Path(str(source.get("link_state_path") or ""))
    work_items_path = Path(str(source.get("preload_work_items_artifact") or ""))
    summary_path = work_path.parent / "horizon_maintenance_process_summary.json"
    blockers: list[Dict[str, Any]] = []
    compiled_count = 0
    final_bindings: list[Dict[str, Any]] = []
    creation_bindings: list[Dict[str, Any]] = []
    required_ifindex_refs: set[str] = set()
    required_mac_refs: set[str] = set()
    old_conflicts: list[Dict[str, Any]] = []
    final_merge: Dict[str, Any] = {}
    target_ref = "netns:stl-940018:fs0018"
    target_base_mac = ""
    target_readiness_mac = ""
    target_pre_final_mac = ""
    target_final_mac = ""
    final_source_counts: Dict[str, int] = {}
    started = time.perf_counter()
    try:
        if not topology_path.is_file():
            raise RuntimeError(f"r119_topology_missing:{topology_path}")
        if not link_state_path.is_file():
            raise RuntimeError(f"r119_link_state_missing:{link_state_path}")
        if not work_items_path.is_file():
            raise RuntimeError(f"r119_work_items_missing:{work_items_path}")
        # Reading both physical inputs is part of the replay contract. The
        # topology is consumed below; link-state is immutable handoff evidence.
        _read_json(link_state_path)
        if list(source.get("preload_work_items") or []):
            raise RuntimeError("r119_work_items_not_compact_artifact_owned")
        work_items = [
            _current_shared_gz_historical_work_item(item)
            for item in _load_preload_work_items(source)
        ]
        if len(work_items) != 25:
            raise RuntimeError(
                f"r119_preload_work_item_count_mismatch:expected=25:actual={len(work_items)}"
            )

        base_record = dict(source.get("base_generation_record") or {})
        base_inputs = dict(base_record.get("compiled_runtime_inputs") or {})
        ifindex_cache, mac_cache = _base_generation_interface_binding_caches(
            base_inputs
        )
        target_base_mac = str(mac_cache.get(target_ref) or "")

        readiness_details = {
            str(
                dict(detail_any or {}).get("interface_ref")
                or dict(detail_any or {}).get("interface_name")
                or ""
            ): dict(detail_any or {})
            for detail_any in list(
                dict(source.get("bpf_owned_readiness") or {}).get(
                    "attached_interface_details"
                )
                or []
            )
            if str(
                dict(detail_any or {}).get("interface_ref")
                or dict(detail_any or {}).get("interface_name")
                or ""
            )
        }
        target_readiness_mac = str(
            dict(readiness_details.get(target_ref) or {}).get("mac_address") or ""
        )
        for interface_ref, detail in sorted(readiness_details.items()):
            old_ifindex = ifindex_cache.get(interface_ref)
            new_ifindex = detail.get("ifindex")
            old_mac = str(mac_cache.get(interface_ref) or "")
            new_mac = str(detail.get("mac_address") or "")
            if (
                old_ifindex not in (None, "", "None")
                and new_ifindex not in (None, "", "None")
                and int(old_ifindex) != int(new_ifindex)
            ) or (old_mac and new_mac and old_mac != new_mac):
                old_conflicts.append(
                    {
                        "interface_ref": interface_ref,
                        "base_ifindex": int(old_ifindex or 0),
                        "readiness_ifindex": int(new_ifindex or 0),
                        "base_mac": old_mac,
                        "readiness_mac": new_mac,
                    }
                )
        if not any(item["interface_ref"] == target_ref for item in old_conflicts):
            raise RuntimeError("r119_expected_fs0018_old_merge_conflict_missing")
        if not summary_path.is_file():
            raise RuntimeError("r119_process_summary_missing")
        failed_summary = dict(_read_json(summary_path))
        if str(failed_summary.get("reason") or "") != (
            "horizon_maintenance_frozen_mac_conflict:" + target_ref
        ):
            raise RuntimeError("r119_process_summary_conflict_reason_mismatch")

        creation_bindings = _simulated_additive_creation_bindings(
            source=source,
            topology_path=topology_path,
            interface_ifindex_cache=ifindex_cache,
            interface_mac_cache=mac_cache,
        )
        _merge_created_interface_bindings(
            interface_ifindex_cache=ifindex_cache,
            interface_mac_cache=mac_cache,
            bindings=creation_bindings,
        )
        target_pre_final_mac = str(mac_cache.get(target_ref) or "")
        if target_pre_final_mac != target_base_mac:
            raise RuntimeError("r119_readiness_merged_before_final_boundary")

        required_ifindex_refs, required_mac_refs, _route_contexts = (
            _required_interface_refs_for_preload_work_items(
                items=work_items,
                topology_path=topology_path,
            )
        )
        final_refs = sorted(required_ifindex_refs | required_mac_refs)
        if not final_refs or any(
            not interface_ref.startswith("netns:") for interface_ref in final_refs
        ):
            raise RuntimeError("r119_final_binding_refs_not_explicit_netns_refs")

        created_by_ref = {
            str(item.get("interface_ref") or ""): dict(item)
            for item in creation_bindings
        }
        for interface_ref in final_refs:
            source_name = "active_base_generation_cache"
            ifindex = ifindex_cache.get(interface_ref)
            mac_address = str(mac_cache.get(interface_ref) or "")
            if interface_ref in created_by_ref:
                source_name = "additive_creation_fact"
                ifindex = created_by_ref[interface_ref].get("ifindex")
                mac_address = str(
                    created_by_ref[interface_ref].get("mac_address") or ""
                )
            # r119 readiness details are used only as recorded post-prepare
            # kernel facts for the targeted final-capture boundary. They are
            # never merged into the initial cache.
            if interface_ref in readiness_details:
                source_name = "r119_recorded_final_kernel_fact"
                ifindex = readiness_details[interface_ref].get("ifindex")
                mac_address = str(
                    readiness_details[interface_ref].get("mac_address") or ""
                )
            if ifindex in (None, "", "None") or int(ifindex) <= 0 or not mac_address:
                raise RuntimeError(
                    f"r119_final_binding_fact_missing:{interface_ref}"
                )
            final_source_counts[source_name] = (
                final_source_counts.get(source_name, 0) + 1
            )
            final_bindings.append(
                {
                    "interface_ref": interface_ref,
                    "ifindex": int(ifindex),
                    "mac_address": mac_address,
                    "binding_source": (
                        "targeted_existing_interface_prepare_operation_result"
                    ),
                }
            )
        _validate_final_interface_binding_scope(
            required_interface_refs=final_refs,
            bindings=final_bindings,
        )
        final_merge = _merge_final_interface_bindings(
            interface_ifindex_cache=ifindex_cache,
            interface_mac_cache=mac_cache,
            bindings=final_bindings,
        )
        target_final_mac = str(mac_cache.get(target_ref) or "")
        if not target_readiness_mac or target_final_mac != target_readiness_mac:
            raise RuntimeError("r119_final_capture_did_not_override_fs0018_mac")

        budget = dict(
            source.get("resident_attach_coverage_budget")
            or source.get("bpf_attach_budget")
            or base_inputs.get("resident_attach_coverage_budget")
            or base_inputs.get("bpf_attach_budget")
            or {}
        )
        with patch(
            "satellite.bpf_mpls_live.interface_ifindex",
            side_effect=AssertionError("live ifindex discovery reached"),
        ), patch(
            "satellite.bpf_mpls_live.interface_mac",
            side_effect=AssertionError("live MAC discovery reached"),
        ):
            for item_any in work_items:
                frozen = _freeze_preload_work_item(
                    item=_current_shared_gz_historical_work_item(
                        dict(item_any or {})
                    ),
                    base_generation_compiled_inputs=base_inputs,
                    resident_attach_budget=budget,
                    topology_path=topology_path,
                    interface_ifindex_cache=ifindex_cache,
                    interface_mac_cache=mac_cache,
                )
                frozen_check = dict(
                    frozen.get("frozen_compiled_runtime_inputs_check") or {}
                )
                if frozen_check.get("ready") is not True:
                    raise RuntimeError(
                        "r119_strict_compile_not_ready:"
                        + str(frozen_check.get("reason") or "unknown")
                    )
                compiled_count += 1
    except Exception as exc:  # noqa: BLE001
        blockers.append(
            {
                "reason": "final_binding_conflict_replay_failed",
                "exception_type": type(exc).__name__,
                "message": str(exc),
                "compiled_count": compiled_count,
            }
        )

    required_refs = required_ifindex_refs | required_mac_refs
    required_node_ids = {
        parts[1]
        for interface_ref in required_refs
        if len(parts := str(interface_ref).split(":", 2)) == 3
        and parts[0] == "netns"
        and parts[1]
    }
    return _check(
        name="final_binding_conflict_replay",
        catches=[
            "r119 readiness/base multi-source premerge MAC conflict",
            "final binding capture omitted or scanned outside the explicit 25-generation requirement set",
            "generation compile performed live ifindex/MAC lookup",
        ],
        ok=not blockers,
        evidence={
            "work_path": str(work_path),
            "work_items_path": str(work_items_path),
            "topology_path": str(topology_path),
            "link_state_path": str(link_state_path),
            "old_multisource_conflict_reproduced": bool(old_conflicts),
            "old_multisource_conflict_count": len(old_conflicts),
            "old_conflict_examples": old_conflicts[:5],
            "readiness_merged_before_final_boundary": False,
            "target_interface_ref": target_ref,
            "target_base_mac": target_base_mac,
            "target_readiness_mac": target_readiness_mac,
            "target_pre_final_mac": target_pre_final_mac,
            "target_final_mac": target_final_mac,
            "simulated_additive_creation_binding_count": len(creation_bindings),
            "required_ifindex_ref_count": len(required_ifindex_refs),
            "required_mac_ref_count": len(required_mac_refs),
            "required_final_ref_count": len(required_refs),
            "targeted_node_batch_count": len(required_node_ids),
            "targeted_nsenter_command_reduction_count": (
                len(required_refs) - len(required_node_ids)
            ),
            "targeted_node_batch_worker_cap": 16,
            "captured_final_ref_count": len(final_bindings),
            "final_capture_full_scan": False,
            "final_capture_exact_explicit_ref_set": bool(
                required_refs
                and {item["interface_ref"] for item in final_bindings}
                == required_refs
            ),
            "final_capture_source_counts": final_source_counts,
            "final_binding_merge": final_merge,
            "expected_compile_count": 25,
            "compiled_count": compiled_count,
            "live_interface_lookup_blocked_during_compile": compiled_count == 25,
            "wall_seconds": round(time.perf_counter() - started, 6),
        },
        blockers=blockers,
    )


def _parent_host_prepare_handoff_replay_check(run_dir: Path) -> Dict[str, Any]:
    work_paths = sorted(
        run_dir.glob(
            "replenished_segments/segment_*/horizon_maintenance_process_work.json"
        )
    )
    ready_paths = sorted(
        run_dir.glob(
            "replenished_segments/segment_*/horizon_maintenance_ready.json"
        )
    )
    normal_path = run_dir / "normal_runtime_inputs.json"
    events_path = run_dir / "events.jsonl"
    required_paths = [normal_path, events_path]
    if not work_paths or not ready_paths or any(not path.is_file() for path in required_paths):
        return _check(
            name="parent_host_prepare_handoff_replay",
            catches=["r120 parent host runtime omitted worker prepared ownership"],
            ok=False,
            blockers=[{"reason": "r120_host_handoff_artifacts_missing"}],
        )

    work_path = work_paths[0]
    ready_path = ready_paths[0]
    blockers: list[Dict[str, Any]] = []
    old_handoff_failure = ""
    parent_missing_consumer_resources: list[Dict[str, Any]] = []
    consumer_resources: list[Dict[str, Any]] = []
    handoff_report: Dict[str, Any] = {}
    existing_scope: Dict[str, list[str]] = {}
    delta_scope: Dict[str, list[str]] = {}
    old_ready_scope: Dict[str, list[str]] = {}
    expected_owned_scope: Dict[str, list[str]] = {}
    event_runtime_epoch_count = 0
    ready_consumed_event_index = -1
    failure_event_index = -1
    started = time.perf_counter()

    def _normalize_scope(scope: Mapping[str, Sequence[str]]) -> Dict[str, list[str]]:
        return {
            str(slot_name): sorted(
                {
                    str(resource_key)
                    for resource_key in list(resource_keys or [])
                    if str(resource_key)
                }
            )
            for slot_name, resource_keys in sorted(dict(scope or {}).items())
            if str(slot_name) and list(resource_keys or [])
        }

    try:
        work = dict(_read_json(work_path))
        ready = dict(_read_json(ready_path))
        host_prepare = dict(ready.get("host_additive_prepare_report") or {})
        existing_scope = _normalize_scope(
            dict(work.get("existing_prepared_resource_scope_by_slot") or {})
        )
        delta_scope = _normalize_scope(
            dict(work.get("delta_prepare_scope_by_slot") or {})
        )
        old_ready_scope = _normalize_scope(
            dict(host_prepare.get("prepared_resource_scope_by_slot") or {})
        )
        expected_owned_scope = _normalize_scope(
            {
                slot_name: [
                    *existing_scope.get(slot_name, []),
                    *delta_scope.get(slot_name, []),
                ]
                for slot_name in sorted(set(existing_scope) | set(delta_scope))
            }
        )
        if not existing_scope or not delta_scope or not expected_owned_scope:
            raise RuntimeError("r120_prepared_scope_evidence_incomplete")
        dropped_by_old_ready = {
            slot_name: sorted(
                set(resource_keys) - set(old_ready_scope.get(slot_name, []))
            )
            for slot_name, resource_keys in existing_scope.items()
            if set(resource_keys) - set(old_ready_scope.get(slot_name, []))
        }
        if not dropped_by_old_ready:
            raise RuntimeError("r120_old_ready_scope_drop_not_reproduced")

        first_plan_key = "H1:starlink-shell-slice-0001"
        first_host_plan = _read_compact_named_object(
            normal_path,
            section_name="host_network_mutation_plan_by_segment_key",
            object_name=first_plan_key,
        )
        operations = [dict(item) for item in list(first_host_plan.get("operations") or [])]
        if not operations:
            raise RuntimeError("r120_h1_first_host_plan_operations_missing")

        topology_path = Path(str(work.get("topology_path") or ""))
        link_state_path = Path(str(work.get("link_state_path") or ""))
        frozen_pid_cache = {
            str(node_id): int(pid)
            for node_id, pid in dict(work.get("frozen_container_pid_cache") or {}).items()
        }
        if not topology_path.is_file() or not link_state_path.is_file():
            raise RuntimeError("r120_worker_physical_paths_missing")
        installer = DirectNetnsVethSlotLinkNamespaceQdiscInstaller()
        installer._runtime_prepared = True
        installer._prepared_topology_path = str(run_dir / "topology.json")
        installer._prepared_link_state_path = str(run_dir / "link_state.json")
        installer._prepared_resource_keys_by_slot = {
            slot_name: set(resource_keys)
            for slot_name, resource_keys in existing_scope.items()
        }
        installer._prepared_resource_counts = {
            "bridge_count": len(existing_scope),
            "preconnected_endpoint_count": 0,
            "candidate_resource_count": sum(
                len(items) for items in existing_scope.values()
            ),
            "candidate_endpoint_count": 0,
        }
        installer._container_pid_cache = dict(frozen_pid_cache)
        installer._container_pid_cache_complete = True
        parent_topology_path = installer._prepared_topology_path
        parent_link_state_path = installer._prepared_link_state_path

        topology = installer._load_topology(topology_path)
        inventory = installer._inventory(topology, topology_path)
        for operation in operations:
            slot_name = str(operation.get("slot_name") or "")
            target_binding = dict(operation.get("target_binding") or {})
            if not slot_name or not target_binding:
                continue
            slot = inventory.get(slot_name)
            if slot is None:
                raise RuntimeError(f"r120_first_plan_slot_missing:{slot_name}")
            resource = installer._resource_for_binding(
                slot=slot,
                binding=target_binding,
            )
            record = {
                "operation": str(operation.get("operation") or ""),
                "slot_name": slot_name,
                "resource_key": str(resource.get("resource_key") or ""),
            }
            consumer_resources.append(record)
            try:
                installer._ensure_resource_prepared(slot=slot, resource=resource)
            except RuntimeError as exc:
                if "runtime_resource_materialization_forbidden" not in str(exc):
                    raise
                parent_missing_consumer_resources.append(record)
        if not parent_missing_consumer_resources:
            raise RuntimeError("r120_first_consumer_missing_resource_not_reproduced")
        if any(
            item["resource_key"] not in delta_scope.get(item["slot_name"], [])
            for item in parent_missing_consumer_resources
        ):
            raise RuntimeError("r120_first_consumer_missing_resource_not_in_delta")

        class ReplayExecutor:
            def __init__(self) -> None:
                self.accept_count = 0
                self.physical_work_count = 0

            def container_pid_cache_snapshot(self) -> Dict[str, int]:
                return installer.container_pid_cache_snapshot()

            def accept_horizon_maintenance_prepared_state(
                self, **kwargs: Any
            ) -> Dict[str, Any]:
                self.accept_count += 1
                return installer.accept_horizon_maintenance_prepared_state(**kwargs)

        replay_executor = ReplayExecutor()
        try:
            _accept_horizon_maintenance_host_prepare_handoff(
                network_executor=replay_executor,
                ready_artifact=ready,
            )
        except RuntimeError as exc:
            old_handoff_failure = str(exc)
        if "horizon_maintenance_handoff_dropped_prepared_resources" not in (
            old_handoff_failure
        ):
            raise RuntimeError("r120_old_parent_handoff_failure_not_reproduced")

        patched_host_prepare = {
            **host_prepare,
            "prepared_resource_scope_by_slot": expected_owned_scope,
            "prepared_owned_resource_scope_by_slot": expected_owned_scope,
            "prepared_resource_counts": {
                **dict(host_prepare.get("prepared_resource_counts") or {}),
                "bridge_count": len(expected_owned_scope),
                "candidate_resource_count": sum(
                    len(items) for items in expected_owned_scope.values()
                ),
            },
        }
        handoff_report = _accept_horizon_maintenance_host_prepare_handoff(
            network_executor=replay_executor,
            ready_artifact={
                **ready,
                "host_additive_prepare_report": patched_host_prepare,
            },
        )
        for slot_name, resource_keys in expected_owned_scope.items():
            for resource_key in resource_keys:
                installer._ensure_resource_prepared(
                    slot={"slot_name": slot_name},
                    resource={"resource_key": resource_key},
                )
        for item in consumer_resources:
            installer._ensure_resource_prepared(
                slot={"slot_name": item["slot_name"]},
                resource={"resource_key": item["resource_key"]},
            )
        if (
            installer._prepared_topology_path != parent_topology_path
            or installer._prepared_link_state_path != parent_link_state_path
        ):
            raise RuntimeError("r120_parent_active_paths_not_preserved")
        if replay_executor.physical_work_count != 0:
            raise RuntimeError("r120_handoff_performed_physical_work")

        events = [
            json.loads(line)
            for line in events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        event_runtime_epoch_count = sum(
            1 for item in events if item.get("event_type") == "runtime_epoch_result"
        )
        for index, event in enumerate(events):
            if (
                event.get("event_type") == "horizon_maintenance_ready_consumed"
                and str(event.get("horizon_id") or "") == "H1"
            ):
                ready_consumed_event_index = index
            if (
                event.get("event_type") == "runtime_exception"
                and "host-side epoch apply failed for H1:starlink-shell-slice-0001"
                in str(event.get("failure_reason") or "")
            ):
                failure_event_index = index
        if (
            event_runtime_epoch_count != 25
            or ready_consumed_event_index < 0
            or failure_event_index <= ready_consumed_event_index
        ):
            raise RuntimeError("r120_boundary_failure_event_sequence_mismatch")
    except Exception as exc:  # noqa: BLE001
        blockers.append(
            {
                "reason": "parent_host_prepare_handoff_replay_failed",
                "exception_type": type(exc).__name__,
                "message": str(exc),
            }
        )

    return _check(
        name="parent_host_prepare_handoff_replay",
        catches=[
            "r120 worker prepared physical resources were absent from the parent installer ownership index",
            "ready artifact published target-window scope instead of additive owned union",
            "H1 first host consumer reached an unregistered prepared resource",
        ],
        ok=not blockers,
        evidence={
            "work_path": str(work_path),
            "ready_path": str(ready_path),
            "normal_runtime_inputs_path": str(normal_path),
            "existing_parent_slot_count": len(existing_scope),
            "existing_parent_resource_count": sum(
                len(items) for items in existing_scope.values()
            ),
            "delta_slot_count": len(delta_scope),
            "delta_resource_count": sum(len(items) for items in delta_scope.values()),
            "old_ready_slot_count": len(old_ready_scope),
            "old_ready_resource_count": sum(
                len(items) for items in old_ready_scope.values()
            ),
            "expected_owned_slot_count": len(expected_owned_scope),
            "expected_owned_resource_count": sum(
                len(items) for items in expected_owned_scope.values()
            ),
            "old_handoff_failure": old_handoff_failure[:1000],
            "first_consumer_operation_resource_count": len(consumer_resources),
            "first_consumer_missing_before_handoff_count": len(
                parent_missing_consumer_resources
            ),
            "first_consumer_missing_before_handoff": (
                parent_missing_consumer_resources
            ),
            "all_owned_resources_accepted_after_handoff": bool(handoff_report),
            "all_first_consumer_resources_accepted_after_handoff": bool(
                handoff_report
            ),
            "parent_active_link_state_preserved": bool(
                handoff_report.get("parent_active_link_state_preserved")
            ),
            "physical_prepare_skipped": bool(
                handoff_report.get("physical_prepare_skipped")
            ),
            "live_host_scan_skipped": bool(
                handoff_report.get("live_host_scan_skipped")
            ),
            "live_container_scan_skipped": bool(
                handoff_report.get("live_container_scan_skipped")
            ),
            "hidden_reconcile_skipped": bool(
                handoff_report.get("hidden_reconcile_skipped")
            ),
            "r120_runtime_epoch_count": event_runtime_epoch_count,
            "r120_ready_consumed_before_failure": bool(
                ready_consumed_event_index >= 0
                and failure_event_index > ready_consumed_event_index
            ),
            "wall_seconds": round(time.perf_counter() - started, 6),
        },
        blockers=blockers,
    )


def _historical_r121_multicast_and_worker_cleanup_replay_check(
    run_dir: Path,
) -> Dict[str, Any]:
    events_path = run_dir / "events.jsonl"
    runtime_report_path = run_dir / "real_runtime_report.json"
    work_path = (
        run_dir
        / "replenished_segments"
        / "segment_001"
        / "horizon_maintenance_process_work.json"
    )
    summary_path = work_path.parent / "horizon_maintenance_process_summary.json"
    required = [events_path, runtime_report_path, work_path, summary_path]
    if not all(path.exists() for path in required):
        return _check(
            name="r121_multicast_and_worker_cleanup_replay",
            catches=[
                "r121 host mDNS classifier miss and concurrent worker cleanup race"
            ],
            ok=False,
            blockers=[{"reason": "r121_failure_artifacts_missing"}],
        )
    blockers: list[Dict[str, Any]] = []
    evidence: Dict[str, Any] = {}
    started = time.perf_counter()
    try:
        before_attribution = _read_last_compact_named_object(
            events_path,
            object_name="pre_observation_miss_drop_sample_attribution",
        )
        after_attribution = _read_last_compact_named_object(
            events_path,
            object_name="miss_drop_sample_attribution",
        )
        samples = [
            dict(item)
            for attribution in (before_attribution, after_attribution)
            for item in list(attribution.get("samples") or [])
        ]
        if len(samples) < 2 or any(
            str(sample.get("reason") or "") != "classifier_miss"
            or str(sample.get("dst_ip") or "") != "224.0.0.251"
            or str(sample.get("map_that_missed") or "")
            != "ingress_classifier_map"
            or not str(sample.get("src_ip") or "").endswith(".1")
            for sample in samples
        ):
            raise RuntimeError("r121_mdns_classifier_miss_not_reproduced")

        final_cleanup = _read_last_compact_named_object(
            runtime_report_path,
            object_name="final_cleanup",
        )
        cleanup_artifacts = dict(
            final_cleanup.get("post_cleanup_direct_backend_artifacts") or {}
        )
        previous_remaining_interfaces = list(
            cleanup_artifacts.get("interfaces") or []
        )
        if (
            final_cleanup.get("all_cleanup_success") is not False
            or not previous_remaining_interfaces
        ):
            raise RuntimeError("r121_concurrent_worker_cleanup_residue_not_reproduced")

        work = dict(_read_json(work_path))
        worker_summary = dict(_read_json(summary_path))
        existing_scope = {
            str(slot_name): sorted(
                {str(key) for key in list(keys or []) if str(key)}
            )
            for slot_name, keys in dict(
                work.get("existing_prepared_resource_scope_by_slot") or {}
            ).items()
            if str(slot_name) and list(keys or [])
        }
        delta_scope = {
            str(slot_name): sorted(
                {str(key) for key in list(keys or []) if str(key)}
            )
            for slot_name, keys in dict(
                work.get("delta_prepare_scope_by_slot") or {}
            ).items()
            if str(slot_name) and list(keys or [])
        }
        merged_scope: Dict[str, list[str]] = {}
        for slot_name in sorted(set(existing_scope) | set(delta_scope)):
            merged_scope[slot_name] = sorted(
                set(existing_scope.get(slot_name, []))
                | set(delta_scope.get(slot_name, []))
            )
        if (
            not existing_scope
            or not delta_scope
            or worker_summary.get("ready") is not True
            or worker_summary.get("host_additive_prepare_ok") is not True
        ):
            raise RuntimeError("r121_worker_owned_scope_evidence_incomplete")

        topology_path = Path(str(work.get("topology_path") or ""))
        link_state_path = Path(str(work.get("link_state_path") or ""))
        if not topology_path.is_file() or not link_state_path.is_file():
            raise RuntimeError("r121_worker_cleanup_physical_paths_missing")
        installer = DirectNetnsVethSlotLinkNamespaceQdiscInstaller()
        topology = installer._load_topology(topology_path)
        old_inventory = installer._runtime_artifact_inventory(
            topology=topology,
            topology_path=topology_path,
            prepared_resource_scope_by_slot=existing_scope,
        )
        merged_inventory = installer._runtime_artifact_inventory(
            topology=topology,
            topology_path=topology_path,
            prepared_resource_scope_by_slot=merged_scope,
        )
        future_only_host_interfaces = sorted(
            set(merged_inventory.get("host_interface_names") or [])
            - set(old_inventory.get("host_interface_names") or [])
        )
        if not future_only_host_interfaces:
            raise RuntimeError("r121_worker_future_host_scope_not_reproduced")

        installer._prepared_resource_keys_by_slot = {
            slot_name: set(keys) for slot_name, keys in existing_scope.items()
        }
        mutator_state = {"active": True}

        class ReplayProcess:
            pid = 121

            def poll(self):
                return None if mutator_state["active"] else 0

            def terminate(self):
                mutator_state["active"] = False

            def wait(self, timeout=None):
                mutator_state["active"] = False
                return 0

            def kill(self):
                mutator_state["active"] = False

        quiesce = _terminate_horizon_maintenance_process_workers_for_cleanup(
            [ReplayProcess()]
        )

        def _bounded_delete(*args: Any, **kwargs: Any) -> str:
            if mutator_state["active"]:
                raise RuntimeError("cleanup_started_before_worker_quiesce")
            return "deleted"

        installer._delete_host_interface_best_effort = _bounded_delete  # type: ignore[method-assign]
        installer._delete_bridge_best_effort = _bounded_delete  # type: ignore[method-assign]
        cleanup = installer.cleanup_runtime_artifacts(
            topology_path=topology_path,
            link_state_path=link_state_path,
            additional_prepared_resource_scope_by_slot=delta_scope,
        )
        metrics = dict(cleanup.get("cleanup_metrics") or {})
        if (
            quiesce.get("all_terminal") is not True
            or cleanup.get("cleanup_completed") is not True
            or metrics.get("bounded_owned_scope") is not True
            or metrics.get("full_topology_candidate_inventory_fallback") is True
            or str(metrics.get("cleanup_scope_source") or "")
            != "prepared_owned_resource_index_plus_frozen_horizon_worker_scope"
            or int(metrics.get("frozen_horizon_worker_resource_scope_count") or 0)
            != sum(len(keys) for keys in delta_scope.values())
        ):
            raise RuntimeError("r121_bounded_worker_cleanup_replay_failed")

        evidence = {
            "mdns_sample_count": len(samples),
            "mdns_sources": sorted({str(sample.get("src_ip")) for sample in samples}),
            "mdns_interfaces": sorted(
                {str(sample.get("interface_name")) for sample in samples}
            ),
            "previous_cleanup_success": final_cleanup.get("all_cleanup_success"),
            "previous_remaining_host_interface_count": len(
                previous_remaining_interfaces
            ),
            "existing_owned_resource_count": sum(
                len(keys) for keys in existing_scope.values()
            ),
            "worker_delta_resource_count": sum(
                len(keys) for keys in delta_scope.values()
            ),
            "merged_owned_resource_count": sum(
                len(keys) for keys in merged_scope.values()
            ),
            "future_only_host_interface_count": len(future_only_host_interfaces),
            "worker_wall_seconds": float(worker_summary.get("wall_seconds") or 0.0),
            "worker_quiesced_before_cleanup": bool(
                quiesce.get("quiesced_before_host_artifact_cleanup")
            ),
            "replay_cleanup_scope_source": str(
                metrics.get("cleanup_scope_source") or ""
            ),
            "replay_cleanup_global_scan": bool(
                metrics.get("full_topology_candidate_inventory_fallback")
            ),
            "wall_seconds": round(time.perf_counter() - started, 6),
        }
    except Exception as exc:
        blockers.append(
            {
                "reason": "r121_multicast_and_worker_cleanup_replay_failed",
                "exception_type": type(exc).__name__,
                "message": str(exc),
            }
        )
    return _check(
        name="r121_multicast_and_worker_cleanup_replay",
        catches=[
            "r121 host Avahi mDNS caused strict classifier miss/drop during H0",
            "r121 H1 worker continued physical preparation while parent cleanup ran",
            "r121 parent cleanup omitted the frozen unconsumed H1 delta owned scope",
        ],
        ok=not blockers,
        evidence=evidence,
        blockers=blockers,
    )


def _r122_compose_multicast_gap_replay_check(run_dir: Path) -> Dict[str, Any]:
    diagnostic_path = run_dir / "r122_pre_runtime_multicast_gap.json"
    compose_path = run_dir / "seed_output" / "docker-compose.yml"
    if not diagnostic_path.exists() or not compose_path.exists():
        return _check(
            name="r122_compose_multicast_gap_replay",
            catches=["r122 direct-veth-only multicast gate omitted compose IX links"],
            ok=False,
            blockers=[{"reason": "r122_multicast_gap_artifacts_missing"}],
        )
    blockers: list[Dict[str, Any]] = []
    evidence: Dict[str, Any] = {}
    try:
        diagnostic = dict(_read_json(diagnostic_path))
        observations = [
            dict(item)
            for item in list(diagnostic.get("compose_ix_observations") or [])
        ]
        if (
            diagnostic.get("decision") != "stopped_before_runtime"
            or diagnostic.get("matches_r121_samples") is not True
            or diagnostic.get("manual_runtime_mutation_performed") is not False
            or int(diagnostic.get("direct_prepared_veth_count") or 0) <= 0
            or int(
                diagnostic.get("direct_prepared_veth_multicast_enabled_count") or 0
            )
            != 0
            or len(observations) != 2
            or any(
                "MULTICAST" not in list(item.get("bridge_flags") or [])
                or "MULTICAST" not in list(item.get("interface_flags") or [])
                for item in observations
            )
        ):
            raise RuntimeError("r122_compose_multicast_gap_not_reproduced")

        original_compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
        original_networks = dict(original_compose.get("networks") or {})
        required_networks = ["net_ix_ix202", "net_ix_ix210"]
        if any(name not in original_networks for name in required_networks):
            raise RuntimeError("r122_compose_ix_networks_missing")
        if any(
            str(
                dict(dict(original_networks[name]).get("driver_opts") or {}).get(
                    "com.docker.network.bridge.name"
                )
                or ""
            )
            for name in required_networks
        ):
            raise RuntimeError("r122_old_compose_gap_not_reproduced")

        with tempfile.TemporaryDirectory() as tmpdir:
            replay_run_dir = Path(tmpdir) / "run"
            replay_output_dir = replay_run_dir / "seed_output"
            replay_output_dir.mkdir(parents=True)
            (replay_output_dir / "docker-compose.yml").write_text(
                compose_path.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            frozen = _historical_freeze_compose_ix_bridge_bindings(
                run_dir=replay_run_dir,
                output_dir=replay_output_dir,
            )
            rewritten = yaml.safe_load(
                (replay_output_dir / "docker-compose.yml").read_text(
                    encoding="utf-8"
                )
            )
        rewritten_networks = dict(rewritten.get("networks") or {})
        for name in required_networks:
            actual = str(
                dict(dict(rewritten_networks[name]).get("driver_opts") or {}).get(
                    "com.docker.network.bridge.name"
                )
                or ""
            )
            if actual != _historical_compose_ix_bridge_name(name):
                raise RuntimeError(f"r122_compose_ix_binding_not_frozen:{name}")
        evidence = {
            "direct_prepared_veth_count": int(
                diagnostic.get("direct_prepared_veth_count") or 0
            ),
            "direct_multicast_enabled_count": int(
                diagnostic.get("direct_prepared_veth_multicast_enabled_count") or 0
            ),
            "compose_multicast_gap_count": len(observations),
            "compose_gap_interface_refs": sorted(
                str(item.get("interface_ref") or "") for item in observations
            ),
            "frozen_ix_bridge_binding_count": int(
                frozen.get("network_count") or 0
            ),
            "required_rewritten_bridge_names": {
                name: _historical_compose_ix_bridge_name(name)
                for name in required_networks
            },
        }
    except Exception as exc:
        blockers.append(
            {
                "reason": "r122_compose_multicast_gap_replay_failed",
                "exception_type": type(exc).__name__,
                "message": str(exc),
            }
        )
    return _check(
        name="r122_compose_multicast_gap_replay",
        catches=[
            "r122 direct-veth-only multicast gate omitted compose IX bridges",
            "r122 compose-created gateway classifier endpoints remained multicast-capable",
        ],
        ok=not blockers,
        evidence=evidence,
        blockers=blockers,
    )


def _real_created_veth_binding_handoff_check() -> Dict[str, Any]:
    suffix = f"{os.getpid() % 100000:05d}"
    host_interface = f"svg{suffix}h"
    temp_interface = f"svg{suffix}t"
    container_interface = f"svg{suffix}c"
    installer = DirectNetnsVethSlotLinkNamespaceQdiscInstaller()
    installer._container_pid = lambda _: os.getpid()  # type: ignore[method-assign]
    blockers: list[Dict[str, Any]] = []
    binding: Dict[str, Any] = {}
    cleanup_returncode: int | None = None
    try:
        binding = dict(
            installer._create_prepared_veth(
                bridge_name="unused",
                slot_name="pre-long-run-created-veth-binding",
                endpoint={
                    "host_interface_name": host_interface,
                    "temp_interface_name": temp_interface,
                    "node_id": "pre-long-run-current-netns",
                    "interface_name": container_interface,
                    "ip_address": "192.0.2.1/30",
                },
                activate_now=False,
                assign_ip=False,
                known_host_interfaces=set(),
                known_container_interfaces=set(),
                capture_binding=True,
            )
            or {}
        )
        if (
            binding.get("binding_source") != "created_veth_operation_result"
            or int(binding.get("ifindex") or 0) <= 0
            or not str(binding.get("mac_address") or "")
        ):
            blockers.append(
                {
                    "reason": "real_created_veth_binding_invalid",
                    "binding": binding,
                }
            )
    except Exception as exc:
        blockers.append(
            {
                "reason": "real_created_veth_binding_failed",
                "exception_type": type(exc).__name__,
                "message": str(exc),
            }
        )
    finally:
        cleanup = installer._run_command(
            ["sudo", "-n", "ip", "link", "del", host_interface],
            allow_failure=True,
        )
        cleanup_returncode = int(cleanup.returncode)
        residue = installer._run_command(
            ["sudo", "-n", "ip", "link", "show", "dev", host_interface],
            allow_failure=True,
        )
        if int(residue.returncode) == 0:
            blockers.append({"reason": "real_created_veth_cleanup_residue"})
    return _check(
        name="real_created_veth_binding_handoff",
        catches=[
            "r117 netns sysfs path did not expose the newly created interface ifindex"
        ],
        ok=not blockers,
        evidence={
            "interface_ref": str(binding.get("interface_ref") or ""),
            "ifindex": int(binding.get("ifindex") or 0),
            "binding_source": str(binding.get("binding_source") or ""),
            "cleanup_returncode": cleanup_returncode,
        },
        blockers=blockers,
    )


def _real_unicast_physical_link_check() -> Dict[str, Any]:
    suffix = f"{os.getpid() % 100000:05d}"
    bridge_name = f"sub{suffix}"
    host_interface = f"suh{suffix}"
    temp_interface = f"sut{suffix}"
    container_interface = f"suc{suffix}"
    installer = DirectNetnsVethSlotLinkNamespaceQdiscInstaller()
    installer._container_pid = lambda _: os.getpid()  # type: ignore[method-assign]
    blockers: list[Dict[str, Any]] = []
    observed_flags: Dict[str, list[str]] = {}
    try:
        installer._set_bridge_ready(
            bridge_name=bridge_name,
            subnet="192.0.2.0/30",
            bridge_gateway_ip="192.0.2.1",
        )
        installer._create_prepared_veth(
            bridge_name=bridge_name,
            slot_name="pre-long-run-unicast-physical-link",
            endpoint={
                "host_interface_name": host_interface,
                "temp_interface_name": temp_interface,
                "node_id": "pre-long-run-current-netns",
                "interface_name": container_interface,
                "ip_address": "192.0.2.2/30",
            },
            activate_now=True,
            assign_ip=True,
            known_host_interfaces=set(),
            known_container_interfaces=set(),
            capture_binding=True,
        )
        for label, payload in (
            (
                "bridge",
                json.loads(
                    installer._run_command(
                        ["ip", "-j", "link", "show", "dev", bridge_name]
                    ).stdout
                ),
            ),
            (
                "host_veth",
                json.loads(
                    installer._run_command(
                        ["ip", "-j", "link", "show", "dev", host_interface]
                    ).stdout
                ),
            ),
            (
                "container_veth",
                json.loads(
                    installer._nsenter(
                        node_id="pre-long-run-current-netns",
                        command=[
                            "ip", "-j", "link", "show", "dev", container_interface
                        ],
                    ).stdout
                ),
            ),
        ):
            flags = [str(flag) for flag in list(dict(payload[0]).get("flags") or [])]
            observed_flags[label] = flags
            if "MULTICAST" in flags:
                blockers.append(
                    {
                        "reason": "real_physical_link_multicast_still_enabled",
                        "link": label,
                        "flags": flags,
                    }
                )
    except Exception as exc:
        blockers.append(
            {
                "reason": "real_unicast_physical_link_failed",
                "exception_type": type(exc).__name__,
                "message": str(exc),
            }
        )
    finally:
        installer._run_command(
            ["sudo", "-n", "ip", "link", "del", host_interface],
            allow_failure=True,
        )
        installer._run_command(
            ["sudo", "-n", "ip", "link", "del", bridge_name],
            allow_failure=True,
        )
        for interface_name in (host_interface, bridge_name):
            residue = installer._run_command(
                ["ip", "link", "show", "dev", interface_name],
                allow_failure=True,
            )
            if int(residue.returncode) == 0:
                blockers.append(
                    {
                        "reason": "real_unicast_physical_link_cleanup_residue",
                        "interface_name": interface_name,
                    }
                )
    return _check(
        name="real_unicast_physical_link",
        catches=[
            "r121 host Avahi mDNS entered strict gateway classifier through multicast-capable bridges"
        ],
        ok=not blockers,
        evidence={
            "observed_flags": observed_flags,
            "unicast_only": bool(observed_flags)
            and all("MULTICAST" not in flags for flags in observed_flags.values()),
        },
        blockers=blockers,
    )


def _real_compose_ix_unicast_prepare_check() -> Dict[str, Any]:
    suffix = f"{os.getpid() % 100000:05d}"
    network_name = f"skyseed-gate-ix-{suffix}"
    frozen_network_name = f"net_ix_gate_{suffix}"
    bridge_name = _historical_compose_ix_bridge_name(frozen_network_name)
    host_interface = f"sih{suffix}"
    temp_interface = f"sit{suffix}"
    container_interface = f"sic{suffix}"
    installer = DirectNetnsVethSlotLinkNamespaceQdiscInstaller()
    installer._container_pid_cache = {"gate": os.getpid()}
    installer._container_pid_cache_complete = True
    blockers: list[Dict[str, Any]] = []
    before_flags: Dict[str, list[str]] = {}
    after_flags: Dict[str, list[str]] = {}
    capture: Dict[str, Any] = {}
    try:
        created = installer._run_command(
            [
                "docker", "network", "create", "--driver", "bridge",
                "--opt", f"com.docker.network.bridge.name={bridge_name}",
                "--subnet", "198.18.250.0/29", network_name,
            ]
        )
        if int(created.returncode) != 0:
            raise RuntimeError("real_compose_ix_network_create_failed")
        installer._run_command(
            [
                "sudo", "-n", "ip", "link", "add", host_interface,
                "type", "veth", "peer", "name", temp_interface,
            ]
        )
        installer._run_command(
            [
                "sudo", "-n", "ip", "link", "set", temp_interface,
                "netns", str(os.getpid()),
            ]
        )
        installer._nsenter(
            node_id="gate",
            command=["ip", "link", "set", temp_interface, "name", container_interface],
        )
        installer._run_command(
            [
                "sudo", "-n", "ip", "link", "set", host_interface,
                "master", bridge_name,
            ]
        )
        installer._run_command(
            ["sudo", "-n", "ip", "link", "set", host_interface, "up"]
        )
        installer._nsenter(
            node_id="gate",
            command=["ip", "link", "set", container_interface, "up"],
        )

        before_bridge = json.loads(
            installer._run_command(
                ["ip", "-j", "link", "show", "dev", bridge_name]
            ).stdout
        )[0]
        before_endpoint = json.loads(
            installer._nsenter(
                node_id="gate",
                command=["ip", "-j", "link", "show", "dev", container_interface],
            ).stdout
        )[0]
        before_flags = {
            "compose_bridge": list(before_bridge.get("flags") or []),
            "compose_endpoint": list(before_endpoint.get("flags") or []),
        }
        if any("MULTICAST" not in flags for flags in before_flags.values()):
            raise RuntimeError("real_compose_ix_multicast_precondition_missing")

        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            (run_dir / "frozen_compose_ix_bridge_bindings.json").write_text(
                json.dumps(
                    {
                        "schema_version": "skyseed-frozen-compose-ix-bridge-bindings-v1",
                        "ok": True,
                        "bindings": [
                            {
                                "network_name": frozen_network_name,
                                "bridge_name": bridge_name,
                                "binding_source": "frozen_compose_driver_option",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            host_prepare = _historical_prepare_frozen_compose_ix_bridges_unicast(
                network_executor=installer,
                run_dir=run_dir,
            )
        capture = installer.capture_existing_container_interface_bindings(
            interface_refs=[f"netns:gate:{container_interface}"]
        )
        after_bridge = json.loads(
            installer._run_command(
                ["ip", "-j", "link", "show", "dev", bridge_name]
            ).stdout
        )[0]
        after_endpoint = json.loads(
            installer._nsenter(
                node_id="gate",
                command=["ip", "-j", "link", "show", "dev", container_interface],
            ).stdout
        )[0]
        after_flags = {
            "compose_bridge": list(after_bridge.get("flags") or []),
            "compose_endpoint": list(after_endpoint.get("flags") or []),
        }
        if (
            host_prepare.get("prepared_interface_count") != 1
            or capture.get("unicast_physical_prepare") is not True
            or any("MULTICAST" in flags for flags in after_flags.values())
        ):
            raise RuntimeError("real_compose_ix_unicast_prepare_incomplete")
    except Exception as exc:
        blockers.append(
            {
                "reason": "real_compose_ix_unicast_prepare_failed",
                "exception_type": type(exc).__name__,
                "message": str(exc),
            }
        )
    finally:
        installer._run_command(
            ["sudo", "-n", "ip", "link", "del", host_interface],
            allow_failure=True,
        )
        installer._run_command(
            ["docker", "network", "rm", network_name],
            allow_failure=True,
        )
        for interface_name in (host_interface, bridge_name):
            residue = installer._run_command(
                ["ip", "link", "show", "dev", interface_name],
                allow_failure=True,
            )
            if int(residue.returncode) == 0:
                blockers.append(
                    {
                        "reason": "real_compose_ix_unicast_cleanup_residue",
                        "interface_name": interface_name,
                    }
                )
    return _check(
        name="real_compose_ix_unicast_prepare",
        catches=[
            "r122 prewarm check found compose-created IX bridge and endpoint still multicast-capable"
        ],
        ok=not blockers,
        evidence={
            "bridge_name": bridge_name,
            "before_flags": before_flags,
            "after_flags": after_flags,
            "capture_unicast_physical_prepare": bool(
                capture.get("unicast_physical_prepare")
            ),
        },
        blockers=blockers,
    )


def _initial_frozen_binding_producer_check(run_dir: Path) -> Dict[str, Any]:
    normal_path = run_dir / "normal_runtime_inputs.json"
    coverage_path = run_dir / "precompose_run_window_resource_coverage_plan.json"
    if not normal_path.exists() or not coverage_path.exists():
        return _check(
            name="initial_frozen_binding_producer",
            catches=["r118 H0 compose-created interface had no frozen producer"],
            ok=False,
            blockers=[{"reason": "r118_initial_binding_artifacts_missing"}],
        )
    blockers: list[Dict[str, Any]] = []
    captured_refs: list[str] = []
    started = time.perf_counter()
    try:
        normal = dict(_read_json(normal_path))
        coverage = dict(_read_json(coverage_path))
        topology_path = Path(str(normal.get("topology_path") or ""))
        h0_contexts = dict(
            dict(normal.get("context_by_slice_by_segment") or {}).get("H0") or {}
        )
        if not h0_contexts:
            raise RuntimeError("r118_h0_contexts_missing")
        context_payload = dict(h0_contexts[sorted(h0_contexts)[0]] or {})
        current_context = _current_shared_gz_historical_context(context_payload)
        snapshot = _topology_snapshot_from_payload(
            dict(current_context.get("snapshot") or {})
        )
        plan = _runtime_update_plan_from_payload(
            dict(current_context.get("runtime_update_plan") or {})
        )
        contexts = _realization_contexts(
            topology_path=topology_path,
            topology_snapshot=snapshot,
            runtime_update_plan=plan,
        )
        creation_bindings = _simulated_additive_creation_bindings(
            source={
                "existing_prepared_resource_scope_by_slot": dict(
                    coverage.get("prepared_resource_scope_by_slot") or {}
                ),
                "delta_prepare_scope_by_slot": {},
            },
            topology_path=topology_path,
            interface_ifindex_cache={},
            interface_mac_cache={},
        )
        prewarm = {
            "preparation_summary": {
                "prepare_summary": {
                    "frozen_interface_binding_cache": {
                        "ifindex_by_interface_ref": {
                            item["interface_ref"]: item["ifindex"]
                            for item in creation_bindings
                        },
                        "mac_by_interface_ref": {
                            item["interface_ref"]: item["mac_address"]
                            for item in creation_bindings
                        },
                        "complete": True,
                        "live_discovery_allowed": False,
                    }
                }
            }
        }

        class CaptureExecutor:
            def seal_container_pid_cache(
                self, *, expected_node_ids: Sequence[str]
            ) -> Dict[str, Any]:
                expected = sorted({str(item) for item in expected_node_ids if str(item)})
                if not expected:
                    raise RuntimeError("initial_binding_replay_pid_scope_empty")
                return {
                    "schema_version": "skyseed-frozen-container-pid-cache-seal-v1",
                    "ok": True,
                    "complete": True,
                    "expected_node_count": len(expected),
                    "frozen_container_pid_cache_count": len(expected),
                    "live_discovery_allowed": False,
                }

            def capture_existing_container_interface_bindings(
                self, *, interface_refs: Sequence[str]
            ) -> Dict[str, Any]:
                captured_refs.extend(str(ref) for ref in interface_refs)
                return {
                    "ok": True,
                    "full_scan": False,
                    "unicast_physical_prepare": True,
                    "multicast_disable_count": len(interface_refs),
                    "bindings": [
                        {
                            "interface_ref": str(ref),
                            "ifindex": 2_000_000 + index,
                            "mac_address": _stable_local_mac(str(ref)),
                            "binding_source": (
                                "targeted_existing_interface_prepare_operation_result"
                            ),
                        }
                        for index, ref in enumerate(interface_refs)
                    ],
                }

        generation_id = int(
            dict(plan.target_snapshot or {}).get("bpf_generation_id") or 0
        )
        ifindex_cache, mac_cache, capture = (
            _complete_initial_frozen_interface_binding_caches(
                network_executor=CaptureExecutor(),
                contexts=contexts,
                generation_id=generation_id,
                prewarm=prewarm,
            )
        )
        with patch(
            "satellite.bpf_mpls_live.interface_ifindex",
            side_effect=AssertionError("live ifindex discovery reached"),
        ), patch(
            "satellite.bpf_mpls_live.interface_mac",
            side_effect=AssertionError("live MAC discovery reached"),
        ):
            compiled = _bpf_owned_compile_mpls_maps_for_contexts(
                contexts=contexts,
                generation_id=generation_id,
                interface_ifindex_cache=ifindex_cache,
                interface_mac_cache=mac_cache,
                require_complete_interface_binding_cache=True,
            )
        if not dict(compiled.get("compiled_mpls_map_counts") or {}):
            raise RuntimeError("r118_h0_strict_compile_counts_missing")
        if int(capture.get("targeted_final_capture_ref_count") or 0) != len(
            captured_refs
        ):
            raise RuntimeError("r118_targeted_capture_count_mismatch")
    except Exception as exc:
        blockers.append(
            {
                "reason": "initial_frozen_binding_producer_failed",
                "exception_type": type(exc).__name__,
                "message": str(exc),
            }
        )
    return _check(
        name="initial_frozen_binding_producer",
        catches=["r118 H0 compose-created interface had no frozen producer"],
        ok=not blockers,
        evidence={
            "captured_ref_count": len(captured_refs),
            "captured_ref_examples": captured_refs[:10],
            "wall_seconds": round(time.perf_counter() - started, 6),
        },
        blockers=blockers,
    )


def _iter_events(path: Path) -> Iterable[Dict[str, Any]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                value = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                yield value


def _previous_failed_cleanup_evidence_check(run_dir: Path) -> Dict[str, Any]:
    events_path = run_dir / "events.jsonl"
    if not events_path.exists():
        return _check(
            name="previous_failed_cleanup_evidence",
            catches=["r113 H0 generation cleanup queued while H0 still active"],
            ok=False,
            blockers=[{"reason": "events_jsonl_missing"}],
        )
    active_horizon = ""
    violations: list[dict[str, Any]] = []
    cleanup_event_count = 0
    for event in _iter_events(events_path):
        event_type = str(event.get("event_type") or "")
        if event_type == "runtime_epoch_result":
            segment_key = str(event.get("segment_epoch_key") or "")
            if ":" in segment_key:
                active_horizon = segment_key.split(":", 1)[0]
        if event_type in {
            "bpf_retired_generation_cleanup_blocked",
            "bpf_retired_generation_cleanup_queued",
        }:
            cleanup_event_count += 1
            event_horizon = str(event.get("horizon_id") or "")
            if active_horizon and event_horizon == active_horizon:
                violations.append(
                    {
                        "event_type": event_type,
                        "timestamp_utc": str(event.get("timestamp_utc") or ""),
                        "active_horizon": active_horizon,
                        "cleanup_horizon": event_horizon,
                        "generation_id": int(event.get("generation_id") or 0),
                    }
                )
    return _check(
        name="previous_failed_cleanup_evidence",
        catches=["r113 H0 generation cleanup queued while H0 still active"],
        ok=bool(violations),
        evidence={
            "cleanup_event_count": cleanup_event_count,
            "active_horizon_cleanup_violation_count": len(violations),
            "previous_failed_artifact_rejected": bool(violations),
        },
        blockers=(
            []
            if violations
            else [{"reason": "previous_cleanup_failure_not_reproduced"}]
        ),
    )


def _previous_failed_worker_input_evidence_check(run_dir: Path) -> Dict[str, Any]:
    work_paths = sorted(
        run_dir.glob(
            "replenished_segments/segment_*/horizon_maintenance_process_work.json"
        )
    )
    events_path = run_dir / "events.jsonl"
    failure_reasons = [
        str(event.get("failure_reason") or "")
        for event in _iter_events(events_path)
        if str(event.get("event_type") or "") == "runtime_exception"
    ]
    missing_cache_paths = []
    incomplete_binding_paths = []
    complete_cache_wrong_source_paths = []
    for path in work_paths:
        payload = _read_json(path)
        if not dict(payload.get("frozen_container_pid_cache") or {}):
            missing_cache_paths.append(str(path))
        details = list(
            dict(payload.get("bpf_owned_readiness") or {}).get(
                "attached_interface_details"
            )
            or []
        )
        if not details or any(
            dict(detail or {}).get("ifindex") in (None, "", "None")
            or not str(dict(detail or {}).get("mac_address") or "")
            for detail in details
        ):
            incomplete_binding_paths.append(str(path))
        readiness = dict(payload.get("bpf_owned_readiness") or {})
        binding = dict(
            dict(dict(payload.get("base_generation_record") or {}).get(
                "compiled_runtime_inputs"
            ) or {}).get("frozen_interface_binding_cache")
            or dict(readiness.get("compiled_runtime_inputs") or {}).get(
                "frozen_interface_binding_cache"
            )
            or {}
        )
        process_summary_path = path.parent / "horizon_maintenance_process_summary.json"
        process_summary = (
            dict(_read_json(process_summary_path))
            if process_summary_path.exists()
            else {}
        )
        worker_reason = str(process_summary.get("reason") or "")
        failed_refs = (
            [worker_reason.split("frozen_interface_mac_binding_missing:", 1)[1]]
            if worker_reason.startswith("frozen_interface_mac_binding_missing:")
            else []
        )
        if (
            binding.get("complete") is True
            and binding.get("live_discovery_allowed") is False
            and any(
                ref in dict(binding.get("mac_by_interface_ref") or {})
                for ref in failed_refs
            )
        ):
            complete_cache_wrong_source_paths.append(str(path))
    boundary_ready_missing = any(
        "horizon_maintenance_ready_missing_before_boundary" in reason
        for reason in failure_reasons
    )
    old_failure_reproduced = bool(
        (missing_cache_paths or incomplete_binding_paths) and boundary_ready_missing
    )
    wrong_source_failure_reproduced = bool(complete_cache_wrong_source_paths)
    ok = bool(work_paths and (old_failure_reproduced or wrong_source_failure_reproduced))
    return _check(
        name="previous_failed_worker_input_evidence",
        catches=[
            "r114 process worker discarded parent container PID/netns cache",
            "r115 process worker repeated live ifindex/MAC discovery per generation",
            "r116 worker ignored the complete compiler binding cache and used attach-only details",
            "H1 maintenance missed the runtime boundary",
        ],
        ok=ok,
        evidence={
            "work_artifact_count": len(work_paths),
            "missing_frozen_container_pid_cache_count": len(missing_cache_paths),
            "incomplete_frozen_interface_binding_count": len(
                incomplete_binding_paths
            ),
            "complete_cache_wrong_source_count": len(
                complete_cache_wrong_source_paths
            ),
            "boundary_ready_missing": boundary_ready_missing,
            "previous_failed_artifact_rejected": ok,
        },
        blockers=(
            []
            if ok
            else [{"reason": "previous_worker_input_failure_not_reproduced"}]
        ),
    )


def _cleanup_lifecycle_semantic_check(
    run_dir: Path,
    *,
    expected_entries_per_horizon: int,
) -> Dict[str, Any]:
    from apps.run_starlink_shell_experiment import (  # noqa: WPS433
        _bpf_triple_buffer_physical_generation_id,
        _maintenance_horizon_ids,
        _retired_bpf_generation_cleanup_candidate_horizon_status,
    )
    from horizon_maintenance import (  # noqa: WPS433
        forbidden_work_audit,
        retired_cleanup_dependency_report,
    )

    current = _retired_bpf_generation_cleanup_candidate_horizon_status(
        candidate={"generation_id": 1, "horizon_id": "H0"},
        readiness={"current_horizon_id": "H0", "active_generation": 2},
    )
    retired = _retired_bpf_generation_cleanup_candidate_horizon_status(
        candidate={"generation_id": 24, "horizon_id": "H0"},
        readiness={"current_horizon_id": "H1", "active_generation": 32},
    )
    blockers: list[dict[str, Any]] = []
    if current.get("retired") is not False:
        blockers.append({"reason": "current_horizon_candidate_marked_retired"})
    if retired.get("retired") is not True:
        blockers.append({"reason": "previous_horizon_candidate_not_retired"})
    coverage_path = run_dir / "runtime_queue_route_identity_coverage.json"
    coverage = dict(_read_json(coverage_path)) if coverage_path.is_file() else {}
    slot_coverage = dict(coverage.get("bpf_generation_slot_coverage") or {})
    slot_records = [
        dict(item)
        for item in list(slot_coverage.get("records") or [])
        if isinstance(item, Mapping)
    ]
    expected_actual_horizons = ("H0", "H1", "H2", "H3")
    expected_total_generation_records = (
        len(expected_actual_horizons) * expected_entries_per_horizon
    )
    if slot_coverage.get("ok") is not True or list(
        slot_coverage.get("blockers") or []
    ):
        blockers.append({"reason": "lifecycle_generation_slot_coverage_not_ok"})
    if int(slot_coverage.get("generation_slot_record_count") or 0) != (
        expected_total_generation_records
    ):
        blockers.append(
            {
                "reason": "lifecycle_generation_slot_total_count_mismatch",
                "expected": expected_total_generation_records,
                "actual": int(
                    slot_coverage.get("generation_slot_record_count") or 0
                ),
            }
        )
    if int(slot_coverage.get("horizon_count") or 0) != len(
        expected_actual_horizons
    ):
        blockers.append({"reason": "lifecycle_generation_slot_horizon_count_mismatch"})
    if list(slot_coverage.get("generation_slot_widths") or []) != [32]:
        blockers.append({"reason": "lifecycle_generation_slot_width_mismatch"})
    actual_generation_sets: Dict[str, set[int]] = {}
    for horizon_id in expected_actual_horizons:
        horizon_records = [
            item for item in slot_records if str(item.get("horizon_id") or "") == horizon_id
        ]
        # The coverage artifact owns compact counts and keeps only a bounded
        # detail sample. Validate any retained per-H sample completely, while
        # using the authoritative aggregate above for all four horizons.
        if horizon_records and len(horizon_records) != expected_entries_per_horizon:
            blockers.append(
                {
                    "reason": "lifecycle_generation_record_count_mismatch",
                    "horizon_id": horizon_id,
                    "expected": expected_entries_per_horizon,
                    "actual": len(horizon_records),
                }
            )
        if any(list(item.get("mismatches") or []) for item in horizon_records):
            blockers.append(
                {
                    "reason": "lifecycle_generation_slot_assignment_mismatch",
                    "horizon_id": horizon_id,
                }
            )
        actual_generation_sets[horizon_id] = {
            int(item.get("physical_generation_id") or 0) for item in horizon_records
        }

    records: list[dict[str, Any]] = []
    observed_lifecycle = []
    for index, horizon_id in enumerate(expected_actual_horizons):
        records.append({"horizon_id": horizon_id, "segment_index": index})
        observed_lifecycle.append(
            _maintenance_horizon_ids(
                current_horizon_id=horizon_id,
                segment_records=records,
            )
        )
    expected_lifecycle = [("H0", ""), ("H1", "H0"), ("H2", "H1"), ("H3", "H2")]
    if observed_lifecycle != expected_lifecycle:
        blockers.append({"reason": "four_horizon_cleanup_lifecycle_mismatch"})
    worker_lifecycle = []
    for target_index in range(1, 6):
        current_horizon_id = f"H{target_index - 1}"
        previous_horizon_id = "" if target_index == 1 else f"H{target_index - 2}"
        target_horizon_id = f"H{target_index}"
        cleanup_report = retired_cleanup_dependency_report(
            previous_horizon_id=previous_horizon_id,
            completed_count=(expected_entries_per_horizon if previous_horizon_id else 0),
            status=("completed" if previous_horizon_id else "not_required"),
        )
        current_generations = {
            _bpf_triple_buffer_physical_generation_id(
                horizon_sequence_index=target_index - 1,
                local_generation_id=local_generation_id,
                generation_slot_width=32,
            )
            for local_generation_id in range(expected_entries_per_horizon)
        }
        target_generations = {
            _bpf_triple_buffer_physical_generation_id(
                horizon_sequence_index=target_index,
                local_generation_id=local_generation_id,
                generation_slot_width=32,
            )
            for local_generation_id in range(expected_entries_per_horizon)
        }
        cleanup_generations = (
            {
                _bpf_triple_buffer_physical_generation_id(
                    horizon_sequence_index=target_index - 2,
                    local_generation_id=local_generation_id,
                    generation_slot_width=32,
                )
                for local_generation_id in range(expected_entries_per_horizon)
            }
            if previous_horizon_id
            else set()
        )
        if cleanup_generations & current_generations:
            blockers.append(
                {
                    "reason": "cleanup_intersects_current_generation",
                    "target_horizon_id": target_horizon_id,
                }
            )
        if cleanup_generations & target_generations:
            blockers.append(
                {
                    "reason": "cleanup_intersects_future_target_generation",
                    "target_horizon_id": target_horizon_id,
                }
            )
        if cleanup_report.get("active_generation_delete_allowed") is not False:
            blockers.append({"reason": "active_generation_cleanup_allowed"})
        if cleanup_report.get("target_generation_cleanup_scan_count") != 0:
            blockers.append({"reason": "target_generation_cleanup_scan_present"})
        if cleanup_report.get("generation_gc_scan_count") != 0:
            blockers.append({"reason": "generation_gc_scan_present"})
        worker_lifecycle.append(
            {
                "target_horizon_id": target_horizon_id,
                "current_horizon_id": current_horizon_id,
                "cleanup_previous_horizon_id": previous_horizon_id,
                "cleanup_status": str(cleanup_report.get("status") or ""),
                "cleanup_generation_count": len(cleanup_generations),
                "current_generation_count": len(current_generations),
                "target_generation_count": len(target_generations),
                "cleanup_disjoint_from_current_and_target": not bool(
                    cleanup_generations & (current_generations | target_generations)
                ),
                "active_generation_flip_allowed": False,
                "future_host_link_activation_allowed": False,
                "future_qdisc_preapply_allowed": False,
            }
        )
    if worker_lifecycle[0]["cleanup_status"] != "not_required":
        blockers.append({"reason": "h0_bootstrap_cleanup_not_not_required"})
    if [item["cleanup_previous_horizon_id"] for item in worker_lifecycle] != [
        "",
        "H0",
        "H1",
        "H2",
        "H3",
    ]:
        blockers.append({"reason": "rolling_cleanup_previous_horizon_sequence_mismatch"})

    maintenance_forbidden_audit = forbidden_work_audit(
        {
            "maintenance_stage": "offline_h0_h5_lifecycle_replay",
            "active_generation_flip_allowed": False,
            "future_host_link_activation_allowed": False,
            "future_map_write_allowed": False,
            "runtime_full_generation_reload_count": 0,
            "full_attach_count": 0,
            "live_full_map_dump_count": 0,
            "live_full_map_diff_count": 0,
            "target_generation_cleanup_scan_count": 0,
            "generation_gc_scan_count": 0,
        }
    )
    if maintenance_forbidden_audit.get("passed") is not True:
        blockers.append({"reason": "maintenance_forbidden_work_audit_failed"})
    physical_slots = [
        _bpf_triple_buffer_physical_generation_id(
            horizon_sequence_index=index,
            local_generation_id=0,
            generation_slot_width=32,
        )
        for index in range(5)
    ]
    if physical_slots != [0, 32, 64, 0, 32]:
        blockers.append({"reason": "triple_buffer_horizon_reuse_mismatch"})
    return _check(
        name="cleanup_lifecycle_semantics",
        catches=["r113 cleanup was keyed to generation flip instead of H retirement"],
        ok=not blockers,
        evidence={
            "coverage_path": str(coverage_path),
            "authoritative_generation_slot_record_count": int(
                slot_coverage.get("generation_slot_record_count") or 0
            ),
            "authoritative_generation_slot_horizon_count": int(
                slot_coverage.get("horizon_count") or 0
            ),
            "compact_generation_detail_record_count": len(slot_records),
            "actual_generation_slot_record_count": len(slot_records),
            "retained_generation_detail_sets_h0_to_h3": {
                key: sorted(value) for key, value in actual_generation_sets.items()
            },
            "current_horizon_case": current,
            "retired_horizon_case": retired,
            "four_horizon_lifecycle": observed_lifecycle,
            "worker_lifecycle_h1_to_h5": worker_lifecycle,
            "triple_buffer_physical_slots_h0_to_h4": physical_slots,
            "maintenance_forbidden_work_audit": maintenance_forbidden_audit,
        },
        blockers=blockers,
    )


def _frozen_direct_delta_attach_check() -> Dict[str, Any]:
    from linux_mpls_runtime import (  # noqa: WPS433
        _prepare_bpf_owned_preactivation_attach_capability_from_frozen_inputs,
    )

    interface_refs = [f"netns:contract-sat-{index}:if0" for index in range(4)]
    details = [
        {
            "interface_ref": interface_ref,
            "interface_name": interface_ref,
            "ifindex": 101 + index,
            "mac_address": f"02:00:00:00:00:{index + 1:02x}",
            "attach_directions": ["ingress", "egress"],
            "expected_roles": ["g_to_z_classifier"],
            "control_plane_ipv4_addrs": [f"10.0.0.{index + 1}"],
        }
        for index, interface_ref in enumerate(interface_refs)
    ]
    frozen_inputs = {
        "schema_version": "skyseed-bpf-owned-compiled-runtime-inputs-v2",
        "active_generation": {"value": 32},
        "ut_gateway_policy_map": [],
        "ingress_classifier_map": [],
        "gateway_selector_ingress_map": [],
        "core_lfib_map": [],
        "egress_handoff_map": [],
        "zone_edge_delivery_map": [],
        "generation_base_map": [],
        "map_conflicts": [],
        "compiled_mpls_map_counts": {},
        "attach_plan": {
            interface_ref: ["ingress", "egress"]
            for interface_ref in interface_refs
        },
        "attached_interfaces": interface_refs,
        "attached_interface_details": details,
        "resident_attach_coverage_budget": {
            "budget_source": "contract-check",
            "max_attach_interfaces": 8,
        },
    }
    readiness = {
        "attach_plan": {interface_ref: ["ingress"] for interface_ref in interface_refs},
        "attached_interfaces": interface_refs,
        "attached_interface_details": [
            {**detail, "attach_directions": ["ingress"]}
            for detail in details
        ],
        "map_capacities": {
            "classifiers": 128,
            "lfib": 256,
            "handoffs": 128,
        },
    }
    attach_calls: list[dict[str, Any]] = []

    def attach(interface_ref_arg: str, object_path: Path, **kwargs: Any):
        attach_calls.append(
            {
                "interface_ref": interface_ref_arg,
                "object_path": str(object_path),
                **kwargs,
            }
        )
        return [{"returncode": 0, "stdout": "", "stderr": ""}]

    blockers: list[dict[str, Any]] = []
    report: Dict[str, Any] = {}
    try:
        with patch(
            "satellite.bpf_mpls_live.compile_mpls_bpf",
            return_value={"returncode": 0, "stdout": "", "stderr": ""},
        ), patch(
            "satellite.bpf_mpls_live.attach_mpls",
            side_effect=attach,
        ), patch(
            "satellite.bpf_mpls_live.verify_mpls_clsact",
            side_effect=AssertionError("live clsact probe reached"),
        ), patch(
            "satellite.bpf_mpls_live.interface_ipv4_addresses",
            side_effect=AssertionError("live address discovery reached"),
        ), patch(
            "satellite.bpf_mpls_live.read_mpls_tc_filter_stats",
            side_effect=AssertionError("live filter stats read reached"),
        ):
            report = (
                _prepare_bpf_owned_preactivation_attach_capability_from_frozen_inputs(
                    topology_path=Path("/nonexistent/frozen-contract-topology.json"),
                    frozen_compiled_runtime_inputs=frozen_inputs,
                    bpf_owned_readiness=readiness,
                    max_attach_workers=2,
                    retain_inactive_generations=[0, 32],
                    prepared_generation_window_count=3,
                )
            )
    except Exception as exc:
        blockers.append(
            {"reason": "frozen_direct_delta_attach_execution_failed", "error": str(exc)}
        )
    if report and report.get("ok") is not True:
        blockers.append(
            {
                "reason": "frozen_direct_delta_attach_not_ready",
                "detail": str(report.get("reason") or ""),
            }
        )
    if len(attach_calls) != len(interface_refs):
        blockers.append(
            {
                "reason": "frozen_direct_delta_attach_call_count_mismatch",
                "actual": len(attach_calls),
            }
        )
    elif any(
        call.get("clsact_setup_mode") != "direct_existing_delta"
        for call in attach_calls
    ):
        blockers.append(
            {
                "reason": "frozen_direct_delta_attach_used_probe_mode",
                "actual": [
                    call.get("clsact_setup_mode") for call in attach_calls
                ],
            }
        )
    execution = dict(
        dict(report.get("prepare_steps") or {}).get(
            "preactivation_attach_prepare"
        )
        or {}
    ).get("delta_attach_execution") or {}
    if (
        int(dict(execution).get("requested_worker_count") or 0) != 2
        or int(dict(execution).get("worker_count") or 0) != 2
        or dict(execution).get("bounded_parallel") is not True
    ):
        blockers.append(
            {
                "reason": "frozen_direct_delta_attach_not_bounded_parallel",
                "execution": dict(execution),
            }
        )
    return _check(
        name="frozen_direct_delta_attach",
        catches=[
            "r115 repeated live ifindex/MAC and clsact discovery missed H1 boundary",
            "maintenance attach accidentally became validation-only and skipped real delta attach",
        ],
        ok=not blockers,
        evidence={
            "delta_attach_count": int(report.get("delta_attach_count") or 0),
            "attach_call_count": len(attach_calls),
            "delta_attach_execution": dict(execution),
            "clsact_setup_modes": [
                str(item.get("clsact_setup_mode") or "") for item in attach_calls
            ],
            "physical_check_source": str(
                report.get("attach_capability_prepare_physical_check_source") or ""
            ),
        },
        blockers=blockers,
    )


def _r125_initial_attach_h0_preload_handoff_check(run_dir: Path) -> Dict[str, Any]:
    first_failure = dict(_read_json(run_dir / "first_failure_summary.json"))
    runtime_blocker = dict(first_failure.get("runtime_blocker") or {})
    post_run = dict(_read_json(run_dir / "post_run_preflight.json"))
    pre_runtime = dict(_read_json(run_dir / "pre_runtime_bpf_owned_prepare.json"))
    expected_error = (
        "pre_runtime_bpf_generation_preload_failed:"
        "bpf_lookahead_preload_prepared_attach_capability_missing:"
        "prepared_attach_capability_report_missing"
    )
    detail = {
        "interface_ref": "netns:r125-replay:if0",
        "attach_directions": ["ingress"],
        "expected_roles": ["g_to_z_classifier"],
        "ifindex": 125,
        "mac_address": "02:00:00:00:00:7d",
    }
    initial_readiness = {
        "ready": True,
        "ok": True,
        "attach_plan": {detail["interface_ref"]: ["ingress"]},
        "attached_interfaces": [detail["interface_ref"]],
        "attached_interface_details": [detail],
        "prepare_steps": {
            "real_backend_prepare": {
                "ok": True,
                "attach_plan": {detail["interface_ref"]: ["ingress"]},
                "attached_interfaces": [detail["interface_ref"]],
                "attached_interface_details": [detail],
                "compiled_mpls_map_counts": {"ingress_classifier_map": 1},
                "map_capacities": {"classifiers": 128},
            }
        },
    }
    proof = _prepared_attach_capability_readiness_for_preload(initial_readiness)
    contract = _bpf_owned_prepared_attach_capability_contract(proof)
    runtime_source = inspect.getsource(_run_live_temporal_runtime)
    blockers: list[dict[str, Any]] = []
    if str(runtime_blocker.get("error") or "") != expected_error:
        blockers.append({"reason": "r125_first_failure_evidence_missing"})
    if int(first_failure.get("runtime_epochs_completed") or 0) != 0:
        blockers.append({"reason": "r125_failed_after_runtime_epoch_started"})
    if post_run.get("clean_after_run") is not True:
        blockers.append({"reason": "r125_post_failure_cleanup_not_clean"})
    if int(pre_runtime.get("pre_runtime_full_attach_count") or 0) <= 0:
        blockers.append({"reason": "r125_initial_full_attach_evidence_missing"})
    if contract.get("ok") is not True:
        blockers.append(
            {
                "reason": "r125_initial_attach_projection_contract_failed",
                "detail": str(contract.get("reason") or ""),
            }
        )
    if (
        "pre_runtime_preload_attach_readiness" not in runtime_source
        or "_prepared_attach_capability_readiness_for_preload" not in runtime_source
        or "bpf_owned_readiness=dict(\n                            pre_runtime_preload_attach_readiness"
        not in runtime_source
    ):
        blockers.append({"reason": "r125_h0_preload_handoff_not_connected"})
    return _check(
        name="r125_initial_attach_h0_preload_handoff",
        catches=[
            "r125 initial full attach readiness omitted the attach-only capability contract consumed by H0 inactive preload",
        ],
        ok=not blockers,
        evidence={
            "first_failure_error": str(runtime_blocker.get("error") or ""),
            "runtime_epochs_completed": int(
                first_failure.get("runtime_epochs_completed") or 0
            ),
            "post_failure_clean": bool(post_run.get("clean_after_run")),
            "initial_full_attach_count": int(
                pre_runtime.get("pre_runtime_full_attach_count") or 0
            ),
            "projection_source": str(
                dict(proof.get("preactivation_attach_prepare") or {}).get(
                    "source"
                )
                or ""
            ),
            "projection_contract": contract,
            "h0_preload_consumes_projected_readiness": not any(
                blocker.get("reason") == "r125_h0_preload_handoff_not_connected"
                for blocker in blockers
            ),
        },
        blockers=blockers,
    )


def _r127_typed_context_worker_handoff_check(run_dir: Path) -> Dict[str, Any]:
    """Current generic handoff invariant; the historical run is provenance only."""
    del run_dir
    blockers: list[Dict[str, Any]] = []
    try:
        work_item = _rolling_horizon_process_preload_work_item(
            generation_item={
                "segment_epoch_key": "H1:compact-contract-snapshot",
                "generation_id": 32,
                "logical_generation_id": 0,
                "physical_generation_id": 32,
                "intent_hash": "compact-contract-intent",
            },
            entry={
                "epoch_id": "compact-contract-epoch",
                "slice_id": "compact-contract-snapshot",
                "segment_id": "H1",
            },
            state_contract={},
            bpf_generation_slot_width=25,
            retain_generation_ids=[0, 32],
            base_generation_record={"generation_id": 0},
        )
    except Exception as exc:  # noqa: BLE001
        work_item = {}
        blockers.append(
            {
                "reason": "r127_typed_context_replay_failed",
                "message": str(exc),
            }
        )
    context_ref = dict(work_item.get("context_ref") or {})
    if "context" in work_item:
        blockers.append({"reason": "worker_handoff_embeds_full_context"})
    if (
        context_ref.get("schema_version")
        != "skyseed-horizon-maintenance-context-ref-v1"
        or context_ref.get("segment_epoch_key")
        != "H1:compact-contract-snapshot"
    ):
        blockers.append({"reason": "worker_handoff_context_ref_invalid"})
    return _check(
        name="compact_context_worker_handoff",
        catches=[
            "typed runtime objects or full per-epoch contexts leaking into the process-worker request",
            "large parent-side work-item serialization returning after a runtime refactor",
        ],
        ok=not blockers,
        evidence={
            "full_context_embedded": "context" in work_item,
            "context_ref": context_ref,
            "work_item_bytes": len(
                json.dumps(work_item, sort_keys=True, separators=(",", ":")).encode(
                    "utf-8"
                )
            ),
        },
        blockers=blockers,
    )


def _r128_edge_route_worker_handoff_check(run_dir: Path) -> Dict[str, Any]:
    first_failure = dict(_read_json(run_dir / "first_failure_summary.json"))
    post_run = dict(_read_json(run_dir / "post_run_preflight.json"))
    ready = dict(
        _read_json(
            run_dir
            / "replenished_segments"
            / "segment_001"
            / "horizon_maintenance_ready.json"
        )
    )
    failure_reason = str(first_failure.get("failure_reason") or "")
    expected_prefix = "bpf_owned_edge_route_baseline_missing_before_runtime:"
    existing_evidence = {
        "ut_default_routes": [
            {
                "key": ["ut2025", "ua0", "10.0.0.1", "10.0.0.2"],
                "ut_default_route_present": True,
            }
        ],
        "gateway_local_return_routes": [],
    }
    worker_evidence = {
        "ut_default_routes": [],
        "gateway_local_return_routes": [
            {
                "key": [
                    "gw-r128",
                    "ga0",
                    "10.1.0.1",
                    "10.1.0.2",
                    "10.0.0.2",
                ],
                "gw_bpf_local_return_route_present": True,
            }
        ],
    }
    merged = _merge_bpf_owned_route_identity_evidence(
        existing_evidence,
        worker_evidence,
    )
    worker_source = inspect.getsource(run_horizon_maintenance_process_payload)
    runtime_source = inspect.getsource(_run_live_temporal_runtime)
    blockers: list[Dict[str, Any]] = []
    if not failure_reason.startswith(expected_prefix):
        blockers.append({"reason": "r128_first_failure_evidence_missing"})
    if int(first_failure.get("runtime_epochs_completed") or 0) != 25:
        blockers.append({"reason": "r128_h0_lifecycle_not_completed_before_failure"})
    if post_run.get("clean_after_run") is not True:
        blockers.append({"reason": "r128_post_failure_cleanup_not_clean"})
    if ready.get("ready") is not True or int(
        ready.get("inactive_preload_record_count") or 0
    ) != 25:
        blockers.append({"reason": "r128_h1_worker_ready_evidence_missing"})
    if ready.get("edge_route_baseline_prepare_report"):
        blockers.append({"reason": "r128_old_worker_unexpectedly_prepared_edge_routes"})
    if (
        int(merged.get("ut_default_route_count") or 0) != 1
        or int(merged.get("gateway_local_return_route_count") or 0) != 1
    ):
        blockers.append({"reason": "r128_route_identity_handoff_merge_failed"})
    if (
        "_prepare_bpf_owned_edge_route_baseline" not in worker_source
        or '"edge_route_baseline_prepare_report"' not in worker_source
        or "prepared_bpf_owned_route_identity_evidence" not in worker_source
        or "horizon_maintenance_edge_route_handoff_incomplete" not in runtime_source
        or "_merge_bpf_owned_route_identity_evidence" not in runtime_source
    ):
        blockers.append({"reason": "r128_edge_route_worker_handoff_not_connected"})
    return _check(
        name="r128_edge_route_worker_handoff",
        catches=[
            "r128 H1 worker published 25 generations but omitted two future gateway local-return route baselines",
        ],
        ok=not blockers,
        evidence={
            "first_failure_error": failure_reason,
            "runtime_epochs_completed": int(
                first_failure.get("runtime_epochs_completed") or 0
            ),
            "post_failure_clean": bool(post_run.get("clean_after_run")),
            "old_worker_ready": bool(ready.get("ready")),
            "old_worker_inactive_preload_record_count": int(
                ready.get("inactive_preload_record_count") or 0
            ),
            "old_worker_edge_route_report_present": bool(
                ready.get("edge_route_baseline_prepare_report")
            ),
            "merged_ut_default_route_count": int(
                merged.get("ut_default_route_count") or 0
            ),
            "merged_gateway_local_return_route_count": int(
                merged.get("gateway_local_return_route_count") or 0
            ),
        },
        blockers=blockers,
    )


def _r130_staged_horizon_preload_lookup_check(run_dir: Path) -> Dict[str, Any]:
    first_failure_path = run_dir / "first_failure_summary.json"
    if first_failure_path.is_file():
        first_failure = dict(_read_json(first_failure_path))
    else:
        runtime_exception: Dict[str, Any] = {}
        for line in (run_dir / "events.raw.jsonl").read_text(
            encoding="utf-8"
        ).splitlines():
            event = dict(json.loads(line))
            if str(event.get("event_type") or "") == "runtime_exception":
                runtime_exception = event
        failure_context = dict(runtime_exception.get("failure_context") or {})
        epoch_context = dict(failure_context.get("current_epoch_context") or {})
        first_failure = {
            "failure_reason": str(runtime_exception.get("failure_reason") or ""),
            "runtime_epochs_completed": int(
                epoch_context.get("runtime_epochs_completed") or 0
            ),
            "evidence_source": "events.raw.jsonl:runtime_exception",
        }
    post_path = run_dir / "post_run_preflight.json"
    if not post_path.is_file():
        post_path = run_dir / "post_recovery_preflight.json"
    post_run = dict(_read_json(post_path))
    post_assessment = dict(post_run.get("assessment") or post_run)
    post_clean = bool(
        post_run.get("clean_after_run")
        or (
            post_assessment.get("ready")
            and post_assessment.get("environment_clean")
        )
    )
    ready = dict(
        _read_json(
            run_dir
            / "replenished_segments"
            / "segment_001"
            / "horizon_maintenance_ready.json"
        )
    )
    handoff = dict(
        _read_json(
            run_dir
            / "replenished_segments"
            / "segment_001"
            / "horizon_maintenance_handoff_preloaded_generations.json"
        )
    )
    records = [dict(item) for item in list(handoff.get("records") or [])]
    epoch_details = [
        dict(json.loads(line))
        for line in (run_dir / "epoch_execution_details.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    last_h0_detail = epoch_details[-1] if epoch_details else {}
    expected_staged_source_state_id = (
        "live_state:"
        + str(last_h0_detail.get("snapshot_id") or "")
        + ":"
        + str(last_h0_detail.get("epoch_id") or "")
    )
    blockers: list[Dict[str, Any]] = []
    result: Dict[str, Any] = {}
    failure_reason = str(first_failure.get("failure_reason") or "")
    if "reason=bpf_preloaded_generation_missing" not in failure_reason:
        blockers.append({"reason": "r130_first_failure_evidence_missing"})
    if int(first_failure.get("runtime_epochs_completed") or 0) != 25:
        blockers.append({"reason": "r130_runtime_lifecycle_count_mismatch"})
    if not post_clean:
        blockers.append({"reason": "r130_post_failure_cleanup_not_clean"})
    if (
        ready.get("ready") is not True
        or str(ready.get("current_horizon_id") or "") != "H0"
        or int(ready.get("inactive_preload_record_count") or 0) != 25
        or len(records) != 25
    ):
        blockers.append({"reason": "r130_h1_worker_handoff_evidence_invalid"})
    if not records or str(records[0].get("source_state_id") or "") != (
        expected_staged_source_state_id
    ):
        blockers.append({"reason": "r130_staged_source_state_identity_mismatch"})
    if records:
        record = records[0]
        proof = {
            "schema_version": "skyseed-bpf-owned-readiness-v1",
            "logical_intent_ready": True,
            "generation_id": int(record.get("generation_id") or 0),
            "intent_hash": str(record.get("intent_hash") or ""),
            "runtime_epoch_intent": dict(
                dict(record.get("preload_report") or {}).get(
                    "runtime_epoch_intent"
                )
                or {}
            ),
            "attached_interfaces": list(
                dict(record.get("preload_report") or {}).get(
                    "attached_interfaces"
                )
                or []
            ),
            "prepare_steps": {},
        }
        staged_readiness = {
            "ready": True,
            "ok": True,
            "active_generation": 24,
            "generation_id": 24,
            "current_horizon_id": "H0",
            "current_source_state_id": "r130-active-h0-before-boundary",
            "staged_target_horizon_id": "H1",
            "staged_source_state_id": expected_staged_source_state_id,
            "preloaded_generation_records": records,
            "attached_interfaces": [],
            "attach_plan": {},
            "attached_interface_details": [],
        }
        try:
            with patch(
                "linux_mpls_runtime._derive_bpf_owned_readiness_proof",
                return_value=proof,
            ), patch(
                "satellite.bpf_mpls_live.load_mpls_maps",
                return_value={
                    "ok": True,
                    "active_generation_flip_only": True,
                    "map_write_update_count": 0,
                    "active_generation_update_count": 1,
                    "active_generation_switch_count": 1,
                    "map_write_wall_seconds": 0.0,
                    "runtime_reload_attribution": {
                        "preloaded_generation_used": True,
                    },
                },
            ), patch(
                "linux_mpls_runtime._bpf_owned_readiness_mismatch_reason",
                return_value="",
            ):
                result = _prepare_bpf_owned_readiness(
                    runtime_update_plan=object(),
                    topology_path=Path("r130-frozen-replay-topology.json"),
                    contexts=(object(),),
                    bpf_owned_readiness=staged_readiness,
                    fail_closed_on_reuse_miss=True,
                )
        except Exception as exc:
            blockers.append(
                {
                    "reason": "r130_staged_h1_preload_replay_failed",
                    "exception_type": type(exc).__name__,
                    "message": str(exc),
                }
            )
        if result and not (
            result.get("ready") is True
            and result.get("preloaded_generation_consumed") is True
            and str(result.get("current_horizon_id") or "") == "H1"
            and result.get("active_generation") == record.get("generation_id")
        ):
            blockers.append({"reason": "r130_staged_h1_preload_not_consumed"})
    return _check(
        name="r130_staged_horizon_preload_lookup",
        catches=[
            "r130 published 25 ready H1 generations but looked them up under still-active H0 and failed closed at the boundary",
        ],
        ok=not blockers,
        evidence={
            "first_failure_error": failure_reason,
            "runtime_epochs_completed": int(
                first_failure.get("runtime_epochs_completed") or 0
            ),
            "post_failure_clean": post_clean,
            "worker_current_horizon_id": str(ready.get("current_horizon_id") or ""),
            "worker_handoff_record_count": len(records),
            "expected_staged_source_state_id": expected_staged_source_state_id,
            "first_h1_record_source_state_id": str(
                records[0].get("source_state_id") if records else ""
            ),
            "replay_ready": bool(result.get("ready")),
            "replay_current_horizon_id": str(
                result.get("current_horizon_id") or ""
            ),
            "replay_active_generation": int(result.get("active_generation") or 0),
        },
        blockers=blockers,
    )


def _r132_generation_zero_h3_consumer_check(run_dir: Path) -> Dict[str, Any]:
    manifest = _read_json(run_dir / "run_manifest.json")
    completed_epoch_count = sum(
        1
        for line in (run_dir / "epoch_execution_details.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    )
    segment_dir = run_dir / "replenished_segments" / "segment_003"
    ready = _read_json(segment_dir / "horizon_maintenance_ready.json")
    handoff = _read_json(
        segment_dir / "horizon_maintenance_handoff_preloaded_generations.json"
    )
    records = [
        dict(item)
        for item in list(handoff.get("records") or [])
        if isinstance(item, Mapping)
    ]
    blockers: list[Dict[str, Any]] = []
    failure_reason = str(manifest.get("failure_reason") or "")
    if "reason=bpf_preloaded_generation_missing" not in failure_reason:
        blockers.append({"reason": "r132_generation_zero_failure_evidence_missing"})
    if completed_epoch_count != 75:
        blockers.append({"reason": "r132_pre_h3_runtime_lifecycle_count_mismatch"})
    if manifest.get("post_run_clean") is not True:
        blockers.append({"reason": "r132_post_failure_cleanup_not_clean"})
    if (
        ready.get("ready") is not True
        or str(ready.get("next_horizon_id") or "") != "H3"
        or len(records) != 25
        or any(record.get("inactive_generation_written") is not True for record in records)
    ):
        blockers.append({"reason": "r132_h3_worker_handoff_evidence_invalid"})

    first_result: Dict[str, Any] = {}
    successor_result: Dict[str, Any] = {}
    first_record = records[0] if records else {}
    successor_record = records[1] if len(records) > 1 else {}
    if int(first_record.get("generation_id") or 0) != 0:
        blockers.append({"reason": "r132_h3_first_physical_generation_not_zero"})
    if first_record:
        direct_match = _bpf_owned_find_preloaded_generation_record(
            readiness={"preloaded_generation_records": records},
            target_generation_id=0,
            target_intent_hash=str(first_record.get("intent_hash") or ""),
            target_horizon_id="H3",
            current_source_state_id=str(first_record.get("source_state_id") or ""),
        )
        if direct_match is None:
            blockers.append({"reason": "r132_generation_zero_direct_lookup_failed"})

        staged_readiness = {
            "ready": True,
            "ok": True,
            "active_generation": 88,
            "generation_id": 88,
            "current_horizon_id": "H2",
            "current_source_state_id": "r132-active-h2-before-boundary",
            "staged_target_horizon_id": "H3",
            "staged_source_state_id": str(first_record.get("source_state_id") or ""),
            "preloaded_generation_records": records,
            "attached_interfaces": [],
            "attach_plan": {},
            "attached_interface_details": [],
            "prepared_generation_ids": list(ready.get("prepared_generation_ids") or []),
        }

        def proof_for(record: Mapping[str, Any]) -> Dict[str, Any]:
            preload_report = dict(record.get("preload_report") or {})
            return {
                "schema_version": "skyseed-bpf-owned-readiness-v1",
                "logical_intent_ready": True,
                "generation_id": int(record.get("generation_id") or 0),
                "intent_hash": str(record.get("intent_hash") or ""),
                "runtime_epoch_intent": dict(
                    preload_report.get("runtime_epoch_intent") or {}
                ),
                "prepare_steps": {},
            }

        map_load = {
            "ok": True,
            "active_generation_flip_only": True,
            "map_write_update_count": 0,
            "active_generation_update_count": 1,
            "active_generation_switch_count": 1,
            "map_write_wall_seconds": 0.0,
            "runtime_reload_attribution": {"preloaded_generation_used": True},
        }
        try:
            with patch(
                "linux_mpls_runtime._derive_bpf_owned_readiness_proof",
                return_value=proof_for(first_record),
            ), patch(
                "satellite.bpf_mpls_live.load_mpls_maps",
                return_value=map_load,
            ), patch(
                "linux_mpls_runtime._bpf_owned_readiness_mismatch_reason",
                return_value="",
            ):
                first_result = _prepare_bpf_owned_readiness(
                    runtime_update_plan=object(),
                    topology_path=Path("r132-frozen-replay-topology.json"),
                    contexts=(object(),),
                    bpf_owned_readiness=staged_readiness,
                    fail_closed_on_reuse_miss=True,
                )
            with patch(
                "linux_mpls_runtime._derive_bpf_owned_readiness_proof",
                return_value=proof_for(successor_record),
            ), patch(
                "satellite.bpf_mpls_live.load_mpls_maps",
                return_value=map_load,
            ), patch(
                "linux_mpls_runtime._bpf_owned_readiness_mismatch_reason",
                return_value="",
            ):
                successor_result = _prepare_bpf_owned_readiness(
                    runtime_update_plan=object(),
                    topology_path=Path("r132-frozen-replay-topology.json"),
                    contexts=(object(),),
                    bpf_owned_readiness=first_result,
                    fail_closed_on_reuse_miss=True,
                )
        except Exception as exc:
            blockers.append(
                {
                    "reason": "r132_h3_generation_zero_lifecycle_replay_failed",
                    "exception_type": type(exc).__name__,
                    "message": str(exc),
                }
            )
        if first_result and not (
            first_result.get("ready") is True
            and first_result.get("preloaded_generation_consumed") is True
            and first_result.get("active_generation") == 0
            and len(list(first_result.get("preloaded_generation_records") or [])) == 24
            and all(
                record.get("generation_id") != 0
                for record in list(first_result.get("preloaded_generation_records") or [])
            )
        ):
            blockers.append({"reason": "r132_h3_generation_zero_not_consumed_once"})
        if successor_result and not (
            successor_result.get("ready") is True
            and successor_result.get("preloaded_generation_consumed") is True
            and successor_result.get("active_generation") == 1
            and len(list(successor_result.get("preloaded_generation_records") or [])) == 23
        ):
            blockers.append({"reason": "r132_h3_generation_one_successor_not_consumed"})

    return _check(
        name="r132_generation_zero_h3_consumer",
        catches=[
            "r132 completed H0/H1/H2 75/75 then rejected valid H3 physical generation zero because zero was treated as a missing value",
        ],
        ok=not blockers,
        evidence={
            "failure_reason": failure_reason,
            "completed_epoch_count": completed_epoch_count,
            "post_run_clean": bool(manifest.get("post_run_clean")),
            "h3_record_count": len(records),
            "h3_written_record_count": sum(
                record.get("inactive_generation_written") is True
                for record in records
            ),
            "first_generation_id": first_record.get("generation_id"),
            "first_consumer_ready": bool(first_result.get("ready")),
            "first_consumer_remaining_record_count": len(
                list(first_result.get("preloaded_generation_records") or [])
            ),
            "successor_consumer_ready": bool(successor_result.get("ready")),
            "successor_active_generation": successor_result.get("active_generation"),
        },
        blockers=blockers,
    )


def _r133_nonblocking_ping_observer_gap_check(run_dir: Path) -> Dict[str, Any]:
    manifest = _read_json(run_dir / "run_manifest.json")
    runtime_report = _read_json(run_dir / "real_runtime_report.json")
    observation_path = run_dir / "runtime_ping_observation.jsonl"
    observation_count = sum(
        1
        for line in observation_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    observer_enabled = bool(
        dict(runtime_report.get("artifact_recording") or {}).get(
            "runtime_ping_observer_enabled"
        )
    )
    blockers: list[Dict[str, Any]] = []
    if manifest.get("aggregate_runtime_succeeded") is not True:
        blockers.append({"reason": "r133_4h_dataplane_success_evidence_missing"})
    if observer_enabled:
        blockers.append({"reason": "r133_ping_observer_gap_not_reproduced"})
    if observation_count != 0:
        blockers.append({"reason": "r133_empty_ping_observation_evidence_missing"})
    return _check(
        name="r133_nonblocking_ping_observer_gap",
        catches=[
            "r133 completed 100/100 but the canonical profile disabled the nonblocking runtime ping observer and produced no raw observations",
        ],
        ok=not blockers,
        evidence={
            "aggregate_runtime_succeeded": bool(
                manifest.get("aggregate_runtime_succeeded")
            ),
            "observer_enabled": observer_enabled,
            "observation_count": observation_count,
            "ping_success": manifest.get("ping_success"),
        },
        blockers=blockers,
    )


def _r129_resident_attach_and_retired_cleanup_handoff_check(
    run_dir: Path,
) -> Dict[str, Any]:
    first_failure = dict(_read_json(run_dir / "first_failure_summary.json"))
    post_run = dict(_read_json(run_dir / "post_run_preflight.json"))
    h1_ready = dict(
        _read_json(
            run_dir
            / "replenished_segments"
            / "segment_001"
            / "horizon_maintenance_ready.json"
        )
    )
    h2_failure = dict(
        _read_json(
            run_dir
            / "replenished_segments"
            / "segment_002"
            / "horizon_maintenance_failure.json"
        )
    )
    h2_work = dict(
        _read_json(
            run_dir
            / "replenished_segments"
            / "segment_002"
            / "horizon_maintenance_process_work.json"
        )
    )
    failed_ref = "netns:stl-940098:fs0098"
    h2_attach = dict(h2_failure.get("bpf_attach_capability_report") or {})
    h2_prepare = dict(
        dict(h2_attach.get("prepare_steps") or {}).get(
            "preactivation_attach_prepare"
        )
        or {}
    )
    failed_steps = [
        dict(step)
        for step in list(dict(h2_prepare.get("steps") or {}).get("attach") or [])
        if str(dict(step.get("interface_ref") or {}).get("interface_ref") or "")
        == failed_ref
    ]
    failed_detail = next(
        (
            dict(detail)
            for detail in list(h2_attach.get("attached_interface_details") or [])
            if str(dict(detail).get("interface_ref") or "") == failed_ref
        ),
        {},
    )
    h1_attach = dict(h1_ready.get("bpf_attach_capability_report") or {})
    existing = {
        "attach_plan": {failed_ref: ["ingress"]},
        "attached_interfaces": [failed_ref],
        "attached_interface_details": [failed_detail],
    }
    merged = _merge_prepared_attach_capability_readiness(existing, h1_attach)
    pending: Dict[str, Dict[int, Dict[str, Any]]] = {}
    compiled_manifest = {
        "schema_version": "skyseed-bpf-owned-compiled-runtime-inputs-v2",
        "active_generation": {"value": 0},
        "ut_gateway_policy_map": [],
        "ingress_classifier_map": [],
        "gateway_selector_ingress_map": [],
        "core_lfib_map": [],
        "egress_handoff_map": [],
        "zone_edge_delivery_map": [],
        "generation_base_map": [],
    }
    for generation_id in range(25):
        _retired_cleanup_candidate_lifecycle_transition(
            pending_by_horizon=pending,
            candidate={
                "generation_id": generation_id,
                "horizon_id": "H0",
                "compiled_runtime_inputs": {
                    **compiled_manifest,
                    "active_generation": {"value": generation_id},
                },
            },
            readiness={"current_horizon_id": "H0"},
        )
    promoted = _retired_cleanup_candidate_lifecycle_transition(
        pending_by_horizon=pending,
        candidate={
            "generation_id": 24,
            "horizon_id": "H0",
            "compiled_runtime_inputs": {
                **compiled_manifest,
                "active_generation": {"value": 24},
            },
        },
        readiness={"current_horizon_id": "H1"},
    )
    runtime_source = inspect.getsource(_run_live_temporal_runtime)
    failure_reason = str(first_failure.get("failure_reason") or "")
    blockers: list[Dict[str, Any]] = []
    if "tc_bpf_frozen_delta_attach_failed:" + failed_ref not in failure_reason:
        blockers.append({"reason": "r129_first_failure_evidence_missing"})
    if int(first_failure.get("runtime_epochs_completed") or 0) != 39:
        blockers.append({"reason": "r129_runtime_lifecycle_count_mismatch"})
    if post_run.get("clean_after_run") is not True:
        blockers.append({"reason": "r129_post_failure_cleanup_not_clean"})
    if (
        dict(h1_ready.get("edge_route_baseline_prepare_report") or {}).get("ready")
        is not True
    ):
        blockers.append({"reason": "r129_h1_route_handoff_did_not_pass"})
    if not failed_steps or not any(
        int(step.get("returncode") or 0) == 2
        and step.get("clsact_lifecycle_event") == "direct_add_new_delta"
        for step in failed_steps
    ):
        blockers.append({"reason": "r129_existing_clsact_failure_not_reproduced"})
    if failed_ref in set(h1_attach.get("attached_interfaces") or []):
        blockers.append({"reason": "r129_old_handoff_unexpectedly_preserved_resident_ref"})
    if failed_ref not in set(merged.get("attached_interfaces") or []):
        blockers.append({"reason": "r129_resident_attach_union_failed"})
    if int(promoted.get("promoted_candidate_count") or 0) != 25:
        blockers.append({"reason": "r129_retired_horizon_candidates_not_promoted"})
    old_cleanup_report = dict(h2_work.get("retired_cleanup_report") or {})
    if not (
        old_cleanup_report.get("status") == "completed"
        and int(h2_work.get("retired_cleanup_work_item_count") or 0) == 0
        and any(
            str(blocker.get("stage") or "") == "retired_generation_cleanup"
            and str(blocker.get("status") or "") == "unknown"
            for blocker in list(h2_failure.get("blockers") or [])
        )
    ):
        blockers.append({"reason": "r129_zero_cleanup_contract_drift_not_reproduced"})
    if (
        "_merge_prepared_attach_capability_readiness" not in runtime_source
        or "bpf_retired_cleanup_pending_candidates_by_horizon" not in runtime_source
        or "_retired_cleanup_candidate_lifecycle_transition" not in runtime_source
        or "_stage_bpf_owned_readiness_for_preloaded_apply" not in runtime_source
    ):
        blockers.append({"reason": "r129_handoff_fix_not_connected"})
    return _check(
        name="r129_resident_attach_and_retired_cleanup_handoff",
        catches=[
            "r129 H2 treated an H0-resident clsact interface as a new delta after H1 handoff replaced rather than unioned attach capability",
            "r129 discarded H0 cleanup candidates until retirement and then passed completed/0 work that the worker reclassified unknown",
        ],
        ok=not blockers,
        evidence={
            "first_failure_error": failure_reason,
            "runtime_epochs_completed": int(
                first_failure.get("runtime_epochs_completed") or 0
            ),
            "post_failure_clean": bool(post_run.get("clean_after_run")),
            "failed_interface_ref": failed_ref,
            "old_h1_attach_interface_count": len(
                list(h1_attach.get("attached_interfaces") or [])
            ),
            "resident_union_attach_interface_count": len(
                list(merged.get("attached_interfaces") or [])
            ),
            "resident_ref_preserved_by_union": failed_ref
            in set(merged.get("attached_interfaces") or []),
            "promoted_retired_h0_candidate_count": int(
                promoted.get("promoted_candidate_count") or 0
            ),
            "old_h2_cleanup_work_item_count": int(
                h2_work.get("retired_cleanup_work_item_count") or 0
            ),
            "old_h2_cleanup_input_status": str(
                old_cleanup_report.get("status") or ""
            ),
        },
        blockers=blockers,
    )
def _r123_pid_seal_cost_and_compaction_check(run_dir: Path) -> Dict[str, Any]:
    work_paths = sorted(
        run_dir.glob(
            "replenished_segments/segment_*/horizon_maintenance_process_work.json"
        )
    )
    process_summary_paths = sorted(
        run_dir.glob(
            "replenished_segments/segment_*/horizon_maintenance_process_summary.json"
        )
    )
    pre_runtime_path = run_dir / "pre_runtime_bpf_owned_prepare.json"
    first_failure_path = run_dir / "first_failure_summary.json"
    ready_path = (
        process_summary_paths[0].parent / "horizon_maintenance_ready.json"
        if process_summary_paths
        else run_dir / "missing-horizon-maintenance-ready.json"
    )
    blockers: list[Dict[str, Any]] = []
    evidence: Dict[str, Any] = {"run_dir": str(run_dir)}
    required_paths = [
        *work_paths[:1],
        *process_summary_paths[:1],
        pre_runtime_path,
        first_failure_path,
        ready_path,
    ]
    if len(required_paths) != 5 or any(not path.is_file() for path in required_paths):
        return _check(
            name="r123_pid_seal_cost_and_compaction",
            catches=[
                "r123 unsealed parent PID cache rejected a ready H1 worker",
                "r123 repeated H0 discovery and clsact guard consumed about 1000 seconds",
                "r123 ready/handoff artifacts nested full generation payloads",
            ],
            ok=False,
            evidence=evidence,
            blockers=[{"reason": "r123_required_artifacts_missing"}],
        )

    work = dict(_read_json(work_paths[0]))
    process_summary = dict(_read_json(process_summary_paths[0]))
    pre_runtime = dict(_read_json(pre_runtime_path))
    first_failure = dict(_read_json(first_failure_path))
    ready = dict(_read_json(ready_path))
    frozen_pid_cache = {
        str(node_id): int(pid)
        for node_id, pid in dict(work.get("frozen_container_pid_cache") or {}).items()
        if str(node_id) and int(pid) > 0
    }
    base_binding = dict(
        dict(
            dict(work.get("base_generation_record") or {}).get(
                "compiled_runtime_inputs"
            )
            or {}
        ).get("frozen_interface_binding_cache")
        or {}
    )
    base_binding_refs = {
        *dict(base_binding.get("ifindex_by_interface_ref") or {}),
        *dict(base_binding.get("mac_by_interface_ref") or {}),
    }
    base_binding_node_ids = {
        parts[1]
        for interface_ref in base_binding_refs
        if len(parts := str(interface_ref).split(":", 2)) == 3
        and parts[0] == "netns"
        and parts[1]
    }
    if base_binding_node_ids != set(frozen_pid_cache):
        blockers.append({"reason": "r123_pid_seal_scope_not_exact_base_binding_nodes"})
    existing_scope = {
        str(slot_name): {str(item) for item in list(items or []) if str(item)}
        for slot_name, items in dict(
            work.get("existing_prepared_resource_scope_by_slot") or {}
        ).items()
        if str(slot_name)
    }
    prepared_scope = {
        str(slot_name): {str(item) for item in list(items or []) if str(item)}
        for slot_name, items in dict(
            dict(ready.get("host_additive_prepare_report") or {}).get(
                "prepared_resource_scope_by_slot"
            )
            or {}
        ).items()
        if str(slot_name)
    }
    installer = DirectNetnsVethSlotLinkNamespaceQdiscInstaller()
    installer._runtime_prepared = True
    installer._prepared_topology_path = str(work.get("topology_path") or "")
    installer._prepared_link_state_path = str(work.get("link_state_path") or "")
    installer._prepared_resource_keys_by_slot = {
        slot_name: set(items) for slot_name, items in existing_scope.items()
    }
    installer._container_pid_cache = dict(frozen_pid_cache)
    unsealed_error = ""
    try:
        installer.accept_horizon_maintenance_prepared_state(
            topology_path=Path(str(work.get("topology_path") or "")),
            link_state_path=Path(str(work.get("link_state_path") or "")),
            prepared_resource_scope_by_slot=prepared_scope,
            frozen_container_pid_cache=frozen_pid_cache,
        )
    except RuntimeError as exc:
        unsealed_error = str(exc)
    if unsealed_error != "horizon_maintenance_handoff_pid_cache_not_sealed":
        blockers.append({"reason": "r123_unsealed_pid_cache_not_reproduced"})
    try:
        seal = installer.seal_container_pid_cache(
            expected_node_ids=sorted(frozen_pid_cache)
        )
        handoff = installer.accept_horizon_maintenance_prepared_state(
            topology_path=Path(str(work.get("topology_path") or "")),
            link_state_path=Path(str(work.get("link_state_path") or "")),
            prepared_resource_scope_by_slot=prepared_scope,
            frozen_container_pid_cache=installer.frozen_container_pid_cache_snapshot(),
        )
        if seal.get("complete") is not True or handoff.get("ok") is not True:
            raise RuntimeError("sealed_handoff_not_ready")
    except Exception as exc:
        blockers.append(
            {
                "reason": "r123_pid_seal_handoff_replay_failed",
                "message": str(exc),
            }
        )

    preload_records = [
        dict(item)
        for item in list(pre_runtime.get("preloaded_generation_records") or [])
        if isinstance(item, Mapping)
    ]
    discovery_seconds = sum(
        float(
            dict(record.get("timing_breakdown") or {}).get(
                "interface_discovery_logical_binding_wall_seconds"
            )
            or 0.0
        )
        for record in preload_records
    )
    clsact_seconds = sum(
        float(
            dict(record.get("timing_breakdown") or {}).get(
                "bpf_reused_attach_clsact_guard_seconds"
            )
            or 0.0
        )
        for record in preload_records
    )
    if discovery_seconds < 600.0 or clsact_seconds < 250.0:
        blockers.append({"reason": "r123_repeated_cost_evidence_not_reproduced"})
    if str(first_failure.get("failure_reason") or "") != (
        "horizon_maintenance_handoff_pid_cache_mismatch"
    ):
        blockers.append({"reason": "r123_pid_failure_evidence_missing"})
    if process_summary.get("ready") is not True or int(
        process_summary.get("inactive_preload_record_count") or 0
    ) != 25:
        blockers.append({"reason": "r123_worker_ready_evidence_missing"})

    preload_source = inspect.getsource(_prepare_bpf_owned_preactivation_preload)
    runtime_source = inspect.getsource(MinimalRealLinuxMplsInstaller.apply_and_validate)
    requirement_source = inspect.getsource(
        _required_interface_refs_for_preload_work_items
    )
    cleanup_source = inspect.getsource(
        DirectNetnsVethSlotLinkNamespaceQdiscInstaller.cleanup_runtime_artifacts
    )
    binding_capture_source = inspect.getsource(
        DirectNetnsVethSlotLinkNamespaceQdiscInstaller.
        capture_existing_container_interface_bindings
    )
    delta_attach_executor_source = inspect.getsource(
        _execute_bpf_delta_attach_tasks
    )
    frozen_attach_source = inspect.getsource(
        _prepare_bpf_owned_preactivation_attach_capability_from_frozen_inputs
    )
    readiness_source = inspect.getsource(_prepare_bpf_owned_readiness)
    bpftool_resolver_source = inspect.getsource(resolve_bpftool)
    bpf_tool_environment_source = inspect.getsource(collect_environment)
    bpf_run_command_source = inspect.getsource(run_command)
    activation_source = inspect.getsource(
        DirectNetnsVethSlotLinkNamespaceQdiscInstaller._activate_resource
    )
    activation_group_source = inspect.getsource(
        DirectNetnsVethSlotLinkNamespaceQdiscInstaller.apply_operation_group
    )
    strict_parameter_recovery = _parameter_epoch_recovery_contract(
        effective_epoch_type="parameter_epoch",
        operational_clean_mode=True,
        forwarding_backend="bpf_owned",
        planned_operations=(),
        runtime_update_plan={},
        recovery_reason="pre_long_run_contract_replay",
    )
    source_contracts = {
        "h0_preload_uses_empty_contexts": "contexts=()" in preload_source,
        "h0_preload_requires_frozen_inputs": (
            "require_frozen_compiled_runtime_inputs=True" in preload_source
        ),
        "h0_preload_requires_prepared_attach": (
            "require_prepared_attach_capability_for_preactivation_preload=True"
            in preload_source
        ),
        "runtime_lookahead_parameter_deleted": (
            "lookahead_bpf_preload_contexts" not in runtime_source
        ),
        "worker_requirement_compile_deleted": (
            "_bpf_owned_compile_mpls_maps_for_contexts" not in requirement_source
        ),
        "worker_requirement_is_explicit_context_refs": (
            "_bpf_owned_interface_binding_refs_for_contexts" in requirement_source
        ),
        "cleanup_container_peer_delete_deleted": (
            "_delete_container_interface" not in cleanup_source
        ),
        "cleanup_temp_delete_deleted": (
            "_delete_host_interface_best_effort(temp" not in cleanup_source
        ),
        "targeted_binding_capture_bounded_parallel": (
            "ThreadPoolExecutor" in binding_capture_source
            and "max_workers=effective_workers" in binding_capture_source
        ),
        "maintenance_delta_attach_bounded_parallel": (
            "ThreadPoolExecutor" in delta_attach_executor_source
            and "max_workers=effective_workers" in delta_attach_executor_source
            and "task_indexes_by_node" in delta_attach_executor_source
            and '"parallel_unit": "frozen_node"' in delta_attach_executor_source
            and "_execute_bpf_delta_attach_tasks" in frozen_attach_source
            and "max_attach_workers=max_attach_workers" in frozen_attach_source
        ),
        "bpf_prepare_failure_never_selects_linux_fallback": (
            '"fallback_to_linux": prepare.get' not in readiness_source
            and '"fallback_to_linux": False' in readiness_source
        ),
        "operational_clean_parameter_recovery_fails_closed": (
            strict_parameter_recovery.get("allowed") is False
            and strict_parameter_recovery.get("blocked") is True
            and str(strict_parameter_recovery.get("blocker_label") or "")
            == "PARAMETER_UPDATE_HIDDEN_BPF_RECOVERY_BLOCKER"
        ),
        "bpftool_is_explicit_input_without_discovery": (
            "SKYSEED_BPFTOOL_PATH" in bpftool_resolver_source
            and "shutil.which" not in bpftool_resolver_source
            and ".glob(" not in bpftool_resolver_source
        ),
        "bpf_toolchain_uses_exact_paths_without_discovery": (
            "EXACT_TOOL_PATHS" in bpf_tool_environment_source
            and "shutil.which" not in bpf_tool_environment_source
            and "EXACT_TOOL_PATHS" in bpf_run_command_source
            and "shutil.which" not in bpf_run_command_source
        ),
        "post_activation_discovery_path_deleted": (
            not hasattr(
                DirectNetnsVethSlotLinkNamespaceQdiscInstaller,
                "_probe_activated_resource",
            )
            and "_probe_activated_resource" not in activation_source
            and "_probe_activated_resource" not in activation_group_source
            and "-brief" not in activation_source
            and "-brief" not in activation_group_source
        ),
    }
    for contract_name, passed in source_contracts.items():
        if not passed:
            blockers.append({"reason": contract_name})

    large_marker = "r123-large-payload-marker"
    compact_record = _inactive_preload_record(
        queued_record={"record_id": "r123-compaction-replay"},
        preload_report={
            "ok": True,
            "inactive_generation_written": True,
            "active_generation_flip_skipped": True,
            "runtime_apply_skipped": True,
            "compiled_runtime_inputs": {
                "schema_version": "skyseed-bpf-owned-compiled-runtime-inputs-v2",
                "active_generation": {"value": 32},
                "ut_gateway_policy_map": [],
                "ingress_classifier_map": [{"payload": large_marker * 10000}],
                "gateway_selector_ingress_map": [],
                "core_lfib_map": [{"payload": large_marker * 10000}],
                "egress_handoff_map": [{"payload": large_marker * 10000}],
                "zone_edge_delivery_map": [],
                "generation_base_map": [],
            },
            "preactivation_preload": {
                "manifest_only_loader": True,
                "batch_map_write_used": True,
                "actual_data_write_count": 3,
            },
        },
        retain_generation_ids=[31, 32],
    )
    rendered_compact = json.dumps(compact_record, sort_keys=True)
    if large_marker in rendered_compact or len(rendered_compact) > 20_000:
        blockers.append({"reason": "r123_generation_artifact_compaction_failed"})

    artifact_sizes = {
        "normal_runtime_inputs_bytes": (
            run_dir / "normal_runtime_inputs.json"
        ).stat().st_size,
        "process_work_items_bytes": Path(
            str(work.get("preload_work_items_artifact") or "")
        ).stat().st_size,
        "ready_bytes": (
            ready_path.stat().st_size
        ),
        "handoff_preloaded_bytes": (
            process_summary_paths[0].parent
            / "horizon_maintenance_handoff_preloaded_generations.json"
        ).stat().st_size,
    }
    host_prepare_seconds = float(
        dict(ready.get("host_additive_prepare_report") or {}).get("wall_seconds")
        or 0.0
    )
    attach_prepare_seconds = float(
        dict(
            dict(
                dict(ready.get("bpf_attach_capability_report") or {}).get(
                    "prepare_steps"
                )
                or {}
            ).get("preactivation_attach_prepare")
            or {}
        ).get("wall_seconds")
        or 0.0
    )
    strict_compile_budget_seconds = 10.0
    old_unattributed_seconds = max(
        0.0,
        float(process_summary.get("wall_seconds") or 0.0)
        - host_prepare_seconds
        - attach_prepare_seconds
        - strict_compile_budget_seconds,
    )
    projected_worker_core_seconds = (
        host_prepare_seconds
        + strict_compile_budget_seconds
        + attach_prepare_seconds / 16.0
        + old_unattributed_seconds / 16.0
    )
    if projected_worker_core_seconds >= 100.0:
        blockers.append({"reason": "r123_projected_worker_core_budget_not_below_100s"})
    evidence.update(
        {
            "r123_failure_reason": first_failure.get("failure_reason"),
            "frozen_pid_count": len(frozen_pid_cache),
            "base_binding_ref_count": len(base_binding_refs),
            "base_binding_node_count": len(base_binding_node_ids),
            "pid_scope_matches_base_binding_nodes": (
                base_binding_node_ids == set(frozen_pid_cache)
            ),
            "unsealed_error": unsealed_error,
            "sealed_handoff_ok": not any(
                blocker.get("reason") == "r123_pid_seal_handoff_replay_failed"
                for blocker in blockers
            ),
            "preloaded_generation_count": len(preload_records),
            "discovery_seconds": round(discovery_seconds, 6),
            "clsact_guard_seconds": round(clsact_seconds, 6),
            "preload_wall_seconds": float(
                pre_runtime.get("pre_runtime_generation_preload_wall_seconds") or 0.0
            ),
            "worker_wall_seconds": float(process_summary.get("wall_seconds") or 0.0),
            "host_prepare_seconds": host_prepare_seconds,
            "attach_prepare_seconds": attach_prepare_seconds,
            "strict_compile_budget_seconds": strict_compile_budget_seconds,
            "old_unattributed_seconds": round(old_unattributed_seconds, 6),
            "projected_worker_core_seconds": round(
                projected_worker_core_seconds,
                6,
            ),
            "source_contracts": source_contracts,
            "compact_generation_record_bytes": len(rendered_compact.encode("utf-8")),
            "r123_artifact_sizes": artifact_sizes,
        }
    )
    return _check(
        name="r123_pid_seal_cost_and_compaction",
        catches=[
            "r123 unsealed parent PID cache rejected a ready H1 worker",
            "r123 repeated H0 discovery and clsact guard consumed about 1000 seconds",
            "r123 25-generation worker performed a duplicate requirements compile pass",
            "r123 ready/handoff artifacts nested full generation payloads",
            "r123 cleanup repeated peer and completed-temp deletes",
        ],
        ok=not blockers,
        evidence=evidence,
        blockers=blockers,
    )


def run_contract_check(
    *,
    candidate_profile_run_dir: Path,
    worker_failed_run_dir: Path,
    cleanup_failed_run_dir: Path,
    initial_binding_failed_run_dir: Path,
    final_binding_conflict_run_dir: Path,
    parent_host_handoff_failed_run_dir: Path,
    compose_multicast_gap_run_dir: Path,
    latest_failed_run_dir: Path,
    initial_attach_handoff_failed_run_dir: Path,
    typed_context_failed_run_dir: Path,
    edge_route_handoff_failed_run_dir: Path,
    resident_attach_handoff_failed_run_dir: Path,
    staged_horizon_handoff_failed_run_dir: Path,
    generation_zero_h3_failed_run_dir: Path,
    ping_observer_missing_run_dir: Path,
    shared_gz_r134_replay_report: Path,
    retired_compose_ix_failed_run_dir: Path,
    compose_build_timeout_failed_run_dir: Path,
    runtime_consumer_failed_run_dir: Path,
    runtime_consumer_replay_report: Path,
    expected_horizon_count: int,
    expected_entries_per_horizon: int,
) -> Dict[str, Any]:
    checks = [
        _profile_command_shape_check(
            candidate_profile_run_dir,
            expected_horizon_count=expected_horizon_count,
        ),
        _frozen_horizon_scope_check(
            worker_failed_run_dir,
            expected_horizon_count=expected_horizon_count,
            expected_entries_per_horizon=expected_entries_per_horizon,
        ),
        _runtime_queue_coverage_check(
            worker_failed_run_dir,
            expected_horizon_count=expected_horizon_count,
        ),
        _worker_process_replay_check(worker_failed_run_dir),
        _real_created_veth_binding_handoff_check(),
        _real_unicast_physical_link_check(),
        _real_compose_ix_unicast_prepare_check(),
        _initial_frozen_binding_producer_check(initial_binding_failed_run_dir),
        _actual_frozen_generation_compile_check(worker_failed_run_dir),
        _final_binding_conflict_replay_check(final_binding_conflict_run_dir),
        _parent_host_prepare_handoff_replay_check(
            parent_host_handoff_failed_run_dir
        ),
        _r122_compose_multicast_gap_replay_check(
            compose_multicast_gap_run_dir
        ),
        _r123_pid_seal_cost_and_compaction_check(latest_failed_run_dir),
        _r125_initial_attach_h0_preload_handoff_check(
            initial_attach_handoff_failed_run_dir
        ),
        _r127_typed_context_worker_handoff_check(typed_context_failed_run_dir),
        _r128_edge_route_worker_handoff_check(edge_route_handoff_failed_run_dir),
        _r129_resident_attach_and_retired_cleanup_handoff_check(
            resident_attach_handoff_failed_run_dir
        ),
        _r130_staged_horizon_preload_lookup_check(
            staged_horizon_handoff_failed_run_dir
        ),
        _r132_generation_zero_h3_consumer_check(
            generation_zero_h3_failed_run_dir
        ),
        _r133_nonblocking_ping_observer_gap_check(
            ping_observer_missing_run_dir
        ),
        _r134_shared_gz_replay_report_check(shared_gz_r134_replay_report),
        _frozen_live_state_alias_consumer_check(),
        _r135_retired_compose_ix_active_path_check(
            retired_compose_ix_failed_run_dir
        ),
        _r136_useful_compose_build_timeout_check(
            compose_build_timeout_failed_run_dir
        ),
        _r138_runtime_consumer_contract_replay_check(
            runtime_consumer_failed_run_dir,
            runtime_consumer_replay_report,
        ),
        _previous_failed_worker_input_evidence_check(worker_failed_run_dir),
        _previous_failed_cleanup_evidence_check(cleanup_failed_run_dir),
        _cleanup_lifecycle_semantic_check(
            final_binding_conflict_run_dir,
            expected_entries_per_horizon=expected_entries_per_horizon,
        ),
        _frozen_direct_delta_attach_check(),
    ]
    checks = [
        (
            check
            if isinstance(check, Mapping)
            else _check(
                name=f"contract_check_{index}_returned_invalid_result",
                catches=["companion check implementation returned no structured result"],
                ok=False,
                evidence={"result_type": type(check).__name__},
                blockers=[{"reason": "contract_check_invalid_result"}],
            )
        )
        for index, check in enumerate(checks)
    ]
    blockers = [
        {"check": check["name"], **dict(blocker)}
        for check in checks
        for blocker in list(check.get("blockers") or [])
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "candidate_profile_run_dir": str(candidate_profile_run_dir),
        "worker_failed_run_dir": str(worker_failed_run_dir),
        "cleanup_failed_run_dir": str(cleanup_failed_run_dir),
        "initial_binding_failed_run_dir": str(initial_binding_failed_run_dir),
        "final_binding_conflict_run_dir": str(final_binding_conflict_run_dir),
        "parent_host_handoff_failed_run_dir": str(
            parent_host_handoff_failed_run_dir
        ),
        "compose_multicast_gap_run_dir": str(compose_multicast_gap_run_dir),
        "latest_failed_run_dir": str(latest_failed_run_dir),
        "initial_attach_handoff_failed_run_dir": str(
            initial_attach_handoff_failed_run_dir
        ),
        "typed_context_failed_run_dir": str(typed_context_failed_run_dir),
        "edge_route_handoff_failed_run_dir": str(
            edge_route_handoff_failed_run_dir
        ),
        "resident_attach_handoff_failed_run_dir": str(
            resident_attach_handoff_failed_run_dir
        ),
        "staged_horizon_handoff_failed_run_dir": str(
            staged_horizon_handoff_failed_run_dir
        ),
        "generation_zero_h3_failed_run_dir": str(
            generation_zero_h3_failed_run_dir
        ),
        "ping_observer_missing_run_dir": str(ping_observer_missing_run_dir),
        "shared_gz_r134_replay_report": str(shared_gz_r134_replay_report),
        "retired_compose_ix_failed_run_dir": str(
            retired_compose_ix_failed_run_dir
        ),
        "compose_build_timeout_failed_run_dir": str(
            compose_build_timeout_failed_run_dir
        ),
        "runtime_consumer_failed_run_dir": str(runtime_consumer_failed_run_dir),
        "runtime_consumer_replay_report": str(runtime_consumer_replay_report),
        "ok": not blockers,
        "check_count": len(checks),
        "failed_check_count": sum(1 for check in checks if check.get("ok") is not True),
        "checks": checks,
        "blockers": blockers,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-profile-run-dir", required=True, type=Path)
    parser.add_argument("--worker-failed-run-dir", required=True, type=Path)
    parser.add_argument("--cleanup-failed-run-dir", required=True, type=Path)
    parser.add_argument("--initial-binding-failed-run-dir", required=True, type=Path)
    parser.add_argument("--final-binding-conflict-run-dir", required=True, type=Path)
    parser.add_argument(
        "--parent-host-handoff-failed-run-dir",
        required=True,
        type=Path,
    )
    parser.add_argument(
        "--compose-multicast-gap-run-dir",
        required=True,
        type=Path,
    )
    parser.add_argument("--latest-failed-run-dir", required=True, type=Path)
    parser.add_argument(
        "--initial-attach-handoff-failed-run-dir",
        required=True,
        type=Path,
    )
    parser.add_argument(
        "--typed-context-failed-run-dir",
        required=True,
        type=Path,
    )
    parser.add_argument(
        "--edge-route-handoff-failed-run-dir",
        required=True,
        type=Path,
    )
    parser.add_argument(
        "--resident-attach-handoff-failed-run-dir",
        required=True,
        type=Path,
    )
    parser.add_argument(
        "--staged-horizon-handoff-failed-run-dir",
        required=True,
        type=Path,
    )
    parser.add_argument(
        "--generation-zero-h3-failed-run-dir",
        required=True,
        type=Path,
    )
    parser.add_argument(
        "--ping-observer-missing-run-dir",
        required=True,
        type=Path,
    )
    parser.add_argument(
        "--shared-gz-r134-replay-report",
        required=True,
        type=Path,
    )
    parser.add_argument(
        "--retired-compose-ix-failed-run-dir",
        required=True,
        type=Path,
    )
    parser.add_argument(
        "--compose-build-timeout-failed-run-dir",
        required=True,
        type=Path,
    )
    parser.add_argument(
        "--runtime-consumer-failed-run-dir",
        required=True,
        type=Path,
    )
    parser.add_argument(
        "--runtime-consumer-replay-report",
        required=True,
        type=Path,
    )
    parser.add_argument("--expected-horizon-count", type=int, required=True)
    parser.add_argument("--expected-entries-per-horizon", type=int, required=True)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = run_contract_check(
        candidate_profile_run_dir=(
            args.candidate_profile_run_dir.expanduser().resolve()
        ),
        worker_failed_run_dir=args.worker_failed_run_dir.expanduser().resolve(),
        cleanup_failed_run_dir=args.cleanup_failed_run_dir.expanduser().resolve(),
        initial_binding_failed_run_dir=(
            args.initial_binding_failed_run_dir.expanduser().resolve()
        ),
        final_binding_conflict_run_dir=(
            args.final_binding_conflict_run_dir.expanduser().resolve()
        ),
        parent_host_handoff_failed_run_dir=(
            args.parent_host_handoff_failed_run_dir.expanduser().resolve()
        ),
        compose_multicast_gap_run_dir=(
            args.compose_multicast_gap_run_dir.expanduser().resolve()
        ),
        latest_failed_run_dir=args.latest_failed_run_dir.expanduser().resolve(),
        initial_attach_handoff_failed_run_dir=(
            args.initial_attach_handoff_failed_run_dir.expanduser().resolve()
        ),
        typed_context_failed_run_dir=(
            args.typed_context_failed_run_dir.expanduser().resolve()
        ),
        edge_route_handoff_failed_run_dir=(
            args.edge_route_handoff_failed_run_dir.expanduser().resolve()
        ),
        resident_attach_handoff_failed_run_dir=(
            args.resident_attach_handoff_failed_run_dir.expanduser().resolve()
        ),
        staged_horizon_handoff_failed_run_dir=(
            args.staged_horizon_handoff_failed_run_dir.expanduser().resolve()
        ),
        generation_zero_h3_failed_run_dir=(
            args.generation_zero_h3_failed_run_dir.expanduser().resolve()
        ),
        ping_observer_missing_run_dir=(
            args.ping_observer_missing_run_dir.expanduser().resolve()
        ),
        shared_gz_r134_replay_report=(
            args.shared_gz_r134_replay_report.expanduser().resolve()
        ),
        retired_compose_ix_failed_run_dir=(
            args.retired_compose_ix_failed_run_dir.expanduser().resolve()
        ),
        compose_build_timeout_failed_run_dir=(
            args.compose_build_timeout_failed_run_dir.expanduser().resolve()
        ),
        runtime_consumer_failed_run_dir=(
            args.runtime_consumer_failed_run_dir.expanduser().resolve()
        ),
        runtime_consumer_replay_report=(
            args.runtime_consumer_replay_report.expanduser().resolve()
        ),
        expected_horizon_count=int(args.expected_horizon_count),
        expected_entries_per_horizon=int(args.expected_entries_per_horizon),
    )
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report.get("ok") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
