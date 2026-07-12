#!/usr/bin/env python3
# encoding: utf-8

"""Recompile r134's frozen 4H snapshots with the current shared G/Z ABI."""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
import mmap
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator, Mapping

try:
    from ._path_setup import bootstrap_app_import_path
except ImportError:  # pragma: no cover
    from _path_setup import bootstrap_app_import_path

SATELLITE_DIR = bootstrap_app_import_path()

from contracts import TopologySnapshot
from linux_mpls_runtime import (
    _bpf_owned_compile_mpls_maps_for_contexts,
    _realization_contexts,
)
from planner import UtInternetExperimentPlanner
from run_starlink_shell_experiment import _frozen_runtime_ping_observation_schedule


DEFAULT_RUN_DIR = (
    SATELLITE_DIR
    / "output"
    / "validation_profiles"
    / "r134-4h-nonblocking-ping-observer"
)
DEFAULT_POLICY_CATALOG = (
    SATELLITE_DIR
    / "configs"
    / "starlink_shell_ground"
    / "s4_gz_rich_validation_ut_gateway_policy_32.json"
)
DEFAULT_GATEWAY_BINDING = (
    SATELLITE_DIR
    / "configs"
    / "starlink_shell_ground"
    / "s4_visualization_gateway_binding_12.json"
)
SPOTLIGHT_GENERATIONS = {31, 32, 33, 41, 42, 43}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _skip_ws(data: mmap.mmap, index: int) -> int:
    while data[index] in b" \t\r\n":
        index += 1
    return index


def _string_end(data: mmap.mmap, index: int) -> int:
    if data[index] != ord('"'):
        raise ValueError(f"JSON string expected at byte {index}")
    index += 1
    escaped = False
    while True:
        value = data[index]
        index += 1
        if escaped:
            escaped = False
        elif value == ord("\\"):
            escaped = True
        elif value == ord('"'):
            return index


def _value_end(data: mmap.mmap, index: int) -> int:
    index = _skip_ws(data, index)
    first = data[index]
    if first == ord('"'):
        return _string_end(data, index)
    closing_by_opening = {ord("{"): ord("}"), ord("["): ord("]")}
    if first not in closing_by_opening:
        while data[index] not in b",}]":
            index += 1
        return index
    stack = [closing_by_opening[first]]
    index += 1
    in_string = False
    escaped = False
    while stack:
        value = data[index]
        index += 1
        if in_string:
            if escaped:
                escaped = False
            elif value == ord("\\"):
                escaped = True
            elif value == ord('"'):
                in_string = False
            continue
        if value == ord('"'):
            in_string = True
        elif value in closing_by_opening:
            stack.append(closing_by_opening[value])
        elif value == stack[-1]:
            stack.pop()
    return index


def _iter_object_pairs(
    data: mmap.mmap,
    object_start: int,
) -> Iterator[tuple[str, int, int]]:
    index = _skip_ws(data, object_start)
    if data[index] != ord("{"):
        raise ValueError(f"JSON object expected at byte {index}")
    index = _skip_ws(data, index + 1)
    while data[index] != ord("}"):
        key_start = index
        key_end = _string_end(data, key_start)
        key = str(json.loads(data[key_start:key_end]))
        index = _skip_ws(data, key_end)
        if data[index] != ord(":"):
            raise ValueError(f"JSON colon expected at byte {index}")
        value_start = _skip_ws(data, index + 1)
        value_end = _value_end(data, value_start)
        yield key, value_start, value_end
        index = _skip_ws(data, value_end)
        if data[index] == ord(","):
            index = _skip_ws(data, index + 1)
            continue
        if data[index] != ord("}"):
            raise ValueError(f"JSON object delimiter expected at byte {index}")


def _context_object_start(data: mmap.mmap) -> int:
    marker = b'"context_by_slice_by_segment":'
    marker_start = data.find(marker)
    if marker_start < 0:
        raise ValueError("context_by_slice_by_segment missing")
    return _skip_ws(data, marker_start + len(marker))


def _iter_frozen_context_records(
    path: Path,
) -> Iterator[tuple[str, str, dict[str, Any]]]:
    with path.open("rb") as stream, mmap.mmap(
        stream.fileno(), 0, access=mmap.ACCESS_READ
    ) as data:
        for horizon_id, horizon_start, _horizon_end in _iter_object_pairs(
            data,
            _context_object_start(data),
        ):
            for slice_id, value_start, value_end in _iter_object_pairs(
                data,
                horizon_start,
            ):
                yield horizon_id, slice_id, dict(
                    json.loads(data[value_start:value_end])
                )


