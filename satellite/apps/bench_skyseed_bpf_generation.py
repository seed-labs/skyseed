#!/usr/bin/env python3
# encoding: utf-8

"""Probe and microbench BPF map active_generation updates for Skyseed."""

from __future__ import annotations

import argparse
import ctypes
import errno
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


BPF_MAP_CREATE = 0
BPF_MAP_UPDATE_ELEM = 2
BPF_MAP_TYPE_ARRAY = 2
BPF_ANY = 0
SYS_BPF_X86_64 = 321


class BpfMapCreateAttr(ctypes.Structure):
    _fields_ = [
        ("map_type", ctypes.c_uint),
        ("key_size", ctypes.c_uint),
        ("value_size", ctypes.c_uint),
        ("max_entries", ctypes.c_uint),
        ("map_flags", ctypes.c_uint),
        ("inner_map_fd", ctypes.c_uint),
        ("numa_node", ctypes.c_uint),
        ("map_name", ctypes.c_char * 16),
        ("map_ifindex", ctypes.c_uint),
        ("btf_fd", ctypes.c_uint),
        ("btf_key_type_id", ctypes.c_uint),
        ("btf_value_type_id", ctypes.c_uint),
        ("btf_vmlinux_value_type_id", ctypes.c_uint),
    ]


class BpfMapUpdateAttr(ctypes.Structure):
    _fields_ = [
        ("map_fd", ctypes.c_uint),
        ("key", ctypes.c_ulonglong),
        ("value", ctypes.c_ulonglong),
        ("flags", ctypes.c_ulonglong),
    ]


LIBC = ctypes.CDLL(None, use_errno=True)


