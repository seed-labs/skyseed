#!/usr/bin/env python3
# encoding: utf-8

"""Live tc-BPF loader for the strict MPLS-like Skyseed dataplane prototype."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import ipaddress
import json
from pathlib import Path
import shutil
import tempfile
import time
from typing import Any, Mapping, Sequence

from skyseed_v1.backends.bpf.canary import (
    PIN_BASE,
    bpftool,
    bpftool_json,
    command_ok,
    decode_bpftool_hex_u32,
    decode_bpftool_hex_u32_pair,
    decode_bpftool_hex_u64,
    interface_ifindex as docker_or_host_ifindex,
    ipv4_little_endian_u32,
    mac_bytes,
    pack_u32s,
    privileged_path_exists,
    run_command,
    run_interface_command,
)
from skyseed_v1.compilers.bpf_mpls import G_TO_Z, Z_TO_G, stable_u32
from skyseed_v1.operations.paths import PROJECT_CACHE_ROOT


DEFAULT_MPLS_SOURCE = Path(__file__).resolve().parent / "native" / "skyseed_tc_mpls_dataplane.c"
MPLS_PINNED_MAPS = [
    "active_generation",
    "ingress_classifier_map",
    "ut_gateway_policy_map",
    "gateway_selector_ingress_map",
    "core_lfib_map",
    "egress_handoff_map",
    "zone_edge_delivery_map",
    "generation_base_map",
    "packet_counters",
    "interface_counters",
    "interface_role_map",
    "miss_samples",
]
MPLS_KERNEL_MAP_NAMES = {
    "active_generation": "active_generati",
    "ingress_classifier_map": "ingress_classif",
    "ut_gateway_policy_map": "ut_gateway_poli",
    "gateway_selector_ingress_map": "gateway_selecto",
    "core_lfib_map": "core_lfib_map",
    "egress_handoff_map": "egress_handoff_",
    "zone_edge_delivery_map": "zone_edge_deliv",
    "generation_base_map": "generation_base",
    "packet_counters": "packet_counters",
    "interface_counters": "interface_count",
    "interface_role_map": "interface_role_",
    "miss_samples": "miss_samples",
}
MPLS_MAP_SHAPES = {
    "active_generation": {
        "type": "array",
        "bytes_key": 4,
        "bytes_value": 4,
        "max_entries": 1,
    },
    "ingress_classifier_map": {
        "type": "hash",
        "bytes_key": 16,
        "bytes_value": 36,
        "max_entries": 128,
    },
    "ut_gateway_policy_map": {
        "type": "lpm_trie",
        "bytes_key": 12,
        "bytes_value": 8,
        "max_entries": 8192,
    },
    "gateway_selector_ingress_map": {
        "type": "hash",
        "bytes_key": 16,
        "bytes_value": 28,
        "max_entries": 8192,
    },
    "core_lfib_map": {
        "type": "hash",
        "bytes_key": 16,
        "bytes_value": 28,
        "max_entries": 256,
    },
    "egress_handoff_map": {
        "type": "hash",
        "bytes_key": 16,
        "bytes_value": 32,
        "max_entries": 128,
    },
    "zone_edge_delivery_map": {
        "type": "hash",
        "bytes_key": 16,
        "bytes_value": 24,
        "max_entries": 8192,
    },
    "generation_base_map": {
        "type": "hash",
        "bytes_key": 4,
        "bytes_value": 4,
        "max_entries": 8192,
    },
    "packet_counters": {
        "type": "array",
        "bytes_key": 4,
        "bytes_value": 8,
        "max_entries": 23,
    },
    "interface_counters": {
        "type": "hash",
        "bytes_key": 8,
        "bytes_value": 8,
        "max_entries": 8192,
    },
    "interface_role_map": {
        "type": "hash",
        "bytes_key": 4,
        "bytes_value": 4,
        "max_entries": 8192,
    },
    "miss_samples": {
        "type": "array",
        "bytes_key": 4,
        "bytes_value": 80,
        "max_entries": 32,
    },
}
GENERATION_KEYED_MPLS_MAPS = {
    "ingress_classifier_map",
    "gateway_selector_ingress_map",
    "core_lfib_map",
    "egress_handoff_map",
    "zone_edge_delivery_map",
}
GENERATION_CAPACITY_SLACK = 16
BPF_MAP_GENERATION_CAPACITY_BLOCKER = "BPF_MAP_GENERATION_CAPACITY_BLOCKER"
MPLS_COUNTER_LABELS = {
    0: "classifier_hit",
    1: "label_push",
    2: "core_lfib_hit",
    3: "label_swap",
    4: "egress_handoff_hit",
    5: "label_pop",
    6: "miss",
    7: "drop_missing_label",
    8: "drop_missing_generation",
    9: "drop_bad_packet",
    10: "redirect",
    11: "pass",
    12: "drop",
    13: "pop_pull_fail",
    14: "pop_proto_fail",
    15: "pop_adjust_fail",
    16: "pop_manual_fallback",
    17: "pop_manual_fail",
    18: "non_gz_ip_pass",
    19: "unknown_role_count",
    20: "role_map_missing_count",
    21: "gz_ip_pass_without_classification",
    22: "mpls_shim_pass_without_lfib_or_handoff",
}
MPLS_DIRECTION = {
    G_TO_Z: 1,
    Z_TO_G: 2,
    "g_to_z": 1,
    "z_to_g": 2,
}
MPLS_CORE_ACTION = {
    "swap": 1,
    "preserve": 2,
}
ETH_P_IP = 0x0800
MPLS_INTERFACE_ROLE_CLASSIFIER = 1
MPLS_INTERFACE_ROLE_MPLS = 2
MPLS_INTERFACE_ROLE_UT_GATEWAY_SELECTOR = 4
MPLS_MISS_SAMPLE_REASONS = {
    1: "classifier_miss",
    2: "mpls_lfib_or_handoff_miss",
    3: "missing_interface_role",
    4: "unknown_interface_role",
    5: "missing_generation",
    6: "bad_packet",
    7: "missing_or_bad_mpls_label",
}
MPLS_MISS_SAMPLE_MAPS = {
    1: "ingress_classifier_map",
    2: "core_lfib_map",
    3: "egress_handoff_map",
    4: "active_generation",
    5: "interface_role_map",
    6: "packet_parse",
    7: "zone_edge_delivery_map",
}
MPLS_MISS_SAMPLE_SLOTS = {
    0: "classifier",
    1: "mpls_lfib_handoff",
    2: "missing_role",
    3: "unknown_role",
    4: "missing_generation",
    5: "bad_packet",
    6: "missing_label",
}


def compile_mpls_bpf(
    source: Path = DEFAULT_MPLS_SOURCE,
    object_path: Path | None = None,
    *,
    capacities: dict[str, int] | None = None,
    cache_dir: Path | None = None,
) -> dict[str, Any]:
    capacities = {
        "classifiers": 128,
        "gateway_selectors": 8192,
        "ut_gateway_policies": 8192,
        "lfib": 256,
        "handoffs": 128,
        "zone_edge_deliveries": 8192,
        "packet_counters": 23,
        "interface_counter_entries": 8192,
        "generation_bases": 8192,
        **dict(capacities or {}),
    }
    if object_path is None:
        object_path = Path(tempfile.mkdtemp(prefix="skyseed-mpls-bpf-")) / (
            "skyseed_tc_mpls_dataplane.o"
        )
    include_args: list[str] = []
    arch_include = Path("/usr/include/x86_64-linux-gnu")
    if arch_include.exists():
        include_args = ["-I", str(arch_include)]
    define_args = [
        f"-DSKYSEED_BPF_MAX_MPLS_CLASSIFIERS={int(capacities['classifiers'])}",
        "-DSKYSEED_BPF_MAX_GATEWAY_SELECTORS="
        f"{int(capacities['gateway_selectors'])}",
        "-DSKYSEED_BPF_MAX_UT_GATEWAY_POLICIES="
        f"{int(capacities['ut_gateway_policies'])}",
        f"-DSKYSEED_BPF_MAX_MPLS_LFIB={int(capacities['lfib'])}",
        f"-DSKYSEED_BPF_MAX_MPLS_HANDOFFS={int(capacities['handoffs'])}",
        "-DSKYSEED_BPF_MAX_ZONE_EDGE_DELIVERIES="
        f"{int(capacities['zone_edge_deliveries'])}",
        "-DSKYSEED_BPF_MAX_MPLS_PACKET_COUNTERS="
        f"{int(capacities['packet_counters'])}",
        "-DSKYSEED_BPF_MAX_INTERFACE_COUNTER_ENTRIES="
        f"{int(capacities['interface_counter_entries'])}",
        "-DSKYSEED_BPF_MAX_GENERATION_BASES="
        f"{int(capacities['generation_bases'])}",
    ]
    source_bytes = source.read_bytes()
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    cache_payload = {
        "schema_version": "skyseed-mpls-bpf-object-cache-v1",
        "source": str(source),
        "source_sha256": source_sha256,
        "capacities": capacities,
        "define_args": define_args,
        "include_args": include_args,
        "clang_target": "bpf",
    }
    cache_key = hashlib.sha256(
        json.dumps(cache_payload, sort_keys=True).encode("utf-8")
    ).hexdigest()
    effective_cache_dir = (
        cache_dir
        if cache_dir is not None
        else PROJECT_CACHE_ROOT / "bpf_mpls_objects"
    )
    cached_object_path = effective_cache_dir / f"{cache_key}.o"
    cached_metadata_path = effective_cache_dir / f"{cache_key}.json"
    object_path.parent.mkdir(parents=True, exist_ok=True)
    if cached_object_path.exists() and cached_metadata_path.exists():
        try:
            cached_metadata = json.loads(
                cached_metadata_path.read_text(encoding="utf-8")
            )
        except json.JSONDecodeError:
            cached_metadata = {}
        if cached_metadata == cache_payload:
            shutil.copy2(cached_object_path, object_path)
            return {
                "args": ["clang", "<cached>", str(source), "-o", str(object_path)],
                "returncode": 0,
                "stdout": "",
                "stderr": "",
                "object_path": str(object_path),
                "source": str(source),
                "capacities": capacities,
                "cache": {
                    "enabled": True,
                    "cache_hit": True,
                    "cache_key": cache_key,
                    "cache_object_path": str(cached_object_path),
                    "cache_metadata_path": str(cached_metadata_path),
                    "source_sha256": source_sha256,
                },
            }
    result = run_command(
        [
            "clang",
            "-O2",
            "-target",
            "bpf",
            "-D__TARGET_ARCH_x86",
            *define_args,
            *include_args,
            "-c",
            str(source),
            "-o",
            str(object_path),
        ],
        timeout=20.0,
    )
    result["object_path"] = str(object_path)
    result["source"] = str(source)
    result["capacities"] = capacities
    result["cache"] = {
        "enabled": True,
        "cache_hit": False,
        "cache_key": cache_key,
        "cache_object_path": str(cached_object_path),
        "cache_metadata_path": str(cached_metadata_path),
        "source_sha256": source_sha256,
    }
    if command_ok(result):
        effective_cache_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(object_path, cached_object_path)
        cached_metadata_path.write_text(
            json.dumps(cache_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return result


def interface_ifindex(interface_ref: str) -> int:
    parsed = parse_interface_ref(interface_ref)
    if parsed["kind"] == "ipnetns":
        result = run_command(
            [
                "ip",
                "-n",
                parsed["namespace"],
                "-o",
                "link",
                "show",
                "dev",
                parsed["interface_name"],
            ],
            timeout=4.0,
            privileged=True,
        )
        if not command_ok(result):
            raise RuntimeError(
                f"missing interface {interface_ref}: {result.get('stderr')}"
            )
        return int(str(result["stdout"]).split(":", 1)[0].strip())
    return docker_or_host_ifindex(interface_ref)


def interface_mac(interface_ref: str) -> str:
    parsed = parse_interface_ref(interface_ref)
    if parsed["kind"] == "ipnetns":
        result = run_command(
            [
                "ip",
                "-n",
                parsed["namespace"],
                "-j",
                "link",
                "show",
                "dev",
                parsed["interface_name"],
            ],
            timeout=4.0,
            privileged=True,
        )
        if not command_ok(result):
            raise RuntimeError(
                f"could not read interface MAC for {interface_ref}: {result.get('stderr')}"
            )
        parsed_json = json.loads(result.get("stdout") or "[]")
        return str(parsed_json[0]["address"])
    result = run_mpls_interface_command(
        interface_ref,
        ["ip", "-j", "link", "show", "dev", "{iface}"],
        timeout=4.0,
    )
    if not command_ok(result):
        raise RuntimeError(
            f"could not read interface MAC for {interface_ref}: {result.get('stderr')}"
        )
    parsed_json = json.loads(result.get("stdout") or "[]")
    return str(parsed_json[0]["address"])


def parse_interface_ref(interface_ref: str) -> dict[str, str]:
    raw = str(interface_ref)
    if raw.startswith("ipnetns:"):
        _prefix, namespace, interface_name = raw.split(":", 2)
        return {
            "kind": "ipnetns",
            "namespace": namespace,
            "interface_name": interface_name,
            "interface_ref": raw,
        }
    return {
        "kind": "docker_or_host",
        "namespace": "",
        "interface_name": raw.rsplit(":", 1)[-1] if raw.startswith("netns:") else raw,
        "interface_ref": raw,
    }


def run_mpls_interface_command(
    interface_ref: str,
    args: Sequence[str],
    *,
    timeout: float,
) -> dict[str, Any]:
    parsed = parse_interface_ref(interface_ref)
    command = [
        parsed["interface_name"] if str(part) == "{iface}" else str(part)
        for part in args
    ]
    if parsed["kind"] == "ipnetns":
        result = run_command(
            ["ip", "netns", "exec", parsed["namespace"], *command],
            timeout=timeout,
            privileged=True,
        )
        result["interface_ref"] = parsed
        return result
    return run_interface_command(interface_ref, command, timeout=timeout)


def _tc_result_text(result: dict[str, Any]) -> str:
    return f"{result.get('stdout') or ''}\n{result.get('stderr') or ''}"


def _tc_missing_clsact(result: dict[str, Any]) -> bool:
    text = _tc_result_text(result)
    return (
        int(result.get("returncode") or 0) != 0
        and "Cannot find specified qdisc" in text
    )


def _tc_clsact_present(result: dict[str, Any]) -> bool:
    return command_ok(result) and "clsact" in _tc_result_text(result)


def _tc_clsact_already_present(result: dict[str, Any]) -> bool:
    text = _tc_result_text(result)
    return "File exists" in text or "Exclusivity flag on, cannot modify" in text


def _normalized_clsact_result(
    result: dict[str, Any],
    *,
    event: str,
) -> dict[str, Any]:
    normalized = dict(result)
    normalized["original_returncode"] = result.get("returncode")
    normalized["returncode"] = 0
    normalized["clsact_idempotent_ok"] = True
    normalized["clsact_lifecycle_event"] = event
    return normalized


def _synthetic_clsact_result(
    interface_ref: str,
    *,
    event: str,
    returncode: int = 0,
    stderr: str = "",
) -> dict[str, Any]:
    return {
        "args": ["tc", "qdisc", "del", "dev", "{iface}", "clsact"],
        "interface_ref": parse_interface_ref(interface_ref),
        "returncode": int(returncode),
        "stdout": "",
        "stderr": stderr,
        "clsact_lifecycle_event": event,
    }


def interface_ipv4_addresses(interface_ref: str) -> list[str]:
    result = run_mpls_interface_command(
        interface_ref,
        ["ip", "-o", "-4", "addr", "show", "dev", "{iface}"],
        timeout=4.0,
    )
    addresses: list[str] = []
    if not command_ok(result):
        return addresses
    for line in str(result.get("stdout") or "").splitlines():
        parts = line.split()
        for index, part in enumerate(parts):
            if part != "inet" or index + 1 >= len(parts):
                continue
            address = parts[index + 1].split("/", 1)[0]
            try:
                ipaddress.IPv4Address(address)
            except ipaddress.AddressValueError:
                continue
            addresses.append(address)
    return sorted(set(addresses))


def attach_mpls(
    interface_ref: str,
    object_path: Path,
    *,
    directions: Sequence[str] = ("ingress",),
    control_plane_ipv4_addrs: Sequence[str] = (),
    clsact_setup_mode: str = "reset",
) -> list[dict[str, Any]]:
    if clsact_setup_mode not in {
        "reset",
        "ensure_new_delta",
        "expect_existing",
        "direct_new_delta",
        "direct_existing_delta",
    }:
        raise ValueError(f"unsupported clsact setup mode {clsact_setup_mode!r}")
    results: list[dict[str, Any]] = []
    if clsact_setup_mode == "reset":
        delete_result = run_mpls_interface_command(
            interface_ref,
            ["tc", "qdisc", "del", "dev", "{iface}", "clsact"],
            timeout=4.0,
        )
        delete_result["clsact_lifecycle_event"] = "delete_attempted"
        if _tc_missing_clsact(delete_result):
            delete_result = _normalized_clsact_result(
                delete_result,
                event="delete_missing_ok_for_reset",
            )
        results.append(delete_result)
        add_result = run_mpls_interface_command(
            interface_ref,
            ["tc", "qdisc", "add", "dev", "{iface}", "clsact"],
            timeout=8.0,
        )
        add_result["clsact_lifecycle_event"] = "add"
        results.append(add_result)
    elif clsact_setup_mode == "ensure_new_delta":
        show_result = run_mpls_interface_command(
            interface_ref,
            ["tc", "qdisc", "show", "dev", "{iface}"],
            timeout=4.0,
        )
        show_result["clsact_lifecycle_event"] = "show_before_new_delta"
        results.append(show_result)
        if not _tc_clsact_present(show_result):
            results.append(
                _synthetic_clsact_result(
                    interface_ref,
                    event="delete_skipped_missing_on_new_delta",
                )
            )
        add_result = run_mpls_interface_command(
            interface_ref,
            ["tc", "qdisc", "add", "dev", "{iface}", "clsact"],
            timeout=8.0,
        )
        add_result["clsact_lifecycle_event"] = "add"
        if int(add_result.get("returncode") or 0) != 0 and _tc_clsact_already_present(
            add_result
        ):
            add_result = _normalized_clsact_result(
                add_result,
                event="add_existing_ok_for_new_delta",
            )
        results.append(add_result)
    elif clsact_setup_mode == "expect_existing":
        show_result = run_mpls_interface_command(
            interface_ref,
            ["tc", "qdisc", "show", "dev", "{iface}"],
            timeout=4.0,
        )
        show_result["clsact_lifecycle_event"] = "show_before_expected_reuse"
        results.append(show_result)
        if not _tc_clsact_present(show_result):
            results.append(
                _synthetic_clsact_result(
                    interface_ref,
                    event="missing_on_expected_reuse",
                    returncode=2,
                    stderr="expected reused interface is missing clsact",
                )
            )
            return results
    elif clsact_setup_mode == "direct_new_delta":
        add_result = run_mpls_interface_command(
            interface_ref,
            ["tc", "qdisc", "add", "dev", "{iface}", "clsact"],
            timeout=8.0,
        )
        add_result["clsact_lifecycle_event"] = "direct_add_new_delta"
        results.append(add_result)
        if not command_ok(add_result):
            return results
    else:
        results.append(
            _synthetic_clsact_result(
                interface_ref,
                event="direct_existing_delta_no_probe",
            )
        )
    for direction in directions:
        if direction not in {"ingress", "egress"}:
            raise ValueError(f"unsupported tc-BPF attach direction {direction!r}")
        if direction in {"ingress", "egress"}:
            for dst_ip in sorted(
                {str(ip) for ip in control_plane_ipv4_addrs if str(ip)}
            ):
                ip_match_field = "dst_ip" if direction == "ingress" else "src_ip"
                flower_result = run_mpls_interface_command(
                    interface_ref,
                    [
                        "tc",
                        "filter",
                        "add",
                        "dev",
                        "{iface}",
                        direction,
                        "prio",
                        "1",
                        "protocol",
                        "ip",
                        "flower",
                        ip_match_field,
                        dst_ip,
                        "action",
                        "goto",
                        "chain",
                        "100",
                    ],
                    timeout=10.0,
                )
                if (
                    int(flower_result.get("returncode") or 0) != 0
                    and "File exists" in _tc_result_text(flower_result)
                ):
                    flower_result = _normalized_clsact_result(
                        flower_result,
                        event="control_plane_bypass_existing_ok",
                    )
                results.append(flower_result)
        results.append(
            run_mpls_interface_command(
                interface_ref,
                [
                    "tc",
                    "filter",
                    "add",
                    "dev",
                    "{iface}",
                    direction,
                    "prio",
                    "10",
                    "bpf",
                    "da",
                    "obj",
                    str(object_path),
                    "sec",
                    "classifier/mpls",
                ],
                timeout=10.0,
            )
        )
    return results


def verify_mpls_clsact(interface_ref: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    show_result = run_mpls_interface_command(
        interface_ref,
        ["tc", "qdisc", "show", "dev", "{iface}"],
        timeout=4.0,
    )
    show_result["clsact_lifecycle_event"] = "show_before_expected_reuse"
    results.append(show_result)
    if not _tc_clsact_present(show_result):
        results.append(
            _synthetic_clsact_result(
                interface_ref,
                event="missing_on_expected_reuse",
                returncode=2,
                stderr="expected reused interface is missing clsact",
            )
        )
    return results


def read_mpls_tc_filter_stats(
    interface_ref: str,
    *,
    directions: Sequence[str] = ("ingress", "egress"),
) -> dict[str, Any]:
    stats: dict[str, Any] = {}
    for direction in directions:
        if direction not in {"ingress", "egress"}:
            raise ValueError(f"unsupported tc filter direction {direction!r}")
        stats[str(direction)] = run_mpls_interface_command(
            interface_ref,
            ["tc", "-s", "filter", "show", "dev", "{iface}", direction],
            timeout=4.0,
        )
    return {
        "interface_ref": str(interface_ref),
        "directions": list(stats.keys()),
        "stats": stats,
    }


def rollback_mpls(
    *,
    attached_interfaces: Sequence[str],
    remove_maps: bool,
) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    for interface_ref in sorted({str(item) for item in attached_interfaces if str(item)}):
        steps.append(
            run_mpls_interface_command(
                interface_ref,
                ["tc", "qdisc", "del", "dev", "{iface}", "clsact"],
                timeout=4.0,
            )
        )
    if remove_maps:
        for map_name in MPLS_PINNED_MAPS:
            steps.append(
                run_command(
                    ["rm", "-f", str(PIN_BASE / map_name)],
                    timeout=4.0,
                    privileged=True,
                )
            )
    residue = mpls_residue_check(attached_interfaces)
    return {
        "steps": steps,
        "residue": residue,
        "rollback_completed": bool(residue["clean"]),
    }


def mpls_residue_check(attached_interfaces: Sequence[str]) -> dict[str, Any]:
    qdisc_hits: list[str] = []
    for interface_ref in sorted({str(item) for item in attached_interfaces if str(item)}):
        qdisc = run_mpls_interface_command(
            interface_ref,
            ["tc", "qdisc", "show", "dev", "{iface}"],
            timeout=4.0,
        )
        if "clsact" in (str(qdisc.get("stdout") or "") + str(qdisc.get("stderr") or "")):
            qdisc_hits.append(interface_ref)
    pinned = {name: privileged_path_exists(PIN_BASE / name) for name in MPLS_PINNED_MAPS}
    return {
        "clsact_interface_hits": qdisc_hits,
        "pinned_maps": pinned,
        "clean": not qdisc_hits and not any(pinned.values()),
    }


def mpls_map_reference(map_name: str) -> tuple[list[str], dict[str, Any]]:
    refs = mpls_map_references(map_name)
    if refs:
        return refs[-1]
    pinned = PIN_BASE / map_name
    return ["pinned", str(pinned)], {
        "kind": "missing",
        "logical_name": map_name,
        "kernel_name": MPLS_KERNEL_MAP_NAMES.get(map_name, map_name[:15]),
    }


def mpls_map_references(map_name: str) -> list[tuple[list[str], dict[str, Any]]]:
    pinned = PIN_BASE / map_name
    if privileged_path_exists(pinned):
        return [(["pinned", str(pinned)], {"kind": "pinned", "path": str(pinned)})]
    maps = bpftool_json(["-j", "map", "show"])
    kernel_name = MPLS_KERNEL_MAP_NAMES.get(map_name, map_name[:15])
    matches = [
        item
        for item in maps
        if isinstance(item, dict) and item.get("name") == kernel_name
    ]
    if matches:
        return [
            (
                ["id", str(item["id"])],
                {
                    "kind": "id",
                    "logical_name": map_name,
                    "kernel_name": kernel_name,
                    "id": item["id"],
                },
            )
            for item in sorted(matches, key=lambda item: int(item.get("id", -1)))
        ]
    spec = MPLS_MAP_SHAPES.get(map_name)
    if spec:
        shape_matches = [
            item
            for item in maps
            if isinstance(item, dict)
            and all(item.get(key) == value for key, value in spec.items())
        ]
        if shape_matches:
            return [
                (
                    ["id", str(item["id"])],
                    {
                        "kind": "id_by_shape",
                        "logical_name": map_name,
                        "id": item["id"],
                        "shape": spec,
                    },
                )
                for item in sorted(
                    shape_matches,
                    key=lambda item: int(item.get("id", -1)),
                )
            ]
    return []


def update_mpls_map(
    map_name: str,
    key_bytes: list[str],
    value_bytes: list[str],
) -> dict[str, Any]:
    refs = mpls_map_references(map_name)
    if not refs:
        ref_args, ref_meta = mpls_map_reference(map_name)
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
    sub_updates: list[dict[str, Any]] = []
    for ref_args, ref_meta in refs:
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
        sub_updates.append(result)
    return {
        "args": ["bpftool", "map", "update", map_name, "<fanout>"],
        "returncode": 0 if all(command_ok(item) for item in sub_updates) else 1,
        "stdout": "",
        "stderr": "\n".join(
            str(item.get("stderr") or "") for item in sub_updates if item.get("stderr")
        ),
        "map_reference": {
            "kind": "fanout",
            "logical_name": map_name,
            "count": len(refs),
            "references": [meta for _args, meta in refs],
        },
        "sub_updates": sub_updates,
    }


def update_mpls_u32_map(map_name: str, key: int, value: int) -> dict[str, Any]:
    return update_mpls_map(map_name, pack_u32s(key), pack_u32s(value))


def load_static_ut_gateway_policies(
    entries: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Cold-start-only batch load for immutable UT LPM gateway selectors."""

    pending: list[dict[str, Any]] = []
    logical_keys: set[tuple[int, str]] = set()
    selector_ifindices: set[int] = set()
    for raw in entries:
        entry = dict(raw or {})
        key = dict(entry.get("key") or entry)
        value = dict(entry.get("value") or entry)
        ifindex = int(key.get("egress_ifindex") or 0)
        prefix = ipaddress.ip_network(str(key.get("prefix") or ""), strict=False)
        label = int(value.get("gateway_selector_label") or 0)
        if ifindex <= 0 or prefix.version != 4 or label < 16:
            raise ValueError(f"invalid static UT gateway policy entry: {entry}")
        logical_key = (ifindex, str(prefix))
        if logical_key in logical_keys:
            raise ValueError(f"duplicate static UT gateway policy entry: {logical_key}")
        logical_keys.add(logical_key)
        selector_ifindices.add(ifindex)
        pending.append(
            {
                "map_name": "ut_gateway_policy_map",
                "key_bytes": pack_u32s(
                    32 + int(prefix.prefixlen),
                    ifindex,
                    ipv4_little_endian_u32(str(prefix.network_address)),
                ),
                "value_bytes": pack_u32s(1, label),
                "operation": "cold_start_static_policy_update",
                "generation": None,
                "logical_key": {
                    "egress_ifindex": ifindex,
                    "prefix": str(prefix),
                },
            }
        )
    updates = _annotated_batch_map_updates(pending)
    ok = bool(entries) and all(command_ok(item) for item in updates)
    return {
        "ok": ok,
        "immutable_for_run": True,
        "runtime_write_count": 0,
        "policy_entry_count": len(logical_keys),
        "selector_interface_count": len(selector_ifindices),
        "batch_command_count": 1 if pending else 0,
        "updates": updates,
        "reason": "" if ok else "static_ut_gateway_policy_batch_load_failed",
    }


