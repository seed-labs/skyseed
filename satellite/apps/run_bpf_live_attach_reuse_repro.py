#!/usr/bin/env python3
# encoding: utf-8

"""Run a small live BPF attach-reuse/map-reload repro."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import time
from typing import Any

from satellite.bpf_dataplane_canary import command_ok
from satellite.bpf_mpls_live import (
    attach_mpls,
    build_minimal_live_maps,
    cleanup_minimal_live_namespaces,
    compile_mpls_bpf,
    load_mpls_maps,
    mpls_residue_check,
    read_mpls_active_generation,
    read_mpls_packet_counters,
    rollback_mpls,
    run_command,
    setup_minimal_live_namespaces,
)
from satellite.linux_mpls_runtime import _bpf_owned_attach_reuse_plan


def _with_generation(compiled: dict[str, Any], generation: int) -> dict[str, Any]:
    updated = deepcopy(compiled)
    updated["active_generation"] = {"value": int(generation)}
    for map_name in (
        "ingress_classifier_map",
        "core_lfib_map",
        "egress_handoff_map",
    ):
        for entry in list(updated.get(map_name) or []):
            key = dict(entry.get("key") or {})
            key["generation"] = int(generation)
            entry["key"] = key
    return updated


def _counter_delta(
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, int]:
    before_counters = dict(before.get("counters") or {})
    after_counters = dict(after.get("counters") or {})
    labels = sorted(set(before_counters) | set(after_counters))
    return {
        label: int(after_counters.get(label) or 0) - int(before_counters.get(label) or 0)
        for label in labels
    }


def _ping(names: dict[str, str]) -> dict[str, Any]:
    return run_command(
        [
            "ip",
            "netns",
            "exec",
            names["ut"],
            "ping",
            "-n",
            "-c",
            "1",
            "-W",
            "2",
            "10.80.3.2",
        ],
        timeout=10.0,
        privileged=True,
    )


def run_attach_reuse_repro(*, output_dir: Path) -> dict[str, Any]:
    started_at = time.perf_counter()
    suffix = f"{int(time.time()) % 100000:x}"
    output_dir.mkdir(parents=True, exist_ok=True)
    names: dict[str, str] = {}
    attached: list[str] = []
    report: dict[str, Any] = {
        "schema_version": "skyseed-bpf-live-attach-reuse-repro-v1",
        "ok": False,
        "strict_linux_fallback": False,
        "linux_gz_fallback_enabled": False,
        "linux_gz_fallback_command_count": 0,
        "eligible_core_fallback_to_linux": 0,
        "packet_keyed_ip_route_fallback": False,
        "runtime_transactions": [],
        "steps": {},
    }
    with tempfile.TemporaryDirectory(prefix="skyseed-bpf-attach-reuse-") as tmp:
        object_path = Path(tmp) / "skyseed_tc_mpls_dataplane.o"
        try:
            setup = setup_minimal_live_namespaces(suffix)
            report["steps"]["setup"] = setup
            names = dict(setup.get("names") or {})
            if not setup.get("ok"):
                report["reason"] = "minimal_topology_setup_failed"
                return report

            compiled0 = build_minimal_live_maps(names)
            compiled1 = _with_generation(compiled0, 1)
            attach_plan = {
                str(interface_ref): [str(direction) for direction in directions]
                for interface_ref, directions in dict(
                    compiled0.get("attach_plan") or {}
                ).items()
            }
            interfaces = list(attach_plan.keys())
            report["attach_plan"] = attach_plan

            first_started_at = time.perf_counter()
            compile_result = compile_mpls_bpf(object_path=object_path)
            report["steps"]["compile"] = compile_result
            if not command_ok(compile_result):
                report["reason"] = "compile_failed"
                return report
            report["steps"]["cleanup_before_attach"] = rollback_mpls(
                attached_interfaces=interfaces,
                remove_maps=True,
            )
            attach_results: list[dict[str, Any]] = []
            for interface_ref in interfaces:
                result = attach_mpls(
                    interface_ref,
                    object_path,
                    directions=attach_plan.get(interface_ref, ["ingress"]),
                )
                attach_results.extend(result)
                attached.append(interface_ref)
            report["steps"]["attach"] = attach_results
            attach_ok = bool(attach_results) and all(
                command_ok(result)
                or (
                    "tc" in [str(part) for part in result.get("args", [])]
                    and "qdisc" in [str(part) for part in result.get("args", [])]
                    and "del" in [str(part) for part in result.get("args", [])]
                )
                for result in attach_results
            )
            if not attach_ok:
                report["reason"] = "tc_bpf_attach_failed"
                return report
            first_map_load = load_mpls_maps(compiled0)
            first_ping_before = read_mpls_packet_counters()
            first_ping = _ping(names)
            first_ping_after = read_mpls_packet_counters()
            first_wall = time.perf_counter() - first_started_at

            previous_readiness = {
                "prepare_steps": {
                    "real_backend_prepare": {
                        "attach_plan": attach_plan,
                    },
                },
            }
            attach_reuse = _bpf_owned_attach_reuse_plan(
                compiled=compiled1,
                previous_readiness=previous_readiness,
            )
            second_started_at = time.perf_counter()
            second_map_load = load_mpls_maps(compiled1)
            second_ping_before = read_mpls_packet_counters()
            second_ping = _ping(names)
            second_ping_after = read_mpls_packet_counters()
            second_wall = time.perf_counter() - second_started_at

            report["runtime_transactions"] = [
                {
                    "sequence": 1,
                    "generation": 0,
                    "prepare_mode": "full_attach_and_map_load",
                    "wall_seconds": round(first_wall, 6),
                    "map_load_ok": bool(first_map_load.get("ok")),
                    "ping_ok": command_ok(first_ping),
                    "counter_delta": _counter_delta(first_ping_before, first_ping_after),
                },
                {
                    "sequence": 2,
                    "generation": 1,
                    "prepare_mode": "reuse_attached_bpf_map_reload",
                    "attach_reuse": attach_reuse,
                    "wall_seconds": round(second_wall, 6),
                    "map_load_ok": bool(second_map_load.get("ok")),
                    "ping_ok": command_ok(second_ping),
                    "counter_delta": _counter_delta(second_ping_before, second_ping_after),
                },
            ]
            final_counters = read_mpls_packet_counters()
            report["active_generation"] = read_mpls_active_generation()
            report["final_packet_counters"] = final_counters
            counters = dict(final_counters.get("counters") or {})
            required_positive = (
                "classifier_hit",
                "label_push",
                "core_lfib_hit",
                "label_swap",
                "egress_handoff_hit",
                "label_pop",
            )
            required_zero = (
                "miss",
                "drop",
                "unknown_role_count",
                "role_map_missing_count",
                "gz_ip_pass_without_classification",
                "mpls_shim_pass_without_lfib_or_handoff",
            )
            report["ok"] = (
                bool(first_map_load.get("ok"))
                and bool(second_map_load.get("ok"))
                and command_ok(first_ping)
                and command_ok(second_ping)
                and attach_reuse.get("reuse_eligible") is True
                and all(int(counters.get(label) or 0) > 0 for label in required_positive)
                and all(int(counters.get(label) or 0) == 0 for label in required_zero)
                and int(report["active_generation"].get("active_generation") or -1) == 1
            )
            report["reason"] = "" if report["ok"] else "bpf_attach_reuse_repro_failed"
            return report
        finally:
            if attached:
                report["rollback"] = rollback_mpls(
                    attached_interfaces=attached,
                    remove_maps=True,
                )
            if names:
                report["namespace_cleanup"] = cleanup_minimal_live_namespaces(names)
            report["post_run_residue"] = mpls_residue_check(attached)
            report["wall_seconds"] = round(time.perf_counter() - started_at, 6)
            json_path = output_dir / "bpf_live_attach_reuse_repro.json"
            report["artifact"] = str(json_path)
            json_path.write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("satellite/output/bpf-live-attach-reuse-repro-20260623"),
    )
    args = parser.parse_args()
    report = run_attach_reuse_repro(output_dir=args.output_dir)
    print(
        json.dumps(
            {
                "ok": report.get("ok"),
                "reason": report.get("reason"),
                "artifact": report.get("artifact"),
                "runtime_transactions": report.get("runtime_transactions"),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
