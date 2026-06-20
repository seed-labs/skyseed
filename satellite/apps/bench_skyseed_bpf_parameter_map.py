#!/usr/bin/env python3
# encoding: utf-8

"""Run an isolated tc-BPF ParameterEpoch parameter-map prototype for Skyseed."""

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
DEFAULT_SOURCE = REPO_ROOT / "satellite" / "experimental" / "bpf" / "skyseed_tc_parameter_map.c"
DEFAULT_JSON = REPO_ROOT / "satellite" / "baselines" / "skyseed_bpf_parameter_map_summary.json"
PIN_BASE = Path("/sys/fs/bpf/tc/globals")

PINNED_MAPS = [
    "parameter_generation",
    "param_status",
    "parameter_values",
    "parameter_counters",
    "future_param_gen",
]
KERNEL_MAP_NAMES = {
    "parameter_generation": "parameter_gener",
    "param_status": "param_status",
    "parameter_values": "parameter_value",
    "parameter_counters": "parameter_count",
    "future_param_gen": "future_param_ge",
}
MAP_SPECS = {
    "parameter_generation": {"type": "array", "bytes_key": 4, "bytes_value": 4, "max_entries": 1},
    "param_status": {"type": "array", "bytes_key": 4, "bytes_value": 4, "max_entries": 8},
    "parameter_values": {"type": "hash", "bytes_key": 12, "bytes_value": 12, "max_entries": 128},
    "parameter_counters": {"type": "array", "bytes_key": 4, "bytes_value": 8, "max_entries": 16},
    "future_param_gen": {"type": "array", "bytes_key": 4, "bytes_value": 4, "max_entries": 2},
}

PRIMARY_EDGE_IP = "10.92.0.2"
MISSING_EDGE_IP = "10.92.0.99"
ICMP_FLOW = 1
EDGE_PRIMARY = 1
EDGE_MISSING = 99
LOSS_PPM_ALL = 1_000_000

COUNTERS = {
    0: "parameter_generation_hit",
    1: "parameter_entry_hit",
    2: "pass_count",
    3: "drop_inactive",
    4: "drop_loss",
    5: "drop_missing_generation",
    6: "drop_missing_parameter",
    7: "future_generation_not_used",
    8: "drop_bad_packet",
}


def run_command(args: list[str], *, timeout: float = 10.0, privileged: bool = False) -> dict[str, Any]:
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