def _hex_bytes_to_int_le(hex_bytes: Sequence[str], offset: int) -> int | None:
    if offset + 4 > len(hex_bytes):
        return None
    try:
        raw = bytes(int(part, 16) for part in hex_bytes[offset : offset + 4])
    except ValueError:
        return None
    return int.from_bytes(raw, byteorder="little")


def _hex_bytes_to_mac(hex_bytes: Sequence[str], offset: int) -> str | None:
    if offset + 6 > len(hex_bytes):
        return None
    try:
        return ":".join(f"{int(part, 16):02x}" for part in hex_bytes[offset : offset + 6])
    except ValueError:
        return None


def _decode_map_update_fields(
    *,
    map_name: str,
    key_bytes: Sequence[str],
    value_bytes: Sequence[str],
) -> dict[str, Any]:
    warnings: list[str] = []

    def u32s(parts: Sequence[str], count: int) -> list[int | None]:
        return [_hex_bytes_to_int_le(parts, index * 4) for index in range(count)]

    decoded_key: dict[str, Any] = {}
    decoded_value: dict[str, Any] = {}
    if map_name in {
        "ingress_classifier_map",
        "gateway_selector_ingress_map",
        "core_lfib_map",
        "egress_handoff_map",
        "zone_edge_delivery_map",
    }:
        key_fields = u32s(key_bytes, 4)
        if len(key_bytes) != 16:
            warnings.append(f"unexpected_key_length:{len(key_bytes)}")
        if map_name == "ingress_classifier_map":
            decoded_key = {
                "generation": key_fields[0],
                "ingress_ifindex": key_fields[1],
                "dst_ipv4": key_fields[2],
                "service_class": key_fields[3],
            }
            value_fields = u32s(value_bytes, 6)
            if len(value_bytes) != 36:
                warnings.append(f"unexpected_value_length:{len(value_bytes)}")
            decoded_value = {
                "active": value_fields[0],
                "fec_id_u32": value_fields[1],
                "direction": value_fields[2],
                "initial_label": value_fields[3],
                "out_ifindex": value_fields[4],
                "inner_ethertype": value_fields[5],
                "dst_mac": _hex_bytes_to_mac(value_bytes, 24),
                "src_mac": _hex_bytes_to_mac(value_bytes, 30),
            }
        elif map_name in {"core_lfib_map", "gateway_selector_ingress_map"}:
            decoded_key = {
                "generation": key_fields[0],
                "ingress_ifindex": key_fields[1],
                "label": key_fields[2],
                "direction": key_fields[3],
            }
            value_fields = u32s(value_bytes, 4)
            if len(value_bytes) != 28:
                warnings.append(f"unexpected_value_length:{len(value_bytes)}")
            decoded_value = {
                "active": value_fields[0],
                "action": value_fields[1],
                "out_label": value_fields[2],
                "out_ifindex": value_fields[3],
                "dst_mac": _hex_bytes_to_mac(value_bytes, 16),
                "src_mac": _hex_bytes_to_mac(value_bytes, 22),
            }
        elif map_name == "egress_handoff_map":
            decoded_key = {
                "generation": key_fields[0],
                "ingress_ifindex": key_fields[1],
                "label": key_fields[2],
                "direction": key_fields[3],
            }
            value_fields = u32s(value_bytes, 5)
            if len(value_bytes) != 32:
                warnings.append(f"unexpected_value_length:{len(value_bytes)}")
            decoded_value = {
                "active": value_fields[0],
                "action": value_fields[1],
                "out_ifindex": value_fields[2],
                "inner_ethertype": value_fields[3],
                "new_dst_ip": value_fields[4],
                "dst_mac": _hex_bytes_to_mac(value_bytes, 20),
                "src_mac": _hex_bytes_to_mac(value_bytes, 26),
            }
        else:
            decoded_key = {
                "generation": key_fields[0],
                "ingress_ifindex": key_fields[1],
                "label": key_fields[2],
                "dst_ipv4": key_fields[3],
            }
            value_fields = u32s(value_bytes, 3)
            if len(value_bytes) != 24:
                warnings.append(f"unexpected_value_length:{len(value_bytes)}")
            decoded_value = {
                "active": value_fields[0],
                "out_ifindex": value_fields[1],
                "new_dst_ip": value_fields[2],
                "dst_mac": _hex_bytes_to_mac(value_bytes, 12),
                "src_mac": _hex_bytes_to_mac(value_bytes, 18),
            }
    elif map_name == "generation_base_map":
        decoded_key = {"generation": _hex_bytes_to_int_le(key_bytes, 0)}
        decoded_value = {"base_generation": _hex_bytes_to_int_le(value_bytes, 0)}
        if len(key_bytes) != 4:
            warnings.append(f"unexpected_key_length:{len(key_bytes)}")
        if len(value_bytes) != 4:
            warnings.append(f"unexpected_value_length:{len(value_bytes)}")
    elif map_name in {"active_generation", "interface_role_map"}:
        decoded_key = {"slot_or_ifindex": _hex_bytes_to_int_le(key_bytes, 0)}
        decoded_value = {"value": _hex_bytes_to_int_le(value_bytes, 0)}
        if len(key_bytes) != 4:
            warnings.append(f"unexpected_key_length:{len(key_bytes)}")
        if len(value_bytes) != 4:
            warnings.append(f"unexpected_value_length:{len(value_bytes)}")
    return {
        "key": decoded_key,
        "value": decoded_value,
        "warnings": warnings,
    }


def _map_update_metadata(
    *,
    map_name: str,
    operation: str,
    generation: int | None,
    key_bytes: list[str],
    value_bytes: list[str],
    logical_key: dict[str, Any] | None = None,
) -> dict[str, Any]:
    decoded = _decode_map_update_fields(
        map_name=map_name,
        key_bytes=key_bytes,
        value_bytes=value_bytes,
    )
    return {
        "map_name": map_name,
        "operation": operation,
        "generation": generation,
        "key_hex": list(key_bytes),
        "key_hex_length": len(key_bytes),
        "key_byte_length": len(key_bytes),
        "value_hex_sample": list(value_bytes[:16]),
        "value_hex": list(value_bytes),
        "value_hex_length": len(value_bytes),
        "value_byte_length": len(value_bytes),
        "logical_key": dict(logical_key or {}),
        "decoded_key": dict(decoded.get("key") or {}),
        "decoded_value": dict(decoded.get("value") or {}),
        "decode_warnings": list(decoded.get("warnings") or []),
    }


def _annotated_map_update(
    map_name: str,
    key_bytes: list[str],
    value_bytes: list[str],
    *,
    operation: str,
    generation: int | None,
    logical_key: dict[str, Any] | None = None,
) -> dict[str, Any]:
    started_at = time.perf_counter()
    result = update_mpls_map(map_name, key_bytes, value_bytes)
    result["map_update_wall_seconds"] = round(time.perf_counter() - started_at, 6)
    result["map_update"] = _map_update_metadata(
        map_name=map_name,
        operation=operation,
        generation=generation,
        key_bytes=key_bytes,
        value_bytes=value_bytes,
        logical_key=logical_key,
    )
    return result


