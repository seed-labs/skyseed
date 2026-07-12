#!/usr/bin/env python3
# encoding: utf-8

"""Opt-in experimental tc-BPF backend apply path for bounded Skyseed runs."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Any, Sequence

from satellite.bpf_backend import compile_from_run_dir, parameter_loss_ppm


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = REPO_ROOT / "satellite" / "experimental" / "bpf" / "skyseed_tc_dataplane_canary.c"
PIN_BASE = Path("/sys/fs/bpf/tc/globals")
_CONTAINER_PID_CACHE: dict[str, int] = {}
_CONTAINER_PID_CACHE_COMPLETE = False
CANARY_PINNED_MAPS = [
    "active_generation",
    "generation_status",
    "fec_forwarding",
    "packet_counters",
    "interface_counters",
    "parameter_generation",
    "param_status",
    "parameter_values",
]
KERNEL_MAP_NAMES = {
    "active_generation": "active_generati",
    "generation_status": "generation_stat",
    "fec_forwarding": "fec_forwarding",
    "packet_counters": "packet_counters",
    "interface_counters": "interface_count",
    "parameter_generation": "parameter_gener",
    "param_status": "param_status",
    "parameter_values": "parameter_value",
}
COUNTER_LABELS = {
    0: "pass",
    1: "drop",
    2: "redirect",
    3: "canonical_fallback",
    4: "runtime_generation_hit",
    5: "fec_hit",
    6: "drop_missing_generation",
    7: "drop_missing_fec",
    8: "drop_inactive_fec",
    9: "parameter_generation_hit",
    10: "parameter_entry_hit",
    11: "drop_inactive_parameter",
    12: "drop_loss",
    13: "drop_missing_parameter",
    14: "drop_bad_packet",
    15: "return_handoff_hit",
    16: "seen",
}
IPPROTO_ICMP = 1
ACTION_FORWARD = 1
ACTION_PASS = 2
BPFTOOL = ""
EXACT_TOOL_PATHS = {
    "clang": "/usr/bin/clang",
    "tc": "/usr/sbin/tc",
    "ip": "/usr/sbin/ip",
    "docker": "/usr/bin/docker",
    "sudo": "/usr/bin/sudo",
    "nsenter": "/usr/bin/nsenter",
    "rm": "/usr/bin/rm",
    "test": "/usr/bin/test",
    "true": "/usr/bin/true",
}


BPF_BACKEND_MODE = "experimental_bpf_backend"
SELECTOR_MODE_CANARY = "semantic_intent_canary_subset"
SELECTOR_MODE_FULL_ELIGIBLE = "full_eligible_scope"
CANARY_MAX_SCOPE_ENTRIES = 16
FULL_SCOPE_SAFETY_LIMITS = {
    "max_runtime_generations": 512,
    "max_gz_fec_entries": 8192,
    "max_parameter_generations": 512,
    "max_parameter_entries": 8192,
    "max_scope_entries": 4096,
}
DEFAULT_CANARY_CAPACITIES = {
    "runtime_generations": 64,
    "gz_fec_entries": 16,
    "parameter_generations": 64,
    "parameter_entries": 16,
    "interface_counter_entries": 8192,
}
DEFAULT_BACKEND_SELECTOR = {
    "mode": SELECTOR_MODE_CANARY,
    "ut_ids": [],
    "gw_ids": [],
    "fec_ids": [],
    "zone_ids": [],
    "generation_ids": [],
    "parameter_generation_ids": [],
    "max_scope_entries": 1,
    "safety_limits": dict(FULL_SCOPE_SAFETY_LIMITS),
}


def apply_bpf_experimental_backend(
    *,
    run_dir: Path,
    topology_path: Path,
    link_state_path: Path,
    runtime_report: dict[str, Any],
    bpf_source: Path = DEFAULT_SOURCE,
    ping_count: int = 2,
    selector: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach an opt-in experimental BPF backend subset, ping, audit, rollback."""

    started_at = time.perf_counter()
    selector = normalize_selector(selector)
    compiled = compile_from_run_dir(run_dir, input_mode="experimental_bpf_backend")
    scope: dict[str, Any] = {
        "scope": "not_selected",
        "selector": selector,
        "entries": [],
        "attached_interfaces": [],
        "semantic_intent_sources": {
            "runtime": "G/Z FEC generation map + RuntimeEpoch metadata",
            "parameter": "ParameterEpoch execution details parameter map",
            "scope": "not_selected",
        },
    }
    report: dict[str, Any] = {
        "schema_version": 1,
        "backend_mode": BPF_BACKEND_MODE,
        "bpf_backend_mode": BPF_BACKEND_MODE,
        "bpf_enabled": True,
        "bpf_canary_enabled": True,
        "source": str(bpf_source),
        "run_dir": str(run_dir),
        "selector": selector,
        "scope": scope,
        "bpf_scope": scope["scope"],
        "qdisc_timeline_backend_status": "blocked_or_trace_only",
        "parameter_backend_fields": ["active", "loss_ppm", "drop", "counters"],
        "parameter_backend_excluded_fields": ["delay_ms", "rate_bps", "bandwidth_mbps"],
        "semantic_intent_sources": scope["semantic_intent_sources"],
        "canonical_runtime_replaced": False,
        "linux_direct_backend_replaced": False,
        "canonical_runtime_unchanged": True,
        "theaterq_or_external_qdisc_module": False,
        "metrics": _empty_metrics(
            enabled=True,
            backend_mode=BPF_BACKEND_MODE,
            bpf_scope=scope["scope"],
        ),
        "steps": {},
    }
    attached_interfaces: list[str] = []
    rollback_report: dict[str, Any] | None = None

    with tempfile.TemporaryDirectory(prefix="skyseed-bpf-canary-") as tmp:
        object_path = Path(tmp) / "skyseed_tc_dataplane_canary.o"
        try:
            try:
                runtime_report = enrich_runtime_report_from_run_dir(run_dir, runtime_report)
                scope = select_experimental_scope(
                    topology_path=topology_path,
                    link_state_path=link_state_path,
                    runtime_report=runtime_report,
                    compiled=compiled,
                    selector=selector,
                )
            except Exception as exc:
                report["ok"] = False
                report["reason"] = "scope_selection_failed_fail_closed"
                report["scope_selection_error"] = str(exc)
                report["scope_selection_diagnostics"] = dict(
                    getattr(
                        exc,
                        "skyseed_scope_selection_diagnostics",
                        diagnose_experimental_scope_selection(
                            runtime_report=runtime_report,
                            selector=selector,
                        ),
                    )
                    or {}
                )
                report["metrics"] = build_metrics(
                    enabled=True,
                    backend_mode=BPF_BACKEND_MODE,
                    bpf_scope=scope["scope"],
                    attached_interfaces=[],
                    loaded_runtime_generation_count=0,
                    loaded_gz_fec_count=0,
                    loaded_parameter_generation_count=0,
                    loaded_parameter_link_count=0,
                    active_generation_switch_count=0,
                    counters={
                        "drop_missing_fec": 1,
                        "drop": 1,
                    },
                    rollback_completed=True,
                )
                report["rollback"] = {
                    "steps": [],
                    "residue": {
                        "clsact_interface_hits": [],
                        "pinned_maps": {},
                        "clean": True,
                    },
                    "rollback_completed": True,
                }
                report["wall_seconds"] = round(time.perf_counter() - started_at, 6)
                return report
            report["scope"] = scope
            report["bpf_scope"] = scope["scope"]
            report["semantic_intent_sources"] = scope["semantic_intent_sources"]
            report["metrics"] = _empty_metrics(
                enabled=True,
                backend_mode=BPF_BACKEND_MODE,
                bpf_scope=scope["scope"],
            )
            environment = collect_environment()
            report["environment"] = environment
            blockers = attach_blockers(environment)
            report["blockers"] = blockers
            if blockers:
                report["ok"] = False
                report["reason"] = "toolchain_or_privilege_blocked"
                return report

            report["compiled_summary"] = {
                "runtime_generation_count": compiled["compact_summary"]["runtime_generation_count"],
                "gz_fec_entry_count": compiled["compact_summary"]["gz_fec_entry_count"],
                "parameter_generation_count": compiled["compact_summary"][
                    "parameter_generation_count"
                ],
                "parameter_link_count": compiled["compact_summary"][
                    "parameter_link_count"
                ],
                "parameter_entry_count": compiled["compact_summary"]["parameter_entry_count"],
                "fixed_10s_assumed": compiled["compact_summary"]["fixed_10s_assumed"],
                "qdisc_timeline_backend_status": compiled["compact_summary"][
                    "qdisc_timeline_backend_status"
                ],
                "canonical_runtime_unchanged": compiled["compact_summary"][
                    "canonical_runtime_unchanged"
                ],
            }
            capacities = bpf_map_capacities_for_scope(compiled=compiled, scope=scope)
            report["bpf_map_capacities"] = capacities
            report["steps"]["compile"] = compile_bpf(
                bpf_source,
                object_path,
                capacities=capacities,
            )
            if report["steps"]["compile"]["returncode"] != 0:
                report["ok"] = False
                report["reason"] = "compile_failed"
                return report

            report["steps"]["cleanup_before_attach"] = rollback_canary(
                attached_interfaces=scope["attached_interfaces"],
                remove_maps=True,
            )
            attach_results = []
            for interface_name in scope["attached_interfaces"]:
                attach_results.extend(attach_canary(interface_name, object_path))
                attached_interfaces.append(interface_name)
            report["steps"]["attach"] = attach_results
            if not attach_results or not all(
                command_ok(result) or is_tc_qdisc_delete(result)
                for result in attach_results
            ):
                report["ok"] = False
                report["reason"] = "tc_bpf_attach_failed"
                return report

            map_load = load_canary_maps(
                compiled=compiled,
                scope=scope,
                active_runtime_generation=int(
                    compiled["bpf_maps"]["runtime"]["active_generation"]["value"]
                ),
                active_parameter_generation=int(
                    compiled["bpf_maps"]["parameter"]["parameter_generation"]["value"]
                ),
            )
            report["steps"]["map_load"] = map_load
            if not map_load["ok"]:
                report["ok"] = False
                report["reason"] = "bpf_map_load_failed"
                return report

            sanity_entry = scope["entries"][0]
            ping = docker_ping(
                node_id=sanity_entry["ut_id"],
                target_ip=sanity_entry["target_ip"],
                count=ping_count,
            )
            report["steps"]["canary_ping"] = ping
            counters = read_packet_counters()
            report["steps"]["counters"] = counters
            report["metrics"] = build_metrics(
                enabled=True,
                backend_mode=BPF_BACKEND_MODE,
                bpf_scope=scope["scope"],
                attached_interfaces=attached_interfaces,
                loaded_runtime_generation_count=int(map_load["loaded_generation_count"]),
                loaded_gz_fec_count=int(map_load["loaded_fec_count"]),
                loaded_parameter_generation_count=int(
                    map_load["loaded_parameter_generation_count"]
                ),
                loaded_parameter_link_count=int(map_load["loaded_parameter_link_count"]),
                loaded_parameter_entry_count=int(
                    map_load.get("loaded_parameter_entry_count", 0)
                ),
                active_generation_switch_count=int(
                    map_load["active_generation_switch_count"]
                ),
                counters=counters.get("counters", {}),
                rollback_completed=False,
            )
            report["ok"] = (
                command_ok(ping)
                and int(report["metrics"].get("bpf_redirect_counters", 0)) > 0
                and int(report["metrics"].get("bpf_pass_counters", 0)) >= 0
            )
            report["reason"] = None if report["ok"] else "canary_ping_or_counter_check_failed"
            return report
        finally:
            rollback_report = rollback_canary(
                attached_interfaces=attached_interfaces or scope["attached_interfaces"],
                remove_maps=True,
            )
            report["rollback"] = rollback_report
            report["metrics"]["bpf_rollback_completed"] = bool(
                rollback_report.get("rollback_completed")
            )
            residue = rollback_report.get("residue", {})
            report["metrics"]["bpf_attached_interface_count_after_rollback"] = len(
                residue.get("clsact_interface_hits", [])
            )
            report["wall_seconds"] = round(time.perf_counter() - started_at, 6)