def collect_environment() -> dict[str, Any]:
    bpffs = Path("/sys/fs/bpf")
    return {
        "tools": {
            "clang": tool_status("clang", ["--version"]),
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
        },
        "sudo": sudo_status(),
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
    shape_matches = [item for item in maps if all(item.get(key) == value for key, value in spec.items())]
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


def update_parameter_map(
    generation: int,
    edge: int,
    flow: int,
    *,
    active: int,
    loss_ppm: int,
    mark: int,
) -> dict[str, Any]:
    key = struct.pack("<III", generation, edge, flow)
    value = struct.pack("<III", active, loss_ppm, mark)
    return update_raw_map("parameter_values", key, value)


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
    dumped = dump_map("parameter_counters")
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


def cleanup(names: dict[str, str]) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    if names.get("ns_server") and names.get("if_server"):
        steps.append(tc_exec(names["ns_server"], ["qdisc", "del", "dev", names["if_server"], "clsact"], timeout=4.0))
    for map_name in PINNED_MAPS:
        steps.append(run_command(["rm", "-f", str(PIN_BASE / map_name)], timeout=4.0, privileged=True))
    for key in ["ns_client", "ns_server"]:
        if names.get(key):
            steps.append(run_command(["ip", "netns", "del", names[key]], timeout=5.0, privileged=True))
    return {"steps": steps, "residue": residue_check(names)}


def residue_check(names: dict[str, str]) -> dict[str, Any]:
    netns = run_command(["ip", "netns", "list"], timeout=4.0, privileged=True)
    links = run_command(["ip", "-o", "link", "show"], timeout=4.0, privileged=True)
    pinned = {name: privileged_path_exists(PIN_BASE / name) for name in PINNED_MAPS}
    namespace_names = [names.get(key, "") for key in ["ns_client", "ns_server"]]
    link_names = [names.get(key, "") for key in ["if_client", "if_server"]]
    namespace_hits = [name for name in namespace_names if name and name in (netns["stdout"] + netns["stderr"])]
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
        run_command(["ip", "netns", "add", names["ns_client"]], timeout=8.0, privileged=True),
        run_command(["ip", "netns", "add", names["ns_server"]], timeout=8.0, privileged=True),
        run_command(
            ["ip", "link", "add", names["if_client"], "type", "veth", "peer", "name", names["if_server"]],
            timeout=8.0,
            privileged=True,
        ),
        run_command(["ip", "link", "set", names["if_client"], "netns", names["ns_client"]], timeout=8.0, privileged=True),
        run_command(["ip", "link", "set", names["if_server"], "netns", names["ns_server"]], timeout=8.0, privileged=True),
    ]
    for namespace in [names["ns_client"], names["ns_server"]]:
        steps.append(ns_exec(namespace, ["ip", "link", "set", "lo", "up"]))
        steps.append(ns_exec(namespace, ["sysctl", "-q", "-w", "net.ipv6.conf.all.disable_ipv6=1"]))
        steps.append(ns_exec(namespace, ["sysctl", "-q", "-w", "net.ipv6.conf.default.disable_ipv6=1"]))
    steps.extend(
        [
            ns_exec(names["ns_client"], ["ip", "addr", "add", "10.92.0.1/24", "dev", names["if_client"]]),
            ns_exec(names["ns_server"], ["ip", "addr", "add", f"{PRIMARY_EDGE_IP}/24", "dev", names["if_server"]]),
            ns_exec(names["ns_server"], ["ip", "addr", "add", f"{MISSING_EDGE_IP}/32", "dev", names["if_server"]]),
            ns_exec(names["ns_client"], ["ip", "link", "set", names["if_client"], "up"]),
            ns_exec(names["ns_server"], ["ip", "link", "set", names["if_server"], "up"]),
        ]
    )
    return steps


def attach_section(names: dict[str, str], object_path: Path) -> list[dict[str, Any]]:
    return [
        tc_exec(names["ns_server"], ["qdisc", "del", "dev", names["if_server"], "clsact"], timeout=4.0),
        tc_exec(names["ns_server"], ["qdisc", "add", "dev", names["if_server"], "clsact"], timeout=8.0),
        tc_exec(
            names["ns_server"],
            [
                "filter",
                "add",
                "dev",
                names["if_server"],
                "ingress",
                "bpf",
                "da",
                "obj",
                str(object_path),
                "sec",
                "classifier/parameter",
            ],
            timeout=10.0,
        ),
    ]


def attach_ok(attach: list[dict[str, Any]]) -> bool:
    return bool(attach) and attach[-1]["returncode"] == 0


def initialize_maps() -> dict[str, Any]:
    updates = [
        update_u32_map("parameter_generation", 0, 0),
        update_u32_map("future_param_gen", 0, 1),
        update_u32_map("param_status", 0, 1),
        update_u32_map("param_status", 1, 1),
        update_u32_map("param_status", 2, 1),
        update_parameter_map(0, EDGE_PRIMARY, ICMP_FLOW, active=1, loss_ppm=0, mark=0x1100),
        update_parameter_map(1, EDGE_PRIMARY, ICMP_FLOW, active=0, loss_ppm=0, mark=0x1101),
        update_parameter_map(2, EDGE_PRIMARY, ICMP_FLOW, active=1, loss_ppm=LOSS_PPM_ALL, mark=0x1102),
    ]
    return {"ok": all(update["returncode"] == 0 for update in updates), "updates": updates}


def set_generation(generation: int) -> dict[str, Any]:
    return update_u32_map("parameter_generation", 0, generation)


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
        names["ns_client"],
        ["ping", "-c", str(count), "-W", str(timeout_s), ip],
        timeout=max(6.0, count * (timeout_s + 1.0)),
    )
    result["parsed"] = parse_ping(result["stdout"])
    return result


def ping_passed(ping: dict[str, Any]) -> bool:
    parsed = ping.get("parsed") or {}
    return ping.get("returncode") == 0 and parsed.get("received", 0) > 0


