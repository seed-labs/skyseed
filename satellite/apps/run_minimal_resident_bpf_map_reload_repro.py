#!/usr/bin/env python3
# encoding: utf-8

"""Smallest live bounded resident BPF map reload repro.

This is intentionally not a rich S4 or acceptance runner. It creates a tiny
netns/veth topology, performs one fresh tc-BPF attach with generation 0, then
reuses the resident attach and reloads generation 1 maps before validation.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any, Sequence

SATELLITE_DIR = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = SATELLITE_DIR.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))
if str(SATELLITE_DIR) not in sys.path:
    sys.path.insert(0, str(SATELLITE_DIR))

from satellite.bpf_dataplane_canary import command_ok, run_command  # noqa: E402
from satellite.bpf_mpls_live import (  # noqa: E402
    build_minimal_divergent_generation_maps,
    cleanup_minimal_live_namespaces,
    compile_mpls_bpf,
    attach_mpls,
    load_mpls_maps,
    mpls_map_state,
    mpls_residue_check,
    read_mpls_active_generation,
    read_mpls_packet_counters,
    rollback_mpls,
    setup_minimal_divergent_live_namespaces,
    verify_mpls_clsact,
)
from satellite.skyseed_preflight import run_skyseed_preflight  # noqa: E402


RELOADED_MAPS = (
    "ingress_classifier_map",
    "core_lfib_map",
    "egress_handoff_map",
    "interface_role_map",
    "active_generation",
)


def _json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _filter_compiled_generation(compiled: dict[str, Any], generation: int) -> dict[str, Any]:
    filtered = dict(compiled)
    for map_name in (
        "ingress_classifier_map",
        "core_lfib_map",
        "egress_handoff_map",
    ):
        filtered[map_name] = [
            dict(entry)
            for entry in list(compiled.get(map_name) or [])
            if int(dict(dict(entry).get("key") or {}).get("generation") or 0)
            == int(generation)
        ]
    filtered["active_generation"] = {"value": int(generation)}
    filtered["schema_version"] = (
        f"skyseed-live-mpls-resident-reload-generation-{generation}-v1"
    )
    return filtered


def _expected_entries(compiled: dict[str, Any]) -> dict[str, int]:
    interface_ifindexes: set[int] = set()
    for map_name in (
        "ingress_classifier_map",
        "core_lfib_map",
        "egress_handoff_map",
    ):
        for entry in list(compiled.get(map_name) or []):
            key = dict(dict(entry).get("key") or {})
            if key.get("ingress_ifindex") is not None:
                interface_ifindexes.add(int(key.get("ingress_ifindex") or 0))
    return {
        "ingress_classifier_map": len(list(compiled.get("ingress_classifier_map") or [])),
        "core_lfib_map": len(list(compiled.get("core_lfib_map") or [])),
        "egress_handoff_map": len(list(compiled.get("egress_handoff_map") or [])),
        "interface_role_map": len({item for item in interface_ifindexes if item > 0}),
        "active_generation": 1,
    }


def _map_evidence(compiled: dict[str, Any], map_load: dict[str, Any]) -> dict[str, Any]:
    expected = _expected_entries(compiled)
    written = {
        "ingress_classifier_map": int(map_load.get("loaded_classifier_count") or 0),
        "core_lfib_map": int(map_load.get("loaded_core_lfib_count") or 0),
        "egress_handoff_map": int(map_load.get("loaded_egress_handoff_count") or 0),
        "interface_role_map": int(map_load.get("loaded_interface_role_count") or 0),
        "active_generation": int(map_load.get("active_generation_update_count") or 0),
    }
    return {
        map_name: {
            "expected_entries": expected.get(map_name),
            "written_entries": written.get(map_name),
            "state": mpls_map_state(map_name, expected_entries=expected.get(map_name)),
        }
        for map_name in RELOADED_MAPS
    }


def _counter_subset(counters: dict[str, Any]) -> dict[str, int]:
    names = (
        "classifier_hit",
        "label_push",
        "core_lfib_hit",
        "label_swap",
        "egress_handoff_hit",
        "label_pop",
        "miss",
        "drop",
        "drop_missing_label",
        "drop_missing_generation",
        "drop_bad_packet",
    )
    return {name: int(counters.get(name) or 0) for name in names}


def _counter_delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    keys = sorted({*before.keys(), *after.keys()})
    return {key: int(after.get(key) or 0) - int(before.get(key) or 0) for key in keys}


def _compose_network_residue_summary() -> dict[str, Any]:
    try:
        preflight = run_skyseed_preflight(ignore_pids=[os.getpid(), os.getppid()])
    except Exception as exc:  # pragma: no cover - host-tool dependent.
        return {
            "available": False,
            "error": str(exc),
            "compose_network_residue_count": None,
            "runtime_lock_count": None,
        }
    checks = dict(preflight.get("checks") or {})
    docker_state = dict(checks.get("docker_state") or {})
    networks = dict(docker_state.get("networks") or {})
    locks = list(dict(checks.get("runtime_lock_files") or {}).get("files") or [])
    return {
        "available": True,
        "compose_project_network_count": len(
            list(networks.get("compose_project_networks") or [])
        ),
        "named_skyseed_network_count": len(
            list(networks.get("named_skyseed_networks") or [])
        ),
        "compose_network_residue_count": len(
            list(networks.get("compose_project_networks") or [])
        )
        + len(list(networks.get("named_skyseed_networks") or [])),
        "runtime_lock_count": len(locks),
        "runtime_locks": locks,
    }


def _first_setup_failure(steps: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    for index, step in enumerate(steps):
        if not command_ok(step):
            return {"index": index, "step": dict(step)}
    return None


def _classify_map_failure(map_load: dict[str, Any]) -> str:
    first_failed = dict(map_load.get("first_failed_update") or {})
    analysis = dict(first_failed.get("failure_analysis") or {})
    conclusion = str(analysis.get("conclusion") or "")
    if conclusion == "bpf_mpls_map_schema_mismatch":
        return "BPF_SCHEMA_MISMATCH_REPRODUCED"
    if conclusion == "E2BIG_REQUIRES_NON_SCHEMA_INVESTIGATION":
        return "BPF_E2BIG_WITH_SCHEMA_MATCH"
    return conclusion or "BPF_MAP_RELOAD_FAILED"


def _cleanup_proof(
    *,
    attached: Sequence[str],
    names: dict[str, str],
    namespace_cleanup: dict[str, Any] | None,
) -> dict[str, Any]:
    residue = mpls_residue_check(attached)
    netns_list = run_command(["ip", "netns", "list"], timeout=4.0, privileged=True)
    remaining_namespaces = [
        name
        for name in names.values()
        if name and name in str(netns_list.get("stdout") or "")
    ]
    compose_residue = _compose_network_residue_summary()
    pinned = dict(residue.get("pinned_maps") or {})
    return {
        "direct_interfaces": len(list(residue.get("clsact_interface_hits") or [])),
        "direct_interface_hits": list(residue.get("clsact_interface_hits") or []),
        "bpf_pins": sum(1 for present in pinned.values() if present),
        "pinned_maps": pinned,
        "locks": compose_residue.get("runtime_lock_count"),
        "compose_network_residue": compose_residue.get("compose_network_residue_count"),
        "remaining_repro_namespaces": remaining_namespaces,
        "namespace_cleanup": namespace_cleanup,
        "raw_mpls_residue": residue,
        "compose_residue": compose_residue,
        "clean": (
            len(list(residue.get("clsact_interface_hits") or [])) == 0
            and not any(pinned.values())
            and not remaining_namespaces
            and compose_residue.get("runtime_lock_count") == 0
            and compose_residue.get("compose_network_residue_count") == 0
        ),
    }


def run_repro(*, output_path: Path) -> dict[str, Any]:
    started = time.perf_counter()
    suffix = f"resident-{int(time.time()) % 100000:x}"
    names: dict[str, str] = {}
    attached: list[str] = []
    namespace_cleanup: dict[str, Any] | None = None
    report: dict[str, Any] = {
        "schema_version": "skyseed-minimal-resident-bpf-map-reload-repro-v1",
        "s4_run": False,
        "rich_s4_acceptance": False,
        "canary_or_experimental_evidence_used": False,
        "linux_mpls_gz_lfib_fallback": False,
        "ok": False,
        "conclusion": "MINIMAL_REPRO_NOT_STARTED",
        "steps": {},
    }
    with tempfile.TemporaryDirectory(prefix="skyseed-resident-bpf-reload-") as tmp:
        object_path = Path(tmp) / "skyseed_tc_mpls_dataplane.o"
        try:
            initial_pin_cleanup = rollback_mpls(attached_interfaces=[], remove_maps=True)
            report["steps"]["initial_pin_cleanup"] = initial_pin_cleanup
            report["steps"]["initial_clean_state"] = mpls_residue_check([])
            report["baseline_map_schema_evidence"] = {
                map_name: mpls_map_state(map_name)
                for map_name in RELOADED_MAPS
            }
            if not bool(report["steps"]["initial_clean_state"].get("clean")):
                report["conclusion"] = "INITIAL_BPF_RESIDUE_NOT_CLEAN"
                report["reason"] = "initial BPF pins remained after cleanup"
                return report

            setup = setup_minimal_divergent_live_namespaces(suffix)
            report["steps"]["setup"] = setup
            names = dict(setup.get("names") or {})
            if not setup.get("ok"):
                report["conclusion"] = "MINIMAL_REPRO_SETUP_FAILED"
                report["first_failure"] = _first_setup_failure(
                    list(setup.get("steps") or [])
                )
                return report

            compiled_all = build_minimal_divergent_generation_maps(names)
            compiled_bootstrap = _filter_compiled_generation(compiled_all, 0)
            compiled_steady = _filter_compiled_generation(compiled_all, 1)
            report["compiled_counts"] = {
                "bootstrap": _expected_entries(compiled_bootstrap),
                "steady": _expected_entries(compiled_steady),
            }
            attach_plan = {
                str(interface_ref): [str(direction) for direction in directions]
                for interface_ref, directions in dict(
                    compiled_all.get("attach_plan") or {}
                ).items()
            }
            interfaces = list(attach_plan.keys())

            compile_result = compile_mpls_bpf(object_path=object_path)
            report["steps"]["compile"] = compile_result
            if not command_ok(compile_result):
                report["conclusion"] = "BPF_COMPILE_FAILED"
                report["first_failure"] = compile_result
                return report

            cleanup_before_attach = rollback_mpls(
                attached_interfaces=interfaces,
                remove_maps=True,
            )
            report["steps"]["cleanup_before_attach"] = cleanup_before_attach
            if not bool(cleanup_before_attach.get("rollback_completed")):
                report["conclusion"] = "PRE_ATTACH_CLEANUP_FAILED"
                return report

            attach_results: list[dict[str, Any]] = []
            for interface_ref in interfaces:
                attach_results.extend(
                    attach_mpls(
                        interface_ref,
                        object_path,
                        directions=attach_plan.get(interface_ref, ["ingress"]),
                        clsact_setup_mode="reset",
                    )
                )
                attached.append(interface_ref)
            report["steps"]["bootstrap_attach"] = attach_results
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
                report["conclusion"] = "BPF_BOOTSTRAP_ATTACH_FAILED"
                report["first_failure"] = _first_setup_failure(attach_results)
                return report

            bootstrap_load = load_mpls_maps(compiled_bootstrap)
            report["steps"]["bootstrap_map_load"] = bootstrap_load
            report["bootstrap_map_evidence"] = _map_evidence(
                compiled_bootstrap,
                bootstrap_load,
            )
            if not bootstrap_load.get("ok"):
                report["conclusion"] = _classify_map_failure(bootstrap_load)
                report["first_failure"] = bootstrap_load.get("first_failed_update")
                return report

            clsact_guard = {
                interface_ref: verify_mpls_clsact(interface_ref)
                for interface_ref in interfaces
            }
            report["steps"]["steady_resident_attach_guard"] = clsact_guard
            guard_ok = all(
                all(command_ok(item) for item in results)
                for results in clsact_guard.values()
            )
            if not guard_ok:
                report["conclusion"] = "BPF_RESIDENT_ATTACH_GUARD_FAILED"
                return report

            counters_before_validation = _counter_subset(
                dict(read_mpls_packet_counters().get("counters") or {})
            )
            active_before_steady = read_mpls_active_generation()
            steady_load = load_mpls_maps(compiled_steady)
            active_after_steady = read_mpls_active_generation()
            report["steps"]["steady_map_reload"] = steady_load
            report["steady_map_evidence"] = _map_evidence(compiled_steady, steady_load)
            report["active_generation_transition"] = {
                "before": active_before_steady,
                "after": active_after_steady,
                "loader_active_generation_flip_attempted": steady_load.get(
                    "active_generation_flip_attempted"
                ),
                "loader_active_generation_flip_bpftool_invoked": steady_load.get(
                    "active_generation_flip_bpftool_invoked"
                ),
                "active_generation_flip_skipped": steady_load.get(
                    "active_generation_flip_skipped"
                ),
                "active_generation_left_unchanged_by_loader": steady_load.get(
                    "active_generation_left_unchanged_by_loader"
                ),
            }
            if not steady_load.get("ok"):
                report["conclusion"] = _classify_map_failure(steady_load)
                report["first_failure"] = steady_load.get("first_failed_update")
                return report

            report["attach_lifecycle_actual"] = {
                "bootstrap_attach_command_count": len(attach_results),
                "steady_attach_command_count": 0,
                "steady_clsact_guard_count": sum(
                    len(results) for results in clsact_guard.values()
                ),
                "full_attach_fallback": False,
                "dirty_recovery_reset": False,
                "resident_attach_reused": True,
                "interfaces": interfaces,
            }
            report["fallback_evidence"] = {
                "linux_mpls_gz_lfib_fallback": False,
                "linux_gz_fallback_command_count": 0,
                "eligible_core_fallback_to_linux": 0,
                "canary_or_experimental_evidence_used": False,
            }

            report["steps"]["steady_generation_ping"] = run_command(
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
                    "10.80.4.2",
                ],
                timeout=8.0,
                privileged=True,
            )
            counters_after_validation = _counter_subset(
                dict(read_mpls_packet_counters().get("counters") or {})
            )
            report["validation"] = {
                "attempted": True,
                "steady_generation_ping_success": command_ok(
                    report["steps"]["steady_generation_ping"]
                ),
                "packet_counters_before": counters_before_validation,
                "packet_counters_after": counters_after_validation,
                "packet_counter_delta": _counter_delta(
                    counters_before_validation,
                    counters_after_validation,
                ),
            }
            delta = dict(report["validation"]["packet_counter_delta"])
            miss_drop_fallback_delta_zero = (
                int(delta.get("miss") or 0) == 0
                and int(delta.get("drop") or 0) == 0
                and int(delta.get("drop_missing_label") or 0) == 0
                and int(delta.get("drop_missing_generation") or 0) == 0
                and int(delta.get("drop_bad_packet") or 0) == 0
            )
            report["validation"]["miss_drop_fallback_delta_zero"] = (
                miss_drop_fallback_delta_zero
            )
            if (
                command_ok(report["steps"]["steady_generation_ping"])
                and miss_drop_fallback_delta_zero
            ):
                report["ok"] = True
                report["conclusion"] = "MINIMAL_RESIDENT_MAP_RELOAD_PASS"
                report["reason"] = ""
            else:
                report["conclusion"] = "MINIMAL_RESIDENT_MAP_RELOAD_VALIDATION_FAILED"
                report["reason"] = "steady generation validation failed"
            return report
        finally:
            if attached:
                report["steps"]["final_bpf_cleanup"] = rollback_mpls(
                    attached_interfaces=attached,
                    remove_maps=True,
                )
            if names:
                namespace_cleanup = cleanup_minimal_live_namespaces(names)
                report["steps"]["namespace_cleanup"] = namespace_cleanup
            report["cleanup_proof"] = _cleanup_proof(
                attached=attached,
                names=names,
                namespace_cleanup=namespace_cleanup,
            )
            report["wall_seconds"] = round(time.perf_counter() - started, 6)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(_json_safe(report), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("/tmp/skyseed_minimal_resident_bpf_map_reload_repro.json"),
    )
    args = parser.parse_args()
    report = run_repro(output_path=args.output)
    print(json.dumps(_json_safe(report), indent=2, sort_keys=True))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
