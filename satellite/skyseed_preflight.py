#!/usr/bin/env python3
# encoding: utf-8

"""Scoped Skyseed preflight checks and cleanup suggestions."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Sequence

from mpls_host import probe_host_mpls_readiness

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
SATELLITE_DIR = Path(__file__).resolve().parent
DEFAULT_RUN_ROOT = SATELLITE_DIR / "output" / "starlink_shell_experiments"
DEFAULT_STATUS_DIR = SATELLITE_DIR / "output"
PREFLIGHT_PROFILE_DEFAULT = "default"
PREFLIGHT_PROFILE_OPERATIONAL_CLEAN_BPF_OWNED = "operational-clean-bpf-owned"
PREFLIGHT_PROFILE_LEGACY_MPLS = "legacy-mpls"
PREFLIGHT_PROFILES = (
    PREFLIGHT_PROFILE_DEFAULT,
    PREFLIGHT_PROFILE_OPERATIONAL_CLEAN_BPF_OWNED,
    PREFLIGHT_PROFILE_LEGACY_MPLS,
)
SKYSEED_COMPOSE_PROJECT = "seed_output"
SKYSEED_COMPOSE_FILE_NAME = "docker-compose.yml"
BRIDGE_NETFILTER_SYSCTLS = (
    "net.bridge.bridge-nf-call-iptables",
    "net.bridge.bridge-nf-call-ip6tables",
    "net.bridge.bridge-nf-call-arptables",
)
SKYSEED_CONTAINER_PATTERNS = (
    re.compile(r"^stl-\d+$"),
    re.compile(r"^gw\d+$"),
    re.compile(r"^ut\d+$"),
    re.compile(r"^ix\d+$"),
    re.compile(r"^starlinkexp-"),
    re.compile(r"^seed_output_"),
    re.compile(r"^ut\d+-placeholder-rtr$"),
    re.compile(r"^[0-9a-f]{12}_ut\d+-placeholder-rtr$"),
)
SKYSEED_NETWORK_PATTERNS = (
    re.compile(r"^seed_output_"),
    re.compile(r"^skyseed-sat-runtime-mgmt$"),
    re.compile(r"^ut\d+-access-slot\d+$"),
    re.compile(r"^gw\d+-gateway-slot\d+$"),
    re.compile(r"^p\d{3}p\d{3}-cross-slot\d+$"),
    re.compile(r"^fs\d{4}$"),
)
DIRECT_BACKEND_HOST_INTERFACE_PATTERNS = (
    re.compile(r"^sbr[0-9a-f]{11}$"),
    re.compile(r"^vhs[0-9a-f]{11}$"),
    re.compile(r"^vtm[0-9a-f]{11}$"),
)
BPF_PIN_BASE = Path("/sys/fs/bpf/tc/globals")
SKYSEED_BPF_PINNED_MAPS = (
    "active_generation",
    "ingress_classifier_map",
    "core_lfib_map",
    "egress_handoff_map",
    "packet_counters",
    "interface_counters",
    "interface_role_map",
    "miss_samples",
)
LOCK_FILE_PATTERNS = ("*.lock", "*.pid", "*.pidfile")
LOW_DISK_WARN_BYTES = 10 * 1024 * 1024 * 1024
LOW_DISK_ERROR_BYTES = 2 * 1024 * 1024 * 1024
PREFERRED_SEED310_PYTHON = Path("/home/seed/miniconda3/envs/seed310/bin/python")
LEGACY_SEED_EMULATOR_OVERRIDE = str(Path("/home/seed/seed-emulator").resolve())
MIN_SUPPORTED_PYTHON = (3, 10)


def _timestamp_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        text=True,
        capture_output=True,
        check=False,
    )


def _run_jsonable(command: Sequence[str]) -> Dict[str, Any]:
    completed = _run(command)
    return {
        "command": list(command),
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "ok": completed.returncode == 0,
    }


def _safe_int(value: str | None) -> int | None:
    if value is None:
        return None
    stripped = str(value).strip()
    if not stripped or not stripped.isdigit():
        return None
    return int(stripped)


def _sysctl_probe(name: str) -> Dict[str, Any]:
    completed = _run(["sysctl", "-n", name])
    value = completed.stdout.strip()
    return {
        "name": name,
        "available": completed.returncode == 0,
        "value": value,
        "int_value": _safe_int(value),
        "stderr": completed.stderr.strip(),
    }


def _matches_any(value: str, patterns: Sequence[re.Pattern[str]]) -> bool:
    return any(pattern.search(value) for pattern in patterns)


def _normalize_preflight_profile(profile: str | None) -> str:
    value = str(profile or PREFLIGHT_PROFILE_DEFAULT).strip().lower().replace("_", "-")
    aliases = {
        "clean": PREFLIGHT_PROFILE_OPERATIONAL_CLEAN_BPF_OWNED,
        "operational-clean": PREFLIGHT_PROFILE_OPERATIONAL_CLEAN_BPF_OWNED,
        "bpf-owned": PREFLIGHT_PROFILE_OPERATIONAL_CLEAN_BPF_OWNED,
        "strict-bpf-owned": PREFLIGHT_PROFILE_OPERATIONAL_CLEAN_BPF_OWNED,
        "mpls": PREFLIGHT_PROFILE_LEGACY_MPLS,
        "linux-mpls": PREFLIGHT_PROFILE_LEGACY_MPLS,
    }
    value = aliases.get(value, value)
    if value not in PREFLIGHT_PROFILES:
        raise ValueError(
            "unknown Skyseed preflight profile "
            f"{profile!r}; expected one of {', '.join(PREFLIGHT_PROFILES)}"
        )
    return value


def _docker_container_state() -> Dict[str, Any]:
    inspect = _run(
        [
            "docker",
            "ps",
            "-a",
            "--format",
            "{{.Names}}\t{{.Status}}\t{{.Label \"com.docker.compose.project\"}}",
        ]
    )
    containers: List[Dict[str, Any]] = []
    compose_project_containers: List[Dict[str, Any]] = []
    named_skyseed_containers: List[Dict[str, Any]] = []
    state_counts = {"running": 0, "exited": 0, "other": 0}
    compose_project_state_counts = {
        "running": 0,
        "exited": 0,
        "other": 0,
    }
    if inspect.returncode == 0:
        for line in inspect.stdout.splitlines():
            if not line.strip():
                continue
            parts = line.split("\t")
            name = parts[0].strip()
            status = parts[1].strip() if len(parts) > 1 else ""
            project = parts[2].strip() if len(parts) > 2 else ""
            state = "running" if status.lower().startswith("up") else "exited" if status.lower().startswith("exited") else "other"
            state_counts[state] += 1
            entry = {
                "name": name,
                "status": status,
                "compose_project": project or None,
                "state": state,
            }
            containers.append(entry)
            if project == SKYSEED_COMPOSE_PROJECT:
                compose_project_containers.append(entry)
                compose_project_state_counts[state] = (
                    int(compose_project_state_counts.get(state, 0)) + 1
                )
            elif _matches_any(name, SKYSEED_CONTAINER_PATTERNS):
                named_skyseed_containers.append(entry)
    return {
        "command": ["docker", "ps", "-a", "--format", "{{.Names}}\\t{{.Status}}\\t{{.Label \"com.docker.compose.project\"}}"],
        "ok": inspect.returncode == 0,
        "returncode": inspect.returncode,
        "stderr": inspect.stderr.strip(),
        "state_counts": state_counts,
        "compose_project_state_counts": compose_project_state_counts,
        "compose_project_containers": compose_project_containers,
        "named_skyseed_containers": named_skyseed_containers,
    }


def _docker_container_state_project_scoped() -> Dict[str, Any]:
    inspect = _run(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            f"label=com.docker.compose.project={SKYSEED_COMPOSE_PROJECT}",
            "--format",
            "{{.Names}}\t{{.Status}}\t{{.Label \"com.docker.compose.project\"}}",
        ]
    )
    compose_project_containers: List[Dict[str, Any]] = []
    compose_project_state_counts = {
        "running": 0,
        "exited": 0,
        "other": 0,
    }
    if inspect.returncode == 0:
        for line in inspect.stdout.splitlines():
            if not line.strip():
                continue
            parts = line.split("\t")
            name = parts[0].strip()
            status = parts[1].strip() if len(parts) > 1 else ""
            project = parts[2].strip() if len(parts) > 2 else ""
            state = (
                "running"
                if status.lower().startswith("up")
                else "exited"
                if status.lower().startswith("exited")
                else "other"
            )
            compose_project_state_counts[state] = (
                int(compose_project_state_counts.get(state, 0)) + 1
            )
            compose_project_containers.append(
                {
                    "name": name,
                    "status": status,
                    "compose_project": project or SKYSEED_COMPOSE_PROJECT,
                    "state": state,
                }
            )
    return {
        "command": [
            "docker",
            "ps",
            "-a",
            "--filter",
            f"label=com.docker.compose.project={SKYSEED_COMPOSE_PROJECT}",
            "--format",
            "{{.Names}}\\t{{.Status}}\\t{{.Label \"com.docker.compose.project\"}}",
        ],
        "ok": inspect.returncode == 0,
        "returncode": inspect.returncode,
        "stderr": inspect.stderr.strip(),
        "scope": "compose_project_label",
        "full_docker_container_scan": False,
        "state_counts": dict(compose_project_state_counts),
        "compose_project_state_counts": compose_project_state_counts,
        "compose_project_containers": compose_project_containers,
        "named_skyseed_containers": [],
    }


def _docker_network_state() -> Dict[str, Any]:
    listing = _run(
        [
            "docker",
            "network",
            "ls",
            "--format",
            "{{.Name}}\t{{.Label \"com.docker.compose.project\"}}",
        ]
    )
    compose_project_networks: List[Dict[str, Any]] = []
    named_skyseed_networks: List[Dict[str, Any]] = []
    attachment_count = 0
    attachment_failures: List[Dict[str, Any]] = []
    if listing.returncode == 0:
        for line in listing.stdout.splitlines():
            if not line.strip():
                continue
            parts = line.split("\t")
            name = parts[0].strip()
            project = parts[1].strip() if len(parts) > 1 else ""
            if name in {"bridge", "host", "none"}:
                continue
            inspect = _run(["docker", "network", "inspect", name, "--format", "{{json .Containers}}"])
            attached = 0
            if inspect.returncode == 0:
                try:
                    payload = json.loads(inspect.stdout.strip() or "null")
                    if isinstance(payload, dict):
                        attached = len(payload)
                except json.JSONDecodeError:
                    attachment_failures.append({"network_name": name, "stderr": "json_decode_failed"})
            else:
                attachment_failures.append({"network_name": name, "stderr": inspect.stderr.strip()})
            attachment_count += attached
            entry = {
                "name": name,
                "compose_project": project or None,
                "attachment_count": attached,
            }
            if project == SKYSEED_COMPOSE_PROJECT:
                compose_project_networks.append(entry)
            elif _matches_any(name, SKYSEED_NETWORK_PATTERNS):
                named_skyseed_networks.append(entry)
    return {
        "command": ["docker", "network", "ls", "--format", "{{.Name}}\\t{{.Label \"com.docker.compose.project\"}}"],
        "ok": listing.returncode == 0,
        "returncode": listing.returncode,
        "stderr": listing.stderr.strip(),
        "compose_project_networks": compose_project_networks,
        "named_skyseed_networks": named_skyseed_networks,
        "attachment_count": attachment_count,
        "attachment_failures": attachment_failures,
    }


def _docker_network_state_project_scoped() -> Dict[str, Any]:
    listing = _run(
        [
            "docker",
            "network",
            "ls",
            "--filter",
            f"label=com.docker.compose.project={SKYSEED_COMPOSE_PROJECT}",
            "--format",
            "{{.Name}}\t{{.Label \"com.docker.compose.project\"}}",
        ]
    )
    compose_project_networks: List[Dict[str, Any]] = []
    if listing.returncode == 0:
        for line in listing.stdout.splitlines():
            if not line.strip():
                continue
            parts = line.split("\t")
            name = parts[0].strip()
            project = parts[1].strip() if len(parts) > 1 else ""
            if name:
                compose_project_networks.append(
                    {
                        "name": name,
                        "compose_project": project or SKYSEED_COMPOSE_PROJECT,
                        "attachment_count": None,
                        "attachment_count_skipped": True,
                    }
                )
    return {
        "command": [
            "docker",
            "network",
            "ls",
            "--filter",
            f"label=com.docker.compose.project={SKYSEED_COMPOSE_PROJECT}",
            "--format",
            "{{.Name}}\\t{{.Label \"com.docker.compose.project\"}}",
        ],
        "ok": listing.returncode == 0,
        "returncode": listing.returncode,
        "stderr": listing.stderr.strip(),
        "scope": "compose_project_label",
        "full_docker_network_scan": False,
        "network_inspect_skipped": True,
        "compose_project_networks": compose_project_networks,
        "named_skyseed_networks": [],
        "attachment_count": 0,
        "attachment_failures": [],
    }


def _compose_processes(*, ignore_pids: Sequence[int] | None = None) -> Dict[str, Any]:
    completed = _run(["ps", "-eo", "pid=,ppid=,etimes=,args="])
    processes: List[Dict[str, Any]] = []
    ignored = {int(pid) for pid in (ignore_pids or [])}
    if completed.returncode == 0:
        for line in completed.stdout.splitlines():
            line = line.rstrip()
            if not line:
                continue
            parts = line.split(None, 3)
            if len(parts) < 4:
                continue
            pid = int(parts[0])
            ppid = int(parts[1])
            etimes = int(parts[2])
            args = parts[3]
            if pid in ignored:
                continue
            if "docker-compose" in args or "run_starlink_shell_experiment.py" in args or "run_starlink_shell_runtime_attempt.py" in args:
                if "rg " in args or "ps -eo" in args or "skyseed_preflight" in args:
                    continue
                processes.append(
                    {
                        "pid": pid,
                        "ppid": ppid,
                        "elapsed_seconds": etimes,
                        "args": args,
                    }
                )
    return {
        "command": ["ps", "-eo", "pid=,ppid=,etimes=,args="],
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stderr": completed.stderr.strip(),
        "processes": processes,
    }


def _direct_backend_host_artifacts() -> Dict[str, Any]:
    completed = _run(["ip", "-o", "link", "show"])
    interfaces: List[str] = []
    if completed.returncode == 0:
        for line in completed.stdout.splitlines():
            match = re.match(r"^\d+:\s+([^:@]+)", line)
            if not match:
                continue
            name = match.group(1).strip()
            if _matches_any(name, DIRECT_BACKEND_HOST_INTERFACE_PATTERNS):
                interfaces.append(name)
    return {
        "command": ["ip", "-o", "link", "show"],
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stderr": completed.stderr.strip(),
        "interfaces": sorted(set(interfaces)),
    }


def _bpf_pinned_map_artifacts() -> Dict[str, Any]:
    maps: List[str] = []
    checked_paths: List[str] = []
    for map_name in SKYSEED_BPF_PINNED_MAPS:
        path = BPF_PIN_BASE / map_name
        checked_paths.append(str(path))
        completed = _run(["sudo", "-n", "test", "-e", str(path)])
        if completed.returncode == 0:
            maps.append(map_name)
    return {
        "pin_base": str(BPF_PIN_BASE),
        "map_names": sorted(maps),
        "checked_paths": checked_paths,
        "ok": True,
    }


def direct_backend_host_artifacts_report() -> Dict[str, Any]:
    """Public wrapper for scoped direct-backend residue inspection."""

    return _direct_backend_host_artifacts()


def _skipped_diagnostic_check(*, name: str, result_key: str) -> Dict[str, Any]:
    return {
        "ok": True,
        "skipped": True,
        "skip_reason": "operational_clean_lightweight_preflight_defers_global_diagnostic",
        "diagnostic_only": True,
        result_key: [],
    }


def cleanup_direct_backend_host_artifacts(
    interface_names: Sequence[str] | None = None,
    progress_callback: Callable[[Dict[str, Any]], None] | None = None,
) -> Dict[str, Any]:
    """Delete only Skyseed direct-backend host artifacts matched by stable name patterns."""

    before = _direct_backend_host_artifacts()
    discovered_names = list(before.get("interfaces", []))
    if interface_names is None:
        target_names = list(discovered_names)
    else:
        target_names = sorted(
            {
                str(name)
                for name in interface_names
                if _matches_any(str(name), DIRECT_BACKEND_HOST_INTERFACE_PATTERNS)
            }
        )
    removed: List[str] = []
    skipped_absent: List[str] = []
    failed: List[Dict[str, Any]] = []
    total = len(target_names)
    if progress_callback is not None:
        progress_callback(
            {
                "phase": "direct_backend_host_interface_cleanup",
                "completed_interface_count": 0,
                "total_interface_count": total,
                "removed_interface_count": 0,
                "skipped_absent_interface_count": 0,
                "failed_interface_removal_count": 0,
            }
        )
    for index, name in enumerate(target_names, start=1):
        completed = _run(["sudo", "-n", "ip", "link", "del", name])
        if completed.returncode == 0:
            removed.append(name)
        else:
            stderr = completed.stderr.strip()
            if "Cannot find device" in stderr:
                skipped_absent.append(name)
            else:
                failed.append(
                    {
                        "interface_name": name,
                        "returncode": completed.returncode,
                        "stderr": stderr,
                    }
                )
        if progress_callback is not None:
            progress_callback(
                {
                    "phase": "direct_backend_host_interface_cleanup",
                    "interface_name": name,
                    "completed_interface_count": index,
                    "total_interface_count": total,
                    "removed_interface_count": len(removed),
                    "skipped_absent_interface_count": len(skipped_absent),
                    "failed_interface_removal_count": len(failed),
                }
            )
    after = _direct_backend_host_artifacts()
    remaining = list(after.get("interfaces", []))
    return {
        "scope": "skyseed_only_direct_backend_host_interfaces",
        "requested_interface_names": target_names,
        "initial_interface_count": len(discovered_names),
        "requested_interface_count": len(target_names),
        "removed_interface_names": removed,
        "skipped_absent_interface_names": skipped_absent,
        "failed_interface_removals": failed,
        "remaining_interface_names": remaining,
        "removed_interface_count": len(removed),
        "remaining_interface_count": len(remaining),
        "all_removed": not remaining and not failed,
    }


def _runtime_lock_path_allowed(path: Path) -> bool:
    try:
        resolved = path.expanduser().resolve(strict=False)
    except OSError:
        return False
    roots = (
        DEFAULT_RUN_ROOT.resolve(strict=False),
        DEFAULT_STATUS_DIR.resolve(strict=False),
    )
    return any(resolved == root or root in resolved.parents for root in roots)


def _cleanup_command_result(
    *,
    command: Sequence[str],
    apply: bool,
) -> Dict[str, Any]:
    if not apply:
        return {
            "command": list(command),
            "planned": True,
            "executed": False,
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "ok": None,
        }
    completed = _run(command)
    return {
        "command": list(command),
        "planned": True,
        "executed": True,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
        "ok": completed.returncode == 0,
    }


def _cleanup_compose_networks_with_stale_endpoints(
    network_names: Sequence[str],
    *,
    apply: bool,
) -> List[Dict[str, Any]]:
    """Remove scoped compose networks, including Docker's stale endpoint-name case."""

    names = sorted({str(name) for name in network_names if str(name)})
    if not names:
        return []
    results: List[Dict[str, Any]] = []
    initial = _cleanup_command_result(
        command=["docker", "network", "rm", *names],
        apply=apply,
    )
    results.append(initial)
    if (
        not apply
        or initial.get("ok") is True
        or "active endpoints" not in str(initial.get("stderr") or "")
    ):
        return results

    for network_name in names:
        inspect = _run(["docker", "network", "inspect", network_name])
        inspect_result: Dict[str, Any] = {
            "command": ["docker", "network", "inspect", network_name],
            "planned": True,
            "executed": True,
            "returncode": inspect.returncode,
            "stdout": inspect.stdout.strip(),
            "stderr": inspect.stderr.strip(),
            "ok": inspect.returncode == 0,
            "stale_endpoint_disconnect": True,
        }
        results.append(inspect_result)
        if inspect.returncode != 0:
            continue
        try:
            payload = json.loads(inspect.stdout or "[]")
        except json.JSONDecodeError as exc:
            results[-1]["ok"] = False
            results[-1]["stderr"] = f"json_decode_failed:{exc}"
            continue
        network = payload[0] if isinstance(payload, list) and payload else {}
        containers = dict(network.get("Containers") or {})
        endpoint_names = sorted(
            {
                str(meta.get("Name") or container_id)
                for container_id, meta in containers.items()
                if str(meta.get("Name") or container_id)
            }
        )
        for endpoint_name in endpoint_names:
            disconnect = _run(
                ["docker", "network", "disconnect", "-f", network_name, endpoint_name]
            )
            results.append(
                {
                    "command": [
                        "docker",
                        "network",
                        "disconnect",
                        "-f",
                        network_name,
                        endpoint_name,
                    ],
                    "planned": True,
                    "executed": True,
                    "returncode": disconnect.returncode,
                    "stdout": disconnect.stdout.strip(),
                    "stderr": disconnect.stderr.strip(),
                    "ok": disconnect.returncode == 0,
                    "stale_endpoint_disconnect": True,
                }
            )

    retry = _cleanup_command_result(
        command=["docker", "network", "rm", *names],
        apply=apply,
    )
    retry["retry_after_stale_endpoint_disconnect"] = True
    results.append(retry)
    return results


