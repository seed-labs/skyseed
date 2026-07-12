#!/usr/bin/env python3
# encoding: utf-8

"""Probe kernel and userspace support for isolated Skyseed substrate experiments."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any


PREFIX = "skyseed_probe"


def run_command(
    args: list[str],
    *,
    sudo: bool = False,
    timeout: float = 5.0,
) -> dict[str, Any]:
    command = (["sudo", "-n"] + args) if sudo else args
    started = time.perf_counter()
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
            "wall_seconds": time.perf_counter() - started,
        }
    except FileNotFoundError as exc:
        return {
            "args": command,
            "returncode": 127,
            "stdout": "",
            "stderr": str(exc),
            "wall_seconds": time.perf_counter() - started,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "args": command,
            "returncode": 124,
            "stdout": (exc.stdout or "").strip() if isinstance(exc.stdout, str) else "",
            "stderr": (exc.stderr or "").strip() if isinstance(exc.stderr, str) else "",
            "wall_seconds": time.perf_counter() - started,
            "timeout": True,
        }


def command_version(binary: str, args: list[str]) -> dict[str, Any]:
    path = shutil.which(binary)
    result: dict[str, Any] = {"path": path, "available": bool(path)}
    if path:
        result["version"] = run_command([path] + args, timeout=4.0)
    return result


def sudo_available() -> bool:
    return run_command(["true"], sudo=True, timeout=3.0)["returncode"] == 0


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def module_state(module_name: str) -> dict[str, Any]:
    modules = read_text(Path("/proc/modules")) or ""
    loaded = any(line.split(maxsplit=1)[0] == module_name for line in modules.splitlines())
    modinfo = run_command(["modinfo", module_name], timeout=4.0)
    return {
        "loaded": loaded,
        "modinfo_available": modinfo["returncode"] == 0,
        "modinfo_error": modinfo["stderr"] if modinfo["returncode"] else "",
    }


def bpffs_state() -> dict[str, Any]:
    mount_info = read_text(Path("/proc/self/mountinfo")) or ""
    mounts = [
        line
        for line in mount_info.splitlines()
        if " - bpf " in line or line.rstrip().endswith(" bpf")
    ]
    sys_fs_bpf = Path("/sys/fs/bpf")
    return {
        "path_exists": sys_fs_bpf.exists(),
        "path_is_dir": sys_fs_bpf.is_dir(),
        "mounted": bool(mounts),
        "mounts": mounts[:4],
        "writable_by_current_user": os.access(sys_fs_bpf, os.W_OK) if sys_fs_bpf.exists() else False,
    }


def try_netns_tc_probe(*, sudo: bool) -> dict[str, Any]:
    ns_name = f"{PREFIX}_{os.getpid()}"
    veth_a = f"{PREFIX}a0"
    veth_b = f"{PREFIX}b0"
    steps: list[dict[str, Any]] = []

    def step(args: list[str], timeout: float = 5.0) -> dict[str, Any]:
        result = run_command(args, sudo=sudo, timeout=timeout)
        steps.append(result)
        return result

    try:
        create_ns = step(["ip", "netns", "add", ns_name])
        if create_ns["returncode"] != 0:
            return {
                "ok": False,
                "failed_step": "ip netns add",
                "steps": steps,
            }
        create_veth = step(["ip", "link", "add", veth_a, "type", "veth", "peer", "name", veth_b])
        if create_veth["returncode"] != 0:
            return {"ok": False, "failed_step": "ip link add veth", "steps": steps}
        move_peer = step(["ip", "link", "set", veth_b, "netns", ns_name])
        if move_peer["returncode"] != 0:
            return {"ok": False, "failed_step": "ip link set netns", "steps": steps}
        qdisc_add = step(["tc", "qdisc", "add", "dev", veth_a, "root", "netem", "delay", "1ms"])
        qdisc_del = step(["tc", "qdisc", "del", "dev", veth_a, "root"])
        return {
            "ok": qdisc_add["returncode"] == 0 and qdisc_del["returncode"] == 0,
            "netns_ok": True,
            "veth_ok": True,
            "tc_netem_ok": qdisc_add["returncode"] == 0,
            "steps": steps,
        }
    finally:
        run_command(["ip", "link", "del", veth_a], sudo=sudo, timeout=4.0)
        run_command(["ip", "netns", "del", ns_name], sudo=sudo, timeout=4.0)


def mpls_state(*, sudo: bool) -> dict[str, Any]:
    sysctl_paths = {
        "platform_labels": Path("/proc/sys/net/mpls/platform_labels"),
        "ip_ttl_propagate": Path("/proc/sys/net/mpls/ip_ttl_propagate"),
    }
    values = {name: read_text(path) for name, path in sysctl_paths.items()}
    ip_route = run_command(["ip", "-f", "mpls", "route", "show"], sudo=sudo, timeout=4.0)
    return {
        "sysctls": values,
        "ip_mpls_route_show": ip_route,
        "route_show_ok": ip_route["returncode"] == 0,
        "modules": {
            "mpls_router": module_state("mpls_router"),
            "mpls_iptunnel": module_state("mpls_iptunnel"),
        },
    }


def summarize(report: dict[str, Any]) -> list[str]:
    sudo_probe = report["capabilities"]["sudo_netns_tc_probe"]
    bpf_tools = report["tools"]["bpftool"]["available"]
    clang = report["tools"]["clang"]["available"]
    btf = report["kernel"]["btf_vmlinux_exists"]
    direct = report["capabilities"]["direct_netns_tc_probe"]["ok"]
    sudo_ok = sudo_probe["ok"]
    lines = [
        f"kernel={report['kernel']['release']}",
        f"ip={report['tools']['ip']['available']} tc={report['tools']['tc']['available']}",
        f"direct_netns_tc={direct} sudo_netns_tc={sudo_ok}",
        f"bpftool={bpf_tools} clang={clang} btf_vmlinux={btf}",
        f"bpffs_mounted={report['kernel']['bpffs']['mounted']}",
        f"tc_netem_probe={sudo_probe.get('tc_netem_ok', False)}",
        f"mpls_route_show={report['kernel']['mpls']['route_show_ok']}",
    ]
    return lines


def build_report() -> dict[str, Any]:
    sudo_ok = sudo_available()
    report: dict[str, Any] = {
        "schema_version": 1,
        "probe": "skyseed_kernel_feature_probe",
        "timestamp_unix": time.time(),
        "user": {
            "uid": os.getuid(),
            "euid": os.geteuid(),
            "sudo_nopasswd": sudo_ok,
        },
        "kernel": {
            "uname": run_command(["uname", "-a"])["stdout"],
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "btf_vmlinux_exists": Path("/sys/kernel/btf/vmlinux").exists(),
            "bpffs": bpffs_state(),
            "modules": {
                "cls_bpf": module_state("cls_bpf"),
                "act_bpf": module_state("act_bpf"),
                "sch_netem": module_state("sch_netem"),
                "sch_htb": module_state("sch_htb"),
            },
        },
        "tools": {
            "ip": command_version("ip", ["-V"]),
            "tc": command_version("tc", ["-V"]),
            "bpftool": command_version("bpftool", ["version"]),
            "clang": command_version("clang", ["--version"]),
            "llc": command_version("llc", ["--version"]),
            "gcc": command_version("gcc", ["--version"]),
        },
        "capabilities": {
            "direct_netns_tc_probe": try_netns_tc_probe(sudo=False),
            "sudo_netns_tc_probe": try_netns_tc_probe(sudo=True) if sudo_ok else {"ok": False, "reason": "sudo_unavailable"},
        },
    }
    report["kernel"]["mpls"] = mpls_state(sudo=sudo_ok)
    bpftool_path = report["tools"]["bpftool"]["path"]
    if bpftool_path:
        report["kernel"]["bpftool_feature_probe"] = run_command(
            [bpftool_path, "feature", "probe", "kernel"],
            sudo=sudo_ok,
            timeout=15.0,
        )
    else:
        report["kernel"]["bpftool_feature_probe"] = {
            "returncode": 127,
            "stdout": "",
            "stderr": "bpftool not found",
        }
    report["summary_lines"] = summarize(report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-out", type=Path, help="Write compact JSON report to this path.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON to stdout.")
    args = parser.parse_args()

    report = build_report()
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for line in report["summary_lines"]:
        print(line)
    if args.pretty or not args.json_out:
        print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
