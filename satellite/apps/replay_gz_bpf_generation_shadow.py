#!/usr/bin/env python3
# encoding: utf-8

"""Replay real Skyseed G/Z BPF generations in the isolated tc-BPF shadow path."""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from satellite.apps.compile_gz_fec_to_bpf_generation import (  # noqa: E402
    compile_generation_map,
    load_artifacts,
)
from satellite.apps import bench_skyseed_bpf_gz_shadow as shadow  # noqa: E402


DEFAULT_RUN_DIR = (
    REPO_ROOT
    / "satellite"
    / "output"
    / "starlink_shell_experiments"
    / "gz-z6metadata-2h-gated-20260611-0824"
)
DEFAULT_JSON = REPO_ROOT / "satellite" / "baselines" / "skyseed_gz_bpf_shadow_replay_summary.json"
MIN_GENERATIONS_FOR_PROBES = 3


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def active_g_entries(generation: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        entry
        for entry in generation.get("fec_forwarding", [])
        if entry.get("active") and entry.get("fec_kind") == "G"
    ]


def active_gz_local_entries(generation: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        entry
        for entry in generation.get("fec_forwarding", [])
        if entry.get("active")
        and entry.get("fec_kind") == "GZ"
        and (entry.get("local_delivery_marker") or {}).get("value")
    ]


def inactive_entries(generation: dict[str, Any]) -> list[dict[str, Any]]:
    return [entry for entry in generation.get("fec_forwarding", []) if not entry.get("active")]


def choose_active_g(generation: dict[str, Any], *, prefer_gw1: bool) -> dict[str, Any]:
    entries = active_g_entries(generation)
    if not entries:
        raise ValueError(f"generation {generation['generation_id']} has no active G FEC")
    if prefer_gw1:
        preferred = [entry for entry in entries if entry.get("selected_gateway_id") == "gw1"]
    else:
        preferred = [entry for entry in entries if entry.get("selected_gateway_id") != "gw1"]
    return copy.deepcopy((preferred or entries)[0])


def choose_inactive_generation(generations: list[dict[str, Any]]) -> tuple[int, dict[str, Any]]:
    for index, generation in enumerate(generations[2:], start=2):
        entries = inactive_entries(generation)
        if entries:
            return index, copy.deepcopy(entries[0])
    for index, generation in enumerate(generations[1:], start=1):
        entries = inactive_entries(generation)
        if entries:
            return index, copy.deepcopy(entries[0])
    raise ValueError("selected generation window has no inactive tombstone FEC")


def choose_local_delivery(generation: dict[str, Any]) -> dict[str, Any]:
    entries = active_gz_local_entries(generation)
    if not entries:
        raise ValueError(f"generation {generation['generation_id']} has no local-delivery GZ FEC")
    return copy.deepcopy(entries[0])


