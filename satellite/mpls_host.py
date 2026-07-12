#!/usr/bin/env python3
# encoding: utf-8

"""Public host MPLS readiness and preparation helpers."""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Sequence

HOST_MPLS_REQUIRED_MODULES = ("mpls_router", "mpls_iptunnel", "mpls_gso")
HOST_MPLS_PLATFORM_LABELS = 1048575
BRIDGE_NETFILTER_PASSTHROUGH_SYSCTLS = (
    "net.bridge.bridge-nf-call-iptables",
    "net.bridge.bridge-nf-call-ip6tables",
    "net.bridge.bridge-nf-call-arptables",
)

_PREPARE_LOCK = threading.Lock()
_PREPARED_STATE: Dict[str, Any] = {
    "prepared": False,
    "summary": None,
}


class HostMplsPreparationError(RuntimeError):
    """Raised when host MPLS readiness cannot be established."""

    def __init__(self, message: str, *, report: Dict[str, Any]):
        self.report = report
        super().__init__(message)


def _run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        capture_output=True,
        text=True,
        check=False,
    )


def _run_jsonable(command: Sequence[str]) -> Dict[str, Any]:
    completed = _run_command(command)
    return {
        "command": list(command),
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
        "ok": completed.returncode == 0,
    }


def _probe_host_module(module_name: str) -> Dict[str, Any]:
    loaded = False
    modules_path = Path("/proc/modules")
    if modules_path.exists():
        loaded = any(
            line.startswith(f"{module_name} ")
            for line in modules_path.read_text(encoding="utf-8").splitlines()
        )
    dry_run = _run_jsonable(["modprobe", "-n", "-v", module_name])
    return {
        "module_name": module_name,
        "loaded": loaded,
        "loadable": dry_run["ok"],
        "probe": dry_run,
    }


def _probe_sysctl(name: str) -> Dict[str, Any]:
    completed = _run_command(["sysctl", "-n", name])
    value = completed.stdout.strip()
    int_value = int(value) if value.isdigit() else None
    return {
        "name": name,
        "available": completed.returncode == 0,
        "value": value,
        "int_value": int_value,
        "stderr": completed.stderr.strip(),
    }


def _probe_iproute2() -> Dict[str, Any]:
    route_show = _run_jsonable(["ip", "-f", "mpls", "route", "show"])
    route_help = _run_jsonable(["ip", "route", "help"])
    route_help_text = str(route_help.get("stdout") or route_help.get("stderr") or "").lower()
    return {
        "ip_f_mpls_route_show": route_show,
        "supports_mpls_family": "family := [ inet | inet6 | mpls" in route_help_text,
        "supports_encap_mpls": "encaptype := [ mpls" in route_help_text,
    }


def probe_host_mpls_readiness() -> Dict[str, Any]:
    modules = {
        module_name: _probe_host_module(module_name)
        for module_name in HOST_MPLS_REQUIRED_MODULES
    }
    sysctl_probe = _probe_sysctl("net.mpls.platform_labels")
    iproute2 = _probe_iproute2()
    blocking_reasons: List[str] = []
    if not all(module.get("loaded", False) for module in modules.values()):
        blocking_reasons.append("host_mpls_modules_not_loaded")
    if not all(module.get("loadable", False) for module in modules.values()):
        blocking_reasons.append("host_mpls_modules_not_loadable")
    if not sysctl_probe.get("available", False):
        blocking_reasons.append("host_net.mpls.platform_labels_unavailable")
    elif sysctl_probe.get("int_value") is None:
        blocking_reasons.append("host_net.mpls.platform_labels_non_numeric")
    elif int(sysctl_probe["int_value"]) <= 0:
        blocking_reasons.append("host_net.mpls.platform_labels_zero")
    if not bool(iproute2["ip_f_mpls_route_show"].get("ok")):
        blocking_reasons.append("host_ip_f_mpls_route_show_failed")
    if not bool(iproute2["supports_mpls_family"]) or not bool(
        iproute2["supports_encap_mpls"]
    ):
        blocking_reasons.append("host_iproute2_mpls_support_incomplete")
    recommended_next_steps = [
        "Load host modules with modprobe mpls_router, mpls_iptunnel, and mpls_gso.",
        "Set host net.mpls.platform_labels to a non-zero value before starting the real runtime.",
        "Rerun the public host MPLS preparation helper after reboot before the first real MPLS runtime.",
    ]
    if not blocking_reasons:
        recommended_next_steps = [
            "Host MPLS readiness is green.",
            "Proceed with real runtime container bring-up and node-local MPLS programming.",
        ]
    return {
        "requirements": {
            "host_kernel_modules": list(HOST_MPLS_REQUIRED_MODULES),
            "host_sysctls": ["net.mpls.platform_labels"],
            "iproute2_features": ["ip -f mpls route", "ip route ... encap mpls"],
        },
        "host": {
            "modules": modules,
            "sysctls": {"net.mpls.platform_labels": sysctl_probe},
            "iproute2": iproute2,
        },
        "assessment": {
            "ready": not blocking_reasons,
            "blocking_reasons": blocking_reasons,
            "recommended_next_steps": recommended_next_steps,
        },
    }