def _bpftool_batch_update_result(
    *,
    pending_updates: Sequence[Mapping[str, Any]],
    command_result: Mapping[str, Any],
    wall_seconds: float,
) -> list[dict[str, Any]]:
    update_count = max(1, len(list(pending_updates)))
    per_update_wall_seconds = round(float(wall_seconds) / float(update_count), 6)
    returncode = 0 if command_ok(dict(command_result)) else 1
    results: list[dict[str, Any]] = []
    for index, pending in enumerate(pending_updates):
        map_name = str(pending["map_name"])
        key_bytes = list(pending.get("key_bytes") or [])
        value_bytes = list(pending.get("value_bytes") or [])
        results.append(
            {
                "args": [
                    "bpftool",
                    "batch",
                    "file",
                    "<skyseed-bpf-map-update-batch>",
                ],
                "returncode": returncode,
                "stdout": str(command_result.get("stdout") or ""),
                "stderr": str(command_result.get("stderr") or ""),
                "batched": True,
                "batch_index": index,
                "batch_update_count": len(list(pending_updates)),
                "batch_command": dict(command_result),
                "map_update_wall_seconds": per_update_wall_seconds,
                "map_update": _map_update_metadata(
                    map_name=map_name,
                    operation=str(pending.get("operation") or "update"),
                    generation=(
                        int(pending.get("generation"))
                        if pending.get("generation") not in (None, "", "None")
                        else None
                    ),
                    key_bytes=key_bytes,
                    value_bytes=value_bytes,
                    logical_key=dict(pending.get("logical_key") or {}),
                ),
            }
        )
    return results


def _bpftool_batch_update_line(
    *,
    map_name: str,
    key_bytes: Sequence[str],
    value_bytes: Sequence[str],
) -> str:
    pinned_path = PIN_BASE / str(map_name)
    key_hex = " ".join(str(part).lower().replace("0x", "").zfill(2) for part in key_bytes)
    value_hex = " ".join(
        str(part).lower().replace("0x", "").zfill(2) for part in value_bytes
    )
    return f"map update pinned {pinned_path} key hex {key_hex} value hex {value_hex}"


def _bpftool_batch_delete_line(
    *,
    map_name: str,
    key_bytes: Sequence[str],
) -> str:
    pinned_path = PIN_BASE / str(map_name)
    key_hex = " ".join(
        str(part).lower().replace("0x", "").zfill(2) for part in key_bytes
    )
    return f"map delete pinned {pinned_path} key hex {key_hex}"


def _annotated_batch_map_updates(
    pending_updates: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    pending = [dict(item) for item in pending_updates]
    if not pending:
        return []
    batch_file: Path | None = None
    started_at = time.perf_counter()
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            prefix="skyseed-bpf-map-update-",
            suffix=".bpftool",
            delete=False,
        ) as handle:
            batch_file = Path(handle.name)
            for item in pending:
                operation = str(item.get("operation") or "update")
                if operation.startswith("delete_"):
                    handle.write(
                        _bpftool_batch_delete_line(
                            map_name=str(item["map_name"]),
                            key_bytes=list(item.get("key_bytes") or []),
                        )
                    )
                else:
                    handle.write(
                        _bpftool_batch_update_line(
                            map_name=str(item["map_name"]),
                            key_bytes=list(item.get("key_bytes") or []),
                            value_bytes=list(item.get("value_bytes") or []),
                        )
                    )
                handle.write("\n")
        command_result = bpftool(["batch", "file", str(batch_file)], timeout=120.0)
    finally:
        if batch_file is not None:
            try:
                batch_file.unlink()
            except OSError:
                pass
    wall_seconds = round(time.perf_counter() - started_at, 6)
    return _bpftool_batch_update_result(
        pending_updates=pending,
        command_result=command_result,
        wall_seconds=wall_seconds,
    )


def _annotated_map_delete(
    map_name: str,
    key_bytes: list[str],
    *,
    generation: int | None,
    operation: str = "delete_stale_target_generation",
    logical_key: dict[str, Any] | None = None,
) -> dict[str, Any]:
    started_at = time.perf_counter()
    result = delete_mpls_map_key(map_name, key_bytes)
    result["map_update_wall_seconds"] = round(time.perf_counter() - started_at, 6)
    result["map_update"] = _map_update_metadata(
        map_name=map_name,
        operation=operation,
        generation=generation,
        key_bytes=key_bytes,
        value_bytes=[],
        logical_key=logical_key,
    )
    return result


def generation_delete_manifest_for_compiled_mpls_maps(
    compiled: Mapping[str, Any],
    *,
    generation_id: int,
) -> list[dict[str, Any]]:
    """Build preplanned BPF map delete work from compiled runtime inputs."""

    active_generation = int(generation_id)
    manifest: list[dict[str, Any]] = []

    def append_delete(
        map_name: str,
        key_bytes: list[str],
        *,
        logical_key: Mapping[str, Any],
    ) -> None:
        manifest.append(
            {
                "map_name": map_name,
                "key_bytes": list(key_bytes),
                "operation": "delete_manifest_generation_key",
                "generation": active_generation,
                "logical_key": dict(logical_key or {}),
            }
        )

    for entry in list(dict(compiled or {}).get("ingress_classifier_map") or []):
        key = dict(dict(entry).get("key") or {})
        generation = int(key.get("generation", active_generation))
        if generation != active_generation:
            continue
        ingress_ifindex = int(key.get("ingress_ifindex") or 0)
        dst_ipv4 = int(
            key.get("dst_ipv4") or target_prefix_to_ipv4(key.get("target_prefix"))
        )
        service_class = int(
            key.get("service_class_u32") or service_class_u32(key.get("service_class"))
        )
        append_delete(
            "ingress_classifier_map",
            pack_u32s(generation, ingress_ifindex, dst_ipv4, service_class),
            logical_key={
                "generation": generation,
                "ingress_ifindex": ingress_ifindex,
                "dst_ipv4": dst_ipv4,
                "service_class": service_class,
            },
        )

    for map_name in (
        "gateway_selector_ingress_map",
        "core_lfib_map",
        "egress_handoff_map",
    ):
        for entry in list(dict(compiled or {}).get(map_name) or []):
            key = dict(dict(entry).get("key") or {})
            generation = int(key.get("generation", active_generation))
            if generation != active_generation:
                continue
            append_delete(
                map_name,
                pack_u32s(
                    generation,
                    int(key.get("ingress_ifindex") or 0),
                    int(key.get("label") or 0),
                    direction_u32(key.get("direction", G_TO_Z)),
                ),
                logical_key=dict(key),
            )

    for entry in list(dict(compiled or {}).get("zone_edge_delivery_map") or []):
        key = dict(dict(entry).get("key") or {})
        generation = int(key.get("generation", active_generation))
        if generation != active_generation:
            continue
        append_delete(
            "zone_edge_delivery_map",
            pack_u32s(
                generation,
                int(key.get("ingress_ifindex") or 0),
                int(key.get("label") or 0),
                int(key.get("dst_ipv4") or 0),
            ),
            logical_key=dict(key),
        )

    for entry in list(dict(compiled or {}).get("generation_base_map") or []):
        key = dict(dict(entry).get("key") or {})
        generation = int(key.get("generation", active_generation))
        if generation != active_generation:
            continue
        append_delete(
            "generation_base_map",
            pack_u32s(generation),
            logical_key={"generation": generation},
        )

    if not any(item["map_name"] == "generation_base_map" for item in manifest):
        append_delete(
            "generation_base_map",
            pack_u32s(active_generation),
            logical_key={"generation": active_generation},
        )
    return manifest


def _skipped_map_update(
    map_name: str,
    key_bytes: list[str],
    value_bytes: list[str],
    *,
    operation: str,
    generation: int | None,
    reason: str,
    logical_key: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "args": ["bpftool", "map", "update", map_name, "<skipped>"],
        "returncode": 1,
        "stdout": "",
        "stderr": reason,
        "skipped": True,
        "map_update": _map_update_metadata(
            map_name=map_name,
            operation=operation,
            generation=generation,
            key_bytes=key_bytes,
            value_bytes=value_bytes,
            logical_key=logical_key,
        ),
    }


def _noop_map_update(
    map_name: str,
    key_bytes: list[str],
    value_bytes: list[str],
    *,
    operation: str,
    generation: int | None,
    reason: str,
    logical_key: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "args": ["bpftool", "map", "update", map_name, "<noop-skipped>"],
        "returncode": 0,
        "stdout": "",
        "stderr": "",
        "skipped": True,
        "noop_skipped": True,
        "noop_skip_reason": reason,
        "map_update_wall_seconds": 0.0,
        "map_update": _map_update_metadata(
            map_name=map_name,
            operation=operation,
            generation=generation,
            key_bytes=key_bytes,
            value_bytes=value_bytes,
            logical_key=logical_key,
        ),
    }


def _normalized_generationless_logical_key(
    map_name: str,
    logical_key: Mapping[str, Any],
) -> dict[str, Any]:
    normalized = {
        str(key): value
        for key, value in dict(logical_key or {}).items()
        if str(key) != "generation"
    }
    if map_name == "ingress_classifier_map":
        return {
            key: normalized.get(key)
            for key in ("ingress_ifindex", "dst_ipv4", "service_class")
            if key in normalized
        }
    if map_name in {
        "gateway_selector_ingress_map",
        "core_lfib_map",
        "egress_handoff_map",
    }:
        return {
            key: normalized.get(key)
            for key in ("ingress_ifindex", "label", "direction")
            if key in normalized
        }
    if map_name == "zone_edge_delivery_map":
        return {
            key: normalized.get(key)
            for key in ("ingress_ifindex", "label", "dst_ipv4")
            if key in normalized
        }
    return normalized


def _generationless_key_fingerprint(
    *,
    map_name: str,
    logical_key: Mapping[str, Any],
) -> str:
    payload = {
        "map_name": str(map_name),
        "logical_key_without_generation": _normalized_generationless_logical_key(
            str(map_name),
            logical_key,
        ),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]


def _value_fingerprint(value_bytes: Sequence[str]) -> str:
    return hashlib.sha256(
        " ".join(
            str(part).lower().replace("0x", "").zfill(2)
            for part in list(value_bytes or [])
        ).encode("utf-8")
    ).hexdigest()[:16]


def _map_update_generation_write_cost_audit(
    *,
    pending_map_updates: Sequence[Mapping[str, Any]],
    filtered_pending_map_updates: Sequence[Mapping[str, Any]],
    noop_map_updates: Sequence[Mapping[str, Any]],
    map_diff_trace: Mapping[str, Mapping[str, Any]],
    updates: Sequence[Mapping[str, Any]],
    loaded_interface_role_count: int,
    skip_interface_role_map_updates: bool,
    active_generation_update: Mapping[str, Any],
    active_generation_update_count: int,
    defer_active_generation_flip: bool,
    runtime_update_phase: str,
    active_generation: int,
    live_active_generation_before: int | None,
    map_write_wall_seconds: float,
    active_generation_flip_wall_seconds: float,
    manifest_only_loader: bool = False,
    batch_map_write_required: bool = False,
    batch_map_write_used: bool = False,
) -> dict[str, Any]:
    started_at = time.perf_counter()
    pending = [dict(item) for item in pending_map_updates]
    scheduled = [dict(item) for item in filtered_pending_map_updates]
    noop = [dict(item) for item in noop_map_updates]
    scheduled_by_map: dict[str, int] = {}
    desired_by_map: dict[str, int] = {}
    noop_by_map: dict[str, int] = {}
    generation_keyed_desired_by_map: dict[str, int] = {}
    generation_keyed_scheduled_by_map: dict[str, int] = {}
    invariant_groups: dict[tuple[str, str, str], set[int]] = {}
    for item in pending:
        map_name = str(item.get("map_name") or "")
        desired_by_map[map_name] = desired_by_map.get(map_name, 0) + 1
        if map_name not in GENERATION_KEYED_MPLS_MAPS:
            continue
        generation_keyed_desired_by_map[map_name] = (
            generation_keyed_desired_by_map.get(map_name, 0) + 1
        )
        group_key = (
            map_name,
            _generationless_key_fingerprint(
                map_name=map_name,
                logical_key=dict(item.get("logical_key") or {}),
            ),
            _value_fingerprint(list(item.get("value_bytes") or [])),
        )
        generations = invariant_groups.setdefault(group_key, set())
        if item.get("generation") not in (None, "", "None"):
            generations.add(int(item.get("generation") or 0))
    for item in scheduled:
        map_name = str(item.get("map_name") or "")
        scheduled_by_map[map_name] = scheduled_by_map.get(map_name, 0) + 1
        if map_name in GENERATION_KEYED_MPLS_MAPS:
            generation_keyed_scheduled_by_map[map_name] = (
                generation_keyed_scheduled_by_map.get(map_name, 0) + 1
            )
    for item in noop:
        update = dict(item.get("map_update") or {})
        map_name = str(update.get("map_name") or "")
        noop_by_map[map_name] = noop_by_map.get(map_name, 0) + 1
    delete_update_count = sum(
        1
        for item in updates
        if str(dict(item.get("map_update") or {}).get("operation") or "").startswith(
            "delete_"
        )
    )
    batched_updates = [dict(item) for item in updates if item.get("batched")]
    batch_command_count = 1 if batched_updates else 0
    role_map_skipped_count = (
        int(loaded_interface_role_count) if skip_interface_role_map_updates else 0
    )
    active_generation_noop_or_deferred = bool(
        active_generation_update.get("noop_skipped") or defer_active_generation_flip
    )
    current_schema_skipped_now_count = (
        len(noop)
        + role_map_skipped_count
        + (1 if active_generation_noop_or_deferred else 0)
    )
    generation_keyed_desired_count = sum(generation_keyed_desired_by_map.values())
    generation_keyed_scheduled_count = sum(generation_keyed_scheduled_by_map.values())
    invariant_group_count = len(invariant_groups)
    repeated_invariant_group_count = sum(
        1 for generations in invariant_groups.values() if len(generations) > 1
    )
    repeated_invariant_occurrence_count = sum(
        max(0, len(generations) - 1) for generations in invariant_groups.values()
    )
    per_map: dict[str, dict[str, Any]] = {}
    all_maps = sorted(
        set(desired_by_map)
        | set(scheduled_by_map)
        | set(noop_by_map)
        | set(str(key) for key in map_diff_trace.keys())
    )
    for map_name in all_maps:
        diff = dict(map_diff_trace.get(map_name) or {})
        desired_count = int(desired_by_map.get(map_name, 0))
        scheduled_count = int(scheduled_by_map.get(map_name, 0))
        noop_count = int(noop_by_map.get(map_name, 0))
        schema_candidate = (
            scheduled_count if map_name in GENERATION_KEYED_MPLS_MAPS else 0
        )
        per_map[map_name] = {
            "desired_update_count": desired_count,
            "scheduled_write_count": scheduled_count,
            "noop_skipped_count": noop_count,
            "insert_count": int(diff.get("insert_count") or 0),
            "update_count": int(diff.get("update_count") or 0),
            "delete_count": int(diff.get("delete_count") or 0),
            "existing_count": diff.get("existing_count"),
            "generation_keyed": map_name in GENERATION_KEYED_MPLS_MAPS,
            "current_schema_mandatory_write_count": schema_candidate,
            "schema_optimization_candidate_count": schema_candidate,
        }
    return {
        "schema_version": "skyseed-bpf-generation-write-cost-audit-v1",
        "audit_scope": "single_load_mpls_maps_call",
        "runtime_update_phase": str(runtime_update_phase),
        "active_generation": int(active_generation),
        "live_active_generation_before": live_active_generation_before,
        "manifest_only_loader": bool(manifest_only_loader),
        "batch_map_write_required": bool(batch_map_write_required),
        "batch_map_write_used": bool(batch_map_write_used),
        "batch_command_count": batch_command_count,
        "batched_sub_update_count": len(batched_updates),
        "desired_data_update_count": len(pending),
        "scheduled_data_write_count": len(scheduled),
        "actual_data_noop_skipped_count": len(noop),
        "delete_update_count": delete_update_count,
        "interface_role_map_skipped_update_count": role_map_skipped_count,
        "active_generation_update_count": int(active_generation_update_count),
        "active_generation_noop_or_deferred": active_generation_noop_or_deferred,
        "current_schema_skipped_now_count": current_schema_skipped_now_count,
        "current_schema_bpftool_data_write_count": len(scheduled),
        "current_schema_generation_keyed_desired_count": generation_keyed_desired_count,
        "current_schema_generation_keyed_scheduled_write_count": (
            generation_keyed_scheduled_count
        ),
        "schema_optimization_candidate_count": generation_keyed_scheduled_count,
        "schema_optimization_candidate_reason": (
            "generation-keyed dataplane entries must be written per generation "
            "under the current BPF lookup schema; skipping them requires a shared "
            "content map/default-generation indirection or equivalent schema change"
        ),
        "generation_invariant_group_count": invariant_group_count,
        "generation_invariant_repeated_group_count": repeated_invariant_group_count,
        "generation_invariant_repeated_occurrence_count": (
            repeated_invariant_occurrence_count
        ),
        "per_map": per_map,
        "map_write_wall_seconds": float(map_write_wall_seconds),
        "active_generation_flip_wall_seconds": float(active_generation_flip_wall_seconds),
        "audit_wall_seconds": round(time.perf_counter() - started_at, 6),
        "audit_included_in_map_write_wall_time": False,
        "contains_full_entry_payloads": False,
    }


def _normalize_bpftool_hex_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    for part in value:
        text = str(part).lower().replace("0x", "")
        if text:
            normalized.append(text.zfill(2))
    return normalized


def _hex_key_id(value: Sequence[str]) -> str:
    return " ".join(str(part).lower().replace("0x", "").zfill(2) for part in value)


