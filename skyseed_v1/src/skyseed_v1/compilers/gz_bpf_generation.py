#!/usr/bin/env python3
# encoding: utf-8

"""Compile Skyseed G/Z FEC artifacts into a BPF-style generation map JSON."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from skyseed_v1.operations.paths import PROJECT_OUTPUT_ROOT

DEFAULT_RUN_DIR = PROJECT_OUTPUT_ROOT / "offline_compilers" / "gz_bpf_generation" / "input"
DEFAULT_JSON = PROJECT_OUTPUT_ROOT / "offline_compilers" / "gz_bpf_generation" / "summary.json"

COUNTER_LABELS = {
    "generation_hit": "generation_hit",
    "fec_hit": "fec_hit",
    "redirect_pass": "redirect_pass",
    "drop_missing_generation": "drop_missing_generation",
    "drop_missing_fec": "drop_missing_fec",
    "drop_inactive": "drop_inactive",
    "future_generation_not_used": "future_generation_not_used",
    "local_delivery": "local_delivery",
}

LINUX_MPLS_LEAKAGE_KEYS = {
    "forward_labels",
    "reverse_labels",
    "estimated_lfib_route_operation_items",
}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def stable_u32(value: str) -> int:
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], byteorder="little", signed=False)


def load_artifacts(input_path: Path) -> dict[str, Any]:
    if input_path.is_file():
        return {
            "source_root": str(input_path),
            "gz_fec_plan_path": str(input_path),
            "gz_update_diff_summary_path": None,
            "temporal_plan_path": None,
            "gz_fec_plan": read_json(input_path),
            "gz_update_diff_summary": None,
            "temporal_plan": None,
        }

    candidates = [
        input_path / "gz_fec_plan.json",
        input_path / "config" / "gz_fec_plan.json",
    ]
    plan_path = next((path for path in candidates if path.exists()), None)
    if plan_path is None:
        raise FileNotFoundError(f"could not find gz_fec_plan.json under {input_path}")

    diff_candidates = [
        input_path / "gz_update_diff_summary.json",
        input_path / "config" / "gz_update_diff_summary.json",
    ]
    diff_path = next((path for path in diff_candidates if path.exists()), None)
    temporal_path = input_path / "temporal_plan.json"
    return {
        "source_root": str(input_path),
        "gz_fec_plan_path": str(plan_path),
        "gz_update_diff_summary_path": str(diff_path) if diff_path else None,
        "temporal_plan_path": str(temporal_path) if temporal_path.exists() else None,
        "gz_fec_plan": read_json(plan_path),
        "gz_update_diff_summary": read_json(diff_path) if diff_path else None,
        "temporal_plan": read_json(temporal_path) if temporal_path.exists() else None,
    }


def runtime_epoch_index(temporal_plan: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not temporal_plan:
        return {}
    index: dict[str, dict[str, Any]] = {}
    for epoch in temporal_plan.get("runtime_epochs", []) or []:
        if not isinstance(epoch, dict):
            continue
        slice_id = epoch.get("slice_id")
        if isinstance(slice_id, str):
            index[slice_id] = epoch
    return index


def infer_fec_kind(fec: dict[str, Any]) -> str:
    group_kind = str(fec.get("group_kind") or "")
    if group_kind == "g_forward_fec":
        return "G"
    if group_kind == "gz_return_fec":
        return "GZ"
    fec_id = str(fec.get("fec_id") or "")
    if fec_id.startswith("gw"):
        return "GZ"
    return "G"


def local_delivery_marker(fec: dict[str, Any], kind: str) -> dict[str, Any]:
    satellite_path = fec.get("satellite_path") or []
    terminal = satellite_path[-1] if satellite_path else None
    responsible = responsible_satellite_id(fec, kind)
    if kind != "GZ":
        return {
            "available": False,
            "value": False,
            "basis": "G gateway-exit FEC is not a zone local-delivery marker",
        }
    return {
        "available": bool(responsible and terminal),
        "value": bool(responsible and terminal and responsible == terminal),
        "basis": "GZ zone_serving_satellite_id equals satellite_path terminal when available",
    }


def responsible_satellite_id(fec: dict[str, Any], kind: str) -> str | None:
    if kind == "GZ":
        return (
            fec.get("zone_serving_satellite_id")
            or fec.get("pop_point")
            or fec.get("selected_gateway_satellite_id")
        )
    return fec.get("serving_satellite_id") or fec.get("selected_gateway_satellite_id")


def next_hop_for_fec(fec: dict[str, Any], kind: str) -> dict[str, Any]:
    if kind == "GZ":
        edges = fec.get("reverse_link_ids") or []
        return {
            "logical_node": responsible_satellite_id(fec, kind),
            "logical_edge": edges[0] if edges else None,
            "basis": "reverse_link_ids and zone responsible satellite",
        }
    edges = fec.get("forward_link_ids") or []
    return {
        "logical_node": fec.get("selected_gateway_id"),
        "logical_edge": edges[0] if edges else None,
        "basis": "forward_link_ids and selected gateway",
    }


def expected_counter_labels(fec: dict[str, Any], kind: str, *, active: bool) -> list[str]:
    if not active:
        return [COUNTER_LABELS["drop_inactive"]]
    labels = [COUNTER_LABELS["generation_hit"], COUNTER_LABELS["fec_hit"]]
    marker = local_delivery_marker(fec, kind)
    if marker["value"]:
        labels.append(COUNTER_LABELS["local_delivery"])
    else:
        labels.append(COUNTER_LABELS["redirect_pass"])
    if kind == "G":
        labels.append("next_hop_gateway")
    else:
        labels.append("next_hop_responsible_satellite")
    return labels


def compile_fec_entry(
    fec: dict[str, Any],
    *,
    generation_id: int,
    epoch_id: str,
    active: bool,
    source: str,
) -> dict[str, Any]:
    kind = infer_fec_kind(fec)
    fec_id = str(fec.get("fec_id") or "")
    return {
        "generation_id": generation_id,
        "epoch_id": epoch_id,
        "fec_id": fec_id,
        "fec_key_u32": stable_u32(fec_id),
        "fec_kind": kind,
        "selected_gateway_id": fec.get("selected_gateway_id"),
        "zone_id": fec.get("zone_id"),
        "responsible_satellite_id": responsible_satellite_id(fec, kind),
        "next_hop": next_hop_for_fec(fec, kind) if active else None,
        "active": active,
        "action": "forward" if active else "drop",
        "local_delivery_marker": local_delivery_marker(fec, kind),
        "expected_counter_labels": expected_counter_labels(fec, kind, active=active),
        "source": source,
    }


def build_prior_fec_index(plans: list[dict[str, Any]], generation_index: int) -> dict[str, dict[str, Any]]:
    prior: dict[str, dict[str, Any]] = {}
    for plan in plans[:generation_index]:
        for fec in list(plan.get("forward_fecs", []) or []) + list(plan.get("return_fecs", []) or []):
            fec_id = fec.get("fec_id")
            if isinstance(fec_id, str):
                prior[fec_id] = fec
    return prior


def compile_generation(
    plan: dict[str, Any],
    *,
    generation_id: int,
    diff: dict[str, Any] | None,
    plans: list[dict[str, Any]],
    runtime_by_slice: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    target_snapshot = plan.get("target_snapshot") or {}
    slice_id = target_snapshot.get("slice_id")
    runtime_epoch = runtime_by_slice.get(slice_id) if isinstance(slice_id, str) else None
    epoch_id = str(
        (runtime_epoch or {}).get("epoch_id")
        or plan.get("plan_id")
        or slice_id
        or f"generation-{generation_id}"
    )
    entries = [
        compile_fec_entry(
            fec,
            generation_id=generation_id,
            epoch_id=epoch_id,
            active=True,
            source="gz_fec_plan",
        )
        for fec in list(plan.get("forward_fecs", []) or []) + list(plan.get("return_fecs", []) or [])
    ]

    prior = build_prior_fec_index(plans, generation_id)
    for removed_fec_id in (diff or {}).get("removed_fec_groups", []) or []:
        prior_fec = prior.get(removed_fec_id)
        if prior_fec is None:
            continue
        tombstone = compile_fec_entry(
            copy.deepcopy(prior_fec),
            generation_id=generation_id,
            epoch_id=epoch_id,
            active=False,
            source="gz_update_diff_summary.removed_fec_groups",
        )
        entries.append(tombstone)

    active_entries = [entry for entry in entries if entry["active"]]
    inactive_entries = [entry for entry in entries if not entry["active"]]
    return {
        "generation_id": generation_id,
        "epoch_id": epoch_id,
        "plan_id": plan.get("plan_id"),
        "target_snapshot": target_snapshot,
        "runtime_epoch": {
            "matched": runtime_epoch is not None,
            "epoch_id": (runtime_epoch or {}).get("epoch_id"),
            "activation_relative_time_seconds": (runtime_epoch or {}).get(
                "activation_relative_time_seconds"
            ),
            "activation_time_utc": (runtime_epoch or {}).get("activation_time_utc"),
        },
        "installed": True,
        "active": False,
        "fec_count": len(entries),
        "active_fec_count": len(active_entries),
        "inactive_fec_count": len(inactive_entries),
        "fec_forwarding": entries,
    }


def collect_linux_mpls_leakage(plan_sequence: dict[str, Any], diff_sequence: dict[str, Any] | None) -> dict[str, Any]:
    counts = {key: 0 for key in sorted(LINUX_MPLS_LEAKAGE_KEYS)}

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in counts:
                    counts[key] += 1
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(plan_sequence)
    if diff_sequence:
        walk(diff_sequence)
    present = {key: count for key, count in counts.items() if count}
    return {
        "found": bool(present),
        "field_counts": present,
        "ignored_by_compiler": sorted(present),
    }


def lookup_decision(compiled: dict[str, Any], generation_id: int, fec_id: str) -> dict[str, Any]:
    generations = {generation["generation_id"]: generation for generation in compiled["generations"]}
    generation = generations.get(generation_id)
    if generation is None or not generation.get("installed"):
        return {
            "decision": "drop",
            "reason": "missing_generation",
            "expected_counter_labels": [COUNTER_LABELS["drop_missing_generation"]],
        }
    entries = {
        entry["fec_id"]: entry
        for entry in generation.get("fec_forwarding", [])
        if isinstance(entry.get("fec_id"), str)
    }
    entry = entries.get(fec_id)
    if entry is None:
        return {
            "decision": "drop",
            "reason": "missing_fec",
            "expected_counter_labels": [COUNTER_LABELS["drop_missing_fec"]],
        }
    if not entry.get("active"):
        return {
            "decision": "drop",
            "reason": "inactive_fec",
            "expected_counter_labels": [COUNTER_LABELS["drop_inactive"]],
        }
    return {
        "decision": "pass",
        "reason": "active_fec",
        "expected_counter_labels": entry.get("expected_counter_labels", []),
        "next_hop": entry.get("next_hop"),
    }


def active_lookup_decision(compiled: dict[str, Any], fec_id: str) -> dict[str, Any]:
    active_generation = int(compiled["bpf_maps"]["active_generation"]["value"])
    return lookup_decision(compiled, active_generation, fec_id)


def build_self_checks(compiled: dict[str, Any]) -> dict[str, Any]:
    active_generation = int(compiled["bpf_maps"]["active_generation"]["value"])
    generation_ids = [generation["generation_id"] for generation in compiled["generations"]]
    max_generation = max(generation_ids) if generation_ids else 0
    missing_generation = lookup_decision(compiled, max_generation + 1000, "__missing__")
    missing_fec = lookup_decision(compiled, active_generation, "__missing_fec__")

    inactive_probe = None
    for generation in compiled["generations"]:
        for entry in generation.get("fec_forwarding", []):
            if not entry.get("active"):
                inactive_probe = lookup_decision(compiled, generation["generation_id"], entry["fec_id"])
                inactive_probe["generation_id"] = generation["generation_id"]
                inactive_probe["fec_id"] = entry["fec_id"]
                break
        if inactive_probe:
            break

    future_probe = None
    for generation in compiled["generations"]:
        if generation["generation_id"] <= active_generation:
            continue
        active_entries = [
            entry for entry in generation.get("fec_forwarding", []) if entry.get("active")
        ]
        for candidate in active_entries:
            active_decision = active_lookup_decision(compiled, candidate["fec_id"])
            future_decision = lookup_decision(compiled, generation["generation_id"], candidate["fec_id"])
            candidate_probe = {
                "future_generation_id": generation["generation_id"],
                "fec_id": candidate["fec_id"],
                "active_generation_id": active_generation,
                "active_generation_decision": active_decision,
                "future_generation_decision": future_decision,
                "ok": active_generation != generation["generation_id"],
                "expected_counter_labels": [COUNTER_LABELS["future_generation_not_used"]],
            }
            future_probe = future_probe or candidate_probe
            if (
                active_decision.get("reason") != "active_fec"
                or active_decision.get("next_hop") != future_decision.get("next_hop")
            ):
                future_probe = candidate_probe
                break
        if future_probe:
            break

    return {
        "missing_generation_fail_closed": {
            "ok": missing_generation["reason"] == "missing_generation",
            "probe": missing_generation,
        },
        "missing_fec_fail_closed": {
            "ok": missing_fec["reason"] == "missing_fec",
            "probe": missing_fec,
        },
        "inactive_fec_fail_closed": {
            "ok": bool(inactive_probe and inactive_probe["reason"] == "inactive_fec"),
            "probe": inactive_probe,
        },
        "future_generation_not_active": {
            "ok": bool(future_probe and future_probe["ok"]),
            "probe": future_probe,
        },
    }


def compile_generation_map(
    plan_sequence: dict[str, Any],
    diff_sequence: dict[str, Any] | None = None,
    temporal_plan: dict[str, Any] | None = None,
    *,
    active_generation: int = 0,
) -> dict[str, Any]:
    plans = list(plan_sequence.get("plans", []) or [])
    diffs = list((diff_sequence or {}).get("diffs", []) or [])
    runtime_by_slice = runtime_epoch_index(temporal_plan)
    generations = [
        compile_generation(
            plan,
            generation_id=index,
            diff=diffs[index] if index < len(diffs) else None,
            plans=plans,
            runtime_by_slice=runtime_by_slice,
        )
        for index, plan in enumerate(plans)
    ]
    for generation in generations:
        generation["active"] = generation["generation_id"] == active_generation

    generation_status = [
        {
            "generation_id": generation["generation_id"],
            "epoch_id": generation["epoch_id"],
            "installed": generation["installed"],
            "active": generation["active"],
        }
        for generation in generations
    ]
    fec_entries = [
        entry for generation in generations for entry in generation.get("fec_forwarding", [])
    ]
    active_entries = [entry for entry in fec_entries if entry.get("active")]
    inactive_entries = [entry for entry in fec_entries if not entry.get("active")]
    fec_kind_counts: dict[str, int] = {}
    for entry in active_entries:
        fec_kind_counts[entry["fec_kind"]] = fec_kind_counts.get(entry["fec_kind"], 0) + 1

    compiled = {
        "schema_version": 1,
        "goal": "Skyseed Goal 9 G/Z FEC intent to BPF generation map compiler",
        "generated_at": "not_recorded_for_deterministic_baseline",
        "compiler": "skyseed_v1.compilers.gz_bpf_generation",
        "scope": {
            "shadow_only": True,
            "canonical_runtime_changed": False,
            "gz_semantics_changed": False,
            "real_dataplane_touched": False,
            "parameter_epoch_or_qdisc_timeline": False,
        },
        "bpf_shadow_model": {
            "active_generation": "array[0] -> u32 generation",
            "generation_status": "array[generation] -> installed/active metadata",
            "fec_forwarding": "hash[(generation, fec_key_u32)] -> forwarding decision",
            "packet_counters": sorted(COUNTER_LABELS.values()),
        },
        "bpf_maps": {
            "active_generation": {
                "slot": 0,
                "value": active_generation,
                "epoch_id": generations[active_generation]["epoch_id"] if generations else None,
            },
            "generation_status": generation_status,
            "fec_forwarding": fec_entries,
        },
        "generations": generations,
        "summary": {
            "generation_count": len(generations),
            "active_generation_id": active_generation,
            "active_fec_count": len(active_entries),
            "inactive_fec_tombstone_count": len(inactive_entries),
            "fec_entry_count": len(fec_entries),
            "fec_kind_counts": fec_kind_counts,
            "runtime_epoch_matched_generation_count": sum(
                1 for generation in generations if generation["runtime_epoch"]["matched"]
            ),
        },
        "linux_mpls_label_leakage": collect_linux_mpls_leakage(plan_sequence, diff_sequence),
    }
    compiled["self_checks"] = build_self_checks(compiled)
    return compiled


def compact_for_baseline(compiled: dict[str, Any], artifacts: dict[str, Any]) -> dict[str, Any]:
    entries = compiled["bpf_maps"]["fec_forwarding"]
    samples: dict[str, Any] = {}
    for name, predicate in [
        ("active_g", lambda entry: entry.get("active") and entry.get("fec_kind") == "G"),
        ("active_gz", lambda entry: entry.get("active") and entry.get("fec_kind") == "GZ"),
        ("inactive_tombstone", lambda entry: not entry.get("active")),
    ]:
        sample = next((entry for entry in entries if predicate(entry)), None)
        samples[name] = sample

    compact = {
        "schema_version": compiled["schema_version"],
        "goal": compiled["goal"],
        "generated_at": compiled["generated_at"],
        "compiler": compiled["compiler"],
        "input_artifacts": {
            "source_root": artifacts["source_root"],
            "gz_fec_plan_path": artifacts["gz_fec_plan_path"],
            "gz_update_diff_summary_path": artifacts["gz_update_diff_summary_path"],
            "temporal_plan_path": artifacts["temporal_plan_path"],
        },
        "scope": compiled["scope"],
        "bpf_shadow_model": compiled["bpf_shadow_model"],
        "bpf_maps": {
            "active_generation": compiled["bpf_maps"]["active_generation"],
            "generation_status": compiled["bpf_maps"]["generation_status"],
            "fec_forwarding_entry_count": len(entries),
            "fec_forwarding_samples": samples,
        },
        "generation_summaries": [
            {
                "generation_id": generation["generation_id"],
                "epoch_id": generation["epoch_id"],
                "plan_id": generation["plan_id"],
                "target_snapshot": generation["target_snapshot"],
                "runtime_epoch": generation["runtime_epoch"],
                "installed": generation["installed"],
                "active": generation["active"],
                "fec_count": generation["fec_count"],
                "active_fec_count": generation["active_fec_count"],
                "inactive_fec_count": generation["inactive_fec_count"],
            }
            for generation in compiled["generations"]
        ],
        "summary": compiled["summary"],
        "self_checks": compiled["self_checks"],
        "linux_mpls_label_leakage": compiled["linux_mpls_label_leakage"],
        "full_generation_map_available_from_compiler": True,
    }
    return compact


def render_text_summary(report: dict[str, Any]) -> str:
    summary = report["summary"]
    checks = report["self_checks"]
    leakage = report["linux_mpls_label_leakage"]
    return "\n".join(
        [
            "Skyseed G/Z FEC -> BPF generation map compiler",
            f"generations={summary['generation_count']}",
            f"fec_entries={summary['fec_entry_count']}",
            f"active_fecs={summary['active_fec_count']}",
            f"inactive_tombstones={summary['inactive_fec_tombstone_count']}",
            f"runtime_epoch_matches={summary['runtime_epoch_matched_generation_count']}",
            "fail_closed="
            + ",".join(
                key
                for key in [
                    "missing_generation_fail_closed",
                    "missing_fec_fail_closed",
                    "inactive_fec_fail_closed",
                ]
                if checks[key]["ok"]
            ),
            f"future_generation_not_active={checks['future_generation_not_active']['ok']}",
            f"linux_mpls_label_leakage_found={leakage['found']}",
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compile Skyseed G/Z FEC plan/diff artifacts into BPF generation maps."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_RUN_DIR)
    parser.add_argument("--json-out", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--full-json-out", type=Path, default=None)
    parser.add_argument("--active-generation", type=int, default=0)
    parser.add_argument("--format", choices=["json", "text"], default="text")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    artifacts = load_artifacts(args.input)
    compiled = compile_generation_map(
        artifacts["gz_fec_plan"],
        artifacts["gz_update_diff_summary"],
        artifacts["temporal_plan"],
        active_generation=args.active_generation,
    )
    report = compact_for_baseline(compiled, artifacts)
    write_json(args.json_out, report)
    if args.full_json_out:
        full_report = copy.deepcopy(compiled)
        full_report["input_artifacts"] = report["input_artifacts"]
        write_json(args.full_json_out, full_report)
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_text_summary(report))
        print(f"wrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