def prepare_host_mpls() -> Dict[str, Any]:
    started_at = time.perf_counter()
    command_results: List[Dict[str, Any]] = []
    for command in (
        ["sudo", "-n", "modprobe", "mpls_router"],
        ["sudo", "-n", "modprobe", "mpls_iptunnel"],
        ["sudo", "-n", "modprobe", "mpls_gso"],
        [
            "sudo",
            "-n",
            "sysctl",
            "-w",
            f"net.mpls.platform_labels={HOST_MPLS_PLATFORM_LABELS}",
        ],
    ):
        result = _run_jsonable(command)
        command_results.append(result)
        if result["ok"] is not True:
            raise HostMplsPreparationError(
                "host MPLS preparation command failed",
                report={
                    "prepared": False,
                    "command_results": command_results,
                    "failed_command": result,
                    "wall_seconds": time.perf_counter() - started_at,
                },
            )
    readiness = probe_host_mpls_readiness()
    if readiness["assessment"]["ready"] is not True:
        raise HostMplsPreparationError(
            "host MPLS preparation completed but readiness is still blocked",
            report={
                "prepared": False,
                "command_results": command_results,
                "readiness": readiness,
                "wall_seconds": time.perf_counter() - started_at,
            },
        )
    summary = {
        "prepared": True,
        "prepared_now": True,
        "command_results": command_results,
        "readiness": readiness,
        "wall_seconds": time.perf_counter() - started_at,
    }
    _PREPARED_STATE["prepared"] = True
    _PREPARED_STATE["summary"] = dict(summary)
    return summary


def prepare_bridge_netfilter_passthrough() -> Dict[str, Any]:
    started_at = time.perf_counter()
    command_results: List[Dict[str, Any]] = []
    for key in BRIDGE_NETFILTER_PASSTHROUGH_SYSCTLS:
        result = _run_jsonable(["sudo", "-n", "sysctl", "-w", f"{key}=0"])
        command_results.append(result)
        if result["ok"] is not True:
            raise HostMplsPreparationError(
                "bridge netfilter passthrough preparation failed",
                report={
                    "prepared": False,
                    "command_results": command_results,
                    "failed_command": result,
                    "wall_seconds": time.perf_counter() - started_at,
                },
            )
    return {
        "prepared": True,
        "command_results": command_results,
        "wall_seconds": time.perf_counter() - started_at,
    }


def ensure_host_mpls_ready(*, auto_prepare: bool = True) -> Dict[str, Any]:
    with _PREPARE_LOCK:
        cached_summary = _PREPARED_STATE.get("summary")
        if _PREPARED_STATE.get("prepared") is True and isinstance(cached_summary, dict):
            return {
                "prepared": True,
                "prepared_now": False,
                "command_results": [],
                "readiness": dict(cached_summary.get("readiness", {})),
                "wall_seconds": 0.0,
            }

        readiness = probe_host_mpls_readiness()
        if readiness["assessment"]["ready"] is True:
            summary = {
                "prepared": True,
                "prepared_now": False,
                "command_results": [],
                "readiness": readiness,
                "wall_seconds": 0.0,
            }
            _PREPARED_STATE["prepared"] = True
            _PREPARED_STATE["summary"] = dict(summary)
            return summary

        if auto_prepare:
            preparable_reasons = {
                "host_mpls_modules_not_loaded",
                "host_net.mpls.platform_labels_unavailable",
                "host_net.mpls.platform_labels_zero",
                "host_ip_f_mpls_route_show_failed",
            }
            blocking_reasons = set(readiness["assessment"]["blocking_reasons"])
            if blocking_reasons.issubset(preparable_reasons) or (
                "host_mpls_modules_not_loaded" in blocking_reasons
                and "host_mpls_modules_not_loadable" not in blocking_reasons
            ):
                return prepare_host_mpls()

        raise HostMplsPreparationError(
            "host MPLS is not ready for real runtime execution",
            report={
                "prepared": False,
                "readiness": readiness,
            },
        )


def render_host_mpls_summary(summary: Dict[str, Any]) -> str:
    readiness = dict(summary.get("readiness", {}))
    assessment = dict(readiness.get("assessment", {}))
    lines = [
        "Host MPLS preparation",
        f"  prepared={summary.get('prepared')}",
        f"  prepared_now={summary.get('prepared_now')}",
        f"  wall_seconds={round(float(summary.get('wall_seconds', 0.0)), 6)}",
        f"  ready={assessment.get('ready')}",
    ]
    blocking_reasons = list(assessment.get("blocking_reasons", []))
    if blocking_reasons:
        lines.append(f"  blocking_reasons={', '.join(blocking_reasons)}")
    for command_result in summary.get("command_results", []):
        lines.append(
            "  $ "
            + " ".join(command_result.get("command", []))
            + f" -> rc={command_result.get('returncode')}"
        )
        if command_result.get("stdout"):
            lines.append(f"    stdout: {command_result['stdout']}")
        if command_result.get("stderr"):
            lines.append(f"    stderr: {command_result['stderr']}")
    return "\n".join(lines)


def render_host_mpls_json(summary: Dict[str, Any]) -> str:
    return json.dumps(summary, indent=2, sort_keys=True)