def mpls_map_schema_state(map_name: str) -> dict[str, Any]:
    kernel_name = MPLS_KERNEL_MAP_NAMES.get(map_name, map_name[:15])
    live_map_infos = [
        {
            "id": item.get("id"),
            "name": item.get("name"),
            "type": item.get("type"),
            "bytes_key": item.get("bytes_key"),
            "bytes_value": item.get("bytes_value"),
            "max_entries": item.get("max_entries"),
            "pinned": item.get("pinned"),
            "btf_id": item.get("btf_id"),
        }
        for item in bpftool_json(["-j", "map", "show"])
        if isinstance(item, dict) and item.get("name") == kernel_name
    ]
    shape = dict(MPLS_MAP_SHAPES.get(map_name) or {})
    live_first = dict(live_map_infos[-1]) if live_map_infos else {}
    expected_bytes_key = shape.get("bytes_key")
    expected_bytes_value = shape.get("bytes_value")
    live_bytes_key = live_first.get("bytes_key")
    live_bytes_value = live_first.get("bytes_value")
    expected_type = shape.get("type")
    expected_max_entries = shape.get("max_entries")
    live_type = live_first.get("type")
    live_max_entries = live_first.get("max_entries")
    type_key_value_matches = (
        not live_first
        or (
            live_type == expected_type
            and
            live_bytes_key == expected_bytes_key
            and live_bytes_value == expected_bytes_value
        )
    )
    schema_matches = type_key_value_matches
    return {
        "map_name": map_name,
        "kernel_name": kernel_name,
        "expected_shape": shape,
        "live_map_infos": live_map_infos,
        "live_map_ids": [
            item.get("id")
            for item in live_map_infos
            if item.get("id") is not None
        ],
        "selected_map_id": live_first.get("id"),
        "expected_type": expected_type,
        "live_type": live_type,
        "live_bytes_key": live_bytes_key,
        "live_bytes_value": live_bytes_value,
        "live_max_entries": live_max_entries,
        "expected_max_entries": expected_max_entries,
        "type_key_value_matches_expected": type_key_value_matches,
        "capacity_matches_static_expected_max": (
            live_max_entries == expected_max_entries if live_first else None
        ),
        "live_pinned_path": live_first.get("pinned"),
        "expected_bytes_key": expected_bytes_key,
        "expected_bytes_value": expected_bytes_value,
        "schema_matches_expected": schema_matches,
        "schema_mismatch_reason": (
            ""
            if schema_matches
            else (
                "bpf_mpls_map_schema_mismatch:"
                f"map={map_name};"
                f"expected_type={expected_type};"
                f"live_type={live_type};"
                f"expected_bytes_key={expected_bytes_key};"
                f"live_bytes_key={live_bytes_key};"
                f"expected_bytes_value={expected_bytes_value};"
                f"live_bytes_value={live_bytes_value};"
                f"expected_max_entries={expected_max_entries};"
                f"live_max_entries={live_max_entries}"
            )
        ),
    }


def _bpftool_hex_list_to_u32(value: Any, offset: int = 0) -> int | None:
    if not isinstance(value, list) or offset + 4 > len(value):
        return None
    try:
        raw = bytes(int(str(part), 16) for part in value[offset : offset + 4])
    except ValueError:
        return None
    return int.from_bytes(raw, byteorder="little")


def _generation_keyed_entry_generation(map_name: str, entry: Any) -> int | None:
    if map_name not in GENERATION_KEYED_MPLS_MAPS or not isinstance(entry, dict):
        return None
    return _bpftool_hex_list_to_u32(entry.get("key"), 0)


def mpls_map_state(
    map_name: str,
    *,
    expected_entries: int | None = None,
    active_generation: int | None = None,
    target_generation: int | None = None,
) -> dict[str, Any]:
    references = mpls_map_references(map_name)
    schema_state = mpls_map_schema_state(map_name)
    live_map_infos = list(schema_state.get("live_map_infos") or [])
    map_show_results: list[dict[str, Any]] = []
    if not live_map_infos and references:
        for ref_args, ref_meta in references:
            show_result = bpftool(["-j", "map", "show", *ref_args], timeout=8.0)
            parsed_show: Any = None
            if show_result.get("stdout"):
                try:
                    parsed_show = json.loads(str(show_result.get("stdout") or "{}"))
                except json.JSONDecodeError:
                    parsed_show = None
            show_items = (
                parsed_show
                if isinstance(parsed_show, list)
                else [parsed_show]
                if isinstance(parsed_show, dict)
                else []
            )
            for item in show_items:
                if not isinstance(item, dict):
                    continue
                live_map_infos.append(
                    {
                        "id": item.get("id"),
                        "name": item.get("name"),
                        "type": item.get("type"),
                        "bytes_key": item.get("bytes_key"),
                        "bytes_value": item.get("bytes_value"),
                        "max_entries": item.get("max_entries"),
                        "pinned": item.get("pinned")
                        or dict(ref_meta).get("path"),
                        "btf_id": item.get("btf_id"),
                    }
                )
            map_show_results.append(
                {
                    "map_reference": ref_meta,
                    "returncode": show_result.get("returncode"),
                    "stderr": show_result.get("stderr"),
                    "parsed": parsed_show,
                }
            )
    if live_map_infos:
        shape = dict(schema_state.get("expected_shape") or {})
        live_first = dict(live_map_infos[-1])
        live_type = live_first.get("type")
        live_bytes_key = live_first.get("bytes_key")
        live_bytes_value = live_first.get("bytes_value")
        live_max_entries = live_first.get("max_entries")
        expected_type = shape.get("type")
        expected_bytes_key = shape.get("bytes_key")
        expected_bytes_value = shape.get("bytes_value")
        expected_max_entries = shape.get("max_entries")
        type_key_value_matches = (
            live_type == expected_type
            and live_bytes_key == expected_bytes_key
            and live_bytes_value == expected_bytes_value
        )
        schema_state = {
            **dict(schema_state),
            "live_map_infos": live_map_infos,
            "live_map_ids": [
                item.get("id")
                for item in live_map_infos
                if item.get("id") is not None
            ],
            "selected_map_id": live_first.get("id"),
            "live_type": live_type,
            "live_bytes_key": live_bytes_key,
            "live_bytes_value": live_bytes_value,
            "live_max_entries": live_max_entries,
            "live_pinned_path": live_first.get("pinned"),
            "type_key_value_matches_expected": type_key_value_matches,
            "capacity_matches_static_expected_max": live_max_entries == expected_max_entries,
            "schema_matches_expected": type_key_value_matches,
            "schema_mismatch_reason": (
                ""
                if type_key_value_matches
                else (
                    "bpf_mpls_map_schema_mismatch:"
                    f"map={map_name};"
                    f"expected_type={expected_type};"
                    f"live_type={live_type};"
                    f"expected_bytes_key={expected_bytes_key};"
                    f"live_bytes_key={live_bytes_key};"
                    f"expected_bytes_value={expected_bytes_value};"
                    f"live_bytes_value={live_bytes_value};"
                    f"expected_max_entries={expected_max_entries};"
                    f"live_max_entries={live_max_entries}"
                )
            ),
        }
    dumps: list[dict[str, Any]] = []
    current_entries = 0
    generation_entry_counts: dict[int, int] = {}
    generation_key_hex_by_generation: dict[int, list[list[str]]] = {}
    generation_entries_by_generation: dict[int, list[dict[str, Any]]] = {}
    live_entries_by_key_hex: dict[str, list[str]] = {}
    target_generation_key_hex: list[list[str]] = []
    target_generation_value_hex_by_key: dict[str, list[str]] = {}
    dump_complete = True
    for ref_args, ref_meta in references:
        result = bpftool(["-j", "map", "dump", *ref_args], timeout=8.0)
        entry_count: int | None = None
        if command_ok(result) and result.get("stdout"):
            try:
                parsed = json.loads(str(result.get("stdout") or "[]"))
            except json.JSONDecodeError:
                parsed = []
            if isinstance(parsed, list):
                entry_count = len(parsed)
                current_entries += entry_count
                for entry in parsed:
                    key_hex = entry.get("key") if isinstance(entry, dict) else None
                    value_hex = entry.get("value") if isinstance(entry, dict) else None
                    normalized_key_hex = _normalize_bpftool_hex_list(key_hex)
                    normalized_value_hex = _normalize_bpftool_hex_list(value_hex)
                    if normalized_key_hex:
                        live_entries_by_key_hex[_hex_key_id(normalized_key_hex)] = (
                            normalized_value_hex
                        )
                    generation = _generation_keyed_entry_generation(map_name, entry)
                    if generation is None:
                        continue
                    generation_entry_counts[generation] = (
                        int(generation_entry_counts.get(generation, 0)) + 1
                    )
                    if normalized_key_hex:
                        generation_key_hex_by_generation.setdefault(generation, []).append(
                            normalized_key_hex
                        )
                        generation_entries_by_generation.setdefault(generation, []).append(
                            {
                                "key_hex": normalized_key_hex,
                                "value_hex": normalized_value_hex,
                            }
                        )
                    if target_generation is not None and generation == int(target_generation):
                        if normalized_key_hex:
                            target_generation_key_hex.append(normalized_key_hex)
                            target_generation_value_hex_by_key[
                                _hex_key_id(normalized_key_hex)
                            ] = normalized_value_hex
        elif references:
            dump_complete = False
        dumps.append(
            {
                "map_reference": ref_meta,
                "returncode": result.get("returncode"),
                "stderr": result.get("stderr"),
                "entry_count": entry_count,
            }
        )
    expected_max_entries = dict(schema_state.get("expected_shape") or {}).get(
        "max_entries"
    )
    live_max_entries = schema_state.get("live_max_entries")
    max_entries = live_max_entries or expected_max_entries
    stale_entries = (
        max(0, current_entries - int(expected_entries))
        if references and expected_entries is not None
        else None
    )
    return {
        "map_name": map_name,
        "pinned_path": str(PIN_BASE / map_name),
        "fd_state": "resolved" if references else "missing",
        "references": [meta for _args, meta in references],
        "reference_args": [args for args, _meta in references],
        "map_ids": list(schema_state.get("live_map_ids") or []),
        "selected_map_id": schema_state.get("selected_map_id"),
        "map_fd": None,
        "expected_shape": dict(schema_state.get("expected_shape") or {}),
        "live_map_infos": live_map_infos,
        "map_show_results": map_show_results,
        "expected_type": schema_state.get("expected_type"),
        "live_type": schema_state.get("live_type"),
        "live_bytes_key": schema_state.get("live_bytes_key"),
        "live_bytes_value": schema_state.get("live_bytes_value"),
        "live_max_entries": live_max_entries,
        "expected_bytes_key": schema_state.get("expected_bytes_key"),
        "expected_bytes_value": schema_state.get("expected_bytes_value"),
        "expected_max_entries": expected_max_entries,
        "live_pinned_path": schema_state.get("live_pinned_path"),
        "schema_matches_expected": schema_state.get("schema_matches_expected"),
        "type_key_value_matches_expected": schema_state.get(
            "type_key_value_matches_expected"
        ),
        "capacity_matches_static_expected_max": schema_state.get(
            "capacity_matches_static_expected_max"
        ),
        "schema_mismatch_reason": schema_state.get("schema_mismatch_reason"),
        "max_entries": max_entries,
        "current_entries": current_entries if references else None,
        "expected_entries": expected_entries,
        "generation_entry_counts": {
            str(generation): count
            for generation, count in sorted(generation_entry_counts.items())
        },
        "generation_key_hex_by_generation": {
            str(generation): keys
            for generation, keys in sorted(generation_key_hex_by_generation.items())
        },
        "generation_entries_by_generation": {
            str(generation): entries
            for generation, entries in sorted(generation_entries_by_generation.items())
        },
        "live_entries_by_key_hex": live_entries_by_key_hex,
        "live_entry_dump_complete": dump_complete,
        "active_generation": active_generation,
        "active_generation_entry_count": (
            generation_entry_counts.get(int(active_generation), 0)
            if active_generation is not None
            else None
        ),
        "target_generation": target_generation,
        "target_generation_entry_count": (
            generation_entry_counts.get(int(target_generation), 0)
            if target_generation is not None
            else None
        ),
        "target_generation_stale_key_hex": target_generation_key_hex,
        "target_generation_value_hex_by_key": target_generation_value_hex_by_key,
        "stale_entries": stale_entries,
        "stale_entry_reason": (
            "current_entries_minus_expected_entries"
            if stale_entries is not None
            else "entry_count_unavailable"
        ),
        "dumps": dumps,
    }


def delete_mpls_map_key(map_name: str, key_bytes: list[str]) -> dict[str, Any]:
    refs = mpls_map_references(map_name)
    if not refs:
        ref_args, ref_meta = mpls_map_reference(map_name)
        result = bpftool(
            ["map", "delete", *ref_args, "key", "hex", *key_bytes],
            timeout=8.0,
        )
        result["map_reference"] = ref_meta
        return result
    sub_deletes: list[dict[str, Any]] = []
    for ref_args, ref_meta in refs:
        result = bpftool(
            ["map", "delete", *ref_args, "key", "hex", *key_bytes],
            timeout=8.0,
        )
        result["map_reference"] = ref_meta
        sub_deletes.append(result)
    return {
        "args": ["bpftool", "map", "delete", map_name, "<fanout>"],
        "returncode": 0 if all(command_ok(item) for item in sub_deletes) else 1,
        "stdout": "",
        "stderr": "\n".join(
            str(item.get("stderr") or "") for item in sub_deletes if item.get("stderr")
        ),
        "map_reference": {
            "kind": "fanout",
            "logical_name": map_name,
            "count": len(refs),
            "references": [meta for _args, meta in refs],
        },
        "sub_deletes": sub_deletes,
    }


def _direct_error_evidence(update: dict[str, Any]) -> dict[str, Any]:
    failed_sub_update: dict[str, Any] | None = None
    for sub_update in list(update.get("sub_updates") or []):
        if isinstance(sub_update, dict) and not command_ok(sub_update):
            failed_sub_update = sub_update
            break
    evidence_source = failed_sub_update or update
    skipped = bool(update.get("skipped"))
    stderr = str(evidence_source.get("stderr") or "")
    if skipped and BPF_MAP_GENERATION_CAPACITY_BLOCKER in stderr:
        source = "loader_generation_capacity_preflight"
        source_scope = "generation_capacity_guard"
    else:
        source = "loader_schema_preflight" if skipped else "subprocess_result"
        source_scope = (
            "schema_guard"
            if skipped
            else "fanout_sub_update"
            if failed_sub_update
            else "map_update"
        )
    return {
        "source": source,
        "source_scope": source_scope,
        "returncode": evidence_source.get("returncode"),
        "errno": evidence_source.get("errno"),
        "stderr": evidence_source.get("stderr"),
        "stdout": evidence_source.get("stdout"),
        "exception": evidence_source.get("exception"),
        "exception_type": evidence_source.get("exception_type"),
        "timeout": bool(evidence_source.get("timeout")),
        "args": list(evidence_source.get("args") or []),
        "map_reference": dict(evidence_source.get("map_reference") or {}),
    }


def _map_reload_failure_analysis(
    *,
    metadata: dict[str, Any],
    direct_error: dict[str, Any],
    map_state: dict[str, Any],
) -> dict[str, Any]:
    schema_matches = map_state.get("schema_matches_expected")
    stderr = str(direct_error.get("stderr") or "")
    errno_value = direct_error.get("errno")
    e2big = (
        errno_value in {7, "7", "E2BIG"}
        or "argument list too long" in stderr.lower()
    )
    expected_key = map_state.get("expected_bytes_key")
    expected_value = map_state.get("expected_bytes_value")
    live_key = map_state.get("live_bytes_key")
    live_value = map_state.get("live_bytes_value")
    key_length = int(metadata.get("key_byte_length") or 0)
    value_length = int(metadata.get("value_byte_length") or 0)
    max_entries = map_state.get("max_entries")
    current_entries = map_state.get("current_entries")
    expected_entries = map_state.get("expected_entries")
    suspect_rows: list[dict[str, Any]] = []

    def add(name: str, matched: bool, evidence: dict[str, Any]) -> None:
        suspect_rows.append(
            {
                "candidate": name,
                "matched": bool(matched),
                "evidence": evidence,
            }
        )

    add(
        "generation_capacity_contract",
        str(direct_error.get("source_scope") or "") == "generation_capacity_guard",
        dict(map_state.get("generation_capacity_contract") or {}),
    )
    add(
        "schema_mismatch_stale_pinned_map",
        schema_matches is False,
        {
            "schema_matches_expected": schema_matches,
            "schema_mismatch_reason": map_state.get("schema_mismatch_reason"),
            "expected_type": map_state.get("expected_type"),
            "live_type": map_state.get("live_type"),
            "expected_bytes_key": expected_key,
            "live_bytes_key": live_key,
            "expected_bytes_value": expected_value,
            "live_bytes_value": live_value,
            "expected_max_entries": map_state.get("expected_max_entries"),
            "live_max_entries": map_state.get("live_max_entries"),
        },
    )
    add(
        "max_entries_full_map",
        schema_matches is True
        and max_entries is not None
        and current_entries is not None
        and int(current_entries) >= int(max_entries),
        {
            "current_entries": current_entries,
            "max_entries": max_entries,
            "expected_entries": expected_entries,
        },
    )
    add(
        "command_argument_construction",
        schema_matches is True
        and e2big
        and (
            not direct_error.get("args")
            or "key" not in [str(part) for part in direct_error.get("args") or []]
            or "value" not in [str(part) for part in direct_error.get("args") or []]
        ),
        {"args": list(direct_error.get("args") or [])},
    )
    add(
        "value_encoding_size",
        schema_matches is True
        and (
            (expected_key is not None and key_length != int(expected_key))
            or (expected_value is not None and value_length != int(expected_value))
        ),
        {
            "key_byte_length": key_length,
            "value_byte_length": value_length,
            "expected_bytes_key": expected_key,
            "expected_bytes_value": expected_value,
            "decode_warnings": list(metadata.get("decode_warnings") or []),
        },
    )
    add(
        "stale_entry_cleanup_order",
        schema_matches is True
        and int(map_state.get("stale_entries") or 0) > 0,
        {
            "current_entries": current_entries,
            "expected_entries": expected_entries,
            "stale_entries": map_state.get("stale_entries"),
            "stale_entry_reason": map_state.get("stale_entry_reason"),
        },
    )
    add(
        "wrong_generation_key_cardinality",
        schema_matches is True
        and expected_entries is not None
        and max_entries is not None
        and int(expected_entries) > int(max_entries),
        {
            "generation": metadata.get("generation"),
            "expected_entries": expected_entries,
            "max_entries": max_entries,
            "failed_key": dict(metadata.get("decoded_key") or {}),
        },
    )
    matched = [row for row in suspect_rows if row["matched"]]
    conclusion = "UNKNOWN_MAP_RELOAD_FAILURE"
    if str(direct_error.get("source_scope") or "") == "generation_capacity_guard":
        conclusion = BPF_MAP_GENERATION_CAPACITY_BLOCKER
    elif schema_matches is False:
        conclusion = "bpf_mpls_map_schema_mismatch"
    elif matched:
        conclusion = str(matched[0]["candidate"])
    elif e2big:
        conclusion = "E2BIG_REQUIRES_NON_SCHEMA_INVESTIGATION"
    return {
        "schema_version": "skyseed-bpf-map-reload-analysis-v1",
        "stderr_indicates_e2big": e2big,
        "schema_matches_expected": schema_matches,
        "stale_schema_suspected": schema_matches is False,
        "conclusion": conclusion,
        "candidate_causes": suspect_rows,
        "matched_candidate_causes": matched,
        "schema_match_investigation_required": schema_matches is True and e2big,
    }


