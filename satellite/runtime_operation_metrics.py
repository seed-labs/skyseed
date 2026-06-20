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
        for batch in batch_plans:
            for command in batch.get("commands", []) or []:
                if not isinstance(command, list):
                    continue
                classification = classify_node_local_command([str(part) for part in command])
                metrics.record_operation(
                    family=classification["family"],
                    operation=classification["operation"],
                    wall_seconds=0.0,
                    returncode=0,
                )
        result_list = [dict(result) for result in batch_results if isinstance(result, Mapping)]
        batch_wall_seconds = sum(
            float(result.get("wall_seconds", 0.0) or 0.0) for result in result_list
        )
        payload = metrics.snapshot()
        payload.update(
            {
                "batch_count": len(result_list),
                "docker_exec_batch_count": len(result_list),
                "node_local_command_batch_count": len(result_list),
                "batch_wall_seconds": round(batch_wall_seconds, 6),
                "timing_granularity": "batch_wall_seconds_command_counts",
                "per_command_timing_available": False,
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
        }