def _cleanup_resource_names(
    entries: Sequence[Dict[str, Any]],
    *,
    patterns: Sequence[re.Pattern[str]] | None = None,
) -> List[str]:
    names = sorted({str(entry.get("name") or "") for entry in entries if entry.get("name")})
    if patterns is None:
        return names
    return [name for name in names if _matches_any(name, patterns)]


def cleanup_skyseed_environment_residue(
    *,
    preflight_report: Dict[str, Any] | None = None,
    apply: bool = False,
    include_runtime_locks: bool = True,
) -> Dict[str, Any]:
    """Plan or remove only scoped Skyseed runtime residue discovered by preflight."""

    report = preflight_report if preflight_report is not None else run_skyseed_preflight()
    checks = dict(report.get("checks") or {})
    docker_state = dict(checks.get("docker_state") or {})
    containers = dict(docker_state.get("containers") or {})
    networks = dict(docker_state.get("networks") or {})
    direct_state = dict(checks.get("direct_backend_host_artifacts") or {})
    bpf_pinned_state = dict(checks.get("bpf_pinned_map_artifacts") or {})
    lock_state = dict(checks.get("runtime_lock_files") or {})

    compose_project_containers = _cleanup_resource_names(
        list(containers.get("compose_project_containers") or [])
    )
    named_containers = _cleanup_resource_names(
        list(containers.get("named_skyseed_containers") or []),
        patterns=SKYSEED_CONTAINER_PATTERNS,
    )
    container_names = sorted(set(compose_project_containers + named_containers))
    compose_project_networks = _cleanup_resource_names(
        list(networks.get("compose_project_networks") or [])
    )
    named_networks = _cleanup_resource_names(
        list(networks.get("named_skyseed_networks") or []),
        patterns=SKYSEED_NETWORK_PATTERNS,
    )
    network_names = sorted(set(compose_project_networks + named_networks))
    interface_names = sorted(
        {
            str(name)
            for name in list(direct_state.get("interfaces") or [])
            if _matches_any(str(name), DIRECT_BACKEND_HOST_INTERFACE_PATTERNS)
        }
    )
    lock_paths = [
        str(Path(path).expanduser().resolve(strict=False))
        for path in list(lock_state.get("files") or [])
        if include_runtime_locks and _runtime_lock_path_allowed(Path(path))
    ]
    bpf_pinned_map_names = sorted(
        {
            str(name)
            for name in list(bpf_pinned_state.get("map_names") or [])
            if str(name) in SKYSEED_BPF_PINNED_MAPS
        }
    )

    command_results: List[Dict[str, Any]] = []
    if container_names:
        command_results.append(
            _cleanup_command_result(
                command=["docker", "rm", "-f", *container_names],
                apply=apply,
            )
        )
    if network_names:
        command_results.extend(
            _cleanup_compose_networks_with_stale_endpoints(
                network_names,
                apply=apply,
            )
        )
    if interface_names:
        if apply:
            interface_cleanup = cleanup_direct_backend_host_artifacts(interface_names)
            command_results.extend(
                {
                    "command": ["sudo", "-n", "ip", "link", "del", name],
                    "planned": True,
                    "executed": True,
                    "returncode": 0 if name in interface_cleanup.get("removed_interface_names", []) else None,
                    "stdout": "",
                    "stderr": "",
                    "ok": name in interface_cleanup.get("removed_interface_names", [])
                    or name in interface_cleanup.get("skipped_absent_interface_names", []),
                }
                for name in interface_names
            )
        else:
            command_results.extend(
                _cleanup_command_result(
                    command=["sudo", "-n", "ip", "link", "del", name],
                    apply=False,
                )
                for name in interface_names
            )
    if bpf_pinned_map_names:
        command_results.append(
            _cleanup_command_result(
                command=[
                    "sudo",
                    "-n",
                    "rm",
                    "-f",
                    *[
                        str(BPF_PIN_BASE / map_name)
                        for map_name in bpf_pinned_map_names
                    ],
                ],
                apply=apply,
            )
        )
    removed_lock_paths: List[str] = []
    failed_lock_paths: List[Dict[str, Any]] = []
    for lock_path in lock_paths:
        command_results.append(
            _cleanup_command_result(
                command=["rm", "-f", lock_path],
                apply=False,
            )
        )
        if apply:
            try:
                Path(lock_path).unlink(missing_ok=True)
                removed_lock_paths.append(lock_path)
            except OSError as exc:
                failed_lock_paths.append({"path": lock_path, "error": str(exc)})
            command_results[-1].update(
                {
                    "executed": True,
                    "returncode": 0 if lock_path in removed_lock_paths else 1,
                    "ok": lock_path in removed_lock_paths,
                    "stderr": ""
                    if lock_path in removed_lock_paths
                    else (failed_lock_paths[-1]["error"] if failed_lock_paths else ""),
                }
            )

    executed_results = [result for result in command_results if result.get("executed")]
    failed_results: List[Dict[str, Any]] = []
    for index, result in enumerate(executed_results):
        if result.get("ok") is True:
            continue
        command_key = tuple(str(part) for part in list(result.get("command") or []))
        later_success = any(
            tuple(str(part) for part in list(later.get("command") or []))
            == command_key
            and later.get("ok") is True
            for later in executed_results[index + 1 :]
        )
        if not later_success:
            failed_results.append(result)
    return {
        "scope": "skyseed_only",
        "dry_run": not apply,
        "applied": bool(apply),
        "resource_counts": {
            "containers": len(container_names),
            "networks": len(network_names),
            "direct_backend_host_interfaces": len(interface_names),
            "bpf_pinned_maps": len(bpf_pinned_map_names),
            "runtime_lock_files": len(lock_paths),
        },
        "resource_names": {
            "containers": container_names,
            "networks": network_names,
            "direct_backend_host_interfaces": interface_names,
            "bpf_pinned_maps": bpf_pinned_map_names,
            "runtime_lock_files": lock_paths,
        },
        "command_results": command_results,
        "removed_runtime_lock_files": removed_lock_paths,
        "failed_runtime_lock_files": failed_lock_paths,
        "success": not failed_results,
    }


