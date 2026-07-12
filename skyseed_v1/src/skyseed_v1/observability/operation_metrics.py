#!/usr/bin/env python3
# encoding: utf-8

"""Lightweight optional runtime operation metrics for Skyseed diagnostics."""

from __future__ import annotations

import shlex
import threading
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence


def _strip_wrappers(command: Sequence[str]) -> list[str]:
    parts = [str(part) for part in command]
    if parts[:2] == ["sudo", "-n"]:
        parts = parts[2:]
    if parts[:1] == ["nsenter"]:
        if "--" in parts:
            parts = parts[parts.index("--") + 1 :]
        elif "-n" in parts:
            parts = parts[parts.index("-n") + 1 :]
    if parts[:2] == ["docker", "exec"]:
        rest = parts[2:]
        if rest and rest[0] == "-i":
            rest = rest[1:]
        if rest:
            rest = rest[1:]
        parts = rest
    return parts


def _shell_payload(parts: Sequence[str]) -> str | None:
    normalized = list(parts)
    if len(normalized) >= 3 and normalized[0] in {"sh", "bash"} and normalized[1] in {"-c", "-lc", "-s"}:
        if normalized[1] == "-s":
            return None
        return str(normalized[2])
    return None


def _split_shell_payload(payload: str) -> list[list[str]]:
    commands: list[list[str]] = []
    for chunk in payload.replace("&&", ";").split(";"):
        rendered = chunk.strip()
        if not rendered or rendered in {"true", "set -eu"}:
            continue
        if rendered.endswith("|| true"):
            rendered = rendered[: -len("|| true")].strip()
        try:
            parsed = shlex.split(rendered)
        except ValueError:
            continue
        if parsed:
            commands.append(parsed)
    return commands


def _classify_simple_command(command: Sequence[str]) -> tuple[str, str]:
    parts = _strip_wrappers(command)
    if not parts:
        return "unknown", "unknown"
    payload = _shell_payload(parts)
    if payload is not None:
        inner = _split_shell_payload(payload)
        if len(inner) == 1:
            return _classify_simple_command(inner[0])
        return "shell_batch", "shell_batch"
    if parts[:2] in (["tc", "-batch"], ["tc", "-b"]):
        return "qdisc", "qdisc_batch"
    if parts[:3] == ["tc", "qdisc", "add"]:
        return "qdisc", "qdisc_add"
    if parts[:3] == ["tc", "qdisc", "change"]:
        return "qdisc", "qdisc_change"
    if parts[:3] == ["tc", "qdisc", "replace"]:
        return "qdisc", "qdisc_replace"
    if parts[:3] == ["tc", "qdisc", "del"]:
        return "qdisc", "qdisc_delete"
    if parts[:3] == ["ip", "netns", "add"]:
        return "netns", "netns_add"
    if parts[:3] == ["ip", "netns", "del"]:
        return "netns", "netns_delete"
    if parts[:3] == ["ip", "netns", "exec"]:
        return "netns", "netns_exec"
    if parts[:4] == ["ip", "-f", "mpls", "route"]:
        action = parts[4] if len(parts) > 4 else "unknown"
        return "mpls_route", f"mpls_route_{_normalize_route_action(action)}"
    if parts[:3] == ["ip", "route", "add"]:
        return "route", "route_add"
    if parts[:3] == ["ip", "route", "replace"]:
        return "route", "route_replace"
    if parts[:3] == ["ip", "route", "del"]:
        return "route", "route_delete"
    if parts[:3] == ["ip", "route", "delete"]:
        return "route", "route_delete"
    if parts[:3] == ["ip", "route", "show"]:
        return "route", "route_show"
    if parts[:4] == ["ip", "link", "add", "name"]:
        return "link", "bridge_create"
    if parts[:3] == ["ip", "link", "add"]:
        if "veth" in parts:
            return "veth", "veth_create"
        return "link", "link_create"
    if parts[:3] == ["ip", "link", "del"]:
        return "veth", "veth_delete"
    if parts[:3] == ["ip", "link", "delete"]:
        return "veth", "veth_delete"
    if parts[:3] == ["ip", "link", "set"]:
        joined = " ".join(parts)
        if " netns " in f" {joined} ":
            return "veth", "veth_move_netns"
        if " name " in f" {joined} ":
            return "veth", "veth_rename"
        if " master " in f" {joined} " or " nomaster" in f" {joined} ":
            return "veth", "veth_master_update"
        if joined.endswith(" up") or " up " in f" {joined} ":
            return "veth", "veth_up"
        if joined.endswith(" down") or " down " in f" {joined} ":
            return "veth", "veth_down"
        return "link", "link_set"
    if parts[:3] == ["ip", "addr", "add"]:
        return "addr", "addr_add"
    if parts[:3] == ["ip", "addr", "flush"]:
        return "addr", "addr_flush"
    if parts and parts[0] == "iptables":
        action = next((part for part in parts if part in {"-A", "-D", "-C", "-I", "-R"}), "unknown")
        return "iptables", {
            "-A": "iptables_add",
            "-D": "iptables_delete",
            "-C": "iptables_check",
            "-I": "iptables_insert",
            "-R": "iptables_replace",
        }.get(action, "iptables_unknown")
    if parts[:2] == ["docker", "inspect"]:
        return "docker", "docker_inspect"
    if parts[:3] == ["docker", "network", "rm"]:
        return "docker", "docker_network_delete"
    if parts[:3] == ["docker", "network", "disconnect"]:
        return "docker", "docker_network_disconnect"
    if parts[:3] == ["docker", "network", "inspect"]:
        return "docker", "docker_network_inspect"
    if parts[:2] == ["docker", "exec"]:
        return "docker", "docker_exec"
    return parts[0], f"{parts[0]}_other"