def build_projection(compiled: dict[str, Any], generation_limit: int) -> dict[str, Any]:
    if generation_limit < MIN_GENERATIONS_FOR_PROBES:
        raise ValueError(
            f"--generation-limit must be >= {MIN_GENERATIONS_FOR_PROBES} for the required probes"
        )
    generations = list(compiled.get("generations", []))[:generation_limit]
    if len(generations) < generation_limit:
        raise ValueError(
            f"requested {generation_limit} generations, artifact only has {len(generations)}"
        )

    inactive_index, inactive_source = choose_inactive_generation(generations)
    route_entries: list[dict[str, Any]] = []
    for index, generation in enumerate(generations):
        generation_id = int(generation["generation_id"])
        if index == inactive_index:
            route_entries.append(
                {
                    "generation_id": generation_id,
                    "fec_slot": "FEC_G",
                    "bpf_fec": shadow.FEC_G,
                    "active": False,
                    "action": "drop",
                    "next_hop": "drop",
                    "source_fec": inactive_source,
                }
            )
            continue

        next_hop = "gw1" if index % 2 == 0 else "gw2"
        source = choose_active_g(generation, prefer_gw1=next_hop == "gw1")
        route_entries.append(
            {
                "generation_id": generation_id,
                "fec_slot": "FEC_G",
                "bpf_fec": shadow.FEC_G,
                "active": True,
                "action": "forward",
                "next_hop": next_hop,
                "source_fec": source,
            }
        )

    local_source = choose_local_delivery(generations[0])
    route_entries.append(
        {
            "generation_id": int(generations[0]["generation_id"]),
            "fec_slot": "FEC_Z",
            "bpf_fec": shadow.FEC_Z,
            "active": True,
            "action": "local",
            "next_hop": "local",
            "source_fec": local_source,
        }
    )

    generation_summaries = [
        {
            "generation_id": generation["generation_id"],
            "epoch_id": generation["epoch_id"],
            "plan_id": generation.get("plan_id"),
            "target_snapshot": generation.get("target_snapshot"),
            "runtime_epoch": generation.get("runtime_epoch"),
            "artifact_fec_count": generation.get("fec_count"),
            "artifact_active_fec_count": generation.get("active_fec_count"),
            "artifact_inactive_fec_count": generation.get("inactive_fec_count"),
        }
        for generation in generations
    ]
    return {
        "generation_summaries": generation_summaries,
        "route_entries": route_entries,
        "active_generation_id": int(generations[0]["generation_id"]),
        "future_generation_id": int(generations[1]["generation_id"]),
        "gw1_probe_generation_id": int(generations[0]["generation_id"]),
        "gw2_probe_generation_id": int(generations[1]["generation_id"]),
        "inactive_probe_generation_id": int(generations[inactive_index]["generation_id"]),
        "missing_generation_id": max(int(generation["generation_id"]) for generation in generations) + 1000,
        "loaded_generation_count": len(generations),
        "loaded_fec_count": len(route_entries),
        "artifact_fec_entry_count": sum(int(generation.get("fec_count") or 0) for generation in generations),
    }