def run_probe(
    names: dict[str, str],
    *,
    name: str,
    generation: int,
    destination: str,
    count: int,
    expected_ping: str,
    expected_counters: list[str],
) -> dict[str, Any]:
    set_gen = set_generation(generation)
    counters_before = read_counters()["counters"]
    ping = ping_ip(names, destination, count=count)
    counters_after = read_counters()["counters"]
    delta = counter_delta(counters_before, counters_after)
    observed_ping = "pass" if ping_passed(ping) else "drop"
    counter_ok = all(delta.get(counter, 0) > 0 for counter in expected_counters)
    return {
        "name": name,
        "generation": generation,
        "destination": destination,
        "expected_ping": expected_ping,
        "observed_ping": observed_ping,
        "expected_counters": expected_counters,
        "counter_delta": delta,
        "counter_ok": counter_ok,
        "ok": set_gen["returncode"] == 0 and observed_ping == expected_ping and counter_ok,
        "set_generation": set_gen,
        "ping": ping,
    }


def run_parameter_suite(names: dict[str, str], object_path: Path, ping_count: int, loss_count: int) -> dict[str, Any]:
    attach = attach_section(names, object_path)
    init = initialize_maps()
    probes: list[dict[str, Any]] = []
    if attach_ok(attach) and init["ok"]:
        probes.extend(
            [
                run_probe(
                    names,
                    name="gen0_future_gen1_preloaded_active_pass",
                    generation=0,
                    destination=PRIMARY_EDGE_IP,
                    count=ping_count,
                    expected_ping="pass",
                    expected_counters=[
                        "parameter_generation_hit",
                        "parameter_entry_hit",
                        "pass_count",
                        "future_generation_not_used",
                    ],
                ),
                run_probe(
                    names,
                    name="gen1_inactive_drop",
                    generation=1,
                    destination=PRIMARY_EDGE_IP,
                    count=ping_count,
                    expected_ping="drop",
                    expected_counters=["parameter_generation_hit", "parameter_entry_hit", "drop_inactive"],
                ),
                run_probe(
                    names,
                    name="gen2_active_loss_drop",
                    generation=2,
                    destination=PRIMARY_EDGE_IP,
                    count=loss_count,
                    expected_ping="drop",
                    expected_counters=["parameter_generation_hit", "parameter_entry_hit", "drop_loss"],
                ),
                run_probe(
                    names,
                    name="missing_generation_fail_closed",
                    generation=7,
                    destination=PRIMARY_EDGE_IP,
                    count=ping_count,
                    expected_ping="drop",
                    expected_counters=["drop_missing_generation"],
                ),
                run_probe(
                    names,
                    name="missing_parameter_fail_closed",
                    generation=0,
                    destination=MISSING_EDGE_IP,
                    count=ping_count,
                    expected_ping="drop",
                    expected_counters=["parameter_generation_hit", "drop_missing_parameter"],
                ),
            ]
        )
    probe_by_name = {probe["name"]: probe for probe in probes}
    return {
        "attach": attach,
        "attach_ok": attach_ok(attach),
        "initialize_maps": init,
        "probes": probes,
        "counters": read_counters() if attach_ok(attach) else None,
        "future_generation_not_used_ok": bool(
            (probe_by_name.get("gen0_future_gen1_preloaded_active_pass") or {}).get("ok")
        ),
        "gen0_ok": bool((probe_by_name.get("gen0_future_gen1_preloaded_active_pass") or {}).get("ok")),
        "gen1_ok": bool((probe_by_name.get("gen1_inactive_drop") or {}).get("ok")),
        "gen2_ok": bool((probe_by_name.get("gen2_active_loss_drop") or {}).get("ok")),
        "fail_closed_ok": bool((probe_by_name.get("missing_generation_fail_closed") or {}).get("ok"))
        and bool((probe_by_name.get("missing_parameter_fail_closed") or {}).get("ok")),
        "ok": attach_ok(attach) and init["ok"] and all(probe["ok"] for probe in probes),
    }