def _first_failed_map_update(
    updates: Sequence[dict[str, Any]],
    *,
    expected_entries_by_map: dict[str, int],
    active_generation: int,
    allow_live_failure_analysis: bool = True,
) -> dict[str, Any] | None:
    for index, update in enumerate(updates):
        if command_ok(update):
            continue
        metadata = dict(update.get("map_update") or {})
        map_name = str(metadata.get("map_name") or "unknown")
        direct_error = _direct_error_evidence(update)
        map_state = (
            mpls_map_state(
                map_name,
                expected_entries=expected_entries_by_map.get(map_name),
                active_generation=active_generation,
                target_generation=metadata.get("generation"),
            )
            if allow_live_failure_analysis
            else {
                "skipped": True,
                "reason": "manifest_only_fail_closed_without_live_map_dump",
            }
        )
        if update.get("generation_capacity_contract"):
            map_state["generation_capacity_contract"] = dict(
                update.get("generation_capacity_contract") or {}
            )
        analysis = _map_reload_failure_analysis(
            metadata=metadata,
            direct_error=direct_error,
            map_state=map_state,
        )
        return {
            "schema_version": "skyseed-bpf-map-reload-root-cause-v1",
            "failed_update_index": index,
            "failed_map": map_name,
            "failed_operation": str(metadata.get("operation") or "update"),
            "generation": metadata.get("generation"),
            "active_generation": active_generation,
            "target_generation": metadata.get("generation"),
            "failed_key": dict(metadata.get("logical_key") or {}),
            "failed_key_decoded": dict(metadata.get("decoded_key") or {}),
            "failed_value_decoded": dict(metadata.get("decoded_value") or {}),
            "failed_key_hex": list(metadata.get("key_hex") or []),
            "failed_key_byte_length": metadata.get("key_byte_length"),
            "failed_value_hex_sample": list(metadata.get("value_hex_sample") or []),
            "failed_value_hex": list(metadata.get("value_hex") or []),
            "failed_value_byte_length": metadata.get("value_byte_length"),
            "direct_error_evidence": direct_error,
            "failure_analysis": analysis,
            "map_state": map_state,
            "active_generation_flip_attempted": map_name == "active_generation",
            "active_generation_left_unchanged_by_loader": map_name != "active_generation",
        }
    return None


def direction_u32(value: Any) -> int:
    direction = MPLS_DIRECTION.get(str(value))
    if direction is None:
        return int(value)
    return direction


def service_class_u32(_value: Any) -> int:
    return 0


def target_prefix_to_ipv4(value: Any) -> int:
    text = str(value or "")
    if not text:
        return 0
    network = ipaddress.ip_network(text, strict=False)
    return int.from_bytes(network.network_address.packed, byteorder="little")


def mac_pair_bytes(value: dict[str, Any]) -> list[str]:
    return [
        f"{byte:02x}"
        for byte in (mac_bytes(value.get("dst_mac")) + mac_bytes(value.get("src_mac")))
    ]