def _normalize_route_action(action: str) -> str:
    if action in {"del", "delete"}:
        return "delete"
    if action in {"add", "replace", "show"}:
        return action
    return "unknown"


def classify_runtime_command(command: Sequence[str]) -> dict[str, str]:
    family, operation = _classify_simple_command(command)
    return {"family": family, "operation": operation}


def classify_node_local_command(command: Sequence[str]) -> dict[str, str]:
    return classify_runtime_command(command)


def classify_node_local_command_scope(
    command: Sequence[str],
    *,
    description: str = "",
    phase: str = "",
) -> dict[str, str]:
    """Classify node-local Linux/MPLS command intent for bottleneck attribution."""

    parts = _strip_wrappers(command)
    rendered = " ".join(parts).lower()
    description_text = str(description).lower()
    classification = classify_node_local_command(command)
    family = classification["family"]
    operation = classification["operation"]

    if (
        family == "iptables"
        or "iptables" in rendered
        or "snat" in description_text
        or "filter rule" in description_text
    ):
        scope = "edge_nat_iptables"
    elif (
        "serving-satellite source rule" in description_text
        or "serving-satellite source-policy rules" in description_text
    ):
        scope = "satellite_core_mpls_lfib"
    elif family == "sysctl" or "platform labels" in description_text or "mpls input" in description_text:
        scope = "mpls_readiness_sysctl"
    elif (
        family == "mpls_route"
        or " encap mpls " in f" {rendered} "
        or "forward lfib" in description_text
        or "reverse lfib" in description_text
        or "forward pop" in description_text
        or "reverse pop" in description_text
        or "zone-return ingress route" in description_text
        or "zone-return mpls" in description_text
        or "policy-table default route" in description_text
        or "serving-satellite policy table" in description_text
        or "ut delivery route" in description_text
    ):
        scope = "satellite_core_mpls_lfib"
    elif "source rule" in description_text or "ip rule" in rendered:
        scope = "ip_route_rule"
    elif (
        "ut default route" in description_text
        or "gateway local-origin bpf return route" in description_text
    ):
        scope = "edge_bpf_endpoint_route"
    elif "explicit gateway icmp service loopback" in description_text:
        scope = "edge_bpf_endpoint_service"
    elif family == "route":
        scope = "fallback_control_route"
    else:
        scope = "other_unclassified"

    residue_scope = {
        "satellite_core_mpls_lfib": "satellite_core_mpls_lfib_residue",
        "ip_route_rule": "ip_route_rule_residue",
        "edge_bpf_endpoint_route": "edge_bpf_endpoint_route_residue",
        "edge_bpf_endpoint_service": "edge_bpf_endpoint_service_residue",
        "mpls_readiness_sysctl": "mpls_readiness_sysctl_cleanup_or_check",
        "edge_nat_iptables": "edge_iptables_snat_residue",
        "fallback_control_route": "fallback_control_residue",
    }.get(scope, "other_unclassified_cleanup")
    return {
        **classification,
        "scope": scope,
        "cleanup_scope": residue_scope if str(phase) == "rollback" else scope,
        "bpf_owned_gz_forwarding_candidate": str(scope == "satellite_core_mpls_lfib").lower(),
    }


def _round_seconds_by_key(values: Mapping[str, float]) -> dict[str, float]:
    return {key: round(float(value), 6) for key, value in sorted(values.items())}