def _runtime_lock_files() -> Dict[str, Any]:
    files: List[str] = []
    for root in (DEFAULT_RUN_ROOT, DEFAULT_STATUS_DIR):
        if not root.exists():
            continue
        for pattern in LOCK_FILE_PATTERNS:
            for path in root.rglob(pattern):
                if path.is_file():
                    files.append(str(path))
    return {
        "searched_roots": [str(DEFAULT_RUN_ROOT), str(DEFAULT_STATUS_DIR)],
        "files": sorted(set(files)),
    }


def _disk_space() -> Dict[str, Any]:
    usage = shutil.disk_usage(WORKSPACE_ROOT)
    return {
        "workspace_root": str(WORKSPACE_ROOT),
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
    }


def _bridge_netfilter() -> Dict[str, Any]:
    probes = {
        key: _sysctl_probe(key)
        for key in BRIDGE_NETFILTER_SYSCTLS
    }
    not_passthrough = [
        key
        for key, probe in probes.items()
        if probe.get("int_value") != 0
    ]
    return {
        "sysctls": probes,
        "passthrough_ready": not not_passthrough,
        "blocking_sysctls": not_passthrough,
    }


def _skipped_host_mpls_readiness(*, profile: str) -> Dict[str, Any]:
    return {
        "requirements": {
            "host_kernel_modules": [],
            "host_sysctls": [],
            "iproute2_features": [],
        },
        "host": {
            "modules": {},
            "sysctls": {},
            "iproute2": {},
        },
        "assessment": {
            "ready": None,
            "skipped": True,
            "skip_reason": f"{profile}_does_not_require_host_mpls_preflight",
            "blocking_reasons": [],
            "recommended_next_steps": [
                "Host MPLS readiness was skipped for the BPF-owned operational-clean profile.",
            ],
        },
    }