def load_mpls_maps(compiled: dict[str, Any]) -> dict[str, Any]:
    started_at = time.perf_counter()
    updates: list[dict[str, Any]] = []
    active_generation = int(
        dict(compiled.get("active_generation") or {}).get("value") or 0
    )
    runtime_update_phase = str(
        compiled.get("bpf_runtime_update_phase")
        or compiled.get("runtime_update_phase")
        or "unspecified"
    )
    steady_map_only_phase = runtime_update_phase == "steady_map_only"
    preactivation_preload_phase = runtime_update_phase == "preactivation_preload"
    defer_active_generation_flip = bool(
        compiled.get("defer_active_generation_flip")
        or preactivation_preload_phase
    )
    overlay_manifest = dict(compiled.get("bpf_generation_overlay_manifest") or {})
    manifest_only_loader = bool(
        compiled.get("bpf_map_write_manifest_only")
        or compiled.get("bpf_clean_manifest_only")
        or overlay_manifest.get("manifest_only_loader_required")
    )
    batch_map_write_required = bool(
        compiled.get("batch_map_write_required") or manifest_only_loader
    )
    skip_interface_role_map_updates = bool(
        compiled.get("skip_interface_role_map_updates")
    )
    interface_roles: dict[int, int] = {}
    interface_role_sources: dict[int, set[str]] = {}
    map_write_started_at = time.perf_counter()
    schema_states: dict[str, dict[str, Any]] = {}
    map_write_blocked = False
    pending_map_updates: list[dict[str, Any]] = []
    target_generation_cleanup: dict[str, Any] = {
        "attempted": True,
        "maps": {},
        "delete_update_count": 0,
        "ok": True,
    }
    retain_inactive_generations: set[int] = set()
    for generation in list(compiled.get("retain_inactive_generations") or []):
        if generation in (None, "") or str(generation) == "None":
            continue
        retain_inactive_generations.add(int(generation))
    require_preloaded_generation_flip_only = bool(
        compiled.get("require_preloaded_generation_flip_only")
    )
    parallel_preactivation_workers = max(
        1,
        int(compiled.get("parallel_preactivation_preload_map_update_workers") or 8),
    )
    generation_gc_cleanup: dict[str, Any] = {
        "attempted": True,
        "schema_version": "skyseed-bpf-generation-keyed-map-gc-v1",
        "maps": {},
        "delete_update_count": 0,
        "ok": True,
        "policy": {
            "delete_generations_neither_active_nor_target": True,
            "delete_target_generation_stale_entries": True,
            "never_delete_active_generation_before_flip": True,
            "retain_prepared_inactive_generations": sorted(
                retain_inactive_generations
            ),
            "require_preloaded_generation_flip_only": (
                require_preloaded_generation_flip_only
            ),
            "verify_generation_breakdown_after_cleanup": True,
        },
    }
    generation_capacity_contracts: dict[str, dict[str, Any]] = {}

    def schema_state_for(map_name: str) -> dict[str, Any]:
        if map_name not in schema_states:
            schema_states[map_name] = mpls_map_schema_state(map_name)
        return schema_states[map_name]

    def append_map_update(
        map_name: str,
        key_bytes: list[str],
        value_bytes: list[str],
        *,
        operation: str,
        generation: int | None,
        logical_key: dict[str, Any] | None = None,
    ) -> None:
        pending_map_updates.append(
            {
                "map_name": map_name,
                "key_bytes": list(key_bytes),
                "value_bytes": list(value_bytes),
                "operation": operation,
                "generation": generation,
                "logical_key": dict(logical_key or {}),
            }
        )

    def expected_role_bits(expected_roles: Any) -> int:
        bits = 0
        for raw_role in list(expected_roles or []):
            role_name = str(raw_role or "").lower()
            if "ut_gateway_selector" in role_name:
                bits |= MPLS_INTERFACE_ROLE_UT_GATEWAY_SELECTOR
            if "classifier" in role_name:
                bits |= MPLS_INTERFACE_ROLE_CLASSIFIER
            if (
                "mpls" in role_name
                or "lfib" in role_name
                or "handoff" in role_name
            ):
                bits |= MPLS_INTERFACE_ROLE_MPLS
        return bits

    def add_interface_role(ifindex: int, role: int, source: str) -> None:
        if ifindex <= 0:
            return
        interface_roles[ifindex] = int(interface_roles.get(ifindex, 0)) | int(role)
        if source:
            interface_role_sources.setdefault(ifindex, set()).add(source)

    loaded_classifier_keys: list[dict[str, Any]] = []
    for entry in compiled.get("ingress_classifier_map", []) or []:
        key = dict(entry.get("key") or {})
        value = dict(entry.get("value") or {})
        generation = int(key.get("generation", active_generation))
        ingress_ifindex = int(key.get("ingress_ifindex") or 0)
        add_interface_role(
            ingress_ifindex,
            MPLS_INTERFACE_ROLE_CLASSIFIER,
            "ingress_classifier_map",
        )
        dst_ipv4 = int(key.get("dst_ipv4") or target_prefix_to_ipv4(key.get("target_prefix")))
        service_class = int(key.get("service_class_u32") or service_class_u32(key.get("service_class")))
        fec_id = int(value.get("fec_id_u32") or stable_u32(str(value.get("fec_id") or "")))
        map_key = pack_u32s(generation, ingress_ifindex, dst_ipv4, service_class)
        map_value = (
            pack_u32s(
                1 if value.get("active", True) else 0,
                fec_id,
                direction_u32(value.get("direction", G_TO_Z)),
                int(value.get("initial_label") or 0),
                int(value.get("out_ifindex") or 0),
                int(value.get("inner_ethertype") or ETH_P_IP),
            )
            + mac_pair_bytes(value)
        )
        append_map_update(
                "ingress_classifier_map",
                map_key,
                map_value,
                operation="update",
                generation=generation,
                logical_key={
                    "generation": generation,
                    "ingress_ifindex": ingress_ifindex,
                    "dst_ipv4": dst_ipv4,
                    "service_class": service_class,
                },
        )
        loaded_classifier_keys.append(
            {
                "generation": generation,
                "ingress_ifindex": ingress_ifindex,
                "dst_ipv4": dst_ipv4,
                "service_class": service_class,
            }
        )

    loaded_core_keys: list[dict[str, Any]] = []
    loaded_gateway_selector_keys: list[dict[str, Any]] = []
    for entry in compiled.get("gateway_selector_ingress_map", []) or []:
        key = dict(entry.get("key") or {})
        value = dict(entry.get("value") or {})
        ingress_ifindex = int(key.get("ingress_ifindex") or 0)
        add_interface_role(
            ingress_ifindex,
            MPLS_INTERFACE_ROLE_MPLS,
            "gateway_selector_ingress_map",
        )
        generation = int(key.get("generation", active_generation))
        map_key = pack_u32s(
            generation,
            ingress_ifindex,
            int(key.get("label") or 0),
            direction_u32(key.get("direction", G_TO_Z)),
        )
        map_value = (
            pack_u32s(
                1 if value.get("active", True) else 0,
                MPLS_CORE_ACTION.get(str(value.get("action") or "swap"), 1),
                int(value.get("out_label") or 0),
                int(value.get("out_ifindex") or 0),
            )
            + mac_pair_bytes(value)
        )
        append_map_update(
            "gateway_selector_ingress_map",
            map_key,
            map_value,
            operation="update",
            generation=generation,
            logical_key=dict(key),
        )
        loaded_gateway_selector_keys.append(dict(key))

    for entry in compiled.get("core_lfib_map", []) or []:
        key = dict(entry.get("key") or {})
        value = dict(entry.get("value") or {})
        add_interface_role(
            int(key.get("ingress_ifindex") or 0),
            MPLS_INTERFACE_ROLE_MPLS,
            "core_lfib_map",
        )
        map_key = pack_u32s(
            int(key.get("generation", active_generation)),
            int(key.get("ingress_ifindex") or 0),
            int(key.get("label") or 0),
            direction_u32(key.get("direction", G_TO_Z)),
        )
        map_value = (
            pack_u32s(
                1 if value.get("active", True) else 0,
                MPLS_CORE_ACTION.get(str(value.get("action") or "swap"), 1),
                int(value.get("out_label") or key.get("label") or 0),
                int(value.get("out_ifindex") or 0),
            )
            + mac_pair_bytes(value)
        )
        generation = int(key.get("generation", active_generation))
        append_map_update(
                "core_lfib_map",
                map_key,
                map_value,
                operation="update",
                generation=generation,
                logical_key=dict(key),
        )
        loaded_core_keys.append(dict(key))

    loaded_handoff_keys: list[dict[str, Any]] = []
    for entry in compiled.get("egress_handoff_map", []) or []:
        key = dict(entry.get("key") or {})
        value = dict(entry.get("value") or {})
        add_interface_role(
            int(key.get("ingress_ifindex") or 0),
            MPLS_INTERFACE_ROLE_MPLS,
            "egress_handoff_map",
        )
        map_key = pack_u32s(
            int(key.get("generation", active_generation)),
            int(key.get("ingress_ifindex") or 0),
            int(key.get("label") or 0),
            direction_u32(key.get("direction", G_TO_Z)),
        )
        new_dst_ip = (
            ipv4_little_endian_u32(str(value.get("new_dst_ip")))
            if value.get("new_dst_ip")
            else 0
        )
        map_value = (
            pack_u32s(
                1 if value.get("active", True) else 0,
                (
                    4
                    if str(
                        value.get("handoff")
                        or dict(entry.get("metadata") or {}).get("handoff")
                        or ""
                    )
                    == "shared_zone_edge_lookup"
                    else 3
                ),
                int(value.get("out_ifindex") or 0),
                int(value.get("inner_ethertype") or ETH_P_IP),
                new_dst_ip,
            )
            + mac_pair_bytes(value)
        )
        generation = int(key.get("generation", active_generation))
        append_map_update(
                "egress_handoff_map",
                map_key,
                map_value,
                operation="update",
                generation=generation,
                logical_key=dict(key),
        )
        loaded_handoff_keys.append(dict(key))

    loaded_zone_edge_delivery_keys: list[dict[str, Any]] = []
    for entry in compiled.get("zone_edge_delivery_map", []) or []:
        key = dict(entry.get("key") or {})
        value = dict(entry.get("value") or {})
        ingress_ifindex = int(key.get("ingress_ifindex") or 0)
        add_interface_role(
            ingress_ifindex,
            MPLS_INTERFACE_ROLE_MPLS,
            "zone_edge_delivery_map",
        )
        generation = int(key.get("generation", active_generation))
        dst_ipv4 = int(key.get("dst_ipv4") or 0)
        new_dst_ip = (
            ipv4_little_endian_u32(str(value.get("new_dst_ip")))
            if value.get("new_dst_ip")
            else 0
        )
        append_map_update(
            "zone_edge_delivery_map",
            pack_u32s(
                generation,
                ingress_ifindex,
                int(key.get("label") or 0),
                dst_ipv4,
            ),
            pack_u32s(
                1 if value.get("active", True) else 0,
                int(value.get("out_ifindex") or 0),
                new_dst_ip,
            )
            + mac_pair_bytes(value),
            operation="update",
            generation=generation,
            logical_key={
                "generation": generation,
                "ingress_ifindex": ingress_ifindex,
                "label": int(key.get("label") or 0),
                "dst_ipv4": dst_ipv4,
            },
        )
        loaded_zone_edge_delivery_keys.append(dict(key))

    loaded_generation_base_keys: list[dict[str, Any]] = []
    generation_base_entries = list(compiled.get("generation_base_map") or [])
    if not generation_base_entries and compiled.get("generation_base") is not None:
        generation_base = dict(compiled.get("generation_base") or {})
        generation_base_entries = [
            {
                "key": {
                    "generation": int(
                        generation_base.get("generation")
                        if generation_base.get("generation") not in (None, "", "None")
                        else active_generation
                    )
                },
                "value": {
                    "base_generation": int(
                        generation_base.get("base_generation")
                        if generation_base.get("base_generation")
                        not in (None, "", "None")
                        else active_generation
                    )
                },
            }
        ]
    for entry in generation_base_entries:
        key = dict(entry.get("key") or {})
        value = dict(entry.get("value") or {})
        generation = int(key.get("generation", active_generation))
        base_generation = int(value.get("base_generation", generation))
        append_map_update(
            "generation_base_map",
            pack_u32s(generation),
            pack_u32s(base_generation),
            operation="update",
            generation=generation,
            logical_key={"generation": generation},
        )
        loaded_generation_base_keys.append(
            {
                "generation": generation,
                "base_generation": base_generation,
            }
        )

    loaded_delete_manifest_keys: list[dict[str, Any]] = []
    for entry in list(compiled.get("bpf_map_delete_manifest") or []):
        item = dict(entry or {})
        map_name = str(item.get("map_name") or "")
        if map_name not in MPLS_MAP_SHAPES:
            raise ValueError(f"unknown BPF map in delete manifest: {map_name}")
        key_bytes = list(item.get("key_bytes") or [])
        if not key_bytes:
            raise ValueError(f"missing key bytes in BPF delete manifest: {map_name}")
        generation = (
            int(item.get("generation"))
            if item.get("generation") not in (None, "", "None")
            else None
        )
        logical_key = dict(item.get("logical_key") or {})
        append_map_update(
            map_name,
            key_bytes,
            [],
            operation=str(item.get("operation") or "delete_manifest_key"),
            generation=generation,
            logical_key=logical_key,
        )
        loaded_delete_manifest_keys.append(
            {
                "map_name": map_name,
                "generation": generation,
                "logical_key": logical_key,
            }
        )

    for detail in compiled.get("attached_interface_details", []) or []:
        attached_detail = dict(detail or {})
        role_bits = expected_role_bits(attached_detail.get("expected_roles"))
        if role_bits:
            add_interface_role(
                int(attached_detail.get("ifindex") or 0),
                role_bits,
                "attached_interface_details",
            )

    loaded_interface_roles: list[dict[str, Any]] = []
    for ifindex, role in sorted(interface_roles.items()):
        if not skip_interface_role_map_updates:
            append_map_update(
                "interface_role_map",
                pack_u32s(ifindex),
                pack_u32s(role),
                operation="update",
                generation=active_generation,
                logical_key={"ifindex": ifindex},
            )
        loaded_interface_roles.append(
            {
                "ifindex": ifindex,
                "role": role,
                "classifier": bool(role & MPLS_INTERFACE_ROLE_CLASSIFIER),
                "mpls": bool(role & MPLS_INTERFACE_ROLE_MPLS),
                **(
                    {"ut_gateway_selector": True}
                    if role & MPLS_INTERFACE_ROLE_UT_GATEWAY_SELECTOR
                    else {}
                ),
            }
        )

    expected_entries_by_map = {
        "ingress_classifier_map": len(loaded_classifier_keys),
        "gateway_selector_ingress_map": len(loaded_gateway_selector_keys),
        "core_lfib_map": len(loaded_core_keys),
        "egress_handoff_map": len(loaded_handoff_keys),
        "zone_edge_delivery_map": len(loaded_zone_edge_delivery_keys),
        "generation_base_map": len(loaded_generation_base_keys),
        "active_generation": 1,
    }
    interface_role_map_update_expected_count = (
        0 if skip_interface_role_map_updates else len(loaded_interface_roles)
    )
    if not skip_interface_role_map_updates:
        expected_entries_by_map["interface_role_map"] = len(loaded_interface_roles)
    live_active_generation_read = (
        {
            "skipped": True,
            "reason": "manifest_only_loader_does_not_probe_live_active_generation",
            "active_generation": None,
        }
        if manifest_only_loader
        else read_mpls_active_generation()
    )
    live_active_generation = live_active_generation_read.get("active_generation")
    live_active_generation_int = (
        int(live_active_generation)
        if live_active_generation is not None
        else None
    )
    desired_updates_by_map: dict[str, list[dict[str, Any]]] = {}
    desired_key_ids_by_map: dict[str, set[str]] = {}
    for pending in pending_map_updates:
        map_name = str(pending.get("map_name") or "")
        desired_updates_by_map.setdefault(map_name, []).append(pending)
        desired_key_ids_by_map.setdefault(map_name, set()).add(
            _hex_key_id(list(pending.get("key_bytes") or []))
        )
    post_gc_map_states: dict[str, dict[str, Any]] = {}
    map_diff_trace: dict[str, dict[str, Any]] = {
        map_name: {
            "map_name": map_name,
            "desired_count": len(items),
            "existing_count": None,
            "insert_count": 0,
            "update_count": 0,
            "delete_count": 0,
            "noop_skipped_count": 0,
            "wall_seconds": 0.0,
            "diff_trace_complete": True,
        }
        for map_name, items in desired_updates_by_map.items()
    }

    def first_pending_for(map_name: str) -> dict[str, Any] | None:
        for pending in pending_map_updates:
            if pending["map_name"] == map_name:
                return dict(pending)
        return None

    def block_with_skipped_update(
        pending: dict[str, Any],
        *,
        reason: str,
        schema_state: dict[str, Any] | None = None,
        capacity_contract: dict[str, Any] | None = None,
    ) -> None:
        nonlocal map_write_blocked
        skipped = _skipped_map_update(
            str(pending["map_name"]),
            list(pending["key_bytes"]),
            list(pending["value_bytes"]),
            operation=str(pending["operation"]),
            generation=pending.get("generation"),
            reason=reason,
            logical_key=dict(pending.get("logical_key") or {}),
        )
        if schema_state is not None:
            skipped["map_schema_state"] = dict(schema_state)
        if capacity_contract is not None:
            skipped["generation_capacity_contract"] = dict(capacity_contract)
        updates.append(skipped)
        map_write_blocked = True

    if (
        not manifest_only_loader
        and
        preactivation_preload_phase
        and live_active_generation_int == int(active_generation)
        and pending_map_updates
    ):
        block_with_skipped_update(
            pending_map_updates[0],
            reason="RUNTIME_PRELOAD_VISIBILITY_PROOF_FAILED",
        )

    if manifest_only_loader:
        generation_gc_cleanup["attempted"] = False
        generation_gc_cleanup["reason"] = (
            "manifest_only_loader_uses_preplanned_cleanup_work_items"
        )
        generation_gc_cleanup["policy"]["verify_generation_breakdown_after_cleanup"] = False
        target_generation_cleanup["attempted"] = False
        target_generation_cleanup["reason"] = (
            "manifest_only_loader_uses_preplanned_cleanup_work_items"
        )
    for map_name in (
        ()
        if manifest_only_loader
        else (
            "ingress_classifier_map",
            "gateway_selector_ingress_map",
            "core_lfib_map",
            "egress_handoff_map",
            "zone_edge_delivery_map",
            "generation_base_map",
            "interface_role_map",
        )
    ):
        pending = first_pending_for(map_name)
        if pending is None:
            continue
        schema_state = schema_state_for(map_name)
        if schema_state.get("schema_matches_expected") is not True:
            block_with_skipped_update(
                pending,
                reason=str(
                    schema_state.get("schema_mismatch_reason")
                    or "bpf_mpls_map_schema_mismatch"
                ),
                schema_state=schema_state,
            )
            break
        if map_name not in GENERATION_KEYED_MPLS_MAPS:
            continue
        target_expected_entries = int(expected_entries_by_map.get(map_name) or 0)
        pre_gc_map_state = mpls_map_state(
            map_name,
            expected_entries=target_expected_entries,
            active_generation=(
                int(live_active_generation)
                if live_active_generation is not None
                else None
            ),
            target_generation=active_generation,
        )
        retained_generations = {
            generation
            for generation in (live_active_generation_int, int(active_generation))
            if generation is not None
        } | set(retain_inactive_generations)
        generation_keys = dict(
            pre_gc_map_state.get("generation_key_hex_by_generation") or {}
        )
        stale_generation_keys: list[tuple[int, list[str]]] = []
        for generation_raw, keys in generation_keys.items():
            generation = int(generation_raw)
            if generation in retained_generations:
                continue
            for key_hex in list(keys or []):
                stale_generation_keys.append(
                    (generation, [str(part) for part in key_hex])
                )
        desired_target_key_ids = desired_key_ids_by_map.get(map_name, set())
        stale_target_keys: list[list[str]] = []
        if live_active_generation_int != int(active_generation):
            for key_hex in list(
                pre_gc_map_state.get("target_generation_stale_key_hex") or []
            ):
                normalized_key_hex = _normalize_bpftool_hex_list(key_hex)
                if _hex_key_id(normalized_key_hex) not in desired_target_key_ids:
                    stale_target_keys.append(normalized_key_hex)
        cleanup_results: list[dict[str, Any]] = []
        old_generation_cleanup_results: list[dict[str, Any]] = []
        target_generation_cleanup_results: list[dict[str, Any]] = []
        for stale_generation, key_hex in stale_generation_keys:
            cleanup_result = _annotated_map_delete(
                map_name,
                [str(part) for part in key_hex],
                generation=stale_generation,
                operation="delete_stale_inactive_generation",
                logical_key={"generation": stale_generation},
            )
            old_generation_cleanup_results.append(cleanup_result)
            cleanup_results.append(cleanup_result)
            updates.append(cleanup_result)
            if not command_ok(cleanup_result):
                map_write_blocked = True
                break
        if map_write_blocked:
            generation_gc_cleanup["ok"] = False
            break
        for key_hex in stale_target_keys:
            cleanup_result = _annotated_map_delete(
                map_name,
                [str(part) for part in key_hex],
                generation=active_generation,
                operation="delete_stale_target_generation",
                logical_key={"generation": active_generation},
            )
            target_generation_cleanup_results.append(cleanup_result)
            cleanup_results.append(cleanup_result)
            updates.append(cleanup_result)
            if not command_ok(cleanup_result):
                map_write_blocked = True
                break
        target_generation_cleanup["maps"][map_name] = {
            "target_generation": active_generation,
            "stale_target_entry_count": len(stale_target_keys),
            "delete_update_count": len(target_generation_cleanup_results),
            "ok": all(command_ok(item) for item in target_generation_cleanup_results),
        }
        if map_name in map_diff_trace:
            map_diff_trace[map_name]["delete_count"] = len(
                target_generation_cleanup_results
            )
        target_generation_cleanup["delete_update_count"] = int(
            target_generation_cleanup.get("delete_update_count") or 0
        ) + len(target_generation_cleanup_results)
        if target_generation_cleanup_results and not all(
            command_ok(item) for item in target_generation_cleanup_results
        ):
            target_generation_cleanup["ok"] = False
            break
        if map_write_blocked:
            generation_gc_cleanup["ok"] = False
            break
        post_gc_map_state = mpls_map_state(
            map_name,
            expected_entries=target_expected_entries,
            active_generation=live_active_generation_int,
            target_generation=active_generation,
        )
        post_gc_map_states[map_name] = dict(post_gc_map_state)
        post_gc_generation_counts = dict(
            post_gc_map_state.get("generation_entry_counts") or {}
        )
        unexpected_generations_after_gc = [
            generation
            for generation in sorted(post_gc_generation_counts)
            if int(generation) not in retained_generations
        ]
        target_generation_entries_after_gc = int(
            post_gc_map_state.get("target_generation_entry_count") or 0
        )
        generation_gc_map_result = {
            "map_name": map_name,
            "active_generation": live_active_generation_int,
            "target_generation": active_generation,
            "retained_generations": sorted(retained_generations),
            "entries_before_gc": int(pre_gc_map_state.get("current_entries") or 0),
            "entries_after_gc": int(post_gc_map_state.get("current_entries") or 0),
            "pre_cleanup_generation_entry_counts": dict(
                pre_gc_map_state.get("generation_entry_counts") or {}
            ),
            "post_cleanup_generation_entry_counts": post_gc_generation_counts,
            "stale_inactive_generation_entry_count": len(stale_generation_keys),
            "target_generation_stale_entry_count": len(stale_target_keys),
            "delete_update_count": len(cleanup_results),
            "old_generation_delete_update_count": len(old_generation_cleanup_results),
            "target_generation_delete_update_count": len(
                target_generation_cleanup_results
            ),
            "unexpected_generations_after_gc": unexpected_generations_after_gc,
            "target_generation_entry_count_after_gc": target_generation_entries_after_gc,
            "verified_after_cleanup": True,
            "ok": (
                all(command_ok(item) for item in cleanup_results)
                and not unexpected_generations_after_gc
                and (
                    live_active_generation_int == int(active_generation)
                    or target_generation_entries_after_gc <= target_expected_entries
                )
            ),
        }
        generation_gc_cleanup["maps"][map_name] = generation_gc_map_result
        generation_gc_cleanup["delete_update_count"] = int(
            generation_gc_cleanup.get("delete_update_count") or 0
        ) + len(cleanup_results)
        if not generation_gc_map_result["ok"]:
            generation_gc_cleanup["ok"] = False
            block_with_skipped_update(
                pending,
                reason=(
                    "BPF_MAP_GENERATION_GC_FAILED:"
                    f"map={map_name};"
                    f"active_generation={live_active_generation_int};"
                    f"target_generation={active_generation};"
                    f"unexpected_generations_after_gc={unexpected_generations_after_gc};"
                    f"target_generation_entry_count_after_gc={target_generation_entries_after_gc}"
                ),
            )
            break
        active_count = int(post_gc_map_state.get("active_generation_entry_count") or 0)
        retained_entry_count_after_gc = int(
            post_gc_map_state.get("current_entries") or 0
        )
        target_generation_entry_count_after_gc = int(
            post_gc_map_state.get("target_generation_entry_count") or 0
        )
        missing_target_entry_capacity = max(
            0,
            target_expected_entries - target_generation_entry_count_after_gc,
        )
        if live_active_generation_int is not None and live_active_generation_int == int(active_generation):
            required_capacity = max(active_count, target_expected_entries) + GENERATION_CAPACITY_SLACK
        else:
            required_capacity = (
                retained_entry_count_after_gc
                + missing_target_entry_capacity
                + GENERATION_CAPACITY_SLACK
            )
        live_max_entries = post_gc_map_state.get("max_entries")
        capacity_contract = {
            "schema_version": "skyseed-bpf-map-generation-capacity-v1",
            "label": BPF_MAP_GENERATION_CAPACITY_BLOCKER,
            "map_name": map_name,
            "generation_keyed": True,
            "active_generation": live_active_generation_int,
            "target_generation": active_generation,
            "active_generation_entry_count": active_count,
            "retained_entry_count_after_gc": retained_entry_count_after_gc,
            "target_expected_entries": target_expected_entries,
            "target_generation_entry_count_after_gc": (
                target_generation_entry_count_after_gc
            ),
            "missing_target_entry_capacity": missing_target_entry_capacity,
            "target_generation_stale_entries_cleared": len(stale_target_keys),
            "stale_inactive_generation_entries_cleared": len(stale_generation_keys),
            "post_cleanup_generation_entry_counts": post_gc_generation_counts,
            "slack_entries": GENERATION_CAPACITY_SLACK,
            "required_capacity": required_capacity,
            "live_max_entries": live_max_entries,
            "static_expected_max_entries": post_gc_map_state.get("expected_max_entries"),
            "type_key_value_matches_expected": post_gc_map_state.get(
                "type_key_value_matches_expected"
            ),
            "capacity_matches_static_expected_max": post_gc_map_state.get(
                "capacity_matches_static_expected_max"
            ),
            "generation_entry_counts": dict(
                post_gc_map_state.get("generation_entry_counts") or {}
            ),
            "ok": live_max_entries is None or int(live_max_entries) >= required_capacity,
        }
        generation_capacity_contracts[map_name] = dict(capacity_contract)
        if live_max_entries is not None and int(live_max_entries) < required_capacity:
            block_with_skipped_update(
                pending,
                reason=(
                    f"{BPF_MAP_GENERATION_CAPACITY_BLOCKER}:"
                    f"map={map_name};"
                    f"required_capacity={required_capacity};"
                    f"live_max_entries={live_max_entries};"
                    f"active_generation={live_active_generation};"
                    f"active_generation_entry_count={active_count};"
                    f"target_generation={active_generation};"
                    f"target_expected_entries={target_expected_entries}"
                ),
                capacity_contract=capacity_contract,
            )
            break

    noop_map_updates: list[dict[str, Any]] = []
    filtered_pending_map_updates: list[dict[str, Any]] = []
    if manifest_only_loader and not map_write_blocked:
        if require_preloaded_generation_flip_only:
            filtered_pending_map_updates = []
            for map_name, desired_updates in desired_updates_by_map.items():
                diff = map_diff_trace.setdefault(
                    map_name,
                    {
                        "map_name": map_name,
                        "desired_count": len(desired_updates),
                        "existing_count": None,
                        "insert_count": 0,
                        "update_count": 0,
                        "delete_count": 0,
                        "noop_skipped_count": 0,
                        "wall_seconds": 0.0,
                        "diff_trace_complete": True,
                    },
                )
                diff["manifest_only_flip_only_data_write_skipped"] = len(
                    desired_updates
                )
                diff["diff_trace_complete"] = True
        else:
            filtered_pending_map_updates = [dict(item) for item in pending_map_updates]
            for map_name, desired_updates in desired_updates_by_map.items():
                desired_delete_count = sum(
                    1
                    for item in desired_updates
                    if str(item.get("operation") or "").startswith("delete_")
                )
                desired_write_count = len(desired_updates) - desired_delete_count
                diff = map_diff_trace.setdefault(
                    map_name,
                    {
                        "map_name": map_name,
                        "desired_count": len(desired_updates),
                        "existing_count": None,
                        "insert_count": 0,
                        "update_count": 0,
                        "delete_count": 0,
                        "noop_skipped_count": 0,
                        "wall_seconds": 0.0,
                        "diff_trace_complete": True,
                    },
                )
                diff["insert_count"] = desired_write_count
                diff["delete_count"] = desired_delete_count
                diff["existing_count"] = None
                diff["loaded_generations"] = {}
                diff["manifest_only_loader"] = True
                diff["diff_trace_complete"] = True
    elif not map_write_blocked:
        for map_name, desired_updates in desired_updates_by_map.items():
            diff_started_at = time.perf_counter()
            if map_name in post_gc_map_states:
                state = dict(post_gc_map_states[map_name])
                live_values_by_key = dict(
                    state.get("target_generation_value_hex_by_key") or {}
                )
            else:
                state = mpls_map_state(
                    map_name,
                    expected_entries=expected_entries_by_map.get(map_name),
                    active_generation=live_active_generation_int,
                    target_generation=active_generation,
                )
                live_values_by_key = dict(state.get("live_entries_by_key_hex") or {})
            diff = map_diff_trace.setdefault(
                map_name,
                {
                    "map_name": map_name,
                    "desired_count": len(desired_updates),
                    "existing_count": None,
                    "insert_count": 0,
                    "update_count": 0,
                    "delete_count": 0,
                    "noop_skipped_count": 0,
                    "wall_seconds": 0.0,
                    "diff_trace_complete": True,
                },
            )
            diff["existing_count"] = len(live_values_by_key)
            diff["loaded_generations"] = dict(
                state.get("generation_entry_counts") or {}
            )
            diff["active_generation_before"] = live_active_generation_int
            diff["target_generation"] = active_generation
            diff["diff_trace_complete"] = bool(
                state.get("live_entry_dump_complete", True)
            )
            if not diff["diff_trace_complete"]:
                if desired_updates:
                    block_with_skipped_update(
                        desired_updates[0],
                        reason="RUNTIME_DIFF_TRACE_INCOMPLETE",
                    )
                break
            for pending in desired_updates:
                key_bytes = list(pending.get("key_bytes") or [])
                value_bytes = list(pending.get("value_bytes") or [])
                key_id = _hex_key_id(key_bytes)
                live_value = live_values_by_key.get(key_id)
                if live_value is None:
                    diff["insert_count"] = int(diff.get("insert_count") or 0) + 1
                    filtered_pending_map_updates.append(pending)
                elif live_value == _normalize_bpftool_hex_list(value_bytes):
                    diff["noop_skipped_count"] = int(
                        diff.get("noop_skipped_count") or 0
                    ) + 1
                    noop_map_updates.append(
                        _noop_map_update(
                            str(pending["map_name"]),
                            key_bytes,
                            value_bytes,
                            operation=str(pending["operation"]),
                            generation=pending.get("generation"),
                            reason="desired_entry_already_loaded",
                            logical_key=dict(pending.get("logical_key") or {}),
                        )
                    )
                else:
                    diff["update_count"] = int(diff.get("update_count") or 0) + 1
                    filtered_pending_map_updates.append(pending)
            diff["wall_seconds"] = round(time.perf_counter() - diff_started_at, 6)

    expected_data_map_write_update_count = (
        len(loaded_classifier_keys)
        + len(loaded_core_keys)
        + len(loaded_handoff_keys)
        + len(loaded_generation_base_keys)
        + interface_role_map_update_expected_count
    )
    actual_data_write_count = sum(
        1
        for item in filtered_pending_map_updates
        if not str(item.get("operation") or "").startswith("delete_")
    )
    actual_data_delete_count = sum(
        1
        for item in filtered_pending_map_updates
        if str(item.get("operation") or "").startswith("delete_")
    )
    full_generation_reload_blocker = (
        steady_map_only_phase
        and expected_data_map_write_update_count > 0
        and actual_data_write_count >= expected_data_map_write_update_count
    )
    if (
        not map_write_blocked
        and full_generation_reload_blocker
        and filtered_pending_map_updates
    ):
        block_with_skipped_update(
            filtered_pending_map_updates[0],
            reason="RUNTIME_FULL_GENERATION_RELOAD_BLOCKER",
        )

    if (
        not map_write_blocked
        and require_preloaded_generation_flip_only
        and (actual_data_write_count + actual_data_delete_count) > 0
        and filtered_pending_map_updates
    ):
        block_with_skipped_update(
            filtered_pending_map_updates[0],
            reason="RUNTIME_PRELOADED_GENERATION_MISSING_OR_INCOMPLETE",
        )

    parallel_map_write_enabled = bool(
        preactivation_preload_phase
        and len(filtered_pending_map_updates) > 1
        and parallel_preactivation_workers > 1
        and not batch_map_write_required
    )
    parallel_map_write_worker_count = (
        min(parallel_preactivation_workers, len(filtered_pending_map_updates))
        if parallel_map_write_enabled
        else 1
    )

    def write_pending_map_update(pending: Mapping[str, Any]) -> dict[str, Any]:
        operation = str(pending.get("operation") or "update")
        if operation.startswith("delete_"):
            return _annotated_map_delete(
                str(pending["map_name"]),
                list(pending["key_bytes"]),
                operation=operation,
                generation=pending.get("generation"),
                logical_key=dict(pending.get("logical_key") or {}),
            )
        return _annotated_map_update(
            str(pending["map_name"]),
            list(pending["key_bytes"]),
            list(pending["value_bytes"]),
            operation=operation,
            generation=pending.get("generation"),
            logical_key=dict(pending.get("logical_key") or {}),
        )

    if not map_write_blocked:
        if batch_map_write_required and filtered_pending_map_updates:
            written_updates = _annotated_batch_map_updates(
                filtered_pending_map_updates
            )
            updates.extend(written_updates)
            if not all(command_ok(update) for update in written_updates):
                map_write_blocked = True
        elif parallel_map_write_enabled:
            with ThreadPoolExecutor(
                max_workers=parallel_map_write_worker_count
            ) as executor:
                written_updates = list(
                    executor.map(write_pending_map_update, filtered_pending_map_updates)
                )
            updates.extend(written_updates)
            if not all(command_ok(update) for update in written_updates):
                map_write_blocked = True
        else:
            for pending in filtered_pending_map_updates:
                update = write_pending_map_update(pending)
                updates.append(update)
                if not command_ok(update):
                    map_write_blocked = True
                    break

    map_write_update_count = len(
        [
            update
            for update in updates
            if not update.get("skipped")
            if not str(
                dict(update.get("map_update") or {}).get("operation") or ""
            ).startswith("delete_")
        ]
    )
    map_write_wall_seconds = round(time.perf_counter() - map_write_started_at, 6)
    map_write_ok = all(command_ok(update) for update in updates)
    active_generation_flip_started_at = time.perf_counter()
    if map_write_ok:
        if defer_active_generation_flip:
            active_generation_update = _noop_map_update(
                "active_generation",
                pack_u32s(0),
                pack_u32s(active_generation),
                operation="active_generation_flip_deferred",
                generation=active_generation,
                reason="preactivation_preload_keeps_active_generation_unchanged",
                logical_key={"slot": 0},
            )
        elif live_active_generation_int == int(active_generation):
            active_generation_update = _noop_map_update(
                "active_generation",
                pack_u32s(0),
                pack_u32s(active_generation),
                operation="active_generation_flip_noop",
                generation=active_generation,
                reason="active_generation_already_selected",
                logical_key={"slot": 0},
            )
        else:
            active_generation_schema = schema_state_for("active_generation")
            if active_generation_schema.get("schema_matches_expected") is True:
                active_generation_update = _annotated_map_update(
                    "active_generation",
                    pack_u32s(0),
                    pack_u32s(active_generation),
                    operation="active_generation_flip",
                    generation=active_generation,
                    logical_key={"slot": 0},
                )
            else:
                active_generation_update = _skipped_map_update(
                    "active_generation",
                    pack_u32s(0),
                    pack_u32s(active_generation),
                    operation="active_generation_flip",
                    generation=active_generation,
                    reason=str(
                        active_generation_schema.get("schema_mismatch_reason")
                        or "bpf_mpls_map_schema_mismatch"
                    ),
                    logical_key={"slot": 0},
                )
                active_generation_update["map_schema_state"] = dict(
                    active_generation_schema
                )
    else:
        active_generation_update = _skipped_map_update(
            "active_generation",
            pack_u32s(0),
            pack_u32s(active_generation),
            operation="active_generation_flip",
            generation=active_generation,
            reason="data_map_update_failed_before_active_generation_flip",
            logical_key={"slot": 0},
        )
    active_generation_flip_wall_seconds = round(
        time.perf_counter() - active_generation_flip_started_at,
        6,
    )
    updates.append(active_generation_update)
    active_generation_update_count = (
        1
        if command_ok(active_generation_update)
        and not active_generation_update.get("noop_skipped")
        else 0
    )
    generation_write_cost_audit = _map_update_generation_write_cost_audit(
        pending_map_updates=pending_map_updates,
        filtered_pending_map_updates=filtered_pending_map_updates,
        noop_map_updates=noop_map_updates,
        map_diff_trace=map_diff_trace,
        updates=updates,
        loaded_interface_role_count=len(loaded_interface_roles),
        skip_interface_role_map_updates=skip_interface_role_map_updates,
        active_generation_update=active_generation_update,
        active_generation_update_count=active_generation_update_count,
        defer_active_generation_flip=defer_active_generation_flip,
        runtime_update_phase=runtime_update_phase,
        active_generation=active_generation,
        live_active_generation_before=live_active_generation_int,
        map_write_wall_seconds=map_write_wall_seconds,
        active_generation_flip_wall_seconds=active_generation_flip_wall_seconds,
        manifest_only_loader=manifest_only_loader,
        batch_map_write_required=batch_map_write_required,
        batch_map_write_used=bool(
            batch_map_write_required and filtered_pending_map_updates
        ),
    )
    expected_data_map_write_update_count = (
        len(loaded_classifier_keys)
        + len(loaded_core_keys)
        + len(loaded_handoff_keys)
        + len(loaded_generation_base_keys)
        + interface_role_map_update_expected_count
    )
    first_failed_update = _first_failed_map_update(
        updates,
        expected_entries_by_map=expected_entries_by_map,
        active_generation=(
            int(live_active_generation)
            if live_active_generation is not None
            else active_generation
        ),
        allow_live_failure_analysis=not manifest_only_loader,
    )
    per_map_operation_summary: dict[str, dict[str, Any]] = {
        map_name: {
            "map_name": map_name,
            "entries_before": None,
            "entries_after_gc": None,
            "expected_entries_after_load": int(expected_count),
            "entries_inserted_or_updated": 0,
            "entries_deleted": 0,
            "command_count": 0,
            "duplicate_key_write_count": 0,
            "noop_write_count": 0,
            "noop_write_detectability": "live_key_value_diff",
            "wall_seconds": 0.0,
        }
        for map_name, expected_count in expected_entries_by_map.items()
    }
    pending_key_counts: dict[tuple[str, tuple[str, ...]], int] = {}
    for pending in pending_map_updates:
        pending_key = (
            str(pending.get("map_name") or ""),
            tuple(str(part) for part in pending.get("key_bytes") or []),
        )
        pending_key_counts[pending_key] = pending_key_counts.get(pending_key, 0) + 1
    for (map_name, _key), count in pending_key_counts.items():
        if count <= 1:
            continue
        per_map_operation_summary.setdefault(
            map_name,
            {
                "map_name": map_name,
                "entries_before": None,
                "entries_after_gc": None,
                "expected_entries_after_load": None,
                "entries_inserted_or_updated": 0,
                "entries_deleted": 0,
                "command_count": 0,
                "duplicate_key_write_count": 0,
                "noop_write_count": 0,
                "noop_write_detectability": "live_key_value_diff",
                "wall_seconds": 0.0,
            },
        )["duplicate_key_write_count"] += count - 1
    for map_name, gc_result in dict(generation_gc_cleanup.get("maps") or {}).items():
        summary = per_map_operation_summary.get(str(map_name))
        if summary is None:
            continue
        summary["entries_before"] = int(gc_result.get("entries_before_gc") or 0)
        summary["entries_after_gc"] = int(gc_result.get("entries_after_gc") or 0)
        summary["generation_gc_delete_count"] = int(gc_result.get("delete_update_count") or 0)
        summary["target_generation_stale_entry_count"] = int(
            gc_result.get("target_generation_stale_entry_count") or 0
        )
        summary["stale_inactive_generation_entry_count"] = int(
            gc_result.get("stale_inactive_generation_entry_count") or 0
        )
    for update in updates:
        metadata = dict(update.get("map_update") or {})
        map_name = str(metadata.get("map_name") or "unknown")
        operation = str(metadata.get("operation") or "update")
        summary = per_map_operation_summary.setdefault(
            map_name,
            {
                "map_name": map_name,
                "entries_before": None,
                "entries_after_gc": None,
                "expected_entries_after_load": None,
                "entries_inserted_or_updated": 0,
                "entries_deleted": 0,
                "command_count": 0,
                "duplicate_key_write_count": 0,
                "noop_write_count": 0,
                "noop_write_detectability": "live_key_value_diff",
                "wall_seconds": 0.0,
            },
        )
        if "skipped" not in update:
            sub_updates = update.get("sub_updates")
            summary["command_count"] += (
                len(sub_updates) if isinstance(sub_updates, list) else 1
            )
        if update.get("noop_skipped"):
            summary["noop_write_count"] = int(summary.get("noop_write_count") or 0) + 1
        elif update.get("skipped"):
            summary["skipped_blocked_count"] = int(
                summary.get("skipped_blocked_count") or 0
            ) + 1
        elif operation.startswith("delete_"):
            summary["entries_deleted"] += 1
        elif not operation.startswith("active_generation_flip"):
            summary["entries_inserted_or_updated"] += 1
        else:
            summary["active_generation_flip_count"] = int(
                summary.get("active_generation_flip_count") or 0
            ) + 1
        summary["wall_seconds"] = round(
            float(summary.get("wall_seconds") or 0.0)
            + float(update.get("map_update_wall_seconds") or 0.0),
            6,
        )
    for update in noop_map_updates:
        metadata = dict(update.get("map_update") or {})
        map_name = str(metadata.get("map_name") or "unknown")
        summary = per_map_operation_summary.setdefault(
            map_name,
            {
                "map_name": map_name,
                "entries_before": None,
                "entries_after_gc": None,
                "expected_entries_after_load": None,
                "entries_inserted_or_updated": 0,
                "entries_deleted": 0,
                "command_count": 0,
                "duplicate_key_write_count": 0,
                "noop_write_count": 0,
                "noop_write_detectability": "live_key_value_diff",
                "wall_seconds": 0.0,
            },
        )
        summary["noop_write_count"] = int(summary.get("noop_write_count") or 0) + 1
    for map_name, diff in map_diff_trace.items():
        summary = per_map_operation_summary.setdefault(
            map_name,
            {
                "map_name": map_name,
                "entries_before": None,
                "entries_after_gc": None,
                "expected_entries_after_load": None,
                "entries_inserted_or_updated": 0,
                "entries_deleted": 0,
                "command_count": 0,
                "duplicate_key_write_count": 0,
                "noop_write_count": 0,
                "noop_write_detectability": "live_key_value_diff",
                "wall_seconds": 0.0,
            },
        )
        summary["diff"] = dict(diff)
        summary["desired_count"] = diff.get("desired_count")
        summary["existing_count"] = diff.get("existing_count")
        summary["insert_count"] = diff.get("insert_count")
        summary["update_count"] = diff.get("update_count")
        summary["delete_count"] = diff.get("delete_count")
        summary["noop_skipped_count"] = diff.get("noop_skipped_count")
        summary["loaded_generations"] = dict(diff.get("loaded_generations") or {})
    total_noop_skipped_count = sum(
        int(diff.get("noop_skipped_count") or 0)
        for diff in map_diff_trace.values()
    )
    active_generation_flip_only = (
        actual_data_write_count == 0
        and active_generation_update_count == 1
        and not defer_active_generation_flip
    )
    preloaded_generation_used = (
        steady_map_only_phase
        and total_noop_skipped_count >= expected_data_map_write_update_count
        and expected_data_map_write_update_count > 0
        and active_generation_update_count == 1
    )
    preactivation_preload_data_write_used = (
        preactivation_preload_phase
        and not full_generation_reload_blocker
        and actual_data_write_count > 0
    )
    manifest_only_map_write_used = (
        manifest_only_loader
        and not full_generation_reload_blocker
        and (actual_data_write_count + actual_data_delete_count) > 0
    )
    map_diff_update_used = (
        (
            preactivation_preload_data_write_used
            and not manifest_only_loader
        )
        or (
            not manifest_only_loader
            and
            not full_generation_reload_blocker
            and actual_data_write_count > 0
            and actual_data_write_count < expected_data_map_write_update_count
        )
    )
    outcome_labels: list[str] = []
    if preloaded_generation_used:
        outcome_labels.append("RUNTIME_PRELOADED_GENERATION_USED")
    if manifest_only_map_write_used:
        outcome_labels.append("RUNTIME_MANIFEST_ONLY_MAP_WRITE_USED")
    if manifest_only_map_write_used and batch_map_write_required:
        outcome_labels.append("RUNTIME_MANIFEST_ONLY_BATCH_MAP_WRITE_USED")
    if map_diff_update_used:
        outcome_labels.append("RUNTIME_MAP_DIFF_UPDATE_USED")
    if total_noop_skipped_count:
        outcome_labels.append("RUNTIME_NOOP_MAP_WRITES_SKIPPED")
    if active_generation_flip_only:
        outcome_labels.append("RUNTIME_ACTIVE_GENERATION_FLIP_ONLY")
    if full_generation_reload_blocker:
        outcome_labels.append("RUNTIME_FULL_GENERATION_RELOAD_BLOCKER")
    if any(not bool(diff.get("diff_trace_complete", True)) for diff in map_diff_trace.values()):
        outcome_labels.append("RUNTIME_DIFF_TRACE_INCOMPLETE")
    if preactivation_preload_phase and live_active_generation_int == int(active_generation):
        outcome_labels.append("RUNTIME_PRELOAD_VISIBILITY_PROOF_FAILED")
    if skip_interface_role_map_updates and loaded_interface_roles:
        outcome_labels.append("RUNTIME_INTERFACE_ROLE_MAP_UPDATES_SKIPPED")
    if parallel_map_write_enabled:
        outcome_labels.append("RUNTIME_PREACTIVATION_PRELOAD_PARALLEL_MAP_WRITE_USED")
    if not outcome_labels and actual_data_write_count:
        outcome_labels.append("RUNTIME_MAP_DIFF_UPDATE_USED")
    reload_strategy = (
        "preloaded_generation_active_generation_flip_only"
        if active_generation_flip_only
        else "manifest_only_batch_map_write"
        if manifest_only_map_write_used and batch_map_write_required
        else "manifest_only_map_write"
        if manifest_only_map_write_used
        else "map_diff_update"
        if map_diff_update_used or total_noop_skipped_count
        else "full_generation_reload_blocked"
        if full_generation_reload_blocker
        else "map_load"
    )
    runtime_reload_attribution = {
        "schema_version": "skyseed-bpf-map-reload-attribution-v1",
        "reload_strategy": reload_strategy,
        "runtime_update_phase": runtime_update_phase,
        "full_generation_reload": False,
        "full_generation_reload_blocker": full_generation_reload_blocker,
        "diff_update_enabled": True,
        "manifest_only_loader": manifest_only_loader,
        "batch_map_write_required": batch_map_write_required,
        "batch_map_write_used": bool(
            batch_map_write_required and filtered_pending_map_updates
        ),
        "batch_map_write_update_count": (
            len(filtered_pending_map_updates) if batch_map_write_required else 0
        ),
        "batch_map_write_delete_count": (
            actual_data_delete_count if batch_map_write_required else 0
        ),
        "retain_inactive_generations": sorted(retain_inactive_generations),
        "require_preloaded_generation_flip_only": (
            require_preloaded_generation_flip_only
        ),
        "preactivation_preload": preactivation_preload_phase,
        "preactivation_preload_data_write_used": (
            preactivation_preload_data_write_used
        ),
        "defer_active_generation_flip": defer_active_generation_flip,
        "parallel_map_write_enabled": parallel_map_write_enabled,
        "parallel_map_write_worker_count": parallel_map_write_worker_count,
        "parallel_map_write_scheduled_update_count": (
            len(filtered_pending_map_updates) if parallel_map_write_enabled else 0
        ),
        "interface_role_map_updates_skipped": skip_interface_role_map_updates,
        "interface_role_map_update_skip_reason": (
            "roles_prepared_by_attach_capability"
            if skip_interface_role_map_updates
            else ""
        ),
        "interface_role_map_skipped_update_count": (
            len(loaded_interface_roles) if skip_interface_role_map_updates else 0
        ),
        "preloaded_generation_used": preloaded_generation_used,
        "active_generation_flip_only": active_generation_flip_only,
        "map_diff_update_used": map_diff_update_used,
        "manifest_only_map_write_used": manifest_only_map_write_used,
        "actual_data_write_count": actual_data_write_count,
        "actual_data_delete_count": actual_data_delete_count,
        "expected_data_map_write_update_count": expected_data_map_write_update_count,
        "noop_skipped_count": total_noop_skipped_count,
        "loaded_generations_by_map": {
            map_name: dict(diff.get("loaded_generations") or {})
            for map_name, diff in map_diff_trace.items()
        },
        "active_generation_before": live_active_generation_int,
        "active_generation_after": (
            active_generation
            if active_generation_update_count == 1
            else live_active_generation_int
        ),
        "inactive_preloaded_generation_visibility_proof": {
            "target_generation": active_generation,
            "active_generation_before": live_active_generation_int,
            "target_entries_loaded_while_inactive": (
                live_active_generation_int is not None
                and live_active_generation_int != int(active_generation)
            ),
            "active_generation_flip_deferred": defer_active_generation_flip,
            "proof": "BPF lookups are generation-keyed and selected by active_generation",
            "ok": not (
                preactivation_preload_phase
                and live_active_generation_int == int(active_generation)
            ),
        },
        "validation_or_reporting_included_in_map_write_wall_time": False,
        "schema_state_cache_scope": "per_load_mpls_maps_call",
        "map_schema_dump_reused_within_load": True,
        "duplicate_noop_detection_scope": "live_key_value_diff",
        "map_diff_trace": map_diff_trace,
        "per_map_operation_summary": per_map_operation_summary,
        "generation_write_cost_audit": generation_write_cost_audit,
        "outcome_labels": outcome_labels,
    }

    return {
        "updates": updates,
        "ok": all(command_ok(update) for update in updates),
        "runtime_reload_attribution": runtime_reload_attribution,
        "reload_strategy": runtime_reload_attribution["reload_strategy"],
        "diff_update_enabled": True,
        "manifest_only_loader": manifest_only_loader,
        "batch_map_write_required": batch_map_write_required,
        "map_diff_trace": map_diff_trace,
        "generation_write_cost_audit": generation_write_cost_audit,
        "noop_map_updates": noop_map_updates,
        "noop_map_write_count": total_noop_skipped_count,
        "actual_data_write_count": actual_data_write_count,
        "actual_data_delete_count": actual_data_delete_count,
        "full_generation_reload_blocker": full_generation_reload_blocker,
        "preloaded_generation_used": preloaded_generation_used,
        "active_generation_flip_only": active_generation_flip_only,
        "outcome_labels": list(runtime_reload_attribution["outcome_labels"]),
        "active_generation": active_generation,
        "active_generation_update": active_generation_update,
        "map_write_update_count": map_write_update_count,
        "parallel_map_write_enabled": parallel_map_write_enabled,
        "batch_map_write_used": bool(
            batch_map_write_required and filtered_pending_map_updates
        ),
        "batch_map_write_update_count": (
            len(filtered_pending_map_updates) if batch_map_write_required else 0
        ),
        "batch_map_write_delete_count": (
            actual_data_delete_count if batch_map_write_required else 0
        ),
        "parallel_map_write_worker_count": parallel_map_write_worker_count,
        "parallel_map_write_scheduled_update_count": (
            len(filtered_pending_map_updates) if parallel_map_write_enabled else 0
        ),
        "active_generation_update_count": active_generation_update_count,
        "active_generation_switch_count": active_generation_update_count,
        "active_generation_flip_skipped": (
            not map_write_ok or bool(active_generation_update.get("noop_skipped"))
        ),
        "active_generation_flip_attempted": (
            bool(map_write_ok) and not active_generation_update.get("noop_skipped")
        ),
        "active_generation_flip_bpftool_invoked": active_generation_update_count == 1,
        "active_generation_left_unchanged_by_loader": (
            not map_write_ok or bool(active_generation_update.get("noop_skipped"))
        ),
        "map_write_blocked_after_first_failure": bool(map_write_blocked),
        "live_active_generation_before_load": live_active_generation_read,
        "target_generation_cleanup": target_generation_cleanup,
        "generation_gc_cleanup": generation_gc_cleanup,
        "generation_capacity_contracts": generation_capacity_contracts,
        "expected_data_map_write_update_count": expected_data_map_write_update_count,
        "expected_total_update_count": expected_data_map_write_update_count + 1,
        "total_update_count": len(updates),
        "map_write_wall_seconds": map_write_wall_seconds,
        "active_generation_flip_wall_seconds": active_generation_flip_wall_seconds,
        "total_wall_seconds": round(time.perf_counter() - started_at, 6),
        "first_failed_update": first_failed_update,
        "failed_map": None if first_failed_update is None else first_failed_update.get("failed_map"),
        "failed_operation": None if first_failed_update is None else first_failed_update.get("failed_operation"),
        "failed_generation": None if first_failed_update is None else first_failed_update.get("generation"),
        "map_state": {} if first_failed_update is None else dict(first_failed_update.get("map_state") or {}),
        "schema_consistency": {
            "bootstrap_loader": "load_mpls_maps",
            "steady_reload_loader": "load_mpls_maps",
            "bootstrap_and_steady_reload_share_schema": True,
            "live_schema_states": dict(schema_states),
            "map_shapes": {
                name: dict(MPLS_MAP_SHAPES.get(name) or {})
                for name in expected_entries_by_map
            },
        },
        "generation_update_order": {
            "inactive_generation_written_before_active_generation_flip": True,
            "active_generation_update_is_last": True,
            "active_generation_flip_requires_all_data_map_updates": True,
        },
        "loaded_classifier_count": len(loaded_classifier_keys),
        "loaded_gateway_selector_ingress_count": len(
            loaded_gateway_selector_keys
        ),
        "loaded_core_lfib_count": len(loaded_core_keys),
        "loaded_egress_handoff_count": len(loaded_handoff_keys),
        "loaded_zone_edge_delivery_count": len(loaded_zone_edge_delivery_keys),
        "loaded_generation_base_count": len(loaded_generation_base_keys),
        "loaded_delete_manifest_count": len(loaded_delete_manifest_keys),
        "loaded_interface_role_count": len(loaded_interface_roles),
        "interface_role_map_updates_skipped": skip_interface_role_map_updates,
        "interface_role_map_update_skip_reason": (
            "roles_prepared_by_attach_capability"
            if skip_interface_role_map_updates
            else ""
        ),
        "interface_role_map_skipped_update_count": (
            len(loaded_interface_roles) if skip_interface_role_map_updates else 0
        ),
        "loaded_classifier_keys": loaded_classifier_keys,
        "loaded_gateway_selector_ingress_keys": loaded_gateway_selector_keys,
        "loaded_core_lfib_keys": loaded_core_keys,
        "loaded_egress_handoff_keys": loaded_handoff_keys,
        "loaded_zone_edge_delivery_keys": loaded_zone_edge_delivery_keys,
        "loaded_interface_roles": loaded_interface_roles,
        "loaded_interface_role_sources": [
            {"ifindex": ifindex, "sources": sorted(sources)}
            for ifindex, sources in sorted(interface_role_sources.items())
        ],
        "intermediate_core_lookup_uses_dst_ip": False,
    }


