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
from typing import Any, Dict, Iterable, List, Sequence

from mpls_host import probe_host_mpls_readiness

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
SATELLITE_DIR = Path(__file__).resolve().parent
DEFAULT_RUN_ROOT = SATELLITE_DIR / "output" / "starlink_shell_experiments"
DEFAULT_STATUS_DIR = SATELLITE_DIR / "output"
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


def direct_backend_host_artifacts_report() -> Dict[str, Any]:
    """Public wrapper for scoped direct-backend residue inspection."""

    return _direct_backend_host_artifacts()


def cleanup_direct_backend_host_artifacts(
    interface_names: Sequence[str] | None = None,
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
    for name in target_names:
        completed = _run(["sudo", "-n", "ip", "link", "del", name])
        if completed.returncode == 0:
            removed.append(name)
            continue
        stderr = completed.stderr.strip()
        if "Cannot find device" in stderr:
            skipped_absent.append(name)
            continue
        failed.append(
            {
                "interface_name": name,
                "returncode": completed.returncode,
                "stderr": stderr,
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
    return suggestions


def run_skyseed_preflight(*, ignore_pids: Sequence[int] | None = None) -> Dict[str, Any]:
    docker_version = _run_jsonable(["docker", "--version"])
    docker_compose_version = _run_jsonable(["docker-compose", "version"])
    docker_state = {
        "containers": _docker_container_state(),
        "networks": _docker_network_state(),
    }
    compose_processes = _compose_processes(ignore_pids=ignore_pids)
    direct_backend = _direct_backend_host_artifacts()
    runtime_locks = _runtime_lock_files()
    disk_space = _disk_space()
    mpls = probe_host_mpls_readiness()
    bridge_netfilter = _bridge_netfilter()
    python_runtime = _python_runtime()

    warnings: List[str] = []
    blocking: List[str] = []

    if not docker_version.get("ok"):
        blocking.append("docker_unavailable")
    if not docker_compose_version.get("ok"):
        blocking.append("docker_compose_unavailable")
    if compose_processes.get("processes"):
        blocking.append("skyseed_compose_or_runtime_processes_present")
    compose_project_container_count = len(
        docker_state["containers"].get("compose_project_containers", [])
    )
    named_container_count = len(
        docker_state["containers"].get("named_skyseed_containers", [])
    )
    compose_project_state_counts = dict(
        docker_state["containers"].get("compose_project_state_counts", {})
    )
    compose_project_network_count = len(
        docker_state["networks"].get("compose_project_networks", [])
    )
    named_network_count = len(
        docker_state["networks"].get("named_skyseed_networks", [])
    )
    if compose_project_container_count or compose_project_network_count:
        warnings.append("seed_output_compose_resources_present")
    if named_container_count:
        warnings.append("named_skyseed_containers_present")
    if (
        int(compose_project_state_counts.get("exited", 0)) > 0
        or int(compose_project_state_counts.get("other", 0)) > 0
    ):
        warnings.append("seed_output_partial_container_state_present")
    if named_network_count:
        warnings.append("named_skyseed_networks_present")
    if direct_backend.get("interfaces"):
        warnings.append("direct_backend_host_interfaces_present")
    if runtime_locks.get("files"):
        warnings.append("runtime_lock_files_present")
    if disk_space["free_bytes"] < LOW_DISK_ERROR_BYTES:
        blocking.append("disk_space_critically_low")
    elif disk_space["free_bytes"] < LOW_DISK_WARN_BYTES:
        warnings.append("disk_space_low")
    if mpls["assessment"]["ready"] is not True:
        warnings.append("host_mpls_not_ready")
    if bridge_netfilter["passthrough_ready"] is not True:
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

    report = {
        "generated_at_utc": _timestamp_utc(),
        "workspace_root": str(WORKSPACE_ROOT),
        "checks": {
            "docker_toolchain": {
                "docker": docker_version,
                "docker_compose": docker_compose_version,
            },
            "docker_state": docker_state,
            "compose_processes": compose_processes,
            "direct_backend_host_artifacts": direct_backend,
            "runtime_lock_files": runtime_locks,
            "disk_space": disk_space,
            "python_runtime": python_runtime,
            "host_mpls": mpls,
            "bridge_netfilter": bridge_netfilter,
            "known_compose_outputs": _find_compose_outputs(),
        },
        "assessment": {
            "ready": not blocking,
            "blocking_reasons": blocking,
            "warning_reasons": warnings,
        },
    }
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
        f"  ready={assessment.get('ready')}",
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
        f"  bridge_netfilter_passthrough_ready={report['checks']['bridge_netfilter'].get('passthrough_ready')}",
    ]
    if report.get("cleanup_suggestions"):
        lines.append("  cleanup_suggestions:")
        for suggestion in report["cleanup_suggestions"]:
            lines.append(f"    - {suggestion['id']}: {suggestion['reason']}")
    return "\n".join(lines)