def apply_bpf_dataplane_canary(
    *,
    run_dir: Path,
    topology_path: Path,
    link_state_path: Path,
    runtime_report: dict[str, Any],
    bpf_source: Path = DEFAULT_SOURCE,
    ping_count: int = 2,
    selector: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Backward-compatible wrapper for the Goal 16 canary flag."""

    return apply_bpf_experimental_backend(
        run_dir=run_dir,
        topology_path=topology_path,
        link_state_path=link_state_path,
        runtime_report=runtime_report,
        bpf_source=bpf_source,
        ping_count=ping_count,
        selector=selector,
    )


def _empty_metrics(*, enabled: bool, backend_mode: str, bpf_scope: str) -> dict[str, Any]:
    return {
        "bpf_backend_mode": backend_mode,
        "bpf_enabled": bool(enabled),
        "bpf_scope": bpf_scope,
        "bpf_canary_enabled": bool(enabled),
        "bpf_attached_interface_count": 0,
        "loaded_runtime_generation_count": 0,
        "loaded_gz_fec_count": 0,
        "loaded_parameter_generation_count": 0,
        "loaded_parameter_link_count": 0,
        "loaded_parameter_entry_count": 0,
        "active_generation_switch_count": 0,
        "bpf_loaded_generation_count": 0,
        "bpf_loaded_fec_count": 0,
        "bpf_pass_counters": 0,
        "bpf_drop_counters": 0,
        "bpf_redirect_counters": 0,
        "canonical_fallback_count": 0,
        "bpf_fail_closed_counters": 0,
        "canonical_runtime_unchanged": True,
        "bpf_rollback_completed": False,
    }


def build_metrics(
    *,
    enabled: bool,
    backend_mode: str = BPF_BACKEND_MODE,
    bpf_scope: str = "semantic_intent_canary_subset",
    attached_interfaces: Sequence[str],
    loaded_runtime_generation_count: int | None = None,
    loaded_gz_fec_count: int | None = None,
    loaded_generation_count: int | None = None,
    loaded_fec_count: int | None = None,
    loaded_parameter_generation_count: int = 0,
    loaded_parameter_link_count: int = 0,
    loaded_parameter_entry_count: int = 0,
    active_generation_switch_count: int = 0,
    counters: dict[str, int],
    rollback_completed: bool,
) -> dict[str, Any]:
    runtime_generation_count = (
        int(loaded_runtime_generation_count)
        if loaded_runtime_generation_count is not None
        else int(loaded_generation_count or 0)
    )
    gz_fec_count = (
        int(loaded_gz_fec_count)
        if loaded_gz_fec_count is not None
        else int(loaded_fec_count or 0)
    )
    fail_closed = sum(
        int(counters.get(label, 0))
        for label in [
            "drop_missing_generation",
            "drop_missing_fec",
            "drop_inactive_fec",
            "drop_inactive_parameter",
            "drop_loss",
            "drop_missing_parameter",
            "drop_bad_packet",
        ]
    )
    return {
        "bpf_backend_mode": backend_mode,
        "bpf_enabled": bool(enabled),
        "bpf_scope": bpf_scope,
        "bpf_canary_enabled": bool(enabled),
        "bpf_attached_interface_count": len(list(attached_interfaces)),
        "loaded_runtime_generation_count": runtime_generation_count,
        "loaded_gz_fec_count": gz_fec_count,
        "loaded_parameter_generation_count": int(loaded_parameter_generation_count),
        "loaded_parameter_link_count": int(loaded_parameter_link_count),
        "loaded_parameter_entry_count": int(loaded_parameter_entry_count),
        "active_generation_switch_count": int(active_generation_switch_count),
        "bpf_loaded_generation_count": runtime_generation_count,
        "bpf_loaded_fec_count": gz_fec_count,
        "bpf_pass_counters": int(counters.get("pass", 0)),
        "bpf_drop_counters": int(counters.get("drop", 0)),
        "bpf_redirect_counters": int(counters.get("redirect", 0)),
        "canonical_fallback_count": int(counters.get("canonical_fallback", 0)),
        "bpf_fail_closed_counters": fail_closed,
        "bpf_packet_counters": dict(counters),
        "canonical_runtime_unchanged": True,
        "bpf_rollback_completed": bool(rollback_completed),
    }


def normalize_selector(selector: dict[str, Any] | None) -> dict[str, Any]:
    normalized = {
        **DEFAULT_BACKEND_SELECTOR,
        "safety_limits": dict(FULL_SCOPE_SAFETY_LIMITS),
    }
    if selector:
        for key, value in selector.items():
            if value is None:
                continue
            if key == "safety_limits" and isinstance(value, dict):
                normalized["safety_limits"].update(value)
            else:
                normalized[key] = value
    mode = str(normalized.get("mode") or SELECTOR_MODE_CANARY)
    if mode not in {SELECTOR_MODE_CANARY, SELECTOR_MODE_FULL_ELIGIBLE}:
        raise ValueError(f"unsupported BPF backend selector mode {mode!r}")
    normalized["mode"] = mode
    for key in ["ut_ids", "gw_ids", "fec_ids", "zone_ids"]:
        value = normalized.get(key) or []
        if isinstance(value, str):
            value = [part.strip() for part in value.split(",") if part.strip()]
        normalized[key] = sorted({str(item) for item in value if str(item)})
    for key in ["generation_ids", "parameter_generation_ids"]:
        value = normalized.get(key) or []
        if isinstance(value, str):
            value = [part.strip() for part in value.split(",") if part.strip()]
        normalized[key] = sorted({int(item) for item in value if str(item) != ""})
    safety_limits = dict(normalized["safety_limits"])
    for key, default in FULL_SCOPE_SAFETY_LIMITS.items():
        safety_limits[key] = max(1, int(safety_limits.get(key) or default))
    safety_limits["max_runtime_generations"] = min(
        safety_limits["max_runtime_generations"],
        FULL_SCOPE_SAFETY_LIMITS["max_runtime_generations"],
    )
    safety_limits["max_gz_fec_entries"] = min(
        safety_limits["max_gz_fec_entries"],
        FULL_SCOPE_SAFETY_LIMITS["max_gz_fec_entries"],
    )
    safety_limits["max_parameter_generations"] = min(
        safety_limits["max_parameter_generations"],
        FULL_SCOPE_SAFETY_LIMITS["max_parameter_generations"],
    )
    safety_limits["max_parameter_entries"] = min(
        safety_limits["max_parameter_entries"],
        FULL_SCOPE_SAFETY_LIMITS["max_parameter_entries"],
    )
    safety_limits["max_scope_entries"] = min(
        safety_limits["max_scope_entries"],
        FULL_SCOPE_SAFETY_LIMITS["max_scope_entries"],
    )
    normalized["safety_limits"] = safety_limits
    requested_scope = int(normalized.get("max_scope_entries") or 1)
    max_scope_limit = (
        CANARY_MAX_SCOPE_ENTRIES
        if mode == SELECTOR_MODE_CANARY
        else safety_limits["max_scope_entries"]
    )
    normalized["max_scope_entries"] = max(1, min(max_scope_limit, requested_scope))
    return normalized


def _direction_sort_key(direction: str) -> int:
    order = {
        "g_to_z_access_ingress": 0,
        "g_to_z": 1,
        "z_to_g_nat_ingress": 2,
        "z_to_g": 3,
    }
    return order.get(str(direction), 99)


def _iter_bpf_owned_readiness(runtime_report: dict[str, Any]) -> list[dict[str, Any]]:
    readiness_records: list[dict[str, Any]] = []
    for iteration in runtime_report.get("iterations", []) or []:
        node_local = iteration.get("node_local_execution") or {}
        candidate_groups = [
            (node_local.get("backend") or {}).get("bpf_owned_readiness") or {},
            (
                (node_local.get("timing") or {})
                .get("backend_apply_contract", {})
                .get("bpf_owned_readiness")
                or {}
            ),
        ]
        for readiness in candidate_groups:
            if not isinstance(readiness, dict):
                continue
            if readiness.get("ready") is not True and readiness.get("ok") is not True:
                continue
            if readiness.get("fallback_to_linux") is True:
                continue
            packet_entries = readiness.get("resolved_packet_route_entries") or []
            mpls_entries = readiness.get("resolved_mpls_flow_entries") or []
            if packet_entries or mpls_entries:
                readiness_records.append(dict(readiness))
    return readiness_records


def _bpf_owned_entry_matches_selector(
    entry: dict[str, Any],
    *,
    selector: dict[str, Any],
    ping_keys: set[tuple[str, str]],
) -> bool:
    ut_id = str(entry.get("ut_id") or "")
    target_ip = str(entry.get("target_ip") or "")
    gateway_id = str(entry.get("selected_gateway_id") or "")
    if selector["ut_ids"] and ut_id not in selector["ut_ids"]:
        return False
    if selector["gw_ids"] and gateway_id not in selector["gw_ids"]:
        return False
    if ping_keys:
        flow_id = str(entry.get("flow_id") or "")
        if str(entry.get("direction") or "").startswith("z_to_g"):
            return any(
                ping_ut == ut_id and (not flow_id or flow_id.endswith(f":{ping_ut}"))
                for ping_ut, _target in ping_keys
            )
        return (ut_id, target_ip) in ping_keys
    return True


def _normalize_bpf_owned_scope_entry(entry: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(entry)
    ingress_interface = str(
        normalized.get("ingress_interface")
        or normalized.get("ut_host_interface")
        or ""
    )
    egress_interface = str(
        normalized.get("egress_interface")
        or normalized.get("out_interface")
        or normalized.get("satellite_host_interface")
        or ""
    )
    normalized["ingress_interface"] = ingress_interface
    normalized["egress_interface"] = egress_interface
    normalized.setdefault("ut_host_interface", ingress_interface)
    normalized.setdefault("satellite_host_interface", egress_interface)
    if normalized.get("satellite_ifindex") is None:
        normalized["satellite_ifindex"] = normalized.get("egress_ifindex")
    normalized.setdefault("runtime_generation", 0)
    normalized.setdefault("parameter_generation", 0)
    normalized.setdefault("parameter_loss_ppm", 0)
    normalized.setdefault(
        "parameter_mark",
        stable_u32(
            ":".join(
                [
                    str(normalized.get("flow_id") or normalized.get("ut_id") or ""),
                    str(normalized.get("direction") or ""),
                    str(normalized.get("slot_name") or ""),
                    str(normalized.get("target_ip") or ""),
                ]
            )
        )
        & 0xFFFF,
    )
    return normalized


def _bpf_owned_readiness_scope_entries(
    readiness: dict[str, Any],
) -> tuple[list[dict[str, Any]], str]:
    packet_entries = [
        dict(entry)
        for entry in readiness.get("resolved_packet_route_entries") or []
        if isinstance(entry, dict)
    ]
    if packet_entries:
        return packet_entries, "packet_route"
    mpls_entries = [
        {
            **dict(entry),
            "scope_evidence": "mpls_lfib",
        }
        for entry in readiness.get("resolved_mpls_flow_entries") or []
        if isinstance(entry, dict)
        and entry.get("map_type") in {"ingress_classifier_map", "egress_handoff_map"}
    ]
    return mpls_entries, "mpls_lfib"


def _select_bpf_owned_full_scope_entries(
    *,
    runtime_report: dict[str, Any],
    selector: dict[str, Any],
) -> dict[str, Any] | None:
    if selector["mode"] != SELECTOR_MODE_FULL_ELIGIBLE:
        return None
    ping_keys = {
        (str(ping.get("ut_id") or ""), str(ping.get("ping_target_ip") or ""))
        for ping in successful_pings(runtime_report)
        if ping.get("ut_id") and ping.get("ping_target_ip")
    }
    readiness_records = _iter_bpf_owned_readiness(runtime_report)
    if not readiness_records:
        return None
    readiness = readiness_records[-1]
    readiness_entries, readiness_entry_source = _bpf_owned_readiness_scope_entries(
        readiness
    )
    raw_entries = [
        dict(entry)
        for entry in readiness_entries
        if isinstance(entry, dict)
        and entry.get("target_ip")
        and _bpf_owned_entry_matches_selector(
            entry,
            selector=selector,
            ping_keys=ping_keys,
        )
    ]
    raw_entries.sort(
        key=lambda entry: (
            _direction_sort_key(str(entry.get("direction") or "")),
            str(entry.get("ut_id") or ""),
            str(entry.get("target_ip") or ""),
            str(entry.get("flow_id") or ""),
        )
    )
    selected_entries: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for entry in raw_entries:
        normalized = _normalize_bpf_owned_scope_entry(entry)
        key = (
            str(normalized.get("direction") or ""),
            str(normalized.get("ingress_interface") or ""),
            str(normalized.get("target_ip") or ""),
            str(normalized.get("flow_id") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        selected_entries.append(normalized)
        if len(selected_entries) >= int(selector["max_scope_entries"]):
            break
    if not selected_entries:
        return None
    attached_interfaces = sorted(
        {
            *[
                str(interface_name)
                for interface_name in readiness.get("attached_interfaces") or []
                if str(interface_name)
            ],
            *[
                str(entry.get("ingress_interface") or "")
                for entry in selected_entries
                if str(entry.get("ingress_interface") or "")
            ],
            *[
                str(entry.get("egress_interface") or "")
                for entry in selected_entries
                if str(entry.get("egress_interface") or "")
            ],
        }
    )
    return {
        "entries": selected_entries,
        "attached_interfaces": attached_interfaces,
        "target_ips": sorted(
            {
                str(entry.get("target_ip"))
                for entry in selected_entries
                if str(entry.get("direction") or "")
                in {"g_to_z_access_ingress", "g_to_z"}
                and str(entry.get("target_ip") or "")
            }
        ),
        "readiness": readiness,
        "readiness_entry_source": readiness_entry_source,
    }


def diagnose_experimental_scope_selection(
    *,
    runtime_report: dict[str, Any],
    selector: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_selector = normalize_selector(selector)
    pings = successful_pings(runtime_report)
    ping_keys = {
        (str(ping.get("ut_id") or ""), str(ping.get("ping_target_ip") or ""))
        for ping in pings
        if ping.get("ut_id") and ping.get("ping_target_ip")
    }
    readiness_records = _iter_bpf_owned_readiness(runtime_report)
    rejected: list[dict[str, Any]] = []
    candidate_entry_count = 0
    selector_matched_entry_count = 0
    candidate_packet_route_entry_count = 0
    candidate_mpls_flow_entry_count = 0
    for readiness_index, readiness in enumerate(readiness_records):
        entries, entry_source = _bpf_owned_readiness_scope_entries(readiness)
        candidate_packet_route_entry_count += len(
            [
                entry
                for entry in readiness.get("resolved_packet_route_entries") or []
                if isinstance(entry, dict)
            ]
        )
        candidate_mpls_flow_entry_count += len(
            [
                entry
                for entry in readiness.get("resolved_mpls_flow_entries") or []
                if isinstance(entry, dict)
            ]
        )
        candidate_entry_count += len(entries)
        matched = [
            entry
            for entry in entries
            if entry.get("target_ip")
            and _bpf_owned_entry_matches_selector(
                entry,
                selector=normalized_selector,
                ping_keys=ping_keys,
            )
        ]
        selector_matched_entry_count += len(matched)
        if not entries:
            rejected.append(
                {
                    "readiness_index": readiness_index,
                    "reason": "readiness_has_no_bpf_owned_scope_entries",
                }
            )
        elif not matched:
            rejected.append(
                {
                    "readiness_index": readiness_index,
                    "reason": (
                        "no_entries_matched_selector_or_successful_ping_keys"
                    ),
                    "entry_count": len(entries),
                    "entry_source": entry_source,
                    "successful_ping_key_count": len(ping_keys),
                }
            )
    if not readiness_records:
        rejected.append(
            {
                "reason": "no_bpf_owned_readiness_records_with_scope_entries",
                "hint": (
                    "runtime report must preserve node_local_execution backend or "
                    "backend_apply_contract bpf_owned_readiness packet-route or "
                    "MPLS LFIB evidence"
                ),
            }
        )
    if not pings:
        rejected.append({"reason": "no_successful_runtime_ping_evidence"})
    return {
        "requested_scope": normalized_selector,
        "candidate_scope": {
            "successful_ping_count": len(pings),
            "successful_ping_key_count": len(ping_keys),
            "bpf_owned_readiness_record_count": len(readiness_records),
            "candidate_entry_count": candidate_entry_count,
            "candidate_packet_route_entry_count": candidate_packet_route_entry_count,
            "candidate_mpls_flow_entry_count": candidate_mpls_flow_entry_count,
            "selector_matched_entry_count": selector_matched_entry_count,
        },
        "rejected_scope_entries": rejected,
    }


def select_experimental_scope(
    *,
    topology_path: Path,
    link_state_path: Path,
    runtime_report: dict[str, Any],
    compiled: dict[str, Any],
    selector: dict[str, Any] | None = None,
) -> dict[str, Any]:
    selector = normalize_selector(selector)
    topology = read_json(topology_path)
    link_state = read_json(link_state_path)
    active_runtime_generation = int(compiled["bpf_maps"]["runtime"]["active_generation"]["value"])
    if selector["generation_ids"]:
        active_runtime_generation = int(selector["generation_ids"][0])
    active_parameter_generation = int(
        compiled["bpf_maps"]["parameter"]["parameter_generation"]["value"]
    )
    if selector["parameter_generation_ids"]:
        active_parameter_generation = int(selector["parameter_generation_ids"][0])

    if selector["mode"] == SELECTOR_MODE_FULL_ELIGIBLE:
        bpf_owned_scope = _select_bpf_owned_full_scope_entries(
            runtime_report=runtime_report,
            selector=selector,
        )
        if bpf_owned_scope is not None:
            compiled_summary = compiled_full_scope_summary(compiled)
            readiness = dict(bpf_owned_scope.get("readiness") or {})
            return {
                "scope": SELECTOR_MODE_FULL_ELIGIBLE,
                "selector": selector,
                "max_scope_entries": selector["max_scope_entries"],
                "entries": list(bpf_owned_scope["entries"]),
                "attached_interfaces": list(bpf_owned_scope["attached_interfaces"]),
                "target_ips": list(bpf_owned_scope["target_ips"]),
                "active_runtime_generation": active_runtime_generation,
                "active_parameter_generation": active_parameter_generation,
                "compiled_full_scope": compiled_summary,
                "semantic_intent_sources": {
                    "runtime": "all compiled G/Z FEC generation map entries",
                    "parameter": "all compiled ParameterEpoch active/loss/drop/counter entries",
                    "scope": (
                        "full eligible strict BPF-owned packet-route/MPLS LFIB scope from "
                        "runtime bpf_owned_readiness evidence"
                    ),
                },
                "canonical_fallback_scope": (
                    "non-IP traffic and traffic outside the current BPF dataplane "
                    "lookup model remain TC_ACT_OK on canonical Linux/direct runtime; "
                    "delay/rate/bandwidth stay in qdisc/netem"
                ),
                "bpf_owned_readiness_source": {
                    "provider": readiness.get("provider"),
                    "generation_id": readiness.get("generation_id"),
                    "packet_route_entry_count": readiness.get(
                        "packet_route_entry_count"
                    ),
                    "resolved_packet_route_entry_count": len(
                        readiness.get("resolved_packet_route_entries") or []
                    ),
                    "resolved_mpls_flow_entry_count": len(
                        readiness.get("resolved_mpls_flow_entries") or []
                    ),
                    "scope_entry_source": bpf_owned_scope.get(
                        "readiness_entry_source"
                    ),
                    "fallback_to_linux": readiness.get("fallback_to_linux"),
                },
            }

    selected_entries: list[dict[str, Any]] = []
    seen_entry_keys: set[tuple[str, str]] = set()
    for ping in successful_pings(runtime_report):
        entry = build_scope_entry(
            ping=ping,
            topology=topology,
            link_state=link_state,
            compiled=compiled,
            active_runtime_generation=active_runtime_generation,
            active_parameter_generation=active_parameter_generation,
            selector=selector,
        )
        if entry is None:
            continue
        entry_key = (str(entry["ut_id"]), str(entry["target_ip"]))
        if entry_key in seen_entry_keys:
            continue
        seen_entry_keys.add(entry_key)
        selected_entries.append(entry)
        if len(selected_entries) >= int(selector["max_scope_entries"]):
            break
    if not selected_entries:
        diagnostics = diagnose_experimental_scope_selection(
            runtime_report=runtime_report,
            selector=selector,
        )
        exc = RuntimeError("could not select an intent-matched BPF backend scope")
        exc.skyseed_scope_selection_diagnostics = diagnostics  # type: ignore[attr-defined]
        raise exc

    if selector["mode"] == SELECTOR_MODE_FULL_ELIGIBLE:
        compiled_summary = compiled_full_scope_summary(compiled)
        return {
            "scope": SELECTOR_MODE_FULL_ELIGIBLE,
            "selector": selector,
            "max_scope_entries": selector["max_scope_entries"],
            "entries": selected_entries,
            "attached_interfaces": sorted(
                {entry["ut_host_interface"] for entry in selected_entries}
            ),
            "target_ips": [entry["target_ip"] for entry in selected_entries],
            "active_runtime_generation": active_runtime_generation,
            "active_parameter_generation": active_parameter_generation,
            "compiled_full_scope": compiled_summary,
            "semantic_intent_sources": {
                "runtime": "all compiled G/Z FEC generation map entries",
                "parameter": "all compiled ParameterEpoch active/loss/drop/counter entries",
                "scope": "full eligible compiled maps plus live ping entries for dataplane sanity",
            },
            "canonical_fallback_scope": (
                "non-IP traffic and traffic outside the current BPF dataplane "
                "lookup model remain TC_ACT_OK on canonical Linux/direct runtime; "
                "delay/rate/bandwidth stay in qdisc/netem"
            ),
        }

    return {
        "scope": SELECTOR_MODE_CANARY,
        "selector": selector,
        "max_scope_entries": selector["max_scope_entries"],
        "entries": selected_entries,
        "attached_interfaces": sorted({entry["ut_host_interface"] for entry in selected_entries}),
        "target_ips": [entry["target_ip"] for entry in selected_entries],
        "active_runtime_generation": active_runtime_generation,
        "active_parameter_generation": active_parameter_generation,
        "semantic_intent_sources": {
            "runtime": "G/Z FEC generation map + RuntimeEpoch metadata",
            "parameter": "ParameterEpoch execution details parameter map",
            "scope": "successful runtime ping constrained by selector and FEC intent",
        },
        "canonical_fallback_scope": (
            "packets outside selected intent-derived target IPs remain TC_ACT_OK "
            "on canonical Linux/direct runtime"
        ),
    }


def compiled_full_scope_summary(compiled: dict[str, Any]) -> dict[str, Any]:
    runtime_status = list(compiled["bpf_maps"]["runtime"].get("generation_status", []))
    fec_entries = list(compiled["bpf_maps"]["runtime"].get("fec_forwarding", []))
    parameter_status = list(compiled["bpf_maps"]["parameter"].get("param_status", []))
    parameter_entries = list(compiled["bpf_maps"]["parameter"].get("parameter_values", []))
    return {
        "runtime_generation_count": len(runtime_status),
        "gz_fec_entry_count": len(fec_entries),
        "parameter_generation_count": len(parameter_status),
        "parameter_link_count": len(
            {str(entry.get("link_id")) for entry in parameter_entries if entry.get("link_id")}
        ),
        "parameter_entry_count": len(parameter_entries),
    }


def build_scope_entry(
    *,
    ping: dict[str, Any],
    topology: dict[str, Any],
    link_state: dict[str, Any],
    compiled: dict[str, Any],
    active_runtime_generation: int,
    active_parameter_generation: int,
    selector: dict[str, Any],
) -> dict[str, Any] | None:
    ut_id = str(ping["ut_id"])
    if selector["ut_ids"] and ut_id not in selector["ut_ids"]:
        return None
    if selector["gw_ids"] and str(ping.get("selected_gateway_id") or "") not in selector["gw_ids"]:
        return None
    serving_satellite_id = str(ping["serving_satellite_id"])
    slot_name = f"{ut_id}-access-slot0"
    slot = slot_by_name(topology, slot_name)
    state = slot_state_by_name(link_state, slot_name)
    binding = dict(state.get("binding") or {})
    if state.get("occupied") and str(binding.get("satellite_id")) != serving_satellite_id:
        serving_satellite_id = str(binding.get("satellite_id") or serving_satellite_id)
    fec_match = matching_fec_entry(
        compiled=compiled,
        generation_id=active_runtime_generation,
        ping=ping,
        selector=selector,
    )
    if fec_match is None:
        return None
    ut_endpoint = dict(slot["preconnected_endpoint"])
    sat_template = dict(slot["dynamic_endpoint_template"])
    satellite_endpoint = {
        **sat_template,
        "node_id": serving_satellite_id,
    }
    ut_host_interface = host_interface_name(
        slot_name=slot_name,
        endpoint=ut_endpoint,
        role="preconnected",
    )
    satellite_host_interface = host_interface_name(
        slot_name=slot_name,
        endpoint=satellite_endpoint,
        role=f"candidate:{serving_satellite_id}",
    )
    return {
        "scope": "semantic_intent_path",
        "ut_id": ut_id,
        "flow_id": str(ping.get("flow_id") or ut_id),
        "target_ip": str(ping["ping_target_ip"]),
        "target_host_node_id": str(ping.get("target_host_node_id") or ""),
        "serving_satellite_id": serving_satellite_id,
        "selected_gateway_id": str(ping.get("selected_gateway_id") or ""),
        "slot_name": slot_name,
        "slot_type": "ut_access",
        "ut_host_interface": ut_host_interface,
        "satellite_host_interface": satellite_host_interface,
        "ingress_ifindex": interface_ifindex(ut_host_interface),
        "egress_ifindex": interface_ifindex(satellite_host_interface),
        "satellite_ifindex": interface_ifindex(satellite_host_interface),
        "dst_mac": stable_local_mac(
            f"{slot_name}:{satellite_endpoint['interface_name']}:{satellite_endpoint['ip_address']}"
        ),
        "src_mac": stable_local_mac(
            f"{slot_name}:{ut_endpoint['interface_name']}:{ut_endpoint['ip_address']}"
        ),
        "runtime_generation": active_runtime_generation,
        "parameter_generation": active_parameter_generation,
        "fec": {
            "fec_id": fec_match.get("fec_id"),
            "fec_kind": fec_match.get("fec_kind"),
            "zone_id": fec_match.get("zone_id"),
            "selected_gateway_id": fec_match.get("selected_gateway_id"),
            "epoch_id": fec_match.get("epoch_id"),
            "source": fec_match.get("source"),
        },
        "parameter_loss_ppm": parameter_loss_ppm(
            dict(state.get("runtime_parameters") or {}).get("loss_percent")
        ),
        "parameter_mark": stable_u32(f"{slot_name}:{ping['ping_target_ip']}") & 0xFFFF,
        "qdisc_parameters_left_to_canonical": {
            "delay_ms": dict(state.get("runtime_parameters") or {}).get("delay_ms"),
            "bandwidth_mbps": dict(state.get("runtime_parameters") or {}).get(
                "bandwidth_mbps"
            ),
        },
    }


def select_canary_scope(
    *,
    topology_path: Path,
    link_state_path: Path,
    runtime_report: dict[str, Any],
) -> dict[str, Any]:
    """Legacy test helper selecting a single path from runtime ping metadata."""

    fake_compiled = {
        "bpf_maps": {
            "runtime": {
                "active_generation": {"value": 0},
                "fec_forwarding": [
                    {
                        "generation_id": 0,
                        "active": True,
                        "fec_id": "__runtime_ping__",
                        "fec_kind": "G",
                        "selected_gateway_id": first_successful_ping(runtime_report).get(
                            "selected_gateway_id"
                        ),
                    }
                ],
            },
            "parameter": {"parameter_generation": {"value": 0}},
        }
    }
    scope = select_experimental_scope(
        topology_path=topology_path,
        link_state_path=link_state_path,
        runtime_report=runtime_report,
        compiled=fake_compiled,
        selector={"max_scope_entries": 1},
    )
    legacy = dict(scope["entries"][0])
    legacy["scope"] = "single_ut_access_path"
    legacy["canonical_fallback_scope"] = scope["canonical_fallback_scope"]
    return legacy


def first_successful_ping(runtime_report: dict[str, Any]) -> dict[str, Any]:
    pings = successful_pings(runtime_report)
    if pings:
        return pings[0]
    raise RuntimeError("could not select a successful UT ping for BPF canary")


def enrich_runtime_report_from_run_dir(
    run_dir: Path,
    runtime_report: dict[str, Any],
) -> dict[str, Any]:
    """Add compact RuntimeEpoch detail records when live session data is sparse."""

    if successful_pings(runtime_report):
        return runtime_report
    details_path = run_dir / "epoch_execution_details.jsonl"
    if not details_path.exists():
        return runtime_report
    enriched = dict(runtime_report)
    iterations = list(enriched.get("iterations", []) or [])
    with details_path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            iterations.append(
                {
                    "node_local_execution": record.get("node_local_execution") or {},
                    "post_transition_connectivity": record.get(
                        "post_transition_connectivity"
                    )
                    or {},
                    "pre_transition_connectivity": record.get(
                        "pre_transition_connectivity"
                    )
                    or {},
                    "post_transition_paths": record.get("post_transition_paths") or [],
                    "pre_transition_paths": record.get("pre_transition_paths") or [],
                }
            )
    enriched["iterations"] = iterations
    return enriched


def successful_pings(runtime_report: dict[str, Any]) -> list[dict[str, Any]]:
    pings: list[dict[str, Any]] = []
    for iteration in runtime_report.get("iterations", []) or []:
        execution = iteration.get("node_local_execution") or {}
        candidate_groups = [
            execution.get("ping_results", []) or [],
            (iteration.get("post_transition_connectivity") or {}).get("results", [])
            or [],
            (iteration.get("post_transition_connectivity") or {}).get("flow_results", [])
            or [],
            (iteration.get("pre_transition_connectivity") or {}).get("flow_results", [])
            or [],
        ]
        for ping in [item for group in candidate_groups for item in group]:
            if (
                isinstance(ping, dict)
                and ping.get("success") is True
                and ping.get("ut_id")
                and ping.get("ping_target_ip")
                and ping.get("serving_satellite_id")
            ):
                pings.append(dict(ping))
    initial = runtime_report.get("initial_runtime_readiness") or {}
    for ping in initial.get("flow_results", []) or []:
        if (
            isinstance(ping, dict)
            and ping.get("success") is True
            and ping.get("ut_id")
            and ping.get("ping_target_ip")
            and ping.get("serving_satellite_id")
        ):
            pings.append(dict(ping))
    return pings


def matching_fec_entry(
    *,
    compiled: dict[str, Any],
    generation_id: int,
    ping: dict[str, Any],
    selector: dict[str, Any],
) -> dict[str, Any] | None:
    entries = [
        entry
        for entry in compiled["bpf_maps"]["runtime"].get("fec_forwarding", [])
        if int(entry.get("generation_id", -1)) == int(generation_id)
        and entry.get("active") is True
    ]
    fec_ids = set(selector.get("fec_ids") or [])
    zone_ids = set(selector.get("zone_ids") or [])
    gw_ids = set(selector.get("gw_ids") or [])
    ping_gateway = str(ping.get("selected_gateway_id") or "")

    def matches(entry: dict[str, Any]) -> bool:
        if fec_ids and str(entry.get("fec_id") or "") not in fec_ids:
            return False
        if zone_ids and str(entry.get("zone_id") or "") not in zone_ids:
            return False
        if gw_ids and str(entry.get("selected_gateway_id") or "") not in gw_ids:
            return False
        if fec_ids or zone_ids or gw_ids:
            return True
        return not ping_gateway or str(entry.get("selected_gateway_id") or "") == ping_gateway

    return next((dict(entry) for entry in entries if matches(entry)), None)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def slot_by_name(topology: dict[str, Any], slot_name: str) -> dict[str, Any]:
    for group in dict(topology.get("slot_inventory", {})).values():
        for slot in group or []:
            if str(slot.get("slot_name")) == slot_name:
                return dict(slot)
    raise RuntimeError(f"missing slot inventory for canary slot {slot_name}")


def slot_state_by_name(link_state: dict[str, Any], slot_name: str) -> dict[str, Any]:
    for slot in link_state.get("slots", []) or []:
        if str(slot.get("slot_name")) == slot_name:
            return dict(slot)
    raise RuntimeError(f"missing link-state slot for canary slot {slot_name}")


def stable_u32(value: str) -> int:
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], byteorder="little", signed=False)


def stable_local_mac(seed: str) -> str:
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    octets = [0x02, digest[0], digest[1], digest[2], digest[3], digest[4]]
    return ":".join(f"{octet:02x}" for octet in octets)


def mac_bytes(value: Any) -> bytes:
    if not value:
        return b"\x00" * 6
    parts = str(value).strip().split(":")
    if len(parts) != 6:
        return b"\x00" * 6
    try:
        return bytes(int(part, 16) for part in parts)
    except ValueError:
        return b"\x00" * 6


def fingerprint(payload: dict[str, Any]) -> str:
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()[:16]


def short_name(prefix: str, seed: str) -> str:
    return f"{prefix}{fingerprint({'seed': seed})[:11]}"[:15]


def host_interface_name(*, slot_name: str, endpoint: dict[str, Any], role: str) -> str:
    seed = f"{slot_name}:{role}:{endpoint.get('node_id')}:{endpoint.get('interface_name')}"
    return short_name("vhs", seed)


def interface_ref(interface_name: str) -> dict[str, str]:
    raw = str(interface_name)
    if raw.startswith("netns:"):
        _prefix, node_id, netns_interface = raw.split(":", 2)
        return {
            "raw": raw,
            "netns": str(node_id),
            "interface_name": str(netns_interface),
            "interface_ref": raw,
        }
    return {
        "raw": raw,
        "netns": "host",
        "interface_name": raw,
        "interface_ref": raw,
    }


def container_pid(node_id: str) -> int:
    node_key = str(node_id)
    cached = _CONTAINER_PID_CACHE.get(node_key)
    if cached is not None:
        return cached
    if _CONTAINER_PID_CACHE_COMPLETE:
        raise RuntimeError(f"frozen container pid missing for {node_id}")
    result = run_command(
        ["docker", "inspect", "-f", "{{.State.Pid}}", node_key],
        timeout=4.0,
    )
    if not command_ok(result):
        raise RuntimeError(f"unable to resolve container pid for {node_id}: {result['stderr']}")
    pid = int(str(result.get("stdout") or "0").strip())
    _CONTAINER_PID_CACHE[node_key] = pid
    return pid


def clear_container_pid_cache() -> None:
    global _CONTAINER_PID_CACHE_COMPLETE
    _CONTAINER_PID_CACHE.clear()
    _CONTAINER_PID_CACHE_COMPLETE = False


def container_pid_cache_snapshot() -> dict[str, int]:
    return dict(_CONTAINER_PID_CACHE)


def consume_frozen_container_pid_cache(cache: dict[str, int]) -> None:
    global _CONTAINER_PID_CACHE_COMPLETE
    normalized = {
        str(node_id): int(pid)
        for node_id, pid in dict(cache or {}).items()
        if str(node_id) and int(pid) > 0
    }
    if not normalized or len(normalized) != len(dict(cache or {})):
        raise RuntimeError("frozen container pid cache invalid")
    _CONTAINER_PID_CACHE.clear()
    _CONTAINER_PID_CACHE.update(normalized)
    _CONTAINER_PID_CACHE_COMPLETE = True


def interface_command(interface_name: str, args: Sequence[str]) -> list[str]:
    ref = interface_ref(interface_name)
    command = [str(part) for part in args]
    if ref["netns"] == "host":
        return command
    return [
        "nsenter",
        "-t",
        str(container_pid(ref["netns"])),
        "-n",
        "--",
        *command,
    ]


def run_interface_command(
    interface_name: str,
    args: Sequence[str],
    *,
    timeout: float,
    privileged: bool = True,
) -> dict[str, Any]:
    ref = interface_ref(interface_name)
    command = [
        str(ref["interface_name"]) if str(part) == "{iface}" else str(part)
        for part in args
    ]
    result = run_command(
        interface_command(interface_name, command),
        timeout=timeout,
        privileged=privileged,
    )
    result["interface_ref"] = ref
    return result


def run_command(
    args: Sequence[str],
    *,
    timeout: float = 10.0,
    privileged: bool = False,
) -> dict[str, Any]:
    command = [str(part) for part in args]
    if command:
        command[0] = EXACT_TOOL_PATHS.get(command[0], command[0])
        if command[0] == EXACT_TOOL_PATHS["nsenter"] and "--" in command:
            nested_index = command.index("--") + 1
            if nested_index < len(command):
                command[nested_index] = EXACT_TOOL_PATHS.get(
                    command[nested_index], command[nested_index]
                )
    if privileged and os.geteuid() != 0:
        command = [EXACT_TOOL_PATHS["sudo"], "-n", *command]
    try:
        completed = subprocess.run(
            command,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
        return {
            "args": command,
            "returncode": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
        }
    except FileNotFoundError as exc:
        return {"args": command, "returncode": 127, "stdout": "", "stderr": str(exc)}
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return {
            "args": command,
            "returncode": 124,
            "stdout": stdout.strip(),
            "stderr": stderr.strip(),
            "timeout": True,
        }


def command_ok(result: dict[str, Any]) -> bool:
    return int(result.get("returncode", 1)) == 0


def is_tc_qdisc_delete(result: dict[str, Any]) -> bool:
    args = [str(part) for part in result.get("args", [])]
    return "tc" in args and "qdisc" in args and "del" in args


def collect_environment() -> dict[str, Any]:
    bpftool_path = resolve_bpftool()
    return {
        "tools": {
            name: path
            for name in ["clang", "tc", "ip", "docker", "sudo"]
            if os.path.isfile(path := EXACT_TOOL_PATHS[name])
            and os.access(path, os.X_OK)
        }
        | {
            "bpftool": bpftool_path,
        },
        "kernel": {
            "bpffs_exists": Path("/sys/fs/bpf").exists(),
            "tc_globals_exists": privileged_path_exists(PIN_BASE),
        },
        "sudo": {
            "uid": os.getuid(),
            "euid": os.geteuid(),
            "noninteractive_sudo": command_ok(
                run_command(
                    [EXACT_TOOL_PATHS["sudo"], "-n", EXACT_TOOL_PATHS["true"]],
                    timeout=4.0,
                )
            ),
        },
    }


def attach_blockers(environment: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    for tool in ["clang", "tc", "ip", "docker", "bpftool"]:
        if not environment["tools"].get(tool):
            blockers.append(f"missing_{tool}")
    if not environment["kernel"].get("bpffs_exists"):
        blockers.append("missing_bpffs")
    if os.geteuid() != 0 and not environment["sudo"].get("noninteractive_sudo"):
        blockers.append("missing_root_or_passwordless_sudo")
    return blockers


def resolve_bpftool() -> str | None:
    configured = str(os.environ.get("SKYSEED_BPFTOOL_PATH") or "").strip()
    if not configured:
        return None
    path = Path(configured)
    if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
        return None
    result = run_command([str(path), "version"], timeout=5.0)
    return str(path) if command_ok(result) else None


def bpf_map_capacities_for_scope(
    *,
    compiled: dict[str, Any],
    scope: dict[str, Any],
) -> dict[str, int]:
    if scope.get("scope") != SELECTOR_MODE_FULL_ELIGIBLE:
        return dict(DEFAULT_CANARY_CAPACITIES)
    limits = dict((scope.get("selector") or {}).get("safety_limits") or FULL_SCOPE_SAFETY_LIMITS)
    summary = dict(scope.get("compiled_full_scope") or compiled_full_scope_summary(compiled))
    selected_probe_entries = len(
        {
            (
                str(entry.get("target_ip")),
                str(entry.get("ingress_ifindex") or entry.get("ut_host_interface") or ""),
            )
            for entry in list(scope.get("entries") or [])
            if entry.get("target_ip")
        }
    )
    runtime_generations = min(
        int(limits.get("max_runtime_generations") or FULL_SCOPE_SAFETY_LIMITS["max_runtime_generations"]),
        max(1, int(summary.get("runtime_generation_count") or 0)),
    )
    parameter_generations = min(
        int(limits.get("max_parameter_generations") or FULL_SCOPE_SAFETY_LIMITS["max_parameter_generations"]),
        max(1, int(summary.get("parameter_generation_count") or 0)),
    )
    gz_fec_entries = min(
        int(limits.get("max_gz_fec_entries") or FULL_SCOPE_SAFETY_LIMITS["max_gz_fec_entries"]),
        max(1, int(summary.get("gz_fec_entry_count") or 0) + selected_probe_entries),
    )
    parameter_entries = min(
        int(limits.get("max_parameter_entries") or FULL_SCOPE_SAFETY_LIMITS["max_parameter_entries"]),
        max(1, int(summary.get("parameter_entry_count") or 0) + selected_probe_entries),
    )
    return {
        "runtime_generations": runtime_generations,
        "gz_fec_entries": gz_fec_entries,
        "parameter_generations": parameter_generations,
        "parameter_entries": parameter_entries,
        "interface_counter_entries": max(
            256,
            int(DEFAULT_CANARY_CAPACITIES["interface_counter_entries"]),
        ),
    }


def compile_bpf(
    source: Path,
    object_path: Path,
    *,
    capacities: dict[str, int] | None = None,
) -> dict[str, Any]:
    include_args: list[str] = []
    arch_include = Path("/usr/include/x86_64-linux-gnu")
    if arch_include.exists():
        include_args = ["-I", str(arch_include)]
    capacities = dict(capacities or DEFAULT_CANARY_CAPACITIES)
    define_args = [
        f"-DSKYSEED_BPF_MAX_RUNTIME_GENERATIONS={int(capacities['runtime_generations'])}",
        f"-DSKYSEED_BPF_MAX_FEC_ENTRIES={int(capacities['gz_fec_entries'])}",
        f"-DSKYSEED_BPF_MAX_PARAMETER_GENERATIONS={int(capacities['parameter_generations'])}",
        f"-DSKYSEED_BPF_MAX_PARAMETER_ENTRIES={int(capacities['parameter_entries'])}",
        "-DSKYSEED_BPF_MAX_INTERFACE_COUNTER_ENTRIES="
        f"{int(capacities.get('interface_counter_entries') or DEFAULT_CANARY_CAPACITIES['interface_counter_entries'])}",
    ]
    return run_command(
        [
            "clang",
            "-O2",
            "-target",
            "bpf",
            "-D__TARGET_ARCH_x86",
            *define_args,
            *include_args,
            "-c",
            str(source),
            "-o",
            str(object_path),
        ],
        timeout=20.0,
    )


def attach_canary(interface_name: str, object_path: Path) -> list[dict[str, Any]]:
    return [
        run_interface_command(
            interface_name,
            ["tc", "qdisc", "del", "dev", "{iface}", "clsact"],
            timeout=4.0,
        ),
        run_interface_command(
            interface_name,
            ["tc", "qdisc", "add", "dev", "{iface}", "clsact"],
            timeout=8.0,
        ),
        run_interface_command(
            interface_name,
            [
                "tc",
                "filter",
                "add",
                "dev",
                "{iface}",
                "ingress",
                "bpf",
                "da",
                "obj",
                str(object_path),
                "sec",
                "classifier/canary",
            ],
            timeout=10.0,
        ),
        run_interface_command(
            interface_name,
            [
                "tc",
                "filter",
                "add",
                "dev",
                "{iface}",
                "egress",
                "bpf",
                "da",
                "obj",
                str(object_path),
                "sec",
                "classifier/canary",
            ],
            timeout=10.0,
        ),
    ]


def rollback_canary(
    *,
    attached_interfaces: Sequence[str],
    remove_maps: bool,
) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    for interface_name in sorted({str(name) for name in attached_interfaces if str(name)}):
        steps.append(
            run_interface_command(
                interface_name,
                ["tc", "qdisc", "del", "dev", "{iface}", "clsact"],
                timeout=4.0,
            )
        )
    if remove_maps:
        for map_name in CANARY_PINNED_MAPS:
            steps.append(
                run_command(
                    ["rm", "-f", str(PIN_BASE / map_name)],
                    timeout=4.0,
                    privileged=True,
                )
            )
    residue = residue_check(attached_interfaces)
    return {
        "steps": steps,
        "residue": residue,
        "rollback_completed": bool(residue["clean"]),
    }


def residue_check(attached_interfaces: Sequence[str]) -> dict[str, Any]:
    qdisc_hits: list[str] = []
    for interface_name in sorted({str(name) for name in attached_interfaces if str(name)}):
        qdisc = run_interface_command(
            interface_name,
            ["tc", "qdisc", "show", "dev", "{iface}"],
            timeout=4.0,
        )
        if "clsact" in (qdisc.get("stdout", "") + qdisc.get("stderr", "")):
            qdisc_hits.append(interface_name)
    pinned = {name: privileged_path_exists(PIN_BASE / name) for name in CANARY_PINNED_MAPS}
    return {
        "clsact_interface_hits": qdisc_hits,
        "pinned_maps": pinned,
        "clean": not qdisc_hits and not any(pinned.values()),
    }


def privileged_path_exists(path: Path) -> bool:
    return command_ok(run_command(["test", "-e", str(path)], timeout=4.0, privileged=True))


def interface_ifindex(interface_name: str) -> int:
    result = run_interface_command(
        interface_name,
        ["ip", "-o", "link", "show", "dev", "{iface}"],
        timeout=4.0,
    )
    if not command_ok(result):
        raise RuntimeError(f"missing canary interface {interface_name}: {result['stderr']}")
    prefix = result["stdout"].split(":", 1)[0].strip()
    return int(prefix)


def docker_ping(*, node_id: str, target_ip: str, count: int) -> dict[str, Any]:
    return run_command(
        ["docker", "exec", node_id, "ping", "-n", "-c", str(count), "-W", "2", target_ip],
        timeout=max(8.0, count * 4.0),
    )


def load_canary_maps(
    *,
    compiled: dict[str, Any],
    scope: dict[str, Any],
    active_runtime_generation: int,
    active_parameter_generation: int,
) -> dict[str, Any]:
    updates: list[dict[str, Any]] = []
    full_scope = scope.get("scope") == SELECTOR_MODE_FULL_ELIGIBLE
    capacities = bpf_map_capacities_for_scope(compiled=compiled, scope=scope)
    runtime_status = list(compiled["bpf_maps"]["runtime"]["generation_status"])
    parameter_status = list(compiled["bpf_maps"]["parameter"]["param_status"])
    runtime_status_to_load = runtime_status[: int(capacities["runtime_generations"])]
    parameter_status_to_load = parameter_status[: int(capacities["parameter_generations"])]
    fec_entries_to_load = (
        list(compiled["bpf_maps"]["runtime"].get("fec_forwarding", []))
        if full_scope
        else []
    )
    parameter_entries_to_load = (
        list(compiled["bpf_maps"]["parameter"].get("parameter_values", []))
        if full_scope
        else []
    )
    selected_probe_entries = len(
        {
            str(entry.get("target_ip"))
            for entry in list(scope.get("entries") or [])
            if entry.get("target_ip")
        }
    )
    capacity_errors: list[str] = []
    if len(runtime_status_to_load) < len(runtime_status):
        capacity_errors.append("runtime_generation_capacity_exceeded")
    if len(parameter_status_to_load) < len(parameter_status):
        capacity_errors.append("parameter_generation_capacity_exceeded")
    if len(fec_entries_to_load) + selected_probe_entries > int(capacities["gz_fec_entries"]):
        capacity_errors.append("gz_fec_capacity_exceeded")
    if (
        len(parameter_entries_to_load) + selected_probe_entries
        > int(capacities["parameter_entries"])
    ):
        capacity_errors.append("parameter_entry_capacity_exceeded")
    active_generations_before = {
        "runtime": int(active_runtime_generation),
        "parameter": int(active_parameter_generation),
    }
    updates.append(update_u32_map("active_generation", 0, active_runtime_generation))
    for generation in runtime_status_to_load:
        updates.append(
            update_u32_map(
                "generation_status",
                int(generation["generation_id"]),
                1 if generation.get("installed") else 0,
            )
        )
    updates.append(update_u32_map("parameter_generation", 0, active_parameter_generation))
    if parameter_status_to_load:
        for generation in parameter_status_to_load:
            updates.append(
                update_u32_map(
                    "param_status",
                    int(generation["generation_id"]),
                    1 if generation.get("installed") else 0,
                )
            )
    else:
        updates.append(update_u32_map("param_status", active_parameter_generation, 1))

    loaded_semantic_fec_keys: set[tuple[int, int]] = set()
    for entry in fec_entries_to_load:
        generation_id = int(entry["generation_id"])
        fec_key = int(entry["fec_key_u32"])
        route_key = pack_u32s(generation_id, 0, fec_key)
        route_action = ACTION_FORWARD if entry.get("active") else ACTION_PASS
        route_value = (
            pack_u32s(1 if entry.get("active") else 0, route_action, 0, 0)
            + ["00"] * 12
        )
        updates.append(update_hash_map("fec_forwarding", route_key, route_value))
        loaded_semantic_fec_keys.add((generation_id, fec_key))

    loaded_semantic_parameter_keys: set[tuple[int, int, int]] = set()
    loaded_semantic_parameter_links: set[str] = set()
    for entry in parameter_entries_to_load:
        generation_id = int(entry["generation_id"])
        link_key = int(entry["link_key_u32"])
        flow_key = int(entry.get("flow_key_u32") or 0)
        parameter_key = pack_u32s(generation_id, link_key, flow_key)
        parameter_value = pack_u32s(
            1 if entry.get("active") else 0,
            int(entry.get("loss_ppm", 0) or 0),
            int(entry.get("mark", 0) or 0),
        )
        updates.append(update_hash_map("parameter_values", parameter_key, parameter_value))
        loaded_semantic_parameter_keys.add((generation_id, link_key, flow_key))
        if entry.get("link_id"):
            loaded_semantic_parameter_links.add(str(entry["link_id"]))

    loaded_dataplane_route_keys: set[tuple[int, int, int]] = set()
    loaded_target_ips: set[str] = set()
    loaded_dataplane_parameter_links: set[str] = set()
    loaded_dataplane_parameter_keys: set[tuple[int, int, int]] = set()
    for entry in scope["entries"]:
        target_ip = str(entry["target_ip"])
        dst_ipv4 = ipv4_little_endian_u32(entry["target_ip"])
        ingress_ifindex = int(entry.get("ingress_ifindex") or 0)
        if not ingress_ifindex and entry.get("ut_host_interface"):
            ingress_ifindex = interface_ifindex(str(entry["ut_host_interface"]))
        route_key_tuple = (active_runtime_generation, ingress_ifindex, dst_ipv4)
        if route_key_tuple in loaded_dataplane_route_keys:
            continue
        route_key = pack_u32s(*route_key_tuple)
        route_value = pack_u32s(
            1,
            ACTION_FORWARD,
            int(entry.get("egress_ifindex") or entry["satellite_ifindex"]),
            (
                ipv4_little_endian_u32(str(entry["new_dst_ip"]))
                if entry.get("new_dst_ip")
                else 0
            ),
        ) + [
            f"{byte:02x}"
            for byte in (
                mac_bytes(entry.get("dst_mac")) + mac_bytes(entry.get("src_mac"))
            )
        ]
        updates.append(update_hash_map("fec_forwarding", route_key, route_value))
        parameter_key = pack_u32s(active_parameter_generation, dst_ipv4, IPPROTO_ICMP)
        parameter_value = pack_u32s(
            1,
            int(entry.get("parameter_loss_ppm", 0)),
            int(entry.get("parameter_mark", 0)),
        )
        updates.append(update_hash_map("parameter_values", parameter_key, parameter_value))
        loaded_dataplane_route_keys.add(route_key_tuple)
        loaded_target_ips.add(target_ip)
        loaded_dataplane_parameter_links.add(str(entry["slot_name"]))
        loaded_dataplane_parameter_keys.add(
            (active_parameter_generation, dst_ipv4, IPPROTO_ICMP)
        )
    loaded_parameter_entry_count = (
        len(loaded_semantic_parameter_keys)
        if full_scope
        else len(loaded_dataplane_parameter_keys)
    )
    loaded_fec_count = (
        len(loaded_semantic_fec_keys)
        if full_scope
        else len(loaded_dataplane_route_keys)
    )
    loaded_parameter_link_count = (
        len(loaded_semantic_parameter_links)
        if full_scope
        else len(loaded_dataplane_parameter_links)
    )

    return {
        "updates": updates,
        "ok": not capacity_errors and all(command_ok(update) for update in updates),
        "capacity_errors": capacity_errors,
        "capacities": capacities,
        "loaded_generation_count": len(runtime_status_to_load),
        "loaded_parameter_generation_count": len(parameter_status_to_load)
        if parameter_status
        else 1,
        "loaded_fec_count": loaded_fec_count,
        "loaded_parameter_link_count": loaded_parameter_link_count,
        "loaded_parameter_entry_count": loaded_parameter_entry_count,
        "active_generation_switch_count": len(active_generations_before),
        "loaded_dataplane_route_count": len(loaded_dataplane_route_keys),
        "loaded_dataplane_parameter_entry_count": len(loaded_dataplane_parameter_keys),
        "target_ips": sorted(loaded_target_ips),
        "loaded_dataplane_route_keys": [
            {
                "generation_id": generation_id,
                "ingress_ifindex": ingress_ifindex,
                "dst_ipv4": dst_ipv4,
            }
            for generation_id, ingress_ifindex, dst_ipv4 in sorted(
                loaded_dataplane_route_keys
            )
        ],
    }


def update_u32_map(map_name: str, key: int, value: int) -> dict[str, Any]:
    return update_hash_map(map_name, pack_u32s(key), pack_u32s(value))


def update_hash_map(map_name: str, key_bytes: list[str], value_bytes: list[str]) -> dict[str, Any]:
    ref_args, ref_meta = map_reference(map_name)
    result = bpftool(
        [
            "map",
            "update",
            *ref_args,
            "key",
            "hex",
            *key_bytes,
            "value",
            "hex",
            *value_bytes,
        ],
        timeout=8.0,
    )
    result["map_reference"] = ref_meta
    return result


def map_reference(map_name: str) -> tuple[list[str], dict[str, Any]]:
    pinned = PIN_BASE / map_name
    if privileged_path_exists(pinned):
        return ["pinned", str(pinned)], {"kind": "pinned", "path": str(pinned)}
    maps = bpftool_json(["-j", "map", "show"])
    kernel_name = KERNEL_MAP_NAMES.get(map_name, map_name[:15])
    matches = [
        item
        for item in maps
        if isinstance(item, dict) and item.get("name") == kernel_name
    ]
    if matches:
        selected = sorted(matches, key=lambda item: int(item.get("id", -1)))[-1]
        return ["id", str(selected["id"])], {
            "kind": "id",
            "logical_name": map_name,
            "kernel_name": kernel_name,
            "id": selected["id"],
        }
    return ["pinned", str(pinned)], {
        "kind": "missing",
        "logical_name": map_name,
        "kernel_name": kernel_name,
    }


def bpftool(args: Sequence[str], *, timeout: float = 10.0) -> dict[str, Any]:
    global BPFTOOL
    if not BPFTOOL:
        BPFTOOL = resolve_bpftool() or ""
    if not BPFTOOL:
        return {
            "args": ["<unconfigured-skyseed-bpftool>", *args],
            "returncode": 127,
            "stdout": "",
            "stderr": "SKYSEED_BPFTOOL_PATH is missing or invalid",
            "timeout": False,
        }
    return run_command([BPFTOOL, *args], timeout=timeout, privileged=True)


def bpftool_json(args: Sequence[str]) -> list[Any]:
    result = bpftool(args, timeout=8.0)
    if not result.get("stdout"):
        return []
    try:
        parsed = json.loads(result["stdout"])
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def read_packet_counters() -> dict[str, Any]:
    ref_args, ref_meta = map_reference("packet_counters")
    result = bpftool(["-j", "map", "dump", *ref_args], timeout=8.0)
    counters: dict[str, int] = {label: 0 for label in COUNTER_LABELS.values()}
    parsed: Any = None
    if result.get("stdout"):
        try:
            parsed = json.loads(result["stdout"])
        except json.JSONDecodeError:
            parsed = None
    for entry in parsed if isinstance(parsed, list) else []:
        key = decode_bpftool_hex_u32(entry.get("key"))
        value = decode_bpftool_hex_u64(entry.get("value"))
        label = COUNTER_LABELS.get(key)
        if label is not None:
            counters[label] = value
    return {
        "command": result,
        "map_reference": ref_meta,
        "parsed": parsed,
        "counters": counters,
    }


def read_interface_counters() -> dict[str, Any]:
    ref_args, ref_meta = map_reference("interface_counters")
    result = bpftool(["-j", "map", "dump", *ref_args], timeout=8.0)
    parsed: Any = None
    if result.get("stdout"):
        try:
            parsed = json.loads(result["stdout"])
        except json.JSONDecodeError:
            parsed = None
    entries: list[dict[str, Any]] = []
    counters_by_ifindex: dict[int, dict[str, int]] = {}
    for entry in parsed if isinstance(parsed, list) else []:
        key = decode_bpftool_hex_u32_pair(entry.get("key"))
        value = decode_bpftool_hex_u64(entry.get("value"))
        ifindex, counter_id = key
        label = COUNTER_LABELS.get(counter_id, f"counter_{counter_id}")
        counters = counters_by_ifindex.setdefault(
            ifindex,
            {known_label: 0 for known_label in COUNTER_LABELS.values()},
        )
        counters[label] = value
        entries.append(
            {
                "ifindex": ifindex,
                "counter_id": counter_id,
                "counter_label": label,
                "value": value,
            }
        )
    return {
        "command": result,
        "map_reference": ref_meta,
        "parsed": parsed,
        "entries": entries,
        "counters_by_ifindex": {
            str(ifindex): counters
            for ifindex, counters in sorted(counters_by_ifindex.items())
        },
    }


def decode_bpftool_hex_u32(value: Any) -> int:
    raw = bytes(int(part, 16) for part in list(value or [])[:4])
    return int.from_bytes(raw.ljust(4, b"\0"), byteorder="little", signed=False)


def decode_bpftool_hex_u32_pair(value: Any) -> tuple[int, int]:
    raw = bytes(int(part, 16) for part in list(value or [])[:8])
    raw = raw.ljust(8, b"\0")
    return (
        int.from_bytes(raw[:4], byteorder="little", signed=False),
        int.from_bytes(raw[4:8], byteorder="little", signed=False),
    )


def decode_bpftool_hex_u64(value: Any) -> int:
    raw = bytes(int(part, 16) for part in list(value or [])[:8])
    return int.from_bytes(raw.ljust(8, b"\0"), byteorder="little", signed=False)


def ipv4_little_endian_u32(value: str) -> int:
    return int.from_bytes(ipaddress.IPv4Address(value).packed, byteorder="little")


def pack_u32s(*values: int) -> list[str]:
    raw = b"".join((int(value) & 0xFFFFFFFF).to_bytes(4, "little") for value in values)
    return [f"{byte:02x}" for byte in raw]