def read_mpls_packet_counters() -> dict[str, Any]:
    counters: dict[str, int] = {label: 0 for label in MPLS_COUNTER_LABELS.values()}
    dumps: list[dict[str, Any]] = []
    for ref_args, ref_meta in mpls_map_references("packet_counters"):
        result = bpftool(["-j", "map", "dump", *ref_args], timeout=8.0)
        parsed: Any = None
        if result.get("stdout"):
            try:
                parsed = json.loads(result["stdout"])
            except json.JSONDecodeError:
                parsed = None
        for entry in parsed if isinstance(parsed, list) else []:
            key = decode_bpftool_hex_u32(entry.get("key"))
            value = decode_bpftool_hex_u64(entry.get("value"))
            label = MPLS_COUNTER_LABELS.get(key)
            if label is not None:
                counters[label] += value
        dumps.append(
            {
                "command": result,
                "map_reference": ref_meta,
                "parsed": parsed,
            }
        )
    return {
        "command": dumps[-1]["command"] if dumps else {},
        "map_reference": {
            "kind": "fanout",
            "count": len(dumps),
            "references": [dump["map_reference"] for dump in dumps],
        },
        "parsed": [dump["parsed"] for dump in dumps],
        "counters": counters,
    }


def _bpftool_hex_bytes(value: Any) -> bytes:
    if not isinstance(value, list):
        return b""
    rendered: list[int] = []
    for item in value:
        try:
            rendered.append(int(str(item), 16))
        except ValueError:
            return b""
    return bytes(rendered)


