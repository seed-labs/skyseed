#!/usr/bin/env python3
# encoding: utf-8

"""Run an isolated tc-BPF packet-path prototype for Skyseed RuntimeEpoch."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = (
    REPO_ROOT / "satellite" / "experimental" / "bpf" / "skyseed_tc_generation_path.c"
)
DEFAULT_JSON = (
    REPO_ROOT / "satellite" / "baselines" / "skyseed_bpf_packet_path_summary.json"
)
PINNED_MAPS = [
    "active_generation",
    "generation_policy",
    "packet_counters",
    "lookup_pad",
]
PIN_BASE = Path("/sys/fs/bpf/tc/globals")
KERNEL_MAP_NAMES = {
    "active_generation": "active_generati",
    "generation_policy": "generation_poli",
    "packet_counters": "packet_counters",
    "lookup_pad": "lookup_pad",
}
MAP_SPECS = {
    "active_generation": {"type": "array", "bytes_key": 4, "bytes_value": 4, "max_entries": 1},
    "generation_policy": {"type": "array", "bytes_key": 4, "bytes_value": 4, "max_entries": 16},
    "packet_counters": {"type": "array", "bytes_key": 4, "bytes_value": 8, "max_entries": 8},
    "lookup_pad": {"type": "array", "bytes_key": 4, "bytes_value": 4, "max_entries": 4},
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
            "iperf3": tool_status("iperf3", ["--version"]),
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
                "libbpf-dev",
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


def hex_u32(value: int) -> list[str]:
    return [(value & 0xFFFFFFFF).to_bytes(4, "little").hex()[index : index + 2] for index in range(0, 8, 2)]


def update_u32_map(map_name: str, key: int, value: int) -> dict[str, Any]:
    ref_args, ref_meta = map_reference(map_name)
    result = bpftool(
        [
            "map",
            "update",
            *ref_args,
            "key",
            "hex",
            *hex_u32(key),
            "value",
            "hex",
            *hex_u32(value),
        ]
    )
    result["map_reference"] = ref_meta
    return result


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


def cleanup(names: dict[str, str]) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    ns_a = names["ns_a"]
    ns_b = names["ns_b"]
    veth_a = names["veth_a"]

    steps.append(tc_exec(ns_a, ["qdisc", "del", "dev", veth_a, "clsact"], timeout=4.0))
    for map_name in PINNED_MAPS:
        steps.append(
            run_command(["rm", "-f", str(PIN_BASE / map_name)], timeout=4.0, privileged=True)
        )
    steps.append(run_command(["ip", "netns", "del", ns_a], timeout=5.0, privileged=True))
    steps.append(run_command(["ip", "netns", "del", ns_b], timeout=5.0, privileged=True))
    return {"steps": steps, "residue": residue_check(names)}


def privileged_path_exists(path: Path) -> bool:
    return run_command(["test", "-e", str(path)], timeout=4.0, privileged=True)["returncode"] == 0


def residue_check(names: dict[str, str]) -> dict[str, Any]:
    netns = run_command(["ip", "netns", "list"], timeout=4.0, privileged=True)
    links = run_command(["ip", "-o", "link", "show"], timeout=4.0, privileged=True)
    pinned = {name: privileged_path_exists(PIN_BASE / name) for name in PINNED_MAPS}
    namespace_hits = [
        name
        for name in [names["ns_a"], names["ns_b"]]
        if name and name in (netns["stdout"] + netns["stderr"])
    ]
    link_hits = [
        name
        for name in [names["veth_a"], names["veth_b"]]
        if name and name in (links["stdout"] + links["stderr"])
    ]
    return {
        "namespace_hits": namespace_hits,
        "link_hits": link_hits,
        "pinned_maps": pinned,
        "clean": not namespace_hits and not link_hits and not any(pinned.values()),
    }


def setup_netns(names: dict[str, str]) -> list[dict[str, Any]]:
    ns_a = names["ns_a"]
    ns_b = names["ns_b"]
    veth_a = names["veth_a"]
    veth_b = names["veth_b"]
    steps = [
        run_command(["ip", "netns", "add", ns_a], timeout=8.0, privileged=True),
        run_command(["ip", "netns", "add", ns_b], timeout=8.0, privileged=True),
        run_command(
            ["ip", "link", "add", veth_a, "type", "veth", "peer", "name", veth_b],
            timeout=8.0,
            privileged=True,
        ),
        run_command(["ip", "link", "set", veth_a, "netns", ns_a], timeout=8.0, privileged=True),
        run_command(["ip", "link", "set", veth_b, "netns", ns_b], timeout=8.0, privileged=True),
        ns_exec(ns_a, ["ip", "addr", "add", "10.77.0.1/24", "dev", veth_a]),
        ns_exec(ns_b, ["ip", "addr", "add", "10.77.0.2/24", "dev", veth_b]),
        ns_exec(ns_a, ["ip", "link", "set", "lo", "up"]),
        ns_exec(ns_b, ["ip", "link", "set", "lo", "up"]),
        ns_exec(ns_a, ["ip", "link", "set", veth_a, "up"]),
        ns_exec(ns_b, ["ip", "link", "set", veth_b, "up"]),
    ]
    return steps


def attach_section(names: dict[str, str], object_path: Path, section: str) -> list[dict[str, Any]]:
    ns_a = names["ns_a"]
    veth_a = names["veth_a"]
    return [
        tc_exec(ns_a, ["qdisc", "del", "dev", veth_a, "clsact"], timeout=4.0),
        tc_exec(ns_a, ["qdisc", "add", "dev", veth_a, "clsact"], timeout=8.0),
        tc_exec(
            ns_a,
            [
                "filter",
                "add",
                "dev",
                veth_a,
                "egress",
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


def ping_once(names: dict[str, str], *, count: int, timeout_s: int = 1) -> dict[str, Any]:
    result = ns_exec(
        names["ns_a"],
        ["ping", "-c", str(count), "-W", str(timeout_s), "10.77.0.2"],
        timeout=max(6.0, count * (timeout_s + 1.0)),
    )
    result["parsed"] = parse_ping(result["stdout"])
    return result


def initialize_maps(map_names: list[str]) -> dict[str, Any]:
    updates: list[dict[str, Any]] = []
    if "active_generation" in map_names:
        updates.append(update_u32_map("active_generation", 0, 0))
    if "generation_policy" in map_names:
        updates.append(update_u32_map("generation_policy", 0, 1))
        updates.append(update_u32_map("generation_policy", 1, 0))
        updates.append(update_u32_map("generation_policy", 2, 1))
    if "lookup_pad" in map_names:
        updates.append(update_u32_map("lookup_pad", 0, 1))
    return {"ok": all(update["returncode"] == 0 for update in updates), "updates": updates}


def attach_ok(attach: list[dict[str, Any]]) -> bool:
    return bool(attach) and attach[-1]["returncode"] == 0


def generation_switch_test(names: dict[str, str], object_path: Path, ping_count: int) -> dict[str, Any]:
    attach = attach_section(names, object_path, "classifier/generation")
    init = initialize_maps(["active_generation", "generation_policy"])
    phases: list[dict[str, Any]] = []
    for generation in [0, 1, 2]:
        update = update_u32_map("active_generation", 0, generation)
        ping_result = ping_once(names, count=ping_count)
        expected_pass = generation in [0, 2]
        observed_pass = ping_result["returncode"] == 0
        phases.append(
            {
                "generation": generation,
                "expected": "pass" if expected_pass else "drop",
                "observed": "pass" if observed_pass else "drop",
                "ok": expected_pass == observed_pass,
                "map_update": update,
                "ping": ping_result,
            }
        )
    return {
        "attach": attach,
        "attach_ok": attach_ok(attach),
        "initialize_maps": init,
        "phases": phases,
        "counters": dump_map("packet_counters"),
        "ok": attach_ok(attach) and init["ok"] and all(phase["ok"] for phase in phases),
    }


def ping_overhead(names: dict[str, str], object_path: Path, ping_count: int) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    detach = tc_exec(names["ns_a"], ["qdisc", "del", "dev", names["veth_a"], "clsact"], timeout=4.0)
    baseline = ping_once(names, count=ping_count)
    cases.append({"name": "no_bpf_baseline", "detach": detach, "ping": baseline})
    for name, section in [
        ("empty_bpf", "classifier/empty"),
        ("lookup1", "classifier/lookup1"),
        ("lookup3", "classifier/lookup3"),
        ("counter_update", "classifier/counter"),
    ]:
        attach = attach_section(names, object_path, section)
        init_maps = {
            "empty_bpf": [],
            "lookup1": ["active_generation"],
            "lookup3": ["active_generation", "generation_policy", "lookup_pad"],
            "counter_update": [],
        }[name]
        init = initialize_maps(init_maps)
        ping_result = ping_once(names, count=ping_count)
        counters = dump_map("packet_counters") if name == "counter_update" else None
        cases.append(
            {
                "name": name,
                "section": section,
                "attach": attach,
                "attach_ok": attach_ok(attach),
                "initialize_maps": init,
                "ping": ping_result,
                "counters": counters,
            }
        )
    return {"cases": cases}


def throughput_probe(names: dict[str, str]) -> dict[str, Any]:
    if not shutil.which("iperf3"):
        return {"skipped": True, "reason": "missing_iperf3"}
    server_command = ["ip", "netns", "exec", names["ns_b"], "iperf3", "-s", "-1", "-J"]
    if os.geteuid() != 0:
        server_command = ["sudo", "-n"] + server_command
    server = subprocess.Popen(
        server_command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    time.sleep(0.5)
    client = ns_exec(names["ns_a"], ["iperf3", "-c", "10.77.0.2", "-t", "2", "-J"], timeout=8.0)
    try:
        stdout, stderr = server.communicate(timeout=5.0)
    except subprocess.TimeoutExpired:
        server.kill()
        stdout, stderr = server.communicate(timeout=2.0)
    parsed: Any = None
    if client["stdout"]:
        try:
            parsed = json.loads(client["stdout"])
        except json.JSONDecodeError:
            parsed = None
    return {
        "client": client,
        "client_parsed": parsed,
        "server_returncode": server.returncode,
        "server_stdout": stdout.strip(),
        "server_stderr": stderr.strip(),
    }


def run_isolated_experiment(source: Path, ping_count: int, run_throughput: bool) -> dict[str, Any]:
    pid_suffix = str(os.getpid())[-6:]
    names = {
        "ns_a": f"ssbpf7a{pid_suffix}",
        "ns_b": f"ssbpf7b{pid_suffix}",
        "veth_a": f"ssbpf7a{pid_suffix}"[:15],
        "veth_b": f"ssbpf7b{pid_suffix}"[:15],
    }
    result: dict[str, Any] = {"names": names}
    with tempfile.TemporaryDirectory(prefix="skyseed-bpf-packet-") as tmp:
        object_path = Path(tmp) / "skyseed_tc_generation_path.o"
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
            result["generation_switch"] = generation_switch_test(names, object_path, ping_count)
            result["overhead"] = ping_overhead(names, object_path, ping_count)
            result["throughput"] = throughput_probe(names) if run_throughput else {"skipped": True}
            result["ok"] = bool(result["generation_switch"]["ok"])
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


def format_text(report: dict[str, Any]) -> str:
    lines = [
        f"Goal: {report['goal']}",
        f"Blocked: {report['blocked']} blockers={report['blockers']}",
        f"tc-BPF attach: {report['tc_bpf_attach']}",
        f"Residue clean: {(report.get('residue') or {}).get('clean')}",
    ]
    tools = report["environment"]["tools"]
    lines.append(
        "Tools: "
        f"clang={tools['clang'].get('version', tools['clang'].get('path'))!r}; "
        f"llc={tools['llc'].get('version', tools['llc'].get('path'))!r}; "
        f"bpftool={tools['bpftool'].get('version', tools['bpftool'].get('path'))!r}; "
        f"tc={tools['tc'].get('version', tools['tc'].get('path'))!r}"
    )
    if report.get("pass_drop_pass") and not report["pass_drop_pass"].get("skipped"):
        lines.append("Pass/drop/pass:")
        for phase in report["pass_drop_pass"].get("phases", []):
            lines.append(
                "  "
                f"gen{phase['generation']} expected={phase['expected']} "
                f"observed={phase['observed']} ok={phase['ok']} "
                f"ping={ping_summary(phase['ping'])}"
            )
    if report.get("packet_path_overhead") and not report["packet_path_overhead"].get("skipped"):
        lines.append("Packet-path ping overhead cases:")
        for case in report["packet_path_overhead"].get("cases", []):
            lines.append(f"  {case['name']}: {ping_summary(case['ping'])}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bpf-source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--json-out", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--format", choices=["json", "text"], default="json")
    parser.add_argument("--ping-count", type=int, default=5)
    parser.add_argument("--skip-throughput", action="store_true")
    args = parser.parse_args()

    environment = collect_environment()
    blockers = attach_blockers(environment)
    report: dict[str, Any] = {
        "schema_version": 1,
        "goal": "Skyseed Goal 7 RuntimeEpoch BPF packet-path prototype",
        "timestamp_unix": time.time(),
        "source": str(args.bpf_source),
        "environment": environment,
        "blocked": bool(blockers),
        "blockers": blockers,
        "notes": {
            "canonical_runtime_changed": False,
            "real_dataplane_touched": False,
            "experiment_scope": "isolated netns/veth only",
        },
    }

    if blockers:
        report["attach_attempted"] = False
        report["tc_bpf_attach"] = {"skipped": True, "reason": "toolchain_or_privilege_blocked"}
        report["pass_drop_pass"] = {"skipped": True, "reason": "tc_bpf_attach_not_available"}
        report["packet_path_overhead"] = {"skipped": True, "reason": "tc_bpf_attach_not_available"}
        empty_names = {"ns_a": "", "ns_b": "", "veth_a": "", "veth_b": ""}
        report["residue"] = residue_check(empty_names)
    else:
        report["attach_attempted"] = True
        experiment = run_isolated_experiment(
            args.bpf_source,
            ping_count=args.ping_count,
            run_throughput=not args.skip_throughput,
        )
        report["experiment"] = experiment
        report["tc_bpf_attach"] = {
            "attempted": True,
            "generation_attach_ok": bool(
                (experiment.get("generation_switch") or {}).get("attach_ok")
            ),
            "overhead_attach_ok": all(
                case.get("name") == "no_bpf_baseline" or case.get("attach_ok")
                for case in (experiment.get("overhead") or {}).get("cases", [])
            ),
            "ok": bool((experiment.get("generation_switch") or {}).get("attach_ok")),
            "reason": experiment.get("reason"),
        }
        report["pass_drop_pass"] = experiment.get("generation_switch")
        report["packet_path_overhead"] = experiment.get("overhead")
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