def _current_policy_inputs(
    policy_catalog: Path,
    gateway_binding_catalog: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    policies = {
        str(item["ut_id"]): dict(item)
        for item in list(_read_json(policy_catalog))
    }
    services = {
        str(item["gateway_id"]): {
            "gateway_id": str(item["gateway_id"]),
            "gateway_selector_label": int(item["gateway_selector_label"]),
            "gateway_service_ipv4": str(item["gateway_service_ipv4"]),
        }
        for item in list(_read_json(gateway_binding_catalog))
    }
    return policies, services


def _snapshot_with_current_policy(
    payload: Mapping[str, Any],
    *,
    policies: Mapping[str, Mapping[str, Any]],
    services: Mapping[str, Mapping[str, Any]],
) -> TopologySnapshot:
    source = dict(payload)
    source.pop("validation", None)
    active_ut_ids = set(dict(source.get("ut_zone_membership") or {}))
    hints = dict(source.get("planner_hints") or {})
    hints["ut_gateway_policies"] = {
        ut_id: dict(policies[ut_id])
        for ut_id in sorted(active_ut_ids)
        if ut_id in policies
    }
    referenced_gateway_ids = {
        str(rule.get("gateway_id") or "")
        for policy in hints["ut_gateway_policies"].values()
        for rule in list(policy.get("rules") or [])
        if str(rule.get("gateway_id") or "")
    }
    hints["gateway_service_catalog"] = {
        gateway_id: dict(services[gateway_id])
        for gateway_id in sorted(referenced_gateway_ids)
        if gateway_id in services
    }
    source["planner_hints"] = hints
    fields = TopologySnapshot.__dataclass_fields__
    return TopologySnapshot(**{name: source[name] for name in fields})


def _flow_metadata_count(
    compiled: Mapping[str, Any],
    map_name: str,
) -> tuple[int, int]:
    flow_ids = [
        str(dict(entry.get("metadata") or {}).get("flow_id") or "")
        for entry in list(compiled.get(map_name) or [])
    ]
    nonempty = [flow_id for flow_id in flow_ids if flow_id]
    return len(nonempty), len(set(nonempty))


def replay(
    *,
    run_dir: Path,
    policy_catalog: Path,
    gateway_binding_catalog: Path,
    limit: int | None = None,
) -> dict[str, Any]:
    policies, services = _current_policy_inputs(
        policy_catalog,
        gateway_binding_catalog,
    )
    frozen_segments = list(_read_json(run_dir / "frozen_prepared_horizon_segments.json"))
    topology_by_horizon = {
        str(segment.get("horizon_id") or segment.get("segment_id") or ""):
        Path(str(segment["topology_path"]))
        for segment in frozen_segments
    }
    records: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    normal_inputs = run_dir / "normal_runtime_inputs.json"
    for global_index, (horizon_id, slice_id, historical) in enumerate(
        _iter_frozen_context_records(normal_inputs)
    ):
        if limit is not None and global_index >= int(limit):
            break
        snapshot = _snapshot_with_current_policy(
            dict(historical["snapshot"]),
            policies=policies,
            services=services,
        )
        snapshot_issues = snapshot.validate()
        old_target = dict(
            dict(historical.get("runtime_update_plan") or {}).get("target_snapshot")
            or {}
        )
        physical_generation = old_target.get("bpf_physical_generation_id")
        if physical_generation in (None, "", "None"):
            blockers.append(
                {
                    "global_index": global_index,
                    "horizon_id": horizon_id,
                    "slice_id": slice_id,
                    "reason": "historical_physical_generation_missing",
                }
            )
            continue
        plan = UtInternetExperimentPlanner().plan(snapshot)
        plan = replace(
            plan,
            target_snapshot={
                **dict(plan.target_snapshot),
                "bpf_physical_generation_id": int(physical_generation),
                "bpf_generation_id": int(physical_generation),
            },
        )
        topology_path = topology_by_horizon.get(horizon_id, run_dir / "topology.json")
        contexts = _realization_contexts(
            topology_path=topology_path,
            topology_snapshot=snapshot,
            runtime_update_plan=plan,
        )
        compiled = _bpf_owned_compile_mpls_maps_for_contexts(
            contexts=contexts,
            generation_id=int(physical_generation),
            offline=True,
        )
        default_count = sum(
            context.traffic_scope == "default_gateway_class" for context in contexts
        )
        special_count = sum(
            context.traffic_scope == "special_prefix_gateway_class" for context in contexts
        )
        selector_count, unique_selector_flows = _flow_metadata_count(
            compiled, "gateway_selector_ingress_map"
        )
        delivery_count, unique_delivery_flows = _flow_metadata_count(
            compiled, "zone_edge_delivery_map"
        )
        policy_rows = list(compiled.get("ut_gateway_policy_map") or [])
        schedule = _frozen_runtime_ping_observation_schedule(
            SimpleNamespace(
                session_id=f"offline:{horizon_id}:{slice_id}",
                backend={
                    "bpf_owned_readiness": {
                        "active_generation": int(physical_generation)
                    }
                },
                target_slice={"slice_id": slice_id},
                contexts=tuple(contexts),
            )
        )
        record_blockers: list[str] = []
        if snapshot_issues:
            record_blockers.append("snapshot_contract_invalid")
        if default_count != special_count or default_count * 2 != len(contexts):
            record_blockers.append("default_special_context_coverage_mismatch")
        if selector_count != len(contexts) or unique_selector_flows != len(contexts):
            record_blockers.append("gateway_selector_ingress_flow_coverage_mismatch")
        if delivery_count != len(contexts) or unique_delivery_flows != len(contexts):
            record_blockers.append("zone_edge_delivery_flow_coverage_mismatch")
        if len(policy_rows) != len(contexts):
            record_blockers.append("static_ut_policy_coverage_mismatch")
        if any(
            dict(row.get("metadata") or {}).get("runtime_write_count")
            in (None, "", "None")
            or int(dict(row.get("metadata") or {})["runtime_write_count"]) != 0
            for row in policy_rows
        ):
            record_blockers.append("static_ut_policy_runtime_write_detected")
        if len(schedule) != len(contexts):
            record_blockers.append("observer_schedule_coverage_mismatch")
        if list(compiled.get("map_conflicts") or []):
            record_blockers.append("compiled_map_conflict")
        generation_fields = (
            "ingress_classifier_map",
            "gateway_selector_ingress_map",
            "core_lfib_map",
            "egress_handoff_map",
            "zone_edge_delivery_map",
        )
        if any(
            dict(entry.get("key") or {}).get("generation") in (None, "", "None")
            or int(dict(entry.get("key") or {})["generation"])
            != int(physical_generation)
            for map_name in generation_fields
            for entry in list(compiled.get(map_name) or [])
        ):
            record_blockers.append("compiled_generation_key_mismatch")
        record = {
            "global_generation_index": global_index,
            "horizon_id": horizon_id,
            "slice_id": slice_id,
            "physical_generation": int(physical_generation),
            "spotlight": (
                global_index in SPOTLIGHT_GENERATIONS
                or int(physical_generation) in SPOTLIGHT_GENERATIONS
            ),
            "context_count": len(contexts),
            "default_context_count": default_count,
            "special_context_count": special_count,
            "observer_request_count": len(schedule),
            "compiled_map_counts": dict(compiled.get("compiled_mpls_map_counts") or {}),
            "unique_selector_flow_count": unique_selector_flows,
            "unique_delivery_flow_count": unique_delivery_flows,
            "map_conflict_count": len(list(compiled.get("map_conflicts") or [])),
            "snapshot_issue_count": len(snapshot_issues),
            "blockers": record_blockers,
            "ok": not record_blockers,
        }
        records.append(record)
        if record_blockers:
            blockers.append(
                {
                    "global_index": global_index,
                    "horizon_id": horizon_id,
                    "slice_id": slice_id,
                    "physical_generation": int(physical_generation),
                    "reasons": record_blockers,
                }
            )
        if (global_index + 1) % 10 == 0 or record_blockers:
            print(
                f"replayed={global_index + 1} horizon={horizon_id} "
                f"physical_generation={physical_generation} blockers={len(blockers)}",
                flush=True,
            )
    expected = min(100, int(limit)) if limit is not None else 100
    if len(records) != expected:
        blockers.append(
            {
                "reason": "replayed_generation_count_mismatch",
                "expected": expected,
                "actual": len(records),
            }
        )
    return {
        "schema_version": "skyseed-r134-shared-gz-offline-replay-v1",
        "source_run_dir": str(run_dir),
        "historical_artifact_policy": (
            "r134 snapshots and physical generation assignments are inputs only; "
            "historical compiled v1 artifacts are never consumed"
        ),
        "current_compiled_schema": "skyseed-bpf-owned-compiled-runtime-inputs-v2",
        "replayed_generation_count": len(records),
        "horizon_counts": {
            horizon_id: sum(record["horizon_id"] == horizon_id for record in records)
            for horizon_id in sorted({record["horizon_id"] for record in records})
        },
        "spotlight_records": [record for record in records if record["spotlight"]],
        "records": records,
        "blocker_count": len(blockers),
        "blockers": blockers,
        "ok": not blockers,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    parser.add_argument("--policy-catalog", type=Path, default=DEFAULT_POLICY_CATALOG)
    parser.add_argument(
        "--gateway-binding-catalog",
        type=Path,
        default=DEFAULT_GATEWAY_BINDING,
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    report = replay(
        run_dir=args.run_dir.resolve(),
        policy_catalog=args.policy_catalog.resolve(),
        gateway_binding_catalog=args.gateway_binding_catalog.resolve(),
        limit=args.limit,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "ok": report["ok"],
                "replayed_generation_count": report["replayed_generation_count"],
                "horizon_counts": report["horizon_counts"],
                "blocker_count": report["blocker_count"],
                "output": str(args.output),
            },
            sort_keys=True,
        )
    )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