def _u32_from_bytes(data: bytes, offset: int) -> int:
    if offset + 4 > len(data):
        return 0
    return int.from_bytes(data[offset : offset + 4], "little")


def _u64_from_bytes(data: bytes, offset: int) -> int:
    if offset + 8 > len(data):
        return 0
    return int.from_bytes(data[offset : offset + 8], "little")


def _ipv4_from_le_u32(value: int) -> str | None:
    if not value:
        return None
    try:
        return str(ipaddress.IPv4Address(int(value).to_bytes(4, "little")))
    except ipaddress.AddressValueError:
        return None


def _decode_miss_sample(slot: int, value: Any) -> dict[str, Any] | None:
    data = _bpftool_hex_bytes(value)
    if len(data) < 80:
        return None
    sequence = _u64_from_bytes(data, 0)
    reason = _u32_from_bytes(data, 8)
    map_id = _u32_from_bytes(data, 12)
    if sequence == 0 and reason == 0 and map_id == 0:
        return None
    src_ipv4 = _u32_from_bytes(data, 44)
    dst_ipv4 = _u32_from_bytes(data, 48)
    key_dst_ipv4 = _u32_from_bytes(data, 60)
    return {
        "slot": slot,
        "slot_name": MPLS_MISS_SAMPLE_SLOTS.get(slot, f"slot_{slot}"),
        "sequence": sequence,
        "reason_id": reason,
        "reason": MPLS_MISS_SAMPLE_REASONS.get(reason, "unknown"),
        "map_id": map_id,
        "map_that_missed": MPLS_MISS_SAMPLE_MAPS.get(map_id, "unknown"),
        "generation": _u32_from_bytes(data, 16),
        "active_generation": _u32_from_bytes(data, 20),
        "ifindex": _u32_from_bytes(data, 24),
        "interface_role_bits": _u32_from_bytes(data, 28),
        "interface_role": {
            "classifier": bool(_u32_from_bytes(data, 28) & MPLS_INTERFACE_ROLE_CLASSIFIER),
            "mpls": bool(_u32_from_bytes(data, 28) & MPLS_INTERFACE_ROLE_MPLS),
        },
        "direction": _u32_from_bytes(data, 32),
        "ethertype": _u32_from_bytes(data, 36),
        "has_mpls_shim": bool(_u32_from_bytes(data, 40)),
        "src_ipv4": src_ipv4,
        "src_ip": _ipv4_from_le_u32(src_ipv4),
        "dst_ipv4": dst_ipv4,
        "dst_ip": _ipv4_from_le_u32(dst_ipv4),
        "lookup_key": {
            "generation": _u32_from_bytes(data, 52),
            "ingress_ifindex": _u32_from_bytes(data, 56),
            "dst_ipv4": key_dst_ipv4,
            "dst_ip": _ipv4_from_le_u32(key_dst_ipv4),
            "service_class": _u32_from_bytes(data, 64),
            "label": _u32_from_bytes(data, 68),
            "direction": _u32_from_bytes(data, 72),
        },
        "gz_scope": bool(_u32_from_bytes(data, 76)),
    }


def read_mpls_miss_samples() -> dict[str, Any]:
    samples: list[dict[str, Any]] = []
    dumps: list[dict[str, Any]] = []
    for ref_args, ref_meta in mpls_map_references("miss_samples"):
        result = bpftool(["-j", "map", "dump", *ref_args], timeout=8.0)
        parsed: Any = None
        if result.get("stdout"):
            try:
                parsed = json.loads(result["stdout"])
            except json.JSONDecodeError:
                parsed = None
        for entry in parsed if isinstance(parsed, list) else []:
            slot = decode_bpftool_hex_u32(entry.get("key"))
            sample = _decode_miss_sample(slot, entry.get("value"))
            if sample is not None:
                sample["map_reference"] = ref_meta
                samples.append(sample)
        dumps.append(
            {
                "command": result,
                "map_reference": ref_meta,
                "parsed": parsed,
            }
        )
    samples.sort(key=lambda item: (int(item.get("sequence") or 0), int(item.get("slot") or 0)))
    return {
        "ok": all(command_ok(dump["command"]) for dump in dumps) if dumps else False,
        "command": dumps[-1]["command"] if dumps else {},
        "map_reference": {
            "kind": "fanout",
            "count": len(dumps),
            "references": [dump["map_reference"] for dump in dumps],
        },
        "sample_count": len(samples),
        "samples": samples,
        "parsed": [dump["parsed"] for dump in dumps],
    }


def read_mpls_interface_counters() -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    dumps: list[dict[str, Any]] = []
    for ref_args, ref_meta in mpls_map_references("interface_counters"):
        result = bpftool(["-j", "map", "dump", *ref_args], timeout=8.0)
        parsed: Any = None
        if result.get("stdout"):
            try:
                parsed = json.loads(result["stdout"])
            except json.JSONDecodeError:
                parsed = None
        for entry in parsed if isinstance(parsed, list) else []:
            ifindex, counter_id = decode_bpftool_hex_u32_pair(entry.get("key"))
            entries.append(
                {
                    "ifindex": ifindex,
                    "counter_id": counter_id,
                    "counter_label": MPLS_COUNTER_LABELS.get(
                        counter_id,
                        f"counter_{counter_id}",
                    ),
                    "value": decode_bpftool_hex_u64(entry.get("value")),
                    "map_reference": ref_meta,
                }
            )
        dumps.append(
            {
                "command": result,
                "map_reference": ref_meta,
                "parsed": parsed,
            }
        )
    return {
        "command": dumps[-1]["command"] if dumps else {},
        "map_reference": {
            "kind": "fanout",
            "count": len(dumps),
            "references": [dump["map_reference"] for dump in dumps],
        },
        "parsed": [dump["parsed"] for dump in dumps],
        "entries": entries,
    }


def read_mpls_interface_roles() -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    dumps: list[dict[str, Any]] = []
    for ref_args, ref_meta in mpls_map_references("interface_role_map"):
        result = bpftool(["-j", "map", "dump", *ref_args], timeout=8.0)
        parsed: Any = None
        if result.get("stdout"):
            try:
                parsed = json.loads(result["stdout"])
            except json.JSONDecodeError:
                parsed = None
        for entry in parsed if isinstance(parsed, list) else []:
            ifindex = decode_bpftool_hex_u32(entry.get("key"))
            role = decode_bpftool_hex_u32(entry.get("value"))
            entries.append(
                {
                    "ifindex": ifindex,
                    "role": role,
                    "classifier": bool(role & MPLS_INTERFACE_ROLE_CLASSIFIER),
                    "mpls": bool(role & MPLS_INTERFACE_ROLE_MPLS),
                    "map_reference": ref_meta,
                }
            )
        dumps.append(
            {
                "command": result,
                "map_reference": ref_meta,
                "parsed": parsed,
            }
        )
    return {
        "ok": all(command_ok(dump["command"]) for dump in dumps) if dumps else False,
        "command": dumps[-1]["command"] if dumps else {},
        "map_reference": {
            "kind": "fanout",
            "count": len(dumps),
            "references": [dump["map_reference"] for dump in dumps],
        },
        "parsed": [dump["parsed"] for dump in dumps],
        "entries": entries,
    }


def read_mpls_active_generation() -> dict[str, Any]:
    generations: list[int] = []
    dumps: list[dict[str, Any]] = []
    for ref_args, ref_meta in mpls_map_references("active_generation"):
        result = bpftool(["-j", "map", "dump", *ref_args], timeout=8.0)
        parsed: Any = None
        if result.get("stdout"):
            try:
                parsed = json.loads(result["stdout"])
            except json.JSONDecodeError:
                parsed = None
        for entry in parsed if isinstance(parsed, list) else []:
            if decode_bpftool_hex_u32(entry.get("key")) == 0:
                generations.append(decode_bpftool_hex_u32(entry.get("value")))
        dumps.append(
            {
                "command": result,
                "map_reference": ref_meta,
                "parsed": parsed,
            }
        )
    active_generation = generations[-1] if generations else None
    return {
        "ok": bool(generations) and len(set(generations)) == 1,
        "command": dumps[-1]["command"] if dumps else {},
        "map_reference": {
            "kind": "fanout",
            "count": len(dumps),
            "references": [dump["map_reference"] for dump in dumps],
        },
        "parsed": [dump["parsed"] for dump in dumps],
        "active_generation": active_generation,
        "active_generations": generations,
    }
