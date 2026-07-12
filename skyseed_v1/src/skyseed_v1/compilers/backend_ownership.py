"""Compile node-local command intent into forwarding-backend ownership."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, List, Optional, Sequence, Tuple

from skyseed_v1.observability.operation_metrics import classify_node_local_command_scope
from skyseed_v1.runtime.execution import RuntimeCommandSpec


FORWARDING_BACKEND_MODES = ("linux_mpls", "bpf_shadow", "bpf_canary", "bpf_owned")
BPF_OWNED_ELIGIBLE_SCOPES = ("satellite_core_mpls_lfib", "mpls_readiness_sysctl")
BPF_OWNED_PROOF_SCHEMA_VERSION = "skyseed-bpf-owned-readiness-proof-v1"


def normalize_forwarding_backend_config(
    *,
    forwarding_backend: str = "linux_mpls",
    bpf_owned_readiness: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    backend_mode = str(forwarding_backend or "linux_mpls")
    if backend_mode not in FORWARDING_BACKEND_MODES:
        raise RuntimeError(
            "unsupported forwarding backend "
            f"{backend_mode!r}; expected one of {', '.join(FORWARDING_BACKEND_MODES)}"
        )
    readiness = dict(bpf_owned_readiness or {})
    has_structured_proof = readiness.get("schema_version") == BPF_OWNED_PROOF_SCHEMA_VERSION
    ready = bool(
        (readiness.get("ok") is True or readiness.get("ready") is True)
        and (backend_mode != "bpf_owned" or has_structured_proof)
        and not readiness.get("readiness_mismatch_reason")
        and not readiness.get("fallback_reason")
    )
    reason = str(
        readiness.get("reason")
        or readiness.get("failure_reason")
        or (
            "bpf_owned_readiness_proof_missing"
            if backend_mode == "bpf_owned" and not has_structured_proof
            else ""
        )
        or (
            "bpf_owned_readiness_not_proven"
            if backend_mode == "bpf_owned" and not ready
            else ""
        )
    )
    return {
        "backend_mode": backend_mode,
        "linux_backend_mode": "linux_mpls",
        "bpf_owned_readiness": {**readiness, "ready": ready, "reason": reason},
        "fail_closed": backend_mode == "bpf_owned",
        "eligible_scopes": list(BPF_OWNED_ELIGIBLE_SCOPES),
    }


def core_direction_for_spec(spec: RuntimeCommandSpec) -> str:
    text = f"{spec.description} {' '.join(spec.command)}".lower()
    if "reverse" in text or "zone-return" in text or "zone return" in text:
        return "z_to_g_core"
    if "forward" in text or "policy-table default" in text or "encap mpls" in text:
        return "g_to_z_core"
    if "mpls" in text:
        return "core_mpls_readiness"
    return "not_satellite_core_forwarding"


def classify_backend_ownership(
    spec: RuntimeCommandSpec,
    *,
    backend_config: Dict[str, Any],
) -> Dict[str, Any]:
    scope_info = classify_node_local_command_scope(
        spec.command,
        description=spec.description,
        phase=spec.phase,
    )
    scope = str(scope_info.get("scope") or "other_unclassified")
    backend_mode = str(backend_config.get("backend_mode") or "linux_mpls")
    eligible = scope in set(BPF_OWNED_ELIGIBLE_SCOPES)
    readiness = dict(backend_config.get("bpf_owned_readiness") or {})
    readiness_ok = bool(readiness.get("ready") is True)
    readiness_reason = str(readiness.get("reason") or "")
    direction = core_direction_for_spec(spec) if eligible else "not_satellite_core_forwarding"

    if not eligible:
        return {
            "scope": scope,
            "eligible_for_bpf_owned": False,
            "core_direction": direction,
            "ownership_status": "host_required",
            "apply_action": "retained_host_required",
            "skip": False,
            "reason": "outside_bpf_owned_satellite_core_scope",
        }
    if backend_mode == "linux_mpls":
        return {
            "scope": scope,
            "eligible_for_bpf_owned": True,
            "core_direction": direction,
            "ownership_status": "linux_owned",
            "apply_action": "applied_by_linux",
            "skip": False,
            "reason": "",
        }
    if backend_mode in {"bpf_shadow", "bpf_canary"}:
        return {
            "scope": scope,
            "eligible_for_bpf_owned": True,
            "core_direction": direction,
            "ownership_status": "shadow_only",
            "apply_action": "applied_by_linux_shadow_or_canary_only",
            "skip": False,
            "reason": f"{backend_mode}_does_not_replace_host_apply",
        }
    if backend_mode == "bpf_owned" and readiness_ok:
        return {
            "scope": scope,
            "eligible_for_bpf_owned": True,
            "core_direction": direction,
            "ownership_status": "bpf_owned",
            "apply_action": "skipped_by_bpf_backend",
            "skip": True,
            "reason": "bpf_owned_readiness_proven_strict_no_linux_gz_fallback",
        }
    return {
        "scope": scope,
        "eligible_for_bpf_owned": True,
        "core_direction": direction,
        "ownership_status": (
            "bpf_owned_planner_error" if backend_mode == "bpf_owned" else "fallback_to_linux"
        ),
        "apply_action": (
            "blocked_before_runtime" if backend_mode == "bpf_owned" else "fallback_to_linux"
        ),
        "skip": backend_mode == "bpf_owned",
        "reason": readiness_reason or "bpf_owned_readiness_not_proven",
    }


def _command_specs_payload(specs: Sequence[RuntimeCommandSpec]) -> List[Dict[str, Any]]:
    return [spec.to_dict() for spec in specs]


def plan_backend_apply_contract(
    specs: Sequence[RuntimeCommandSpec],
    *,
    backend_config: Dict[str, Any],
    contract_phase: str,
) -> Tuple[List[RuntimeCommandSpec], Dict[str, Any]]:
    kept_specs: List[RuntimeCommandSpec] = []
    skipped_specs: List[RuntimeCommandSpec] = []
    counts_by_status: Dict[str, int] = {}
    counts_by_action: Dict[str, int] = {}
    counts_by_scope: Dict[str, int] = {}
    core_direction_counts: Dict[str, int] = {}
    fallback_reasons: Dict[str, int] = {}
    planner_error_reasons: Dict[str, int] = {}
    command_reports: List[Dict[str, Any]] = []

    for spec in specs:
        decision = classify_backend_ownership(spec, backend_config=backend_config)
        annotated = replace(
            spec,
            ownership_status=str(decision["ownership_status"]),
            apply_action=str(decision["apply_action"]),
            backend_scope=str(decision["scope"]),
            skip_reason=str(decision["reason"]),
        )
        status = annotated.ownership_status
        action = annotated.apply_action
        scope = annotated.backend_scope
        counts_by_status[status] = counts_by_status.get(status, 0) + 1
        counts_by_action[action] = counts_by_action.get(action, 0) + 1
        counts_by_scope[scope] = counts_by_scope.get(scope, 0) + 1
        if bool(decision.get("eligible_for_bpf_owned")):
            direction = str(decision.get("core_direction") or "unknown_core_direction")
            core_direction_counts[direction] = core_direction_counts.get(direction, 0) + 1
        if status == "fallback_to_linux":
            reason = str(decision.get("reason") or "unknown")
            fallback_reasons[reason] = fallback_reasons.get(reason, 0) + 1
        if status == "bpf_owned_planner_error":
            reason = str(decision.get("reason") or "unknown")
            planner_error_reasons[reason] = planner_error_reasons.get(reason, 0) + 1
        if bool(decision.get("skip")):
            skipped_specs.append(annotated)
        else:
            kept_specs.append(annotated)
        command_reports.append(
            {
                **annotated.to_dict(),
                "eligible_for_bpf_owned": bool(decision.get("eligible_for_bpf_owned")),
                "core_direction": decision.get("core_direction"),
            }
        )

    skipped_count = len(skipped_specs)
    fallback_count = counts_by_status.get("fallback_to_linux", 0)
    planner_error_count = counts_by_status.get("bpf_owned_planner_error", 0)
    bpf_owned_skipped_count = counts_by_status.get("bpf_owned", 0)
    eligible_core_fallback_to_linux = sum(
        1
        for report in command_reports
        if report.get("eligible_for_bpf_owned")
        and report.get("ownership_status") == "fallback_to_linux"
    )
    linux_gz_fallback_command_count = eligible_core_fallback_to_linux
    linux_gz_fallback_enabled = bool(
        str(backend_config.get("backend_mode") or "") == "bpf_owned"
        and linux_gz_fallback_command_count > 0
    )
    bpf_owned_allowed_host_required_scopes = {
        "edge_bpf_endpoint_route",
        "edge_bpf_endpoint_service",
    }
    bpf_owned_disallowed_host_required_reports = [
        report
        for report in command_reports
        if str(backend_config.get("backend_mode") or "") == "bpf_owned"
        and report.get("ownership_status") == "host_required"
        and str(report.get("backend_scope") or "") not in bpf_owned_allowed_host_required_scopes
    ]
    backend_mode = str(backend_config.get("backend_mode") or "")
    effective_backend_mode = (
        "bpf_owned_planner_error"
        if backend_mode == "bpf_owned" and planner_error_count > 0
        else "bpf_owned_disallowed_host_required_apply"
        if backend_mode == "bpf_owned" and bpf_owned_disallowed_host_required_reports
        else "bpf_owned_debug_safety_linux_fallback"
        if backend_mode == "bpf_owned" and linux_gz_fallback_enabled
        else backend_mode
    )
    strict_guard_ok = not (
        backend_mode == "bpf_owned"
        and (
            linux_gz_fallback_command_count > 0
            or planner_error_count > 0
            or bool(bpf_owned_disallowed_host_required_reports)
        )
    )
    return kept_specs, {
        "contract_phase": contract_phase,
        "backend_mode": backend_config.get("backend_mode"),
        "effective_backend_mode": effective_backend_mode,
        "linux_backend_mode": backend_config.get("linux_backend_mode"),
        "eligible_scopes": list(backend_config.get("eligible_scopes") or []),
        "fail_closed": bool(backend_config.get("fail_closed")),
        "bpf_owned_readiness": dict(backend_config.get("bpf_owned_readiness") or {}),
        "requested_command_count": len(specs),
        "linux_applied_command_count": len(kept_specs),
        "skipped_linux_command_count": skipped_count,
        "skipped_by_bpf_backend_count": bpf_owned_skipped_count,
        "linux_gz_fallback_enabled": linux_gz_fallback_enabled,
        "linux_gz_fallback_command_count": linux_gz_fallback_command_count,
        "eligible_core_fallback_to_linux": eligible_core_fallback_to_linux,
        "bpf_owned_strict_no_linux_gz_fallback_guard": strict_guard_ok,
        "fallback_to_linux_count": fallback_count,
        "fallback_to_linux_reasons": dict(sorted(fallback_reasons.items())),
        "bpf_owned_planner_error_count": planner_error_count,
        "bpf_owned_planner_error_reasons": dict(sorted(planner_error_reasons.items())),
        "bpf_owned_allowed_host_required_scopes": sorted(bpf_owned_allowed_host_required_scopes),
        "bpf_owned_disallowed_host_required_count": len(
            bpf_owned_disallowed_host_required_reports
        ),
        "bpf_owned_disallowed_host_required_commands": bpf_owned_disallowed_host_required_reports,
        "bpf_owned_satellite_core_gz_scope_count": counts_by_status.get("bpf_owned", 0),
        "host_required_count": counts_by_status.get("host_required", 0),
        "shadow_only_count": counts_by_status.get("shadow_only", 0),
        "linux_owned_count": counts_by_status.get("linux_owned", 0),
        "counts_by_ownership_status": dict(sorted(counts_by_status.items())),
        "counts_by_apply_action": dict(sorted(counts_by_action.items())),
        "counts_by_scope": dict(sorted(counts_by_scope.items())),
        "core_direction_counts": dict(sorted(core_direction_counts.items())),
        "gz_core_coverage": {
            "g_to_z_core_command_count": core_direction_counts.get("g_to_z_core", 0),
            "z_to_g_core_command_count": core_direction_counts.get("z_to_g_core", 0),
            "core_mpls_readiness_command_count": core_direction_counts.get(
                "core_mpls_readiness", 0
            ),
        },
        "cleanup_scopes_narrowed_by_bpf_ownership": (
            ["satellite_core_mpls_lfib_residue", "mpls_readiness_sysctl_cleanup_or_check"]
            if bpf_owned_skipped_count
            else []
        ),
        "skipped_specs": _command_specs_payload(skipped_specs),
        "commands": command_reports,
        "scope_boundary": {
            "satellite_core_forwarding": list(BPF_OWNED_ELIGIBLE_SCOPES),
            "excluded": [
                "qdisc delay/rate/loss/bandwidth",
                "veth/netns/address lifecycle",
                "UT/GW edge IP behavior",
                "iptables/SNAT",
                "Mini Internet edge routes",
                "fallback/control traffic",
            ],
        },
    }


__all__ = [
    "BPF_OWNED_ELIGIBLE_SCOPES",
    "BPF_OWNED_PROOF_SCHEMA_VERSION",
    "FORWARDING_BACKEND_MODES",
    "classify_backend_ownership",
    "core_direction_for_spec",
    "normalize_forwarding_backend_config",
    "plan_backend_apply_contract",
]
