#!/usr/bin/env python3
# encoding: utf-8

"""Run an isolated tc-BPF G/Z forwarding shadow prototype for Skyseed."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = REPO_ROOT / "satellite" / "experimental" / "bpf" / "skyseed_tc_gz_shadow.c"
DEFAULT_JSON = REPO_ROOT / "satellite" / "baselines" / "skyseed_bpf_gz_shadow_summary.json"
PIN_BASE = Path("/sys/fs/bpf/tc/globals")

PINNED_MAPS = [
    "active_generation",
    "generation_status",
    "fec_forwarding",
    "packet_counters",
    "future_generation",
]
KERNEL_MAP_NAMES = {
    "active_generation": "active_generati",
    "generation_status": "generation_stat",
    "fec_forwarding": "fec_forwarding",
    "packet_counters": "packet_counters",
    "future_generation": "future_generat",
}
MAP_SPECS = {
    "active_generation": {"type": "array", "bytes_key": 4, "bytes_value": 4, "max_entries": 1},
    "generation_status": {"type": "array", "bytes_key": 4, "bytes_value": 4, "max_entries": 8},
    "fec_forwarding": {"type": "hash", "bytes_key": 8, "bytes_value": 28, "max_entries": 64},
    "packet_counters": {"type": "array", "bytes_key": 4, "bytes_value": 8, "max_entries": 16},
    "future_generation": {"type": "array", "bytes_key": 4, "bytes_value": 4, "max_entries": 2},
}

FEC_G = 1
FEC_Z = 2
ACTION_FORWARD = 1
ACTION_LOCAL = 2
NEXT_HOP_GW1 = 1
NEXT_HOP_GW2 = 2
FEC_G_IP = "10.91.0.100"
FEC_Z_IP = "10.91.0.200"
MISSING_FEC_IP = "10.91.0.250"

COUNTERS = {
    0: "generation_hit",
    1: "fec_hit",
    2: "redirect_pass",
    3: "drop_missing_generation",
    4: "drop_missing_fec",
    5: "drop_inactive",
    6: "future_generation_not_used",
    7: "local_delivery",
    8: "drop_bad_packet",
    9: "next_hop_gw1",
    10: "next_hop_gw2",
    11: "shadow_pass",
}


def run_command(
    args: list[str],
    *,
    timeout: float = 10.0,
    privileged: bool = False,
) -> dict[str, Any]:
    command = args
    if privileged and os.geteuid() != 0:
        command = ["sudo", "-n"] + args
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


def resolve_bpftool() -> str:
    candidates: list[str] = []
    path = shutil.which("bpftool")
    if path:
        candidates.append(path)
    candidates.extend(str(candidate) for candidate in sorted(Path("/usr/lib/linux-tools").glob("*/bpftool")))
    candidates.extend(str(candidate) for candidate in sorted(Path("/usr/lib").glob("linux-tools-*/bpftool")))
    for candidate in candidates:
        result = run_command([candidate, "version"], timeout=5.0)
        if result["returncode"] == 0:
            return candidate
    return path or "bpftool"


BPFTOOL = resolve_bpftool()


def first_lines(value: str, limit: int = 4) -> str:
    return "\n".join(value.splitlines()[:limit])


def tool_status(name: str, version_args: list[str]) -> dict[str, Any]:
    path = BPFTOOL if name == "bpftool" else shutil.which(name)
    result: dict[str, Any] = {"path": path, "available": path is not None}
    if path:
        version = run_command([path] + version_args, timeout=5.0)
        result["version"] = first_lines(version["stdout"] or version["stderr"])
        result["version_returncode"] = version["returncode"]
        result["available"] = version["returncode"] == 0
    return result


def sudo_status() -> dict[str, Any]:
    result = run_command(["sudo", "-n", "true"], timeout=4.0)
    return {
        "uid": os.getuid(),
        "euid": os.geteuid(),
        "noninteractive_sudo": result["returncode"] == 0,
        "sudo_check": result,
    }


def package_status(packages: list[str]) -> dict[str, Any]:
    result = run_command(["dpkg-query", "-W", "-f=${Package} ${Version} ${Status}\n", *packages])
    records: dict[str, Any] = {package: {"installed": False} for package in packages}
    if result["stdout"]:
        for line in result["stdout"].splitlines():
            parts = line.split(maxsplit=4)
            if len(parts) >= 5:
                records[parts[0]] = {
                    "installed": parts[4] == "installed",
                    "version": parts[1],
                    "status": parts[2:],
                }
    records["_command"] = result
    return records


def collect_environment() -> dict[str, Any]:
    bpffs = Path("/sys/fs/bpf")
    btf = Path("/sys/kernel/btf/vmlinux")
    return {
        "tools": {
            "clang": tool_status("clang", ["--version"]),
            "llc": tool_status("llc", ["--version"]),
            "bpftool": tool_status("bpftool", ["version"]),
            "tc": tool_status("tc", ["-V"]),
            "ip": tool_status("ip", ["-V"]),
            "ping": tool_status("ping", ["-V"]),
        },
        "kernel": {
            "uname": run_command(["uname", "-a"], timeout=4.0)["stdout"],
            "bpffs_exists": bpffs.exists(),
            "bpffs_is_dir": bpffs.is_dir(),
            "bpffs_mount": run_command(["findmnt", "-rn", "-T", str(bpffs)], timeout=4.0),
            "btf_vmlinux_exists": btf.exists(),
        },
        "sudo": sudo_status(),
        "apt_packages": package_status(
            [
                "clang",
                "llvm",
                "linux-tools-common",
                "linux-tools-generic",
                "linux-tools-5.4.0-216",
                "linux-tools-5.4.0-216-generic",
                "gcc",
                "make",
                "pkg-config",
            ]
        ),
        "python": {
            "executable": sys.executable,
            "version": sys.version.split()[0],
        },
    }


def attach_blockers(environment: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    for tool in ["clang", "bpftool", "tc", "ip", "ping"]:
        if not environment["tools"][tool]["available"]:
            blockers.append(f"missing_{tool}")
    if not environment["kernel"]["bpffs_exists"]:
        blockers.append("missing_bpffs")
    if os.geteuid() != 0 and not environment["sudo"]["noninteractive_sudo"]:
        blockers.append("missing_root_or_passwordless_sudo")
    return blockers


def compile_bpf(source: Path, object_path: Path) -> dict[str, Any]:
    include_args: list[str] = []
    arch_include = Path("/usr/include/x86_64-linux-gnu")
    if arch_include.exists():
        include_args = ["-I", str(arch_include)]
    return run_command(
        [
            "clang",
            "-O2",
            "-target",
            "bpf",
            "-D__TARGET_ARCH_x86",
            *include_args,
            "-c",
            str(source),
            "-o",
            str(object_path),
        ],
        timeout=20.0,
    )


def ns_exec(namespace: str, args: list[str], *, timeout: float = 10.0) -> dict[str, Any]:
    return run_command(["ip", "netns", "exec", namespace] + args, timeout=timeout, privileged=True)


def tc_exec(namespace: str, args: list[str], *, timeout: float = 10.0) -> dict[str, Any]:
    return ns_exec(namespace, ["tc"] + args, timeout=timeout)


def bpftool(args: list[str], *, timeout: float = 10.0) -> dict[str, Any]:
    return run_command([BPFTOOL] + args, timeout=timeout, privileged=True)


def privileged_path_exists(path: Path) -> bool:
    return run_command(["test", "-e", str(path)], timeout=4.0, privileged=True)["returncode"] == 0


def map_reference(map_name: str) -> tuple[list[str], dict[str, Any]]:
    pinned = PIN_BASE / map_name
    if privileged_path_exists(pinned):
        return ["pinned", str(pinned)], {"kind": "pinned", "path": str(pinned)}

    show = bpftool(["-j", "map", "show"], timeout=8.0)
    maps: list[dict[str, Any]] = []
    if show["stdout"]:
        try:
            parsed = json.loads(show["stdout"])
            if isinstance(parsed, list):
                maps = [item for item in parsed if isinstance(item, dict)]
        except json.JSONDecodeError:
            maps = []
    kernel_name = KERNEL_MAP_NAMES.get(map_name, map_name[:15])
    matches = [item for item in maps if item.get("name") == kernel_name]
    if matches:
        selected = sorted(matches, key=lambda item: int(item.get("id", -1)))[-1]
        return ["id", str(selected["id"])], {
            "kind": "id",
            "logical_name": map_name,
            "kernel_name": kernel_name,
            "id": selected["id"],
        }
    spec = MAP_SPECS[map_name]
    shape_matches = [
        item
        for item in maps
        if all(item.get(key) == value for key, value in spec.items())
    ]
    if shape_matches:
        selected = sorted(shape_matches, key=lambda item: int(item.get("id", -1)))[-1]
        return ["id", str(selected["id"])], {
            "kind": "id_by_shape",
            "logical_name": map_name,
            "kernel_name": kernel_name,
            "id": selected["id"],
            "shape": spec,
        }
    return ["pinned", str(pinned)], {
        "kind": "missing",
        "logical_name": map_name,
        "kernel_name": kernel_name,
        "bpftool_map_show": show,
    }


def hex_bytes(data: bytes) -> list[str]:
    return [f"{byte:02x}" for byte in data]


def u32_bytes(value: int) -> bytes:
    return struct.pack("<I", value & 0xFFFFFFFF)


def update_raw_map(map_name: str, key: bytes, value: bytes) -> dict[str, Any]:
    ref_args, ref_meta = map_reference(map_name)
    result = bpftool(
        [
            "map",
            "update",
            *ref_args,
            "key",
            "hex",
            *hex_bytes(key),
            "value",
            "hex",
            *hex_bytes(value),
        ],
        timeout=8.0,
    )
    result["map_reference"] = ref_meta
    return result


def update_u32_map(map_name: str, key: int, value: int) -> dict[str, Any]:
    return update_raw_map(map_name, u32_bytes(key), u32_bytes(value))


def parse_mac(value: str) -> bytes:
    return bytes(int(part, 16) for part in value.strip().split(":"))


def update_route_map(
    generation: int,
    fec: int,
    *,
    active: int,
    action: int,
    ifindex: int,
    next_hop: int,
    dst_mac: bytes = b"\x00" * 6,
    src_mac: bytes = b"\x00" * 6,
) -> dict[str, Any]:
    key = struct.pack("<II", generation, fec)
    value = struct.pack("<IIII6s6s", active, action, ifindex, next_hop, dst_mac, src_mac)
    return update_raw_map("fec_forwarding", key, value)


def dump_map(map_name: str) -> dict[str, Any]:
    ref_args, ref_meta = map_reference(map_name)
    result = bpftool(["-j", "map", "dump", *ref_args], timeout=8.0)
    parsed: Any = None
    if result["stdout"]:
        try:
            parsed = json.loads(result["stdout"])
        except json.JSONDecodeError:
            parsed = None
    return {"command": result, "map_reference": ref_meta, "parsed": parsed}


def parse_u32_hex(values: list[str]) -> int:
    raw = bytes(int(item, 16) for item in values)
    return struct.unpack("<I", raw[:4])[0]


def parse_u64_hex(values: list[str]) -> int:
    raw = bytes(int(item, 16) for item in values)
    return struct.unpack("<Q", raw[:8])[0]


def read_counters() -> dict[str, Any]:
    dumped = dump_map("packet_counters")
    counters = {name: 0 for name in COUNTERS.values()}
    parsed = dumped.get("parsed")
    if isinstance(parsed, list):
        for item in parsed:
            key_values = item.get("key")
            value_values = item.get("value")
            if not isinstance(key_values, list) or not isinstance(value_values, list):
                continue
            key = parse_u32_hex(key_values)
            if key in COUNTERS:
                counters[COUNTERS[key]] = parse_u64_hex(value_values)
    return {"counters": counters, "dump": dumped}


def counter_delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    keys = sorted(set(before) | set(after))
    return {key: after.get(key, 0) - before.get(key, 0) for key in keys}


def read_int_sysfs(namespace: str, path: str) -> dict[str, Any]:
    result = ns_exec(namespace, ["cat", path], timeout=4.0)
    value: int | None = None
    if result["returncode"] == 0:
        try:
            value = int(result["stdout"].strip())
        except ValueError:
            value = None
    return {"value": value, "command": result}


def interface_stat(namespace: str, interface: str, stat: str) -> dict[str, Any]:
    return read_int_sysfs(namespace, f"/sys/class/net/{interface}/statistics/{stat}")


def interface_rx_snapshot(names: dict[str, str]) -> dict[str, Any]:
    return {
        "gw1_rx_packets": interface_stat(names["ns_gw1"], names["if_gw1"], "rx_packets"),
        "gw2_rx_packets": interface_stat(names["ns_gw2"], names["if_gw2"], "rx_packets"),
    }


def interface_rx_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, int | None]:
    deltas: dict[str, int | None] = {}
    for key in ["gw1_rx_packets", "gw2_rx_packets"]:
        before_value = (before.get(key) or {}).get("value")
        after_value = (after.get(key) or {}).get("value")
        deltas[key] = None if before_value is None or after_value is None else after_value - before_value
    return deltas


def get_namespace_ifindex(namespace: str, interface: str) -> dict[str, Any]:
    return read_int_sysfs(namespace, f"/sys/class/net/{interface}/ifindex")


def get_namespace_mac(namespace: str, interface: str) -> dict[str, Any]:
    result = ns_exec(namespace, ["cat", f"/sys/class/net/{interface}/address"], timeout=4.0)
    mac: bytes | None = None
    if result["returncode"] == 0:
        mac = parse_mac(result["stdout"])
    return {"mac": mac.hex(":") if mac else None, "mac_bytes": mac, "command": result}


def cleanup(names: dict[str, str]) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    if names.get("ns_fwd") and names.get("if_fwd_ut"):
        steps.append(tc_exec(names["ns_fwd"], ["qdisc", "del", "dev", names["if_fwd_ut"], "clsact"], timeout=4.0))
    for map_name in PINNED_MAPS:
        steps.append(run_command(["rm", "-f", str(PIN_BASE / map_name)], timeout=4.0, privileged=True))
    for key in ["ns_ut", "ns_fwd", "ns_gw1", "ns_gw2"]:
        if names.get(key):
            steps.append(run_command(["ip", "netns", "del", names[key]], timeout=5.0, privileged=True))
    return {"steps": steps, "residue": residue_check(names)}


def residue_check(names: dict[str, str]) -> dict[str, Any]:
    netns = run_command(["ip", "netns", "list"], timeout=4.0, privileged=True)
    links = run_command(["ip", "-o", "link", "show"], timeout=4.0, privileged=True)
    pinned = {name: privileged_path_exists(PIN_BASE / name) for name in PINNED_MAPS}
    namespace_names = [names.get(key, "") for key in ["ns_ut", "ns_fwd", "ns_gw1", "ns_gw2"]]
    link_names = [
        names.get(key, "")
        for key in ["if_ut", "if_fwd_ut", "if_fwd_gw1", "if_gw1", "if_fwd_gw2", "if_gw2"]
    ]
    namespace_hits = [
        name for name in namespace_names if name and name in (netns["stdout"] + netns["stderr"])
    ]
    link_hits = [name for name in link_names if name and name in (links["stdout"] + links["stderr"])]
    return {
        "namespace_hits": namespace_hits,
        "link_hits": link_hits,
        "pinned_maps": pinned,
        "tc_filter_residue_possible": bool(namespace_hits),
        "clean": not namespace_hits and not link_hits and not any(pinned.values()),
    }


def setup_netns(names: dict[str, str]) -> list[dict[str, Any]]:
    steps = [
        run_command(["ip", "netns", "add", names["ns_ut"]], timeout=8.0, privileged=True),
        run_command(["ip", "netns", "add", names["ns_fwd"]], timeout=8.0, privileged=True),
        run_command(["ip", "netns", "add", names["ns_gw1"]], timeout=8.0, privileged=True),
        run_command(["ip", "netns", "add", names["ns_gw2"]], timeout=8.0, privileged=True),
        run_command(
            ["ip", "link", "add", names["if_ut"], "type", "veth", "peer", "name", names["if_fwd_ut"]],
            timeout=8.0,
            privileged=True,
        ),
        run_command(
            [
                "ip",
                "link",
                "add",
                names["if_fwd_gw1"],
                "type",
                "veth",
                "peer",
                "name",
                names["if_gw1"],
            ],
            timeout=8.0,
            privileged=True,
        ),
        run_command(
            [
                "ip",
                "link",
                "add",
                names["if_fwd_gw2"],
                "type",
                "veth",
                "peer",
                "name",
                names["if_gw2"],
            ],
            timeout=8.0,
            privileged=True,
        ),
        run_command(["ip", "link", "set", names["if_ut"], "netns", names["ns_ut"]], timeout=8.0, privileged=True),
        run_command(["ip", "link", "set", names["if_fwd_ut"], "netns", names["ns_fwd"]], timeout=8.0, privileged=True),
        run_command(["ip", "link", "set", names["if_fwd_gw1"], "netns", names["ns_fwd"]], timeout=8.0, privileged=True),
        run_command(["ip", "link", "set", names["if_gw1"], "netns", names["ns_gw1"]], timeout=8.0, privileged=True),
        run_command(["ip", "link", "set", names["if_fwd_gw2"], "netns", names["ns_fwd"]], timeout=8.0, privileged=True),
        run_command(["ip", "link", "set", names["if_gw2"], "netns", names["ns_gw2"]], timeout=8.0, privileged=True),
    ]
    for namespace in [names["ns_ut"], names["ns_fwd"], names["ns_gw1"], names["ns_gw2"]]:
        steps.append(ns_exec(namespace, ["ip", "link", "set", "lo", "up"]))
        steps.append(ns_exec(namespace, ["sysctl", "-q", "-w", "net.ipv6.conf.all.disable_ipv6=1"]))
        steps.append(ns_exec(namespace, ["sysctl", "-q", "-w", "net.ipv6.conf.default.disable_ipv6=1"]))
        steps.append(ns_exec(namespace, ["sysctl", "-q", "-w", "net.ipv4.conf.all.rp_filter=0"]))
        steps.append(ns_exec(namespace, ["sysctl", "-q", "-w", "net.ipv4.conf.default.rp_filter=0"]))
    steps.extend(
        [
            ns_exec(names["ns_ut"], ["ip", "addr", "add", "10.90.0.2/24", "dev", names["if_ut"]]),
            ns_exec(names["ns_fwd"], ["ip", "addr", "add", "10.90.0.1/24", "dev", names["if_fwd_ut"]]),
            ns_exec(names["ns_fwd"], ["ip", "addr", "add", "10.90.1.1/24", "dev", names["if_fwd_gw1"]]),
            ns_exec(names["ns_gw1"], ["ip", "addr", "add", "10.90.1.2/24", "dev", names["if_gw1"]]),
            ns_exec(names["ns_fwd"], ["ip", "addr", "add", "10.90.2.1/24", "dev", names["if_fwd_gw2"]]),
            ns_exec(names["ns_gw2"], ["ip", "addr", "add", "10.90.2.2/24", "dev", names["if_gw2"]]),
            ns_exec(names["ns_fwd"], ["ip", "addr", "add", f"{FEC_Z_IP}/32", "dev", names["if_fwd_ut"]]),
            ns_exec(names["ns_gw1"], ["ip", "addr", "add", f"{FEC_G_IP}/32", "dev", names["if_gw1"]]),
            ns_exec(names["ns_gw2"], ["ip", "addr", "add", f"{FEC_G_IP}/32", "dev", names["if_gw2"]]),
        ]
    )
    for namespace, interface in [
        (names["ns_ut"], names["if_ut"]),
        (names["ns_fwd"], names["if_fwd_ut"]),
        (names["ns_fwd"], names["if_fwd_gw1"]),
        (names["ns_gw1"], names["if_gw1"]),
        (names["ns_fwd"], names["if_fwd_gw2"]),
        (names["ns_gw2"], names["if_gw2"]),
    ]:
        steps.append(ns_exec(namespace, ["ip", "link", "set", interface, "up"]))
    steps.extend(
        [
            ns_exec(names["ns_fwd"], ["sysctl", "-q", "-w", "net.ipv4.ip_forward=1"]),
            ns_exec(names["ns_ut"], ["ip", "route", "add", "10.91.0.0/24", "via", "10.90.0.1", "dev", names["if_ut"]]),
            ns_exec(names["ns_gw1"], ["ip", "route", "add", "10.90.0.0/24", "via", "10.90.1.1", "dev", names["if_gw1"]]),
            ns_exec(names["ns_gw2"], ["ip", "route", "add", "10.90.0.0/24", "via", "10.90.2.1", "dev", names["if_gw2"]]),
        ]
    )
    return steps


def attach_section(names: dict[str, str], object_path: Path, section: str) -> list[dict[str, Any]]:
    return [
        tc_exec(names["ns_fwd"], ["qdisc", "del", "dev", names["if_fwd_ut"], "clsact"], timeout=4.0),
        tc_exec(names["ns_fwd"], ["qdisc", "add", "dev", names["if_fwd_ut"], "clsact"], timeout=8.0),
        tc_exec(
            names["ns_fwd"],
            [
                "filter",
                "add",
                "dev",
                names["if_fwd_ut"],
                "ingress",
                "bpf",
                "da",
                "obj",
                str(object_path),
                "sec",
                section,
            ],
            timeout=10.0,
        ),
    ]


def attach_ok(attach: list[dict[str, Any]]) -> bool:
    return bool(attach) and attach[-1]["returncode"] == 0


def parse_ping(stdout: str) -> dict[str, Any]:
    packet_match = re.search(
        r"(\d+) packets transmitted, (\d+) received, .*?(\d+(?:\.\d+)?)% packet loss",
        stdout,
    )
    rtt_match = re.search(r"rtt [^=]* = ([0-9.]+)/([0-9.]+)/([0-9.]+)/([0-9.]+) ms", stdout)
    parsed: dict[str, Any] = {}
    if packet_match:
        parsed.update(
            {
                "transmitted": int(packet_match.group(1)),
                "received": int(packet_match.group(2)),
                "packet_loss_pct": float(packet_match.group(3)),
            }
        )
    if rtt_match:
        parsed.update(
            {
                "rtt_min_ms": float(rtt_match.group(1)),
                "rtt_avg_ms": float(rtt_match.group(2)),
                "rtt_max_ms": float(rtt_match.group(3)),
                "rtt_mdev_ms": float(rtt_match.group(4)),
            }
        )
    return parsed


def ping_ip(names: dict[str, str], ip: str, *, count: int, timeout_s: int = 1) -> dict[str, Any]:
    result = ns_exec(
        names["ns_ut"],
        ["ping", "-c", str(count), "-W", str(timeout_s), ip],
        timeout=max(6.0, count * (timeout_s + 1.0)),
    )
    result["parsed"] = parse_ping(result["stdout"])
    return result


def ping_passed(ping: dict[str, Any]) -> bool:
    parsed = ping.get("parsed") or {}
    return ping.get("returncode") == 0 and parsed.get("received", 0) > 0


def collect_forwarding_metadata(names: dict[str, str]) -> dict[str, Any]:
    return {
        "gw1_ifindex": get_namespace_ifindex(names["ns_fwd"], names["if_fwd_gw1"]),
        "gw2_ifindex": get_namespace_ifindex(names["ns_fwd"], names["if_fwd_gw2"]),
        "gw1_dst_mac": get_namespace_mac(names["ns_gw1"], names["if_gw1"]),
        "gw2_dst_mac": get_namespace_mac(names["ns_gw2"], names["if_gw2"]),
        "gw1_src_mac": get_namespace_mac(names["ns_fwd"], names["if_fwd_gw1"]),
        "gw2_src_mac": get_namespace_mac(names["ns_fwd"], names["if_fwd_gw2"]),
    }


def metadata_ok(metadata: dict[str, Any]) -> bool:
    for key in ["gw1_ifindex", "gw2_ifindex"]:
        if metadata[key].get("value") is None:
            return False
    for key in ["gw1_dst_mac", "gw2_dst_mac", "gw1_src_mac", "gw2_src_mac"]:
        if metadata[key].get("mac_bytes") is None:
            return False
    return True


def initialize_maps(metadata: dict[str, Any]) -> dict[str, Any]:
    if not metadata_ok(metadata):
        return {"ok": False, "reason": "forwarding_metadata_unavailable", "updates": []}

    gw1_ifindex = int(metadata["gw1_ifindex"]["value"])
    gw2_ifindex = int(metadata["gw2_ifindex"]["value"])
    updates = [
        update_u32_map("active_generation", 0, 0),
        update_u32_map("future_generation", 0, 1),
        update_u32_map("generation_status", 0, 1),
        update_u32_map("generation_status", 1, 1),
        update_u32_map("generation_status", 2, 1),
        update_route_map(
            0,
            FEC_G,
            active=1,
            action=ACTION_FORWARD,
            ifindex=gw1_ifindex,
            next_hop=NEXT_HOP_GW1,
            dst_mac=metadata["gw1_dst_mac"]["mac_bytes"],
            src_mac=metadata["gw1_src_mac"]["mac_bytes"],
        ),
        update_route_map(
            1,
            FEC_G,
            active=1,
            action=ACTION_FORWARD,
            ifindex=gw2_ifindex,
            next_hop=NEXT_HOP_GW2,
            dst_mac=metadata["gw2_dst_mac"]["mac_bytes"],
            src_mac=metadata["gw2_src_mac"]["mac_bytes"],
        ),
        update_route_map(
            2,
            FEC_G,
            active=0,
            action=ACTION_FORWARD,
            ifindex=gw1_ifindex,
            next_hop=NEXT_HOP_GW1,
            dst_mac=metadata["gw1_dst_mac"]["mac_bytes"],
            src_mac=metadata["gw1_src_mac"]["mac_bytes"],
        ),
        update_route_map(
            0,
            FEC_Z,
            active=1,
            action=ACTION_LOCAL,
            ifindex=0,
            next_hop=0,
        ),
    ]
    return {"ok": all(update["returncode"] == 0 for update in updates), "updates": updates}


def set_generation(generation: int) -> dict[str, Any]:
    return update_u32_map("active_generation", 0, generation)


def run_probe(
    names: dict[str, str],
    *,
    name: str,
    generation: int,
    destination: str,
    count: int,
    expected_ping: str,
    expected_counter: str | None = None,
    expected_rx: str | None = None,
) -> dict[str, Any]:
    set_gen = set_generation(generation)
    counters_before = read_counters()["counters"]
    rx_before = interface_rx_snapshot(names)
    ping = ping_ip(names, destination, count=count)
    rx_after = interface_rx_snapshot(names)
    counters_after = read_counters()["counters"]
    delta = counter_delta(counters_before, counters_after)
    rx_delta = interface_rx_delta(rx_before, rx_after)

    observed_ping = "pass" if ping_passed(ping) else "drop"
    counter_ok = expected_counter is None or delta.get(expected_counter, 0) > 0
    rx_ok = True
    if expected_rx == "gw1":
        rx_ok = (rx_delta.get("gw1_rx_packets") or 0) > 0 and (rx_delta.get("gw2_rx_packets") or 0) == 0
    elif expected_rx == "gw2":
        rx_ok = (rx_delta.get("gw2_rx_packets") or 0) > 0 and (rx_delta.get("gw1_rx_packets") or 0) == 0
    elif expected_rx == "none":
        rx_ok = (rx_delta.get("gw1_rx_packets") or 0) == 0 and (rx_delta.get("gw2_rx_packets") or 0) == 0

    return {
        "name": name,
        "generation": generation,
        "destination": destination,
        "expected_ping": expected_ping,
        "observed_ping": observed_ping,
        "expected_counter": expected_counter,
        "counter_delta": delta,
        "counter_ok": counter_ok,
        "expected_rx": expected_rx,
        "rx_delta": rx_delta,
        "rx_ok": rx_ok,
        "ok": set_gen["returncode"] == 0 and observed_ping == expected_ping and counter_ok and rx_ok,
        "set_generation": set_gen,
        "ping": ping,
        "rx_before": rx_before,
        "rx_after": rx_after,
    }


def run_redirect_suite(names: dict[str, str], object_path: Path, metadata: dict[str, Any], ping_count: int) -> dict[str, Any]:
    attach = attach_section(names, object_path, "classifier/redirect")
    init = initialize_maps(metadata)
    probes: list[dict[str, Any]] = []
    if attach_ok(attach) and init["ok"]:
        probes.extend(
            [
                run_probe(
                    names,
                    name="future_preloaded_gen0_fec_g_to_gw1",
                    generation=0,
                    destination=FEC_G_IP,
                    count=ping_count,
                    expected_ping="pass",
                    expected_counter="future_generation_not_used",
                    expected_rx="gw1",
                ),
                run_probe(
                    names,
                    name="gen1_fec_g_to_gw2",
                    generation=1,
                    destination=FEC_G_IP,
                    count=ping_count,
                    expected_ping="pass",
                    expected_counter="next_hop_gw2",
                    expected_rx="gw2",
                ),
                run_probe(
                    names,
                    name="gen2_fec_g_inactive",
                    generation=2,
                    destination=FEC_G_IP,
                    count=ping_count,
                    expected_ping="drop",
                    expected_counter="drop_inactive",
                    expected_rx="none",
                ),
                run_probe(
                    names,
                    name="missing_fec",
                    generation=0,
                    destination=MISSING_FEC_IP,
                    count=ping_count,
                    expected_ping="drop",
                    expected_counter="drop_missing_fec",
                    expected_rx="none",
                ),
                run_probe(
                    names,
                    name="missing_generation",
                    generation=7,
                    destination=FEC_G_IP,
                    count=ping_count,
                    expected_ping="drop",
                    expected_counter="drop_missing_generation",
                    expected_rx="none",
                ),
                run_probe(
                    names,
                    name="fec_z_local_delivery",
                    generation=0,
                    destination=FEC_Z_IP,
                    count=ping_count,
                    expected_ping="pass",
                    expected_counter="local_delivery",
                    expected_rx="none",
                ),
            ]
        )
    probe_by_name = {probe["name"]: probe for probe in probes}
    gen0 = probe_by_name.get("future_preloaded_gen0_fec_g_to_gw1", {})
    gen1 = probe_by_name.get("gen1_fec_g_to_gw2", {})
    fail_closed_names = ["gen2_fec_g_inactive", "missing_fec", "missing_generation"]
    next_hop_redirect_ok = (
        attach_ok(attach)
        and init["ok"]
        and bool(gen0.get("counter_ok"))
        and bool(gen0.get("rx_ok"))
        and bool(gen1.get("counter_ok"))
        and bool(gen1.get("rx_ok"))
    )
    end_to_end_ping_ok = (
        gen0.get("observed_ping") == "pass" and gen1.get("observed_ping") == "pass"
    )
    fail_closed_ok = all((probe_by_name.get(name) or {}).get("ok") for name in fail_closed_names)
    local_delivery_ok = bool((probe_by_name.get("fec_z_local_delivery") or {}).get("ok"))
    return {
        "attach": attach,
        "attach_ok": attach_ok(attach),
        "initialize_maps": init,
        "probes": probes,
        "counters": read_counters() if attach_ok(attach) else None,
        "next_hop_redirect_ok": next_hop_redirect_ok,
        "end_to_end_ping_ok": end_to_end_ping_ok,
        "fail_closed_ok": fail_closed_ok,
        "local_delivery_ok": local_delivery_ok,
        "ok": next_hop_redirect_ok and fail_closed_ok and local_delivery_ok,
    }


def run_shadow_suite(names: dict[str, str], object_path: Path, metadata: dict[str, Any], ping_count: int) -> dict[str, Any]:
    attach = attach_section(names, object_path, "classifier/shadow")
    init = initialize_maps(metadata)
    probes: list[dict[str, Any]] = []
    if attach_ok(attach) and init["ok"]:
        probes.extend(
            [
                run_probe(
                    names,
                    name="shadow_gen0_fec_g_decides_gw1",
                    generation=0,
                    destination=FEC_G_IP,
                    count=ping_count,
                    expected_ping="drop",
                    expected_counter="next_hop_gw1",
                ),
                run_probe(
                    names,
                    name="shadow_gen1_fec_g_decides_gw2",
                    generation=1,
                    destination=FEC_G_IP,
                    count=ping_count,
                    expected_ping="drop",
                    expected_counter="next_hop_gw2",
                ),
                run_probe(
                    names,
                    name="shadow_gen2_fec_g_inactive",
                    generation=2,
                    destination=FEC_G_IP,
                    count=ping_count,
                    expected_ping="drop",
                    expected_counter="drop_inactive",
                ),
                run_probe(
                    names,
                    name="shadow_missing_fec",
                    generation=0,
                    destination=MISSING_FEC_IP,
                    count=ping_count,
                    expected_ping="drop",
                    expected_counter="drop_missing_fec",
                ),
                run_probe(
                    names,
                    name="shadow_missing_generation",
                    generation=7,
                    destination=FEC_G_IP,
                    count=ping_count,
                    expected_ping="drop",
                    expected_counter="drop_missing_generation",
                ),
                run_probe(
                    names,
                    name="shadow_fec_z_local_delivery",
                    generation=0,
                    destination=FEC_Z_IP,
                    count=ping_count,
                    expected_ping="pass",
                    expected_counter="local_delivery",
                ),
            ]
        )
    return {
        "attach": attach,
        "attach_ok": attach_ok(attach),
        "initialize_maps": init,
        "probes": probes,
        "counters": read_counters() if attach_ok(attach) else None,
        "ok": attach_ok(attach) and init["ok"] and all(probe["counter_ok"] for probe in probes),
    }


def redirect_blocker(redirect_suite: dict[str, Any]) -> str | None:
    if not redirect_suite.get("attach_ok"):
        attach = redirect_suite.get("attach") or []
        stderr = attach[-1].get("stderr") if attach else ""
        return f"tc_redirect_section_attach_failed: {stderr}"
    if not (redirect_suite.get("initialize_maps") or {}).get("ok"):
        return "map_initialization_failed"
    if redirect_suite.get("next_hop_redirect_ok") and not redirect_suite.get("end_to_end_ping_ok"):
        return "end_to_end_ping_reply_failed_after_expected_next_hop_rx"
    failed = [probe["name"] for probe in redirect_suite.get("probes", []) if not probe["ok"]]
    if failed:
        return "redirect_probe_failed: " + ",".join(failed)
    return None


def run_isolated_experiment(source: Path, ping_count: int) -> dict[str, Any]:
    suffix = str(os.getpid())[-5:]
    names = {
        "ns_ut": f"ssgzut{suffix}",
        "ns_fwd": f"ssgzfw{suffix}",
        "ns_gw1": f"ssgzgw1{suffix}",
        "ns_gw2": f"ssgzgw2{suffix}",
        "if_ut": f"sgut{suffix}",
        "if_fwd_ut": f"sgfu{suffix}",
        "if_fwd_gw1": f"sgf1{suffix}",
        "if_gw1": f"sgg1{suffix}",
        "if_fwd_gw2": f"sgf2{suffix}",
        "if_gw2": f"sgg2{suffix}",
    }
    result: dict[str, Any] = {"names": names}
    cleanup(names)
    with tempfile.TemporaryDirectory(prefix="skyseed-bpf-gz-shadow-") as tmp:
        object_path = Path(tmp) / "skyseed_tc_gz_shadow.o"
        result["compile"] = compile_bpf(source, object_path)
        if result["compile"]["returncode"] != 0:
            result["ok"] = False
            result["reason"] = "compile_failed"
            result["cleanup"] = cleanup(names)
            return result
        try:
            setup = setup_netns(names)
            result["setup"] = setup
            failed_setup = [step for step in setup if step["returncode"] != 0]
            if failed_setup:
                result["ok"] = False
                result["reason"] = "setup_failed"
                return result
            metadata = collect_forwarding_metadata(names)
            result["forwarding_metadata"] = {
                key: {inner_key: inner_value for inner_key, inner_value in value.items() if inner_key != "mac_bytes"}
                for key, value in metadata.items()
            }
            result["redirect"] = run_redirect_suite(names, object_path, metadata, ping_count)
            result["redirect_success"] = bool(result["redirect"]["next_hop_redirect_ok"])
            result["redirect_end_to_end_ping_success"] = bool(result["redirect"]["end_to_end_ping_ok"])
            result["redirect_blocker"] = redirect_blocker(result["redirect"])
            result["shadow"] = run_shadow_suite(names, object_path, metadata, ping_count)
            result["ok"] = bool(result["redirect"]["ok"] and result["shadow"]["ok"])
            return result
        finally:
            result["cleanup"] = cleanup(names)


def ping_summary(ping: dict[str, Any]) -> str:
    parsed = ping.get("parsed") or {}
    if "rtt_avg_ms" in parsed:
        return (
            f"rc={ping.get('returncode')} recv={parsed.get('received')}/"
            f"{parsed.get('transmitted')} loss={parsed.get('packet_loss_pct')}% "
            f"avg={parsed.get('rtt_avg_ms')}ms"
        )
    if parsed:
        return (
            f"rc={ping.get('returncode')} recv={parsed.get('received')}/"
            f"{parsed.get('transmitted')} loss={parsed.get('packet_loss_pct')}%"
        )
    return f"rc={ping.get('returncode')}"


def format_probe(probe: dict[str, Any]) -> str:
    return (
        f"{probe['name']}: gen={probe['generation']} expected={probe['expected_ping']} "
        f"observed={probe['observed_ping']} ok={probe['ok']} ping={ping_summary(probe['ping'])}"
    )


def format_text(report: dict[str, Any]) -> str:
    lines = [
        f"Goal: {report['goal']}",
        f"Blocked: {report['blocked']} blockers={report['blockers']}",
        f"tc-BPF attach: {report['tc_bpf_attach']}",
        f"Redirect success: {report.get('redirect_success')}",
        f"Redirect end-to-end ping success: {report.get('redirect_end_to_end_ping_success')}",
        f"Redirect blocker: {report.get('redirect_blocker')}",
        f"Residue clean: {(report.get('residue') or {}).get('clean')}",
    ]
    experiment = report.get("experiment") or {}
    redirect = experiment.get("redirect") or {}
    if redirect.get("probes"):
        lines.append("Redirect probes:")
        lines.extend("  " + format_probe(probe) for probe in redirect["probes"])
    shadow = experiment.get("shadow") or {}
    if shadow.get("probes"):
        lines.append("Shadow probes:")
        lines.extend("  " + format_probe(probe) for probe in shadow["probes"])
    counters = report.get("counter_summary") or {}
    if counters:
        lines.append("Counters:")
        lines.extend(f"  {key}={value}" for key, value in sorted(counters.items()))
    return "\n".join(lines)


def final_counter_summary(experiment: dict[str, Any]) -> dict[str, int]:
    shadow_counters = (((experiment.get("shadow") or {}).get("counters") or {}).get("counters")) or {}
    redirect_counters = (((experiment.get("redirect") or {}).get("counters") or {}).get("counters")) or {}
    return {name: int(shadow_counters.get(name, redirect_counters.get(name, 0))) for name in COUNTERS.values()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bpf-source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--json-out", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--format", choices=["json", "text"], default="json")
    parser.add_argument("--ping-count", type=int, default=2)
    args = parser.parse_args()

    environment = collect_environment()
    blockers = attach_blockers(environment)
    report: dict[str, Any] = {
        "schema_version": 1,
        "goal": "Skyseed Goal 8 isolated G/Z forwarding BPF shadow prototype",
        "timestamp_unix": time.time(),
        "source": str(args.bpf_source),
        "environment": environment,
        "blocked": bool(blockers),
        "blockers": blockers,
        "notes": {
            "canonical_runtime_changed": False,
            "gz_semantics_changed": False,
            "real_dataplane_touched": False,
            "experiment_scope": "isolated netns/veth only",
            "netlink_batch_direction": False,
            "external_kernel_module_installed": False,
        },
    }

    if blockers:
        report["attach_attempted"] = False
        report["tc_bpf_attach"] = {"skipped": True, "reason": "toolchain_or_privilege_blocked"}
        report["redirect_success"] = False
        report["redirect_end_to_end_ping_success"] = False
        report["redirect_blocker"] = "tc_bpf_attach_not_available"
        empty_names = {"ns_ut": "", "ns_fwd": "", "ns_gw1": "", "ns_gw2": ""}
        report["residue"] = residue_check(empty_names)
        report["counter_summary"] = {name: 0 for name in COUNTERS.values()}
    else:
        report["attach_attempted"] = True
        experiment = run_isolated_experiment(args.bpf_source, ping_count=args.ping_count)
        report["experiment"] = experiment
        report["tc_bpf_attach"] = {
            "attempted": True,
            "redirect_attach_ok": bool((experiment.get("redirect") or {}).get("attach_ok")),
            "shadow_attach_ok": bool((experiment.get("shadow") or {}).get("attach_ok")),
            "ok": bool((experiment.get("redirect") or {}).get("attach_ok"))
            and bool((experiment.get("shadow") or {}).get("attach_ok")),
            "reason": experiment.get("reason"),
        }
        report["redirect_success"] = bool(experiment.get("redirect_success"))
        report["redirect_end_to_end_ping_success"] = bool(
            experiment.get("redirect_end_to_end_ping_success")
        )
        report["redirect_blocker"] = experiment.get("redirect_blocker")
        report["counter_summary"] = final_counter_summary(experiment)
        report["residue"] = experiment.get("cleanup", {}).get("residue")

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.format == "text":
        print(format_text(report))
    else:
        print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