def run_isolated_experiment(source: Path, ping_count: int, loss_count: int) -> dict[str, Any]:
    suffix = str(os.getpid())[-5:]
    names = {
        "ns_client": f"sspmcl{suffix}",
        "ns_server": f"sspmsv{suffix}",
        "if_client": f"spmc{suffix}",
        "if_server": f"spms{suffix}",
    }
    result: dict[str, Any] = {"names": names}
    cleanup(names)
    with tempfile.TemporaryDirectory(prefix="skyseed-bpf-parameter-map-") as tmp:
        object_path = Path(tmp) / "skyseed_tc_parameter_map.o"
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
            result["parameter_suite"] = run_parameter_suite(names, object_path, ping_count, loss_count)
            result["ok"] = bool(result["parameter_suite"]["ok"])
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
        f"{probe['name']}: gen={probe['generation']} dst={probe['destination']} "
        f"expected={probe['expected_ping']} observed={probe['observed_ping']} "
        f"ok={probe['ok']} ping={ping_summary(probe['ping'])}"
    )


def final_counter_summary(experiment: dict[str, Any]) -> dict[str, int]:
    counters = (
        (((experiment.get("parameter_suite") or {}).get("counters") or {}).get("counters"))
        or {}
    )
    return {name: int(counters.get(name, 0)) for name in COUNTERS.values()}


def format_text(report: dict[str, Any]) -> str:
    lines = [
        f"Goal: {report['goal']}",
        f"Blocked: {report['blocked']} blockers={report['blockers']}",
        f"tc-BPF attach: {report['tc_bpf_attach']}",
        f"Parameter suite ok: {report.get('parameter_suite_ok')}",
        f"Residue clean: {(report.get('residue') or {}).get('clean')}",
    ]
    experiment = report.get("experiment") or {}
    suite = experiment.get("parameter_suite") or {}
    if suite.get("probes"):
        lines.append("Parameter probes:")
        lines.extend("  " + format_probe(probe) for probe in suite["probes"])
    counters = report.get("counter_summary") or {}
    if counters:
        lines.append("Counters:")
        lines.extend(f"  {key}={value}" for key, value in sorted(counters.items()))
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bpf-source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--json-out", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--format", choices=["json", "text"], default="json")
    parser.add_argument("--ping-count", type=int, default=2)
    parser.add_argument("--loss-count", type=int, default=3)
    args = parser.parse_args()

    environment = collect_environment()
    blockers = attach_blockers(environment)
    report: dict[str, Any] = {
        "schema_version": 1,
        "goal": "Skyseed Goal 11 isolated ParameterEpoch BPF parameter-map prototype",
        "timestamp_unix": time.time(),
        "source": str(args.bpf_source),
        "environment": environment,
        "blocked": bool(blockers),
        "blockers": blockers,
        "notes": {
            "canonical_runtime_changed": False,
            "runtime_epoch_scheduler_changed": False,
            "parameter_epoch_scheduler_changed": False,
            "real_dataplane_touched": False,
            "experiment_scope": "isolated netns/veth only",
            "qdisc_timeline_changed": False,
            "delay_bandwidth_rate_reordering_modeled": False,
            "external_kernel_module_installed": False,
            "satellite_output_written": False,
        },
    }

    if blockers:
        report["attach_attempted"] = False
        report["tc_bpf_attach"] = {"skipped": True, "reason": "toolchain_or_privilege_blocked"}
        report["parameter_suite_ok"] = False
        empty_names = {"ns_client": "", "ns_server": "", "if_client": "", "if_server": ""}
        report["residue"] = residue_check(empty_names)
        report["counter_summary"] = {name: 0 for name in COUNTERS.values()}
    else:
        report["attach_attempted"] = True
        experiment = run_isolated_experiment(args.bpf_source, args.ping_count, args.loss_count)
        suite = experiment.get("parameter_suite") or {}
        report["experiment"] = experiment
        report["tc_bpf_attach"] = {
            "attempted": True,
            "parameter_attach_ok": bool(suite.get("attach_ok")),
            "ok": bool(suite.get("attach_ok")),
            "reason": experiment.get("reason"),
        }
        report["parameter_suite_ok"] = bool(suite.get("ok"))
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