def route_update(route: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    generation_id = int(route["generation_id"])
    bpf_fec = int(route["bpf_fec"])
    if route["action"] == "local":
        return shadow.update_route_map(
            generation_id,
            bpf_fec,
            active=1,
            action=shadow.ACTION_LOCAL,
            ifindex=0,
            next_hop=0,
        )

    next_hop = route["next_hop"]
    if next_hop == "gw2":
        ifindex = int(metadata["gw2_ifindex"]["value"])
        dst_mac = metadata["gw2_dst_mac"]["mac_bytes"]
        src_mac = metadata["gw2_src_mac"]["mac_bytes"]
        next_hop_value = shadow.NEXT_HOP_GW2
    else:
        ifindex = int(metadata["gw1_ifindex"]["value"])
        dst_mac = metadata["gw1_dst_mac"]["mac_bytes"]
        src_mac = metadata["gw1_src_mac"]["mac_bytes"]
        next_hop_value = shadow.NEXT_HOP_GW1

    return shadow.update_route_map(
        generation_id,
        bpf_fec,
        active=1 if route["active"] else 0,
        action=shadow.ACTION_FORWARD,
        ifindex=ifindex,
        next_hop=next_hop_value,
        dst_mac=dst_mac,
        src_mac=src_mac,
    )


def initialize_replay_maps(metadata: dict[str, Any], projection: dict[str, Any]) -> dict[str, Any]:
    if not shadow.metadata_ok(metadata):
        return {"ok": False, "reason": "forwarding_metadata_unavailable", "updates": []}

    updates = [
        shadow.update_u32_map("active_generation", 0, projection["active_generation_id"]),
        shadow.update_u32_map("future_generation", 0, projection["future_generation_id"]),
    ]
    for generation in projection["generation_summaries"]:
        updates.append(shadow.update_u32_map("generation_status", int(generation["generation_id"]), 1))
    for route in projection["route_entries"]:
        updates.append(route_update(route, metadata))
    return {"ok": all(update["returncode"] == 0 for update in updates), "updates": updates}


def run_probe(
    names: dict[str, str],
    *,
    name: str,
    generation: int,
    destination: str,
    count: int,
    behavior: str,
    expected_counter: str,
    expected_rx: str,
) -> dict[str, Any]:
    set_gen = shadow.set_generation(generation)
    counters_before = shadow.read_counters()["counters"]
    rx_before = shadow.interface_rx_snapshot(names)
    ping = shadow.ping_ip(names, destination, count=count)
    rx_after = shadow.interface_rx_snapshot(names)
    counters_after = shadow.read_counters()["counters"]
    delta = shadow.counter_delta(counters_before, counters_after)
    rx_delta = shadow.interface_rx_delta(rx_before, rx_after)

    observed_ping = "pass" if shadow.ping_passed(ping) else "drop"
    counter_ok = delta.get(expected_counter, 0) > 0
    if expected_rx == "gw1":
        rx_ok = (rx_delta.get("gw1_rx_packets") or 0) > 0 and (rx_delta.get("gw2_rx_packets") or 0) == 0
    elif expected_rx == "gw2":
        rx_ok = (rx_delta.get("gw2_rx_packets") or 0) > 0 and (rx_delta.get("gw1_rx_packets") or 0) == 0
    else:
        rx_ok = (rx_delta.get("gw1_rx_packets") or 0) == 0 and (rx_delta.get("gw2_rx_packets") or 0) == 0

    if behavior == "drop":
        behavior_ok = observed_ping == "drop" and rx_ok
    elif behavior == "local":
        behavior_ok = observed_ping == "pass" and rx_ok
    else:
        behavior_ok = rx_ok

    return {
        "name": name,
        "generation": generation,
        "destination": destination,
        "behavior": behavior,
        "observed_ping": observed_ping,
        "expected_counter": expected_counter,
        "counter_delta": delta,
        "counter_ok": counter_ok,
        "expected_rx": expected_rx,
        "rx_delta": rx_delta,
        "rx_ok": rx_ok,
        "behavior_ok": behavior_ok,
        "ok": set_gen["returncode"] == 0 and counter_ok and behavior_ok,
        "set_generation_returncode": set_gen["returncode"],
        "ping": {
            "returncode": ping.get("returncode"),
            "parsed": ping.get("parsed"),
            "stderr": ping.get("stderr"),
        },
    }


def run_replay_suite(
    names: dict[str, str],
    object_path: Path,
    metadata: dict[str, Any],
    projection: dict[str, Any],
    ping_count: int,
) -> dict[str, Any]:
    attach = shadow.attach_section(names, object_path, "classifier/redirect")
    init = initialize_replay_maps(metadata, projection)
    probes: list[dict[str, Any]] = []
    if shadow.attach_ok(attach) and init["ok"]:
        probes.extend(
            [
                run_probe(
                    names,
                    name="future_preloaded_active_gen_still_gw1",
                    generation=projection["gw1_probe_generation_id"],
                    destination=shadow.FEC_G_IP,
                    count=ping_count,
                    behavior="next_hop",
                    expected_counter="future_generation_not_used",
                    expected_rx="gw1",
                ),
                run_probe(
                    names,
                    name="activated_generation_gw2",
                    generation=projection["gw2_probe_generation_id"],
                    destination=shadow.FEC_G_IP,
                    count=ping_count,
                    behavior="next_hop",
                    expected_counter="next_hop_gw2",
                    expected_rx="gw2",
                ),
                run_probe(
                    names,
                    name="inactive_tombstone_drop",
                    generation=projection["inactive_probe_generation_id"],
                    destination=shadow.FEC_G_IP,
                    count=ping_count,
                    behavior="drop",
                    expected_counter="drop_inactive",
                    expected_rx="none",
                ),
                run_probe(
                    names,
                    name="missing_generation_drop",
                    generation=projection["missing_generation_id"],
                    destination=shadow.FEC_G_IP,
                    count=ping_count,
                    behavior="drop",
                    expected_counter="drop_missing_generation",
                    expected_rx="none",
                ),
                run_probe(
                    names,
                    name="missing_fec_drop",
                    generation=projection["gw1_probe_generation_id"],
                    destination=shadow.MISSING_FEC_IP,
                    count=ping_count,
                    behavior="drop",
                    expected_counter="drop_missing_fec",
                    expected_rx="none",
                ),
                run_probe(
                    names,
                    name="local_delivery_pass",
                    generation=projection["gw1_probe_generation_id"],
                    destination=shadow.FEC_Z_IP,
                    count=ping_count,
                    behavior="local",
                    expected_counter="local_delivery",
                    expected_rx="none",
                ),
            ]
        )

    probe_by_name = {probe["name"]: probe for probe in probes}
    generation_switch_ok = bool(
        (probe_by_name.get("future_preloaded_active_gen_still_gw1") or {}).get("ok")
        and (probe_by_name.get("activated_generation_gw2") or {}).get("ok")
    )
    fail_closed_ok = all(
        bool((probe_by_name.get(name) or {}).get("ok"))
        for name in ["inactive_tombstone_drop", "missing_generation_drop", "missing_fec_drop"]
    )
    local_delivery_ok = bool((probe_by_name.get("local_delivery_pass") or {}).get("ok"))
    return {
        "attach_ok": shadow.attach_ok(attach),
        "attach_returncode": attach[-1]["returncode"] if attach else None,
        "attach_stderr": attach[-1].get("stderr") if attach else None,
        "initialize_maps": {
            "ok": init["ok"],
            "reason": init.get("reason"),
            "update_returncodes": [update["returncode"] for update in init.get("updates", [])],
        },
        "probes": probes,
        "counters": shadow.read_counters() if shadow.attach_ok(attach) else None,
        "generation_switch_ok": generation_switch_ok,
        "fail_closed_ok": fail_closed_ok,
        "local_delivery_ok": local_delivery_ok,
        "ok": generation_switch_ok and fail_closed_ok and local_delivery_ok,
    }


def run_isolated_replay(source: Path, projection: dict[str, Any], ping_count: int) -> dict[str, Any]:
    suffix = str(os.getpid())[-5:]
    names = {
        "ns_ut": f"ssgzrut{suffix}",
        "ns_fwd": f"ssgzrfw{suffix}",
        "ns_gw1": f"ssgzrg1{suffix}",
        "ns_gw2": f"ssgzrg2{suffix}",
        "if_ut": f"sgrut{suffix}",
        "if_fwd_ut": f"sgrfu{suffix}",
        "if_fwd_gw1": f"sgrf1{suffix}",
        "if_gw1": f"sgrg1{suffix}",
        "if_fwd_gw2": f"sgrf2{suffix}",
        "if_gw2": f"sgrg2{suffix}",
    }
    result: dict[str, Any] = {"names": names}
    shadow.cleanup(names)
    with tempfile.TemporaryDirectory(prefix="skyseed-gz-bpf-shadow-replay-") as tmp:
        object_path = Path(tmp) / "skyseed_tc_gz_shadow.o"
        result["compile"] = shadow.compile_bpf(source, object_path)
        if result["compile"]["returncode"] != 0:
            result["ok"] = False
            result["reason"] = "compile_failed"
            result["cleanup"] = shadow.cleanup(names)
            return result
        try:
            setup = shadow.setup_netns(names)
            failed_setup = [step for step in setup if step["returncode"] != 0]
            result["setup_ok"] = not failed_setup
            result["setup_failed_returncodes"] = [step["returncode"] for step in failed_setup]
            if failed_setup:
                result["ok"] = False
                result["reason"] = "setup_failed"
                return result
            metadata = shadow.collect_forwarding_metadata(names)
            result["forwarding_metadata"] = {
                key: {inner_key: inner_value for inner_key, inner_value in value.items() if inner_key != "mac_bytes"}
                for key, value in metadata.items()
            }
            result["replay"] = run_replay_suite(names, object_path, metadata, projection, ping_count)
            result["ok"] = bool(result["replay"]["ok"])
            return result
        finally:
            result["cleanup"] = shadow.cleanup(names)


def packet_counter_summary(experiment: dict[str, Any]) -> dict[str, int]:
    counters = ((((experiment.get("replay") or {}).get("counters") or {}).get("counters")) or {})
    return {name: int(counters.get(name, 0)) for name in shadow.COUNTERS.values()}


def compact_experiment(experiment: dict[str, Any] | None) -> dict[str, Any] | None:
    if experiment is None:
        return None
    replay = experiment.get("replay") or {}
    probes = []
    for probe in replay.get("probes") or []:
        probes.append(
            {
                "name": probe["name"],
                "generation": probe["generation"],
                "destination": probe["destination"],
                "behavior": probe["behavior"],
                "observed_ping": probe["observed_ping"],
                "expected_counter": probe["expected_counter"],
                "counter_delta": probe["counter_delta"],
                "counter_ok": probe["counter_ok"],
                "expected_rx": probe["expected_rx"],
                "rx_delta": probe["rx_delta"],
                "rx_ok": probe["rx_ok"],
                "behavior_ok": probe["behavior_ok"],
                "ok": probe["ok"],
            }
        )
    return {
        "ok": bool(experiment.get("ok")),
        "reason": experiment.get("reason"),
        "compile_ok": (experiment.get("compile") or {}).get("returncode") == 0,
        "setup_ok": bool(experiment.get("setup_ok")),
        "replay": {
            "attach_ok": bool(replay.get("attach_ok")),
            "initialize_maps_ok": bool((replay.get("initialize_maps") or {}).get("ok")),
            "probe_count": len(probes),
            "probes": probes,
            "generation_switch_ok": bool(replay.get("generation_switch_ok")),
            "fail_closed_ok": bool(replay.get("fail_closed_ok")),
            "local_delivery_ok": bool(replay.get("local_delivery_ok")),
        },
        "cleanup": {
            "residue": (experiment.get("cleanup") or {}).get("residue"),
        },
    }


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    artifacts = load_artifacts(args.run_dir)
    compiled = compile_generation_map(
        artifacts["gz_fec_plan"],
        artifacts["gz_update_diff_summary"],
        artifacts["temporal_plan"],
        active_generation=0,
    )
    projection = build_projection(compiled, args.generation_limit)
    environment = shadow.collect_environment()
    blockers = shadow.attach_blockers(environment)
    report: dict[str, Any] = {
        "schema_version": 1,
        "goal": "Skyseed Goal 10 BPF shadow compiler plus isolated packet-path replay",
        "generated_at": "not_recorded_for_reproducible_baseline",
        "input_artifacts": {
            "source_root": artifacts["source_root"],
            "gz_fec_plan_path": artifacts["gz_fec_plan_path"],
            "gz_update_diff_summary_path": artifacts["gz_update_diff_summary_path"],
            "temporal_plan_path": artifacts["temporal_plan_path"],
        },
        "scope": {
            "shadow_only": True,
            "canonical_runtime_changed": False,
            "gz_semantics_changed": False,
            "real_dataplane_touched": False,
            "long_real_runtime": False,
            "parameter_epoch_or_qdisc_timeline": False,
        },
        "generation_cadence": {
            "fixed_10s_assumed": False,
            "source": "artifact generation sequence and optional RuntimeEpoch activation annotations",
            "selected_generations": projection["generation_summaries"],
        },
        "projection": projection,
        "blocked": bool(blockers),
        "blockers": blockers,
    }
    if blockers:
        empty_names = {"ns_ut": "", "ns_fwd": "", "ns_gw1": "", "ns_gw2": ""}
        report["experiment"] = None
        report["residue"] = shadow.residue_check(empty_names)
        bpf_counters = {name: 0 for name in shadow.COUNTERS.values()}
        report["results"] = {
            "attach_attempted": False,
            "generation_switch_ok": False,
            "fail_closed_ok": False,
            "local_delivery_ok": False,
            "ok": False,
        }
    else:
        experiment = run_isolated_replay(args.bpf_source, projection, args.ping_count)
        replay = experiment.get("replay") or {}
        report["residue"] = (experiment.get("cleanup") or {}).get("residue")
        bpf_counters = packet_counter_summary(experiment)
        report["results"] = {
            "attach_attempted": True,
            "attach_ok": bool(replay.get("attach_ok")),
            "generation_switch_ok": bool(replay.get("generation_switch_ok")),
            "fail_closed_ok": bool(replay.get("fail_closed_ok")),
            "local_delivery_ok": bool(replay.get("local_delivery_ok")),
            "ok": bool(experiment.get("ok")),
            "reason": experiment.get("reason"),
        }
        report["experiment"] = compact_experiment(experiment)

    report["counter_summary"] = {
        "loaded_generation_count": int(projection["loaded_generation_count"]),
        "loaded_fec_count": int(projection["loaded_fec_count"]),
        "replayed_generation_count": int(projection["loaded_generation_count"]),
        "artifact_fec_entry_count": int(projection["artifact_fec_entry_count"]),
        **bpf_counters,
        "next_hop_counters": {
            "gw1": int(bpf_counters.get("next_hop_gw1", 0)),
            "gw2": int(bpf_counters.get("next_hop_gw2", 0)),
        },
        "redirect_pass_counters": {
            "redirect_pass": int(bpf_counters.get("redirect_pass", 0)),
            "local_delivery": int(bpf_counters.get("local_delivery", 0)),
            "shadow_pass": int(bpf_counters.get("shadow_pass", 0)),
        },
    }
    return report


def render_text(report: dict[str, Any]) -> str:
    counters = report["counter_summary"]
    results = report["results"]
    residue = report.get("residue") or {}
    lines = [
        "Skyseed G/Z BPF shadow replay",
        f"input={report['input_artifacts']['source_root']}",
        f"blocked={report['blocked']} blockers={report['blockers']}",
        f"loaded_generations={counters['loaded_generation_count']}",
        f"loaded_fecs={counters['loaded_fec_count']} artifact_fecs={counters['artifact_fec_entry_count']}",
        f"generation_switch_ok={results['generation_switch_ok']}",
        f"fail_closed_ok={results['fail_closed_ok']}",
        f"local_delivery_ok={results['local_delivery_ok']}",
        f"residue_clean={residue.get('clean')}",
        f"fixed_10s_assumed={report['generation_cadence']['fixed_10s_assumed']}",
        "counters:",
    ]
    for key in [
        "future_generation_not_used",
        "drop_missing_generation",
        "drop_missing_fec",
        "drop_inactive",
        "redirect_pass",
        "local_delivery",
        "next_hop_gw1",
        "next_hop_gw2",
    ]:
        lines.append(f"  {key}={counters.get(key)}")
    replay = ((report.get("experiment") or {}).get("replay") or {})
    probes = replay.get("probes") or []
    if probes:
        lines.append("probes:")
        for probe in probes:
            lines.append(
                "  "
                + f"{probe['name']}: gen={probe['generation']} behavior={probe['behavior']} "
                + f"counter_ok={probe['counter_ok']} rx_ok={probe['rx_ok']} ok={probe['ok']}"
            )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    parser.add_argument("--generation-limit", type=int, default=3)
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--json-out", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--bpf-source", type=Path, default=shadow.DEFAULT_SOURCE)
    parser.add_argument("--ping-count", type=int, default=2)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_report(args)
    if args.json_out:
        write_json(args.json_out, report)
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_text(report))
        if args.json_out:
            print(f"wrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