def sudo_available() -> bool:
    return subprocess.run(
        ["sudo", "-n", "true"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).returncode == 0


def maybe_reexec_with_sudo(no_sudo_reexec: bool) -> None:
    if no_sudo_reexec or os.geteuid() == 0 or not sudo_available():
        return
    os.execvp("sudo", ["sudo", "-n", sys.executable] + sys.argv)


def bpf_syscall(command: int, attr: ctypes.Structure) -> tuple[int, int]:
    ctypes.set_errno(0)
    result = LIBC.syscall(SYS_BPF_X86_64, command, ctypes.byref(attr), ctypes.sizeof(attr))
    return int(result), ctypes.get_errno()


def create_array_map(name: bytes, max_entries: int = 1024, value_size: int = 8) -> tuple[int, dict[str, Any]]:
    attr = BpfMapCreateAttr()
    attr.map_type = BPF_MAP_TYPE_ARRAY
    attr.key_size = 4
    attr.value_size = value_size
    attr.max_entries = max_entries
    attr.map_flags = 0
    attr.map_name = name[:15]
    fd, err = bpf_syscall(BPF_MAP_CREATE, attr)
    return fd, {
        "fd": fd,
        "errno": err,
        "errno_name": errno.errorcode.get(err, ""),
        "attr_size": ctypes.sizeof(attr),
    }


def update_map(fd: int, key_value: int, value_value: int) -> tuple[int, int]:
    key = ctypes.c_uint(key_value)
    value = ctypes.c_ulonglong(value_value)
    attr = BpfMapUpdateAttr()
    attr.map_fd = fd
    attr.key = ctypes.addressof(key)
    attr.value = ctypes.addressof(value)
    attr.flags = BPF_ANY
    return bpf_syscall(BPF_MAP_UPDATE_ELEM, attr)


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def summarize(values: list[float], failures: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "count": len(values),
        "failure_count": len(failures),
        "p50_us": percentile(values, 0.50),
        "p95_us": percentile(values, 0.95),
        "max_us": max(values) if values else None,
        "mean_us": statistics.fmean(values) if values else None,
        "failures": failures[:5],
    }


def bench_updates(fd: int, iterations: int, *, key_count: int) -> dict[str, Any]:
    values: list[float] = []
    failures: list[dict[str, Any]] = []
    for index in range(iterations):
        started = time.perf_counter_ns()
        result, err = update_map(fd, index % key_count, index)
        ended = time.perf_counter_ns()
        if result == 0:
            values.append((ended - started) / 1000.0)
        else:
            failures.append(
                {
                    "iteration": index,
                    "errno": err,
                    "errno_name": errno.errorcode.get(err, ""),
                }
            )
    return summarize(values, failures)


def run_command(args: list[str], *, timeout: float = 10.0, sudo: bool = False) -> dict[str, Any]:
    command = (["sudo", "-n"] + args) if sudo and os.geteuid() != 0 else args
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
        return {
            "args": command,
            "returncode": 124,
            "stdout": (exc.stdout or "").strip() if isinstance(exc.stdout, str) else "",
            "stderr": (exc.stderr or "").strip() if isinstance(exc.stderr, str) else "",
            "timeout": True,
        }


def tc_bpf_attach_probe(source: Path, work_dir: Path) -> dict[str, Any]:
    clang = shutil.which("clang")
    tc = shutil.which("tc")
    ip = shutil.which("ip")
    if not clang or not tc or not ip:
        return {
            "skipped": True,
            "reason": "missing_tool",
            "clang": clang,
            "tc": tc,
            "ip": ip,
        }
    obj_path = work_dir / "skyseed_tc_generation_probe.o"
    compile_result = run_command(
        [
            clang,
            "-O2",
            "-g",
            "-target",
            "bpf",
            "-c",
            str(source),
            "-o",
            str(obj_path),
        ],
        timeout=20.0,
    )
    if compile_result["returncode"] != 0:
        return {"skipped": True, "reason": "compile_failed", "compile": compile_result}

    veth_a = f"skyseedbpfa{os.getpid()}"
    veth_b = f"skyseedbpfb{os.getpid()}"
    steps: list[dict[str, Any]] = []
    try:
        for step in [
            ["ip", "link", "add", veth_a, "type", "veth", "peer", "name", veth_b],
            ["ip", "link", "set", veth_a, "up"],
            ["tc", "qdisc", "add", "dev", veth_a, "clsact"],
            ["tc", "filter", "add", "dev", veth_a, "egress", "bpf", "da", "obj", str(obj_path), "sec", "classifier"],
        ]:
            result = run_command(step, timeout=8.0)
            steps.append(result)
            if result["returncode"] != 0:
                return {"ok": False, "compile": compile_result, "steps": steps}
        return {"ok": True, "compile": compile_result, "steps": steps}
    finally:
        run_command(["tc", "qdisc", "del", "dev", veth_a, "clsact"], timeout=4.0)
        run_command(["ip", "link", "del", veth_a], timeout=4.0)
        run_command(["ip", "link", "del", veth_b], timeout=4.0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=5000)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--no-sudo-reexec", action="store_true")
    parser.add_argument(
        "--bpf-source",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "experimental" / "bpf" / "skyseed_tc_generation_probe.c",
    )
    args = parser.parse_args()

    maybe_reexec_with_sudo(args.no_sudo_reexec)
    report: dict[str, Any] = {
        "schema_version": 1,
        "bench": "skyseed_bpf_generation",
        "timestamp_unix": time.time(),
        "uid": os.getuid(),
        "euid": os.geteuid(),
        "iterations": args.iterations,
        "tools": {
            "bpftool": shutil.which("bpftool"),
            "clang": shutil.which("clang"),
            "llc": shutil.which("llc"),
            "tc": shutil.which("tc"),
        },
        "kernel": {
            "btf_vmlinux_exists": Path("/sys/kernel/btf/vmlinux").exists(),
            "bpffs_exists": Path("/sys/fs/bpf").exists(),
        },
    }

    map_fd, create_result = create_array_map(b"skyseed_state", max_entries=1024, value_size=8)
    report["bpf_map_create"] = create_result
    if map_fd < 0:
        report["bpf_map_update"] = {
            "skipped": True,
            "reason": "map_create_failed",
            "errno": create_result["errno"],
            "errno_name": create_result["errno_name"],
        }
        report["active_generation_update"] = {"skipped": True, "reason": "map_create_failed"}
    else:
        try:
            report["bpf_map_update"] = bench_updates(map_fd, args.iterations, key_count=1024)
            report["active_generation_update"] = bench_updates(map_fd, args.iterations, key_count=1)
        finally:
            os.close(map_fd)

    report["tc_bpf_attach"] = tc_bpf_attach_probe(args.bpf_source, Path("/tmp"))
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