@dataclass
class RuntimeOperationMetrics:
    """Accumulate optional typed command counters and command-level timing."""

    schema_version: int = 1
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    command_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    total_wall_seconds: float = 0.0
    counts_by_family: dict[str, int] = field(default_factory=dict)
    counts_by_operation: dict[str, int] = field(default_factory=dict)
    wall_seconds_by_family: dict[str, float] = field(default_factory=dict)
    wall_seconds_by_operation: dict[str, float] = field(default_factory=dict)
    returncode_counts: dict[str, int] = field(default_factory=dict)

    def record_command(
        self,
        command: Sequence[str],
        *,
        wall_seconds: float,
        returncode: int,
    ) -> None:
        classification = classify_runtime_command(command)
        self.record_operation(
            family=classification["family"],
            operation=classification["operation"],
            wall_seconds=wall_seconds,
            returncode=returncode,
        )

    def record_operation(
        self,
        *,
        family: str,
        operation: str,
        wall_seconds: float,
        returncode: int = 0,
        count: int = 1,
    ) -> None:
        count = max(0, int(count))
        if count == 0:
            return
        seconds = float(wall_seconds)
        with self._lock:
            self.command_count += count
            if int(returncode) == 0:
                self.success_count += count
            else:
                self.failure_count += count
            self.total_wall_seconds += seconds
            self.counts_by_family[family] = self.counts_by_family.get(family, 0) + count
            self.counts_by_operation[operation] = (
                self.counts_by_operation.get(operation, 0) + count
            )
            self.wall_seconds_by_family[family] = (
                self.wall_seconds_by_family.get(family, 0.0) + seconds
            )
            self.wall_seconds_by_operation[operation] = (
                self.wall_seconds_by_operation.get(operation, 0.0) + seconds
            )
            returncode_key = str(int(returncode))
            self.returncode_counts[returncode_key] = (
                self.returncode_counts.get(returncode_key, 0) + count
            )

    def snapshot(self, *, reset: bool = False) -> dict[str, Any]:
        with self._lock:
            payload = {
                "schema_version": self.schema_version,
                "command_count": self.command_count,
                "success_count": self.success_count,
                "failure_count": self.failure_count,
                "total_wall_seconds": round(self.total_wall_seconds, 6),
                "counts_by_family": dict(sorted(self.counts_by_family.items())),
                "counts_by_operation": dict(sorted(self.counts_by_operation.items())),
                "wall_seconds_by_family": _round_seconds_by_key(
                    self.wall_seconds_by_family
                ),
                "wall_seconds_by_operation": _round_seconds_by_key(
                    self.wall_seconds_by_operation
                ),
                "returncode_counts": dict(sorted(self.returncode_counts.items())),
            }
            if reset:
                self.reset()
            return payload

    def reset(self) -> None:
        self.command_count = 0
        self.success_count = 0
        self.failure_count = 0
        self.total_wall_seconds = 0.0
        self.counts_by_family.clear()
        self.counts_by_operation.clear()
        self.wall_seconds_by_family.clear()
        self.wall_seconds_by_operation.clear()
        self.returncode_counts.clear()