def _python_runtime() -> Dict[str, Any]:
    current_executable = Path(sys.executable).resolve()
    preferred_exists = PREFERRED_SEED310_PYTHON.exists()
    preferred_selected = (
        preferred_exists
        and (
            current_executable == PREFERRED_SEED310_PYTHON.resolve()
            or str(current_executable).startswith(str(PREFERRED_SEED310_PYTHON.parent.resolve()))
        )
    )
    version_info = tuple(int(part) for part in sys.version_info[:3])
    pythonpath_entries = [
        entry
        for entry in str(os.environ.get("PYTHONPATH", "")).split(":")
        if entry.strip()
    ]
    normalized_pythonpath_entries = [
        str(Path(entry).expanduser().resolve(strict=False))
        for entry in pythonpath_entries
    ]
    legacy_seed_override_present = (
        LEGACY_SEED_EMULATOR_OVERRIDE in normalized_pythonpath_entries
    )
    return {
        "current_executable": str(current_executable),
        "current_version": ".".join(str(part) for part in version_info),
        "supported_version": version_info >= MIN_SUPPORTED_PYTHON,
        "minimum_supported_version": ".".join(str(part) for part in MIN_SUPPORTED_PYTHON),
        "preferred_seed310_python": str(PREFERRED_SEED310_PYTHON),
        "preferred_seed310_exists": preferred_exists,
        "preferred_seed310_selected": preferred_selected,
        "pythonpath_entries": pythonpath_entries,
        "legacy_seed_emulator_pythonpath_override_present": (
            legacy_seed_override_present
        ),
        "legacy_seed_emulator_override_path": LEGACY_SEED_EMULATOR_OVERRIDE,
    }


