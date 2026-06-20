#!/usr/bin/env python3
# encoding: utf-8

"""Opt-in experimental tc-BPF dataplane canary for bounded Skyseed runs."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from typing import Any, Sequence

from satellite.bpf_backend import compile_from_run_dir, parameter_loss_ppm


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = REPO_ROOT / "satellite" / "experimental" / "bpf" / "skyseed_tc_dataplane_canary.c"
PIN_BASE = Path("/sys/fs/bpf/tc/globals")
CANARY_PINNED_MAPS = [
    "active_generation",
    "generation_status",
    "fec_forwarding",
    "packet_counters",
    "parameter_generation",
    "param_status",
    "parameter_values",
]
KERNEL_MAP_NAMES = {
    "active_generation": "active_generati",
    "generation_status": "generation_stat",
    "fec_forwarding": "fec_forwarding",
    "packet_counters": "packet_counters",
    "parameter_generation": "parameter_gener",
    "param_status": "param_status",
    "parameter_values": "parameter_value",
}
COUNTER_LABELS = {
    0: "pass",
    1: "drop",
    2: "redirect",
    3: "canonical_fallback",
    4: "runtime_generation_hit",
    5: "fec_hit",
    6: "drop_missing_generation",
    7: "drop_missing_fec",
    8: "drop_inactive_fec",
    9: "parameter_generation_hit",
    10: "parameter_entry_hit",
    11: "drop_inactive_parameter",
    12: "drop_loss",
    13: "drop_missing_parameter",
    14: "drop_bad_packet",
}
IPPROTO_ICMP = 1
ACTION_FORWARD = 1
BPFTOOL = ""


def apply_bpf_dataplane_canary(
    *,
    run_dir: Path,
    topology_path: Path,
    link_state_path: Path,
    runtime_report: dict[str, Any],
    bpf_source: Path = DEFAULT_SOURCE,
    ping_count: int = 2,
) -> dict[str, Any]:
    """Attach a single-path live canary, ping through it, read counters, rollback."""

    started_at = time.perf_counter()
    scope = select_canary_scope(
        topology_path=topology_path,
        link_state_path=link_state_path,
        runtime_report=runtime_report,
    )
    report: dict[str, Any] = {
        "schema_version": 1,
        "backend_mode": "experimental_bpf_dataplane_canary",
        "bpf_canary_enabled": True,
        "source": str(bpf_source),
        "run_dir": str(run_dir),
        "scope": scope,
        "qdisc_timeline_backend_status": "blocked_or_trace_only",
        "canonical_runtime_replaced": False,
        "linux_direct_backend_replaced": False,
        "theaterq_or_external_qdisc_module": False,
        "metrics": _empty_metrics(enabled=True),
        "steps": {},
    }
    attached_interfaces: list[str] = []
    rollback_report: dict[str, Any] | None = None

    with tempfile.TemporaryDirectory(prefix="skyseed-bpf-canary-") as tmp:
        object_path = Path(tmp) / "skyseed_tc_dataplane_canary.o"
        try:
            environment = collect_environment()
            report["environment"] = environment
            blockers = attach_blockers(environment)
            report["blockers"] = blockers
            if blockers:
                report["ok"] = False
                report["reason"] = "toolchain_or_privilege_blocked"
                return report

            compiled = compile_from_run_dir(run_dir, input_mode="dataplane_canary")
            report["compiled_summary"] = {
                "runtime_generation_count": compiled["compact_summary"]["runtime_generation_count"],
                "gz_fec_entry_count": compiled["compact_summary"]["gz_fec_entry_count"],
                "parameter_generation_count": compiled["compact_summary"][
                    "parameter_generation_count"
                ],
                "parameter_entry_count": compiled["compact_summary"]["parameter_entry_count"],
                "fixed_10s_assumed": compiled["compact_summary"]["fixed_10s_assumed"],
            }
            report["steps"]["compile"] = compile_bpf(bpf_source, object_path)
            if report["steps"]["compile"]["returncode"] != 0:
                report["ok"] = False
                report["reason"] = "compile_failed"
                return report

            report["steps"]["cleanup_before_attach"] = rollback_canary(
                attached_interfaces=[scope["ut_host_interface"]],
                remove_maps=True,
            )
            attach = attach_canary(scope["ut_host_interface"], object_path)
            report["steps"]["attach"] = attach
            if not command_ok(attach[-1]):
                report["ok"] = False
                report["reason"] = "tc_bpf_attach_failed"
                return report
            attached_interfaces.append(scope["ut_host_interface"])

            map_load = load_canary_maps(
                compiled=compiled,
                scope=scope,
                active_runtime_generation=int(
                    compiled["bpf_maps"]["runtime"]["active_generation"]["value"]
                ),
                active_parameter_generation=int(
                    compiled["bpf_maps"]["parameter"]["parameter_generation"]["value"]
                ),
            )
            report["steps"]["map_load"] = map_load
            if not map_load["ok"]:
                report["ok"] = False
                report["reason"] = "bpf_map_load_failed"
                return report

            ping = docker_ping(
                node_id=scope["ut_id"],
                target_ip=scope["target_ip"],
                count=ping_count,
            )
            report["steps"]["canary_ping"] = ping
            counters = read_packet_counters()
            report["steps"]["counters"] = counters
            report["metrics"] = build_metrics(
                enabled=True,
                attached_interfaces=attached_interfaces,
                loaded_generation_count=int(map_load["loaded_generation_count"]),
                loaded_fec_count=int(map_load["loaded_fec_count"]),
                counters=counters.get("counters", {}),
                rollback_completed=False,
            )
            report["ok"] = (
                command_ok(ping)
                and int(report["metrics"].get("bpf_redirect_counters", 0)) > 0
                and int(report["metrics"].get("bpf_pass_counters", 0)) >= 0
            )
            report["reason"] = None if report["ok"] else "canary_ping_or_counter_check_failed"
            return report
        finally:
            rollback_report = rollback_canary(
                attached_interfaces=attached_interfaces or [scope["ut_host_interface"]],
                remove_maps=True,
            )
            report["rollback"] = rollback_report
            report["metrics"]["bpf_rollback_completed"] = bool(
                rollback_report.get("rollback_completed")
            )
            residue = rollback_report.get("residue", {})
            report["metrics"]["bpf_attached_interface_count_after_rollback"] = len(
                residue.get("clsact_interface_hits", [])
            )
            report["wall_seconds"] = round(time.perf_counter() - started_at, 6)


def _empty_metrics(*, enabled: bool) -> dict[str, Any]:
    return {
        "bpf_canary_enabled": bool(enabled),
        "bpf_attached_interface_count": 0,
        "bpf_loaded_generation_count": 0,
        "bpf_loaded_fec_count": 0,
        "bpf_pass_counters": 0,
        "bpf_drop_counters": 0,
        "bpf_redirect_counters": 0,
        "canonical_fallback_count": 0,
        "bpf_rollback_completed": False,
    }


def build_metrics(
    *,
    enabled: bool,
    attached_interfaces: Sequence[str],
    loaded_generation_count: int,
    loaded_fec_count: int,
    counters: dict[str, int],
    rollback_completed: bool,
) -> dict[str, Any]:
    return {
        "bpf_canary_enabled": bool(enabled),
        "bpf_attached_interface_count": len(list(attached_interfaces)),
        "bpf_loaded_generation_count": int(loaded_generation_count),
        "bpf_loaded_fec_count": int(loaded_fec_count),
        "bpf_pass_counters": int(counters.get("pass", 0)),
        "bpf_drop_counters": int(counters.get("drop", 0)),
        "bpf_redirect_counters": int(counters.get("redirect", 0)),
        "canonical_fallback_count": int(counters.get("canonical_fallback", 0)),
        "bpf_packet_counters": dict(counters),
        "bpf_rollback_completed": bool(rollback_completed),
    }


def select_canary_scope(
    *,
    topology_path: Path,
    link_state_path: Path,
    runtime_report: dict[str, Any],
) -> dict[str, Any]:
    ping = first_successful_ping(runtime_report)
    topology = read_json(topology_path)
    link_state = read_json(link_state_path)
    ut_id = str(ping["ut_id"])
    serving_satellite_id = str(ping["serving_satellite_id"])
    slot_name = f"{ut_id}-access-slot0"
    slot = slot_by_name(topology, slot_name)
    state = slot_state_by_name(link_state, slot_name)
    if not state.get("occupied"):
        raise RuntimeError(f"canary slot is not occupied: {slot_name}")
    binding = dict(state.get("binding") or {})
    if str(binding.get("satellite_id")) != serving_satellite_id:
        serving_satellite_id = str(binding.get("satellite_id") or serving_satellite_id)
    ut_endpoint = dict(slot["preconnected_endpoint"])
    sat_template = dict(slot["dynamic_endpoint_template"])
    satellite_endpoint = {
        **sat_template,
        "node_id": serving_satellite_id,
    }
    ut_host_interface = host_interface_name(
        slot_name=slot_name,
        endpoint=ut_endpoint,
        role="preconnected",
    )
    satellite_host_interface = host_interface_name(
        slot_name=slot_name,
        endpoint=satellite_endpoint,
        role=f"candidate:{serving_satellite_id}",
    )
    return {
        "scope": "single_ut_access_path",
        "ut_id": ut_id,
        "flow_id": str(ping.get("flow_id") or ut_id),
        "target_ip": str(ping["ping_target_ip"]),
        "target_host_node_id": str(ping.get("target_host_node_id") or ""),
        "serving_satellite_id": serving_satellite_id,
        "selected_gateway_id": str(ping.get("selected_gateway_id") or ""),
        "slot_name": slot_name,
        "slot_type": "ut_access",
        "ut_host_interface": ut_host_interface,
        "satellite_host_interface": satellite_host_interface,
        "satellite_ifindex": interface_ifindex(satellite_host_interface),
        "parameter_loss_ppm": parameter_loss_ppm(
            dict(state.get("runtime_parameters") or {}).get("loss_percent")
        ),
        "parameter_mark": stable_u32(f"{slot_name}:{ping['ping_target_ip']}") & 0xFFFF,
        "canonical_fallback_scope": (
            "all packets outside target_ip remain TC_ACT_OK on canonical backend"
        ),
    }


def first_successful_ping(runtime_report: dict[str, Any]) -> dict[str, Any]:
    for iteration in runtime_report.get("iterations", []) or []:
        execution = iteration.get("node_local_execution") or {}
        for ping in execution.get("ping_results", []) or []:
            if (
                isinstance(ping, dict)
                and ping.get("success") is True
                and ping.get("ut_id")
                and ping.get("ping_target_ip")
                and ping.get("serving_satellite_id")
            ):
                return dict(ping)
    initial = runtime_report.get("initial_runtime_readiness") or {}
    for ping in initial.get("flow_results", []) or []:
        if (
            isinstance(ping, dict)
            and ping.get("success") is True
            and ping.get("ut_id")
            and ping.get("ping_target_ip")
            and ping.get("serving_satellite_id")
        ):
            return dict(ping)
    raise RuntimeError("could not select a successful UT ping for BPF canary")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def slot_by_name(topology: dict[str, Any], slot_name: str) -> dict[str, Any]:
    for group in dict(topology.get("slot_inventory", {})).values():
        for slot in group or []:
            if str(slot.get("slot_name")) == slot_name:
                return dict(slot)
    raise RuntimeError(f"missing slot inventory for canary slot {slot_name}")


def slot_state_by_name(link_state: dict[str, Any], slot_name: str) -> dict[str, Any]:
    for slot in link_state.get("slots", []) or []:
        if str(slot.get("slot_name")) == slot_name:
            return dict(slot)
    raise RuntimeError(f"missing link-state slot for canary slot {slot_name}")


def stable_u32(value: str) -> int:
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], byteorder="little", signed=False)


def fingerprint(payload: dict[str, Any]) -> str:
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()[:16]


def short_name(prefix: str, seed: str) -> str:
    return f"{prefix}{fingerprint({'seed': seed})[:11]}"[:15]


def host_interface_name(*, slot_name: str, endpoint: dict[str, Any], role: str) -> str:
    seed = f"{slot_name}:{role}:{endpoint.get('node_id')}:{endpoint.get('interface_name')}"
    return short_name("vhs", seed)


def run_command(
    args: Sequence[str],
    *,
    timeout: float = 10.0,
    privileged: bool = False,
) -> dict[str, Any]:
    command = [str(part) for part in args]
    if privileged and os.geteuid() != 0:
        command = ["sudo", "-n", *command]
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


def command_ok(result: dict[str, Any]) -> bool:
    return int(result.get("returncode", 1)) == 0


def collect_environment() -> dict[str, Any]:
    bpftool_path = resolve_bpftool()
    return {
        "tools": {
            name: shutil.which(name)
            for name in ["clang", "tc", "ip", "docker", "sudo"]
        }
        | {
            "bpftool": bpftool_path,
        },
        "kernel": {
            "bpffs_exists": Path("/sys/fs/bpf").exists(),
            "tc_globals_exists": privileged_path_exists(PIN_BASE),
        },
        "sudo": {
            "uid": os.getuid(),
            "euid": os.geteuid(),
            "noninteractive_sudo": command_ok(run_command(["sudo", "-n", "true"], timeout=4.0)),
        },
    }


def attach_blockers(environment: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    for tool in ["clang", "tc", "ip", "docker", "bpftool"]:
        if not environment["tools"].get(tool):
            blockers.append(f"missing_{tool}")
    if not environment["kernel"].get("bpffs_exists"):
        blockers.append("missing_bpffs")
    if os.geteuid() != 0 and not environment["sudo"].get("noninteractive_sudo"):
        blockers.append("missing_root_or_passwordless_sudo")
    return blockers


def resolve_bpftool() -> str | None:
    candidates: list[str] = []
    path = shutil.which("bpftool")
    if path:
        candidates.append(path)
    candidates.extend(str(candidate) for candidate in sorted(Path("/usr/lib/linux-tools").glob("*/bpftool")))
    candidates.extend(str(candidate) for candidate in sorted(Path("/usr/lib").glob("linux-tools-*/bpftool")))
    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        result = run_command([candidate, "version"], timeout=5.0)
        if command_ok(result):
            return candidate
    return path


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


def attach_canary(interface_name: str, object_path: Path) -> list[dict[str, Any]]:
    return [
        run_command(
            ["tc", "qdisc", "del", "dev", interface_name, "clsact"],
            timeout=4.0,
            privileged=True,
        ),
        run_command(
            ["tc", "qdisc", "add", "dev", interface_name, "clsact"],
            timeout=8.0,
            privileged=True,
        ),
        run_command(
            [
                "tc",
                "filter",
                "add",
                "dev",
                interface_name,
                "ingress",
                "bpf",
                "da",
                "obj",
                str(object_path),
                "sec",
                "classifier/canary",
            ],
            timeout=10.0,
            privileged=True,
        ),
    ]


def rollback_canary(
    *,
    attached_interfaces: Sequence[str],
    remove_maps: bool,
) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    for interface_name in sorted({str(name) for name in attached_interfaces if str(name)}):
        steps.append(
            run_command(
                ["tc", "qdisc", "del", "dev", interface_name, "clsact"],
                timeout=4.0,
                privileged=True,
            )
        )
    if remove_maps:
        for map_name in CANARY_PINNED_MAPS:
            steps.append(
                run_command(
                    ["rm", "-f", str(PIN_BASE / map_name)],
                    timeout=4.0,
                    privileged=True,
                )
            )
    residue = residue_check(attached_interfaces)
    return {
        "steps": steps,
        "residue": residue,
        "rollback_completed": bool(residue["clean"]),
    }


def residue_check(attached_interfaces: Sequence[str]) -> dict[str, Any]:
    qdisc_hits: list[str] = []
    for interface_name in sorted({str(name) for name in attached_interfaces if str(name)}):
        qdisc = run_command(
            ["tc", "qdisc", "show", "dev", interface_name],
            timeout=4.0,
            privileged=True,
        )
        if "clsact" in (qdisc.get("stdout", "") + qdisc.get("stderr", "")):
            qdisc_hits.append(interface_name)
    pinned = {name: privileged_path_exists(PIN_BASE / name) for name in CANARY_PINNED_MAPS}
    return {
        "clsact_interface_hits": qdisc_hits,
        "pinned_maps": pinned,
        "clean": not qdisc_hits and not any(pinned.values()),
    }


def privileged_path_exists(path: Path) -> bool:
    return command_ok(run_command(["test", "-e", str(path)], timeout=4.0, privileged=True))


def interface_ifindex(interface_name: str) -> int:
    result = run_command(
        ["ip", "-o", "link", "show", "dev", interface_name],
        timeout=4.0,
        privileged=True,
    )
    if not command_ok(result):
        raise RuntimeError(f"missing canary interface {interface_name}: {result['stderr']}")
    prefix = result["stdout"].split(":", 1)[0].strip()
    return int(prefix)


def docker_ping(*, node_id: str, target_ip: str, count: int) -> dict[str, Any]:
    return run_command(
        ["docker", "exec", node_id, "ping", "-n", "-c", str(count), "-W", "2", target_ip],
        timeout=max(8.0, count * 4.0),
    )


def load_canary_maps(
    *,
    compiled: dict[str, Any],
    scope: dict[str, Any],
    active_runtime_generation: int,
    active_parameter_generation: int,
) -> dict[str, Any]:
    updates: list[dict[str, Any]] = []
    runtime_status = list(compiled["bpf_maps"]["runtime"]["generation_status"])
    parameter_status = list(compiled["bpf_maps"]["parameter"]["param_status"])
    max_runtime_generations = min(64, len(runtime_status))
    max_parameter_generations = max(1, min(64, len(parameter_status)))
    updates.append(update_u32_map("active_generation", 0, active_runtime_generation))
    for generation in runtime_status[:max_runtime_generations]:
        updates.append(
            update_u32_map(
                "generation_status",
                int(generation["generation_id"]),
                1 if generation.get("installed") else 0,
            )
        )
    dst_ipv4 = ipv4_little_endian_u32(scope["target_ip"])
    route_key = pack_u32s(active_runtime_generation, dst_ipv4)
    route_value = pack_u32s(1, ACTION_FORWARD, int(scope["satellite_ifindex"]))
    updates.append(update_hash_map("fec_forwarding", route_key, route_value))

    updates.append(update_u32_map("parameter_generation", 0, active_parameter_generation))
    if parameter_status:
        for generation in parameter_status[:max_parameter_generations]:
            updates.append(
                update_u32_map(
                    "param_status",
                    int(generation["generation_id"]),
                    1 if generation.get("installed") else 0,
                )
            )
    else:
        updates.append(update_u32_map("param_status", active_parameter_generation, 1))
    parameter_key = pack_u32s(active_parameter_generation, dst_ipv4, IPPROTO_ICMP)
    parameter_value = pack_u32s(
        1,
        int(scope.get("parameter_loss_ppm", 0)),
        int(scope.get("parameter_mark", 0)),
    )
    updates.append(update_hash_map("parameter_values", parameter_key, parameter_value))
    return {
        "updates": updates,
        "ok": all(command_ok(update) for update in updates),
        "loaded_generation_count": max_runtime_generations,
        "loaded_parameter_generation_count": max_parameter_generations,
        "loaded_fec_count": 1,
        "target_dst_ipv4_u32": dst_ipv4,
    }


def update_u32_map(map_name: str, key: int, value: int) -> dict[str, Any]:
    return update_hash_map(map_name, pack_u32s(key), pack_u32s(value))


def update_hash_map(map_name: str, key_bytes: list[str], value_bytes: list[str]) -> dict[str, Any]:
    ref_args, ref_meta = map_reference(map_name)
    result = bpftool(
        [
            "map",
            "update",
            *ref_args,
            "key",
            "hex",
            *key_bytes,
            "value",
            "hex",
            *value_bytes,
        ],
        timeout=8.0,
    )
    result["map_reference"] = ref_meta
    return result


def map_reference(map_name: str) -> tuple[list[str], dict[str, Any]]:
    pinned = PIN_BASE / map_name
    if privileged_path_exists(pinned):
        return ["pinned", str(pinned)], {"kind": "pinned", "path": str(pinned)}
    maps = bpftool_json(["-j", "map", "show"])
    kernel_name = KERNEL_MAP_NAMES.get(map_name, map_name[:15])
    matches = [
        item
        for item in maps
        if isinstance(item, dict) and item.get("name") == kernel_name
    ]
    if matches:
        selected = sorted(matches, key=lambda item: int(item.get("id", -1)))[-1]
        return ["id", str(selected["id"])], {
            "kind": "id",
            "logical_name": map_name,
            "kernel_name": kernel_name,
            "id": selected["id"],
        }
    return ["pinned", str(pinned)], {
        "kind": "missing",
        "logical_name": map_name,
        "kernel_name": kernel_name,
    }


def bpftool(args: Sequence[str], *, timeout: float = 10.0) -> dict[str, Any]:
    global BPFTOOL
    if not BPFTOOL:
        BPFTOOL = resolve_bpftool() or "bpftool"
    return run_command([BPFTOOL, *args], timeout=timeout, privileged=True)


def bpftool_json(args: Sequence[str]) -> list[Any]:
    result = bpftool(args, timeout=8.0)
    if not result.get("stdout"):
        return []
    try:
        parsed = json.loads(result["stdout"])
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def read_packet_counters() -> dict[str, Any]:
    ref_args, ref_meta = map_reference("packet_counters")
    result = bpftool(["-j", "map", "dump", *ref_args], timeout=8.0)
    counters: dict[str, int] = {label: 0 for label in COUNTER_LABELS.values()}
    parsed: Any = None
    if result.get("stdout"):
        try:
            parsed = json.loads(result["stdout"])
        except json.JSONDecodeError:
            parsed = None
    for entry in parsed if isinstance(parsed, list) else []:
        key = decode_bpftool_hex_u32(entry.get("key"))
        value = decode_bpftool_hex_u64(entry.get("value"))
        label = COUNTER_LABELS.get(key)
        if label is not None:
            counters[label] = value
    return {
        "command": result,
        "map_reference": ref_meta,
        "parsed": parsed,
        "counters": counters,
    }


def decode_bpftool_hex_u32(value: Any) -> int:
    raw = bytes(int(part, 16) for part in list(value or [])[:4])
    return int.from_bytes(raw.ljust(4, b"\0"), byteorder="little", signed=False)


def decode_bpftool_hex_u64(value: Any) -> int:
    raw = bytes(int(part, 16) for part in list(value or [])[:8])
    return int.from_bytes(raw.ljust(8, b"\0"), byteorder="little", signed=False)


def ipv4_little_endian_u32(value: str) -> int:
    return int.from_bytes(ipaddress.IPv4Address(value).packed, byteorder="little")


def pack_u32s(*values: int) -> list[str]:
    raw = b"".join((int(value) & 0xFFFFFFFF).to_bytes(4, "little") for value in values)
    return [f"{byte:02x}" for byte in raw]