def summarize_node_local_batches(
    batch_plans: Iterable[Mapping[str, Any]],
    batch_results: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    try:
        metrics = RuntimeOperationMetrics()
        plan_list = [dict(batch) for batch in batch_plans if isinstance(batch, Mapping)]
        result_list = [dict(result) for result in batch_results if isinstance(result, Mapping)]
        scopes_by_name: dict[str, int] = {}
        cleanup_scopes_by_name: dict[str, int] = {}
        planned_state_touches_by_scope: dict[str, int] = {}
        wall_by_scope: dict[str, float] = {}
        wall_by_cleanup_scope: dict[str, float] = {}
        affected_nodes: set[str] = set()
        per_command_timing_available = False
        per_command_timing_count = 0
        candidate_bpf_owned_command_count = 0
        candidate_bpf_owned_wall_seconds = 0.0

        for batch_index, batch in enumerate(plan_list):
            node_id = str(batch.get("node_id") or "")
            phase = str(batch.get("phase") or "")
            if node_id:
                affected_nodes.add(node_id)
            commands = list(batch.get("commands", []) or [])
            descriptions = list(batch.get("descriptions", []) or [])
            result = result_list[batch_index] if batch_index < len(result_list) else {}
            command_timings = list(result.get("command_timings", []) or [])
            timing_by_index = {
                int(item.get("command_index")): item
                for item in command_timings
                if isinstance(item, Mapping) and item.get("command_index") is not None
            }
            if timing_by_index:
                per_command_timing_available = True
            for command_index, command in enumerate(commands):
                if not isinstance(command, list):
                    continue
                description = str(
                    descriptions[command_index] if command_index < len(descriptions) else ""
                )
                command_parts = [str(part) for part in command]
                scope_info = classify_node_local_command_scope(
                    command_parts,
                    description=description,
                    phase=phase,
                )
                scope = scope_info["scope"]
                cleanup_scope = scope_info["cleanup_scope"]
                scopes_by_name[scope] = scopes_by_name.get(scope, 0) + 1
                cleanup_scopes_by_name[cleanup_scope] = (
                    cleanup_scopes_by_name.get(cleanup_scope, 0) + 1
                )
                planned_state_touches_by_scope[scope] = (
                    planned_state_touches_by_scope.get(scope, 0) + 1
                )
                if scope_info["bpf_owned_gz_forwarding_candidate"] == "true":
                    candidate_bpf_owned_command_count += 1
                timing = timing_by_index.get(command_index)
                wall_seconds = 0.0
                returncode = 0
                if timing is not None:
                    per_command_timing_count += 1
                    wall_seconds = float(timing.get("wall_seconds", 0.0) or 0.0)
                    returncode = int(timing.get("returncode", 0) or 0)
                    wall_by_scope[scope] = wall_by_scope.get(scope, 0.0) + wall_seconds
                    wall_by_cleanup_scope[cleanup_scope] = (
                        wall_by_cleanup_scope.get(cleanup_scope, 0.0) + wall_seconds
                    )
                    if scope_info["bpf_owned_gz_forwarding_candidate"] == "true":
                        candidate_bpf_owned_wall_seconds += wall_seconds
                classification = classify_node_local_command(command_parts)
                metrics.record_operation(
                    family=classification["family"],
                    operation=classification["operation"],
                    wall_seconds=wall_seconds,
                    returncode=returncode,
                )
        batch_wall_seconds = sum(
            float(result.get("wall_seconds", 0.0) or 0.0) for result in result_list
        )
        max_batch_wall_seconds = max(
            [float(result.get("wall_seconds", 0.0) or 0.0) for result in result_list],
            default=0.0,
        )
        payload = metrics.snapshot()
        timing_granularity = (
            "per_command_wall_seconds_and_batch_wall_seconds"
            if per_command_timing_available
            else "batch_wall_seconds_command_counts"
        )
        payload.update(
            {
                "batch_count": len(result_list),
                "docker_exec_batch_count": len(result_list),
                "node_local_command_batch_count": len(result_list),
                "batch_wall_seconds": round(batch_wall_seconds, 6),
                "cumulative_batch_wall_seconds": round(batch_wall_seconds, 6),
                "max_batch_wall_seconds": round(max_batch_wall_seconds, 6),
                "worker_critical_path_wall_seconds_source": "caller_phase_timing",
                "timing_granularity": timing_granularity,
                "per_command_timing_available": per_command_timing_available,
                "per_command_timing_count": per_command_timing_count,
                "affected_node_count": len(affected_nodes),
                "counts_by_scope": dict(sorted(scopes_by_name.items())),
                "cleanup_counts_by_scope": dict(sorted(cleanup_scopes_by_name.items())),
                "planned_state_touch_count_by_scope": dict(
                    sorted(planned_state_touches_by_scope.items())
                ),
                "changed_state_count_available": False,
                "wall_seconds_by_scope": _round_seconds_by_key(wall_by_scope),
                "wall_seconds_by_cleanup_scope": _round_seconds_by_key(
                    wall_by_cleanup_scope
                ),
                "bpf_owned_gz_forwarding_candidate": {
                    "command_count": candidate_bpf_owned_command_count,
                    "wall_seconds": round(candidate_bpf_owned_wall_seconds, 6),
                    "cleanup_scopes_that_would_shrink": [
                        "satellite_core_mpls_lfib_residue"
                    ],
                    "note": (
                        "Counts commands in satellite_core_mpls_lfib scope. "
                        "Wall seconds require per-command timing and are otherwise 0."
                    ),
                },
                "measurement_semantics": {
                    "phase_wall_seconds": "recorded by caller timing fields",
                    "batch_wall_seconds": "cumulative worker batch wall, not critical path",
                    "per_command_wall_seconds": (
                        "direct when command_timings are present; unavailable for old artifacts"
                    ),
                },
            }
        )
        return payload
    except Exception as exc:  # noqa: BLE001 - diagnostics must not affect runtime.
        return {
            "schema_version": 1,
            "available": False,
            "instrumentation_error": str(exc),
            "command_count": 0,
            "batch_count": 0,
            "docker_exec_batch_count": 0,
            "node_local_command_batch_count": 0,
            "batch_wall_seconds": 0.0,
            "timing_granularity": "batch_wall_seconds_command_counts",
            "per_command_timing_available": False,
            "per_command_timing_count": 0,
            "counts_by_scope": {},
            "cleanup_counts_by_scope": {},
            "planned_state_touch_count_by_scope": {},
        }