def _find_compose_outputs() -> List[str]:
    matches: List[str] = []
    if not DEFAULT_RUN_ROOT.exists():
        return matches
    for path in DEFAULT_RUN_ROOT.rglob(SKYSEED_COMPOSE_FILE_NAME):
        matches.append(str(path))
    return sorted(matches)


def _cleanup_suggestions(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    suggestions: List[Dict[str, Any]] = []
    docker_state = report["checks"]["docker_state"]
    process_state = report["checks"]["compose_processes"]
    direct_state = report["checks"]["direct_backend_host_artifacts"]
    lock_state = report["checks"]["runtime_lock_files"]
    bpf_state = report["checks"]["bpf_pinned_map_artifacts"]

    compose_containers = docker_state["containers"].get("compose_project_containers", [])
    named_containers = docker_state["containers"].get("named_skyseed_containers", [])
    compose_networks = docker_state["networks"].get("compose_project_networks", [])
    named_networks = docker_state["networks"].get("named_skyseed_networks", [])
    if compose_containers or compose_networks:
        suggestions.append(
            {
                "id": "compose_project_cleanup",
                "scope": "skyseed_only",
                "reason": "Docker resources with compose project label seed_output are present.",
                "commands": [
                    "docker ps -aq --filter 'label=com.docker.compose.project=seed_output'",
                    "docker network ls -q --filter 'label=com.docker.compose.project=seed_output'",
                    "docker rm -f $(docker ps -aq --filter 'label=com.docker.compose.project=seed_output')",
                    "docker network rm $(docker network ls -q --filter 'label=com.docker.compose.project=seed_output')",
                ],
                "resource_counts": {
                    "containers": len(compose_containers),
                    "networks": len(compose_networks),
                },
            }
        )
    if named_containers:
        suggestions.append(
            {
                "id": "named_skyseed_container_cleanup",
                "scope": "skyseed_only",
                "reason": "Named Skyseed containers exist outside the current compose project label set.",
                "commands": [
                    "docker rm -f " + " ".join(sorted(entry["name"] for entry in named_containers))
                ],
                "resource_names": [entry["name"] for entry in named_containers],
            }
        )
    if named_networks:
        suggestions.append(
            {
                "id": "named_skyseed_network_cleanup",
                "scope": "skyseed_only",
                "reason": "Named Skyseed slot/runtime networks exist without compose project labels.",
                "commands": [
                    "docker network rm " + " ".join(sorted(entry["name"] for entry in named_networks))
                ],
                "resource_names": [entry["name"] for entry in named_networks],
            }
        )
    if process_state.get("processes"):
        commands = [f"kill -TERM {entry['pid']}" for entry in process_state["processes"]]
        suggestions.append(
            {
                "id": "compose_process_cleanup",
                "scope": "skyseed_only",
                "reason": "Potentially stale Skyseed compose/runtime processes are still running.",
                "commands": commands,
                "processes": process_state["processes"],
            }
        )
    if direct_state.get("interfaces"):
        suggestions.append(
            {
                "id": "direct_backend_interface_cleanup",
                "scope": "skyseed_only",
                "reason": "Direct-backend host bridges or veths remain on the host.",
                "commands": [
                    f"sudo ip link del {name}"
                    for name in direct_state["interfaces"]
                ],
                "interface_names": list(direct_state["interfaces"]),
            }
        )
    if lock_state.get("files"):
        suggestions.append(
            {
                "id": "runtime_lock_cleanup",
                "scope": "skyseed_only",
                "reason": "Skyseed runtime lock or pid files are present under satellite/output.",
                "commands": [
                    "rm -f " + " ".join(lock_state["files"])
                ],
                "file_paths": list(lock_state["files"]),
            }
        )
    if bpf_state.get("map_names"):
        suggestions.append(
            {
                "id": "bpf_pinned_map_cleanup",
                "scope": "skyseed_only_bpf_pinned_maps",
                "reason": "Skyseed BPF maps remain pinned under /sys/fs/bpf/tc/globals.",
                "commands": [
                    "sudo rm -f "
                    + " ".join(
                        str(BPF_PIN_BASE / name)
                        for name in list(bpf_state.get("map_names") or [])
                    )
                ],
                "map_names": list(bpf_state.get("map_names") or []),
            }
        )
    return suggestions


def _skyseed_cleanliness_counts(checks: Dict[str, Any]) -> Dict[str, int]:
    docker_state = dict(checks.get("docker_state") or {})
    containers = dict(docker_state.get("containers") or {})
    networks = dict(docker_state.get("networks") or {})
    return {
        "compose_project_containers": len(
            list(containers.get("compose_project_containers") or [])
        ),
        "named_skyseed_containers": len(
            list(containers.get("named_skyseed_containers") or [])
        ),
        "compose_project_networks": len(
            list(networks.get("compose_project_networks") or [])
        ),
        "named_skyseed_networks": len(
            list(networks.get("named_skyseed_networks") or [])
        ),
        "direct_backend_host_interfaces": len(
            list(
                dict(checks.get("direct_backend_host_artifacts") or {}).get(
                    "interfaces",
                    [],
                )
            )
        ),
        "runtime_lock_files": len(
            list(dict(checks.get("runtime_lock_files") or {}).get("files", []))
        ),
        "bpf_pinned_maps": len(
            list(
                dict(checks.get("bpf_pinned_map_artifacts") or {}).get(
                    "map_names",
                    [],
                )
            )
        ),
        "compose_or_runtime_processes": len(
            list(dict(checks.get("compose_processes") or {}).get("processes", []))
        ),
    }


def _assess_skyseed_preflight(
    checks: Dict[str, Any],
    *,
    profile: str,
) -> Dict[str, Any]:
    profile = _normalize_preflight_profile(profile)
    docker_toolchain = dict(checks.get("docker_toolchain") or {})
    docker_state = dict(checks.get("docker_state") or {})
    containers = dict(docker_state.get("containers") or {})
    disk_space = dict(checks.get("disk_space") or {})
    mpls = dict(checks.get("host_mpls") or {})
    bridge_netfilter = dict(checks.get("bridge_netfilter") or {})
    python_runtime = dict(checks.get("python_runtime") or {})
    compose_processes = dict(checks.get("compose_processes") or {})
    cleanliness_counts = _skyseed_cleanliness_counts(checks)
    clean_residue_reasons = [
        name for name, count in cleanliness_counts.items() if int(count) > 0
    ]

    warnings: List[str] = []
    blocking: List[str] = []

    if not dict(docker_toolchain.get("docker") or {}).get("ok"):
        blocking.append("docker_unavailable")
    if not dict(docker_toolchain.get("docker_compose") or {}).get("ok"):
        blocking.append("docker_compose_unavailable")
    if compose_processes.get("processes"):
        blocking.append("skyseed_compose_or_runtime_processes_present")

    compose_project_state_counts = dict(
        containers.get("compose_project_state_counts", {})
    )
    if cleanliness_counts["compose_project_containers"] or cleanliness_counts[
        "compose_project_networks"
    ]:
        warnings.append("seed_output_compose_resources_present")
    if cleanliness_counts["named_skyseed_containers"]:
        warnings.append("named_skyseed_containers_present")
    if (
        int(compose_project_state_counts.get("exited", 0)) > 0
        or int(compose_project_state_counts.get("other", 0)) > 0
    ):
        warnings.append("seed_output_partial_container_state_present")
    if cleanliness_counts["named_skyseed_networks"]:
        warnings.append("named_skyseed_networks_present")
    if cleanliness_counts["direct_backend_host_interfaces"]:
        warnings.append("direct_backend_host_interfaces_present")
    if cleanliness_counts["runtime_lock_files"]:
        warnings.append("runtime_lock_files_present")
    if cleanliness_counts["bpf_pinned_maps"]:
        warnings.append("bpf_pinned_maps_present")

    free_bytes = int(disk_space.get("free_bytes") or 0)
    if free_bytes < LOW_DISK_ERROR_BYTES:
        blocking.append("disk_space_critically_low")
    elif free_bytes < LOW_DISK_WARN_BYTES:
        warnings.append("disk_space_low")

    host_mpls_assessment = dict(mpls.get("assessment") or {})
    if host_mpls_assessment.get("skipped") is True:
        warnings.append("host_mpls_preflight_skipped")
    elif host_mpls_assessment.get("ready") is not True:
        if profile == PREFLIGHT_PROFILE_LEGACY_MPLS:
            blocking.append("host_mpls_not_ready")
        else:
            warnings.append("host_mpls_not_ready")

    if bridge_netfilter.get("passthrough_ready") is not True:
        warnings.append("bridge_netfilter_passthrough_not_ready")
    if python_runtime.get("supported_version") is not True:
        warnings.append("python_version_too_old_for_seedemu_runtime")
    if (
        python_runtime.get("preferred_seed310_exists") is True
        and python_runtime.get("preferred_seed310_selected") is not True
    ):
        warnings.append("not_running_under_preferred_seed310_python")
    if python_runtime.get("legacy_seed_emulator_pythonpath_override_present") is True:
        warnings.append("legacy_seed_emulator_pythonpath_override_present")

    clean_environment_required = profile == PREFLIGHT_PROFILE_OPERATIONAL_CLEAN_BPF_OWNED
    if clean_environment_required and clean_residue_reasons:
        blocking.append("skyseed_environment_not_clean")

    return {
        "profile": profile,
        "ready": not blocking,
        "clean_environment_required": clean_environment_required,
        "environment_clean": not clean_residue_reasons,
        "cleanliness_counts": cleanliness_counts,
        "clean_residue_reasons": clean_residue_reasons,
        "blocking_reasons": blocking,
        "warning_reasons": warnings,
    }


def run_skyseed_preflight(
    *,
    ignore_pids: Sequence[int] | None = None,
    profile: str = PREFLIGHT_PROFILE_DEFAULT,
) -> Dict[str, Any]:
    profile = _normalize_preflight_profile(profile)
    operational_clean_lightweight = (
        profile == PREFLIGHT_PROFILE_OPERATIONAL_CLEAN_BPF_OWNED
    )
    docker_version = _run_jsonable(["docker", "--version"])
    docker_compose_version = _run_jsonable(["docker-compose", "version"])
    docker_state = {
        "containers": (
            _docker_container_state_project_scoped()
            if operational_clean_lightweight
            else _docker_container_state()
        ),
        "networks": (
            _docker_network_state_project_scoped()
            if operational_clean_lightweight
            else _docker_network_state()
        ),
    }
    compose_processes = _compose_processes(ignore_pids=ignore_pids)
    direct_backend = (
        _skipped_diagnostic_check(
            name="direct_backend_host_artifacts",
            result_key="interfaces",
        )
        if operational_clean_lightweight
        else _direct_backend_host_artifacts()
    )
    bpf_pinned_maps = (
        _skipped_diagnostic_check(
            name="bpf_pinned_map_artifacts",
            result_key="map_names",
        )
        if operational_clean_lightweight
        else _bpf_pinned_map_artifacts()
    )
    runtime_locks = _runtime_lock_files()
    disk_space = _disk_space()
    if operational_clean_lightweight:
        mpls = _skipped_host_mpls_readiness(profile=profile)
    else:
        mpls = probe_host_mpls_readiness()
    bridge_netfilter = _bridge_netfilter()
    python_runtime = _python_runtime()

    report = {
        "generated_at_utc": _timestamp_utc(),
        "workspace_root": str(WORKSPACE_ROOT),
        "profile": profile,
        "diagnostic_policy": (
            {
                "mode": "operational_clean_lightweight",
                "global_diagnostics_deferred": True,
                "docker_scope": "compose_project_label",
                "direct_backend_global_scan": "deferred",
                "bpf_pin_global_scan": "deferred",
                "known_compose_outputs_scan": "deferred",
            }
            if operational_clean_lightweight
            else {
                "mode": "default_full_diagnostic",
                "global_diagnostics_deferred": False,
            }
        ),
        "checks": {
            "docker_toolchain": {
                "docker": docker_version,
                "docker_compose": docker_compose_version,
            },
            "docker_state": docker_state,
            "compose_processes": compose_processes,
            "direct_backend_host_artifacts": direct_backend,
            "bpf_pinned_map_artifacts": bpf_pinned_maps,
            "runtime_lock_files": runtime_locks,
            "disk_space": disk_space,
            "python_runtime": python_runtime,
            "host_mpls": mpls,
            "bridge_netfilter": bridge_netfilter,
            "known_compose_outputs": (
                []
                if operational_clean_lightweight
                else _find_compose_outputs()
            ),
        },
    }
    report["assessment"] = _assess_skyseed_preflight(
        report["checks"],
        profile=profile,
    )
    report["cleanup_suggestions"] = _cleanup_suggestions(report)
    return report


def render_preflight_json(report: Dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True) + "\n"


def render_preflight_summary(report: Dict[str, Any]) -> str:
    assessment = report.get("assessment", {})
    docker_state = report["checks"]["docker_state"]
    disk = report["checks"]["disk_space"]
    lines = [
        "Skyseed preflight",
        f"  profile={assessment.get('profile', report.get('profile', PREFLIGHT_PROFILE_DEFAULT))}",
        f"  ready={assessment.get('ready')}",
        f"  clean_environment_required={assessment.get('clean_environment_required')}",
        f"  environment_clean={assessment.get('environment_clean')}",
        "  blocking_reasons="
        + (", ".join(assessment.get("blocking_reasons", [])) or "none"),
        "  warning_reasons="
        + (", ".join(assessment.get("warning_reasons", [])) or "none"),
        "  python_executable="
        + str(report["checks"]["python_runtime"].get("current_executable")),
        "  python_version="
        + str(report["checks"]["python_runtime"].get("current_version")),
        "  preferred_seed310_selected="
        + str(report["checks"]["python_runtime"].get("preferred_seed310_selected")),
        "  legacy_seed_pythonpath_override="
        + str(
            report["checks"]["python_runtime"].get(
                "legacy_seed_emulator_pythonpath_override_present"
            )
        ),
        f"  compose_project_containers={len(docker_state['containers'].get('compose_project_containers', []))}",
        f"  named_skyseed_containers={len(docker_state['containers'].get('named_skyseed_containers', []))}",
        "  compose_project_container_states="
        + json.dumps(docker_state["containers"].get("compose_project_state_counts", {}), sort_keys=True),
        f"  compose_project_networks={len(docker_state['networks'].get('compose_project_networks', []))}",
        f"  named_skyseed_networks={len(docker_state['networks'].get('named_skyseed_networks', []))}",
        f"  direct_backend_host_interfaces={len(report['checks']['direct_backend_host_artifacts'].get('interfaces', []))}",
        f"  runtime_lock_files={len(report['checks']['runtime_lock_files'].get('files', []))}",
        f"  free_disk_gib={round(float(disk['free_bytes']) / (1024 ** 3), 2)}",
        f"  host_mpls_ready={report['checks']['host_mpls']['assessment'].get('ready')}",
        f"  host_mpls_skipped={report['checks']['host_mpls']['assessment'].get('skipped', False)}",
        f"  bridge_netfilter_passthrough_ready={report['checks']['bridge_netfilter'].get('passthrough_ready')}",
    ]
    if report.get("cleanup_suggestions"):
        lines.append("  cleanup_suggestions:")
        for suggestion in report["cleanup_suggestions"]:
            lines.append(f"    - {suggestion['id']}: {suggestion['reason']}")
    if report.get("cleanup_result"):
        cleanup = dict(report.get("cleanup_result") or {})
        counts = dict(cleanup.get("resource_counts") or {})
        lines.append("  cleanup_result:")
        lines.append(f"    dry_run={cleanup.get('dry_run')}")
        lines.append(f"    applied={cleanup.get('applied')}")
        lines.append(f"    success={cleanup.get('success')}")
        lines.append(
            "    resources="
            + json.dumps(counts, sort_keys=True)
        )
        lines.append(
            f"    command_count={len(list(cleanup.get('command_results') or []))}"
        )
    return "\n".join(lines)
